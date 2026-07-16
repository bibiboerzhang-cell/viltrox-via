"""In-process fallback job queue."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, Optional

from app.core.config import (
    WORKER_ASYNC_CONSUMERS,
    WORKER_CONFIGURED_CONCURRENCY,
    WORKER_SERVICE_PROCESSES,
)
from app.services.ai.orchestrator import TaskStatus, VideoJobInput
from app.services.jobs.queue_common import (
    BaseJobQueue,
    DURABLE_PROVIDER_JOB_TYPES,
    TRANSIENT_JOB_STATUSES,
    normalize_payload,
    utcnow,
)


class InProcessJobQueue(BaseJobQueue):
    backend_name = "inprocess"

    def __init__(self, orchestrator: Any) -> None:
        self._orch = orchestrator
        self._generic_tasks: Dict[str, asyncio.Task] = {}
        self._generic_status: Dict[str, Dict[str, Any]] = {}
        self._queues: Dict[str, list[asyncio.Queue]] = {}

    async def _publish(self, task_id: str, event: Dict[str, Any]) -> None:
        for queue in list(self._queues.get(task_id, [])):
            await queue.put(event)

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
        if job_type == "audit_submission":
            if isinstance(payload, dict):
                payload = VideoJobInput(**payload)
            task_id = await self._orch.enqueue(payload)
            return task_id

        if str(job_type or "").strip() in DURABLE_PROVIDER_JOB_TYPES:
            raise RuntimeError(
                f"durable_queue_required:{job_type}:inprocess_queue_has_no_provider_execution_fence"
            )

        task_id = str(uuid.uuid4())
        normalized_payload = normalize_payload(payload)
        normalized_lock_key = str(lock_key or "").strip()
        if normalized_lock_key:
            for existing_task_id, item in self._generic_status.items():
                if item.get("lock_key") == normalized_lock_key and str(item.get("status") or "") in TRANSIENT_JOB_STATUSES:
                    return existing_task_id
        now = utcnow()
        raw_job = {
            "task_id": task_id,
            "job_type": job_type,
            "submission_id": submission_id or normalized_payload.get("submission_id") or 0,
            "payload": normalized_payload,
        }
        self._generic_status[task_id] = {
            "task_id": task_id,
            "status": TaskStatus.QUEUED.value,
            "submission_id": str(raw_job["submission_id"]),
            "job_type": job_type,
            "retry_count": "0",
            "error_message": "",
            "created_at": now,
            "updated_at": now,
            "payload_json": json.dumps(normalized_payload, ensure_ascii=False),
            "priority": str(priority if priority is not None else 5),
            "lock_key": normalized_lock_key,
            "timeout_seconds": str(timeout_seconds if timeout_seconds is not None else 300),
        }
        await self._publish(task_id, {"event_type": "queued", "task_id": task_id, "status": TaskStatus.QUEUED.value, "created_at": now})
        from app.services.jobs.processor import process_background_job

        self._generic_tasks[task_id] = asyncio.create_task(process_background_job(self, raw_job))
        return task_id

    async def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        if task_id in self._generic_status:
            return dict(self._generic_status[task_id])
        try:
            task = await self._orch.store.get(task_id)
        except KeyError:
            return None
        return {
            "task_id": task_id,
            "status": task.status.value,
            "submission_id": str(task.job.submission_id),
            "retry_count": str(task.retry_count),
            "error_message": " | ".join(task.errors) if task.errors else "",
            "updated_at": str(int(task.updated_at)),
        }

    async def set_status(self, task_id: str, status: str, **extra: Any) -> None:
        current = self._generic_status.setdefault(task_id, {"task_id": task_id, "created_at": utcnow()})
        current["status"] = status
        current["updated_at"] = utcnow()
        for key, value in extra.items():
            current[key] = "" if value is None else str(value)
        event_type = "result_ready" if status in {TaskStatus.DONE.value, TaskStatus.PARTIAL.value} else status
        await self._publish(
            task_id,
            {
                "event_type": event_type,
                "task_id": task_id,
                "status": status,
                "created_at": utcnow(),
                **{key: value for key, value in extra.items() if value is not None},
            },
        )

    async def pop_job(self, consumer_name: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        return None

    async def ack(self, raw_job: Dict[str, Any]) -> None:
        return None

    async def move_to_dead_letter(self, raw_job: Dict[str, Any], reason: str) -> None:
        await self.set_status(str(raw_job.get("task_id") or ""), "failed", error_message=reason)

    async def subscribe_task_events(self, task_id: str):
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.setdefault(task_id, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues.get(task_id, []).remove(queue)

    async def close(self) -> None:
        for task in self._generic_tasks.values():
            task.cancel()
        self._generic_tasks.clear()
        self._generic_status.clear()

    async def runtime_stats(self) -> Dict[str, Any]:
        active_tasks = sum(1 for task in self._generic_tasks.values() if not task.done())
        waiting = sum(1 for item in self._generic_status.values() if item.get("status") in {TaskStatus.QUEUED.value, TaskStatus.RETRYING.value})
        failed = sum(1 for item in self._generic_status.values() if item.get("status") == TaskStatus.FAILED.value)
        return {
            "backend": self.backend_name,
            "active_tasks": active_tasks,
            "waiting_jobs": waiting,
            "failed_jobs": failed,
            "dead_letter_size": 0,
            "summary": {
                "configured_concurrency": WORKER_CONFIGURED_CONCURRENCY,
                "worker_processes": WORKER_SERVICE_PROCESSES,
                "worker_async_consumers": WORKER_ASYNC_CONSUMERS,
            },
        }
