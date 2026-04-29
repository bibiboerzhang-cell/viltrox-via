"""
services/jobs/queue.py — Redis Streams job queue + Postgres job ledger
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.config import (
    IS_PRODUCTION,
    REDIS_JOB_BLOCK_MS,
    REDIS_JOB_CLAIM_IDLE_MS,
    REDIS_JOB_DEAD_STREAM_KEY,
    REDIS_JOB_EVENT_PREFIX,
    REDIS_JOB_GROUP,
    REDIS_JOB_STREAM_KEY,
    REDIS_URL,
    WORKER_CONFIGURED_CONCURRENCY,
    WORKER_ASYNC_CONSUMERS,
    WORKER_SERVICE_PROCESSES,
)
from app.db.connection import db_connection_scope, get_conn
from app.services.ai.orchestrator import TaskStatus, VideoJobInput

try:
    from redis.asyncio import from_url as redis_from_url
except Exception:
    redis_from_url = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Any) -> Optional[datetime]:
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


def _seconds_between(start: Any, end: Any) -> Optional[float]:
    start_dt = _parse_ts(start)
    end_dt = _parse_ts(end)
    if not start_dt or not end_dt:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def _normalize_payload(payload: Any) -> Dict[str, Any]:
    if is_dataclass(payload):
        return asdict(payload)
    if isinstance(payload, dict):
        return payload
    raise TypeError(f"Unsupported job payload type: {type(payload)!r}")


def _decode_json(raw: Any, default: Any) -> Any:
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
}

TRANSIENT_JOB_STATUSES = {
    TaskStatus.QUEUED.value,
    TaskStatus.RETRYING.value,
    TaskStatus.PROCESSING.value,
    "running",
}


class BaseJobQueue:
    backend_name = "unknown"

    async def enqueue(self, job_type: str, payload: Any, submission_id: Optional[int] = None) -> str:
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

    async def enqueue(self, job_type: str, payload: Any, submission_id: Optional[int] = None) -> str:
        if job_type == "audit_submission":
            if isinstance(payload, dict):
                payload = VideoJobInput(**payload)
            task_id = await self._orch.enqueue(payload)
            return task_id

        task_id = str(uuid.uuid4())
        normalized_payload = _normalize_payload(payload)
        now = _utcnow()
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
        }
        await self._publish(task_id, {"event_type": "queued", "task_id": task_id, "status": TaskStatus.QUEUED.value, "created_at": now})
        # Keep the heavy worker/AI stack out of queue module import time. This
        # matters for admin/runtime tests and lightweight health checks.
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
        current = self._generic_status.setdefault(task_id, {"task_id": task_id, "created_at": _utcnow()})
        current["status"] = status
        current["updated_at"] = _utcnow()
        for key, value in extra.items():
            current[key] = "" if value is None else str(value)
        event_type = "result_ready" if status in {TaskStatus.DONE.value, TaskStatus.PARTIAL.value} else status
        await self._publish(task_id, {"event_type": event_type, "task_id": task_id, "status": status, "created_at": _utcnow(), **{k: v for k, v in extra.items() if v is not None}})

    async def pop_job(self, consumer_name: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        return None

    async def ack(self, raw_job: Dict[str, Any]) -> None:
        return None

    async def move_to_dead_letter(self, raw_job: Dict[str, Any], reason: str) -> None:
        await self.set_status(raw_job.get("task_id", ""), "failed", error_message=reason)

    async def subscribe_task_events(self, task_id: str):
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.setdefault(task_id, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers = self._queues.get(task_id, [])
            if queue in subscribers:
                subscribers.remove(queue)

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
            "generic_tasks": len(self._generic_tasks),
            "active_tasks": active_tasks,
            "tracked_statuses": len(self._generic_status),
            "summary": {
                "waiting": waiting,
                "processing": active_tasks,
                "failed": failed,
                "avg_duration_seconds": None,
                "eta_wait_seconds": None,
                "configured_concurrency": active_tasks or 1,
                "worker_processes": 1,
                "worker_async_consumers": active_tasks or 1,
            },
        }


class RedisJobQueue(BaseJobQueue):
    backend_name = "redis-stream"

    def __init__(self, redis_url: str = REDIS_URL) -> None:
        if not redis_url:
            raise ValueError("REDIS_URL is required for RedisJobQueue")
        if redis_from_url is None:
            raise RuntimeError("redis package is not installed")
        self._client = redis_from_url(redis_url, decode_responses=True)
        self._ready = False
        self._group = REDIS_JOB_GROUP

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        try:
            await self._client.xgroup_create(
                REDIS_JOB_STREAM_KEY,
                self._group,
                id="0-0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._ready = True

    def _task_channel(self, task_id: str) -> str:
        return f"{REDIS_JOB_EVENT_PREFIX}:task:{task_id}"

    def _user_channel(self, user_id: int | str) -> str:
        return f"{REDIS_JOB_EVENT_PREFIX}:user:{user_id}"

    def _status_row_to_dict(self, row: Any) -> Dict[str, Any]:
        if row is None:
            return {}
        data = dict(row)
        extra = _decode_json(data.get("extra_json"), {})
        payload = _decode_json(data.get("payload_json"), {})
        if payload:
            data["payload_json"] = json.dumps(payload, ensure_ascii=False)
        for key, value in extra.items():
            data.setdefault(key, value)
        return {key: ("" if value is None else str(value) if isinstance(value, (int, float)) and key.endswith("_id") else value) for key, value in data.items()}

    def _insert_job_ledger(self, job: Dict[str, Any]) -> None:
        conn = get_conn()
        now = _utcnow()
        payload_json = json.dumps(job["payload"], ensure_ascii=False)
        user_id = int(job["payload"].get("user_id") or 0)
        conn.execute(
            """
            INSERT INTO job_execution_ledger (
                task_id, job_type, submission_id, user_id, status, payload_json, retry_count,
                created_at, updated_at, stage, extra_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["task_id"],
                job["job_type"],
                int(job.get("submission_id") or 0),
                user_id,
                TaskStatus.QUEUED.value,
                payload_json,
                0,
                now,
                now,
                "ingest",
                json.dumps({}, ensure_ascii=False),
            ),
        )
        conn.commit()

    def _update_job_ledger(self, task_id: str, status: str, **extra: Any) -> Optional[Dict[str, Any]]:
        conn = get_conn()
        row = conn.execute(
            """
            SELECT task_id, user_id, status, extra_json, retry_count, result_json, stats_json
            FROM job_execution_ledger
            WHERE task_id=?
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        current_status = str(row["status"] or "").lower()
        incoming_status = str(status or "").lower()
        if current_status in TERMINAL_JOB_STATUSES and incoming_status in TRANSIENT_JOB_STATUSES:
            metadata_updates = []
            metadata_params = []
            if extra.get("stream_id"):
                metadata_updates.append("stream_id=COALESCE(stream_id, ?)")
                metadata_params.append(extra.get("stream_id"))
            if extra.get("consumer_name"):
                metadata_updates.append("consumer_name=COALESCE(consumer_name, ?)")
                metadata_params.append(extra.get("consumer_name"))
            if metadata_updates:
                conn.execute(
                    f"UPDATE job_execution_ledger SET {', '.join(metadata_updates)} WHERE task_id=?",
                    (*metadata_params, task_id),
                )
                conn.commit()
            updated = conn.execute("SELECT * FROM job_execution_ledger WHERE task_id=?", (task_id,)).fetchone()
            snapshot = self._status_row_to_dict(updated)
            snapshot["_stale_status_ignored"] = True
            return snapshot
        extra_json = _decode_json(row["extra_json"], {})
        for key, value in extra.items():
            if key in {"result_json", "stats_json", "summary", "error_message", "result_path", "detection_status", "stage", "stream_id", "consumer_name"}:
                continue
            extra_json[key] = value
        retry_count = int(extra.get("retry_count", row["retry_count"] or 0) or 0)
        now = _utcnow()
        conn.execute(
            """
            UPDATE job_execution_ledger
            SET status=?,
                updated_at=?,
                started_at=CASE WHEN ?='processing' AND started_at IS NULL THEN ? ELSE started_at END,
                finished_at=CASE WHEN ? IN ('done','partial_done','failed') THEN ? ELSE finished_at END,
                retry_count=?,
                error_message=?,
                summary=?,
                detection_status=?,
                result_path=?,
                result_json=?,
                stats_json=?,
                stage=?,
                stream_id=COALESCE(?, stream_id),
                consumer_name=COALESCE(?, consumer_name),
                extra_json=?
            WHERE task_id=?
            """,
            (
                status,
                now,
                status,
                now,
                status,
                now,
                retry_count,
                str(extra.get("error_message") or ""),
                str(extra.get("summary") or ""),
                str(extra.get("detection_status") or ""),
                str(extra.get("result_path") or ""),
                json.dumps(extra.get("result_json") or _decode_json(row["result_json"], {}), ensure_ascii=False),
                json.dumps(extra.get("stats_json") or _decode_json(row["stats_json"], {}), ensure_ascii=False),
                str(extra.get("stage") or extra_json.get("stage") or ""),
                extra.get("stream_id"),
                extra.get("consumer_name"),
                json.dumps(extra_json, ensure_ascii=False),
                task_id,
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM job_execution_ledger WHERE task_id=?", (task_id,)).fetchone()
        return self._status_row_to_dict(updated)

    def _get_job_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        conn = get_conn()
        row = conn.execute("SELECT * FROM job_execution_ledger WHERE task_id=?", (task_id,)).fetchone()
        return self._status_row_to_dict(row) if row else None

    def _find_active_submission_job(self, job_type: str, submission_id: int) -> Optional[str]:
        if not submission_id:
            return None
        conn = get_conn()
        row = conn.execute(
            """
            SELECT task_id
            FROM job_execution_ledger
            WHERE job_type=?
              AND submission_id=?
              AND status IN ('queued', 'retrying', 'processing', 'running')
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_type, submission_id),
        ).fetchone()
        return str(row["task_id"]) if row else None

    def _ledger_queue_summary(self) -> Dict[str, Any]:
        conn = get_conn()
        waiting_statuses = {TaskStatus.QUEUED.value, TaskStatus.RETRYING.value}
        processing_statuses = {TaskStatus.PROCESSING.value, "running"}
        failed_statuses = {TaskStatus.FAILED.value}
        completed_statuses = {TaskStatus.DONE.value, TaskStatus.PARTIAL.value}
        counts = {"waiting": 0, "processing": 0, "failed": 0, "completed": 0}
        by_type: Dict[str, Dict[str, int]] = {}
        oldest_waiting_age: Optional[int] = None
        now = datetime.now(timezone.utc)

        try:
            aggregate_rows = conn.execute(
                """
                SELECT job_type, status, COUNT(*) AS n, MIN(created_at) AS oldest
                FROM job_execution_ledger
                WHERE status IN ('queued', 'retrying', 'processing', 'running', 'failed')
                GROUP BY job_type, status
                """
            ).fetchall()
            rows = conn.execute(
                """
                SELECT task_id, job_type, status, created_at, updated_at, started_at, finished_at
                FROM job_execution_ledger
                ORDER BY id DESC
                LIMIT ?
                """,
                (1000,),
            ).fetchall()
        except Exception:
            return {
                "waiting": 0,
                "processing": 0,
                "failed": 0,
                "avg_duration_seconds": None,
                "eta_wait_seconds": None,
                "configured_concurrency": WORKER_CONFIGURED_CONCURRENCY,
                "worker_processes": WORKER_SERVICE_PROCESSES,
                "worker_async_consumers": WORKER_ASYNC_CONSUMERS,
                "note": "job_execution_ledger unavailable",
            }

        for row in aggregate_rows:
            status = str(row["status"] or "").lower()
            job_type = str(row["job_type"] or "unknown")
            n = int(row["n"] or 0)
            bucket = by_type.setdefault(job_type, {"waiting": 0, "processing": 0, "failed": 0, "completed": 0})
            if status in waiting_statuses:
                counts["waiting"] += n
                bucket["waiting"] += n
                created_at = _parse_ts(row["oldest"])
                if created_at:
                    age = int(max(0, (now - created_at).total_seconds()))
                    oldest_waiting_age = age if oldest_waiting_age is None else max(oldest_waiting_age, age)
            elif status in processing_statuses:
                counts["processing"] += n
                bucket["processing"] += n
            elif status in failed_statuses:
                counts["failed"] += n
                bucket["failed"] += n

        durations: list[float] = []
        completed_last_hour = 0

        for row in rows:
            status = str(row["status"] or "").lower()
            job_type = str(row["job_type"] or "unknown")
            bucket = by_type.setdefault(job_type, {"waiting": 0, "processing": 0, "failed": 0, "completed": 0})
            if status in completed_statuses:
                counts["completed"] += 1
                bucket["completed"] += 1
                duration = _seconds_between(row["started_at"] or row["created_at"], row["finished_at"] or row["updated_at"])
                if duration is not None:
                    durations.append(duration)
                finished_at = _parse_ts(row["finished_at"] or row["updated_at"])
                if finished_at and (now - finished_at).total_seconds() <= 3600:
                    completed_last_hour += 1

        avg_duration = round(sum(durations) / len(durations), 1) if durations else None
        eta_wait_seconds = None
        if counts["waiting"] > 0:
            if completed_last_hour > 0:
                eta_wait_seconds = int(counts["waiting"] / max(completed_last_hour / 3600, 0.001))
            elif avg_duration:
                eta_wait_seconds = int(counts["waiting"] * avg_duration / max(WORKER_CONFIGURED_CONCURRENCY, 1))

        return {
            "waiting": counts["waiting"],
            "processing": counts["processing"],
            "failed": counts["failed"],
            "completed_recent_sample": counts["completed"],
            "completed_last_hour": completed_last_hour,
            "avg_duration_seconds": avg_duration,
            "eta_wait_seconds": eta_wait_seconds,
            "oldest_waiting_age_seconds": oldest_waiting_age,
            "configured_concurrency": WORKER_CONFIGURED_CONCURRENCY,
            "worker_processes": WORKER_SERVICE_PROCESSES,
            "worker_async_consumers": WORKER_ASYNC_CONSUMERS,
            "by_job_type": by_type,
            "sample_size": len(rows),
        }

    async def _publish_event(self, task_id: str, payload: Dict[str, Any], user_id: int = 0) -> None:
        await self._client.publish(self._task_channel(task_id), json.dumps(payload, ensure_ascii=False))
        if user_id:
            await self._client.publish(self._user_channel(user_id), json.dumps(payload, ensure_ascii=False))

    async def enqueue(self, job_type: str, payload: Any, submission_id: Optional[int] = None) -> str:
        await self._ensure_ready()
        payload_dict = _normalize_payload(payload)
        user_id = int(payload_dict.get("user_id") or 0)
        effective_submission_id = int(submission_id or payload_dict.get("submission_id") or 0)
        if job_type == "audit_submission" and effective_submission_id:
            async with db_connection_scope():
                existing_task_id = await asyncio.to_thread(
                    self._find_active_submission_job,
                    job_type,
                    effective_submission_id,
                )
            if existing_task_id:
                return existing_task_id

        task_id = str(uuid.uuid4())
        job = {
            "task_id": task_id,
            "job_type": job_type,
            "submission_id": effective_submission_id,
            "payload": payload_dict,
        }
        async with db_connection_scope():
            await asyncio.to_thread(self._insert_job_ledger, job)
        stream_id = await self._client.xadd(
            REDIS_JOB_STREAM_KEY,
            {
                "task_id": task_id,
                "job_type": job_type,
                "submission_id": str(job["submission_id"]),
                "payload_json": json.dumps(payload_dict, ensure_ascii=False),
                "user_id": str(user_id),
                "created_at": _utcnow(),
            },
        )
        await self.set_status(
            task_id,
            TaskStatus.QUEUED.value,
            submission_id=job["submission_id"],
            user_id=user_id,
            stream_id=str(stream_id),
            stage="ingest",
        )
        return task_id

    async def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with db_connection_scope():
            return await asyncio.to_thread(self._get_job_status, task_id)

    async def set_status(self, task_id: str, status: str, **extra: Any) -> None:
        async with db_connection_scope():
            snapshot = await asyncio.to_thread(self._update_job_ledger, task_id, status, **extra)
        if not snapshot:
            return
        if snapshot.get("_stale_status_ignored"):
            return
        user_id = int(snapshot.get("user_id") or extra.get("user_id") or 0)
        effective_status = str(snapshot.get("status") or status)
        event_type = extra.get("event_type") or (
            "result_ready"
            if effective_status in {TaskStatus.DONE.value, TaskStatus.PARTIAL.value}
            else effective_status
        )
        payload = {
            "event_type": event_type,
            "task_id": task_id,
            "status": effective_status,
            "created_at": _utcnow(),
            "submission_id": snapshot.get("submission_id") or "",
            "retry_count": snapshot.get("retry_count") or "0",
            "stage": snapshot.get("stage") or extra.get("stage") or "",
        }
        for key in ("summary", "error_message", "result_path", "detection_status"):
            value = snapshot.get(key) or extra.get(key)
            if value:
                payload[key] = value
        await self._publish_event(task_id, payload, user_id=user_id)

    async def _claim_stale(self, consumer_name: str, count: int = 5) -> list[Dict[str, Any]]:
        claimed: list[Dict[str, Any]] = []
        pending = await self._client.xpending_range(
            REDIS_JOB_STREAM_KEY,
            self._group,
            min="-",
            max="+",
            count=count,
            idle=REDIS_JOB_CLAIM_IDLE_MS,
        )
        message_ids = [entry["message_id"] for entry in pending if entry.get("time_since_delivered", 0) >= REDIS_JOB_CLAIM_IDLE_MS]
        if not message_ids:
            return claimed
        batches = await self._client.xclaim(
            REDIS_JOB_STREAM_KEY,
            self._group,
            consumer_name,
            min_idle_time=REDIS_JOB_CLAIM_IDLE_MS,
            message_ids=message_ids,
        )
        for stream_id, fields in batches:
            raw_job = {
                "_stream_id": stream_id,
                "task_id": fields.get("task_id", ""),
                "job_type": fields.get("job_type", ""),
                "submission_id": int(fields.get("submission_id") or 0),
                "payload": _decode_json(fields.get("payload_json"), {}),
            }
            claimed.append(raw_job)
            await self.set_status(
                raw_job["task_id"],
                TaskStatus.RETRYING.value,
                event_type="retrying",
                stream_id=str(stream_id),
                consumer_name=consumer_name,
                retry_count=int((await self.get_status(raw_job["task_id"]) or {}).get("retry_count") or 0) + 1,
            )
        return claimed

    async def pop_job(self, consumer_name: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        await self._ensure_ready()
        batches = await self._client.xreadgroup(
            self._group,
            consumer_name,
            {REDIS_JOB_STREAM_KEY: ">"},
            count=1,
            block=max(timeout * 1000, REDIS_JOB_BLOCK_MS),
        )
        if not batches:
            claimed = await self._claim_stale(consumer_name, count=1)
            return claimed[0] if claimed else None
        _, entries = batches[0]
        stream_id, fields = entries[0]
        raw_job = {
            "_stream_id": stream_id,
            "task_id": fields.get("task_id", ""),
            "job_type": fields.get("job_type", ""),
            "submission_id": int(fields.get("submission_id") or 0),
            "payload": _decode_json(fields.get("payload_json"), {}),
        }
        await self.set_status(
            raw_job["task_id"],
            TaskStatus.PROCESSING.value,
            event_type="processing",
            stage="processing",
            stream_id=str(stream_id),
            consumer_name=consumer_name,
        )
        return raw_job

    async def ack(self, raw_job: Dict[str, Any]) -> None:
        stream_id = raw_job.get("_stream_id")
        if stream_id:
            await self._client.xack(REDIS_JOB_STREAM_KEY, self._group, stream_id)

    async def move_to_dead_letter(self, raw_job: Dict[str, Any], reason: str) -> None:
        await self._client.xadd(
            REDIS_JOB_DEAD_STREAM_KEY,
            {
                "task_id": raw_job.get("task_id", ""),
                "job_type": raw_job.get("job_type", ""),
                "reason": reason,
                "payload_json": json.dumps(raw_job.get("payload") or {}, ensure_ascii=False),
                "moved_at": _utcnow(),
            },
        )
        await self.set_status(
            raw_job.get("task_id", ""),
            TaskStatus.FAILED.value,
            event_type="failed",
            error_message=reason,
            stage="dead_letter",
        )
        await self.ack(raw_job)

    async def subscribe_task_events(self, task_id: str):
        pubsub = self._client.pubsub()
        await pubsub.subscribe(self._task_channel(task_id))
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if message is None:
                    yield {
                        "event_type": "heartbeat",
                        "task_id": task_id,
                        "created_at": _utcnow(),
                    }
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="ignore")
                yield _decode_json(data, {"event_type": "message", "task_id": task_id})
        finally:
            await pubsub.unsubscribe(self._task_channel(task_id))
            close_fn = getattr(pubsub, "aclose", None)
            if close_fn is not None:
                await close_fn()

    async def close(self) -> None:
        close_fn = getattr(self._client, "aclose", None)
        if close_fn is not None:
            await close_fn()
            return
        close_fn = getattr(self._client, "close", None)
        if close_fn is not None:
            maybe = close_fn()
            if hasattr(maybe, "__await__"):
                await maybe

    async def runtime_stats(self) -> Dict[str, Any]:
        await self._ensure_ready()
        queue_depth = await self._client.xlen(REDIS_JOB_STREAM_KEY)
        async with db_connection_scope():
            summary = await asyncio.to_thread(self._ledger_queue_summary)
        try:
            groups = await self._client.xinfo_groups(REDIS_JOB_STREAM_KEY)
        except Exception:
            groups = []
        return {
            "backend": self.backend_name,
            "stream_key": REDIS_JOB_STREAM_KEY,
            "group": self._group,
            "queue_depth": int(queue_depth or 0),
            "summary": summary,
            "groups": groups,
            "dead_letter_stream": REDIS_JOB_DEAD_STREAM_KEY,
        }


def build_job_queue(orchestrator: Any = None) -> Optional[BaseJobQueue]:
    if REDIS_URL:
        if redis_from_url is None:
            raise RuntimeError("Redis job queue requires the redis Python package when REDIS_URL is configured")
        return RedisJobQueue(REDIS_URL)
    if IS_PRODUCTION:
        raise RuntimeError("2.0 production/staging requires REDIS_URL and RedisJobQueue; in-process queue is disabled")
    if orchestrator is not None and not IS_PRODUCTION:
        return InProcessJobQueue(orchestrator)
    return None


__all__ = [
    "BaseJobQueue",
    "InProcessJobQueue",
    "RedisJobQueue",
    "VideoJobInput",
    "build_job_queue",
]
