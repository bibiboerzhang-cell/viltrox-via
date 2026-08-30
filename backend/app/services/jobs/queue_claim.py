"""services/jobs/queue_claim.py — RedisJobQueue 抢单/补抓协作对象(claim path)。

class-LOC 棘轮拆分:行为逐字搬运自 queue.py::RedisJobQueue。这里是 worker 公平性
的宪法条款,语义一字不动:

- pop_job:XREADGROUP 新消息优先;空转才回落 stale 补抓;
- claim_stale:pending-idle 补抓受三重闸保护——终态只 ack、付费 provider 围栏
  (durable lease)拦住每一条非终态 stale 消息、processing/running 未超 timeout
  窗不许抢;
- move_to_dead_letter:死信流 + failed 终态 + ack。

所有可 monkeypatch 的模块级符号(get_conn/db_connection_scope/常量/工具函数)
一律经 ``_qm()`` 惰性解析到 queue 模块,保持 tests 对 queue 模块的补丁面不变。
实例侧一律走 ``queue.<attr>``,保持对实例打补丁(get_status/set_status/
_provider_execution_claim_is_live 等)的面不变。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _qm():
    """Resolve the queue module lazily so monkeypatches on it stay effective."""
    from app.services.jobs import queue as queue_module

    return queue_module


def provider_execution_claim_is_live(task_id: str) -> bool:
    """Fail closed while a paid-provider fence still owns this task.

    Redis pending-idle time is not an execution lease.  A second consumer
    must not XCLAIM and terminalize work merely because the first consumer
    has spent 60 seconds inside a legitimate provider call.
    """

    qm = _qm()
    clean_task = str(task_id or "").strip()
    if not clean_task:
        return False
    try:
        row = qm.get_conn().execute(
            """
            SELECT 1 AS live
            FROM vkpi_provider_execution_claims
            WHERE task_id=? AND state='active' AND lease_expires_at>NOW()
            LIMIT 1
            """,
            (clean_task,),
        ).fetchone()
        return bool(row)
    except Exception:
        # Migration 254 is a startup prerequisite for the dedicated
        # worker.  If its state cannot be proved, skipping reclamation is
        # safer than double-running a paid provider operation.
        qm.logger.error(
            "redis stale-claim provider fence check failed closed | task_id=%s",
            clean_task,
            exc_info=True,
        )
        return True


async def claim_stale(queue: Any, consumer_name: str, count: int = 5) -> list[Dict[str, Any]]:
    qm = _qm()
    claimed: list[Dict[str, Any]] = []
    pending = await queue._client.xpending_range(
        qm.REDIS_JOB_STREAM_KEY,
        queue._group,
        min="-",
        max="+",
        count=count,
        idle=qm.REDIS_JOB_CLAIM_IDLE_MS,
    )
    now = datetime.now(timezone.utc)
    message_ids = []
    for entry in pending:
        if entry.get("time_since_delivered", 0) < qm.REDIS_JOB_CLAIM_IDLE_MS:
            continue
        message_id = entry["message_id"]
        stream_rows = await queue._client.xrange(qm.REDIS_JOB_STREAM_KEY, min=message_id, max=message_id, count=1)
        if not stream_rows:
            continue
        _, fields = stream_rows[0]
        task_id = str(fields.get("task_id") or "")
        job_type = str(fields.get("job_type") or "")
        current = await queue.get_status(task_id)
        current_status = str((current or {}).get("status") or "").lower()
        if current_status in qm.TERMINAL_JOB_STATUSES:
            await queue._client.xack(qm.REDIS_JOB_STREAM_KEY, queue._group, message_id)
            continue
        # Queue status can be changed to ``retrying`` by a contender that
        # observed the live fence.  Therefore the durable paid-provider
        # lease must gate *every* non-terminal stale message, not only the
        # nominal processing/running states.
        async with qm.db_connection_scope():
            provider_claim_live = await asyncio.to_thread(
                queue._provider_execution_claim_is_live,
                task_id,
            )
        if provider_claim_live:
            continue
        if current_status in {"processing", "running"}:
            started = qm._parse_ts((current or {}).get("started_at") or (current or {}).get("updated_at") or (current or {}).get("created_at"))
            timeout_seconds = int((current or {}).get("timeout_seconds") or 0)
            if started and timeout_seconds > 0 and (now - started).total_seconds() < timeout_seconds:
                continue
        message_ids.append(message_id)
    if not message_ids:
        return claimed
    batches = await queue._client.xclaim(
        qm.REDIS_JOB_STREAM_KEY,
        queue._group,
        consumer_name,
        min_idle_time=qm.REDIS_JOB_CLAIM_IDLE_MS,
        message_ids=message_ids,
    )
    for stream_id, fields in batches:
        raw_job = {
            "_stream_id": stream_id,
            "_consumer_name": consumer_name,
            "task_id": fields.get("task_id", ""),
            "job_type": fields.get("job_type", ""),
            "submission_id": int(fields.get("submission_id") or 0),
            "payload": qm._decode_json(fields.get("payload_json"), {}),
        }
        current = await queue.get_status(raw_job["task_id"])
        if str((current or {}).get("status") or "").lower() in qm.TERMINAL_JOB_STATUSES:
            await queue.ack(raw_job)
            continue
        await queue.set_status(
            raw_job["task_id"],
            qm.TaskStatus.RETRYING.value,
            event_type="retrying",
            stream_id=str(stream_id),
            consumer_name=consumer_name,
            retry_count=int((await queue.get_status(raw_job["task_id"]) or {}).get("retry_count") or 0) + 1,
        )
        current = await queue.get_status(raw_job["task_id"])
        if str((current or {}).get("status") or "").lower() in qm.TERMINAL_JOB_STATUSES:
            await queue.ack(raw_job)
            continue
        claimed.append(raw_job)
    return claimed


async def pop_job(queue: Any, consumer_name: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
    qm = _qm()
    await queue._ensure_ready()
    async with qm.db_connection_scope():
        await asyncio.to_thread(queue._mark_timed_out_jobs)
    batches = await queue._client.xreadgroup(
        queue._group,
        consumer_name,
        {qm.REDIS_JOB_STREAM_KEY: ">"},
        count=1,
        block=max(timeout * 1000, qm.REDIS_JOB_BLOCK_MS),
    )
    if not batches:
        claimed = await queue._claim_stale(consumer_name, count=1)
        return claimed[0] if claimed else None
    _, entries = batches[0]
    stream_id, fields = entries[0]
    raw_job = {
        "_stream_id": stream_id,
        "_consumer_name": consumer_name,
        "task_id": fields.get("task_id", ""),
        "job_type": fields.get("job_type", ""),
        "submission_id": int(fields.get("submission_id") or 0),
        "payload": qm._decode_json(fields.get("payload_json"), {}),
    }
    await queue.set_status(
        raw_job["task_id"],
        qm.TaskStatus.PROCESSING.value,
        event_type="processing",
        stage="processing",
        stream_id=str(stream_id),
        consumer_name=consumer_name,
    )
    current = await queue.get_status(raw_job["task_id"])
    if str((current or {}).get("status") or "").lower() in qm.TERMINAL_JOB_STATUSES:
        await queue.ack(raw_job)
        return None
    return raw_job


async def move_to_dead_letter(queue: Any, raw_job: Dict[str, Any], reason: str) -> None:
    qm = _qm()
    await queue._client.xadd(
        qm.REDIS_JOB_DEAD_STREAM_KEY,
        {
            "task_id": raw_job.get("task_id", ""),
            "job_type": raw_job.get("job_type", ""),
            "reason": reason,
            "payload_json": json.dumps(raw_job.get("payload") or {}, ensure_ascii=False),
            "moved_at": qm._utcnow(),
        },
    )
    await queue.set_status(
        raw_job.get("task_id", ""),
        qm.TaskStatus.FAILED.value,
        event_type="failed",
        error_message=reason,
        stage="dead_letter",
    )
    await queue.ack(raw_job)
