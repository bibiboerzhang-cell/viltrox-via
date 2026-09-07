"""Explicit search submission must not block the async request loop on queue IO."""
from __future__ import annotations

import asyncio
from threading import Event, get_ident

import pytest

from app.api.routers import vkpi_kol_pool_search as route


@pytest.mark.parametrize("search_mode", ["fresh_network", "hybrid"])
@pytest.mark.parametrize("endpoint", ["smart_kol_search", "smart_kol_search_profile_advance_job"])
def test_explicit_search_queue_io_leaves_request_loop_responsive(monkeypatch, search_mode, endpoint):
    started, release = Event(), Event()
    request_thread = get_ident()
    calls = []
    body = {"input": "street photography", "search_mode": search_mode,
            "include_new_discovery": True, "execute_new_discovery": True,
            "idempotency_key": "one-explicit-submission"}

    def enqueue(**kwargs):
        assert get_ident() != request_thread, "synchronous queue IO blocked the request loop"
        calls.append(kwargs)
        started.set()
        assert release.wait(2), "request loop failed to make progress during queue IO"
        return {"status": "queued", "search_session": {"id": 88}, "job_id": 12}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("inventory or providers must not execute during queue submission")

    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance", enqueue)
    monkeypatch.setattr(route, "_smart_local_recall", forbidden)
    monkeypatch.setattr(route.kol_smart_query_planner, "plan_text_query_provider_free", forbidden)
    monkeypatch.setattr(route.kol_profile_discovery, "discover_new_creators", forbidden)

    async def execute():
        task = asyncio.create_task(getattr(route, endpoint)(body=body, staff={"id": 7}))
        try:
            assert await asyncio.to_thread(started.wait, 1), "queue did not start off the event loop"
            assert not task.done()
            await asyncio.sleep(0)  # Another coroutine can run before the queue transaction returns.
            release.set()
            return await asyncio.wait_for(task, 2)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    result = asyncio.run(execute())
    assert len(calls) == 1
    assert calls[0]["body"]["idempotency_key"] == "one-explicit-submission"
    assert result["status"] == "queued" and result["search_session"] == {"id": 88}
    assert result["provider_calls"] is False
