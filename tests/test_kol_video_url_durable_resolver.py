"""Offline contracts for the durable unseen-video URL resolver."""
from __future__ import annotations

from typing import Any

import json

from app.domains.kol import url_deep_crawl
from app.domains.kol import video_url_resolver as resolver
from app.domains.tasks.search_session_lineage import search_session_lineages


class _CommitConn:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_enqueue_uses_dedicated_video_job_and_native_idempotency(monkeypatch) -> None:
    conn = _CommitConn()
    captured: dict[str, Any] = {}

    def fake_enqueue(_conn, **kwargs):
        captured.update(kwargs)
        return {"id": 9911, "status": "queued", "payload": kwargs["payload"]}, True

    monkeypatch.setattr(resolver, "get_conn", lambda: conn)
    monkeypatch.setattr(resolver, "enqueue_active_apify_job", fake_enqueue)

    result = resolver.enqueue_video_url_resolve_job(
        "https://www.youtube.com/watch?v=abcdefghijk&utm_source=test",
        staff={"id": 12, "user_id": 34},
        search_session_id=55,
    )

    payload = captured["payload"]
    assert result["job_type"] == "video_url_resolve"
    assert result["provider_calls_performed"] is False
    assert captured["job_type"] == "video_url_resolve"
    assert payload["target_type"] == "video_url"
    assert payload["target_id"] == "youtube:abcdefghijk"
    assert payload["search_session_id"] == 55
    assert payload["derive_method"] == "video_url_resolve_v1"
    assert [step["label"] for step in payload["video_url_resolution"]["steps"]] == [
        "解析视频",
        "识别作者",
        "缓存媒体",
        "AI分析",
    ]
    assert conn.commits == 1


def _plan() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return (
        {
            "status": "ready_to_execute",
            "operation": "video_creator_resolve",
            "provider_calls_performed": True,
            "creator_resolution_status": "resolved",
            "creator_identity": {
                "platform": "youtube",
                "channel_id": "UC123",
                "display_name": "Camera Creator",
            },
            "video_metadata": {
                "platform": "youtube",
                "content_url": "https://www.youtube.com/watch?v=abcdefghijk",
                "title": "Camera Review",
                "scrape_status": "success",
            },
        },
        [{"kol_pool_id": 88, "platform": "youtube", "handle": "camera"}],
    )


def test_worker_runner_resolves_base_then_leaves_ai_to_gated_final_job(monkeypatch) -> None:
    emitted: list[dict[str, Any]] = []
    execute_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(url_deep_crawl, "_match_pool", lambda _classified: [])
    monkeypatch.setattr(url_deep_crawl, "_video_flow_plan", lambda *_args: _plan())
    monkeypatch.setattr(url_deep_crawl, "_video_creator_resolved", lambda _flow: True)
    monkeypatch.setattr(
        url_deep_crawl,
        "_execute_existing_creator_video_flow",
        lambda _classified, _matches, flow, body: execute_calls.append(dict(body)) or {
            **flow,
            "status": "queued",
            "operation": "existing_creator_video_analysis",
            "kol_pool_id": 88,
            "evidence_id": 700,
            "cached_video_url": None,
            "ai_analysis": {"state": "queued", "reason": "production_ready"},
            "provider_calls_performed": False,
            "llm_calls_performed": False,
            "viltrox_fit_score_untouched": True,
        },
    )

    result = resolver.run_video_url_resolve_for_job(
        {
            "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "job_id": 9911,
            "search_session_id": 55,
            "search_session_item_id": 66,
            "video_url_resolution": resolver.initial_video_url_resolution_progress(),
        },
        progress_callback=lambda value: emitted.append(value),
    )

    assert result["status"] == "queued"
    assert result["evidence_id"] == 700
    assert result["llm_calls_performed"] is False
    assert result["resolution_progress"]["base_status"] == "ready"
    assert result["resolution_progress"]["status"] == "running"
    assert result["resolution_progress"]["steps"][-1]["status"] == "queued"
    assert execute_calls[0]["parent_job_id"] == 9911
    assert execute_calls[0]["search_session_item_id"] == 66
    assert execute_calls[0]["skip_profile_video_followups"] is True
    assert {value["current_step"] for value in emitted} == {
        "resolve_video",
        "identify_creator",
        "cache_media",
        "ai_analysis",
    }


def test_ai_off_keeps_base_evidence_ready_and_creates_no_fake_llm_state(monkeypatch) -> None:
    monkeypatch.setattr(url_deep_crawl, "_match_pool", lambda _classified: [])
    monkeypatch.setattr(url_deep_crawl, "_video_flow_plan", lambda *_args: _plan())
    monkeypatch.setattr(url_deep_crawl, "_video_creator_resolved", lambda _flow: True)
    monkeypatch.setattr(
        url_deep_crawl,
        "_execute_existing_creator_video_flow",
        lambda _classified, _matches, flow, _body: {
            **flow,
            "status": "ai_disabled",
            "operation": "existing_creator_video_analysis",
            "kol_pool_id": 88,
            "evidence_id": 701,
            "ai_analysis": {
                "state": "not_requested",
                "reason": "model_binding_blocked",
                "provider_calls_allowed": False,
            },
            "viltrox_fit_score_untouched": True,
        },
    )

    result = resolver.run_video_url_resolve_for_job(
        {"url": "https://www.youtube.com/watch?v=abcdefghijk"}
    )

    assert result["status"] == "ai_disabled"
    assert result["evidence_id"] == 701
    assert result["resolution_progress"]["status"] == "ready"
    assert result["resolution_progress"]["base_status"] == "ready"
    assert result["resolution_progress"]["steps"][-1]["status"] == "skipped"
    assert result["ai_analysis"]["provider_calls_allowed"] is False


def test_profile_provider_failure_uses_existing_retryable_media_category(monkeypatch) -> None:
    import pytest

    monkeypatch.setattr(url_deep_crawl, "_match_pool", lambda _classified: [])
    monkeypatch.setattr(url_deep_crawl, "_video_flow_plan", lambda *_args: _plan())
    monkeypatch.setattr(url_deep_crawl, "_video_creator_resolved", lambda _flow: True)
    monkeypatch.setattr(
        url_deep_crawl,
        "_execute_existing_creator_video_flow",
        lambda *_args: {"status": "profile_crawl_failed"},
    )

    with pytest.raises(RuntimeError, match="media_resolve_failed:profile_crawl_failed"):
        resolver.run_video_url_resolve_for_job(
            {"url": "https://www.youtube.com/watch?v=abcdefghijk"}
        )


def test_session_projection_and_lineage_are_resolver_specific() -> None:
    payload = {
        "search_session_lineage": [
            {"search_session_id": 55, "search_session_item_id": 66, "role": "resolver"}
        ],
        "video_url_resolution": {
            "status": "ready",
            "base_status": "ready",
            "current_step": "ai_analysis",
            "steps": [],
        },
        "video_url_resolve_result": {
            "kol_pool_id": 88,
            "evidence_id": 701,
            "creator_identity": {"handle": "camera"},
            "video_metadata": {"title": "Camera Review"},
            "video_flow": {
                "status": "ai_disabled",
                "operation": "existing_creator_video_analysis",
                "kol_pool_id": 88,
                "evidence_id": 701,
                "ai_analysis": {"state": "not_requested"},
            },
        },
    }

    projection = resolver.video_url_session_sync_projection(payload)

    assert search_session_lineages(payload)[0]["role"] == "resolver"
    assert projection["stage"] == "analysis"
    assert projection["base_status"] == "ready"
    assert projection["kol_pool_id"] == 88
    assert projection["evidence_id"] == 701
    assert projection["payload_patch"]["video_flow"]["status"] == "ai_disabled"


def test_resource_slot_and_queue_labels_are_specific() -> None:
    from app.domains.tasks.queue_view import _infer_kind, _infer_stage
    from app.workers.apify_job_resource_slots import resource_group_for_job

    job = {"job_type": "video_url_resolve", "payload": {"target_type": "video_url"}}
    assert resource_group_for_job(job) == "profile_media"
    assert _infer_kind("apify_jobs", "video_url_resolve", payload=job["payload"]) == "视频解析"
    assert _infer_stage("running", "视频解析", "video_url_resolve", payload=job["payload"]) == "search"


def test_worker_dispatches_resolver_without_falling_into_mock(monkeypatch) -> None:
    from app.workers import apify_jobs_worker as worker

    seen: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    monkeypatch.setattr(
        worker,
        "_process_video_url_resolve",
        lambda conn, job, payload: seen.append((conn, job, payload)),
    )
    conn = object()
    job = {"id": 9911, "job_type": "video_url_resolve", "payload": {"target_type": "video_url"}}

    worker._process_job(conn, job)

    assert seen == [(conn, job, job["payload"])]


class _Rows:
    def __init__(self, row: Any) -> None:
        self.row = row

    def fetchone(self):
        return self.row


class _LinkConn:
    def __init__(self) -> None:
        self.payload = {
            "derive_method": "video_url_resolve_v1",
            "target_type": "video_url",
        }
        self.commits = 0

    def execute(self, sql, params=()):
        compact = " ".join(str(sql).split())
        if compact.startswith("SELECT id, payload, status, last_error FROM apify_jobs"):
            return _Rows({"id": 9911, "payload": self.payload, "status": "running", "last_error": ""})
        if compact.startswith("UPDATE apify_jobs SET payload="):
            self.payload = json.loads(str(params[0]))
            return _Rows(None)
        if compact.startswith("SELECT id, status, last_error FROM apify_jobs"):
            return _Rows({"id": 9911, "status": "running", "last_error": ""})
        raise AssertionError(compact)

    def commit(self) -> None:
        self.commits += 1


def test_url_session_attach_marks_dedicated_job_as_resolver(monkeypatch) -> None:
    from app.domains.kol import search_sessions_attach

    conn = _LinkConn()
    monkeypatch.setattr(search_sessions_attach, "get_conn", lambda: conn)
    assert search_sessions_attach._link_job_payloads(
        55,
        [
            {
                "id": 66,
                "job_id": 9911,
                "item_type": "url_video",
                "status": "queued",
                "stage": "identified",
            }
        ],
    ) == 1
    assert search_session_lineages(conn.payload) == [
        {"search_session_id": 55, "search_session_item_id": 66, "role": "resolver"}
    ]


def test_ai_off_resolver_lineage_reduces_to_ready_base() -> None:
    from app.workers.apify_jobs_worker_lineage import _lineage_item_state

    progress = {
        "status": "ready",
        "base_status": "ready",
        "current_step": "ai_analysis",
        "steps": [],
    }
    state = _lineage_item_state(
        {"video_url_resolution": progress},
        [
            {
                "id": 9911,
                "role": "resolver",
                "status": "done",
                "updated_at": "2026-07-15T00:00:00Z",
                "payload": {"video_url_resolution": progress},
            }
        ],
    )
    assert state["item_status"] == "ready"
    assert state["stage"] == "summary"
    assert state["downstream"]["resolver"]["state"] == "ready"


def test_final_v1_terminal_state_closes_the_visible_ai_stage() -> None:
    progress = resolver.initial_video_url_resolution_progress()
    progress = resolver._progress(
        progress,
        "ai_analysis",
        "queued",
        overall="running",
        base_status="ready",
    )
    reconciled = resolver.reconcile_video_url_ai_progress(
        {"video_url_resolution": progress},
        {"video": {"state": "ready", "job_ids": [700]}},
    )
    assert reconciled["status"] == "ready"
    assert reconciled["steps"][-1]["status"] == "ready"
