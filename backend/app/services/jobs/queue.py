"""
services/jobs/queue.py — Redis Streams job queue + Postgres job ledger

class-LOC 棘轮拆分(2026-08-30):RedisJobQueue 保薄门面,重活按职责搬到三个
兄弟协作模块(行为逐字不变):

- queue_claim:抢单/补抓(pop_job/_claim_stale/死信)——worker 公平性宪法条款;
- queue_enqueue:入队去重/XADD/台账绑定/失败隔离;
- queue_heartbeat:就绪探针/事件发布/订阅心跳/运行时统计;
- queue_maintenance:台账读写/超时清扫/队列汇总。

monkeypatch 面:本模块级符号(get_conn/db_connection_scope/常量/工具函数)全部
保留原名,协作模块经无反向 import 的运行时绑定回读本模块命名空间,所以对本
模块打的补丁在协作模块内同样生效;实例级补丁(get_status/set_status 等)经
``queue.<attr>`` 调用同样生效。
"""
from __future__ import annotations

import asyncio
import sys
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
from app.services.jobs import (
    queue_claim,
    queue_enqueue,
    queue_heartbeat,
    queue_maintenance,
    queue_runtime,
)
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
queue_runtime.bind_queue_facade(sys.modules[__name__])


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
        return await queue_enqueue.enqueue(
            self,
            job_type,
            payload,
            submission_id,
            priority=priority,
            lock_key=lock_key,
            timeout_seconds=timeout_seconds,
        )

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
