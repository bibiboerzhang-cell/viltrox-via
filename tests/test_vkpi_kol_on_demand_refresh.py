from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.api.routers import vkpi_kol_pool
from app.domains.kol import pool as kol_pool
import app.domains.sync.refresh_tier as refresh_tier
import app.domains.tasks.enqueue as task_enqueue
from app.workers.tasks import vkpi as vkpi_tasks


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, object]] = []
        self.statuses: list[dict[str, object]] = []
        self.current_status = {"status": "queued"}

    async def enqueue(self, job_type: str, payload: dict, **kwargs):
        self.enqueued.append({"job_type": job_type, "payload": payload, **kwargs})
        return "task-on-demand"

    async def get_status(self, task_id: str):
        return self.current_status

    async def set_status(self, task_id: str, status: str, **extra):
        self.statuses.append({"task_id": task_id, "status": status, **extra})
        self.current_status = {"status": status}


def test_enqueue_kol_pool_on_demand_refresh_uses_active_lock(monkeypatch) -> None:
    queue = FakeQueue()
    monkeypatch.setattr(task_enqueue, "ensure_vkpi_task_schema", lambda: None)
    monkeypatch.setattr(kol_pool, "get_item", lambda kol_pool_id: {"item": {"id": kol_pool_id, "platform": "instagram", "handle": "unit_creator"}})

    result = asyncio.run(
        task_enqueue.enqueue_kol_pool_on_demand_refresh(
            queue,
            123,
            reason="search_stale_while_revalidate",
            max_posts=8,
            staff={"id": 7, "staff_id": 7, "role": "admin"},
        )
    )

    assert result["task_id"] == "task-on-demand"
    assert result["task_type"] == task_enqueue.VKPI_KOL_POOL_ON_DEMAND_REFRESH
    assert result["lock_key"] == "vkpi_kol_pool_on_demand_refresh:kol_pool:123"
    assert queue.enqueued[0]["job_type"] == task_enqueue.VKPI_KOL_POOL_ON_DEMAND_REFRESH
    assert queue.enqueued[0]["payload"]["kol_pool_id"] == 123
    assert queue.enqueued[0]["payload"]["max_posts"] == 3
    assert queue.enqueued[0]["priority"] == 4


def test_on_demand_refresh_endpoint_defaults_to_status_only(monkeypatch) -> None:
    queue = FakeQueue()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_queue=queue)))
    events: list[str] = []
    monkeypatch.delenv("VKPI_KOL_ON_DEMAND_REFRESH_ENABLED", raising=False)
    monkeypatch.delenv("VKPI_ENABLE_KOL_ON_DEMAND_REFRESH", raising=False)
    monkeypatch.setattr(
        refresh_tier,
        "record_kol_search",
        lambda kol_pool_id: events.append("record") or {"kol_pool_id": kol_pool_id, "tier": "warm", "search_count_30d": 1},
    )
    monkeypatch.setattr(
        refresh_tier,
        "freshness_for_kol",
        lambda kol_pool_id: events.append("freshness") or {"kol_pool_id": kol_pool_id, "tier": "warm", "needs_refresh": True, "reason": "never_refreshed"},
    )
    monkeypatch.setattr(
        task_enqueue,
        "enqueue_kol_pool_on_demand_refresh",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider task was enqueued")),
    )

    result = asyncio.run(
        vkpi_kol_pool._maybe_enqueue_refresh(
            request,
            123,
            staff={"id": 7},
            enabled=True,
            reason="search_stale_while_revalidate",
        )
    )

    assert result["triggered"] is False
    assert result["reason"] == "on_demand_refresh_disabled"
    assert result["provider_calls_enabled"] is False
    assert result["search_marker"]["tier"] == "warm"
    assert events == ["record", "freshness"]
    assert queue.enqueued == []


def test_on_demand_refresh_endpoint_enqueues_when_operator_enabled(monkeypatch) -> None:
    queue = FakeQueue()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_queue=queue)))
    monkeypatch.setenv("VKPI_KOL_ON_DEMAND_REFRESH_ENABLED", "1")
    monkeypatch.setattr(refresh_tier, "record_kol_search", lambda kol_pool_id: {"kol_pool_id": kol_pool_id, "tier": "warm"})
    monkeypatch.setattr(refresh_tier, "freshness_for_kol", lambda kol_pool_id: {"kol_pool_id": kol_pool_id, "tier": "warm", "needs_refresh": True})

    async def enqueue(_queue, kol_pool_id: int, **kwargs):
        assert _queue is queue
        assert kol_pool_id == 456
        assert kwargs["max_posts"] == 1
        assert kwargs["enforce_target_write"] is True
        return {"task_id": "task-enabled", "task_type": task_enqueue.VKPI_KOL_POOL_ON_DEMAND_REFRESH, "lock_key": "lock"}

    monkeypatch.setattr(task_enqueue, "enqueue_kol_pool_on_demand_refresh", enqueue)

    result = asyncio.run(
        vkpi_kol_pool._maybe_enqueue_refresh(
            request,
            456,
            staff={"id": 7},
            enabled=True,
            reason="detail_stale_while_revalidate",
        )
    )

    assert result["triggered"] is True
    assert result["provider_calls_enabled"] is True
    assert result["task_id"] == "task-enabled"
    assert result["reason"] == "detail_stale_while_revalidate"


def test_on_demand_worker_marks_refresh_success(monkeypatch) -> None:
    queue = FakeQueue()
    items: list[dict[str, object]] = []
    marks: list[dict[str, object]] = []
    monkeypatch.setattr(task_enqueue, "task_cancel_requested", lambda task_id: False)
    monkeypatch.setattr(task_enqueue, "upsert_task_item", lambda task_id, item_key, **kwargs: items.append({"task_id": task_id, "item_key": item_key, **kwargs}))
    monkeypatch.setattr(kol_pool, "enrich_item", lambda kol_pool_id, **kwargs: {"sync_status": "synced", "provider_status": "synced", "item": {"id": kol_pool_id}})
    monkeypatch.setattr(refresh_tier, "mark_kol_refreshed", lambda kol_pool_id, **kwargs: marks.append({"kol_pool_id": kol_pool_id, **kwargs}))

    asyncio.run(
        vkpi_tasks.process_vkpi_kol_pool_on_demand_refresh_job(
            queue,
            {
                "task_id": "task-success",
                "payload": {"kol_pool_id": 456, "max_posts": 1, "staff": {"id": 7}},
            },
        )
    )

    assert marks == [{"kol_pool_id": 456, "status": "synced"}]
    assert items[-1]["status"] == "done"
    assert queue.statuses[-1]["status"] == "done"
    assert queue.statuses[-1]["stage"] == "vkpi_kol_pool_on_demand_refresh"


def test_on_demand_worker_marks_refresh_error(monkeypatch) -> None:
    queue = FakeQueue()
    items: list[dict[str, object]] = []
    marks: list[dict[str, object]] = []
    monkeypatch.setattr(task_enqueue, "task_cancel_requested", lambda task_id: False)
    monkeypatch.setattr(task_enqueue, "upsert_task_item", lambda task_id, item_key, **kwargs: items.append({"task_id": task_id, "item_key": item_key, **kwargs}))
    monkeypatch.setattr(kol_pool, "enrich_item", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")))
    monkeypatch.setattr(refresh_tier, "mark_kol_refreshed", lambda kol_pool_id, **kwargs: marks.append({"kol_pool_id": kol_pool_id, **kwargs}))

    asyncio.run(
        vkpi_tasks.process_vkpi_kol_pool_on_demand_refresh_job(
            queue,
            {
                "task_id": "task-error",
                "payload": {"kol_pool_id": 789, "max_posts": 1, "staff": {"id": 7}},
            },
        )
    )

    assert marks == [{"kol_pool_id": 789, "status": "error"}]
    assert items[-1]["status"] == "failed"
    assert queue.statuses[-1]["status"] == "failed"
    assert "provider timeout" in queue.statuses[-1]["error_message"]
