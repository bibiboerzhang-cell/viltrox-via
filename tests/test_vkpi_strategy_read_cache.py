from __future__ import annotations

import importlib.util
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.api.routers import vkpi_category_tracks, vkpi_industry_benchmark
from app.services.cache import memory_cache


@pytest.fixture(autouse=True)
def _reset_strategy_cache_test_state(monkeypatch):
    monkeypatch.setattr(memory_cache, "_redis_retry_after_monotonic", 0.0)
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()
    yield
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()


def test_cache_get_or_build_collapses_concurrent_misses(monkeypatch):
    stored: dict[str, object] = {}
    calls = 0
    calls_lock = threading.Lock()

    monkeypatch.setattr(memory_cache, "cache_get", lambda key: stored.get(key))
    monkeypatch.setattr(memory_cache, "cache_set", lambda key, value, ttl=60: stored.__setitem__(key, value))
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)

    def builder():
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return {"status": "ready"}

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _index: memory_cache.cache_get_or_build("strategy:test", builder, ttl=30), range(32)))

    assert calls == 1
    assert results == [{"status": "ready"}] * 32


def test_cache_get_or_build_collapses_misses_across_two_worker_modules():
    """Two independently loaded modules model two web-worker processes.

    They do not share process-local locks or memory, only the Redis-like cache
    and token-safe lock.  A cold request burst must therefore execute one
    builder across both workers.
    """

    class FakeLock:
        def __init__(self, lock: threading.Lock, blocking_timeout: float):
            self._lock = lock
            self._blocking_timeout = blocking_timeout
            self._owned = False

        def acquire(self, blocking=True):
            assert blocking is True
            self._owned = self._lock.acquire(timeout=self._blocking_timeout)
            return self._owned

        def release(self):
            assert self._owned
            self._owned = False
            self._lock.release()

    class FakeRedis:
        def __init__(self):
            self.values: dict[str, bytes] = {}
            self.values_lock = threading.Lock()
            self.locks: dict[str, threading.Lock] = {}
            self.locks_guard = threading.Lock()

        def get(self, key):
            with self.values_lock:
                return self.values.get(key)

        def setex(self, key, _ttl, value):
            with self.values_lock:
                self.values[key] = value

        def lock(self, key, *, timeout, blocking_timeout):
            assert timeout == 30
            with self.locks_guard:
                lock = self.locks.setdefault(key, threading.Lock())
            return FakeLock(lock, blocking_timeout)

    def load_worker(name: str):
        path = Path(memory_cache.__file__)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    redis = FakeRedis()
    worker_a = load_worker("strategy_cache_worker_a")
    worker_b = load_worker("strategy_cache_worker_b")
    worker_a._get_redis = lambda: redis
    worker_b._get_redis = lambda: redis

    calls = 0
    calls_lock = threading.Lock()

    def builder():
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.04)
        return {"status": "ready", "generation": calls}

    barrier = threading.Barrier(2)

    def run(worker):
        barrier.wait()
        return worker.cache_get_or_build("strategy:two-workers", builder, ttl=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (worker_a, worker_b)))

    assert calls == 1
    assert results == [{"status": "ready", "generation": 1}] * 2


def test_redis_failure_reads_the_process_local_write_fallback(monkeypatch):
    class BrokenRedis:
        @staticmethod
        def get(_key):
            raise ConnectionError("redis unavailable")

        @staticmethod
        def setex(_key, _ttl, _value):
            raise ConnectionError("redis unavailable")

    memory_cache._cache.clear()
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: BrokenRedis())

    calls = 0

    def builder():
        nonlocal calls
        calls += 1
        return {"status": "memory-fallback"}

    first = memory_cache.cache_get_or_build("strategy:redis-down", builder, ttl=30)
    second = memory_cache.cache_get_or_build("strategy:redis-down", builder, ttl=30)

    assert first == second == {"status": "memory-fallback"}
    assert calls == 1


def test_healthy_redis_miss_does_not_resurrect_stale_process_fallback(monkeypatch):
    class HealthyRedisMiss:
        @staticmethod
        def get(_key):
            return None

    memory_cache._cache.clear()
    memory_cache._cache["strategy:deleted-elsewhere"] = {
        "value": {"version": "stale"},
        "expires": time.time() + 30,
    }
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: HealthyRedisMiss())

    assert memory_cache.cache_get("strategy:deleted-elsewhere") is None


def test_set_only_redis_failure_opens_bounded_memory_fallback_circuit(monkeypatch):
    class ReadOnlyRedis:
        @staticmethod
        def get(_key):
            return None

        @staticmethod
        def setex(_key, _ttl, _value):
            raise PermissionError("READONLY replica")

    memory_cache._cache.clear()
    monkeypatch.setattr(memory_cache, "_redis_client", ReadOnlyRedis())
    monkeypatch.setattr(memory_cache, "_redis_retry_after_monotonic", 0.0)

    memory_cache.cache_set("strategy:read-only-redis", {"version": 1}, ttl=30)

    assert memory_cache._redis_retry_after_monotonic > time.monotonic()
    assert memory_cache.cache_get("strategy:read-only-redis") == {"version": 1}

    # Once the bounded circuit closes, a healthy Redis miss is authoritative
    # again, preserving cross-process invalidation semantics.
    monkeypatch.setattr(memory_cache, "_redis_retry_after_monotonic", 0.0)
    assert memory_cache.cache_get("strategy:read-only-redis") is None


def test_memory_fallback_expires_at_ttl(monkeypatch):
    now = [1000.0]
    memory_cache._cache.clear()
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(memory_cache.time, "time", lambda: now[0])

    memory_cache.cache_set("strategy:ttl", {"version": 1}, ttl=30)
    now[0] += 29.999
    assert memory_cache.cache_get("strategy:ttl") == {"version": 1}
    now[0] += 0.002
    assert memory_cache.cache_get("strategy:ttl") is None


def test_memory_fallback_evicts_lru_entries_at_capacity(monkeypatch):
    memory_cache._cache.clear()
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(memory_cache, "VKPI_MEMORY_CACHE_MAX_ENTRIES", 3)
    monkeypatch.setattr(memory_cache, "VKPI_MEMORY_CACHE_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(memory_cache, "VKPI_MEMORY_CACHE_MAX_ENTRY_BYTES", 1024 * 1024)

    for key in ("a", "b", "c"):
        memory_cache.cache_set(f"strategy:{key}", {"key": key}, ttl=30)
    assert memory_cache.cache_get("strategy:a") == {"key": "a"}

    memory_cache.cache_set("strategy:d", {"key": "d"}, ttl=30)

    assert len(memory_cache._cache) == 3
    assert "strategy:a" in memory_cache._cache
    assert "strategy:b" not in memory_cache._cache
    assert set(memory_cache._cache) == {"strategy:a", "strategy:c", "strategy:d"}


def test_memory_fallback_enforces_total_and_per_entry_byte_budgets(monkeypatch):
    memory_cache._cache.clear()
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(memory_cache, "VKPI_MEMORY_CACHE_MAX_ENTRIES", 10)
    monkeypatch.setattr(memory_cache, "VKPI_MEMORY_CACHE_MAX_BYTES", 180)
    monkeypatch.setattr(memory_cache, "VKPI_MEMORY_CACHE_MAX_ENTRY_BYTES", 120)

    memory_cache.cache_set("strategy:first", {"payload": "x" * 80}, ttl=30)
    memory_cache.cache_set("strategy:second", {"payload": "y" * 80}, ttl=30)

    assert "strategy:first" not in memory_cache._cache
    assert "strategy:second" in memory_cache._cache
    assert memory_cache.get_cache_stats()["size_bytes"] <= 180

    memory_cache.cache_set("strategy:oversized", {"payload": "z" * 200}, ttl=30)
    assert "strategy:oversized" not in memory_cache._cache


def test_memory_fallback_stays_bounded_under_ten_thousand_unique_keys(monkeypatch):
    memory_cache._cache.clear()
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(memory_cache, "VKPI_MEMORY_CACHE_MAX_ENTRIES", 128)
    monkeypatch.setattr(memory_cache, "VKPI_MEMORY_CACHE_MAX_BYTES", 128 * 256)
    monkeypatch.setattr(memory_cache, "VKPI_MEMORY_CACHE_MAX_ENTRY_BYTES", 256)

    for index in range(10_000):
        memory_cache.cache_set(
            f"vkpi_gtm:plan_preview:test:{index}",
            {"index": index, "status": "preview"},
            ttl=30,
        )

    stats = memory_cache.get_cache_stats()
    assert stats["size"] == 128
    assert stats["size_bytes"] <= 128 * 256
    assert "vkpi_gtm:plan_preview:test:0" not in memory_cache._cache
    assert "vkpi_gtm:plan_preview:test:9999" in memory_cache._cache


def test_builder_error_releases_singleflight_lock(monkeypatch):
    stored: dict[str, object] = {}
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(memory_cache, "cache_get", lambda key: stored.get(key))
    monkeypatch.setattr(memory_cache, "cache_set", lambda key, value, ttl=60: stored.__setitem__(key, value))

    def broken_builder():
        raise RuntimeError("synthetic builder failure")

    try:
        memory_cache.cache_get_or_build("strategy:error", broken_builder, ttl=30)
    except RuntimeError as exc:
        assert str(exc) == "synthetic builder failure"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("builder failure should propagate to router truth fallback")

    assert memory_cache.cache_get_or_build(
        "strategy:error", lambda: {"status": "recovered"}, ttl=30
    ) == {"status": "recovered"}


def test_redis_unlock_failure_does_not_replace_successful_read_result(monkeypatch):
    class BrokenReleaseLock:
        @staticmethod
        def acquire(blocking=True):
            assert blocking is True
            return True

        @staticmethod
        def release():
            raise ConnectionError("redis disappeared after SETEX")

    class RedisThatFailsOnlyOnUnlock:
        def __init__(self):
            self.value = None

        def get(self, _key):
            return self.value

        def setex(self, _key, _ttl, value):
            self.value = value

        @staticmethod
        def lock(_key, *, timeout, blocking_timeout):
            assert timeout == 30
            assert blocking_timeout == 8
            return BrokenReleaseLock()

    redis = RedisThatFailsOnlyOnUnlock()
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: redis)

    assert memory_cache.cache_get_or_build(
        "strategy:unlock-failure",
        lambda: {"status": "ready"},
        ttl=30,
    ) == {"status": "ready"}


def test_redis_failure_invalidation_also_clears_memory_fallback(monkeypatch):
    class BrokenRedis:
        @staticmethod
        def get(_key):
            raise ConnectionError("redis unavailable")

        @staticmethod
        def setex(_key, _ttl, _value):
            raise ConnectionError("redis unavailable")

        @staticmethod
        def delete(_key):
            raise ConnectionError("redis unavailable")

    memory_cache._cache.clear()
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: BrokenRedis())
    memory_cache.cache_set("strategy:invalidate", {"version": 1}, ttl=30)

    assert memory_cache.cache_get("strategy:invalidate") == {"version": 1}
    assert memory_cache.cache_delete("strategy:invalidate") is True
    assert memory_cache.cache_get("strategy:invalidate") is None


def test_category_tracks_router_uses_short_singleflight_cache(monkeypatch):
    captured: dict[str, object] = {}

    def fake_cache(key, builder, ttl):
        captured.update(key=key, ttl=ttl)
        return {"status": "cached-test"}

    monkeypatch.setattr(vkpi_category_tracks, "cache_get_or_build", fake_cache)
    result = vkpi_category_tracks.get_category_tracks(staff={"id": 1, "organization_id": 17})

    assert result == {"status": "cached-test"}
    assert captured == {"key": "vkpi_strategy:category_tracks:v2:org:17", "ttl": 30}


def test_industry_benchmark_cache_key_uses_clamped_window(monkeypatch):
    captured: dict[str, object] = {}

    def fake_cache(key, builder, ttl):
        captured.update(key=key, ttl=ttl)
        return {"status": "cached-test"}

    monkeypatch.setattr(vkpi_industry_benchmark, "cache_get_or_build", fake_cache)
    result = vkpi_industry_benchmark.get_industry_benchmark(
        window_days=9999,
        staff={"id": 1, "organization_id": 23},
    )

    assert result == {"status": "cached-test"}
    assert captured == {"key": "vkpi_strategy:industry_benchmark:v2:org:23:days:365", "ttl": 30}


def test_strategy_cache_keys_are_isolated_by_organization(monkeypatch):
    keys: list[str] = []

    def fake_cache(key, _builder, ttl):
        assert ttl == 30
        keys.append(key)
        return {"status": "cached-test"}

    monkeypatch.setattr(vkpi_category_tracks, "cache_get_or_build", fake_cache)
    monkeypatch.setattr(vkpi_industry_benchmark, "cache_get_or_build", fake_cache)

    for organization_id in (3, 8):
        staff = {"id": organization_id, "organization_id": organization_id}
        vkpi_category_tracks.get_category_tracks(staff=staff)
        vkpi_industry_benchmark.get_industry_benchmark(window_days=90, staff=staff)

    assert keys == [
        "vkpi_strategy:category_tracks:v2:org:3",
        "vkpi_strategy:industry_benchmark:v2:org:3:days:90",
        "vkpi_strategy:category_tracks:v2:org:8",
        "vkpi_strategy:industry_benchmark:v2:org:8:days:90",
    ]


def test_non_default_organization_fails_closed_without_reading_legacy_global_data(monkeypatch):
    def uncached(_key, builder, ttl):
        assert ttl == 30
        return builder()

    monkeypatch.setattr(vkpi_category_tracks, "cache_get_or_build", uncached)
    monkeypatch.setattr(vkpi_industry_benchmark, "cache_get_or_build", uncached)

    category = vkpi_category_tracks.get_category_tracks(
        staff={"id": 99, "organization_id": 99}
    )
    industry = vkpi_industry_benchmark.get_industry_benchmark(
        window_days=90,
        staff={"id": 99, "organization_id": 99},
    )

    assert category == {
        "status": "scope_unavailable",
        "reason": "赛道聚合的底层评论/证据/目录尚未完成多租户字段收窄，未返回默认工作区数据。",
        "organization_id": 99,
    }
    assert industry == {
        "status": "scope_unavailable",
        "reason": "行业对照的底层证据/深析/目录尚未完成多租户字段收窄，未返回默认工作区数据。",
        "organization_id": 99,
        "window_days": 90,
    }


def test_default_organization_keeps_existing_strategy_builders(monkeypatch):
    from app.domains.market import category_tracks, industry_benchmark

    monkeypatch.setattr(category_tracks, "tracks", lambda: {"status": "category-ready"})
    monkeypatch.setattr(
        industry_benchmark,
        "benchmark",
        lambda *, window_days: {"status": "industry-ready", "window_days": window_days},
    )

    assert vkpi_category_tracks._build_for_organization(1) == {"status": "category-ready"}
    assert vkpi_industry_benchmark._build_for_organization(1, window_days=90) == {
        "status": "industry-ready",
        "window_days": 90,
    }
