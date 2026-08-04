from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool, vkpi_kol_pool_search
from app.domains.kol import (
    profile_discovery,
    profile_discovery_queue,
    profile_recall,
    search_sessions,
    smart_query_planner,
)


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> "_FakeCursor":
        self.executed.append((sql, params))
        return _FakeCursor(
            {
                "id": 987,
                "job_type": "smart_search_profile_advance",
                "status": "queued",
                "created_at": "2026-06-08T00:00:00Z",
                "updated_at": "2026-06-08T00:00:00Z",
            }
        )

    def commit(self) -> None:
        self.commits += 1


class _FakeCursor:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any]:
        return self.row


def test_enqueue_smart_search_profile_advance_records_session_job_without_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn()
    summary_updates: list[dict[str, Any]] = []

    # 重构后 enqueue_smart_search_profile_advance 搬到 profile_discovery_queue;get_conn 在该模块命名空间读取。
    monkeypatch.setattr(profile_discovery_queue, "get_conn", lambda: conn)
    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: {"id": 123, "status": "planned"},
    )
    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "update_session_result_summary",
        lambda session_id, **kwargs: summary_updates.append({"session_id": session_id, **kwargs}),
    )
    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "get_session",
        lambda session_id: {"id": session_id, "status": "running", "items": []},
    )

    result = profile_discovery.enqueue_smart_search_profile_advance(
        query_text="找适合闪光灯的 KOL",
        body={},
        staff={"id": 42},
    )

    assert result["status"] == "queued"
    assert result["provider_calls_performed"] is False
    assert result["write_db"] is True
    assert result["viltrox_fit_score_changed_ids"] == []
    assert result["viltrox_fit_score_untouched"] is True
    assert result["writes"] == ["vkpi_kol_search_sessions", "apify_jobs"]

    assert conn.commits == 1
    assert len(conn.executed) == 1
    payload = json.loads(conn.executed[0][1][0])
    assert payload["derive_method"] == "kol_smart_search_profile_advance"
    assert payload["search_session_id"] == 123
    assert payload["query_text"] == "找适合闪光灯的 KOL"
    assert payload["creator_quota"] == 15
    assert payload["reviewer_quota"] == 15
    assert payload["include_new_discovery"] is True
    assert payload["new_discovery_limit"] == 15
    assert payload["advance_limit"] == 15
    assert payload["max_posts"] == 12
    assert payload["advance_mode"] == "account_deep"
    assert payload["item_types"] == ["new_creator", "existing_kol", "recall_candidate"]
    assert payload["viltrox_fit_score_untouched"] is True

    assert summary_updates[0]["session_id"] == 123
    job_summary = summary_updates[0]["summary_patch"]["smart_search_profile_advance_job"]
    assert job_summary["status"] == "queued"
    assert job_summary["advance_limit"] == 15
    assert job_summary["advance_mode"] == "account_deep"
    assert job_summary["viltrox_fit_score_untouched"] is True


def test_smart_search_profile_advance_job_queues_pipeline_instead_of_calling_recall(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"enqueue": 0, "recall": 0}

    def fake_enqueue(*, query_text: str, body: dict[str, Any], staff: dict[str, Any]) -> dict[str, Any]:
        called["enqueue"] += 1
        assert query_text == "找适合闪光灯的 KOL"
        assert body["input"] == "找适合闪光灯的 KOL"
        assert staff["id"] == 42
        return {
            "status": "queued",
            "session_id": 123,
            "search_session": {"id": 123, "status": "running"},
            "job": {"id": 987, "status": "queued"},
            "writes": ["vkpi_kol_search_sessions", "apify_jobs"],
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    def fail_recall(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        called["recall"] += 1
        raise AssertionError("queue_pipeline=true must not call recall synchronously")

    # 重构后 smart_kol_search_profile_advance_job 搬到 vkpi_kol_pool_search;在其命名空间读 kol_profile_discovery/recall。
    monkeypatch.setattr(vkpi_kol_pool_search.kol_profile_discovery, "enqueue_smart_search_profile_advance", fake_enqueue)
    monkeypatch.setattr(vkpi_kol_pool_search.kol_profile_recall, "recall_kol_profiles", fail_recall)

    result = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search_profile_advance_job(
            {"input": "找适合闪光灯的 KOL"},
            staff={"id": 42},
        )
    )

    assert result["status"] == "queued"
    assert result["branch"] == "kol_recall_profile_advance_pipeline"
    assert result["provider_calls"] is False
    assert result["write_db"] is True
    assert result["viltrox_fit_score_changed_ids"] == []
    assert result["viltrox_fit_score_untouched"] is True
    assert called == {"enqueue": 1, "recall": 0}


def test_smart_text_search_persists_session_before_provider_free_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def ensure_session(**kwargs: Any) -> dict[str, Any]:
        events.append("session")
        assert kwargs["create"] is True
        assert kwargs["query_text"] == "camera reviewer"
        return {"id": 321, "status": "planned"}

    def initial_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        assert events == ["session"]
        events.append("provider_free_plan")
        return {
            "status": "fallback",
            "search_query": "camera reviewer",
            "provider_calls_performed": False,
        }

    def initial_recall(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["provider_free"] is True
        events.append("provider_free_recall")
        return {
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0, "provider_free_initial": True},
        }

    def attach_session(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["body"]["session_id"] == 321
        assert kwargs["body"]["create_session"] is False
        events.append("attach")
        return {**kwargs["result"], "search_session": {"id": 321, "status": "partial"}}

    monkeypatch.setattr(vkpi_kol_pool_search.kol_search_sessions, "ensure_session_for_result", ensure_session)
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("request-side LLM planner must not run")
        ),
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        initial_plan,
    )
    monkeypatch.setattr(vkpi_kol_pool_search.kol_profile_recall, "recall_kol_profiles", initial_recall)
    monkeypatch.setattr(vkpi_kol_pool_search, "_attach_smart_recall_session", attach_session)

    result = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {"input": "camera reviewer", "create_session": True},
            staff={"id": 42},
        )
    )

    assert events == ["session", "provider_free_plan", "provider_free_recall", "attach"]
    assert result["provider_calls"] is False
    assert result["search_session"]["id"] == 321


def test_provider_free_preview_skips_embedding_and_llm_rerank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        profile_recall,
        "_embed_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider-free preview must not call embeddings")
        ),
    )
    monkeypatch.setattr(
        profile_recall,
        "_llm_rerank_buckets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider-free preview must not call LLM rerank")
        ),
    )
    monkeypatch.setattr(profile_recall, "_pool_text_fallback_hits", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})

    result = profile_recall.recall_kol_profiles(
        query_text="camera reviewer",
        provider_free=True,
    )

    assert result["items"] == []
    assert result["diagnostics"]["provider_free_initial"] is True
    assert result["diagnostics"]["recall_mode"] == "provider_free_pool_text"
    assert result["diagnostics"]["display_rerank"].startswith("provider_free_initial")


def test_worker_runs_full_planner_after_request_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        smart_query_planner,
        "plan_text_query",
        lambda query, **_kwargs: calls.append(query)
        or {
            "status": "needs_clarification",
            "search_query": "",
            "provider_calls_performed": False,
        },
    )
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: {},
    )

    result = asyncio.run(
        profile_discovery.execute_smart_search_profile_advance_pipeline(
            session_id=321,
            payload={"query_text": "camera reviewer"},
        )
    )

    assert calls == ["camera reviewer"]
    assert result["status"] == "needs_clarification"
    assert result["session_id"] == 321


def test_profile_crawl_execute_defaults_to_queue_with_pending_enrichment(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_checks: list[tuple[int, int, bool]] = []

    def current_staff_session(session_id: int, *, staff: dict[str, Any], scope_to_staff: bool) -> dict[str, Any]:
        owner_checks.append((int(session_id), int(staff["id"]), bool(scope_to_staff)))
        return {"id": int(session_id), "created_by": int(staff["id"]), "items": []}

    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_search_sessions,
        "get_session",
        current_staff_session,
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_discovery,
        "enqueue_search_session_advance",
        lambda **_kwargs: {"status": "queued", "job": {"id": 88}, "writes": ["apify_jobs"]},
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_discovery,
        "execute_profile_crawl_for_session_item",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("sync profile crawl must not run")),
    )

    result = vkpi_kol_pool_search.execute_kol_search_session_item_profile_crawl(
        12,
        34,
        {"execute": True},
        staff={"id": 42},
    )

    assert result["status"] == "queued"
    assert result["deferred_to_queue"] is True
    assert result["provider_calls_performed"] is False
    assert result["enrichment"]["contacts"]["status"] == "pending"
    assert result["enrichment"]["audience"]["status"] == "pending"
    assert owner_checks == [(12, 42, True)]


def test_smart_text_search_with_no_candidates_is_empty_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {"status": "ready", "search_query": "camera reviewer"},
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: {
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0},
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    result = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {"input": "camera reviewer", "create_session": False},
            staff={"id": 42},
        )
    )

    assert result["status"] == "empty"
    assert result["result"]["items"] == []


def test_smart_text_search_applies_explicit_platform_filter_to_local_recall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {"status": "ready", "search_query": "camera reviewer"},
    )
    youtube = {"id": 1, "platform": "youtube", "handle": "yt-reviewer"}
    instagram = {"id": 2, "platform": "instagram", "handle": "ig-reviewer"}
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: {
            "items": [youtube, instagram],
            "buckets": {"creator": [youtube, instagram], "reviewer": []},
            "diagnostics": {"returned_count": 2, "creator_returned": 2, "reviewer_returned": 0},
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    result = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {"input": "camera reviewer", "platforms": ["instagram"], "create_session": False},
            staff={"id": 42},
        )
    )

    assert result["status"] == "ready"
    assert [item["id"] for item in result["result"]["items"]] == [2]
    assert result["result"]["platform_filter"]["filtered_out"] == 1
    assert result["result"]["diagnostics"]["returned_count"] == 1
    assert result["result"]["diagnostics"]["creator_returned"] == 1
    assert result["result"]["diagnostics"]["reviewer_returned"] == 0


def test_empty_recall_attachment_rewrites_session_ready_to_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(
        search_sessions,
        "_attach_recall_result",
        lambda _session_id, _result: {"id": 55, "status": "ready", "items": []},
    )
    monkeypatch.setattr(
        search_sessions,
        "update_session_result_summary",
        lambda session_id, **kwargs: updates.append({"session_id": session_id, **kwargs}) or {"status": kwargs["status"]},
    )

    result = search_sessions.attach_recall_result(
        55,
        {"items": [], "buckets": {"creator": [], "reviewer": []}},
    )

    assert result["status"] == "partial"
    assert updates[0]["summary_patch"]["result_state"] == "empty"


def test_nothing_to_queue_marks_session_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(
        profile_discovery,
        "advance_search_session_items",
        lambda **_kwargs: {"status": "planned", "selected": 0, "eligible": 0},
    )
    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "update_session_result_summary",
        lambda session_id, **kwargs: updates.append({"session_id": session_id, **kwargs}) or {},
    )

    result = profile_discovery_queue.enqueue_search_session_advance(
        session_id=91,
        body={},
        staff={"id": 42},
    )

    assert result["status"] == "nothing_to_queue"
    assert updates[0]["status"] == "partial"


def test_split_profile_advance_pipeline_uses_public_monkeypatch_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    recall_result = {
        "status": "ready",
        "method": "test",
        "items": [{"id": 1, "platform": "instagram"}],
        "buckets": {"creator": [], "reviewer": []},
        "diagnostics": {"returned_count": 1},
    }

    monkeypatch.setattr(
        profile_discovery.profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: calls.append("recall") or recall_result,
    )
    monkeypatch.setattr(
        profile_discovery,
        "filter_recall_result_platforms",
        lambda result, _platforms: calls.append("filter") or result,
    )
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "attach_recall_result",
        lambda _session_id, _result: calls.append("attach_recall") or {"id": 71},
    )

    async def _discover(**_kwargs: Any) -> dict[str, Any]:
        calls.append("discover")
        return {"status": "ready", "items": [{"handle": "creator"}]}

    monkeypatch.setattr(profile_discovery, "discover_new_creators", _discover)
    monkeypatch.setattr(
        profile_discovery,
        "_annotate_new_priority",
        lambda result: calls.append("annotate") or result,
    )
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "attach_new_discovery_result",
        lambda _session_id, _result: calls.append("attach_discovery") or {},
    )
    monkeypatch.setattr(
        profile_discovery,
        "advance_search_session_items",
        lambda **_kwargs: calls.append("advance")
        or {"status": "partial", "counts": {}, "viltrox_fit_score_changed_ids": []},
    )
    monkeypatch.setattr(
        profile_discovery,
        "_profile_advance_pipeline_status",
        lambda *_args: calls.append("status") or "partial",
    )
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: calls.append("update") or {},
    )

    result = asyncio.run(
        profile_discovery.execute_smart_search_profile_advance_pipeline(
            session_id=71,
            payload={
                "query_text": "camera reviewer",
                "product_focus": {"sku": "test"},
                "new_discovery_platforms": ["instagram"],
                "include_content_fit": False,
                "include_lazy_video_backfill": False,
            },
        )
    )

    assert result["status"] == "partial"
    assert calls == [
        "recall",
        "filter",
        "attach_recall",
        "discover",
        "annotate",
        "attach_discovery",
        "advance",
        "status",
        "update",
    ]


def test_attach_new_discovery_dedupes_same_handle_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    """重复卡修(sky_vanya 案):同批内同平台同 handle(大小写差异)只落一条会话项。"""
    from app.domains.kol import search_sessions_attach

    captured: dict[str, Any] = {}

    def fake_record_items(session_id: int, items: list[dict[str, Any]], *, status: str, summary: dict[str, Any]) -> dict[str, Any]:
        captured["items"] = items
        return {"id": session_id, "items": items, "status": status}

    monkeypatch.setattr(search_sessions, "record_items", fake_record_items)
    monkeypatch.setattr(search_sessions, "get_session", lambda _sid: {"result_summary": {}})

    search_sessions_attach.attach_new_discovery_result(
        411,
        {
            "status": "ready",
            "platforms": ["tiktok"],
            "existing_matches": [],
            "new_creators": [
                {"handle": "sky_vanya", "platform": "tiktok", "channel_url": "https://www.tiktok.com/@sky_vanya"},
                {"handle": "other_creator", "platform": "tiktok", "channel_url": "https://www.tiktok.com/@other_creator"},
                # 多路检索变体重复(大小写差异)→ 必须收敛,不再写出第二行/第二张卡。
                {"handle": "Sky_Vanya", "platform": "tiktok", "channel_url": "https://www.tiktok.com/@sky_vanya"},
            ],
            "counts": {},
        },
    )

    new_items = [item for item in captured["items"] if item["item_type"] == "new_creator"]
    assert [item["payload"]["handle"] for item in new_items] == ["sky_vanya", "other_creator"]
