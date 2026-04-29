"""
services/jobs/backpressure.py — queue admission control.

The public submit path should stay fast under bursts. When the async worker
queue is already too deep, reject new link/video analysis requests with a
retry window instead of letting the web process or database fall over.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app.core.config import (
    IS_PRODUCTION,
    JOB_QUEUE_BACKPRESSURE_HARD_LIMIT,
    JOB_QUEUE_BACKPRESSURE_RETRY_AFTER_SEC,
    JOB_QUEUE_BACKPRESSURE_SOFT_LIMIT,
)


def _to_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


async def enforce_queue_backpressure(queue: Any, *, job_type: str) -> Dict[str, Any]:
    if queue is None:
        if IS_PRODUCTION:
            raise HTTPException(status_code=503, detail="job queue unavailable")
        return {"pressure": "local", "waiting": 0, "processing": 0}

    stats = await queue.runtime_stats()
    summary = stats.get("summary") if isinstance(stats, dict) else {}
    if not isinstance(summary, dict):
        summary = {}

    waiting = _to_int(summary.get("waiting"))
    processing = _to_int(summary.get("processing"))
    configured_concurrency = max(1, _to_int(summary.get("configured_concurrency"), 1))
    eta_wait_seconds = summary.get("eta_wait_seconds")
    hard_limit = max(1, JOB_QUEUE_BACKPRESSURE_HARD_LIMIT)
    soft_limit = max(1, min(JOB_QUEUE_BACKPRESSURE_SOFT_LIMIT, hard_limit))

    if waiting >= hard_limit:
        retry_after = max(
            JOB_QUEUE_BACKPRESSURE_RETRY_AFTER_SEC,
            _to_int(eta_wait_seconds, JOB_QUEUE_BACKPRESSURE_RETRY_AFTER_SEC),
        )
        raise HTTPException(
            status_code=429,
            detail={
                "status": "busy",
                "code": "queue_backpressure",
                "message": "Via is busy right now. Please try again shortly.",
                "job_type": job_type,
                "waiting": waiting,
                "processing": processing,
                "configured_concurrency": configured_concurrency,
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    pressure = "soft" if waiting >= soft_limit else "ok"
    return {
        "pressure": pressure,
        "waiting": waiting,
        "processing": processing,
        "configured_concurrency": configured_concurrency,
        "eta_wait_seconds": eta_wait_seconds,
        "soft_limit": soft_limit,
        "hard_limit": hard_limit,
    }
