"""
services/jobs/results.py — 后台任务结果落盘
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[3]
JOB_RESULTS_DIR = BASE_DIR / "data" / "job_results"


def persist_job_result(task_id: str, payload: Any) -> str:
    JOB_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = JOB_RESULTS_DIR / f"{task_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def load_job_result(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))

