"""Per-employee read cache + version-keyed invalidation for search history (lane T)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import search_sessions
from app.domains.kol import search_sessions_history_read_cache as read_cache
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


def _staff(user_id: int = 7) -> dict:
    return {"id": user_id, "user_id": user_id, "role": "member"}


def _ready(tag: str, count: int = 1) -> dict:
    return {
        "status": "ready",
        "count": count,
        "items": [{"id": 44, "query_text": tag}][:count],
        "worker_health": {"state": "observed"},
        "filters": {"status": "ready", "limit": 12, "item_limit": 5, "archived": False},
    }


def _run(builder, *, versions, staff=None, **filters) -> dict:
    return read_cache.cached_list_history(
        list_history_fn=builder,
        staff=_staff() if staff is None else staff,
        data_version_fn=versions,
        **filters,
    )


def test_normalized_filters_mirror_list_history_inputs() -> None:
    first = read_cache.normalized_filters(limit=12, status="READY", query_type="", item_limit=5, archived=False)
    second = read_cache.normalized_filters(limit=12, status="ready", query_type="", item_limit=5, archived=0)
    assert first == second
    assert read_cache.normalized_filters(limit=999)["limit"] == 50
    assert read_cache.normalized_filters(item_limit=99)["item_limit"] == 10
    assert read_cache.normalized_filters(status="not-a-status")["status"] != "not-a-status"


def test_cache_key_partitions_actor_version_and_filters() -> None:
    filters = read_cache.normalized_filters(limit=12, status="ready")
    base = read_cache.history_cache_key(actor_id=7, data_version="s:1:t:i:1:t", filters=filters)
    assert base == read_cache.history_cache_key(actor_id=7, data_version="s:1:t:i:1:t", filters=filters)
    assert base != read_cache.history_cache_key(actor_id=8, data_version="s:1:t:i:1:t", filters=filters)
    assert base != read_cache.history_cache_key(actor_id=7, data_version="s:2:t:i:1:t", filters=filters)
    assert base != read_cache.history_cache_key(
        actor_id=7, data_version="s:1:t:i:1:t", filters=read_cache.normalized_filters(limit=12, archived=True)
    )
    assert base.startswith("vkpi_kol_search_history:v1:actor:7:")


def test_second_call_hits_cache_and_facade_runs_once() -> None:
    calls: list[dict] = []

    def builder(**kwargs) -> dict:
        calls.append(kwargs)
        return _ready("first")

    outcomes: list[str] = []
    kwargs = dict(limit=12, status="ready", item_limit=5, archived=False)
    first = read_cache.cached_list_history(
        list_history_fn=builder, staff=_staff(), data_version_fn=lambda actor: "s:591:t0:i:516:t0",
        observe=lambda item: outcomes.append(item["outcome"]), **kwargs,
    )
    second = read_cache.cached_list_history(
        list_history_fn=builder, staff=_staff(), data_version_fn=lambda actor: "s:591:t0:i:516:t0",
        observe=lambda item: outcomes.append(item["outcome"]), **kwargs,
    )

    assert first == second == _ready("first")
    assert len(calls) == 1
    assert calls[0] == {"limit": 12, "status": "ready", "query_type": "", "item_limit": 5,
                        "staff": _staff(), "archived": False}
    assert outcomes == ["miss_builder", "hit"]
    entry = next(iter(memory_cache._cache.values()))
    assert 0 < entry["expires"] - time.time() <= read_cache.SEARCH_HISTORY_READ_CACHE_TTL_SECONDS


def test_cache_never_crosses_employees() -> None:
    def builder(**kwargs) -> dict:
        return _ready(f"owner-{kwargs['staff']['user_id']}")

    versions = lambda actor: f"s:{actor}:t:i:{actor}:t"  # noqa: E731
    seven = _run(builder, versions=versions, staff=_staff(7), limit=12)
    eight = _run(builder, versions=versions, staff=_staff(8), limit=12)
    seven_again = _run(builder, versions=versions, staff=_staff(7), limit=12)

    assert seven["items"][0]["query_text"] == "owner-7"
    assert eight["items"][0]["query_text"] == "owner-8"
    assert seven_again == seven
    assert len(memory_cache._cache) == 2


def test_session_write_bumps_version_and_next_read_rebuilds() -> None:
    versions = iter(["s:1:t0:i:1:t0", "s:1:t0:i:1:t0", "s:1:t1:i:2:t1"])
    payloads = iter([_ready("before"), _ready("after")])
    calls = 0

    def builder(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        return next(payloads)

    assert _run(builder, versions=lambda actor: next(versions), limit=12)["items"][0]["query_text"] == "before"
    assert _run(builder, versions=lambda actor: next(versions), limit=12)["items"][0]["query_text"] == "before"
    assert _run(builder, versions=lambda actor: next(versions), limit=12)["items"][0]["query_text"] == "after"
    assert calls == 2


def test_unresolved_actor_is_served_directly_and_never_cached() -> None:
    calls = 0
    probes = 0

    def builder(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        return {"status": "ready", "count": 0, "items": [], "filters": {"scope": "current_staff_unresolved"}}

    def probe(actor):
        nonlocal probes
        probes += 1
        return "unused"

    outcomes: list[str] = []
    for _ in range(2):
        result = read_cache.cached_list_history(
            list_history_fn=builder, staff={"role": "member"}, data_version_fn=probe,
            observe=lambda item: outcomes.append(item["outcome"]), limit=12,
        )
        assert result["filters"]["scope"] == "current_staff_unresolved"
    assert calls == 2 and probes == 0
    assert outcomes == ["actor_unresolved_builder"] * 2
    assert memory_cache._cache == {}


def test_unreadable_version_probe_bypasses_cache_without_failing() -> None:
    calls = 0

    def builder(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        return _ready("direct")

    outcomes: list[str] = []
    for _ in range(2):
        result = read_cache.cached_list_history(
            list_history_fn=builder, staff=_staff(), data_version_fn=lambda actor: None,
            observe=lambda item: outcomes.append(item["outcome"]), limit=12,
        )
        assert result == _ready("direct")
    assert calls == 2
    assert outcomes == ["version_unavailable_builder"] * 2
    assert memory_cache._cache == {}


def test_non_ready_payload_is_not_pinned() -> None:
    payloads = iter([{"status": "degraded", "items": []}, _ready("ok")])
    calls = 0

    def builder(**kwargs) -> dict:
        nonlocal calls
        calls += 1
        return next(payloads)

    assert _run(builder, versions=lambda actor: "v", limit=12)["status"] == "degraded"
    assert memory_cache._cache == {}
    assert _run(builder, versions=lambda actor: "v", limit=12)["status"] == "ready"
    assert calls == 2


def test_history_data_version_is_scoped_to_the_employee(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, tuple]] = []

    class _Cursor:
        def fetchone(self):
            return {"session_count": 591, "session_latest": "t-s", "item_count": 516, "item_latest": "t-i"}

    class _Conn:
        def execute(self, sql, params=()):
            captured.append((" ".join(sql.split()), tuple(params)))
            return _Cursor()

    from app.db import connection

    monkeypatch.setattr(connection, "get_conn", lambda: _Conn())
    assert read_cache.history_data_version(7) == "s:591:t-s:i:516:t-i"
    sql, params = captured[0]
    assert params == (7, 7, 7, 7)
    assert sql.count("created_by = ?") == 4
    assert "EXTRACT(EPOCH FROM MAX(updated_at))" in sql
    assert "EXTRACT(EPOCH FROM MAX(i.updated_at))" in sql
    assert "%" not in sql


def test_same_second_archive_and_restore_yield_distinct_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Archive + undo-restore inside one second must not share a cache key.

    The DB compat layer renders timestamps at second precision and the row
    count is unchanged by either write, so the sub-second epoch is the only
    signal separating the two states (observed live: restore served stale).
    """
    from decimal import Decimal

    from app.db import connection

    epochs = iter([Decimal("1788401302.417000"), Decimal("1788401302.679107")])

    class _Cursor:
        def __init__(self, latest):
            self._latest = latest

        def fetchone(self):
            return {"session_count": 591, "session_latest": self._latest, "item_count": 516, "item_latest": None}

    class _Conn:
        def execute(self, sql, params=()):
            return _Cursor(next(epochs))

    monkeypatch.setattr(connection, "get_conn", lambda: _Conn())
    after_archive = read_cache.history_data_version(7)
    after_restore = read_cache.history_data_version(7)
    assert after_archive == "s:591:1788401302.417000:i:516:"
    assert after_restore == "s:591:1788401302.679107:i:516:"
    filters = read_cache.normalized_filters()
    assert read_cache.history_cache_key(
        actor_id=7, data_version=after_archive, filters=filters
    ) != read_cache.history_cache_key(actor_id=7, data_version=after_restore, filters=filters)


def test_history_data_version_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import connection

    def broken():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(connection, "get_conn", broken)
    assert read_cache.history_data_version(7) is None


def test_concurrent_cold_requests_collapse_to_one_facade_call() -> None:
    calls = 0
    guard = threading.Lock()

    def slow_builder(**kwargs) -> dict:
        nonlocal calls
        with guard:
            calls += 1
        time.sleep(0.03)
        return _ready("burst")

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(lambda _i: _run(slow_builder, versions=lambda actor: "v", limit=12), range(32))
        )
    assert calls == 1
    assert all(item == _ready("burst") for item in results)


def test_router_serves_history_through_read_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def facade(**kwargs) -> dict:
        calls.append(kwargs)
        return _ready("router")

    monkeypatch.setattr(search_sessions, "list_history", facade)
    monkeypatch.setattr(read_cache, "history_data_version", lambda actor: f"s:{actor}:t:i:1:t")

    first = vkpi_kol_pool_search.list_kol_search_history(
        limit=12, status="ready", query_type="", item_limit=5, archived=False, staff=_staff(7)
    )
    second = vkpi_kol_pool_search.list_kol_search_history(
        limit=12, status="ready", query_type="", item_limit=5, archived=False, staff=_staff(7)
    )
    assert first == second == _ready("router")
    assert len(calls) == 1
    assert calls[0]["staff"] == _staff(7) and calls[0]["limit"] == 12 and calls[0]["status"] == "ready"
