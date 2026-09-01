#!/usr/bin/env python3
"""Three-run controller for a fenced, provider-free candidate runtime."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ops.freeze_worktree_candidate import (
    _validate_controller_static_receipt,
    run_deploy_gate,
    verify_manifest,
)
from scripts.ops.freeze_worktree_contract import (
    path_identity,
    remove_owned_path,
    write_owned_file_exclusive,
)
from scripts.ops.isolated_runtime_attestation import (
    StrictRuntimeGateError,
    control_plane_digest,
    expected_receipt_plan,
    persist_receipt_bytes as _persist_receipt_bytes,
    phase_candidate_identity as _phase_candidate_identity,
    sha256_path as _sha256_path,
    sign_attestation,
    validate_bound_receipts as _validate_bound_receipts,
)
from scripts.ops.isolated_strict_runtime_resources import (
    ManagedProcess,
    PROVIDER_ENV_NAMES,
    RUNTIME_PREFIX,
    RuntimePorts,
    binary as _binary,
    command as _command,
    minimal_runtime_environment as _minimal_runtime_environment,
    postgres_environment as _postgres_environment,
    private_root as _private_root,
    source_dump_environment as _source_dump_environment,
    unique_loopback_ports as _unique_loopback_ports,
)
from scripts.ops.strict_runtime_seatbelt import (
    candidate_profile,
    require_sandbox_exec,
    run_preflight,
    sandboxed,
)


STRICT_RUNS = 3


def _pid_record(pid: int) -> tuple[int, int, str, str]:
    completed = subprocess.run(
        ["/bin/ps", "-o", "pgid=", "-o", "lstart=", "-o", "command=", "-p", str(pid)],
        check=False, capture_output=True, text=True, timeout=5,
        env={"HOME": "/tmp", "LANG": "C", "LC_ALL": "C", "PATH": os.defpath},
    )
    line = completed.stdout.strip()
    if completed.returncode != 0 or not line: raise StrictRuntimeGateError(f"cannot bind process identity: {pid}")
    fields = line.split(None, 6)
    if len(fields) != 7: raise StrictRuntimeGateError(f"incomplete process identity: {pid}")
    try: session_id = os.getsid(pid)
    except (ProcessLookupError, PermissionError) as exc:
        raise StrictRuntimeGateError(f"cannot bind process session: {pid}") from exc
    return int(fields[0]), session_id, " ".join(fields[1:6]), fields[6]


def _pid_identity(pid: int) -> tuple[int, str]:
    pgid, _sid, started, _command = _pid_record(pid); return pgid, started


def strict_runtime_preflight() -> dict[str, object]:
    names = ("pg_dump", "initdb", "pg_ctl", "createdb", "pg_restore", "redis-server")
    binaries = {name: _binary(name) for name in names}
    ports = RuntimePorts(*_unique_loopback_ports())
    return {
        "pass": True, "binaries": binaries,
        "sandbox_exec": str(require_sandbox_exec()),
        "seatbelt": run_preflight(Path(__file__).resolve().parents[2]),
        "sample_unique_ports": list(ports.values()),
        "source_database_access": "pg_dump_only_default_transaction_read_only",
        "provider_network": "credentials_and_proxies_removed",
        "real_clone_executed": False,
    }


def _prepare_postgres(
    *, root: Path, port: int, source_database_url: str,
    binaries: Mapping[str, str],
) -> tuple[Path, Path]:
    dump = root / "source-readonly.dump"
    data = root / "runtime/data/postgres"
    socket_dir = root / "runtime/postgres-socket"
    data.parent.mkdir(parents=True)
    socket_dir.mkdir(parents=True)
    _command(
        [binaries["pg_dump"], "--format=custom", "--no-owner", "--no-acl",
         "--file", str(dump)],
        cwd=root, env=_source_dump_environment(source_database_url, root),
    )
    _command(
        [binaries["initdb"], "-D", str(data), "--username=postgres", "--auth=trust"],
        cwd=root, env=_postgres_environment(root, port),
    )
    with (data / "postgresql.conf").open("a", encoding="utf-8") as handle:
        handle.write(
            f"\nport={port}\nlisten_addresses='127.0.0.1'\n"
            f"unix_socket_directories='{socket_dir}'\n"
        )
    _command(
        [binaries["pg_ctl"], "-D", str(data), "-l", str(root / "postgres.log"),
         "-w", "start"],
        cwd=root, env=_postgres_environment(root, port), timeout=120,
    )
    _command(
        [binaries["createdb"], "vkpi_gate"], cwd=root,
        env=_postgres_environment(root, port), timeout=120,
    )
    _command(
        [binaries["pg_restore"], "--exit-on-error", "--no-owner", "--no-acl",
         "--dbname=vkpi_gate", str(dump)],
        cwd=root, env=_postgres_environment(root, port, "vkpi_gate"),
    )
    return data, dump


def _start_process(
    arguments: Sequence[str], *, cwd: Path, env: Mapping[str, str], log: Path,
    sandbox_profile: str | None = None,
) -> tuple[ManagedProcess, object]:
    handle = log.open("wb")
    try:
        process = subprocess.Popen(
            sandboxed(arguments, sandbox_profile) if sandbox_profile else list(arguments),
            cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL,
            stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
        )
    except Exception:
        handle.close()
        raise
    try:
        pgid, sid, identity, _command = _pid_record(process.pid)
        if pgid != process.pid or sid != process.pid:
            raise StrictRuntimeGateError("isolated child did not create a private process group")
    except Exception:
        try:
            process.terminate(); process.wait(timeout=5)
        except Exception:
            try:
                process.kill(); process.wait(timeout=5)
            except Exception:
                pass
        handle.close()
        raise
    return ManagedProcess(process, process.pid, pgid, sid, identity), handle


def _stop_processes(processes: Sequence[ManagedProcess]) -> tuple[list[dict[str, object]], list[str]]:
    receipts: list[dict[str, object]] = []
    errors: list[str] = []
    for process in reversed(processes):
        try:
            if process.poll() is None:
                pgid, identity = _pid_identity(process.pid)
                if pgid != process.pgid or identity != process.start_identity:
                    raise StrictRuntimeGateError(f"process identity changed: {process.pid}")
            else:
                group = subprocess.run(
                    ["/bin/ps", "-o", "sid=", "-g", str(process.pgid)],
                    check=False, capture_output=True, text=True, timeout=5,
                ).stdout.split()
                if not group:
                    continue
                if any(int(sid) != process.session_id for sid in group):
                    raise StrictRuntimeGateError(f"process group ownership changed: {process.pgid}")
            os.killpg(process.pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as exc:
            errors.append(f"terminate pid={process.pid}: {type(exc).__name__}: {exc}")
    for process in reversed(processes):
        try:
            returncode = process.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                pgid, identity = _pid_identity(process.pid)
                if pgid != process.pgid or identity != process.start_identity:
                    raise StrictRuntimeGateError(f"process identity changed: {process.pid}")
                os.killpg(process.pgid, signal.SIGKILL)
                returncode = process.process.wait(timeout=5)
            except ProcessLookupError:
                returncode = process.process.poll()
            except Exception as exc:
                errors.append(f"kill pid={process.pid}: {type(exc).__name__}: {exc}")
                returncode = process.process.poll()
        try:
            os.killpg(process.pgid, 0)
            group_alive = True
        except ProcessLookupError:
            group_alive = False
        except PermissionError:
            group_alive = True
        if group_alive:
            try:
                group = subprocess.run(
                    ["/bin/ps", "-o", "sid=", "-g", str(process.pgid)],
                    check=False, capture_output=True, text=True, timeout=5,
                ).stdout.split()
                if not group or any(int(sid) != process.session_id for sid in group):
                    raise StrictRuntimeGateError(f"process group ownership changed: {process.pgid}")
                os.killpg(process.pgid, signal.SIGKILL)
                for _ in range(20):
                    try:
                        os.killpg(process.pgid, 0)
                    except ProcessLookupError:
                        group_alive = False
                        break
                    time.sleep(0.1)
            except Exception as exc:
                errors.append(f"group kill pgid={process.pgid}: {type(exc).__name__}: {exc}")
        stopped = returncode is not None and not group_alive
        if not stopped:
            errors.append(f"process remained live: {process.pid}")
        receipts.append({"pid": process.pid, "pgid": process.pgid,
                         "returncode": returncode, "stopped": stopped})
    return receipts, errors


def _remove_exact_runtime_root(root: Path, identity: tuple[int, int]) -> None:
    info = root.lstat()
    if (
        root.parent != Path("/tmp") or not root.name.startswith(RUNTIME_PREFIX)
        or root.is_symlink() or not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != identity or info.st_uid != os.geteuid()
    ):
        raise StrictRuntimeGateError("strict runtime root identity changed")
    shutil.rmtree(root)


def _port_closed(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25); return probe.connect_ex(("127.0.0.1", port)) == errno.ECONNREFUSED


def _stop_private_postgres(
    *, root: Path, data: Path, pg_ctl: str | None, port: int,
) -> dict[str, object]:
    pid_file = data / "postmaster.pid"
    if not pid_file.is_file():
        return {"attempted": False, "stopped": True}
    if not pg_ctl:
        raise StrictRuntimeGateError("bound pg_ctl is unavailable during cleanup")
    pid = int(pid_file.read_text(encoding="utf-8").splitlines()[0])
    try:
        _pgid, _sid, _started, command = _pid_record(pid)
    except StrictRuntimeGateError:
        if _port_closed(port):
            pid_file.unlink(missing_ok=True)
            return {"attempted": True, "stopped": True, "stale_pidfile": True, "pid": pid}
        raise
    if str(data) not in command:
        raise StrictRuntimeGateError("postmaster identity is not bound to private data dir")
    _command(
        [pg_ctl, "-D", str(data), "stop", "-m", "fast", "-t", "30"],
        cwd=root, env=_postgres_environment(root, port), timeout=60,
    )
    if pid_file.is_file():
        raise StrictRuntimeGateError("private postmaster pidfile remained after stop")
    return {"attempted": True, "stopped": True, "pid": pid, "data": str(data)}


def _finalize_runtime_cleanup(
    *, root: Path, root_identity: tuple[int, int], processes: Sequence[ManagedProcess],
    handles: Sequence[object], pg_ctl: str | None, ports: RuntimePorts | None,
    evidence: Path, run_number: int,
) -> str:
    errors: list[str] = []
    stop_errors: list[str] = []
    process_cleanup_complete = False
    try:
        process_receipts, stop_errors = _stop_processes(processes)
        errors.extend(stop_errors)
        process_cleanup_complete = (len(process_receipts) == len(processes) and not stop_errors
                                    and all(item.get("stopped") is True for item in process_receipts))
    except Exception as exc:
        process_receipts = []
        errors.append(f"workers/web cleanup: {type(exc).__name__}: {exc}")
    for handle in handles:
        try:
            handle.close()
        except Exception as exc:
            errors.append(f"log handle cleanup: {type(exc).__name__}: {exc}")
    try:
        postgres_receipt = _stop_private_postgres(
            root=root, data=root / "runtime/data/postgres", pg_ctl=pg_ctl,
            port=ports.postgres if ports else 0,
        )
    except Exception as exc:
        postgres_receipt = {"attempted": True, "stopped": False}
        errors.append(f"postgres cleanup: {type(exc).__name__}: {exc}")
    port_states: dict[str, bool] = {}
    for port in ports.values() if ports else ():
        try:
            port_states[str(port)] = _port_closed(port)
            if not port_states[str(port)]:
                errors.append(f"loopback port remained open: {port}")
        except Exception as exc:
            port_states[str(port)] = False
            errors.append(f"port probe {port}: {type(exc).__name__}: {exc}")
    try:
        if (
            not process_cleanup_complete
            or postgres_receipt.get("stopped") is not True
            or any(closed is not True for closed in port_states.values())
        ):
            raise StrictRuntimeGateError("live process evidence blocks root removal")
        _remove_exact_runtime_root(root, root_identity)
    except Exception as exc:
        errors.append(f"root cleanup: {type(exc).__name__}: {exc}")
    cleanup = {
        "root": str(root), "removed": not root.exists(), "ports_closed": port_states,
        "processes": process_receipts, "postgres": postgres_receipt,
        "errors": errors, "pass": not errors and not root.exists(),
    }
    evidence.mkdir(parents=True, exist_ok=True)
    cleanup_path = evidence / f"run-{run_number}-cleanup.json"
    cleanup_path.write_text(json.dumps(cleanup, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cleanup_hash = _sha256_path(cleanup_path)
    if errors or root.exists():
        raise StrictRuntimeGateError("strict runtime cleanup failed: " + "; ".join(errors))
    return cleanup_hash


def _wait_runtime_ready(url: str, processes: Sequence[subprocess.Popen[bytes]], timeout: int) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last = "unavailable"
    while time.monotonic() < deadline:
        for process in processes:
            if process.poll() is not None:
                raise StrictRuntimeGateError(
                    f"isolated runtime process exited early: pid={process.pid} rc={process.process.returncode}"
                )
        try:
            with opener.open(url, timeout=1) as response:
                payload = json.loads(response.read())
            trust = payload.get("trust") if isinstance(payload, dict) else {}
            redis = trust.get("redis_worker_fleet") if isinstance(trust, dict) else {}
            if (
                payload.get("status") == "ok"
                and trust.get("worker_online") is True
                and isinstance(redis, dict)
                and redis.get("online") is True
            ):
                return
            last = str(payload.get("status"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last = type(exc).__name__
        time.sleep(0.25)
    raise StrictRuntimeGateError(f"isolated runtime readiness timed out: {last}")


def _copy_bound_manifest(
    *, candidate: Path, root: Path, expected_sha256: str,
) -> tuple[Path, dict[str, object]]:
    source_manifest = candidate.with_suffix(candidate.suffix + ".manifest.json")
    try:
        before = source_manifest.lstat()
        if (
            source_manifest.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
        ):
            raise StrictRuntimeGateError("Phase A manifest is not a trusted regular file")
        descriptor = os.open(
            source_manifest,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise StrictRuntimeGateError("Phase A manifest identity changed before copy")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                manifest_bytes = handle.read()
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise StrictRuntimeGateError("Phase A manifest is unavailable") from exc
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        or hashlib.sha256(manifest_bytes).hexdigest() != expected_sha256
    ):
        raise StrictRuntimeGateError("Phase A manifest hash changed before strict runtime")
    try:
        payload = json.loads(manifest_bytes.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StrictRuntimeGateError("Phase A manifest is invalid") from exc
    if not isinstance(payload, dict):
        raise StrictRuntimeGateError("Phase A manifest payload is invalid")
    target = root / "candidate.manifest.json"
    write_owned_file_exclusive(target, manifest_bytes)
    if _sha256_path(target) != expected_sha256:
        raise StrictRuntimeGateError("strict runtime manifest copy hash mismatch")
    return target, payload


def _rebuild_clean_source(
    *, source: Path, phase_payload: Mapping[str, object], root: Path,
) -> Path:
    from scripts.ops.run_isolated_worktree_gate import _prepare_clean_mirror

    capsule_record = phase_payload.get("dirty_source_capsule")
    if not isinstance(capsule_record, Mapping):
        raise StrictRuntimeGateError("Phase A capsule evidence is missing")
    capsule = Path(str(capsule_record.get("snapshot_path", ""))).resolve()
    manifest = Path(str(capsule_record.get("manifest_path", ""))).resolve()
    capsule_payload = json.loads(manifest.read_text(encoding="utf-8"))
    mirror, bridge = _prepare_clean_mirror(
        source=source, capsule=capsule, capsule_payload=capsule_payload,
        temporary_root=root / "controller",
    )
    expected_bridge = phase_payload.get("provenance_bridge")
    if not isinstance(expected_bridge, Mapping) or any(
        bridge.get(name) != expected_bridge.get(name)
        for name in ("git_head", "git_tree", "branch", "capsule_content_bridge_sha256")
    ):
        raise StrictRuntimeGateError("rebuilt strict source identity differs from Phase A")
    for link, target in (
        (mirror / ".venv", source / ".venv"),
        (mirror / "frontend/node_modules", source / "frontend/node_modules"),
    ):
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=target.is_dir())
    return mirror


def _phase_capsule_identity(
    phase_payload: Mapping[str, object],
) -> dict[str, object]:
    record = phase_payload.get("dirty_source_capsule")
    if not isinstance(record, Mapping):
        raise StrictRuntimeGateError("Phase A capsule evidence is missing")
    capsule = Path(str(record.get("snapshot_path", ""))).resolve()
    manifest = capsule.with_suffix(capsule.suffix + ".manifest.json")
    if str(manifest) != str(Path(str(record.get("manifest_path", ""))).resolve()):
        raise StrictRuntimeGateError("Phase A capsule manifest path mismatch")
    verified = verify_manifest(
        argparse.Namespace(manifest=str(manifest), snapshot=str(capsule))
    )
    expected = {
        "candidate_content_sha256": record.get("candidate_content_sha256"),
        "candidate_file_count": record.get("candidate_file_count"),
        "manifest_sha256": record.get("manifest_sha256"),
        "snapshot_path": record.get("snapshot_path"),
        "source_branch": record.get("source_branch"),
        "source_content_sha256": record.get("source_content_sha256"),
        "source_head": record.get("source_head"),
        "source_status_sha256": record.get("source_status_sha256"),
        "source_worktree_dirty": record.get("source_worktree_dirty"),
    }
    if (
        verified.get("content_sha256") != expected["candidate_content_sha256"]
        or verified.get("file_count") != expected["candidate_file_count"]
        or _sha256_path(manifest) != expected["manifest_sha256"]
        or str(capsule) != str(Path(str(expected["snapshot_path"])).resolve())
        or not expected["source_branch"]
        or not expected["source_content_sha256"]
        or not expected["source_head"]
        or not expected["source_status_sha256"]
        or not isinstance(expected["source_worktree_dirty"], bool)
    ):
        raise StrictRuntimeGateError("capsule differs from Phase A identity")
    return expected


def _run_once(
    *, run_number: int, source: Path, candidate: Path,
    phase_payload: Mapping[str, object], source_database_url: str,
    evidence: Path, timeout: int,
) -> dict[str, object]:
    phase_identity = _phase_candidate_identity(candidate, phase_payload)
    phase_capsule_identity = _phase_capsule_identity(phase_payload)
    root = _private_root()
    ports: RuntimePorts | None = None
    root_identity = (root.lstat().st_dev, root.lstat().st_ino)
    processes: list[ManagedProcess] = []
    handles: list[object] = []
    cleanup: dict[str, object] = {"root": str(root), "removed": False}
    gate_result: dict[str, object] | None = None
    run_summary: dict[str, object] | None = None
    runtime_nonce = secrets.token_hex(32)
    started_at = time.monotonic()
    bound_pg_ctl: str | None = None
    health_env_file = root.with_name(root.name + ".health.env")
    health_env_identity: tuple[int, int] | None = None
    try:
        ports = RuntimePorts(*_unique_loopback_ports())
        for name in ("home", "tmp", "cache", "runtime", "logs", "receipts", "controller"):
            (root / name).mkdir(mode=0o700, exist_ok=True)
        (root / "controller/frontend-dist-rebuild").mkdir(mode=0o700)
        binaries = {name: _binary(name) for name in (
            "pg_dump", "initdb", "pg_ctl", "createdb", "pg_restore", "redis-server"
        )}
        bound_pg_ctl = binaries["pg_ctl"]
        clean_source = _rebuild_clean_source(source=source, phase_payload=phase_payload, root=root)
        from scripts.ops.trusted_npm_audit import _trusted_node, _trusted_npm, _trusted_npx
        from scripts.ops.trusted_git import trusted_git_executable
        npm, node = _trusted_npm(), _trusted_node(); npx = _trusted_npx(npm)
        physical_git = Path(trusted_git_executable())
        health_token = secrets.token_urlsafe(32)
        health_env_identity = write_owned_file_exclusive(
            health_env_file,
            f"OPS_HEALTH_TOKEN={health_token}\n".encode("utf-8"),
        )
        seatbelt = candidate_profile(
            candidate=candidate, clean_source=clean_source,
            venv=source / ".venv", node_modules=source / "frontend/node_modules",
            runtime_root=root, allowed_ports=ports.values(),
            listener_ports=(ports.web,),
            writable_paths=tuple(root / name for name in ("home", "tmp", "cache", "runtime", "logs")),
            allow_runtime_root_write=False,
        )
        verifier_seatbelt = candidate_profile(
            candidate=candidate, clean_source=clean_source,
            venv=source / ".venv", node_modules=source / "frontend/node_modules",
            runtime_root=root, allowed_ports=ports.values(),
            writable_paths=tuple(root / name for name in
                ("tmp", "home", "cache", "receipts", "controller/frontend-dist-rebuild")),
            allow_runtime_root_write=False,
            executable_dirs=(root / "controller",),
            executable_paths=(node, npm, npx, physical_git),
            readable_paths=(health_env_file,),
        )
        manifest, manifest_payload = _copy_bound_manifest(
            candidate=candidate,
            root=root,
            expected_sha256=str(phase_identity["manifest_sha256"]),
        )
        admitted_static_receipt, admitted_static_receipt_bytes = (
            _validate_controller_static_receipt(
                manifest=manifest_payload,
                snapshot=candidate,
            )
        )
        admitted_static_receipt_sha256 = hashlib.sha256(
            admitted_static_receipt_bytes
        ).hexdigest()
        identity = manifest_payload["build"]["identity"]
        recorded_source = manifest_payload.get("source")
        if (
            not isinstance(recorded_source, Mapping)
            or not isinstance(recorded_source.get("repo"), str)
        ):
            raise StrictRuntimeGateError(
                "Phase A manifest recorded source is missing"
            )
        pg_data, dump = _prepare_postgres(
            root=root, port=ports.postgres, source_database_url=source_database_url,
            binaries=binaries,
        )
        redis, redis_handle = _start_process(
            [binaries["redis-server"], "--bind", "127.0.0.1", "--port", str(ports.redis),
             "--save", "", "--appendonly", "no", "--dir", str(root / "runtime")],
            cwd=root, env={"HOME": str(root / "home"), "PATH": os.defpath},
            log=root / "logs/redis.log",
        )
        processes.append(redis); handles.append(redis_handle)
        reviewed_env = root / "strict-local.env"
        database_url = f"postgresql://postgres@127.0.0.1:{ports.postgres}/vkpi_gate"
        redis_url = f"redis://127.0.0.1:{ports.redis}/0"
        reviewed_env.write_text(
            f"LOCAL_DATABASE_URL={database_url}\nLOCAL_REDIS_URL={redis_url}\n"
            "JWT_SECRET=isolated-strict-runtime-not-production\n"
            f"OPS_HEALTH_TOKEN={health_token}\n",
            encoding="utf-8",
        )
        reviewed_env.chmod(0o600)
        (clean_source / ".env").unlink(missing_ok=True)
        (clean_source / ".env").symlink_to(reviewed_env)
        web_env = {
            "APP_BUILD_TIME": str(identity["build_time"]),
            "APP_GIT_BRANCH": str(identity["git_branch"]),
            "APP_GIT_SHA": str(identity["git_sha"]),
            "CANDIDATE_LOCAL_ENV_FILE": str(reviewed_env),
            "CANDIDATE_PYTHON_BIN": str((source / ".venv/bin/python").resolve(strict=True)),
            "CANDIDATE_PORT": str(ports.web), "CANDIDATE_ROOT": str(candidate),
            "CANDIDATE_RUNTIME": str(root / "runtime"), "HOME": str(root / "home"),
            "LANG": "C", "PATH": os.defpath, "PROJECT_ROOT": str(clean_source),
            "TMPDIR": str(root / "tmp"), "XDG_CACHE_HOME": str(root / "cache"),
        }
        web, web_handle = _start_process(
            ["/bin/bash", str(candidate / "scripts/ops/run_isolated_candidate_web.sh")],
            cwd=root, env=web_env, log=root / "logs/web.log", sandbox_profile=seatbelt,
        )
        processes.append(web); handles.append(web_handle)
        fence = root / "runtime/release-validation.fence"
        deadline = time.monotonic() + 30
        while not fence.is_file() and time.monotonic() < deadline:
            if web.poll() is not None:
                raise StrictRuntimeGateError("fenced web exited before creating its fence")
            time.sleep(0.1)
        if not fence.is_file():
            raise StrictRuntimeGateError("fenced web did not create release-validation marker")
        worker_env = _minimal_runtime_environment(
            root=root, candidate=candidate, source=clean_source, ports=ports,
            git_sha=str(identity["git_sha"]), branch=str(identity["git_branch"]), fence=fence,
        )
        main_worker, main_handle = _start_process(
            [str((source / ".venv/bin/python").resolve(strict=True)), "-B", "-m", "app.workers.apify_jobs_worker"],
            cwd=root, env={**worker_env, "VKPI_WORKER_HEARTBEAT_NAME": f"isolated-main-{ports.web}"},
            log=root / "logs/main-worker.log", sandbox_profile=seatbelt,
        )
        processes.append(main_worker); handles.append(main_handle)
        redis_worker, redis_worker_handle = _start_process(
            [str((source / ".venv/bin/python").resolve(strict=True)), "-B", "-m", "app.workers.worker_main"],
            cwd=root, env=worker_env, log=root / "logs/redis-worker.log",
            sandbox_profile=seatbelt,
        )
        processes.append(redis_worker); handles.append(redis_worker_handle)
        health_url = f"http://127.0.0.1:{ports.web}/health"
        base_url = f"http://127.0.0.1:{ports.web}/"
        _wait_runtime_ready(health_url, processes, timeout)
        gate_result = run_deploy_gate(
            argparse.Namespace(
                manifest=str(manifest), snapshot=str(candidate),
                expected_head=str(identity["git_sha"]), expected_branch=str(identity["git_branch"]),
                source=str(clean_source), controller_source=str(clean_source),
                expected_recorded_source=str(recorded_source["repo"]),
                python=str(clean_source / ".venv/bin/python"),
                runtime_root=str(root), health_url=health_url, base_url=base_url,
                health_env_file=str(health_env_file),
                verify_json_out=str(root / "receipts/verify.json"),
                acceptance_json_out=str(root / "receipts/acceptance.json"),
                controller_owned_runtime=True,
                seatbelt_profile=verifier_seatbelt,
                runtime_nonce=runtime_nonce,
                runtime_ports=",".join(str(port) for port in sorted(ports.values())),
                candidate_digest=str(phase_identity["content_sha256"]),
                expected_static_receipt_sha256=admitted_static_receipt_sha256,
                expected_manifest_sha256=str(phase_identity["manifest_sha256"]),
            )
        )
        after_identity = _phase_candidate_identity(candidate, phase_payload)
        after_capsule_identity = _phase_capsule_identity(phase_payload)
        if (
            after_identity != phase_identity
            or after_capsule_identity != phase_capsule_identity
            or gate_result.get("content_sha256")
            != phase_identity["content_sha256"]
        ):
            raise StrictRuntimeGateError("candidate identity changed during strict run")
        if (
            gate_result.get("controller_static_receipt_sha256")
            != admitted_static_receipt_sha256
            or gate_result.get("candidate_manifest_sha256")
            != phase_identity["manifest_sha256"]
        ):
            raise StrictRuntimeGateError(
                "strict deploy consumed evidence differs from Phase A admission"
            )
        validated_receipts = _validate_bound_receipts(
            verify_path=root / "receipts/verify.json",
            acceptance_path=root / "receipts/acceptance.json",
            expected_head=str(phase_identity["git_head"]),
            expected_branch=str(phase_identity["branch"]), base_url=base_url,
            expected_steps=expected_receipt_plan(
                clean_source, controller_static_receipt=True
            )[0],
            expected_endpoints=expected_receipt_plan(
                clean_source, controller_static_receipt=True
            )[1],
            runtime_nonce=runtime_nonce,
            runtime_ports=",".join(str(port) for port in sorted(ports.values())),
            candidate_digest=str(phase_identity["content_sha256"]),
            static_receipt_sha256=admitted_static_receipt_sha256,
            manifest_sha256=str(phase_identity["manifest_sha256"]),
        )
        receipt_hashes = {
            "verify_sha256": str(validated_receipts["verify_sha256"]),
            "acceptance_sha256": str(validated_receipts["acceptance_sha256"]),
        }
        for name, bytes_name, hash_name in (
            ("verify.json", "verify_bytes", "verify_sha256"),
            ("acceptance.json", "acceptance_bytes", "acceptance_sha256"),
        ):
            receipt_bytes = validated_receipts.get(bytes_name)
            if not isinstance(receipt_bytes, bytes):
                raise StrictRuntimeGateError(
                    f"strict runtime did not bind {name} bytes"
                )
            _persist_receipt_bytes(
                evidence / f"run-{run_number}-{name}",
                receipt_bytes,
                receipt_hashes[hash_name],
            )
        run_summary = {
            "run": run_number, "pass": True, "ports": list(ports.values()),
            "runtime_nonce": runtime_nonce,
            "candidate_content_sha256": gate_result["content_sha256"],
            "candidate_manifest_sha256": phase_identity["manifest_sha256"],
            "controller_static_receipt_sha256": admitted_static_receipt_sha256,
            "synthetic_git_head": phase_identity["git_head"],
            "synthetic_git_tree": phase_identity["git_tree"],
            "capsule_digest": phase_identity["capsule_digest"],
            "receipt_hashes": receipt_hashes,
            "dump_bytes": dump.stat().st_size, "duration_seconds": round(time.monotonic() - started_at, 3),
            "provider_credentials_absent": True,
            "provider_offline_gate_active": True, "queue_claims_allowed": False,
        }
        return run_summary
    finally:
        try:
            cleanup_hash = _finalize_runtime_cleanup(
                root=root, root_identity=root_identity, processes=processes, handles=handles,
                pg_ctl=bound_pg_ctl, ports=ports, evidence=evidence, run_number=run_number,
            )
            if run_summary is not None:
                run_summary["cleanup_receipt_sha256"] = cleanup_hash
        finally:
            if health_env_identity is not None:
                remove_owned_path(health_env_file, health_env_identity)


def run_strict_runtime_gate(
    *, source: Path, candidate: Path, phase_payload: Mapping[str, object],
    source_database_url: str, evidence_dir: Path, timeout: int = 180,
) -> dict[str, object]:
    if not candidate.is_dir() or candidate.is_symlink():
        raise StrictRuntimeGateError("strict runtime candidate is unavailable")
    phase_identity = _phase_candidate_identity(candidate, phase_payload)
    source_control = control_plane_digest(source)
    candidate_control = control_plane_digest(candidate)
    if source_control["sha256"] != candidate_control["sha256"]:
        raise StrictRuntimeGateError("source/candidate control-plane digest mismatch")
    evidence_dir.mkdir(parents=True, exist_ok=False)
    evidence_dir.chmod(0o700)
    runs = [
        _run_once(
            run_number=index, source=source, candidate=candidate,
            phase_payload=phase_payload, source_database_url=source_database_url,
            evidence=evidence_dir, timeout=timeout,
        )
        for index in range(1, STRICT_RUNS + 1)
    ]
    digests = {str(item["candidate_content_sha256"]) for item in runs}
    manifest_digests = {str(item["candidate_manifest_sha256"]) for item in runs}
    all_ports = [port for item in runs for port in item["ports"]]
    if len(digests) != 1 or digests != {str(phase_identity["content_sha256"])}:
        raise StrictRuntimeGateError("three strict runs did not share one immutable candidate")
    if manifest_digests != {str(phase_identity["manifest_sha256"])} or len(set(all_ports)) != len(all_ports):
        raise StrictRuntimeGateError("strict runs were not uniquely bound to Phase A")
    if control_plane_digest(source) != source_control or control_plane_digest(candidate) != candidate_control:
        raise StrictRuntimeGateError("control-plane changed during strict runtime")
    result = {
        "attempted": True, "classification": "isolated_strict_runtime_acceptance",
        "pass": all(item["pass"] is True for item in runs), "runs": runs,
        "run_count": len(runs), "same_candidate": True,
        "source_database_access": "read_only_pg_dump",
        "provider_tripwire": "environment_verified_provider_free",
        "candidate_identity": phase_identity,
        "control_plane": {"source": source_control, "candidate": candidate_control,
                          "binding": "operator-reviewed controller at invocation"},
    }
    result["controller_attestation"] = sign_attestation(
        {"candidate_identity": phase_identity, "control_plane_sha256": source_control["sha256"],
         "runs": runs, "trust_boundary": "local operator-reviewed controller"},
        evidence_dir / "controller-attestation.json",
    )
    return result


__all__ = [
    "STRICT_RUNS", "StrictRuntimeGateError", "run_strict_runtime_gate",
    "strict_runtime_preflight",
]


if __name__ == "__main__":
    print(json.dumps(strict_runtime_preflight(), ensure_ascii=False, sort_keys=True))
