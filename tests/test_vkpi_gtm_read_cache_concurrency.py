from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import time

import pytest

from app.api.routers import vkpi_market_brain_summary
from app.domains.market_brain import summary
from app.services.cache import memory_cache


@pytest.fixture(autouse=True)
def _isolated_memory_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_cache, "_get_redis", lambda: None)
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()
    yield
    memory_cache._cache.clear()
    memory_cache._build_locks.clear()


def _staff() -> dict:
    return {
        "id": 7,
        "organization_id": 1,
        "organization_scope_status": "resolved",
        "role": "member",
        "is_owner": 0,
    }


def test_force_refresh_accepts_a_new_generation_built_by_another_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[dict] = []
    marker_key = "manual_refresh:vkpi_gtm:test:distributed-refresh"
    marker_reads = iter([None, None, 123456])

    def fake_cache_get(key: str):
        if key == marker_key:
            return next(marker_reads)
        if key == "vkpi_gtm:test:distributed-refresh":
            return {"status": "ready", "source": "peer-refresh"}
        raise AssertionError(f"unexpected cache key: {key}")

    monkeypatch.setattr(memory_cache, "cache_get", fake_cache_get)
    monkeypatch.setattr(memory_cache, "_cache_mutations_fenced", lambda: False)

    @contextmanager
    def peer_build_lock(_key: str):
        yield True

    monkeypatch.setattr(memory_cache, "_distributed_build_lock", peer_build_lock)
    result = memory_cache.cache_get_or_build(
        "vkpi_gtm:test:distributed-refresh",
        lambda: (_ for _ in ()).throw(AssertionError("local builder ran")),
        ttl=30,
        observe=observations.append,
        force_refresh=True,
    )

    assert result == {"status": "ready", "source": "peer-refresh"}
    assert [item["outcome"] for item in observations] == ["refresh_distributed_hit"]


def test_concurrent_summary_requests_collapse_to_one_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    guard = threading.Lock()

    def slow_build(_staff_value: dict) -> dict:
        nonlocal calls
        with guard:
            calls += 1
        time.sleep(0.03)
        return {"method": "summary-test", "items": [1, 2, 3]}

    monkeypatch.setattr(summary, "build_summary", slow_build)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(
                lambda _index: vkpi_market_brain_summary.get_market_brain_summary(
                    staff=_staff()
                ),
                range(32),
            )
        )

    assert calls == 1
    assert all(
        result == {"method": "summary-test", "items": [1, 2, 3]}
        for result in results
    )
