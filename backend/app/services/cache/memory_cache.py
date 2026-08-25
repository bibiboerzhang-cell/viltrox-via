"""
services/cache/memory_cache.py — Redis-backed cache with memory fallback for local dev
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import inspect
import json
import time
import threading
import weakref
from functools import wraps
from typing import Any, Callable, Optional

from app.core.config import (
    REDIS_CACHE_DEFAULT_TTL_SEC,
    REDIS_CACHE_PREFIX,
    REDIS_URL,
    VKPI_MEMORY_CACHE_MAX_BYTES,
    VKPI_MEMORY_CACHE_MAX_ENTRIES,
    VKPI_MEMORY_CACHE_MAX_ENTRY_BYTES,
)
from app.core.logging import get_logger
from app.core.release_validation import release_validation_active

try:
    from redis import Redis
except Exception:
    Redis = None

logger = get_logger(__name__)

_cache: dict[str, dict] = {}
_lock = threading.RLock()
_build_locks_guard = threading.Lock()
_build_locks: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_stats = {
    "hits": 0,
    "misses": 0,
    "sets": 0,
    "evictions": 0,
    "backend": "memory",
}
_redis_client = None
_redis_retry_after_monotonic = 0.0
_REDIS_FAILURE_COOLDOWN_SEC = 2.0

# The strategy aggregations protected by ``cache_get_or_build`` currently take
# about 2-3 seconds on the local data set.  Keep the distributed lease well
# above that observed tail while bounding how long a request may wait for a
# peer process.  If the peer is genuinely stuck, the caller is allowed to
# rebuild after the blocking timeout instead of hanging the endpoint.
_BUILD_LOCK_LEASE_SEC = 30
_BUILD_LOCK_BLOCKING_TIMEOUT_SEC = 8


def _cache_mutations_fenced() -> bool:
    """Fail closed when cache state is not allowed to change."""

    try:
        return bool(release_validation_active())
    except Exception:
        logger.error("cache.release_validation_status_failed", exc_info=True)
        return True


def _get_redis():
    global _redis_client
    if time.monotonic() < _redis_retry_after_monotonic:
        return None
    if _redis_client is not None:
        _stats["backend"] = "redis"
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


def _mark_redis_failure() -> None:
    """Open a short per-process circuit so fallback can absorb an outage.

    In particular, a Redis deployment may still answer GET while rejecting
    SETEX (read-only replica, failover, quota).  Without this circuit the
    healthy miss would correctly reject stale fallback but then rebuild on
    every request.  During the bounded cooldown this worker serves its local
    TTL copy; after cooldown Redis becomes authoritative again.
    """

    global _redis_retry_after_monotonic
    with _lock:
        _redis_retry_after_monotonic = max(
            _redis_retry_after_monotonic,
            time.monotonic() + _REDIS_FAILURE_COOLDOWN_SEC,
        )
        _stats["backend"] = "redis_circuit_open_memory_fallback"


def _full_key(key: str) -> str:
    return f"{REDIS_CACHE_PREFIX}:{key}"


def _json_default(o: Any) -> Any:
    # Decimal/日期等非原生 JSON 类型 → 可缓存形态(Decimal→float,datetime→isoformat),
    # 避免 cache_set 因 TypeError 整条跳过缓存 + 刷 set_skipped_non_json 警告
    # (例:market-intelligence/cards 的 SUM 返回 Decimal,原先每次都不缓存)。
    # 其余真未知类型仍抛 TypeError → cache_set 优雅跳过(保留兜底)。
    from decimal import Decimal
    from datetime import date, datetime

    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def _serialize(value: Any) -> bytes:
    # JSON instead of pickle: avoids arbitrary-code-execution on deserialize and
    # cross-version unpickle drift. Cached values across all call sites are
    # dict/list/str/int/float/bool/None (JSON-safe). Decimal/datetime 经 _json_default
    # 归一化为可缓存形态;真正非 JSON 安全的类型仍抛 → cache_set 跳过缓存。
    return json.dumps(value, default=_json_default).encode("utf-8")


def _deserialize(value: bytes | None) -> Any:
    if value is None:
        return None
    return json.loads(value.decode("utf-8"))


def _memory_get(key: str) -> tuple[bool, Any]:
    """Return a live process-local fallback entry without changing hit stats."""
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return False, None
        if entry["expires"] < time.time():
            # Expiry cleanup is a cache mutation.  A fenced read may observe
            # the stale entry but must leave it untouched for normal runtime
            # to clean up after validation ends.
            if not _cache_mutations_fenced():
                del _cache[key]
                _stats["evictions"] += 1
            return False, None
        if _cache_mutations_fenced():
            return True, entry["value"]
        # Dict insertion order is used as a small, dependency-free LRU queue.
        # Refreshing the entry prevents a hot authorization-scoped result from
        # being discarded before a cold, one-off parameterized preview.
        _cache.pop(key)
        _cache[key] = entry
        return True, entry["value"]


def _memory_entry_size(entry: dict[str, Any]) -> int:
    size = entry.get("size_bytes")
    if isinstance(size, int) and size >= 0:
        return size
    try:
        return len(_serialize(entry.get("value")))
    except (TypeError, ValueError):
        # Directly injected legacy/test entries that cannot be serialized are
        # treated as over-sized, so capacity enforcement fails closed.
        return VKPI_MEMORY_CACHE_MAX_ENTRY_BYTES + 1


def _memory_set(key: str, value: Any, serialized: bytes, ttl: int) -> bool:
    """Store a bounded process-local fallback entry.

    Redis remains the authoritative cache when healthy.  This fallback exists
    for local development and short Redis outages, so it must never become an
    unbounded sink for high-cardinality request parameters.
    """

    if _cache_mutations_fenced():
        return False

    incoming_bytes = len(serialized)
    if incoming_bytes > VKPI_MEMORY_CACHE_MAX_ENTRY_BYTES:
        with _lock:
            if _cache_mutations_fenced():
                return False
            if _cache.pop(key, None) is not None:
                _stats["evictions"] += 1
        logger.warning(
            "cache.memory_entry_too_large",
            extra={"key": key, "size_bytes": incoming_bytes},
        )
        return False

    now = time.time()
    with _lock:
        if _cache_mutations_fenced():
            return False
        previous = _cache.pop(key, None)
        expired_keys = [
            existing_key
            for existing_key, entry in _cache.items()
            if entry["expires"] < now
        ]
        for expired_key in expired_keys:
            del _cache[expired_key]
            _stats["evictions"] += 1

        current_bytes = sum(_memory_entry_size(entry) for entry in _cache.values())
        while _cache and (
            len(_cache) >= VKPI_MEMORY_CACHE_MAX_ENTRIES
            or current_bytes + incoming_bytes > VKPI_MEMORY_CACHE_MAX_BYTES
        ):
            oldest_key = next(iter(_cache))
            evicted = _cache.pop(oldest_key)
            current_bytes -= _memory_entry_size(evicted)
            _stats["evictions"] += 1

        if incoming_bytes > VKPI_MEMORY_CACHE_MAX_BYTES:
            if previous is not None:
                # Do not resurrect the previous value after an explicit set;
                # the caller still receives the freshly built response.
                _stats["evictions"] += 1
            return False

        _cache[key] = {
            "value": value,
            "expires": now + ttl,
            "size_bytes": incoming_bytes,
        }
        return True


def cache_get(key: str) -> Optional[Any]:
    client = _get_redis()
    if client is not None:
        try:
            value = client.get(_full_key(key))
            if value is not None:
                _stats["hits"] += 1
                return _deserialize(value)
        except Exception:
            logger.warning("cache.redis_get_failed", extra={"key": key}, exc_info=True)
            _mark_redis_failure()

            # Redis could not answer, so the process-local fallback is the
            # only bounded-degradation source available to this worker.
            found, fallback = _memory_get(key)
            if found:
                _stats["hits"] += 1
                _stats["backend"] = "redis_with_memory_fallback"
                return fallback

        # A *healthy* Redis miss is authoritative.  Reading an old local
        # fallback here would resurrect data after another process called
        # cache_delete/cache_clear.  A value whose prior SETEX failed may be
        # rebuilt once when Redis recovers; correctness wins over that small
        # amount of duplicate work.
        _stats["misses"] += 1
        return None

    found, fallback = _memory_get(key)
    if found:
        _stats["hits"] += 1
        return fallback
    _stats["misses"] += 1
    return None


async def cache_get_async(key: str) -> Optional[Any]:
    return await asyncio.to_thread(cache_get, key)


def cache_set(key: str, value: Any, ttl: int = REDIS_CACHE_DEFAULT_TTL_SEC) -> None:
    if _cache_mutations_fenced():
        return
    # Guard: values must be JSON-serializable (see _serialize). A non-JSON-safe
    # value (datetime/Decimal/set/custom object) would break the Redis path and,
    # if cached in-memory, break later when Redis returns. Skip cache gracefully.
    try:
        serialized = _serialize(value)
    except (TypeError, ValueError):
        logger.warning("cache.set_skipped_non_json", extra={"key": key}, exc_info=True)
        return
    client = _get_redis()
    if client is not None:
        try:
            if _cache_mutations_fenced():
                return
            client.setex(_full_key(key), int(ttl), serialized)
            # Do not retain an older fallback copy after Redis recovered. A
            # later healthy Redis miss must not resurrect stale process data.
            with _lock:
                if not _cache_mutations_fenced():
                    _cache.pop(key, None)
            _stats["sets"] += 1
            return
        except Exception:
            logger.warning("cache.redis_set_failed", extra={"key": key, "ttl": int(ttl)}, exc_info=True)
            _mark_redis_failure()
    if _cache_mutations_fenced() or not _memory_set(key, value, serialized, int(ttl)):
        return
    with _lock:
        _stats["sets"] += 1


@contextmanager
def _distributed_build_lock(key: str):
    """Yield whether this process owns Redis' token-safe build lock.

    redis-py's ``Lock`` uses an ownership token when releasing the lock, so an
    expired owner cannot delete a newer process' lease.  Failure to create or
    acquire the lock is a cache-degradation event, not an endpoint failure;
    the process-local lock still prevents a same-worker stampede.
    """

    if _cache_mutations_fenced():
        yield False
        return
    client = _get_redis()
    if client is None:
        yield False
        return

    lock = None
    acquired = False
    try:
        lock = client.lock(
            _full_key(f"build_lock:{key}"),
            timeout=_BUILD_LOCK_LEASE_SEC,
            blocking_timeout=_BUILD_LOCK_BLOCKING_TIMEOUT_SEC,
        )
        if not _cache_mutations_fenced():
            acquired = bool(lock.acquire(blocking=True))
    except Exception:
        logger.warning("cache.redis_build_lock_failed", extra={"key": key}, exc_info=True)
        _mark_redis_failure()

    try:
        yield acquired
    finally:
        if acquired and lock is not None and not _cache_mutations_fenced():
            try:
                lock.release()
            except Exception:
                # Lease expiry or Redis loss must not replace a successfully
                # built read response with a 500. SETEX already published the
                # cache value when possible.
                logger.warning("cache.redis_build_unlock_failed", extra={"key": key}, exc_info=True)
                _mark_redis_failure()
        # If the fence became active after acquisition, deliberately leave the
        # Redis lease to expire instead of issuing DEL through lock.release().
        # Validation stays write-free at the cost of at most one lease TTL.


def cache_get_or_build(
    key: str,
    builder: Callable[[], Any],
    ttl: int = REDIS_CACHE_DEFAULT_TTL_SEC,
    *,
    cache_if: Callable[[Any], bool] | None = None,
    observe: Callable[[dict[str, Any]], None] | None = None,
    force_refresh: bool = False,
) -> Any:
    """Return a cached value or collapse concurrent cold builds.

    The second cache read inside the per-key lock prevents a request burst from
    repeating the same expensive read-only aggregation.  ``force_refresh``
    skips a pre-existing value, while a short-lived generation marker lets
    concurrent or immediately repeated refreshes reuse the value just rebuilt
    by their leader.  This preserves a truthful manual refresh without turning
    it into an unbounded rebuild amplifier.  When Redis is available, a
    token-safe bounded lock collapses cold misses across web workers as well.
    Redis/lock failure falls back to one build per process.
    """

    started_at = time.perf_counter()
    refresh_marker_key = f"manual_refresh:{key}"
    refresh_result_key = f"manual_refresh_result:{key}"
    refresh_window_ttl = max(1, min(int(ttl), 5))
    refresh_marker_at_start = None

    def _observe(
        outcome: str,
        *,
        builder_ms: float | None = None,
        cache_candidate: bool | None = None,
    ) -> None:
        if observe is None:
            return
        payload = {
            "outcome": outcome,
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 3),
            "builder_ms": round(builder_ms, 3) if builder_ms is not None else None,
            "cache_candidate": cache_candidate,
        }
        try:
            observe(payload)
        except Exception:
            # Telemetry must never turn a healthy read into an endpoint error.
            logger.warning("cache.get_or_build_observer_failed", exc_info=True)

    def _recent_refresh_value(marker: Any, *, outcome: str) -> tuple[bool, Any]:
        if marker is None:
            return False, None
        state = str(marker.get("state") or "") if isinstance(marker, dict) else "cached"
        if state == "error":
            _observe(outcome.replace("hit", "error"), cache_candidate=False)
            raise RuntimeError(
                "manual refresh was attempted recently but did not complete; retry after 5 seconds"
            )
        if state == "result":
            wrapped = cache_get(refresh_result_key)
            if isinstance(wrapped, dict) and "value" in wrapped:
                _observe(outcome, cache_candidate=False)
                return True, wrapped["value"]
            return False, None
        cached = cache_get(key)
        if cached is not None:
            _observe(outcome)
            return True, cached
        return False, None

    if force_refresh and _cache_mutations_fenced():
        _observe("refresh_fenced", cache_candidate=False)
        raise RuntimeError("manual refresh is unavailable while cache mutations are fenced")
    if force_refresh:
        refresh_marker_at_start = cache_get(refresh_marker_key)

    def _build(*, outcome: str, allow_cache_write: bool) -> Any:
        builder_started_at = time.perf_counter()
        try:
            value = builder()
            candidate = (
                bool(cache_if(value))
                if allow_cache_write and cache_if is not None
                else allow_cache_write
            )
            if allow_cache_write and candidate:
                cache_set(key, value, ttl=ttl)
            if allow_cache_write and force_refresh:
                state = "cached" if candidate else "result"
                if not candidate:
                    cache_set(
                        refresh_result_key,
                        {"value": value},
                        ttl=refresh_window_ttl,
                    )
                cache_set(
                    refresh_marker_key,
                    {"generation": time.time_ns(), "state": state},
                    ttl=refresh_window_ttl,
                )
        except Exception:
            if allow_cache_write and force_refresh and not _cache_mutations_fenced():
                cache_set(
                    refresh_marker_key,
                    {"generation": time.time_ns(), "state": "error"},
                    ttl=refresh_window_ttl,
                )
            _observe(
                "builder_error",
                builder_ms=(time.perf_counter() - builder_started_at) * 1000,
                cache_candidate=False,
            )
            raise
        _observe(
            outcome,
            builder_ms=(time.perf_counter() - builder_started_at) * 1000,
            cache_candidate=candidate if allow_cache_write else None,
        )
        return value

    if force_refresh and refresh_marker_at_start is not None:
        handled, cached_value = _recent_refresh_value(
            refresh_marker_at_start,
            outcome="refresh_recent_hit",
        )
        if handled:
            return cached_value
    elif not force_refresh:
        cached_value = cache_get(key)
        if cached_value is not None:
            _observe("hit")
            return cached_value
    if _cache_mutations_fenced():
        return _build(outcome="fenced_builder", allow_cache_write=False)

    with _build_locks_guard:
        build_lock = _build_locks.get(key)
        if build_lock is None:
            build_lock = threading.Lock()
            _build_locks[key] = build_lock

    with build_lock:
        if force_refresh:
            current_marker = cache_get(refresh_marker_key)
            if current_marker is not None and current_marker != refresh_marker_at_start:
                handled, cached_value = _recent_refresh_value(
                    current_marker,
                    outcome="refresh_wait_hit",
                )
                if handled:
                    return cached_value
        else:
            cached_value = cache_get(key)
            if cached_value is not None:
                _observe("miss_wait_hit")
                return cached_value
        if _cache_mutations_fenced():
            return _build(outcome="fenced_builder", allow_cache_write=False)
        with _distributed_build_lock(key):
            if _cache_mutations_fenced():
                return _build(outcome="fenced_builder", allow_cache_write=False)
            # A different process may have populated Redis while this worker
            # waited for the distributed lock.
            if force_refresh:
                current_marker = cache_get(refresh_marker_key)
                if current_marker is not None and current_marker != refresh_marker_at_start:
                    handled, cached_value = _recent_refresh_value(
                        current_marker,
                        outcome="refresh_distributed_hit",
                    )
                    if handled:
                        return cached_value
            else:
                cached_value = cache_get(key)
                if cached_value is not None:
                    _observe("miss_distributed_hit")
                    return cached_value
            # Read aggregators may return an honest 200 error/degraded payload
            # instead of raising.  Callers can keep that fail-soft contract
            # without pinning the failure for the full TTL.
            return _build(
                outcome="refresh_builder" if force_refresh else "miss_builder",
                allow_cache_write=True,
            )


async def cache_set_async(key: str, value: Any, ttl: int = REDIS_CACHE_DEFAULT_TTL_SEC) -> None:
    await asyncio.to_thread(cache_set, key, value, ttl)


def cache_delete(key: str) -> bool:
    if _cache_mutations_fenced():
        return False
    redis_deleted = False
    client = _get_redis()
    if client is not None:
        try:
            if not _cache_mutations_fenced():
                redis_deleted = bool(client.delete(_full_key(key)))
        except Exception:
            logger.warning("cache.redis_delete_failed", extra={"key": key}, exc_info=True)
            _mark_redis_failure()
    with _lock:
        memory_deleted = (
            _cache.pop(key, None) is not None
            if not _cache_mutations_fenced()
            else False
        )
    return redis_deleted or memory_deleted


def cache_clear(prefix: str = "") -> int:
    if _cache_mutations_fenced():
        return 0
    redis_deleted = 0
    client = _get_redis()
    if client is not None:
        try:
            pattern = _full_key(prefix + "*") if prefix else _full_key("*")
            for key in client.scan_iter(match=pattern):
                if _cache_mutations_fenced():
                    break
                redis_deleted += int(client.delete(key) or 0)
        except Exception:
            logger.warning("cache.redis_clear_failed", extra={"prefix": prefix}, exc_info=True)
            _mark_redis_failure()
    with _lock:
        if _cache_mutations_fenced():
            memory_deleted = 0
        elif not prefix:
            memory_deleted = len(_cache)
            _cache.clear()
        else:
            keys_to_del = [k for k in _cache if k.startswith(prefix)]
            for k in keys_to_del:
                del _cache[k]
            memory_deleted = len(keys_to_del)
    return redis_deleted + memory_deleted


def get_cache_stats() -> dict:
    client = _get_redis()
    total = _stats["hits"] + _stats["misses"]
    hit_rate = _stats["hits"] / total if total > 0 else 0
    data = {
        **_stats,
        "size": len(_cache) if client is None else None,
        "size_bytes": (
            sum(_memory_entry_size(entry) for entry in _cache.values())
            if client is None
            else None
        ),
        "memory_max_entries": VKPI_MEMORY_CACHE_MAX_ENTRIES,
        "memory_max_bytes": VKPI_MEMORY_CACHE_MAX_BYTES,
        "memory_max_entry_bytes": VKPI_MEMORY_CACHE_MAX_ENTRY_BYTES,
        "hit_rate": round(hit_rate, 3),
        "prefix": REDIS_CACHE_PREFIX,
    }
    if client is not None:
        try:
            data["redis_info"] = {
                "dbsize": int(client.dbsize() or 0),
            }
        except Exception:
            logger.warning("cache.redis_info_failed", exc_info=True)
            _mark_redis_failure()
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
    if _cache_mutations_fenced():
        return 0
    client = _get_redis()
    if client is not None:
        return 0
    now = time.time()
    with _lock:
        if _cache_mutations_fenced():
            return 0
        expired_keys = [k for k, v in _cache.items() if v["expires"] < now]
        for k in expired_keys:
            del _cache[k]
            _stats["evictions"] += 1
        return len(expired_keys)
