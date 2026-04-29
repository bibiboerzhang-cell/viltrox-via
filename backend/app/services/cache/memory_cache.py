"""
services/cache/memory_cache.py — Redis-backed cache with memory fallback for local dev
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import pickle
import time
import threading
from functools import wraps
from typing import Any, Callable, Optional

from app.core.config import REDIS_CACHE_DEFAULT_TTL_SEC, REDIS_CACHE_PREFIX, REDIS_URL
from app.core.logging import get_logger

try:
    from redis import Redis
except Exception:
    Redis = None

logger = get_logger(__name__)

_cache: dict[str, dict] = {}
_lock = threading.RLock()
_stats = {
    "hits": 0,
    "misses": 0,
    "sets": 0,
    "evictions": 0,
    "backend": "memory",
}
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL or Redis is None:
        return None
    try:
        _redis_client = Redis.from_url(REDIS_URL, decode_responses=False)
        _stats["backend"] = "redis"
        return _redis_client
    except Exception:
        logger.warning("cache.redis_connect_failed", exc_info=True)
        _stats["backend"] = "memory"
        _redis_client = None
        return None


def _full_key(key: str) -> str:
    return f"{REDIS_CACHE_PREFIX}:{key}"


def _serialize(value: Any) -> bytes:
    return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)


def _deserialize(value: bytes | None) -> Any:
    if value is None:
        return None
    return pickle.loads(value)


def cache_get(key: str) -> Optional[Any]:
    client = _get_redis()
    if client is not None:
        try:
            value = client.get(_full_key(key))
            if value is None:
                _stats["misses"] += 1
                return None
            _stats["hits"] += 1
            return _deserialize(value)
        except Exception:
            logger.warning("cache.redis_get_failed", extra={"key": key}, exc_info=True)
            _stats["misses"] += 1
            return None

    with _lock:
        entry = _cache.get(key)
        if entry is None:
            _stats["misses"] += 1
            return None
        if entry["expires"] < time.time():
            del _cache[key]
            _stats["misses"] += 1
            _stats["evictions"] += 1
            return None
        _stats["hits"] += 1
        return entry["value"]


async def cache_get_async(key: str) -> Optional[Any]:
    return await asyncio.to_thread(cache_get, key)


def cache_set(key: str, value: Any, ttl: int = REDIS_CACHE_DEFAULT_TTL_SEC) -> None:
    client = _get_redis()
    if client is not None:
        try:
            client.setex(_full_key(key), int(ttl), _serialize(value))
            _stats["sets"] += 1
            return
        except Exception:
            logger.warning("cache.redis_set_failed", extra={"key": key, "ttl": int(ttl)}, exc_info=True)
    with _lock:
        _cache[key] = {
            "value": value,
            "expires": time.time() + ttl,
        }
        _stats["sets"] += 1


async def cache_set_async(key: str, value: Any, ttl: int = REDIS_CACHE_DEFAULT_TTL_SEC) -> None:
    await asyncio.to_thread(cache_set, key, value, ttl)


def cache_delete(key: str) -> bool:
    client = _get_redis()
    if client is not None:
        try:
            return bool(client.delete(_full_key(key)))
        except Exception:
            logger.warning("cache.redis_delete_failed", extra={"key": key}, exc_info=True)
            return False
    with _lock:
        if key in _cache:
            del _cache[key]
            return True
        return False


def cache_clear(prefix: str = "") -> int:
    client = _get_redis()
    if client is not None:
        try:
            pattern = _full_key(prefix + "*") if prefix else _full_key("*")
            deleted = 0
            for key in client.scan_iter(match=pattern):
                deleted += int(client.delete(key) or 0)
            return deleted
        except Exception:
            logger.warning("cache.redis_clear_failed", extra={"prefix": prefix}, exc_info=True)
            return 0
    with _lock:
        if not prefix:
            n = len(_cache)
            _cache.clear()
            return n
        keys_to_del = [k for k in _cache if k.startswith(prefix)]
        for k in keys_to_del:
            del _cache[k]
        return len(keys_to_del)


def get_cache_stats() -> dict:
    total = _stats["hits"] + _stats["misses"]
    hit_rate = _stats["hits"] / total if total > 0 else 0
    data = {
        **_stats,
        "size": len(_cache) if _get_redis() is None else None,
        "hit_rate": round(hit_rate, 3),
        "prefix": REDIS_CACHE_PREFIX,
    }
    client = _get_redis()
    if client is not None:
        try:
            data["redis_info"] = {
                "dbsize": int(client.dbsize() or 0),
            }
        except Exception:
            logger.warning("cache.redis_info_failed", exc_info=True)
    return data


def _make_cache_key(fn: Callable, key: Optional[str], args: tuple, kwargs: dict) -> str:
    if key:
        return key
    args_str = ":".join(str(a) for a in args)
    kwargs_str = ":".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
    return f"{fn.__module__}.{fn.__name__}:{args_str}:{kwargs_str}"


def cached(ttl: int = REDIS_CACHE_DEFAULT_TTL_SEC, key: Optional[str] = None):
    def decorator(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_wrapper(*args, **kwargs):
                cache_key = _make_cache_key(fn, key, args, kwargs)
                cached_value = await cache_get_async(cache_key)
                if cached_value is not None:
                    return cached_value
                result = await fn(*args, **kwargs)
                await cache_set_async(cache_key, result, ttl)
                return result

            return async_wrapper

        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            cache_key = _make_cache_key(fn, key, args, kwargs)
            cached_value = cache_get(cache_key)
            if cached_value is not None:
                return cached_value
            result = fn(*args, **kwargs)
            cache_set(cache_key, result, ttl)
            return result

        return sync_wrapper

    return decorator


def cache_invalidate_admin():
    return cache_clear(prefix="admin_")


def _cleanup_expired():
    client = _get_redis()
    if client is not None:
        return 0
    now = time.time()
    with _lock:
        expired_keys = [k for k, v in _cache.items() if v["expires"] < now]
        for k in expired_keys:
            del _cache[k]
            _stats["evictions"] += 1
        return len(expired_keys)
