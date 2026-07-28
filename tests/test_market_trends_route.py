from __future__ import annotations

import asyncio
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_user_required
import app.api.dependencies.perms as perms_mod
from app.api.routers import intelligence_admin, vkpi_agents
from app.domains.market import market_observation
from app.services.jobs.processor import JOB_HANDLERS
from app.workers.tasks.market import process_market_trends_refresh_job


class _Queue:
    def __init__(self, task_id: str = "market-refresh-task") -> None:
        self.enqueued: list[dict] = []
        self.statuses: list[dict] = []
        self.task_id = task_id

    async def enqueue(self, job_type: str, payload: dict, **kwargs) -> str:
        self.enqueued.append({"job_type": job_type, "payload": payload, **kwargs})
        return self.task_id

    async def set_status(self, task_id: str, status: str, **extra) -> None:
        self.statuses.append({"task_id": task_id, "status": status, **extra})


def _manager_staff(permission: str = "write") -> dict:
    return {
        "id": 17,
        "staff_id": 17,
        "user_id": 71,
        "role": "manager",
        "is_owner": 0,
        "permissions": {"vkpi": permission},
    }


def _client(monkeypatch, staff: dict, queue: _Queue) -> TestClient:
    app = FastAPI()
    app.include_router(intelligence_admin.router)
    app.state.job_queue = queue
    app.dependency_overrides[get_user_required] = lambda: {
        "id": staff["user_id"],
        "role": staff["role"],
    }
    monkeypatch.setattr(perms_mod, "staff_context_for_user", lambda _user: staff)
    return TestClient(app, raise_server_exceptions=False)


def test_market_trends_get_is_zero_write(monkeypatch) -> None:
    monkeypatch.setattr(
        market_observation,
        "_observe_from_market_brain",
        lambda: [
            {
                "topic": "read-only",
                "kind": market_observation.KIND_HOT,
                "source": "test",
                "evidence_refs": [],
                "confidence": "high",
                "suggested_action": "",
            }
        ],
    )
    monkeypatch.setattr(market_observation, "_observe_from_competitor_radar", lambda: [])
    monkeypatch.setattr(market_observation, "_observe_from_bet_ledger", lambda: [])
    monkeypatch.setattr(
        market_observation,
        "_persist_observations",
        lambda _items: (_ for _ in ()).throw(AssertionError("GET must not write")),
    )

    result = intelligence_admin.market_trends(admin={"role": "admin"})

    assert result["count"] == 1
    assert result["write_db"] is False
    assert result["persisted"] == 0


def test_market_trends_history_remains_read_only(monkeypatch) -> None:
    monkeypatch.setattr(
        market_observation,
        "generate_observations",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("history must not synthesize")),
    )
    monkeypatch.setattr(
        market_observation,
        "list_observations_history",
        lambda *, kind, limit: {
            "status": "ok",
            "count": 0,
            "observations": [],
            "source": "history",
            "kind": kind,
            "limit": limit,
        },
    )

    result = intelligence_admin.market_trends(
        kind=market_observation.KIND_RISK,
        history=True,
        limit=23,
        admin={"role": "admin"},
    )

    assert result["source"] == "history"
    assert result["kind"] == market_observation.KIND_RISK
    assert result["limit"] == 23


def test_marketing_brain_daily_get_disables_expiry_write(monkeypatch) -> None:
    from app.domains.market import market_brain

    calls: list[dict] = []
    monkeypatch.setattr(
        market_brain,
        "build_daily_brief",
        lambda staff, **kwargs: calls.append({"staff": staff, **kwargs})
        or {"status": "ok"},
    )

    staff = {"id": 17, "role": "manager"}
    result = vkpi_agents.marketing_brain_daily(staff=staff)

    assert result["status"] == "ok"
    assert calls == [{"staff": staff, "sweep_expired": False}]


def test_marketing_brain_refresh_sweeps_once(monkeypatch) -> None:
    from app.domains.market import market_brain

    calls: list[dict] = []
    monkeypatch.setattr(market_brain, "mark_expired_signals", lambda: 4)
    monkeypatch.setattr(
        market_brain,
        "build_daily_brief",
        lambda staff, **kwargs: calls.append({"staff": staff, **kwargs})
        or {"status": "ok"},
    )

    staff = {"id": 17, "role": "manager"}
    result = vkpi_agents.marketing_brain_refresh(staff=staff)

    assert result["expired_swept"] == 4
    assert calls == [{"staff": staff, "sweep_expired": False}]


def test_refresh_post_requires_vkpi_write_permission(monkeypatch) -> None:
    queue = _Queue()
    response = _client(monkeypatch, _manager_staff("read"), queue).post(
        "/api/intelligence/market/trends/refresh"
    )

    assert response.status_code == 403
    assert queue.enqueued == []


def test_refresh_post_only_enqueues_for_manager_with_write(monkeypatch) -> None:
    queue = _Queue()
    monkeypatch.setattr(
        market_observation,
        "refresh_observations",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP request must not execute refresh inline")
        ),
    )

    response = _client(monkeypatch, _manager_staff("write"), queue).post(
        "/api/intelligence/market/trends/refresh"
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["request_write_mode"] == "queue_only"
    assert response.json()["queue_acceptance"] == "accepted_or_deduplicated"
    assert response.json()["business_write_deferred"] is True
    assert len(queue.enqueued) == 1
    queued = queue.enqueued[0]
    assert queued["job_type"] == "market_trends_refresh"
    assert queued["payload"]["staff_id"] == 17
    assert queued["payload"]["user_id"] == 71
    assert queued["lock_key"] == "market_trends_refresh:global"
    assert queued["timeout_seconds"] == 300


def test_refresh_worker_is_registered_and_persists(monkeypatch) -> None:
    queue = _Queue()
    scope_events: list[str] = []

    @contextmanager
    def bounded_scope():
        scope_events.append("open")
        try:
            yield
        finally:
            scope_events.append("close")

    monkeypatch.setattr(
        "app.workers.tasks.market.db_connection_sync_scope",
        bounded_scope,
    )
    monkeypatch.setattr(
        market_observation,
        "refresh_observations",
        lambda *, staff: {
            "status": "ok",
            "count": 2,
            "persisted": 2,
            "write_db": True,
            "observations": [],
            "staff_id": staff["id"],
        },
    )

    assert JOB_HANDLERS["market_trends_refresh"] is process_market_trends_refresh_job
    asyncio.run(
        process_market_trends_refresh_job(
            queue,
            {
                "task_id": "market-refresh-task",
                "job_type": "market_trends_refresh",
                "payload": {"staff": {"id": 17}},
            },
        )
    )

    assert queue.statuses[0]["status"] == "processing"
    assert queue.statuses[-1]["status"] == "done"
    assert queue.statuses[-1]["result_json"]["persisted"] == 2
    assert scope_events == ["open", "close"]


def test_refresh_post_rejects_empty_queue_receipt(monkeypatch) -> None:
    queue = _Queue(task_id="")

    response = _client(monkeypatch, _manager_staff("write"), queue).post(
        "/api/intelligence/market/trends/refresh"
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "durable job queue returned no job id"


def test_refresh_worker_rejects_missing_task_id() -> None:
    queue = _Queue()

    try:
        asyncio.run(
            process_market_trends_refresh_job(
                queue,
                {
                    "job_type": "market_trends_refresh",
                    "payload": {"staff": {"id": 17}},
                },
            )
        )
    except ValueError as exc:
        assert str(exc) == "task_id required"
    else:
        raise AssertionError("missing task_id must fail closed")
