from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

import verify_apify_worker_process_binding as gate  # noqa: E402


HEAD = "a" * 40
DEPLOY = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"


def _stat(pid: int, ticks: int) -> str:
    # Fields after ``comm`` begin at proc stat field 3. Index 19 is starttime.
    after_comm = ["S", *("0" for _ in range(18)), str(ticks), *("0" for _ in range(5))]
    return f"{pid} (python worker) " + " ".join(after_comm) + "\n"


def _write_process(
    proc_root: Path,
    *,
    pid: int,
    cwd: Path,
    executable: Path,
    control_group: str,
    argv: tuple[str, ...] | None = None,
) -> None:
    pid_dir = proc_root / str(pid)
    pid_dir.mkdir(parents=True)
    (pid_dir / "cwd").symlink_to(cwd)
    (pid_dir / "exe").symlink_to(executable)
    command = argv or (str(executable), "-m", gate.WORKER_MODULE)
    (pid_dir / "cmdline").write_bytes(b"\0".join(item.encode() for item in command) + b"\0")
    (pid_dir / "cgroup").write_text(f"0::{control_group}\n", encoding="utf-8")
    (pid_dir / "stat").write_text(_stat(pid, 10_000 + pid), encoding="ascii")


@pytest.fixture
def fleet(tmp_path: Path) -> dict[str, object]:
    root = tmp_path / "viltrox"
    release = root / "releases" / "release-1"
    release.mkdir(parents=True)
    (release / "BUILD_GIT_SHA").write_text(HEAD + "\n", encoding="ascii")
    current = root / "current"
    current.symlink_to(release)
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python-placeholder\n")
    python.chmod(0o755)
    proc_root = tmp_path / "proc"
    proc_root.mkdir()

    systemd: dict[str, gate.SystemdState] = {}
    workers: list[dict[str, object]] = []
    for index, contract in enumerate(gate.WORKER_CONTRACTS, start=1):
        pid = 2000 + index
        cgroup = f"/system.slice/{contract.unit}"
        systemd[contract.unit] = gate.SystemdState(
            unit=contract.unit,
            load_state="loaded",
            active_state="active",
            sub_state="running",
            main_pid=pid,
            control_group=cgroup,
            working_directory=str(current),
            invocation_id=f"{index:032x}",
        )
        workers.append(
            {
                "worker_name": contract.worker_name,
                "pid": pid,
                "worker_sha": HEAD,
                "boot_nonce_sha256": f"{index:064x}",
                "online": True,
                "lane": contract.lane,
            }
        )
        _write_process(
            proc_root,
            pid=pid,
            cwd=release,
            executable=python,
            control_group=cgroup,
        )

    health = {
        "trust": {
            "worker_fleet": {
                "online_count": 16,
                "expected_count": 16,
                "unique_names": True,
                "unique_pids": True,
                "all_worker_sha_aligned": True,
                "all_heartbeats_fresh": True,
                "workers": workers,
            }
        }
    }
    return {
        "root": root,
        "release": release,
        "current": current,
        "python": python,
        "proc_root": proc_root,
        "systemd": systemd,
        "health": health,
    }


def _evaluate(fleet: dict[str, object]) -> dict[str, object]:
    return gate.evaluate_binding(
        fleet["health"],
        systemd=fleet["systemd"],
        current_release=fleet["current"],
        expected_head=HEAD,
        proc_root=fleet["proc_root"],
        expected_python=fleet["python"],
    )


def test_exact_sixteen_unit_heartbeat_and_proc_bindings_pass(fleet: dict[str, object]) -> None:
    report = _evaluate(fleet)

    assert report["overall"] == {"pass": True, "errors": []}
    assert len(report["units"]) == 16
    assert report["census"] == {
        "worker_process_count": 16,
        "expected_pid_count": 16,
        "unexpected_pids": [],
        "missing_pids": [],
        "unreadable_pid_count": 0,
    }
    assert report["read_only"] is True
    assert report["credentials_emitted"] is False


def test_heartbeat_pid_must_equal_corresponding_systemd_main_pid(fleet: dict[str, object]) -> None:
    workers = fleet["health"]["trust"]["worker_fleet"]["workers"]
    workers[0]["pid"] = 9999

    report = _evaluate(fleet)

    assert report["overall"]["pass"] is False
    assert "heartbeat_main_pid_mismatch:apify-worker-interactive" in report["overall"]["errors"]


def test_heartbeat_names_are_an_exact_fixed_unit_mapping(fleet: dict[str, object]) -> None:
    workers = fleet["health"]["trust"]["worker_fleet"]["workers"]
    workers[1]["worker_name"] = "apify-worker-bulk-unreviewed"

    report = _evaluate(fleet)

    assert report["overall"]["pass"] is False
    assert "health_worker_name_set_mismatch" in report["overall"]["errors"]
    assert "heartbeat_missing:apify-worker-bulk-1" in report["overall"]["errors"]


@pytest.mark.parametrize(
    ("kind", "expected_error"),
    [
        ("cgroup", "process_cgroup_mismatch:apify-worker-interactive"),
        ("cwd", "process_cwd_not_current_release:apify-worker-interactive"),
        ("exe", "process_executable_mismatch:apify-worker-interactive"),
        ("command", "process_command_mismatch:apify-worker-interactive"),
    ],
)
def test_proc_identity_must_belong_to_current_release(
    fleet: dict[str, object],
    kind: str,
    expected_error: str,
) -> None:
    contract = gate.WORKER_CONTRACTS[0]
    state = fleet["systemd"][contract.unit]
    pid_dir = fleet["proc_root"] / str(state.main_pid)
    if kind == "cgroup":
        (pid_dir / "cgroup").write_text("0::/system.slice/unreviewed.service\n", encoding="utf-8")
    elif kind == "cwd":
        other = fleet["root"] / "releases" / "old-release"
        other.mkdir()
        (pid_dir / "cwd").unlink()
        (pid_dir / "cwd").symlink_to(other)
    elif kind == "exe":
        other_python = fleet["root"] / ".venv" / "bin" / "other-python"
        other_python.write_bytes(b"other\n")
        (pid_dir / "exe").unlink()
        (pid_dir / "exe").symlink_to(other_python)
    else:
        (pid_dir / "cmdline").write_bytes(
            str(fleet["python"]).encode() + b"\0-m\0app.workers.worker_main\0"
        )

    report = _evaluate(fleet)

    assert report["overall"]["pass"] is False
    assert expected_error in report["overall"]["errors"]


def test_seventeenth_worker_process_fails_host_census(fleet: dict[str, object]) -> None:
    extra_pid = 2999
    _write_process(
        fleet["proc_root"],
        pid=extra_pid,
        cwd=fleet["release"],
        executable=fleet["python"],
        control_group="/user.slice/unreviewed-worker.scope",
    )

    report = _evaluate(fleet)

    assert report["overall"]["pass"] is False
    assert "unexpected_apify_worker_processes" in report["overall"]["errors"]
    assert "apify_worker_process_count_mismatch" in report["overall"]["errors"]
    assert report["census"]["worker_process_count"] == 17
    assert report["census"]["unexpected_pids"] == [extra_pid]


def test_stale_release_sha_fails_before_process_claims(fleet: dict[str, object]) -> None:
    (fleet["release"] / "BUILD_GIT_SHA").write_text("b" * 40 + "\n", encoding="ascii")

    with pytest.raises(gate.BindingSetupError, match="release_build_sha_mismatch"):
        _evaluate(fleet)


def test_report_never_emits_process_command_lines(fleet: dict[str, object]) -> None:
    report = _evaluate(fleet)
    encoded = json.dumps(report, sort_keys=True)

    assert gate.WORKER_MODULE not in encoded
    assert "cmdline" not in encoded


def test_systemctl_parser_rejects_missing_or_duplicate_properties() -> None:
    unit = gate.WORKER_CONTRACTS[0].unit
    valid = {
        "Id": unit,
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "MainPID": "4321",
        "ControlGroup": f"/system.slice/{unit}",
        "WorkingDirectory": "/opt/viltrox-2.0/current",
        "InvocationID": "a" * 32,
    }
    parsed = gate._parse_systemctl_show(
        "\n".join(f"{key}={value}" for key, value in valid.items()) + "\n",
        unit,
    )
    assert parsed.main_pid == 4321

    missing = dict(valid)
    missing.pop("ControlGroup")
    with pytest.raises(gate.BindingSetupError, match="systemd_state_incomplete"):
        gate._parse_systemctl_show(
            "\n".join(f"{key}={value}" for key, value in missing.items()) + "\n",
            unit,
        )
    duplicate = "\n".join(f"{key}={value}" for key, value in valid.items())
    with pytest.raises(gate.BindingSetupError, match="systemd_state_duplicate_property"):
        gate._parse_systemctl_show(duplicate + "\nMainPID=9999\n", unit)


def test_changed_systemd_snapshot_is_detectable(fleet: dict[str, object]) -> None:
    before = fleet["systemd"]
    contract = gate.WORKER_CONTRACTS[0]
    after = dict(before)
    after[contract.unit] = replace(
        before[contract.unit],
        main_pid=before[contract.unit].main_pid + 100,
        invocation_id="f" * 32,
    )

    assert before != after


def test_production_deploy_requires_and_retains_process_binding_proof() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")

    strict_health = deploy.index("scripts/verify_runtime_health.py", deploy.index("# Remote acceptance"))
    process_binding = deploy.index("scripts/ops/verify_apify_worker_process_binding.py", strict_health)
    evidence_path = deploy.index("apify-worker-process-binding.json", process_binding)
    browser_capture = deploy.index("scripts/capture_browser_console_cdp.mjs", evidence_path)

    assert strict_health < process_binding < evidence_path < browser_capture
    for required in (
        'sudo -n env -i PATH=/usr/bin:/bin',
        "--health-json -",
        '--current-release \'${REMOTE_CURRENT_DIR}\'',
        '--expected-head \'${LOCAL_GIT_SHA}\'',
        'LOCAL_APIFY_WORKER_PROCESS_BINDING="${POST_DEPLOY_EVIDENCE_DIR}/apify-worker-process-binding.json"',
        'chmod 600 "${LOCAL_APIFY_WORKER_PROCESS_BINDING}"',
    ):
        assert required in deploy
