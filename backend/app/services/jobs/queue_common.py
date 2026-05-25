"""Common job queue types and helpers."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.ai.orchestrator import TaskStatus


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(raw.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def seconds_between(start: Any, end: Any) -> Optional[float]:
    start_dt = parse_ts(start)
    end_dt = parse_ts(end)
    if not start_dt or not end_dt:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def normalize_payload(payload: Any) -> Dict[str, Any]:
    if is_dataclass(payload):
        return asdict(payload)
    if isinstance(payload, dict):
        return payload
    raise TypeError(f"Unsupported job payload type: {type(payload)!r}")


def decode_json(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


TERMINAL_JOB_STATUSES = {
    TaskStatus.DONE.value,
    TaskStatus.PARTIAL.value,
    TaskStatus.FAILED.value,
    "prefilter_rejected",
    "cancelled",
    "timeout",
}

TRANSIENT_JOB_STATUSES = {
    TaskStatus.QUEUED.value,
    TaskStatus.RETRYING.value,
    TaskStatus.PROCESSING.value,
    "running",
}


class BaseJobQueue:
    backend_name = "unknown"

    async def enqueue(
        self,
        job_type: str,
        payload: Any,
        submission_id: Optional[int] = None,
        *,
        priority: int | None = None,
        lock_key: str | None = None,
        timeout_seconds: int | None = None,
    ) -> str:
        raise NotImplementedError

    async def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def set_status(self, task_id: str, status: str, **extra: Any) -> None:
        raise NotImplementedError

    async def pop_job(self, consumer_name: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def ack(self, raw_job: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def move_to_dead_letter(self, raw_job: Dict[str, Any], reason: str) -> None:
        raise NotImplementedError

    async def subscribe_task_events(self, task_id: str):
        raise NotImplementedError

    async def close(self) -> None:
        return None

    async def runtime_stats(self) -> Dict[str, Any]:
        return {"backend": self.backend_name}
