"""
services/jobs/results.py — 后台任务结果落盘
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_JOB_RESULTS_DIR = BASE_DIR / "data" / "job_results"


def job_results_dir() -> Path:
    """Return the configured release-stable result store.

    Local development keeps the historical backend-local default. Production
    workers bind ``VKPI_JOB_RESULTS_DIR`` to a shared path outside the immutable
    release so a result remains readable after the ``current`` symlink moves.
    Relative overrides are rejected because their meaning would change with a
    worker's WorkingDirectory/release.
    """

    configured = os.environ.get("VKPI_JOB_RESULTS_DIR", "").strip()
    if not configured:
        return DEFAULT_JOB_RESULTS_DIR
    path = Path(configured)
    if not path.is_absolute():
        raise RuntimeError("VKPI_JOB_RESULTS_DIR must be an absolute path")
    return path


def persist_job_result(task_id: str, payload: Any) -> str:
    directory = job_results_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def load_job_result(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
