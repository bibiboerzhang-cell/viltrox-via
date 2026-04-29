"""
services/via/events.py — Via independent realtime event bus
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict, deque
from typing import Any, AsyncIterator

from app.core.config import (
    REDIS_URL,
    VIA_EVENT_STREAM_BLOCK_MS,
    VIA_EVENT_STREAM_MAXLEN,
    VIA_EVENT_STREAM_PREFIX,
    VIA_SESSION_TTL_SEC,
)

try:
    from redis.asyncio import from_url as redis_from_url
except Exception:
    redis_from_url = None


class BaseViaEventBus:
    backend_name = "unknown"

    async def publish(self, session_key: str, event_type: str, payload: dict[str, Any]) -> str:
        raise NotImplementedError

    async def iter_events(self, session_key: str, after_id: str = "0-0") -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError

    async def runtime_stats(self) -> dict[str, Any]:
        return {"backend": self.backend_name}

    async def close(self) -> None:
        return None


class InMemoryViaEventBus(BaseViaEventBus):
    backend_name = "inmemory"

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=VIA_EVENT_STREAM_MAXLEN))
        self._lock = asyncio.Lock()

    async def publish(self, session_key: str, event_type: str, payload: dict[str, Any]) -> str:
        event_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        event = {
            "id": event_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": payload.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        async with self._lock:
            self._history[session_key].append(event)
            subscribers = list(self._queues.get(session_key, []))
        for queue in subscribers:
            await queue.put(event)
        return event_id

    async def iter_events(self, session_key: str, after_id: str = "0-0") -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._queues[session_key].append(queue)
            history = list(self._history.get(session_key, []))
        try:
            for event in history:
                if after_id != "0-0" and str(event["id"]) <= str(after_id):
                    continue
                yield event
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=max(5, VIA_EVENT_STREAM_BLOCK_MS / 1000))
                except asyncio.TimeoutError:
                    yield {
                        "id": f"{int(time.time() * 1000)}-heartbeat",
                        "event_type": "heartbeat",
                        "payload": {"ok": True},
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    continue
                yield event
        finally:
            async with self._lock:
                if queue in self._queues.get(session_key, []):
                    self._queues[session_key].remove(queue)

    async def runtime_stats(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "backend": self.backend_name,
                "active_sessions": len(self._history),
                "subscribers": sum(len(items) for items in self._queues.values()),
            }


class RedisViaEventBus(BaseViaEventBus):
    backend_name = "redis-stream"

    def __init__(self, redis_url: str = REDIS_URL) -> None:
        if not redis_url:
            raise ValueError("REDIS_URL is required for RedisViaEventBus")
        if redis_from_url is None:
            raise RuntimeError("redis package is not installed")
        self._client = redis_from_url(redis_url, decode_responses=True)

    def _stream_key(self, session_key: str) -> str:
        return f"{VIA_EVENT_STREAM_PREFIX}:{session_key}"

    async def publish(self, session_key: str, event_type: str, payload: dict[str, Any]) -> str:
        stream_key = self._stream_key(session_key)
        event_id = await self._client.xadd(
            stream_key,
            {
                "event_type": event_type,
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "created_at": payload.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            maxlen=VIA_EVENT_STREAM_MAXLEN,
            approximate=True,
        )
        await self._client.expire(stream_key, VIA_SESSION_TTL_SEC)
        return str(event_id)

    async def iter_events(self, session_key: str, after_id: str = "0-0") -> AsyncIterator[dict[str, Any]]:
        stream_key = self._stream_key(session_key)
        last_id = after_id or "0-0"
        while True:
            batches = await self._client.xread({stream_key: last_id}, block=VIA_EVENT_STREAM_BLOCK_MS, count=20)
            if not batches:
                continue
            for _, entries in batches:
                for event_id, fields in entries:
                    last_id = str(event_id)
                    yield {
                        "id": str(event_id),
                        "event_type": fields.get("event_type", "message"),
                        "payload": json.loads(fields.get("payload_json") or "{}"),
                        "created_at": fields.get("created_at", ""),
                    }

    async def runtime_stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "prefix": VIA_EVENT_STREAM_PREFIX,
            "maxlen": VIA_EVENT_STREAM_MAXLEN,
        }

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


def build_via_event_bus() -> BaseViaEventBus:
    if REDIS_URL and redis_from_url is not None:
        return RedisViaEventBus(REDIS_URL)
    return InMemoryViaEventBus()
