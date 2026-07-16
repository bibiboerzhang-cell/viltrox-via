from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _load_guard() -> ModuleType:
    path = ROOT / "scripts" / "worker_pid_guard.py"
    spec = importlib.util.spec_from_file_location("worker_pid_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _marker(tmp_path: Path, value: str = "4321") -> Path:
    path = tmp_path / "worker.pid"
    path.write_text(value, encoding="utf-8")
    return path


def _classify(tmp_path: Path, evidence, marker: Path | None = None):
    root = tmp_path / "repo"
    log = root / "runtime" / "logs" / "worker.log"
    root.mkdir()
    log.parent.mkdir(parents=True)
    log.touch()
    return guard.classify_pidfile(
        marker or _marker(tmp_path),
        expected_root=root,
        expected_log=log,
        inspector=lambda _pid: evidence,
    )


def test_verified_worker_requires_module_cwd_and_lane_log(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    log = root / "runtime" / "logs" / "worker.log"
    root.mkdir()
    log.parent.mkdir(parents=True)
    log.touch()
    evidence = guard.ProcessEvidence(
        exists=True,
        command="python -m app.workers.apify_jobs_worker",
        cwd=str(root),
        log_fds=(str(log), str(log)),
    )

    result = guard.classify_pidfile(
        _marker(tmp_path),
        expected_root=root,
        expected_log=log,
        inspector=lambda _pid: evidence,
    )

    assert result.status == "verified_worker"
    assert result.safe_to_signal is True
    assert result.safe_to_remove_marker is False


def test_absent_pid_is_safe_marker_cleanup_but_never_safe_to_signal(tmp_path: Path) -> None:
    result = _classify(tmp_path, guard.ProcessEvidence(exists=False))

    assert result.status == "stale_absent"
    assert result.safe_to_remove_marker is True
    assert result.safe_to_signal is False


def test_reused_pid_with_foreign_command_is_never_signalled(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    log = root / "runtime" / "logs" / "worker.log"
    root.mkdir()
    log.parent.mkdir(parents=True)
    log.touch()
    evidence = guard.ProcessEvidence(
        exists=True,
        command="python unrelated_service.py",
        cwd=str(root),
        log_fds=(str(log),),
    )

    result = guard.classify_pidfile(
        _marker(tmp_path),
        expected_root=root,
        expected_log=log,
        inspector=lambda _pid: evidence,
    )

    assert result.status == "stale_foreign"
    assert result.safe_to_remove_marker is True
    assert result.safe_to_signal is False


def test_same_module_with_wrong_lane_log_is_foreign_to_marker(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    expected_log = root / "runtime" / "logs" / "worker-interactive.log"
    other_log = root / "runtime" / "logs" / "worker-bulk1.log"
    root.mkdir()
    expected_log.parent.mkdir(parents=True)
    expected_log.touch()
    other_log.touch()
    evidence = guard.ProcessEvidence(
        exists=True,
        command="python -m app.workers.apify_jobs_worker",
        cwd=str(root),
        log_fds=(str(other_log),),
    )

    result = guard.classify_pidfile(
        _marker(tmp_path),
        expected_root=root,
        expected_log=expected_log,
        inspector=lambda _pid: evidence,
    )

    assert result.status == "stale_foreign"
    assert result.safe_to_signal is False


def test_wrong_log_without_command_proof_is_indeterminate(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    expected_log = root / "runtime" / "logs" / "worker-interactive.log"
    other_log = root / "runtime" / "logs" / "worker-bulk1.log"
    root.mkdir()
    expected_log.parent.mkdir(parents=True)
    expected_log.touch()
    other_log.touch()
    evidence = guard.ProcessEvidence(
        exists=True,
        command=None,
        cwd=str(root),
        log_fds=(str(other_log),),
        errors=("ps_permission_denied",),
    )

    result = guard.classify_pidfile(
        _marker(tmp_path),
        expected_root=root,
        expected_log=expected_log,
        inspector=lambda _pid: evidence,
    )

    assert result.status == "indeterminate"
    assert result.safe_to_remove_marker is False
    assert result.safe_to_signal is False


def test_deleted_log_annotation_still_matches_exact_lane(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    log = root / "runtime" / "logs" / "worker.log"
    root.mkdir()
    log.parent.mkdir(parents=True)
    log.touch()
    evidence = guard.ProcessEvidence(
        exists=True,
        command="python -m app.workers.apify_jobs_worker",
        cwd=str(root),
        log_fds=(f"{log} (deleted)",),
    )

    result = guard.classify_pidfile(
        _marker(tmp_path),
        expected_root=root,
        expected_log=log,
        inspector=lambda _pid: evidence,
    )

    assert result.status == "verified_worker"
    assert result.safe_to_signal is True


def test_incomplete_permission_limited_evidence_fails_closed(tmp_path: Path) -> None:
    evidence = guard.ProcessEvidence(
        exists=True,
        command=None,
        cwd=None,
        log_fds=(),
        errors=("signal0_permission_denied", "ps_unavailable_rc_1"),
    )

    result = _classify(tmp_path, evidence)

    assert result.status == "indeterminate"
    assert result.safe_to_remove_marker is False
    assert result.safe_to_signal is False


def test_invalid_marker_is_removable_without_inspecting_process(tmp_path: Path) -> None:
    called = False

    def inspector(_pid: int):
        nonlocal called
        called = True
        raise AssertionError("must not inspect an invalid marker")

    root = tmp_path / "repo"
    log = root / "worker.log"
    root.mkdir()
    result = guard.classify_pidfile(
        _marker(tmp_path, "not-a-pid"),
        expected_root=root,
        expected_log=log,
        inspector=inspector,
    )

    assert called is False
    assert result.status == "invalid_marker"
    assert result.safe_to_remove_marker is True
    assert result.safe_to_signal is False


def test_missing_marker_is_a_noop(tmp_path: Path) -> None:
    result = guard.classify_pidfile(
        tmp_path / "missing.pid",
        expected_root=tmp_path,
        expected_log=tmp_path / "worker.log",
        inspector=lambda _pid: (_ for _ in ()).throw(AssertionError("unexpected")),
    )

    assert result.status == "missing_marker"
    assert result.safe_to_remove_marker is False
    assert result.safe_to_signal is False
