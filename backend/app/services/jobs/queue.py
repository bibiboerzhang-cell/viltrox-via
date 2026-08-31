"""
services/jobs/queue.py — Redis Streams job queue + Postgres job ledger

class-LOC 棘轮拆分(2026-08-30):RedisJobQueue 保薄门面,重活按职责搬到三个
兄弟协作模块(行为逐字不变):

- queue_claim:抢单/补抓(pop_job/_claim_stale/死信)——worker 公平性宪法条款;
- queue_heartbeat:就绪探针/事件发布/订阅心跳/运行时统计;
- queue_maintenance:台账读写/超时清扫/队列汇总。

monkeypatch 面:本模块级符号(get_conn/db_connection_scope/常量/工具函数)全部
保留原名,协作模块经惰性 import 回读本模块命名空间,所以对本模块打的补丁在
协作模块内同样生效;实例级补丁(get_status/set_status 等)经 ``queue.<attr>``
调用同样生效。
"""
from __future__ import annotations

import asyncio
import json
import uuid
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
from app.core.logging import get_logger
from app.db.connection import db_connection_scope, get_conn, is_postgres_runtime
from app.services.ai.orchestrator import TaskStatus, VideoJobInput
from app.services.jobs import queue_claim, queue_heartbeat, queue_maintenance
from app.services.jobs.queue_common import (
    BaseJobQueue,
    TERMINAL_JOB_STATUSES,
    decode_json as _decode_json,
    normalize_payload as _normalize_payload,
    parse_ts as _parse_ts,
    seconds_between as _seconds_between,
    utcnow as _utcnow,
)
from app.services.jobs.queue_inprocess import InProcessJobQueue

try:
    from redis.asyncio import from_url as redis_from_url
except Exception:
    redis_from_url = None

logger = get_logger(__name__)


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

    async def worker_readiness(self, consumer_names: list[str]) -> Dict[str, Any]:
        return await queue_heartbeat.worker_readiness(self, consumer_names)

    def _task_channel(self, task_id: str) -> str:
        return f"{REDIS_JOB_EVENT_PREFIX}:task:{task_id}"

    def _user_channel(self, user_id: int | str) -> str:
        return f"{REDIS_JOB_EVENT_PREFIX}:user:{user_id}"

    def _status_row_to_dict(self, row: Any) -> Dict[str, Any]:
        return queue_maintenance.status_row_to_dict(row)

    def _find_active_lock_job(self, lock_key: str) -> Optional[str]:
        return queue_maintenance.find_active_lock_job(lock_key)

    def _insert_job_ledger(self, job: Dict[str, Any]) -> None:
        queue_maintenance.insert_job_ledger(job)

    def _rollback_job_ledger_insert(self) -> None:
        queue_maintenance.rollback_job_ledger_insert()

    def _update_job_ledger(self, task_id: str, status: str, **extra: Any) -> Optional[Dict[str, Any]]:
        return queue_maintenance.update_job_ledger(self, task_id, status, **extra)

    def _get_job_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        return queue_maintenance.get_job_status(self, task_id)

    def _bind_job_stream(self, task_id: str, stream_id: str) -> Optional[Dict[str, Any]]:
        return queue_maintenance.bind_job_stream(self, task_id, stream_id)

    def _authorize_provider_dispatch(self, task_id: str, stream_id: str) -> Dict[str, Any]:
        return queue_maintenance.authorize_provider_dispatch(self, task_id, stream_id)

    async def authorize_provider_dispatch(self, task_id: str, stream_id: str) -> Dict[str, Any]:
        async with db_connection_scope():
            return await asyncio.to_thread(
                self._authorize_provider_dispatch,
                task_id,
                stream_id,
            )

    def _fail_unbound_stream_job(
        self,
        task_id: str,
        *,
        expected_stream_id: str,
        error_message: str,
    ) -> Optional[Dict[str, Any]]:
        return queue_maintenance.fail_unbound_stream_job(
            self,
            task_id,
            expected_stream_id=expected_stream_id,
            error_message=error_message,
        )

    def _provider_execution_claim_is_live(self, task_id: str) -> bool:
        return queue_claim.provider_execution_claim_is_live(task_id)

    def _find_active_submission_job(self, job_type: str, submission_id: int) -> Optional[str]:
        return queue_maintenance.find_active_submission_job(job_type, submission_id)

    def _ledger_queue_summary(self) -> Dict[str, Any]:
        return queue_maintenance.ledger_queue_summary()

    async def _publish_event(self, task_id: str, payload: Dict[str, Any], user_id: int = 0) -> None:
        await queue_heartbeat.publish_event(self, task_id, payload, user_id=user_id)

    def _mark_timed_out_jobs(self, limit: int = 100) -> int:
        return queue_maintenance.mark_timed_out_jobs(self, limit=limit)

    async def _find_enqueue_duplicate(
        self,
        job_type: str,
        submission_id: int,
        lock_key: str,
    ) -> Optional[str]:
        if job_type == "audit_submission" and submission_id:
            async with db_connection_scope():
                existing_task_id = await asyncio.to_thread(
                    self._find_active_submission_job,
                    job_type,
                    submission_id,
                )
            if existing_task_id:
                return existing_task_id
        if not lock_key:
            return None
        async with db_connection_scope():
            return await asyncio.to_thread(self._find_active_lock_job, lock_key)

    async def _persist_enqueue_ledger(
        self,
        job: Dict[str, Any],
        lock_key: str,
    ) -> Optional[str]:
        async with db_connection_scope():
            try:
                await asyncio.to_thread(self._insert_job_ledger, job)
            except Exception:
                if not lock_key:
                    raise
                await asyncio.to_thread(self._rollback_job_ledger_insert)
                existing_task_id = await asyncio.to_thread(
                    self._find_active_lock_job,
                    lock_key,
                )
                if existing_task_id:
                    return existing_task_id
                raise
        return None

    @staticmethod
    def _enqueue_error_message(prefix: str, exc: BaseException) -> str:
        detail = str(exc).strip().replace("\n", " ")[:400]
        if detail:
            return f"{prefix}: {type(exc).__name__}: {detail}"
        return f"{prefix}: {type(exc).__name__}"

    async def _terminalize_xadd_failure(
        self,
        task_id: str,
        stream_exc: BaseException,
    ) -> None:
        error_message = self._enqueue_error_message("redis xadd failed", stream_exc)
        try:
            async with db_connection_scope():
                snapshot = await asyncio.to_thread(
                    self._update_job_ledger,
                    task_id,
                    TaskStatus.FAILED.value,
                    error_message=error_message,
                    stage="enqueue_failed",
                )
        except Exception:
            logger.exception(
                "redis xadd failed and ledger terminalization also failed | task_id=%s",
                task_id,
            )
            raise RuntimeError(
                "redis xadd failed and job ledger could not be terminalized"
            ) from stream_exc
        if not snapshot:
            logger.error(
                "redis xadd failed but ledger disappeared | task_id=%s",
                task_id,
            )
            raise RuntimeError(
                "redis xadd failed and job ledger did not reach failed"
            ) from stream_exc
        if str(snapshot.get("status") or "").lower() != TaskStatus.FAILED.value:
            logger.error(
                "redis xadd failed but ledger did not reach failed | task_id=%s status=%s",
                task_id,
                snapshot.get("status"),
            )
            raise RuntimeError(
                "redis xadd failed and job ledger did not reach failed"
            ) from stream_exc

    async def _append_enqueue_stream(
        self,
        job: Dict[str, Any],
        user_id: int,
    ) -> str:
        try:
            stream_id = await self._client.xadd(
                REDIS_JOB_STREAM_KEY,
                {
                    "task_id": job["task_id"],
                    "job_type": job["job_type"],
                    "submission_id": str(job["submission_id"]),
                    "payload_json": json.dumps(job["payload"], ensure_ascii=False),
                    "user_id": str(user_id),
                    "created_at": _utcnow(),
                },
            )
        except Exception as stream_exc:
            # Keep the inserted ledger as audit evidence, but terminalize it so
            # a failed Redis append cannot retain the active dedupe lock.
            await self._terminalize_xadd_failure(job["task_id"], stream_exc)
            raise
        return str(stream_id)

    @staticmethod
    def _stream_binding_is_invalid(
        snapshot: Optional[Dict[str, Any]],
        stream_id: str,
    ) -> bool:
        if not snapshot:
            return True
        if snapshot.get("_stream_bind_conflict"):
            return True
        return str(snapshot.get("stream_id") or "") != stream_id

    async def _try_fail_unbound_stream(
        self,
        task_id: str,
        stream_id: str,
        error_message: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with db_connection_scope():
                return await asyncio.to_thread(
                    self._fail_unbound_stream_job,
                    task_id,
                    expected_stream_id=stream_id,
                    error_message=error_message,
                )
        except Exception:
            logger.exception(
                "stream binding failed and ledger terminalization also failed | task_id=%s",
                task_id,
            )
            return None

    @staticmethod
    def _stream_containment_state(
        snapshot: Optional[Dict[str, Any]],
    ) -> tuple[str, bool, bool]:
        if not snapshot:
            return "", False, False
        failed_status = str(snapshot.get("status") or "").lower()
        containment_applied = bool(snapshot.get("_stream_bind_failed_applied"))
        ledger_failed = (
            containment_applied
            and failed_status == TaskStatus.FAILED.value
            and str(snapshot.get("stage") or "") == "stream_bind_failed"
        )
        durable_stream_won = (
            not containment_applied and bool(snapshot.get("_durable_stream_won"))
        )
        return failed_status, ledger_failed, durable_stream_won

    async def _delete_contained_stream(self, task_id: str, stream_id: str) -> None:
        try:
            await self._client.xdel(REDIS_JOB_STREAM_KEY, stream_id)
        except Exception:
            logger.exception(
                "stream binding failed and redis message deletion also failed | task_id=%s stream_id=%s",
                task_id,
                stream_id,
            )

    async def _recover_enqueue_stream_binding(
        self,
        task_id: str,
        stream_id: str,
        bind_exc: BaseException,
    ) -> Dict[str, Any]:
        error_message = self._enqueue_error_message(
            "redis stream ledger binding failed",
            bind_exc,
        )
        failed_snapshot = await self._try_fail_unbound_stream(
            task_id,
            stream_id,
            error_message,
        )
        failed_status, ledger_failed, durable_stream_won = (
            self._stream_containment_state(failed_snapshot)
        )
        if ledger_failed:
            await self._delete_contained_stream(task_id, stream_id)
        if durable_stream_won:
            logger.warning(
                "producer stream bind lost to durable ledger state | task_id=%s stream_id=%s status=%s",
                task_id,
                stream_id,
                failed_status,
            )
            return failed_snapshot or {}
        if ledger_failed:
            raise RuntimeError(
                "redis stream ledger binding failed after xadd"
            ) from bind_exc
        raise RuntimeError(
            "redis stream ledger binding failed and containment is unverified"
        ) from bind_exc

    async def _bind_enqueue_stream(
        self,
        task_id: str,
        stream_id: str,
    ) -> tuple[Dict[str, Any], bool]:
        try:
            async with db_connection_scope():
                snapshot = await asyncio.to_thread(
                    self._bind_job_stream,
                    task_id,
                    stream_id,
                )
            if self._stream_binding_is_invalid(snapshot, stream_id):
                raise RuntimeError("job ledger stream binding was not durable")
            return snapshot or {}, False
        except Exception as bind_exc:
            recovered = await self._recover_enqueue_stream_binding(
                task_id,
                stream_id,
                bind_exc,
            )
            return recovered, True

    async def _publish_queued_enqueue_event(
        self,
        task_id: str,
        stream_id: str,
        snapshot: Dict[str, Any],
        user_id: int,
    ) -> None:
        if str(snapshot.get("status") or "").lower() != TaskStatus.QUEUED.value:
            return
        event = {
            "event_type": TaskStatus.QUEUED.value,
            "task_id": task_id,
            "status": TaskStatus.QUEUED.value,
            "created_at": _utcnow(),
            "submission_id": snapshot.get("submission_id") or "",
            "retry_count": snapshot.get("retry_count") or "0",
            "stage": snapshot.get("stage") or "ingest",
        }
        try:
            await self._publish_event(task_id, event, user_id=user_id)
        except Exception:
            logger.warning(
                "queued event publish failed after durable enqueue | task_id=%s stream_id=%s",
                task_id,
                stream_id,
                exc_info=True,
            )

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
        await self._ensure_ready()
        payload_dict = _normalize_payload(payload)
        user_id = int(payload_dict.get("user_id") or 0)
        effective_submission_id = int(
            submission_id or payload_dict.get("submission_id") or 0
        )
        normalized_lock_key = str(lock_key or "").strip()
        existing_task_id = await self._find_enqueue_duplicate(
            job_type,
            effective_submission_id,
            normalized_lock_key,
        )
        if existing_task_id:
            return existing_task_id

        task_id = str(uuid.uuid4())
        job = {
            "task_id": task_id,
            "job_type": job_type,
            "submission_id": effective_submission_id,
            "payload": payload_dict,
            "priority": priority,
            "lock_key": normalized_lock_key,
            "timeout_seconds": timeout_seconds,
        }
        insert_winner = await self._persist_enqueue_ledger(
            job,
            normalized_lock_key,
        )
        if insert_winner:
            return insert_winner

        stream_id = await self._append_enqueue_stream(job, user_id)
        snapshot, recovered = await self._bind_enqueue_stream(task_id, stream_id)
        if recovered:
            return task_id
        await self._publish_queued_enqueue_event(
            task_id,
            stream_id,
            snapshot,
            user_id,
        )
        return task_id

    async def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with db_connection_scope():
            return await asyncio.to_thread(self._get_job_status, task_id)

    async def set_status(self, task_id: str, status: str, **extra: Any) -> None:
        await queue_heartbeat.set_status(self, task_id, status, **extra)

    async def _claim_stale(self, consumer_name: str, count: int = 5) -> list[Dict[str, Any]]:
        return await queue_claim.claim_stale(self, consumer_name, count=count)

    async def pop_job(self, consumer_name: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
        return await queue_claim.pop_job(self, consumer_name, timeout=timeout)

    async def ack(self, raw_job: Dict[str, Any]) -> None:
        stream_id = raw_job.get("_stream_id")
        if stream_id:
            await self._client.xack(REDIS_JOB_STREAM_KEY, self._group, stream_id)

    async def move_to_dead_letter(self, raw_job: Dict[str, Any], reason: str) -> None:
        await queue_claim.move_to_dead_letter(self, raw_job, reason)

    async def subscribe_task_events(self, task_id: str):
        async for event in queue_heartbeat.subscribe_task_events(self, task_id):
            yield event

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
        return await queue_heartbeat.runtime_stats(self)


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
