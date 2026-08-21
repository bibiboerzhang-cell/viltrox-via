from __future__ import annotations

import asyncio
from contextlib import nullcontext
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_intel
from app.domains.kol import content_fit_enqueue
from app.domains.kol.my_kol_paid_action_access import FENCE_KEY, build_target_fence
from app.workers import apify_jobs_worker as pg_worker
from test_my_kol_video_tracking import _tracking_conn


OWNER = {
    "id": 10,
    "staff_id": 10,
    "user_id": 110,
    "role": "member",
    "permissions_json": '{"vkpi":"write"}',
}
SHARED = {
    "id": 20,
    "staff_id": 20,
    "user_id": 120,
    "role": "member",
    "permissions_json": '{"vkpi":"write"}',
}


def _post_handler() -> Any:
    return getattr(
        vkpi_kol_pool_intel.analyze_pool_item_content_fit,
        "__wrapped__",
        vkpi_kol_pool_intel.analyze_pool_item_content_fit,
    )


def test_shared_content_fit_post_is_denied_before_queue(monkeypatch) -> None:
    from app.db import connection

    conn = _tracking_conn()
    queued: list[str] = []
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(vkpi_kol_pool_intel, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        vkpi_kol_pool_intel,
        "_enqueue_content_fit_on_demand",
        lambda *_a, **_k: queued.append("queued"),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_post_handler()(1, {"force": True}, staff=SHARED))

    conn.close()
    assert raised.value.status_code == 403
    assert raised.value.detail == "my_kol_paid_action_write_forbidden"
    assert queued == []


def test_owner_content_fit_enqueue_persists_target_fence(monkeypatch) -> None:
    from app.db import connection

    conn = _tracking_conn()
    conn.execute("ALTER TABLE apify_jobs ADD COLUMN created_at TEXT")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(
        content_fit_enqueue,
        "_content_fit_ai_readiness",
        lambda: {
            "allowed": True,
            "gate_reason": "allowed",
            "model_readiness_status": "ready",
            "ai_analysis": {},
        },
    )

    def enqueue(_conn: Any, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        captured.update(kwargs)
        return {"id": 71, "status": "queued"}, True

    monkeypatch.setattr(content_fit_enqueue, "enqueue_active_apify_job", enqueue)
    result = content_fit_enqueue.enqueue_content_fit_on_demand(
        1,
        "AF-35-PRO",
        force=True,
        staff=OWNER,
        enforce_target_write=True,
    )

    conn.close()
    assert result["status"] == "queued"
    fence = captured["payload"][FENCE_KEY]
    assert fence["action"] == "content_fit_analysis"
    assert fence["kol_pool_id"] == 1
    assert fence["staff_id"] == 10


def test_content_fit_worker_blocks_revoked_favorite_with_zero_provider(monkeypatch) -> None:
    from app.db import connection

    conn = _tracking_conn()
    fence = build_target_fence(
        conn,
        action="content_fit_analysis",
        kol_pool_id=1,
        staff=OWNER,
    )
    conn.execute(
        "DELETE FROM vkpi_kol_pool_favorites WHERE kol_pool_id=1 AND staff_id=10"
    )
    conn.commit()
    blocked: list[tuple[Any, ...]] = []
    provider: list[str] = []
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(pg_worker, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(pg_worker, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(
        pg_worker,
        "_process_kol_content_fit_analysis",
        lambda *_args: provider.append("llm"),
    )

    pg_worker._process_job(
        None,
        {
            "id": 701,
            "job_type": "kol_content_fit_analysis",
            "payload": {
                "target_type": "kol",
                "target_id": "1",
                "kol_pool_id": 1,
                FENCE_KEY: fence,
            },
        },
    )

    conn.close()
    assert provider == []
    assert blocked[0][2] == "my_kol_paid_action_write_forbidden"
    assert blocked[0][3]["provider_calls_performed"] is False


def test_legacy_content_fit_job_without_fence_never_opens_db_or_provider(monkeypatch) -> None:
    blocked: list[tuple[Any, ...]] = []
    provider: list[str] = []
    monkeypatch.setattr(
        pg_worker,
        "db_connection_sync_scope",
        lambda: (_ for _ in ()).throw(AssertionError("must not open database")),
    )
    monkeypatch.setattr(pg_worker, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(
        pg_worker,
        "_process_kol_content_fit_analysis",
        lambda *_args: provider.append("llm"),
    )

    pg_worker._process_job(
        None,
        {"id": 702, "job_type": "kol_content_fit_analysis", "payload": {"kol_pool_id": 1}},
    )

    assert provider == []
    assert blocked[0][2] == "content_fit_authorization_fence_required"
