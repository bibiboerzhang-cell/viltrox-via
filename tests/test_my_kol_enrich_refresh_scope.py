from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool, vkpi_kol_pool_jobs
from app.domains.discovery import apify_enrich
from app.domains.kol import pool as kol_pool
from app.domains.kol.my_kol_paid_action_access import (
    FENCE_KEY,
    MyKolPaidActionError,
    build_target_fence,
)
import app.domains.tasks.enqueue as task_enqueue
from app.workers.tasks import provider_workflows, vkpi as vkpi_tasks
from test_my_kol_video_tracking import _tracking_conn


OWNER = {"id": 10, "staff_id": 10, "user_id": 110, "role": "member", "permissions_json": '{"vkpi":"write"}'}
EMPLOYEE = {"id": 20, "staff_id": 20, "user_id": 120, "role": "member", "permissions_json": '{"vkpi":"write"}'}


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.statuses: list[dict[str, Any]] = []
        self.current = {"status": "queued"}

    async def enqueue(self, job_type: str, payload: dict[str, Any], **kwargs: Any) -> str:
        self.jobs.append({"job_type": job_type, "payload": payload, **kwargs})
        return "task-1"

    async def get_status(self, _task_id: str) -> dict[str, Any]:
        return self.current

    async def set_status(self, task_id: str, status: str, **kwargs: Any) -> None:
        self.statuses.append({"task_id": task_id, "status": status, **kwargs})
        self.current = {"status": status}


def _request(queue: FakeQueue) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_queue=queue)))


@pytest.fixture()
def tracking_conn():
    conn = _tracking_conn()
    yield conn
    conn.close()


def test_direct_apify_enrich_queues_only_after_target_fence(tracking_conn, monkeypatch) -> None:
    from app.db import connection

    queue = FakeQueue()
    monkeypatch.setattr(connection, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(vkpi_kol_pool, "release_validation_active", lambda: False)

    result = asyncio.run(
        vkpi_kol_pool.kol_pool_enrich_via_apify(_request(queue), 1, staff=OWNER)
    )

    assert result["status"] == "queued"
    assert len(queue.jobs) == 1
    payload = queue.jobs[0]["payload"]
    assert payload["kol_pool_id"] == 1
    assert payload[FENCE_KEY]["action"] == "kol_apify_enrich"
    assert "staff" not in payload


def test_apify_enrich_worker_authorization_failure_is_zero_provider(monkeypatch) -> None:
    queue = FakeQueue()
    provider_calls: list[str] = []
    monkeypatch.setattr(
        provider_workflows,
        "_require_paid_target_fence",
        lambda *_a, **_k: (_ for _ in ()).throw(
            MyKolPaidActionError("my_kol_paid_action_write_forbidden", 403)
        ),
    )
    monkeypatch.setattr(
        apify_enrich,
        "enrich_kol",
        lambda *_a, **_k: provider_calls.append("apify"),
    )

    asyncio.run(provider_workflows.process_kol_apify_enrich_job(
        queue,
        {"task_id": "apify-1", "payload": {"kol_pool_id": 1}},
    ))

    assert provider_calls == []
    assert queue.statuses[-1]["status"] == "failed"
    assert queue.statuses[-1]["stage"] == "authorization_blocked"
    assert "provider_calls_performed\": false" in queue.statuses[-1]["result_json"]


def test_refresh_enqueue_carries_target_fence(tracking_conn, monkeypatch) -> None:
    queue = FakeQueue()
    monkeypatch.setattr(task_enqueue, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(task_enqueue, "ensure_vkpi_task_schema", lambda: None)
    monkeypatch.setattr(
        kol_pool,
        "get_item",
        lambda kol_pool_id: {"item": {"id": kol_pool_id, "platform": "instagram", "handle": "creator"}},
    )

    result = asyncio.run(task_enqueue.enqueue_kol_pool_on_demand_refresh(
        queue,
        1,
        reason="manual_api_enrich",
        staff=OWNER,
        enforce_target_write=True,
    ))

    assert result["status"] == "queued"
    payload = queue.jobs[0]["payload"]
    assert payload[FENCE_KEY]["action"] == "kol_pool_refresh"
    assert payload[FENCE_KEY]["staff_id"] == 10


def test_direct_enrich_and_refresh_deny_shared_target_before_queue_or_marker(
    tracking_conn,
    monkeypatch,
) -> None:
    from app.db import connection
    import app.domains.sync.refresh_tier as refresh_tier

    queue = FakeQueue()
    monkeypatch.setattr(connection, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(task_enqueue, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(vkpi_kol_pool, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        refresh_tier,
        "record_kol_search",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not write marker")),
    )
    monkeypatch.setattr(
        kol_pool,
        "get_item",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not read detail")),
    )

    with pytest.raises(HTTPException) as enrich_error:
        asyncio.run(vkpi_kol_pool.enrich_pool_item.__wrapped__(
            _request(queue), 1, {}, staff=EMPLOYEE
        ))
    with pytest.raises(HTTPException) as refresh_error:
        asyncio.run(vkpi_kol_pool.refresh_pool_item(
            _request(queue), 1, {}, staff=EMPLOYEE
        ))

    assert enrich_error.value.status_code == 403
    assert refresh_error.value.status_code == 403
    assert queue.jobs == []


def test_refresh_worker_revocation_or_legacy_manual_job_is_zero_provider(monkeypatch) -> None:
    provider_calls: list[int] = []
    monkeypatch.setattr(task_enqueue, "task_cancel_requested", lambda _task_id: False)
    monkeypatch.setattr(
        vkpi_tasks.kol_pool,
        "enrich_item",
        lambda kol_pool_id, **_kwargs: provider_calls.append(kol_pool_id),
    )
    monkeypatch.setattr(
        vkpi_tasks,
        "_revalidate_kol_refresh_target",
        lambda _payload: (_ for _ in ()).throw(
            MyKolPaidActionError("my_kol_paid_action_permission_revoked", 403)
        ),
    )

    for payload in (
        {"kol_pool_id": 1, "reason": "manual_api_enrich", FENCE_KEY: {"version": 1}},
        {"kol_pool_id": 1, "reason": "manual_batch_enrich"},
    ):
        queue = FakeQueue()
        asyncio.run(vkpi_tasks.process_vkpi_kol_pool_on_demand_refresh_job(
            queue,
            {"task_id": "refresh-1", "payload": payload},
        ))
        assert queue.statuses[-1]["status"] == "failed"
        assert queue.statuses[-1]["stage"] == "authorization_blocked"

    assert provider_calls == []


def test_actual_refresh_fence_revalidation_detects_revoked_favorite(tracking_conn, monkeypatch) -> None:
    from app.db import connection

    fence = build_target_fence(
        tracking_conn,
        action="kol_pool_refresh",
        kol_pool_id=1,
        staff=OWNER,
    )
    tracking_conn.execute(
        "DELETE FROM vkpi_kol_pool_favorites WHERE kol_pool_id=1 AND staff_id=10"
    )
    tracking_conn.commit()
    monkeypatch.setattr(connection, "get_conn", lambda: tracking_conn)

    with pytest.raises(MyKolPaidActionError) as exc_info:
        vkpi_tasks._revalidate_kol_refresh_target({
            "kol_pool_id": 1,
            "reason": "manual_api_enrich",
            FENCE_KEY: fence,
        })

    assert exc_info.value.code == "my_kol_paid_action_write_forbidden"

    with pytest.raises(MyKolPaidActionError) as legacy_error:
        vkpi_tasks._revalidate_kol_refresh_target({
            "kol_pool_id": 1,
            "reason": "manual_batch_enrich",
        })
    assert legacy_error.value.code == "my_kol_paid_action_fence_required"
    assert vkpi_tasks._revalidate_kol_refresh_target({
        "kol_pool_id": 1,
        "reason": "daily_incremental_sync",
    }) is None


def test_batch_explicit_ids_are_all_authorized_before_first_enqueue(tracking_conn, monkeypatch) -> None:
    from app.db import connection

    queue = FakeQueue()
    monkeypatch.setattr(connection, "get_conn", lambda: tracking_conn)
    monkeypatch.setattr(vkpi_kol_pool_jobs, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        task_enqueue,
        "enqueue_kol_pool_on_demand_refresh",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not enqueue")),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(vkpi_kol_pool_jobs.batch_enrich_pool_items.__wrapped__(
            _request(queue),
            {"ids": [1, 2]},
            staff=OWNER,
        ))

    assert exc_info.value.status_code == 403
    assert queue.jobs == []


def test_batch_automatic_selection_and_promote_are_manager_only(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_kol_pool_jobs, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        kol_pool,
        "list_pool",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan global pool")),
    )
    with pytest.raises(HTTPException) as batch_error:
        asyncio.run(vkpi_kol_pool_jobs.batch_enrich_pool_items.__wrapped__(
            _request(FakeQueue()),
            {},
            staff=EMPLOYEE,
        ))
    assert batch_error.value.status_code == 403

    monkeypatch.setattr(
        vkpi_kol_pool.kol_pool,
        "promote_to_main",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not promote")),
    )
    with pytest.raises(HTTPException) as promote_error:
        vkpi_kol_pool.promote_to_main_kol(1, {}, staff=EMPLOYEE)
    assert promote_error.value.status_code == 403
