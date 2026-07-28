from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.jobs import results


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "scripts/ops/systemd/vkpi-redis-worker.service"


def _unit_value(prefix: str) -> str:
    values = [
        line.removeprefix(prefix)
        for line in UNIT.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix)
    ]
    assert len(values) == 1
    return values[0]


def test_redis_systemd_writable_path_matches_result_persistence_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _unit_value("Environment=VKPI_JOB_RESULTS_DIR=")
    writable = {
        line.split("=", 1)[1]
        for line in UNIT.read_text(encoding="utf-8").splitlines()
        if line.startswith("ReadWritePaths=")
    }

    monkeypatch.setenv("VKPI_JOB_RESULTS_DIR", configured)

    assert results.job_results_dir() == Path(configured)
    assert configured == "/opt/viltrox-2.0/runtime/job-results"
    assert configured in writable


def test_result_persistence_uses_absolute_configured_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "release-stable-results"
    monkeypatch.setenv("VKPI_JOB_RESULTS_DIR", str(directory))

    persisted = Path(results.persist_job_result("task-123", {"ok": True}))

    assert persisted == directory / "task-123.json"
    assert json.loads(persisted.read_text(encoding="utf-8")) == {"ok": True}
    assert results.load_job_result(str(persisted)) == {"ok": True}


def test_result_loading_supports_default_local_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "backend" / "data" / "job_results"
    monkeypatch.delenv("VKPI_JOB_RESULTS_DIR", raising=False)
    monkeypatch.setattr(results, "DEFAULT_JOB_RESULTS_DIR", directory)

    persisted = results.persist_job_result("task-default", {"items": [1, 2]})

    assert results.load_job_result(persisted) == {"items": [1, 2]}
    assert results.load_job_result("task-default.json") == {"items": [1, 2]}


def test_result_loading_rejects_paths_outside_trusted_root_without_path_leak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "job-results"
    directory.mkdir()
    outside = tmp_path / "arbitrary.json"
    outside.write_text('{"secret":true}', encoding="utf-8")
    monkeypatch.setenv("VKPI_JOB_RESULTS_DIR", str(directory))

    for candidate in (str(outside), "../arbitrary.json"):
        with pytest.raises(RuntimeError) as caught:
            results.load_job_result(candidate)
        assert str(caught.value) == "job result unavailable"
        assert str(outside) not in str(caught.value)


def test_result_loading_rejects_non_json_directory_and_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "job-results"
    directory.mkdir()
    non_json = directory / "result.txt"
    non_json.write_text('{"ok":true}', encoding="utf-8")
    json_directory = directory / "folder.json"
    json_directory.mkdir()
    real_json = directory / "real.json"
    real_json.write_text('{"ok":true}', encoding="utf-8")
    symlink = directory / "linked.json"
    symlink.symlink_to(real_json)
    monkeypatch.setenv("VKPI_JOB_RESULTS_DIR", str(directory))

    for candidate in (non_json, json_directory, symlink):
        with pytest.raises(RuntimeError) as caught:
            results.load_job_result(str(candidate))
        assert str(caught.value) == "job result unavailable"


def test_result_loading_rejects_symlinked_parent_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "job-results"
    directory.mkdir()
    actual = directory / "actual"
    actual.mkdir()
    (actual / "result.json").write_text('{"ok":true}', encoding="utf-8")
    linked_parent = directory / "linked"
    linked_parent.symlink_to(actual, target_is_directory=True)
    monkeypatch.setenv("VKPI_JOB_RESULTS_DIR", str(directory))

    with pytest.raises(RuntimeError) as caught:
        results.load_job_result(str(linked_parent / "result.json"))

    assert str(caught.value) == "job result unavailable"


def test_result_persistence_rejects_release_relative_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_JOB_RESULTS_DIR", "backend/data/job_results")

    with pytest.raises(RuntimeError, match="must be an absolute path"):
        results.job_results_dir()
