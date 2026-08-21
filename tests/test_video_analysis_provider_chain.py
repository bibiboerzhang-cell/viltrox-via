"""Release gates for URL resolver -> final-v1 -> content-fit authorization."""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest

from app.domains.kol import provider_job_access as access


USER_ID = 34
STAFF_ID = 12
SESSION_ID = 55
VIDEO_URL = "https://www.youtube.com/watch?v=abcdefghijk"


def _staff(*, active: bool = True, permission: str = "write") -> dict[str, Any]:
    return {
        "id": STAFF_ID,
        "user_id": USER_ID,
        "active": active,
        "suspended_at": None,
        "role": "employee",
        "permissions_json": {"vkpi": permission},
        "user_status": "active",
    }


def _session() -> dict[str, Any]:
    return {
        "id": SESSION_ID,
        "query_text": VIDEO_URL,
        "query_type": "url_video",
        "status": "running",
        "created_by": USER_ID,
        "input_payload": {"product_sku": "AF-35-PRO"},
        "archived_at": None,
    }


def _session_row() -> dict[str, Any]:
    return {
        **_session(),
        "source": "test",
        "input_payload_json": {"product_sku": "AF-35-PRO"},
        "result_summary_json": {},
        "archived_by": None,
        "archive_reason": "",
        "created_at": "2026-08-21T00:00:00Z",
        "updated_at": "2026-08-21T00:00:00Z",
    }


def _evidence(**overrides: Any) -> dict[str, Any]:
    return {
        "evidence_id": 701,
        "kol_pool_id": 88,
        "content_url": VIDEO_URL,
        "platform": "youtube",
        "is_active": True,
        **overrides,
    }


def _item(**overrides: Any) -> dict[str, Any]:
    return {
        "id": 7,
        "session_id": SESSION_ID,
        "kol_pool_id": 88,
        "evidence_id": 701,
        "source_url": VIDEO_URL,
        **overrides,
    }


class _Rows:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.row) if self.row is not None else None


class _Conn:
    def __init__(
        self,
        *,
        actor: dict[str, Any] | None = None,
        session: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        item: dict[str, Any] | None = None,
    ) -> None:
        self.actor = _staff() if actor is None else actor
        self.session = _session_row() if session is None else session
        self.evidence = _evidence() if evidence is None else evidence
        self.item = _item() if item is None else item
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        compact = " ".join(str(sql).split())
        if "FROM staff s" in compact:
            return _Rows(self.actor)
        if "FROM vkpi_kol_search_sessions" in compact:
            return _Rows(self.session)
        if "FROM vkpi_kol_video_evidence" in compact:
            return _Rows(self.evidence)
        if "FROM vkpi_kol_search_session_items" in compact:
            if not self.item:
                return _Rows(None)
            if "WHERE id=? AND session_id=?" in compact:
                if int(params[0]) != int(self.item["id"]) or int(params[1]) != int(self.item["session_id"]):
                    return _Rows(None)
            if "WHERE session_id=? AND kol_pool_id=?" in compact:
                if int(params[0]) != int(self.item["session_id"]) or int(params[1]) != int(self.item["kol_pool_id"]):
                    return _Rows(None)
            return _Rows(self.item)
        if "FROM vkpi_analysis_cache" in compact or "FROM apify_jobs" in compact:
            return _Rows(None)
        if "SELECT viltrox_fit_score FROM vkpi_kol_pool" in compact:
            return _Rows({"viltrox_fit_score": 90})
        raise AssertionError(compact)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None


def _resolver_parent() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "url": VIDEO_URL,
        "source_url": VIDEO_URL,
        "platform": "youtube",
        "video_id": "abcdefghijk",
        "target_type": "video_url",
        "target_id": "youtube:abcdefghijk",
        "derive_method": "video_url_resolve_v1",
        "search_session_id": SESSION_ID,
        "max_posts": 3,
        "staff_id": STAFF_ID,
        "triggered_by_user_id": USER_ID,
    }
    payload[access.FENCE_KEY] = access.build_video_url_provider_fence(
        payload=payload,
        staff=_staff(),
    )
    # Late session attachment is allowed on the resolver, then sealed into the child.
    payload["search_session_item_id"] = 7
    return payload


def _child_base() -> dict[str, Any]:
    return {
        "target_type": "video",
        "target_id": "701",
        "derive_method": "video_analysis_final_v1",
        "kol_pool_id": 88,
        "source_url": VIDEO_URL,
        "search_session_id": SESSION_ID,
        "search_session_item_id": 7,
        "staff_id": STAFF_ID,
        "triggered_by_user_id": USER_ID,
    }


def _authorized_video(monkeypatch: pytest.MonkeyPatch, conn: _Conn | None = None) -> dict[str, Any]:
    from app.domains.kol import search_sessions, video_analysis_job_access

    monkeypatch.setattr(search_sessions, "get_session", lambda _sid: _session())
    return video_analysis_job_access.authorize_video_analysis_job(
        conn or _Conn(),
        _child_base(),
        evidence=_evidence(),
        source_payload=_resolver_parent(),
    )


def test_normal_url_parent_derives_exact_video_and_content_fit_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import connection
    from app.domains.kol import content_fit_job_access

    conn = _Conn()
    video = _authorized_video(monkeypatch, conn)
    fence = video[access.FENCE_KEY]
    assert fence["action"] == access.VIDEO_ANALYSIS
    assert fence["target"] == {
        "evidence_id": 701,
        "kol_pool_id": 88,
        "platform": "youtube",
        "video_id": "abcdefghijk",
        "normalized_url": VIDEO_URL,
        "search_session_item_id": 7,
    }
    assert video["product_sku"] == "AF-35-PRO"
    assert access.revalidate_provider_job_fence(
        conn, video, expected_action=access.VIDEO_ANALYSIS
    )["user_id"] == USER_ID

    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(connection, "db_connection_sync_scope", lambda: nullcontext())
    content = content_fit_job_access.authorize_content_fit_followup(
        {
            "target_type": "kol",
            "target_id": "88",
            "kol_pool_id": 88,
            "product_sku": "AF-35-PRO",
        },
        source_payload=video,
    )
    assert content["search_session_id"] == SESSION_ID
    assert content["search_session_item_id"] == 7
    assert content[access.FENCE_KEY]["action"] == access.CONTENT_FIT_ANALYSIS


def test_final_v1_enqueue_persists_derived_video_fence_and_scoped_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import search_sessions, video_analysis_enqueue

    conn = _Conn()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(search_sessions, "get_session", lambda _sid: _session())
    monkeypatch.setattr(
        video_analysis_enqueue.llm_gateway,
        "budget_preflight",
        lambda *_args, **_kwargs: {
            "provider_gate_reason": "allowed",
            "model_readiness_status": "production_ready",
            "providers": [{"provider": "google", "provider_calls_allowed": True}],
        },
    )
    monkeypatch.setattr(
        video_analysis_enqueue,
        "enqueue_active_apify_job",
        lambda _conn, **kwargs: captured.update(kwargs)
        or ({"id": 9701, "status": "queued", "payload": kwargs["payload"]}, True),
    )
    result = video_analysis_enqueue._enqueue_final_v1_video_analysis(
        conn,
        kol_pool_id=88,
        evidence_id=701,
        search_session_id=SESSION_ID,
        search_session_item_id=7,
        provider_parent_payload=_resolver_parent(),
    )
    assert result["status"] == "queued"
    assert captured["payload"][access.FENCE_KEY]["action"] == access.VIDEO_ANALYSIS
    assert captured["payload"]["product_sku"] == "AF-35-PRO"
    assert "user:34:session:55:item:7" not in captured["idempotency_key"]
    assert captured["idempotency_key"].startswith("apify:v1:video-final-v1:")


@pytest.mark.parametrize(
    ("conn", "expected"),
    [
        (_Conn(actor=_staff(active=False)), "provider_job_actor_inactive"),
        (
            _Conn(session={**_session_row(), "input_payload_json": {"product_sku": "AF-75-PRO"}}),
            "search_session_query_drifted",
        ),
        (_Conn(evidence=_evidence(kol_pool_id=99)), "video_analysis_evidence_drifted"),
        (
            _Conn(evidence=_evidence(content_url="https://www.youtube.com/watch?v=lmnopqrstuv")),
            "video_analysis_evidence_drifted",
        ),
        (_Conn(item=_item(session_id=99)), "video_analysis_session_item_drifted"),
        (_Conn(item=_item(kol_pool_id=99)), "video_analysis_session_item_drifted"),
    ],
)
def test_video_child_blocks_actor_evidence_kol_or_session_item_drift(
    monkeypatch: pytest.MonkeyPatch,
    conn: _Conn,
    expected: str,
) -> None:
    payload = _authorized_video(monkeypatch)
    with pytest.raises(access.ProviderJobAccessError) as raised:
        access.revalidate_provider_job_fence(
            conn,
            payload,
            expected_action=access.VIDEO_ANALYSIS,
        )
    assert raised.value.code == expected


def test_video_worker_revocation_blocks_before_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import connection
    from app.workers import apify_jobs_worker, apify_jobs_worker_runtime

    payload = _authorized_video(monkeypatch)
    blocked: list[tuple[Any, ...]] = []
    provider: list[str] = []
    monkeypatch.setattr(connection, "get_conn", lambda: _Conn(actor=_staff(active=False)))
    monkeypatch.setattr(apify_jobs_worker, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(apify_jobs_worker, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(
        apify_jobs_worker_runtime,
        "process_job_impl",
        lambda *_args: provider.append("gemini"),
    )

    apify_jobs_worker._process_job(
        None,
        {"id": 901, "job_type": "video", "payload": payload},
    )
    assert provider == []
    assert blocked[0][2] == "provider_job_actor_inactive"
    assert blocked[0][3]["provider_calls_performed"] is False


def test_missing_video_fence_fails_closed_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import apify_jobs_worker, apify_jobs_worker_runtime

    provider: list[str] = []
    blocked: list[tuple[Any, ...]] = []
    monkeypatch.setattr(apify_jobs_worker, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(
        apify_jobs_worker_runtime,
        "process_job_impl",
        lambda *_args: provider.append("gemini"),
    )
    apify_jobs_worker._process_job(
        None,
        {
            "id": 902,
            "job_type": "video",
            "payload": {**_child_base(), "search_session_item_id": 7},
        },
    )
    assert provider == []
    assert blocked[0][2] == "video_analysis_authorization_fence_required"


def test_post_provider_revocation_blocks_before_cost_cache_or_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import connection
    from app.workers import apify_jobs_worker_gemini as gemini

    payload = _authorized_video(monkeypatch)
    provider: list[str] = []
    blocked: list[tuple[Any, ...]] = []
    writes: list[str] = []
    monkeypatch.setattr(connection, "get_conn", lambda: _Conn(actor=_staff(active=False)))
    monkeypatch.setattr(gemini, "_load_video_evidence", lambda *_args: _evidence(id=701))
    monkeypatch.setattr(
        gemini,
        "_run_gemini_analyzer_with_timeout",
        lambda *_args, **_kwargs: provider.append("gemini") or {
            "analyzed": True,
            "model": gemini.WORKER_GEMINI_MODEL,
        },
    )
    monkeypatch.setattr(gemini, "_block_job", lambda *_args: blocked.append(_args))
    monkeypatch.setattr(gemini, "_record_gemini_cost", lambda **_kwargs: writes.append("cost"))

    gemini._process_gemini_video(
        object(),  # type: ignore[arg-type]
        {"id": 903},
        payload,
        0.01,
    )
    assert provider == ["gemini"]
    assert writes == []
    assert blocked[0][2] == "provider_job_actor_inactive"
    assert blocked[0][3]["provider_calls_performed"] is None


def test_server_owned_video_and_content_fit_require_explicit_session_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import connection
    from app.domains.kol import content_fit_job_access

    payload = {
        "target_type": "video",
        "target_id": "701",
        "derive_method": "video_analysis_final_v1",
        "kol_pool_id": 88,
        "source_url": VIDEO_URL,
        "search_session_id": 0,
        "product_sku": None,
    }
    capability = access.issue_server_owned_provider_capability(
        action=access.VIDEO_ANALYSIS,
        target_id="701",
        search_session_id=None,
    )
    payload[access.FENCE_KEY] = access.build_video_analysis_provider_fence(
        payload=payload,
        evidence=_evidence(),
        session=None,
        server_owned_capability=capability,
    )
    conn = _Conn()
    assert access.revalidate_provider_job_fence(
        conn, payload, expected_action=access.VIDEO_ANALYSIS
    )["server_owned"] is True
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(connection, "db_connection_sync_scope", lambda: nullcontext())
    content = content_fit_job_access.authorize_content_fit_followup(
        {"target_type": "kol", "target_id": "88", "kol_pool_id": 88},
        source_payload=payload,
    )
    assert content[access.FENCE_KEY]["mode"] == "server_owned"
    assert content[access.FENCE_KEY]["session"]["search_session_id"] == 0

    forged = {"signature": "forged"}
    with pytest.raises(access.ProviderJobAccessError) as raised:
        access.build_video_analysis_provider_fence(
            payload=payload,
            evidence=_evidence(),
            session=None,
            server_owned_capability=forged,  # type: ignore[arg-type]
        )
    assert raised.value.code == "provider_job_actor_required"
