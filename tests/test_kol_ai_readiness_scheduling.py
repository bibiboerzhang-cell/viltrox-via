"""Fail-closed scheduling for optional AI stages in unified KOL input flows."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class _Rows:
    def __init__(self, row: Any) -> None:
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.row if isinstance(self.row, list) else []


class _VideoConn:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, _params=()):
        text = " ".join(str(sql).split())
        if "FROM vkpi_kol_video_evidence e" in text and "LEFT JOIN vkpi_kol_pool" in text:
            return _Rows(
                {
                    "evidence_id": 7,
                    "kol_pool_id": 88,
                    "content_url": "https://www.youtube.com/watch?v=abc123",
                    "evidence_platform": "youtube",
                    "title": "Existing video metadata",
                    "view_count": 1200,
                    "duration_seconds": 45,
                    "kol_handle": "creator",
                    "evidence_type": "video",
                    "media_kind": "video",
                    "viltrox_fit_score": 91,
                }
            )
        if "FROM vkpi_analysis_cache" in text:
            return _Rows(None)
        if "FROM apify_jobs" in text:
            return _Rows(None)
        if "SELECT viltrox_fit_score FROM vkpi_kol_pool" in text:
            return _Rows({"viltrox_fit_score": 91})
        raise AssertionError(text)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _blocked_preflight(*_args, **_kwargs) -> dict[str, Any]:
    return {
        "provider_calls_allowed": False,
        "provider_gate_reason": "model_binding_blocked",
        "model_readiness_status": "not_ready",
        "providers": [
            {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "provider_calls_allowed": False,
                "model_readiness_status": "not_ready",
                "estimated_cost_usd": 0.01,
                "checks": [],
            }
        ],
    }


def _allowed_preflight(*_args, **_kwargs) -> dict[str, Any]:
    return {
        "provider_calls_allowed": True,
        "provider_gate_reason": "provider_calls_allowed",
        "model_readiness_status": "production_ready",
        "providers": [
            {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "provider_calls_allowed": True,
                "model_readiness_status": "production_ready",
                "estimated_cost_usd": 0.01,
                "checks": [],
            }
        ],
    }


def test_video_url_keeps_metadata_but_does_not_queue_when_ai_is_disabled(monkeypatch) -> None:
    from app.domains.kol import search_sessions_attach, video_analysis_enqueue

    conn = _VideoConn()
    monkeypatch.setattr(video_analysis_enqueue.llm_gateway, "budget_preflight", _blocked_preflight)
    monkeypatch.setattr(
        video_analysis_enqueue,
        "enqueue_active_apify_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AI job must not be inserted")),
    )

    result = video_analysis_enqueue._enqueue_final_v1_video_analysis(
        conn,
        kol_pool_id=88,
        evidence_id=7,
        search_session_id=1086,
        search_session_item_id=2001,
    )

    assert result["status"] == "ai_disabled"
    assert result["state"] == "not_requested"
    assert result["write_db"] is False
    assert result["evidence"]["title"] == "Existing video metadata"
    assert result["ai_analysis"] == {
        "state": "not_requested",
        "reason": "ai_disabled",
        "gate_reason": "model_binding_blocked",
        "model_readiness_status": "not_ready",
        "provider_calls_allowed": False,
    }
    assert conn.commits == 0

    url_result = {
        "execute": True,
        "url_type": "video",
        "url": {"normalized": "https://www.youtube.com/watch?v=abc123"},
        "video_flow": {
            "status": "ai_disabled",
            "kol_pool_id": 88,
            "evidence_id": 7,
            "ai_analysis": result["ai_analysis"],
            "enqueue_result": result,
        },
    }
    assert search_sessions_attach._session_status_from_url_result(url_result) == "ready"
    item = search_sessions_attach._url_result_item(1086, url_result)
    assert item["status"] == "skipped"
    assert item["stage"] == "analysis"
    assert item["payload"]["ai_analysis"]["reason"] == "ai_disabled"


def test_video_url_still_queues_when_exact_model_is_production_ready(monkeypatch) -> None:
    from app.domains.kol import video_analysis_enqueue

    conn = _VideoConn()
    monkeypatch.setattr(video_analysis_enqueue.llm_gateway, "budget_preflight", _allowed_preflight)
    monkeypatch.setattr(
        video_analysis_enqueue,
        "enqueue_active_apify_job",
        lambda *_args, **kwargs: (
            {"id": 7001, "job_type": kwargs["job_type"], "status": "queued", "payload": kwargs["payload"]},
            True,
        ),
    )

    result = video_analysis_enqueue._enqueue_final_v1_video_analysis(
        conn,
        kol_pool_id=88,
        evidence_id=7,
        search_session_id=1086,
        search_session_item_id=2001,
    )

    assert result["status"] == "queued"
    assert result["ai_analysis"]["state"] == "queued"
    assert result["ai_analysis"]["provider_calls_allowed"] is True
    assert result["write_db"] is True
    assert conn.commits == 1


def test_account_url_finishes_profile_and_marks_representative_ai_not_requested(monkeypatch) -> None:
    from app.domains.kol import url_deep_crawl_execute_profile_videos as profile_videos

    metadata = {
        "platform": "youtube",
        "content_url": "https://www.youtube.com/watch?v=representative",
        "title": "Representative video",
    }
    monkeypatch.setattr(
        profile_videos,
        "_profile_representative_video_metadata",
        lambda *_args, **_kwargs: [metadata],
    )
    monkeypatch.setattr(
        profile_videos,
        "_filter_incremental_profile_videos",
        lambda videos, _state, *, limit: (videos[:limit], 0),
    )
    monkeypatch.setattr(
        profile_videos,
        "ensure_video_evidence_from_url",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status": "created",
            "evidence_id": 7,
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(
        profile_videos,
        "_enqueue_final_v1_video_analysis",
        lambda *_args, **_kwargs: {
            "status": "ai_disabled",
            "state": "not_requested",
            "reason": "ai_disabled",
            "ai_analysis": {
                "state": "not_requested",
                "reason": "ai_disabled",
                "gate_reason": "model_binding_blocked",
                "model_readiness_status": "not_ready",
                "provider_calls_allowed": False,
            },
            "viltrox_fit_score_changed_ids": [],
            "write_db": False,
        },
    )

    result = profile_videos._execute_profile_representative_video_analysis(
        object(),
        classified=SimpleNamespace(platform="youtube"),
        kol_pool_id=88,
        crawl={"status": "ok"},
        body={"mode": "account_deep", "representative_video_limit": 1},
        incremental_state={},
    )

    assert result["status"] == "completed"
    assert result["queued"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == 0
    assert result["worker_touched"] is False
    assert result["ai_analysis"]["state"] == "not_requested"
    assert result["ai_analysis"]["reason"] == "ai_disabled"
    assert result["items"][0]["status"] == "ai_disabled"


def test_text_search_skips_content_fit_job_when_ai_is_disabled(monkeypatch) -> None:
    from app.domains.kol import content_fit_enqueue

    session = {
        "id": 1089,
        "items": [
            {
                "id": 2201,
                "item_type": "recall_candidate",
                "kol_pool_id": 88,
                "payload": {"handle": "creator", "platform": "youtube"},
            }
        ],
    }
    monkeypatch.setattr(content_fit_enqueue.search_sessions, "get_session", lambda _sid: session)
    monkeypatch.setattr(content_fit_enqueue, "get_conn", lambda: object())
    monkeypatch.setattr(content_fit_enqueue, "_ids_with_video_evidence", lambda _conn, _ids: {88})
    monkeypatch.setattr(content_fit_enqueue, "_ids_with_existing_fit", lambda _conn, _ids: set())
    monkeypatch.setattr(content_fit_enqueue, "_already_queued_ids", lambda _conn, _sid, _ids: set())
    monkeypatch.setattr(
        content_fit_enqueue,
        "_exposure_potential",
        lambda _conn, kid, _fit: {"kol_pool_id": kid, "exposure_potential": 42},
    )
    monkeypatch.setattr(
        content_fit_enqueue,
        "_content_fit_ai_readiness",
        lambda: {
            "allowed": False,
            "gate_reason": "model_binding_blocked",
            "model_readiness_status": "not_ready",
            "ai_analysis": {
                "state": "not_requested",
                "reason": "ai_disabled",
                "gate_reason": "model_binding_blocked",
                "model_readiness_status": "not_ready",
                "provider_calls_allowed": False,
            },
        },
    )
    monkeypatch.setattr(
        content_fit_enqueue,
        "enqueue_active_apify_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("content-fit job must not be inserted")),
    )

    result = content_fit_enqueue.enqueue_content_fit_for_session(session_id=1089)

    assert result["status"] == "ai_disabled"
    assert result["state"] == "not_requested"
    assert result["enqueued_count"] == 0
    assert result["write_db"] is False
    assert result["skipped"] == [
        {
            "kol_pool_id": 88,
            "reason": "ai_disabled",
            "provider_gate_reason": "model_binding_blocked",
        }
    ]
    assert result["ai_analysis"]["reason"] == "ai_disabled"


def test_text_search_still_queues_content_fit_when_model_is_ready(monkeypatch) -> None:
    from app.domains.kol import content_fit_enqueue

    class _Conn:
        def __init__(self) -> None:
            self.commits = 0

        def commit(self) -> None:
            self.commits += 1

    conn = _Conn()
    session = {
        "id": 1089,
        "items": [
            {
                "id": 2201,
                "item_type": "recall_candidate",
                "kol_pool_id": 88,
                "payload": {"handle": "creator", "platform": "youtube"},
            }
        ],
    }
    monkeypatch.setattr(content_fit_enqueue.search_sessions, "get_session", lambda _sid: session)
    monkeypatch.setattr(content_fit_enqueue, "get_conn", lambda: conn)
    monkeypatch.setattr(content_fit_enqueue, "_ids_with_video_evidence", lambda _conn, _ids: {88})
    monkeypatch.setattr(content_fit_enqueue, "_ids_with_existing_fit", lambda _conn, _ids: set())
    monkeypatch.setattr(content_fit_enqueue, "_already_queued_ids", lambda _conn, _sid, _ids: set())
    monkeypatch.setattr(
        content_fit_enqueue,
        "_exposure_potential",
        lambda _conn, kid, _fit: {"kol_pool_id": kid, "exposure_potential": 42},
    )
    monkeypatch.setattr(
        content_fit_enqueue,
        "_content_fit_ai_readiness",
        lambda: {
            "allowed": True,
            "gate_reason": "provider_calls_allowed",
            "model_readiness_status": "production_ready",
            "ai_analysis": {
                "state": "queued",
                "reason": "analysis_authorized",
                "gate_reason": "provider_calls_allowed",
                "model_readiness_status": "production_ready",
                "provider_calls_allowed": True,
            },
        },
    )
    monkeypatch.setattr(
        content_fit_enqueue,
        "enqueue_active_apify_job",
        lambda *_args, **kwargs: (
            {"id": 8001, "status": "queued", "payload": kwargs["payload"]},
            True,
        ),
    )

    result = content_fit_enqueue.enqueue_content_fit_for_session(session_id=1089)

    assert result["status"] == "queued"
    assert result["enqueued"] == [{"kol_pool_id": 88, "job_id": 8001}]
    assert result["ai_analysis"]["state"] == "queued"
    assert result["ai_analysis"]["provider_calls_allowed"] is True
    assert result["write_db"] is True
    assert conn.commits == 1
