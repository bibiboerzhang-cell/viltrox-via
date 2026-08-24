"""Offline contracts for the durable unseen-video URL resolver."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
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
        search_session_item_id=66,
    )

    payload = captured["payload"]
    assert result["job_type"] == "video_url_resolve"
    assert result["provider_calls_performed"] is False
    assert captured["job_type"] == "video_url_resolve"
    assert payload["target_type"] == "video_url"
    assert payload["target_id"] == "youtube:abcdefghijk"
    assert payload["search_session_id"] == 55
    assert payload["search_session_item_id"] == 66
    assert search_session_lineages(payload) == [
        {
            "search_session_id": 55,
            "search_session_item_id": 66,
            "role": "resolver",
        }
    ]
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
    monkeypatch.setattr(url_deep_crawl, "_video_flow_plan", lambda *_args, **_kwargs: _plan())
    monkeypatch.setattr(url_deep_crawl, "_video_creator_resolved", lambda _flow: True)
    monkeypatch.setattr(
        url_deep_crawl,
        "_execute_existing_creator_video_flow",
        lambda _classified, _matches, flow, body, **_kwargs: execute_calls.append(dict(body)) or {
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

    parent_payload = {
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "job_id": 9911,
        "search_session_id": 55,
        "search_session_item_id": 66,
        "kol_provider_job_fence": {"action": "video_url_resolve"},
        "video_url_resolution": resolver.initial_video_url_resolution_progress(),
    }
    result = resolver.run_video_url_resolve_for_job(
        parent_payload,
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
    assert execute_calls[0]["provider_parent_payload"] is parent_payload
    assert execute_calls[0]["skip_profile_video_followups"] is True
    assert {value["current_step"] for value in emitted} == {
        "resolve_video",
        "identify_creator",
        "cache_media",
        "ai_analysis",
    }


def test_ai_off_keeps_base_evidence_ready_and_creates_no_fake_llm_state(monkeypatch) -> None:
    monkeypatch.setattr(url_deep_crawl, "_match_pool", lambda _classified: [])
    monkeypatch.setattr(url_deep_crawl, "_video_flow_plan", lambda *_args, **_kwargs: _plan())
    monkeypatch.setattr(url_deep_crawl, "_video_creator_resolved", lambda _flow: True)
    monkeypatch.setattr(
        url_deep_crawl,
        "_execute_existing_creator_video_flow",
        lambda _classified, _matches, flow, _body, **_kwargs: {
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


def test_official_channel_video_skips_enrollment_and_analysis(monkeypatch) -> None:
    """官方自有账号的视频:不建档、不深析,诚实终态而非失败/假排队。"""

    monkeypatch.setattr(url_deep_crawl, "_match_pool", lambda _classified: [])
    monkeypatch.setattr(
        url_deep_crawl,
        "_video_flow_plan",
        lambda *_args, **_kwargs: (_plan()[0], []),
    )
    monkeypatch.setattr(url_deep_crawl, "_video_creator_resolved", lambda _flow: True)
    monkeypatch.setattr(
        resolver,
        "find_official_channel_match",
        lambda _identity: {
            "id": 113,
            "platform": "youtube",
            "handle": "viltroxofficial",
            "display_name": "Viltrox Official",
        },
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError("official channel video must never reach creator build/analysis")

    monkeypatch.setattr(url_deep_crawl, "_execute_new_creator_video_flow", _boom)
    monkeypatch.setattr(url_deep_crawl, "_execute_existing_creator_video_flow", _boom)

    result = resolver.run_video_url_resolve_for_job(
        {"url": "https://www.youtube.com/watch?v=abcdefghijk"}
    )

    assert result["status"] == "official_channel_video"
    assert result["official_channel"]["handle"] == "viltroxofficial"
    assert result["resolution_progress"]["status"] == "ready"
    assert result["resolution_progress"]["base_status"] == "ready"
    steps = {step["key"]: step for step in result["resolution_progress"]["steps"]}
    assert steps["cache_media"]["status"] == "skipped"
    assert steps["ai_analysis"]["status"] == "skipped"
    assert steps["ai_analysis"]["reason"] == "official_channel_video"
    assert result["ai_analysis"]["provider_calls_allowed"] is False
    assert result["viltrox_fit_score_untouched"] is True
    # 会话项归约:跳过属正常完成,不得算失败/排队。
    from app.domains.kol.search_sessions_serde import _normalize_status

    assert _normalize_status("official_channel_video", item=True) == "skipped"
    assert _normalize_status("official_channel_video") == "ready"


def test_dry_run_intermediate_statuses_map_to_identified_not_unknown() -> None:
    """dry-run 中间态(供应商延迟/未入池)映射为已识别;unknown 会让历史回放误判已执行。"""

    from app.domains.kol.search_sessions_serde import _normalize_status

    assert _normalize_status("provider_refresh_pending", item=True) == "identified"
    assert _normalize_status("creator_not_in_pool", item=True) == "identified"


def test_profile_provider_failure_uses_existing_retryable_media_category(monkeypatch) -> None:
    import pytest

    monkeypatch.setattr(url_deep_crawl, "_match_pool", lambda _classified: [])
    monkeypatch.setattr(url_deep_crawl, "_video_flow_plan", lambda *_args, **_kwargs: _plan())
    monkeypatch.setattr(url_deep_crawl, "_video_creator_resolved", lambda _flow: True)
    monkeypatch.setattr(
        url_deep_crawl,
        "_execute_existing_creator_video_flow",
        lambda *_args, **_kwargs: {"status": "profile_crawl_failed"},
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


class _PayloadMergeCursor:
    def __init__(self, conn: "_PayloadMergeConn") -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        compact = " ".join(str(sql).split())
        self.conn.sql.append(compact)
        if "status='blocked'" in compact:
            serialized = params[1]
        elif "SET status=%s" in compact:
            serialized = params[2]
        else:
            serialized = params[0]
        self.conn.payload.update(json.loads(str(serialized)))


class _PayloadMergeConn:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        self.sql: list[str] = []

    @contextmanager
    def transaction(self):
        yield

    def cursor(self, *_args, **_kwargs):
        return _PayloadMergeCursor(self)


def _late_resolver_lineage() -> dict[str, Any]:
    return {
        "search_session_id": 55,
        "search_session_item_id": 66,
        "search_session_role": "resolver",
        "search_session_lineage": [
            {
                "search_session_id": 55,
                "search_session_item_id": 66,
                "role": "resolver",
            },
            {
                "search_session_id": 56,
                "search_session_item_id": 67,
                "role": "resolver",
            },
        ],
    }


def _stale_resolver_lineage() -> dict[str, Any]:
    return {
        "search_session_id": 55,
        "search_session_item_id": 66,
        "search_session_role": "resolver",
        "search_session_lineage": [
            {
                "search_session_id": 55,
                "search_session_item_id": 66,
                "role": "resolver",
            }
        ],
    }


def test_stale_claim_progress_merge_preserves_late_attached_lineage(monkeypatch) -> None:
    from app.workers import apify_jobs_worker_session
    from app.workers import apify_jobs_worker_video_url as video_handler

    conn = _PayloadMergeConn(_late_resolver_lineage())
    stale_claim = {
        "target_type": "video_url",
        "target_id": "youtube:abcdefghijk",
        **_stale_resolver_lineage(),
    }
    monkeypatch.setattr(apify_jobs_worker_session, "_sync_search_session_job", lambda *_a, **_k: True)

    video_handler._persist_progress(
        conn,
        job_id=9911,
        payload=stale_claim,
        progress=resolver.initial_video_url_resolution_progress(),
    )

    assert search_session_lineages(conn.payload) == search_session_lineages(
        _late_resolver_lineage()
    )
    assert conn.payload["video_url_resolution"]["status"] == "queued"
    assert "COALESCE(apify_jobs.payload, '{}'::jsonb) ||" in conn.sql[0]


def test_stale_claim_success_merge_preserves_late_attached_lineage(monkeypatch) -> None:
    from app.workers import apify_jobs_worker_handlers as handlers
    from app.workers import apify_jobs_worker_video_url as video_handler

    conn = _PayloadMergeConn(_late_resolver_lineage())
    stale_claim = {
        "target_type": "video_url",
        "target_id": "youtube:abcdefghijk",
        "video_url_resolution": resolver.initial_video_url_resolution_progress(),
        **_stale_resolver_lineage(),
    }
    monkeypatch.setattr(video_handler, "guard_provider_job_before_execution", lambda *_a, **_k: True)
    monkeypatch.setattr(video_handler, "db_connection_sync_scope", nullcontext)
    monkeypatch.setattr(handlers, "_resolve_job_staff", lambda *_a, **_k: {"id": 12, "user_id": 34})
    monkeypatch.setattr(
        video_handler,
        "run_video_url_resolve_for_job",
        lambda *_a, **_k: {
            "status": "official_channel_video",
            "resolution_progress": resolver.initial_video_url_resolution_progress(),
        },
    )

    video_handler._process_video_url_resolve(
        conn,
        {"id": 9911, "job_type": "video_url_resolve"},
        stale_claim,
    )

    assert search_session_lineages(conn.payload) == search_session_lineages(
        _late_resolver_lineage()
    )
    assert conn.payload["video_url_resolve_result"]["status"] == "official_channel_video"
    assert "COALESCE(apify_jobs.payload, '{}'::jsonb) ||" in conn.sql[-1]


def test_stale_claim_block_merge_preserves_late_attached_lineage() -> None:
    from app.domains.kol.provider_job_access import (
        ProviderJobAccessError,
        terminal_block_provider_job,
    )

    conn = _PayloadMergeConn(_late_resolver_lineage())
    stale_claim = {
        "target_type": "video_url",
        "target_id": "youtube:abcdefghijk",
        **_stale_resolver_lineage(),
    }

    terminal_block_provider_job(
        conn,
        job_id=9911,
        payload=stale_claim,
        error=ProviderJobAccessError("search_session_cancelled", 409),
    )

    assert search_session_lineages(conn.payload) == search_session_lineages(
        _late_resolver_lineage()
    )
    assert conn.payload["kol_provider_job_fence_result"]["status"] == "blocked"
    assert "COALESCE(apify_jobs.payload, '{}'::jsonb) ||" in conn.sql[-1]


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
