from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_jobs
from app.domains.access import scope as access_scope
from app.domains.kol import outreach_draft
from app.domains.kol.my_kol_paid_action_access import (
    FENCE_KEY,
    build_target_fence,
)
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


class _Result:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _OutreachConn:
    def __init__(self, *, favorite: bool = True):
        self.favorite = favorite
        self.inserted_payload: dict[str, Any] | None = None
        self.kol_context_reads = 0
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        normalized = " ".join(sql.lower().split())
        if "select id, duplicate_of_id from vkpi_kol_pool" in normalized:
            return _Result({"id": 1, "duplicate_of_id": None})
        if "from vkpi_kol_pool_favorites" in normalized:
            return _Result({"id": 1} if self.favorite else None)
        if "select id, handle, display_name from vkpi_kol_pool" in normalized:
            self.kol_context_reads += 1
            return _Result({"id": 1, "handle": "creator", "display_name": "Creator"})
        if "select id from apify_jobs" in normalized:
            return _Result(None)
        if "insert into apify_jobs" in normalized:
            self.inserted_payload = json.loads(str(params[1]))
            return _Result({"id": 77})
        raise AssertionError(f"unexpected SQL: {normalized}")

    def commit(self) -> None:
        self.commits += 1


def _direct_enqueue(body: dict[str, Any], staff: dict[str, Any]) -> dict[str, Any]:
    handler = getattr(
        vkpi_kol_pool_jobs.enqueue_kol_outreach_draft,
        "__wrapped__",
        vkpi_kol_pool_jobs.enqueue_kol_outreach_draft,
    )
    return handler(body, staff=staff)


def test_direct_outreach_enqueue_persists_actor_target_fence(monkeypatch) -> None:
    conn = _OutreachConn()
    monkeypatch.setattr(outreach_draft, "get_conn", lambda: conn)
    monkeypatch.setattr(vkpi_kol_pool_jobs, "release_validation_active", lambda: False)

    result = _direct_enqueue({"kol_pool_id": 1}, OWNER)

    assert result == {"status": "queued", "job_id": 77}
    assert conn.commits == 1
    assert conn.inserted_payload is not None
    fence = conn.inserted_payload[FENCE_KEY]
    assert fence["action"] == "outreach_draft"
    assert fence["kol_pool_id"] == 1
    assert fence["staff_id"] == 10


def test_shared_outreach_enqueue_denied_before_kol_context_or_insert(monkeypatch) -> None:
    conn = _OutreachConn(favorite=False)
    monkeypatch.setattr(outreach_draft, "get_conn", lambda: conn)
    monkeypatch.setattr(vkpi_kol_pool_jobs, "release_validation_active", lambda: False)

    with pytest.raises(HTTPException) as exc_info:
        _direct_enqueue({"kol_pool_id": 1}, SHARED)

    assert exc_info.value.status_code == 403
    assert conn.kol_context_reads == 0
    assert conn.inserted_payload is None


def test_outreach_project_scope_checked_before_target_or_enqueue(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_kol_pool_jobs, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        access_scope,
        "assert_project_access",
        lambda *_a, **_k: (_ for _ in ()).throw(access_scope.ScopeDenied("denied")),
    )
    monkeypatch.setattr(
        outreach_draft,
        "enqueue_outreach_draft_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not enqueue")),
    )

    with pytest.raises(HTTPException) as exc_info:
        _direct_enqueue({"kol_pool_id": 1, "project_id": 91}, OWNER)

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    ("body", "expected_detail"),
    [
        ({"kol_pool_id": "not-an-id"}, "invalid kol_pool_id or project_id"),
        ({"kol_pool_id": 1, "project_id": "not-an-id"}, "invalid kol_pool_id or project_id"),
    ],
)
def test_outreach_enqueue_invalid_ids_are_stable_400(
    monkeypatch,
    body: dict[str, Any],
    expected_detail: str,
) -> None:
    monkeypatch.setattr(vkpi_kol_pool_jobs, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        outreach_draft,
        "enqueue_outreach_draft_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not enqueue")),
    )

    with pytest.raises(HTTPException) as exc_info:
        _direct_enqueue(body, OWNER)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == expected_detail


def test_outreach_worker_requires_fence_without_database_or_provider(monkeypatch) -> None:
    blocked: list[tuple[Any, ...]] = []
    provider_calls: list[str] = []
    monkeypatch.setattr(
        pg_worker,
        "db_connection_sync_scope",
        lambda: (_ for _ in ()).throw(AssertionError("must not open database")),
    )
    monkeypatch.setattr(pg_worker, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(
        pg_worker,
        "_process_kol_outreach_draft",
        lambda *_args: provider_calls.append("llm"),
    )

    pg_worker._process_job(
        None,
        {"id": 801, "job_type": "kol_outreach_draft", "payload": {"kol_pool_id": 1}},
    )

    assert provider_calls == []
    assert blocked[0][2] == "my_kol_paid_action_fence_required"
    assert blocked[0][3]["provider_calls_performed"] is False


def test_outreach_worker_rechecks_target_and_project_before_provider(monkeypatch) -> None:
    from app.db import connection

    conn = _tracking_conn()
    fence = build_target_fence(
        conn,
        action="outreach_draft",
        kol_pool_id=1,
        staff=OWNER,
    )
    payload = {"kol_pool_id": 1, "project_id": 91, FENCE_KEY: fence}
    blocked: list[tuple[Any, ...]] = []
    provider_calls: list[str] = []
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(pg_worker, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(pg_worker, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(
        pg_worker,
        "_process_kol_outreach_draft",
        lambda *_args: provider_calls.append("llm"),
    )
    monkeypatch.setattr(
        access_scope,
        "assert_project_access",
        lambda *_a, **_k: (_ for _ in ()).throw(access_scope.ScopeDenied("denied")),
    )

    pg_worker._process_job(
        None,
        {"id": 802, "job_type": "kol_outreach_draft", "payload": payload},
    )

    conn.close()
    assert provider_calls == []
    assert blocked[0][2] == "project_scope_denied"
    assert blocked[0][3]["provider_calls_performed"] is False


def test_outreach_optimize_scopes_and_redacts_all_prompt_fields(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(vkpi_kol_pool_jobs, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        vkpi_kol_pool_jobs,
        "assert_paid_target_writable",
        lambda kol_pool_id, staff: captured.update({"target": kol_pool_id, "staff": staff}),
    )

    def invoke(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "openai",
            "model": "test-model",
            "text": json.dumps({"subject": "Safe subject", "body": "Safe body"}),
        }

    from app.platform import llm_gateway

    monkeypatch.setattr(llm_gateway, "invoke", invoke)
    result = vkpi_kol_pool_jobs.optimize_kol_outreach(
        {
            "kol_pool_id": 1,
            "subject": "mail leak1@example.test",
            "body": "WhatsApp +1 415 555 0199",
            "product": "mailto:leak2@example.test",
            "kol_name": "Creator https://wa.me/447700900123",
        },
        staff=OWNER,
    )

    assert result["ok"] is True
    assert captured["target"] == 1
    prompt = captured["prompt"].lower()
    for secret in ("leak1@example.test", "415 555 0199", "leak2@example.test", "447700900123"):
        assert secret not in prompt
    assert "[contact removed]" in prompt


def test_outreach_optimize_invalid_id_is_400_before_scope_or_provider(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_kol_pool_jobs, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        vkpi_kol_pool_jobs,
        "assert_paid_target_writable",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not authorize")),
    )
    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_pool_jobs.optimize_kol_outreach(
            {"kol_pool_id": "bad", "body": "hello"},
            staff=OWNER,
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid kol_pool_id"
