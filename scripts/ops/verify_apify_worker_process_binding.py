#!/usr/bin/env python3
"""Read-only binding proof for the production 16-process Apify worker fleet.

The verifier joins three independent identities:

* the fixed systemd unit topology and each unit's ``MainPID``;
* the online worker heartbeat rows exposed by the authenticated ``/health``;
* the live Linux ``/proc`` identity (cgroup, cwd, executable and argv).

It also performs a host-wide process census.  Any additional process whose
command line references ``app.workers.apify_jobs_worker`` makes the proof fail.
No environment block, connection string, journal message or business payload
is read or emitted.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from verify_runtime_health import MAX_INPUT_BYTES, strict_json_loads  # noqa: E402


SCHEMA_VERSION = "vkpi-apify-worker-process-binding/v1"
WORKER_MODULE = "app.workers.apify_jobs_worker"
EXPECTED_WORKER_COUNT = 16
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INVOCATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_PROC_TEXT_BYTES = 64 * 1024
SYSTEMCTL = Path("/usr/bin/systemctl")
SYSTEMD_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "MainPID",
    "ControlGroup",
    "WorkingDirectory",
    "InvocationID",
)


class BindingSetupError(RuntimeError):
    """Fail-closed setup/collection error with a non-secret category."""


@dataclass(frozen=True)
class WorkerContract:
    unit: str
    worker_name: str
    lane: str


@dataclass(frozen=True)
class SystemdState:
    unit: str
    load_state: str
    active_state: str
    sub_state: str
    main_pid: int
    control_group: str
    working_directory: str
    invocation_id: str


def worker_contracts() -> tuple[WorkerContract, ...]:
    rows = [
        WorkerContract(
            unit="vkpi-worker-interactive.service",
            worker_name="apify-worker-interactive",
            lane="interactive",
        )
    ]
    rows.extend(
        WorkerContract(
            unit=f"vkpi-worker-bulk@{index}.service",
            worker_name=f"apify-worker-bulk-{index}",
            lane="batch",
        )
        for index in range(1, 16)
    )
    return tuple(rows)


WORKER_CONTRACTS = worker_contracts()


def _bounded_read(path: Path, *, maximum: int = MAX_PROC_TEXT_BYTES) -> bytes:
    try:
        with path.open("rb") as handle:
            payload = handle.read(maximum + 1)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise BindingSetupError("proc_identity_unreadable") from exc
    if len(payload) > maximum:
        raise BindingSetupError("proc_identity_oversized")
    return payload


def _parse_systemctl_show(raw: str, expected_unit: str) -> SystemdState:
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            raise BindingSetupError("systemd_state_invalid")
        key, value = line.split("=", 1)
        if key in values:
            raise BindingSetupError("systemd_state_duplicate_property")
        values[key] = value
    if set(values) != set(SYSTEMD_PROPERTIES):
        raise BindingSetupError("systemd_state_incomplete")
    try:
        main_pid = int(values["MainPID"])
    except ValueError as exc:
        raise BindingSetupError("systemd_main_pid_invalid") from exc
    control_group = values["ControlGroup"]
    if (
        values["Id"] != expected_unit
        or main_pid <= 1
        or not control_group.startswith("/")
        or "\x00" in control_group
        or any(part == ".." for part in control_group.split("/"))
    ):
        raise BindingSetupError("systemd_state_invalid")
    invocation_id = values["InvocationID"].strip().lower()
    if not INVOCATION_ID_RE.fullmatch(invocation_id):
        raise BindingSetupError("systemd_invocation_id_invalid")
    return SystemdState(
        unit=values["Id"],
        load_state=values["LoadState"],
        active_state=values["ActiveState"],
        sub_state=values["SubState"],
        main_pid=main_pid,
        control_group=control_group,
        working_directory=values["WorkingDirectory"],
        invocation_id=invocation_id,
    )


def collect_systemd_snapshot(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    systemctl: Path = SYSTEMCTL,
) -> dict[str, SystemdState]:
    if not systemctl.is_absolute():
        raise BindingSetupError("systemctl_path_not_absolute")
    snapshot: dict[str, SystemdState] = {}
    for contract in WORKER_CONTRACTS:
        command = [
            str(systemctl),
            "show",
            "--no-pager",
            *(f"--property={name}" for name in SYSTEMD_PROPERTIES),
            contract.unit,
        ]
        try:
            result = run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BindingSetupError("systemctl_show_failed") from exc
        if result.returncode != 0:
            raise BindingSetupError("systemctl_show_failed")
        snapshot[contract.unit] = _parse_systemctl_show(result.stdout, contract.unit)
    if len(snapshot) != EXPECTED_WORKER_COUNT:
        raise BindingSetupError("systemd_unit_count_invalid")
    return snapshot


def _release_identity(current_release: Path, expected_head: str) -> tuple[Path, Path]:
    if not current_release.is_absolute():
        raise BindingSetupError("current_release_not_absolute")
    try:
        current_info = current_release.lstat()
    except OSError as exc:
        raise BindingSetupError("current_release_unreadable") from exc
    if not stat.S_ISLNK(current_info.st_mode):
        raise BindingSetupError("current_release_not_symlink")
    try:
        release = current_release.resolve(strict=True)
    except OSError as exc:
        raise BindingSetupError("current_release_target_unreadable") from exc
    root = current_release.parent.resolve(strict=True)
    releases = root / "releases"
    if release.parent != releases or not release.is_dir() or release.is_symlink():
        raise BindingSetupError("current_release_target_outside_releases")
    build_sha_path = release / "BUILD_GIT_SHA"
    try:
        build_info = build_sha_path.lstat()
        build_sha = build_sha_path.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeError) as exc:
        raise BindingSetupError("release_build_sha_unreadable") from exc
    if (
        stat.S_ISLNK(build_info.st_mode)
        or not stat.S_ISREG(build_info.st_mode)
        or build_info.st_size <= 0
        or build_info.st_size > 256
        or build_sha != expected_head
    ):
        raise BindingSetupError("release_build_sha_mismatch")
    return root, release


def _health_workers(
    payload: Any,
    *,
    expected_head: str,
    errors: list[str],
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        errors.append("health_root_invalid")
        return {}
    trust = payload.get("trust")
    fleet = trust.get("worker_fleet") if isinstance(trust, Mapping) else None
    if not isinstance(fleet, Mapping):
        errors.append("health_worker_fleet_missing")
        return {}
    for key in ("unique_names", "unique_pids", "all_worker_sha_aligned", "all_heartbeats_fresh"):
        if fleet.get(key) is not True:
            errors.append(f"health_fleet_{key}_not_true")
    if fleet.get("online_count") != EXPECTED_WORKER_COUNT:
        errors.append("health_online_count_mismatch")
    if fleet.get("expected_count") != EXPECTED_WORKER_COUNT:
        errors.append("health_expected_count_mismatch")
    rows = fleet.get("workers")
    if not isinstance(rows, list):
        errors.append("health_worker_rows_missing")
        return {}
    online = [row for row in rows if isinstance(row, Mapping) and row.get("online") is True]
    if len(online) != EXPECTED_WORKER_COUNT:
        errors.append("health_online_rows_mismatch")
    by_name: dict[str, Mapping[str, Any]] = {}
    seen_pids: set[int] = set()
    for row in online:
        name = str(row.get("worker_name") or "").strip()
        pid = row.get("pid")
        if not name or name in by_name:
            errors.append("health_worker_name_invalid_or_duplicate")
            continue
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1 or pid in seen_pids:
            errors.append(f"health_worker_pid_invalid:{name}")
        else:
            seen_pids.add(pid)
        if str(row.get("worker_sha") or "").strip().lower() != expected_head:
            errors.append(f"health_worker_sha_mismatch:{name}")
        if not SHA256_RE.fullmatch(str(row.get("boot_nonce_sha256") or "").strip().lower()):
            errors.append(f"health_worker_boot_nonce_invalid:{name}")
        by_name[name] = row
    expected_names = {contract.worker_name for contract in WORKER_CONTRACTS}
    if set(by_name) != expected_names:
        errors.append("health_worker_name_set_mismatch")
    return by_name


def _proc_start_ticks(pid_dir: Path) -> int:
    raw = _bounded_read(pid_dir / "stat", maximum=4096)
    try:
        text = raw.decode("ascii")
        close = text.rfind(")")
        fields = text[close + 2 :].split()
        # Fields after ``comm`` begin at proc stat field 3; starttime is 22.
        value = int(fields[19])
    except (UnicodeError, ValueError, IndexError) as exc:
        raise BindingSetupError("proc_stat_invalid") from exc
    if close <= 0 or value <= 0:
        raise BindingSetupError("proc_stat_invalid")
    return value


def _read_proc_link(path: Path, *, category: str) -> Path:
    try:
        target = os.readlink(path)
        linked = Path(target)
        if not linked.is_absolute():
            linked = path.parent / linked
        return linked.resolve(strict=True)
    except OSError as exc:
        raise BindingSetupError(category) from exc


def _proc_cmdline(pid_dir: Path) -> tuple[str, ...]:
    raw = _bounded_read(pid_dir / "cmdline")
    if not raw or not raw.endswith(b"\0"):
        raise BindingSetupError("proc_cmdline_invalid")
    try:
        argv = tuple(part.decode("utf-8") for part in raw[:-1].split(b"\0"))
    except UnicodeError as exc:
        raise BindingSetupError("proc_cmdline_invalid") from exc
    if not argv or any(not value for value in argv):
        raise BindingSetupError("proc_cmdline_invalid")
    return argv


def _proc_cgroups(pid_dir: Path) -> tuple[str, ...]:
    raw = _bounded_read(pid_dir / "cgroup")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise BindingSetupError("proc_cgroup_invalid") from exc
    paths: list[str] = []
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[2].startswith("/"):
            raise BindingSetupError("proc_cgroup_invalid")
        paths.append(parts[2].rstrip("/") or "/")
    if not paths:
        raise BindingSetupError("proc_cgroup_invalid")
    return tuple(paths)


def _cgroup_matches(observed: Sequence[str], expected: str) -> bool:
    normalized = expected.rstrip("/") or "/"
    return any(path == normalized or path.startswith(normalized + "/") for path in observed)


def _command_matches(argv: Sequence[str], expected_python: Path) -> bool:
    if not argv or Path(argv[0]) != expected_python:
        return False
    try:
        module_flag = argv.index("-m")
    except ValueError:
        return False
    if module_flag <= 0 or module_flag + 1 >= len(argv):
        return False
    if tuple(argv[1:module_flag]) not in ((), ("-B",), ("-u",), ("-I",)):
        return False
    return tuple(argv[module_flag + 1 :]) == (WORKER_MODULE,)


def _looks_like_apify_worker(argv: Sequence[str]) -> bool:
    return any(
        argv[index] == "-m" and argv[index + 1] == WORKER_MODULE
        for index in range(1, len(argv) - 1)
    )


def collect_worker_census(proc_root: Path) -> tuple[set[int], list[int]]:
    worker_pids: set[int] = set()
    unreadable: list[int] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError as exc:
        raise BindingSetupError("proc_census_unavailable") from exc
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            raw = _bounded_read(entry / "cmdline")
        except FileNotFoundError:
            continue
        except BindingSetupError:
            unreadable.append(pid)
            continue
        if not raw:
            continue
        try:
            argv = tuple(part.decode("utf-8", "strict") for part in raw.rstrip(b"\0").split(b"\0"))
        except UnicodeError:
            unreadable.append(pid)
            continue
        if _looks_like_apify_worker(argv):
            worker_pids.add(pid)
    return worker_pids, unreadable


def evaluate_binding(
    payload: Any,
    *,
    systemd: Mapping[str, SystemdState],
    current_release: Path,
    expected_head: str,
    proc_root: Path = Path("/proc"),
    expected_python: Path | None = None,
) -> dict[str, Any]:
    expected_head = str(expected_head or "").strip().lower()
    if not SHA_RE.fullmatch(expected_head):
        raise BindingSetupError("expected_head_invalid")
    root, release = _release_identity(current_release, expected_head)
    python_path = expected_python or (root / ".venv" / "bin" / "python")
    if not python_path.is_absolute():
        raise BindingSetupError("expected_python_not_absolute")
    try:
        resolved_python = python_path.resolve(strict=True)
    except OSError as exc:
        raise BindingSetupError("expected_python_unreadable") from exc

    errors: list[str] = []
    health = _health_workers(payload, expected_head=expected_head, errors=errors)
    unit_rows: list[dict[str, Any]] = []
    expected_pids: set[int] = set()
    if set(systemd) != {contract.unit for contract in WORKER_CONTRACTS}:
        errors.append("systemd_unit_set_mismatch")

    for contract in WORKER_CONTRACTS:
        state = systemd.get(contract.unit)
        row = health.get(contract.worker_name)
        if state is None:
            errors.append(f"systemd_unit_missing:{contract.unit}")
            continue
        if state.load_state != "loaded" or state.active_state != "active" or state.sub_state != "running":
            errors.append(f"systemd_unit_not_running:{contract.unit}")
        if Path(state.working_directory) != current_release:
            errors.append(f"systemd_working_directory_mismatch:{contract.unit}")
        if state.main_pid in expected_pids:
            errors.append(f"systemd_main_pid_duplicate:{contract.unit}")
        expected_pids.add(state.main_pid)
        if row is None:
            errors.append(f"heartbeat_missing:{contract.worker_name}")
        else:
            if row.get("pid") != state.main_pid:
                errors.append(f"heartbeat_main_pid_mismatch:{contract.worker_name}")
            if str(row.get("lane") or "") != contract.lane:
                errors.append(f"heartbeat_lane_mismatch:{contract.worker_name}")

        process_errors: list[str] = []
        pid_dir = proc_root / str(state.main_pid)
        try:
            info = pid_dir.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise BindingSetupError("proc_pid_directory_invalid")
            ticks_before = _proc_start_ticks(pid_dir)
            cwd = _read_proc_link(pid_dir / "cwd", category="proc_cwd_unreadable")
            executable = _read_proc_link(pid_dir / "exe", category="proc_exe_unreadable")
            argv = _proc_cmdline(pid_dir)
            cgroups = _proc_cgroups(pid_dir)
            ticks_after = _proc_start_ticks(pid_dir)
            if ticks_before != ticks_after:
                process_errors.append("pid_identity_changed")
            if cwd != release:
                process_errors.append("cwd_not_current_release")
            if executable != resolved_python:
                process_errors.append("executable_mismatch")
            if not _command_matches(argv, python_path):
                process_errors.append("command_mismatch")
            if not _cgroup_matches(cgroups, state.control_group):
                process_errors.append("cgroup_mismatch")
        except FileNotFoundError:
            process_errors.append("process_disappeared")
        except BindingSetupError as exc:
            process_errors.append(str(exc))
        for issue in process_errors:
            errors.append(f"process_{issue}:{contract.worker_name}")
        unit_rows.append(
            {
                "unit": contract.unit,
                "worker_name": contract.worker_name,
                "lane": contract.lane,
                "main_pid": state.main_pid,
                "heartbeat_pid_match": row is not None and row.get("pid") == state.main_pid,
                "process_identity_pass": not process_errors,
            }
        )

    census_pids, unreadable_pids = collect_worker_census(proc_root)
    if unreadable_pids:
        errors.append("proc_census_incomplete")
    unexpected = sorted(census_pids - expected_pids)
    missing = sorted(expected_pids - census_pids)
    if unexpected:
        errors.append("unexpected_apify_worker_processes")
    if missing:
        errors.append("expected_apify_worker_missing_from_census")
    if len(census_pids) != EXPECTED_WORKER_COUNT:
        errors.append("apify_worker_process_count_mismatch")

    return {
        "schema_version": SCHEMA_VERSION,
        "overall": {"pass": not errors, "errors": errors},
        "release": {
            "current": str(current_release),
            "target": str(release),
            "expected_head": expected_head,
        },
        "expected_count": EXPECTED_WORKER_COUNT,
        "units": unit_rows,
        "census": {
            "worker_process_count": len(census_pids),
            "expected_pid_count": len(expected_pids),
            "unexpected_pids": unexpected,
            "missing_pids": missing,
            "unreadable_pid_count": len(unreadable_pids),
        },
        "read_only": True,
        "credentials_emitted": False,
    }


def _read_health(path: str) -> Any:
    if path == "-":
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        candidate = Path(path)
        try:
            info = candidate.lstat()
            if candidate.is_symlink() or not stat.S_ISREG(info.st_mode):
                raise BindingSetupError("health_json_not_regular")
            with candidate.open("rb") as handle:
                raw = handle.read(MAX_INPUT_BYTES + 1)
        except BindingSetupError:
            raise
        except OSError as exc:
            raise BindingSetupError("health_json_unreadable") from exc
    try:
        return strict_json_loads(raw)
    except ValueError as exc:
        raise BindingSetupError("health_json_invalid") from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-json", default="-", help="Strict /health JSON path, or - for stdin")
    parser.add_argument("--current-release", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = _read_health(args.health_json)
        before = collect_systemd_snapshot()
        report = evaluate_binding(
            payload,
            systemd=before,
            current_release=args.current_release,
            expected_head=args.expected_head,
        )
        after = collect_systemd_snapshot()
        census_after, unreadable_after = collect_worker_census(Path("/proc"))
        expected_after = {state.main_pid for state in after.values()}
        if after != before:
            report["overall"]["errors"].append("systemd_snapshot_changed_during_verification")
        if census_after != expected_after or unreadable_after:
            report["overall"]["errors"].append("worker_census_changed_or_incomplete")
        report["overall"]["pass"] = not report["overall"]["errors"]
        exit_code = 0 if report["overall"]["pass"] else 1
    except BindingSetupError as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "overall": {"pass": False, "errors": [str(exc)]},
            "read_only": True,
            "credentials_emitted": False,
        }
        exit_code = 2
    sys.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
