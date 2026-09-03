"""Authentication-free Redis/memory counter used by rate-limit policies."""

from __future__ import annotations

import logging
import time

from app.core.config import REDIS_RATE_LIMIT_PREFIX, REDIS_URL


try:
    from redis import Redis
except Exception:
    Redis = None


logger = logging.getLogger("viltrox.platform.rate_limit_store")

_stats = {
    "checks": 0,
    "blocks": 0,
    "backend": "memory",
    "redis_errors": 0,
}
_redis_client = None
_memory_windows: dict[str, tuple[int, float]] = {}


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL or Redis is None:
        return None
    _redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
    _stats["backend"] = "redis"
    return _redis_client


def _counter_key(bucket: str, actor_key: str) -> str:
    return f"{REDIS_RATE_LIMIT_PREFIX}:{bucket}:{actor_key}"


def check_rate_limit(
    bucket: str,
    client_id: str,
    max_requests: int,
    window_sec: int,
    *,
    redis_getter=None,
) -> tuple[bool, int]:
    key = _counter_key(bucket, client_id)
    _stats["checks"] += 1
    client = (redis_getter or _get_redis)()
    if client is not None:
        try:
            current = int(client.incr(key))
            if current == 1:
                client.expire(key, window_sec)
        except Exception as exc:  # Redis blip: fail open onto the per-process window, never 500 the route
            _stats["redis_errors"] += 1
            if _stats["redis_errors"] in (1, 10, 100) or _stats["redis_errors"] % 1000 == 0:
                logger.warning(
                    "rate_limit_store.redis_unavailable bucket=%s err=%s count=%s (memory window fallback)",
                    bucket, type(exc).__name__, _stats["redis_errors"],
                )
        else:
            if current > max_requests:
                _stats["blocks"] += 1
                return False, 0
            return True, max_requests - current

    now = time.time()
    current, expires_at = _memory_windows.get(key, (0, now + window_sec))
    if expires_at < now:
        current, expires_at = 0, now + window_sec
    current += 1
    _memory_windows[key] = (current, expires_at)
    if current > max_requests:
        _stats["blocks"] += 1
        return False, 0
    return True, max_requests - current


def get_rate_limit_stats() -> dict:
    client = _get_redis()
    total = _stats["checks"]
    block_rate = _stats["blocks"] / total if total else 0
    data = {
        **_stats,
        "block_rate": round(block_rate, 3),
        "prefix": REDIS_RATE_LIMIT_PREFIX,
    }
    if client is not None:
        try:
            data["redis_keys"] = sum(
                1 for _ in client.scan_iter(match=f"{REDIS_RATE_LIMIT_PREFIX}:*")
            )
        except Exception:
            data["redis_keys"] = None
    else:
        data["active_windows"] = len(_memory_windows)
    return data


def cleanup_old_buckets(max_age_sec: int = 3600) -> int:
    client = _get_redis()
    if client is not None:
        return 0
    cutoff = time.time() - max_age_sec
    deleted = 0
    for key, (_, expires_at) in list(_memory_windows.items()):
        if expires_at < cutoff:
            _memory_windows.pop(key, None)
            deleted += 1
    return deleted


__all__ = ["check_rate_limit", "cleanup_old_buckets", "get_rate_limit_stats"]
