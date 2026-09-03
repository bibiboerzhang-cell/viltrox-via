"""Read cache + version-keyed invalidation for creative-segments search (lane T)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from app.api.routers import vkpi_creative_segments
from app.domains.content import creative_segments_read_cache as read_cache
from app.services.cache import memory_cache


@pytest.fixture(autouse=True)
def _isolated_memory_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(memory_cache, "_redis_retry_after_monotonic", 0.0)
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()
    yield
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()


def _ready_payload(tag: str) -> dict:
    return {
        "status": "ready",
        "method": "lexicon_segments_v1",
        "filters": {"query": "", "style": "", "focal": ""},
        "scanned_videos": 3,
        "segment_count": 9,
        "matched": 9,
        "returned": 2,
        "items": [{"segment_id": f"{tag}-1"}, {"segment_id": f"{tag}-2"}],
        "facets": {},
        "generated_at": "2026-09-02T00:00:00+00:00",
        "note": "n",
    }


def test_normalized_filters_mirror_segment_search_inputs() -> None:
    first = read_cache.normalized_filters(query="  Portrait ", style=" Handheld", focal=" 85mm ", limit=50)
    second = read_cache.normalized_filters(query="portrait", style="handheld", focal="85", limit=50)
    assert first == second
    assert first["limit"] == 50
    assert read_cache.normalized_filters(limit=999)["limit"] == 200
    assert read_cache.normalized_filters(limit=0)["limit"] == 30


def test_cache_key_changes_with_data_version_and_filters() -> None:
    filters = read_cache.normalized_filters(query="a", limit=30)
    base = read_cache.segment_search_cache_key(data_version="n:1:id:1:at:x", filters=filters)
    assert base == read_cache.segment_search_cache_key(data_version="n:1:id:1:at:x", filters=filters)
    assert base != read_cache.segment_search_cache_key(data_version="n:2:id:2:at:y", filters=filters)
    assert base != read_cache.segment_search_cache_key(
        data_version="n:1:id:1:at:x",
        filters=read_cache.normalized_filters(query="b", limit=30),
    )
    assert base.startswith("vkpi_creative_segments:search:v1:")
    assert "a" not in base.split(":q:")[-1] or len(base.split(":q:")[-1]) == 20


def test_second_call_hits_cache_and_builder_runs_once() -> None:
    calls = 0

    def builder(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        return _ready_payload("first")

    outcomes: list[str] = []
    common = dict(
        query="",
        style="",
        focal="",
        limit=50,
        data_version_fn=lambda: "n:3:id:9:at:t0",
        segment_search_fn=builder,
        observe=lambda item: outcomes.append(item["outcome"]),
    )
    first = read_cache.cached_segment_search(**common)
    second = read_cache.cached_segment_search(**common)

    assert first == second == _ready_payload("first")
    assert calls == 1
    assert outcomes == ["miss_builder", "hit"]
    entry = next(iter(memory_cache._cache.values()))
    assert 0 < entry["expires"] - time.time() <= read_cache.CREATIVE_SEGMENTS_READ_CACHE_TTL_SECONDS


def test_source_table_write_changes_version_and_rebuilds_immediately() -> None:
    versions = iter(["n:3:id:9:at:t0", "n:3:id:9:at:t0", "n:4:id:10:at:t1"])
    payloads = iter([_ready_payload("v0"), _ready_payload("v1")])
    calls = 0

    def builder(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        return next(payloads)

    def run() -> dict:
        return read_cache.cached_segment_search(
            query="", style="", focal="", limit=50,
            data_version_fn=lambda: next(versions),
            segment_search_fn=builder,
        )

    assert run()["items"][0]["segment_id"] == "v0-1"
    assert run()["items"][0]["segment_id"] == "v0-1"
    assert run()["items"][0]["segment_id"] == "v1-1"
    assert calls == 2


def test_error_payload_is_never_cached_but_empty_library_is() -> None:
    calls = 0
    payloads = iter([
        {"status": "error", "reason": "boom", "items": []},
        {"status": "empty", "reason": "no final_v1", "items": [], "matched": 0},
        {"status": "ready", "items": [1]},
    ])

    def builder(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        return next(payloads)

    def run() -> dict:
        return read_cache.cached_segment_search(
            query="", style="", focal="", limit=30,
            data_version_fn=lambda: "n:0:id:0:at:",
            segment_search_fn=builder,
        )

    assert run()["status"] == "error"
    assert memory_cache._cache == {}
    assert run()["status"] == "empty"
    assert run()["status"] == "empty"
    assert calls == 2


def test_unreadable_version_probe_bypasses_cache_without_failing() -> None:
    calls = 0

    def builder(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        return _ready_payload("direct")

    outcomes: list[str] = []
    for _ in range(2):
        result = read_cache.cached_segment_search(
            query="", style="", focal="", limit=30,
            data_version_fn=lambda: None,
            segment_search_fn=builder,
            observe=lambda item: outcomes.append(item["outcome"]),
        )
        assert result == _ready_payload("direct")
    assert calls == 2
    assert outcomes == ["version_unavailable_builder"] * 2
    assert memory_cache._cache == {}


def test_final_v1_data_version_reads_ready_rows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, tuple]] = []

    class _Row(dict):
        pass

    class _Cursor:
        def fetchone(self):
            return _Row(n=570, max_id=15188, latest="2026-09-02T21:58:35Z")

    class _Conn:
        def execute(self, sql, params=()):
            captured.append((" ".join(sql.split()), tuple(params)))
            return _Cursor()

    from app.db import connection

    monkeypatch.setattr(connection, "get_conn", lambda: _Conn())
    version = read_cache.final_v1_data_version()

    assert version == "n:570:id:15188:at:2026-09-02T21:58:35Z"
    sql, params = captured[0]
    assert "status = 'ready'" in sql and "derive_method = ?" in sql
    assert "EXTRACT(EPOCH FROM MAX(updated_at))" in sql
    assert params == ("video_analysis_final_v1",)
    assert "%" not in sql


def test_same_second_rewrites_yield_distinct_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two rewrites of existing ready rows inside one second must not share a key.

    Count and max id are unchanged by a rewrite; the compat layer renders
    timestamps at second precision, so only the sub-second epoch separates them.
    """
    from decimal import Decimal

    from app.db import connection

    epochs = iter([Decimal("1788386315.916170"), Decimal("1788386315.940002")])

    class _Cursor:
        def __init__(self, latest):
            self._latest = latest

        def fetchone(self):
            return {"n": 570, "max_id": 15188, "latest": self._latest}

    class _Conn:
        def execute(self, sql, params=()):
            return _Cursor(next(epochs))

    monkeypatch.setattr(connection, "get_conn", lambda: _Conn())
    first = read_cache.final_v1_data_version()
    second = read_cache.final_v1_data_version()
    assert first == "n:570:id:15188:at:1788386315.916170"
    assert second == "n:570:id:15188:at:1788386315.940002"
    filters = read_cache.normalized_filters()
    assert read_cache.segment_search_cache_key(
        data_version=first, filters=filters
    ) != read_cache.segment_search_cache_key(data_version=second, filters=filters)


def test_final_v1_data_version_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import connection

    def broken():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(connection, "get_conn", broken)
    assert read_cache.final_v1_data_version() is None


def test_concurrent_cold_requests_collapse_to_one_build() -> None:
    calls = 0
    guard = threading.Lock()

    def slow_builder(**kwargs) -> dict:
        nonlocal calls
        with guard:
            calls += 1
        time.sleep(0.03)
        return _ready_payload("burst")

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda _i: read_cache.cached_segment_search(
                    query="", style="", focal="", limit=50,
                    data_version_fn=lambda: "n:3:id:9:at:t0",
                    segment_search_fn=slow_builder,
                ),
                range(32),
            )
        )
    assert calls == 1
    assert all(item == _ready_payload("burst") for item in results)


def test_router_serves_through_read_cache_and_keeps_error_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def builder(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("segment index unavailable")
        return _ready_payload("router")

    from app.domains.content import creative_segments

    monkeypatch.setattr(creative_segments, "segment_search", builder)
    monkeypatch.setattr(read_cache, "final_v1_data_version", lambda: "n:1:id:1:at:x")

    failed = vkpi_creative_segments.creative_segments_search(
        query="", style="", focal="", limit=50, staff={"id": 7}
    )
    assert failed == {"status": "error", "reason": "segment index unavailable", "items": []}
    assert memory_cache._cache == {}

    first = vkpi_creative_segments.creative_segments_search(query="", style="", focal="", limit=50, staff={"id": 7})
    second = vkpi_creative_segments.creative_segments_search(query="", style="", focal="", limit=50, staff={"id": 7})
    assert first == second == _ready_payload("router")
    assert calls == 2
