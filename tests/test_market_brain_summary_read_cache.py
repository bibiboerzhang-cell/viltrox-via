"""Warm-before-expiry policy for the GTM summary read cache (lane T)."""
from __future__ import annotations

import threading
import time

import pytest

from app.api.routers import vkpi_market_brain_summary
from app.domains.market_brain import summary
from app.domains.market_brain import summary_read_cache as read_cache
from app.services.cache import memory_cache


@pytest.fixture(autouse=True)
def _isolated_memory_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(memory_cache, "_redis_retry_after_monotonic", 0.0)
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()
    with read_cache._REFRESH_GUARD:
        read_cache._REFRESH_INFLIGHT.clear()
    yield
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()
    with read_cache._REFRESH_GUARD:
        read_cache._REFRESH_INFLIGHT.clear()


def _staff() -> dict:
    return {"id": 7, "organization_id": 1, "organization_scope_status": "resolved", "role": "member", "is_owner": 0}


class _Clock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


def _inline(run) -> None:
    run()


def _collect(bucket: list):
    return lambda run: bucket.append(run)


def test_cold_miss_builds_once_and_stamps_build_time() -> None:
    clock = _Clock()
    calls = 0

    def builder() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "ok", "n": calls}

    outcomes: list[str] = []
    scheduled: list = []
    for _ in range(2):
        value = read_cache.cached_summary(
            "vkpi_gtm:summary:test", builder, ttl=120, cache_if=lambda v: True,
            observe=lambda item: outcomes.append(item["outcome"]),
            refresh_scheduler=_collect(scheduled), now_fn=clock,
        )
        assert value == {"status": "ok", "n": 1}

    assert calls == 1
    assert outcomes == ["miss_builder", "hit"]
    assert memory_cache.cache_get(read_cache.built_at_key("vkpi_gtm:summary:test")) == {"built_at": clock.now}
    assert scheduled == []
    assert read_cache.entry_age_seconds(
        "vkpi_gtm:summary:test", cache_get_fn=memory_cache.cache_get, now_fn=clock
    ) == 0.0


def test_hit_past_half_ttl_schedules_one_background_refresh_and_keeps_serving() -> None:
    clock = _Clock()
    calls = 0

    def builder() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "ok", "n": calls}

    scheduled: list = []
    common = dict(ttl=120, cache_if=lambda v: True, refresh_scheduler=_collect(scheduled), now_fn=clock)
    read_cache.cached_summary("k", builder, **common)
    clock.now += 59
    assert read_cache.cached_summary("k", builder, **common) == {"status": "ok", "n": 1}
    assert scheduled == []
    clock.now += 2
    outcomes: list[str] = []
    assert read_cache.cached_summary(
        "k", builder, observe=lambda item: outcomes.append(item["outcome"]), **common
    ) == {"status": "ok", "n": 1}
    assert outcomes == ["hit"]
    assert len(scheduled) == 1
    assert read_cache.refresh_inflight("k")
    # A second hit while the refresh is in flight must not spawn another one.
    assert read_cache.cached_summary("k", builder, **common) == {"status": "ok", "n": 1}
    assert len(scheduled) == 1

    scheduled[0]()
    assert not read_cache.refresh_inflight("k")
    assert calls == 2
    assert read_cache.cached_summary("k", builder, **common) == {"status": "ok", "n": 2}
    assert memory_cache.cache_get(read_cache.built_at_key("k")) == {"built_at": clock.now}
    assert len(scheduled) == 1


def test_refresh_runs_inline_and_replaces_entry_before_expiry() -> None:
    clock = _Clock()
    calls = 0

    def builder() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "ok", "n": calls}

    common = dict(ttl=60, cache_if=lambda v: True, refresh_scheduler=_inline, now_fn=clock)
    assert read_cache.cached_summary("k", builder, **common) == {"status": "ok", "n": 1}
    clock.now += 31
    assert read_cache.cached_summary("k", builder, **common) == {"status": "ok", "n": 1}
    assert calls == 2
    assert read_cache.cached_summary("k", builder, **common) == {"status": "ok", "n": 2}
    assert calls == 2


def test_missing_build_stamp_never_triggers_refresh() -> None:
    scheduled: list = []
    memory_cache.cache_set("k", {"status": "ok"}, ttl=120)
    value = read_cache.cached_summary(
        "k", lambda: {"status": "fresh"}, ttl=120, cache_if=lambda v: True,
        refresh_scheduler=_collect(scheduled), now_fn=_Clock(),
    )
    assert value == {"status": "ok"}
    assert scheduled == []


def test_uncacheable_payload_is_not_stamped_and_not_refreshed() -> None:
    clock = _Clock()
    scheduled: list = []
    calls = 0

    def builder() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "degraded"}

    common = dict(ttl=120, cache_if=lambda v: False, refresh_scheduler=_collect(scheduled), now_fn=clock)
    read_cache.cached_summary("k", builder, **common)
    clock.now += 100
    read_cache.cached_summary("k", builder, **common)
    assert calls == 2
    assert memory_cache._cache == {}
    assert scheduled == []


def test_background_refresh_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    outcomes = iter([{"status": "ok", "n": 1}, RuntimeError("temporary read failure")])

    def builder() -> dict:
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value

    common = dict(ttl=60, cache_if=lambda v: True, refresh_scheduler=_inline, now_fn=clock)
    read_cache.cached_summary("k", builder, **common)
    clock.now += 31
    with caplog.at_level("WARNING"):
        assert read_cache.cached_summary("k", builder, **common) == {"status": "ok", "n": 1}
    assert not read_cache.refresh_inflight("k")
    assert any("gtm.summary_early_refresh_failed" in record.getMessage() for record in caplog.records)
    assert memory_cache.cache_get("k") == {"status": "ok", "n": 1}


def test_fenced_cache_mutations_keep_refresh_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    scheduled: list = []
    common = dict(ttl=60, cache_if=lambda v: True, refresh_scheduler=_collect(scheduled), now_fn=clock)
    read_cache.cached_summary("k", lambda: {"status": "ok"}, **common)
    clock.now += 31
    monkeypatch.setattr(read_cache, "release_validation_active", lambda: True)
    read_cache.cached_summary("k", lambda: {"status": "ok"}, **common)
    assert scheduled == []


def test_manual_refresh_still_bypasses_cache_and_restamps() -> None:
    clock = _Clock()
    calls = 0

    def builder() -> dict:
        nonlocal calls
        calls += 1
        return {"status": "ok", "n": calls}

    common = dict(ttl=120, cache_if=lambda v: True, refresh_scheduler=_inline, now_fn=clock)
    read_cache.cached_summary("k", builder, **common)
    clock.now += 10
    outcomes: list[str] = []
    forced = read_cache.cached_summary(
        "k", builder, force_refresh=True, observe=lambda item: outcomes.append(item["outcome"]), **common
    )
    assert forced == {"status": "ok", "n": 2}
    assert outcomes == ["refresh_builder"]
    assert memory_cache.cache_get(read_cache.built_at_key("k")) == {"built_at": clock.now}


def test_router_hits_are_fast_and_warm_in_background(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    started = threading.Event()
    release = threading.Event()

    def build(staff: dict) -> dict:
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            release.wait(timeout=5)
        return {"status": "ok", "method": "summary-test", "n": calls}

    monkeypatch.setattr(summary, "build_summary", build)
    first = vkpi_market_brain_summary.get_market_brain_summary(staff=_staff())
    assert first["n"] == 1

    key = vkpi_market_brain_summary._summary_cache_key(_staff())
    stamp_key = read_cache.built_at_key(key)
    stamp = memory_cache.cache_get(stamp_key)
    assert stamp is not None
    ttl = vkpi_market_brain_summary._GTM_READ_CACHE_TTL_SEC
    memory_cache.cache_set(stamp_key, {"built_at": stamp["built_at"] - ttl * 0.6}, ttl=ttl)

    t0 = time.perf_counter()
    second = vkpi_market_brain_summary.get_market_brain_summary(staff=_staff())
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert second["n"] == 1
    assert elapsed_ms < 50, elapsed_ms
    assert started.wait(timeout=5)
    release.set()
    deadline = time.time() + 5
    while read_cache.refresh_inflight(key) and time.time() < deadline:
        time.sleep(0.01)
    assert not read_cache.refresh_inflight(key)
    assert vkpi_market_brain_summary.get_market_brain_summary(staff=_staff())["n"] == 2
    assert calls == 2
