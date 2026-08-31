"""services/jobs/queue_heartbeat.py — RedisJobQueue 就绪/事件/心跳协作对象。

class-LOC 棘轮拆分:行为逐字搬运自 queue.py::RedisJobQueue:

- worker_readiness:零消费就绪探针(绝不 XREADGROUP/XCLAIM/XACK);
- set_status + publish_event:台账状态推进后向 task/user 双通道发事件,
  终态回退被吞(_stale_status_ignored)时零事件;
- subscribe_task_events:订阅任务事件,空消息按心跳补位,finally 退订;
- runtime_stats:队列深度 + 台账汇总。

所有可 monkeypatch 的模块级符号(db_connection_scope/常量/工具函数)一律经
``_qm()`` 读取已绑定的 queue 门面且不反向 import;实例侧一律走
``queue.<attr>``,保持补丁面不变。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from app.services.jobs.queue_runtime import queue_facade


def _qm():
    """Resolve the bound live facade so monkeypatches stay effective."""
    return queue_facade()


async def worker_readiness(queue: Any, consumer_names: list[str]) -> Dict[str, Any]:
    """Prove Redis/group/consumer readiness without consuming a message.

    Release health must not be inferred from a Postgres heartbeat written
    before Redis is usable.  This probe creates the idempotent consumer
    registrations, confirms the expected group on the expected stream, and
    pings Redis.  It deliberately never calls XREADGROUP/XCLAIM/XACK.
    """

    qm = _qm()
    names = [str(name or "").strip() for name in consumer_names if str(name or "").strip()]
    if not names or len(names) != len(set(names)):
        raise RuntimeError("redis worker readiness requires unique consumer names")
    await queue._ensure_ready()
    pong = await queue._client.ping()
    if pong is not True and str(pong).strip().upper() != "PONG":
        raise RuntimeError("redis ping did not return PONG")
    groups = await queue._client.xinfo_groups(qm.REDIS_JOB_STREAM_KEY)
    group_names = {
        str((item or {}).get("name") or "")
        for item in (groups or [])
        if isinstance(item, dict)
    }
    if queue._group not in group_names:
        raise RuntimeError("redis stream consumer group is not visible")
    for name in names:
        await queue._client.xgroup_createconsumer(
            qm.REDIS_JOB_STREAM_KEY,
            queue._group,
            name,
        )
    consumers = await queue._client.xinfo_consumers(qm.REDIS_JOB_STREAM_KEY, queue._group)
    registered = {
        str((item or {}).get("name") or "")
        for item in (consumers or [])
        if isinstance(item, dict)
    }
    missing = [name for name in names if name not in registered]
    if missing:
        raise RuntimeError("redis consumer registration is incomplete")
    return {
        "redis_ready": True,
        "redis_stream_key": qm.REDIS_JOB_STREAM_KEY,
        "redis_group_name": queue._group,
        "redis_consumer_count": len(names),
    }


async def publish_event(queue: Any, task_id: str, payload: Dict[str, Any], user_id: int = 0) -> None:
    await queue._client.publish(queue._task_channel(task_id), json.dumps(payload, ensure_ascii=False))
    if user_id:
        await queue._client.publish(queue._user_channel(user_id), json.dumps(payload, ensure_ascii=False))


async def set_status(queue: Any, task_id: str, status: str, **extra: Any) -> None:
    qm = _qm()
    async with qm.db_connection_scope():
        snapshot = await asyncio.to_thread(queue._update_job_ledger, task_id, status, **extra)
    if not snapshot:
        return
    if snapshot.get("_stale_status_ignored"):
        return
    user_id = int(snapshot.get("user_id") or extra.get("user_id") or 0)
    effective_status = str(snapshot.get("status") or status)
    event_type = extra.get("event_type") or (
        "result_ready"
        if effective_status in {qm.TaskStatus.DONE.value, qm.TaskStatus.PARTIAL.value}
        else effective_status
    )
    payload = {
        "event_type": event_type,
        "task_id": task_id,
        "status": effective_status,
        "created_at": qm._utcnow(),
        "submission_id": snapshot.get("submission_id") or "",
        "retry_count": snapshot.get("retry_count") or "0",
        "stage": snapshot.get("stage") or extra.get("stage") or "",
    }
    for key in ("summary", "error_message", "result_path", "detection_status"):
        value = snapshot.get(key) or extra.get(key)
        if value:
            payload[key] = value
    await queue._publish_event(task_id, payload, user_id=user_id)


async def subscribe_task_events(queue: Any, task_id: str):
    qm = _qm()
    pubsub = queue._client.pubsub()
    await pubsub.subscribe(queue._task_channel(task_id))
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if message is None:
                yield {
                    "event_type": "heartbeat",
                    "task_id": task_id,
                    "created_at": qm._utcnow(),
                }
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="ignore")
            yield qm._decode_json(data, {"event_type": "message", "task_id": task_id})
    finally:
        await pubsub.unsubscribe(queue._task_channel(task_id))
        close_fn = getattr(pubsub, "aclose", None)
        if close_fn is not None:
            await close_fn()


async def runtime_stats(queue: Any) -> Dict[str, Any]:
    qm = _qm()
    await queue._ensure_ready()
    queue_depth = await queue._client.xlen(qm.REDIS_JOB_STREAM_KEY)
    async with qm.db_connection_scope():
        summary = await asyncio.to_thread(queue._ledger_queue_summary)
    try:
        groups = await queue._client.xinfo_groups(qm.REDIS_JOB_STREAM_KEY)
    except Exception:
        groups = []
    return {
        "backend": queue.backend_name,
        "stream_key": qm.REDIS_JOB_STREAM_KEY,
        "group": queue._group,
        "queue_depth": int(queue_depth or 0),
        "summary": summary,
        "groups": groups,
        "dead_letter_stream": qm.REDIS_JOB_DEAD_STREAM_KEY,
    }
