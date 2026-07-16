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


def test_result_persistence_rejects_release_relative_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_JOB_RESULTS_DIR", "backend/data/job_results")

    with pytest.raises(RuntimeError, match="must be an absolute path"):
        results.job_results_dir()
