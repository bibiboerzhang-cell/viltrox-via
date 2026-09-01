from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import base64
import json
import signal
import errno
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scripts.ops import isolated_strict_runtime_gate as strict
from scripts.ops.freeze_worktree_candidate import _atomic_json, _run_git_text
from scripts.ops.isolated_runtime_attestation import control_plane_digest, sign_attestation
from scripts.ops.run_isolated_worktree_gate import (
    IsolatedWorktreeGateError, _assert_source_unchanged, _capture_source_state,
    _source_git_text,
)
from scripts.ops.strict_runtime_seatbelt import (
    SeatbeltError,
    candidate_profile,
    run_preflight,
    sandboxed,
)
from scripts.ops import trusted_npm_audit
from scripts.ops.controlled_candidate_process import run_controlled_candidate
from scripts.ops.freeze_git_bridge import readonly_snapshot_git_environment
from scripts.ops.trusted_git import trusted_git_executable
from scripts.ops.isolated_runtime_attestation import copy_receipt_nofollow


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q"], cwd=path, check=True)
    (path / "tracked").write_text("one\n", encoding="utf-8")
    subprocess.run(["/usr/bin/git", "add", "tracked"], cwd=path, check=True)
    subprocess.run(
        ["/usr/bin/git", "-c", "user.name=Fixture", "-c",
         "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
        cwd=path, check=True,
    )
    return path


def test_controller_git_ignores_hostile_path_and_repository_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = _repo(tmp_path / "real")
    hostile = _repo(tmp_path / "hostile")
    wrapper = tmp_path / "bin"; wrapper.mkdir()
    (wrapper / "git").write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    (wrapper / "git").chmod(0o755)
    expected = subprocess.check_output(
        ["/usr/bin/git", "rev-parse", "HEAD"], cwd=real, text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    ).strip()
    monkeypatch.setenv("PATH", str(wrapper))
    monkeypatch.setenv("GIT_DIR", str(hostile / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(hostile))
    monkeypatch.setenv("GIT_INDEX_FILE", str(hostile / ".git/index"))
    assert _run_git_text(real, "rev-parse", "HEAD") == expected
    assert _source_git_text(real, "rev-parse", "HEAD") == expected


def test_source_state_detects_same_status_content_rewrite(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "tracked").write_text("dirty-one\n", encoding="utf-8")
    before = _capture_source_state(repo)
    (repo / "tracked").write_text("dirty-two\n", encoding="utf-8")
    with pytest.raises(IsolatedWorktreeGateError, match="content bytes"):
        _assert_source_unchanged(repo, before, phase="hostile rewrite")


def test_exact_runtime_cleanup_rejects_path_swap_without_touching_replacement() -> None:
    root = strict._private_root()
    identity = (root.lstat().st_dev, root.lstat().st_ino)
    original = root.with_name(root.name + ".original")
    root.rename(original); root.mkdir(mode=0o700)
    try:
        with pytest.raises(strict.StrictRuntimeGateError, match="identity changed"):
            strict._remove_exact_runtime_root(root, identity)
        assert root.is_dir() and original.is_dir()
    finally:
        shutil.rmtree(root); shutil.rmtree(original)


def test_control_plane_digest_rejects_candidate_verifier_forge(tmp_path: Path) -> None:
    source, candidate = tmp_path / "source", tmp_path / "candidate"
    for root in (source, candidate):
        (root / "scripts").mkdir(parents=True)
        (root / "scripts/verify.sh").write_text("exit 0\n", encoding="utf-8")
    baseline = control_plane_digest(source)
    (candidate / "scripts/verify.sh").write_text("printf fake\n", encoding="utf-8")
    assert control_plane_digest(candidate)["sha256"] != baseline["sha256"]


def test_controller_attestation_is_offline_verifiable_ed25519(tmp_path: Path) -> None:
    target = tmp_path / "attestation.json"
    sign_attestation({"candidate": "abc", "nonce": "unique"}, target)
    record = json.loads(target.read_text(encoding="utf-8"))
    message = json.dumps(record["payload"], sort_keys=True, separators=(",", ":")).encode()
    Ed25519PublicKey.from_public_bytes(base64.b64decode(record["public_key_b64"])).verify(
        base64.b64decode(record["signature_b64"]), message
    )


def test_seatbelt_positive_and_negative_preflight(tmp_path: Path) -> None:
    # The checked-out .venv is deliberately not part of Git and clean-clone
    # harnesses often reuse one through a root symlink.  Build the smallest
    # physical interpreter root this unit needs instead: candidate_profile must
    # continue rejecting a symlink at any trusted root, while an executable
    # symlink *inside* that physical root is resolved to the already trusted
    # host interpreter before sandbox-exec runs it.
    source = tmp_path / "source"
    venv_bin = source / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(Path(sys.executable).resolve())

    result = run_preflight(source)
    assert result["pass"] is True
    assert all(result["checks"].values())


def test_candidate_profile_still_rejects_raw_symlink_roots(tmp_path: Path) -> None:
    roots = {
        name: tmp_path / name
        for name in ("candidate", "clean", "venv", "node_modules", "runtime")
    }
    for path in roots.values():
        path.mkdir()
    linked_candidate = tmp_path / "linked-candidate"
    linked_candidate.symlink_to(roots["candidate"], target_is_directory=True)

    with pytest.raises(SeatbeltError, match="physical non-symlink paths"):
        candidate_profile(
            candidate=linked_candidate,
            clean_source=roots["clean"],
            venv=roots["venv"],
            node_modules=roots["node_modules"],
            runtime_root=roots["runtime"],
            allowed_ports=(18103, 15432, 16379),
        )


def test_process_identity_allows_command_exec_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = iter([(41, 41, "Fri Aug 29 10:00:00 2026", "/bin/bash gate"),
                    (41, 41, "Fri Aug 29 10:00:00 2026", "python -m gunicorn")])
    monkeypatch.setattr(strict, "_pid_record", lambda _pid: next(records))
    assert strict._pid_identity(41) == (41, "Fri Aug 29 10:00:00 2026")
    assert strict._pid_identity(41) == (41, "Fri Aug 29 10:00:00 2026")


def test_start_identity_failure_reaps_child_and_closes_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Child:
        pid = 77
        terminated = waited = False
        def terminate(self): self.terminated = True
        def wait(self, timeout): self.waited = True; return 1
        def kill(self): raise AssertionError("kill should not be needed")
    child = Child()
    monkeypatch.setattr(strict.subprocess, "Popen", lambda *_a, **_k: child)
    monkeypatch.setattr(strict, "_pid_record", lambda _pid: (_ for _ in ()).throw(
        strict.StrictRuntimeGateError("identity unavailable")
    ))
    log = tmp_path / "child.log"
    with pytest.raises(strict.StrictRuntimeGateError):
        strict._start_process(["fixture"], cwd=tmp_path, env={}, log=log)
    assert child.terminated and child.waited
    log.rename(tmp_path / "closed.log")


def test_process_cleanup_records_term_wait_and_kill_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Child:
        pid = 81
        def poll(self): return None
        def wait(self, timeout): raise subprocess.TimeoutExpired("fixture", timeout)
    managed = strict.ManagedProcess(Child(), 81, 81, 81, "stable-start")
    monkeypatch.setattr(strict, "_pid_identity", lambda _pid: (81, "stable-start"))
    calls: list[int] = []
    def fail_signals(_pgid: int, sig: int) -> None:
        calls.append(sig)
        if sig == 0:
            raise ProcessLookupError
        raise OSError("fixture signal failure")
    monkeypatch.setattr(strict.os, "killpg", fail_signals)
    receipts, errors = strict._stop_processes([managed])
    assert signal.SIGTERM in calls and signal.SIGKILL in calls
    assert any("terminate pid=81" in error for error in errors)
    assert any("kill pid=81" in error for error in errors)
    assert receipts[0]["stopped"] is False


def test_cleanup_faults_still_attempt_postgres_ports_and_root_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"; root.mkdir()
    evidence = tmp_path / "evidence"
    stages: list[str] = []
    monkeypatch.setattr(strict, "_stop_processes", lambda _items: ([], ["term/wait/kill failed"]))
    def fail_postgres(**_kwargs):
        stages.append("postgres"); raise OSError("pg_ctl failed")
    monkeypatch.setattr(strict, "_stop_private_postgres", fail_postgres)
    monkeypatch.setattr(strict, "_port_closed", lambda port: stages.append(f"port:{port}") or True)
    monkeypatch.setattr(strict, "_remove_exact_runtime_root", lambda *_args: stages.append("root"))
    with pytest.raises(strict.StrictRuntimeGateError, match="cleanup failed"):
        strict._finalize_runtime_cleanup(
            root=root, root_identity=(root.stat().st_dev, root.stat().st_ino),
            processes=[], handles=[], pg_ctl="/fixture/pg_ctl",
            ports=strict.RuntimePorts(18103, 15432, 16379), evidence=evidence, run_number=1,
        )
    assert stages == ["postgres", "port:18103", "port:15432", "port:16379"]
    receipt = json.loads((evidence / "run-1-cleanup.json").read_text(encoding="utf-8"))
    assert receipt["pass"] is False
    assert any("root cleanup" in error for error in receipt["errors"])


def test_cleanup_records_rmtree_failure_after_other_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"; root.mkdir()
    stages: list[str] = []
    monkeypatch.setattr(strict, "_stop_processes", lambda _items: ([], []))
    monkeypatch.setattr(strict, "_stop_private_postgres", lambda **_kwargs: stages.append("postgres") or {"stopped": True})
    monkeypatch.setattr(strict, "_port_closed", lambda port: stages.append(f"port:{port}") or True)
    monkeypatch.setattr(strict, "_remove_exact_runtime_root", lambda *_args: (_ for _ in ()).throw(OSError("rmtree failed")))
    with pytest.raises(strict.StrictRuntimeGateError, match="rmtree failed"):
        strict._finalize_runtime_cleanup(
            root=root, root_identity=(root.stat().st_dev, root.stat().st_ino),
            processes=[], handles=[], pg_ctl="/fixture/pg_ctl",
            ports=strict.RuntimePorts(18103, 15432, 16379),
            evidence=tmp_path / "evidence", run_number=2,
        )
    assert stages == ["postgres", "port:18103", "port:15432", "port:16379"]


def test_cleanup_never_removes_root_when_process_receipts_are_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"; root.mkdir()
    removed: list[Path] = []
    monkeypatch.setattr(
        strict, "_stop_processes",
        lambda _items: (_ for _ in ()).throw(OSError("process cleanup unavailable")),
    )
    monkeypatch.setattr(strict, "_stop_private_postgres", lambda **_kwargs: {"stopped": True})
    monkeypatch.setattr(strict, "_port_closed", lambda _port: True)
    monkeypatch.setattr(strict, "_remove_exact_runtime_root", lambda path, _identity: removed.append(path))
    with pytest.raises(strict.StrictRuntimeGateError, match="cleanup failed"):
        strict._finalize_runtime_cleanup(
            root=root, root_identity=(root.stat().st_dev, root.stat().st_ino),
            processes=[object()], handles=[], pg_ctl="/fixture/pg_ctl",
            ports=strict.RuntimePorts(18103, 15432, 16379),
            evidence=tmp_path / "evidence", run_number=3,
        )
    assert removed == []
    assert root.is_dir()


def test_trusted_npm_ignores_ambient_path_and_uses_controlled_child_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    npm = tmp_path / "trusted/npm-cli.js"
    npm.parent.mkdir(); npm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8"); npm.chmod(0o755)
    hostile = tmp_path / "hostile"; hostile.mkdir()
    (hostile / "npm").write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    (hostile / "npm").chmod(0o755)
    frontend = tmp_path / "frontend"; frontend.mkdir()
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(trusted_npm_audit, "TRUSTED_NPM_CANDIDATES", (npm,))
    monkeypatch.setattr(trusted_npm_audit, "TRUSTED_NODE_CANDIDATES", (Path("/bin/sh"),))
    monkeypatch.setenv("PATH", str(hostile))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "forged")
    monkeypatch.setenv("VKPI_TEST_TRUSTED_NPM", str(hostile / "npm"))
    monkeypatch.setenv("VKPI_TEST_TRUSTED_NODE", str(hostile / "node"))
    captured: dict[str, object] = {}
    def run(arguments, **kwargs):
        captured.update(arguments=arguments, **kwargs)
        return subprocess.CompletedProcess(arguments, 0, b"", b"")
    monkeypatch.setattr(trusted_npm_audit.subprocess, "run", run)
    trusted_npm_audit.run_trusted_npm_audit(frontend, tmp_path / "receipt.json")
    assert captured["arguments"][1] == str(npm.resolve())
    assert str(hostile) not in captured["env"]["PATH"]
    assert captured["env"]["PATH"] == os.defpath


def test_strict_binary_ignores_hostile_postgres_bin_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = tmp_path / "bin"; hostile.mkdir()
    for name in ("pg_dump", "redis-server"):
        target = hostile / name
        target.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8"); target.chmod(0o755)
    monkeypatch.setenv("POSTGRES_BIN", str(hostile))
    monkeypatch.setenv("PATH", str(hostile))
    assert not strict._binary("pg_dump").startswith(str(hostile))
    assert not strict._binary("redis-server").startswith(str(hostile))


def test_candidate_profile_cannot_overwrite_controller_receipt(tmp_path: Path) -> None:
    candidate, clean, venv, node, runtime = (
        tmp_path / name for name in ("candidate", "clean", "venv", "node", "runtime")
    )
    for path in (candidate, clean, venv, node, runtime): path.mkdir()
    for name in ("home", "tmp", "cache", "runtime", "logs", "receipts"):
        (runtime / name).mkdir()
    receipt = runtime / "receipts/verify.json"
    receipt.write_text("controller\n", encoding="utf-8")
    profile = candidate_profile(
        candidate=candidate, clean_source=clean, venv=venv, node_modules=node,
        runtime_root=runtime, allowed_ports=(),
        writable_paths=tuple(runtime / name for name in ("home", "tmp", "cache", "runtime", "logs")),
        allow_runtime_root_write=False,
    )
    done = subprocess.run(
        sandboxed(["/bin/sh", "-c", f"printf hacked > {receipt}"] , profile),
        capture_output=True, timeout=5,
        env={"HOME": str(runtime / "home"), "PATH": "/usr/bin:/bin"},
    )
    assert done.returncode != 0
    assert receipt.read_text(encoding="utf-8") == "controller\n"


def test_candidate_profile_allows_only_metadata_for_executable_ancestors(
    tmp_path: Path,
) -> None:
    candidate, clean, venv, node_modules, runtime = (
        tmp_path / name
        for name in ("candidate", "clean", "venv", "node_modules", "runtime")
    )
    for path in (candidate, clean, venv, node_modules, runtime):
        path.mkdir()
    executable = tmp_path / "toolchain" / "bin" / "node"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    profile = candidate_profile(
        candidate=candidate,
        clean_source=clean,
        venv=venv,
        node_modules=node_modules,
        runtime_root=runtime,
        allowed_ports=(),
        executable_paths=(executable,),
        readable_paths=(tmp_path / "toolchain",),
    )

    assert f'(allow file-read-metadata (literal "{tmp_path / "toolchain"}"))' in profile
    assert f'(allow file-read-metadata (literal "{tmp_path}"))' in profile
    assert f'(allow file-read* (subpath "{executable.parent}"))' in profile
    assert f'(allow file-read* (subpath "{tmp_path / "toolchain"}"))' in profile
    assert f'(allow process-exec (subpath "{tmp_path / "toolchain"}"))' not in profile
    assert '(allow process-exec\n  (literal "/private/var/select/sh")' in profile
    assert '(allow process-exec (subpath "/private/var/select"))' not in profile
    assert '(allow file-read*\n  (literal "/opt")' in profile
    assert '  (subpath "/bin")' in profile
    assert '  (subpath "/usr/bin")' in profile


def test_candidate_profile_preserves_and_allows_unicode_paths(tmp_path: Path) -> None:
    root = tmp_path / "工程——路径"
    candidate, clean, venv, node_modules, runtime = (
        root / name
        for name in ("candidate", "clean", "venv", "node_modules", "runtime")
    )
    for path in (candidate, clean, venv, node_modules, runtime):
        path.mkdir(parents=True, exist_ok=True)
    marker = node_modules / "marker.txt"
    marker.write_text("ok\n", encoding="utf-8")

    profile = candidate_profile(
        candidate=candidate,
        clean_source=clean,
        venv=venv,
        node_modules=node_modules,
        runtime_root=runtime,
        allowed_ports=(),
    )

    assert str(root) in profile
    assert "\\u2014" not in profile
    done = subprocess.run(
        sandboxed(["/bin/cat", str(marker)], profile),
        capture_output=True,
        text=True,
        timeout=5,
        env={"HOME": str(runtime), "PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "ok\n"


def test_controlled_candidate_reaps_child_that_survives_parent(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import os,time,pathlib\n"
        "pid=os.fork()\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(pid) if pid else '') if pid else None\n"
        "os._exit(0) if pid else time.sleep(30)\n"
    )
    result = run_controlled_candidate(
        [str(Path(os.sys.executable).resolve()), "-c", script], cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, timeout=5,
    )
    assert result.returncode == 125
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_readonly_git_bridge_ignores_hostile_git_python_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _repo(tmp_path / "source")
    snapshot = tmp_path / "snapshot"; snapshot.mkdir()
    hostile = tmp_path / "hostile"; hostile.mkdir()
    for name in ("git", "python3"):
        target = hostile / name; target.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8"); target.chmod(0o755)
    monkeypatch.setenv("PATH", str(hostile))
    controller = tmp_path / "controller"
    with readonly_snapshot_git_environment(snapshot, source, bridge_parent=controller) as environment:
        wrapper = Path(environment["VKPI_FREEZE_GIT_WRAPPER"])
        assert wrapper.read_text(encoding="utf-8").splitlines()[0] == f"#!{Path(os.sys.executable).resolve()}"
        assert str(hostile) not in environment["PATH"]
        assert environment["VKPI_FREEZE_REAL_GIT"] == trusted_git_executable()
        if sys.platform == "darwin":
            assert environment["VKPI_FREEZE_REAL_GIT"] != "/usr/bin/git"
        for name in ("git", "python", "python3", "node", "npm", "npx"):
            observed = subprocess.check_output(
                ["/bin/sh", "-c", f"command -v {name}"], env={**environment, "HOME": str(tmp_path)}, text=True,
            ).strip()
            assert Path(observed).parent == wrapper.parent
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=snapshot, env={**environment, "HOME": str(tmp_path)},
            capture_output=True, text=True, check=True,
        )
    assert result.stdout.strip() == subprocess.check_output(
        [trusted_git_executable(), "rev-parse", "HEAD"], cwd=source, text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    ).strip()


def test_controlled_group_permission_error_is_unknown_and_never_signaled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.ops import controlled_candidate_process as controlled
    monkeypatch.setattr(
        controlled.subprocess, "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    calls: list[int] = []
    def denied(_pgid: int, sig: int) -> None:
        calls.append(sig); raise PermissionError("fixture")
    monkeypatch.setattr(controlled.os, "killpg", denied)
    assert controlled._group_state(41, 41) == "unknown"
    with pytest.raises(RuntimeError, match="ownership is unknown"):
        controlled._signal_owned_group(41, 41, signal.SIGTERM)
    assert calls == [0, 0]


@pytest.mark.parametrize("mode", ["timeout", "interrupt"])
def test_controlled_runner_finally_reaps_on_timeout_and_baseexception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    from scripts.ops import controlled_candidate_process as controlled
    class Child:
        pid = 51
        waits = 0
        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                if mode == "timeout": raise subprocess.TimeoutExpired("fixture", timeout)
                raise KeyboardInterrupt
            return -15
    child = Child()
    monkeypatch.setattr(controlled.subprocess, "Popen", lambda *_a, **_k: child)
    states = iter(["owned-live", "absent", "absent", "absent"])
    monkeypatch.setattr(controlled, "_group_state", lambda *_args: next(states))
    signals: list[int] = []
    monkeypatch.setattr(controlled, "_signal_owned_group", lambda _p, _s, sig: signals.append(sig))
    if mode == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            controlled.run_controlled_candidate(["fixture"], cwd=tmp_path, env={}, timeout=1)
    else:
        result = controlled.run_controlled_candidate(["fixture"], cwd=tmp_path, env={}, timeout=1)
        assert result.returncode == 124
    assert signals == [signal.SIGTERM]
    assert child.waits == 2


def test_managed_process_identity_works_with_real_darwin_sleep(tmp_path: Path) -> None:
    managed, handle = strict._start_process(
        ["/bin/sleep", "30"], cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}, log=tmp_path / "sleep.log",
    )
    try:
        assert managed.pgid == managed.pid == managed.session_id
        receipts, errors = strict._stop_processes([managed])
        assert errors == [] and receipts[0]["stopped"] is True
    finally:
        handle.close()


def test_receipt_copy_rejects_symlink_and_completes_partial_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.json"; source.write_bytes(b"x" * 257)
    expected = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    link = tmp_path / "source-link"; link.symlink_to(source)
    with pytest.raises(OSError):
        copy_receipt_nofollow(link, tmp_path / "rejected.json", expected)
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, data[:7]))
    target = tmp_path / "copied.json"
    copy_receipt_nofollow(source, target, expected)
    assert target.read_bytes() == source.read_bytes()


def test_receipt_copy_rejects_source_change_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.json"; source.write_bytes(b"stable")
    real_fstat = os.fstat; calls = 0
    def changed(fd):
        nonlocal calls
        info = real_fstat(fd); calls += 1
        if calls == 2:
            values = list(info); values[8] += 1
            return os.stat_result(values)
        return info
    monkeypatch.setattr(os, "fstat", changed)
    with pytest.raises(strict.StrictRuntimeGateError, match="changed while"):
        copy_receipt_nofollow(source, tmp_path / "target.json", "0" * 64)


def test_receipt_copy_removes_only_its_partial_target_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.json"; source.write_bytes(b"payload")
    expected = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(os, "write", lambda *_args: 0)
    target = tmp_path / "partial.json"
    with pytest.raises(strict.StrictRuntimeGateError, match="no progress"):
        copy_receipt_nofollow(source, target, expected)
    assert not target.exists()


@pytest.mark.parametrize(("result", "closed"), [
    (errno.ECONNREFUSED, True), (errno.ETIMEDOUT, False), (errno.EACCES, False),
])
def test_port_closed_accepts_only_connection_refused(
    monkeypatch: pytest.MonkeyPatch, result: int, closed: bool,
) -> None:
    class Probe:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def settimeout(self, _timeout): pass
        def connect_ex(self, _address): return result
    monkeypatch.setattr(strict.socket, "socket", lambda *_args: Probe())
    assert strict._port_closed(18103) is closed


def test_strict_private_root_initialization_failure_cleans_exact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(strict.os, "chown", lambda *_args: (_ for _ in ()).throw(OSError("fixture")))
    with pytest.raises(OSError, match="fixture"):
        strict._private_root(parent=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_manifest_publish_never_overwrites_concurrent_target(tmp_path: Path) -> None:
    target = tmp_path / "candidate.manifest.json"
    target.write_text("concurrent-owner\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _atomic_json(target, {"attacker": "replacement"})
    assert target.read_text(encoding="utf-8") == "concurrent-owner\n"
