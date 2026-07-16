from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol import discovery_filters, product_resolver, profile_discovery, smart_query_planner
from app.services.intelligence import account_scan_service


def test_split_evo_request_is_recognized_as_explicit_unresolved_product(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {
            "products": [
                {"sku": "AF-35-EVO", "model_name": "AF 35mm F1.8 EVO", "series": "EVO", "mount": "FE-mount"},
                {"sku": "AF-55-EVO", "model_name": "AF 55mm F1.8 EVO", "series": "EVO", "mount": "FE-mount"},
            ]
        },
    )

    result = product_resolver.unresolved_product_request("找一些适合26 e vo")

    assert result is not None
    assert result["requested_series"] == "EVO"
    assert result["requested_focals"] == [26]
    assert [item["sku"] for item in result["suggestions"]] == ["AF-35-EVO", "AF-55-EVO"]


def test_planner_stops_before_llm_for_explicit_unresolved_product(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.analysis import cache_repo

    seen_cache_methods: list[str] = []
    monkeypatch.setattr(
        cache_repo,
        "get_analysis_cache_entry",
        lambda _target_type, _target_id, *, derive_method: seen_cache_methods.append(derive_method) or None,
    )
    monkeypatch.setattr(smart_query_planner.product_resolver, "resolve_product", lambda _query: None)
    monkeypatch.setattr(
        smart_query_planner.product_resolver,
        "unresolved_product_request",
        lambda _query: {"reason": "explicit_product_not_in_catalog", "message": "choose product", "suggestions": []},
    )
    monkeypatch.setattr(
        smart_query_planner.llm_gateway,
        "invoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
    )

    plan = smart_query_planner.plan_text_query("找一些适合26 e vo")

    assert plan["status"] == "needs_clarification"
    assert plan["search_query"] == ""
    assert plan["provider_calls_performed"] is False
    assert plan["include_new_discovery"] is False
    assert seen_cache_methods == [smart_query_planner.PLAN_DERIVE_METHOD]


def test_brand_name_and_retailer_identity_are_filtered() -> None:
    assert profile_discovery._is_own_brand_account({"handle": "UCDVTU", "channel_name": "VILTROX Photography"})
    assert discovery_filters._is_discovery_garbage({"handle": "focuscenter", "channel_name": "Focus Camera Store"})
    assert not discovery_filters._is_discovery_garbage({"handle": "constantine_photo", "channel_name": "Constantine Photography"})


def test_tiktok_long_planner_query_is_split_without_expanding_result_budget() -> None:
    queries = account_scan_service._short_search_queries(
        "Viltrox 28mm f1.8 FE, Sony E-mount street photographer, travel videographer, compact prime lens review"
    )

    assert queries == [
        "Viltrox 28mm f1.8 FE",
        "Sony E-mount street photographer",
        "travel videographer",
        "compact prime lens review",
    ]
    per_query = max(3, (20 + len(queries) - 1) // len(queries))
    assert per_query * len(queries) == 20


def test_profile_advance_runs_free_contact_extract_before_session_update(monkeypatch: pytest.MonkeyPatch) -> None:
    call_order: list[str] = []
    monkeypatch.setattr(
        profile_discovery,
        "profile_crawl_plan_for_session_item",
        lambda **_kwargs: {"profile_url": "https://youtube.com/@creator", "mode": "account_deep", "max_posts": 3},
    )
    monkeypatch.setattr(
        profile_discovery.url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: {"status": "ready", "profile_flow": {"status": "ready", "kol_pool_id": 42}},
    )

    from app.domains.kol import business_contact_extract

    monkeypatch.setattr(
        business_contact_extract,
        "enrich_contacts_l0",
        lambda kol_pool_id: call_order.append(f"contact:{kol_pool_id}") or {"status": "ok", "email": "public@example.com"},
    )
    monkeypatch.setattr(
        profile_discovery,
        "_enqueue_audience_enrichment",
        lambda kol_pool_id, **_lineage: {"status": "pending", "async": True, "kol_pool_id": kol_pool_id, "job_id": 99},
    )

    def _update(_session_id: int, _item_id: int, *, profile_result: dict[str, Any]) -> dict[str, Any]:
        call_order.append("session_update")
        assert profile_result["contact_enrichment"]["email"] == "public@example.com"
        assert profile_result["audience_enrichment"]["status"] == "pending"
        return {"id": _item_id}

    monkeypatch.setattr(profile_discovery.search_sessions, "update_item_profile_execution", _update)

    result = profile_discovery.execute_profile_crawl_for_session_item(
        session_id=7,
        item_id=8,
        body={"execute": True, "mode": "account_deep", "max_posts": 3},
    )

    assert call_order == ["contact:42", "session_update"]
    assert result["status"] == "partial"
    assert result["contact_enrichment"]["email"] == "public@example.com"
    assert result["audience_enrichment"]["status"] == "pending"


def test_discovery_drops_cross_platform_provider_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(platform: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        assert platform == "instagram"
        return {
            "status": "done",
            "items": [
                {
                    "platform": "instagram",
                    "handle": "wrong-platform",
                    "channel_url": "https://youtube.com/@wrong-platform",
                    "channel_name": "Camera lens reviewer",
                    "sample_title": "35mm camera review",
                    "views": 5000,
                },
                {
                    "handle": "right-platform",
                    "channel_url": "https://instagram.com/right-platform/",
                    "channel_name": "Camera lens reviewer",
                    "sample_title": "35mm camera review",
                    "views": 4000,
                },
            ],
        }

    monkeypatch.setattr(profile_discovery, "search_platform_content", fake_search)
    monkeypatch.setattr(
        profile_discovery.history_match,
        "annotate_platform_items",
        lambda items, *, platform: items,
    )
    monkeypatch.setattr(profile_discovery, "_auto_enroll_discoveries", lambda _items: 0)

    result = asyncio.run(
        profile_discovery.discover_new_creators(
            query_text="35mm camera reviewer",
            platforms=["instagram"],
            limit=10,
        )
    )

    assert [item["handle"] for item in result["new_creators"]] == ["right-platform"]
    assert {item["platform"] for item in result["items"]} == {"instagram"}
    assert result["platform_results"][0]["filtered_platform_mismatch"] == 1


def test_explicit_unsupported_discovery_platform_does_not_expand_to_defaults() -> None:
    result = asyncio.run(
        profile_discovery.discover_new_creators(
            query_text="camera reviewer",
            platforms=["linkedin"],
        )
    )

    assert result["status"] == "invalid_platform"
    assert result["platforms"] == []
    assert result["provider_calls"] is False


def test_empty_profile_advance_is_partial_and_updates_session_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(profile_discovery.search_sessions, "get_session", lambda _session_id: {"items": []})
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "update_session_result_summary",
        lambda session_id, **kwargs: updates.append({"session_id": session_id, **kwargs}) or {},
    )

    result = profile_discovery.advance_search_session_items(
        session_id=77,
        body={"execute": True, "limit": 5},
    )

    assert result["selected"] == 0
    assert result["status"] == "partial"
    assert updates[0]["status"] == "partial"


def test_profile_advance_persists_each_item_before_batch_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[dict[str, Any]] = []
    session_items = [
        {
            "id": item_id,
            "item_type": "recall_candidate",
            "status": "pending",
            "rank": item_id,
            "payload": {
                "handle": f"creator-{item_id}",
                "profile_url": f"https://www.youtube.com/@creator-{item_id}",
            },
        }
        for item_id in (1, 2, 3)
    ]
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "get_session",
        lambda _session_id: {"items": session_items},
    )
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "update_session_result_summary",
        lambda session_id, **kwargs: updates.append({"session_id": session_id, **kwargs}) or {},
    )

    def fake_execute(*, session_id: int, item_id: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
        assert session_id == 901
        assert body and body["execute"] is True
        if item_id == 2:
            raise RuntimeError("provider failed")
        if item_id == 3:
            return {
                "status": "partial",
                "profile_status": "partial",
                "viltrox_fit_score_changed_ids": [],
            }
        return {
            "status": "ready",
            "profile_status": "ready",
            "viltrox_fit_score_changed_ids": [],
        }

    monkeypatch.setattr(profile_discovery, "execute_profile_crawl_for_session_item", fake_execute)

    result = profile_discovery.advance_search_session_items(
        session_id=901,
        body={
            "execute": True,
            "limit": 3,
            "mode": "account_deep",
            "_pipeline_running": True,
        },
    )

    assert result["selected"] == 3
    assert result["status"] == "partial"
    assert len(updates) == 4
    checkpoints = updates[:3]
    assert [update["status"] for update in checkpoints] == ["running", "running", "running"]
    assert [update["summary_patch"]["progress"]["profile_completed"] for update in checkpoints] == [1, 2, 3]
    assert [update["summary_patch"]["progress"]["profile_succeeded"] for update in checkpoints] == [1, 1, 2]
    assert [update["summary_patch"]["progress"]["profile_failed"] for update in checkpoints] == [0, 1, 1]
    assert [update["summary_patch"]["progress"]["current_item"]["item_id"] for update in checkpoints] == [1, 2, 3]
    for update in updates:
        progress = update["summary_patch"]["progress"]
        assert progress["stage_timing"]["stage_started_at"]
        assert progress["stage_timing"]["stage_updated_at"]
        assert progress["base_complete"] is True
        assert progress["requested_tasks_terminal"] is False
        assert progress["required_tasks_complete"] is False
        assert progress["complete"] is False
        assert progress["full_analysis_complete"] is False
        assert progress["decision_eligible"] is False

    final_progress = updates[-1]["summary_patch"]["progress"]
    assert updates[-1]["status"] == "running"
    assert final_progress["profile_completed"] == 3
    assert final_progress["profile_remaining"] == 0
    assert final_progress["stage_timing"]["stage_finished_at"]


def test_pipeline_status_requires_candidates_and_complete_advance() -> None:
    assert profile_discovery._profile_advance_pipeline_status(
        {"items": []},
        {"status": "empty", "items": []},
        {"status": "partial", "selected": 0},
    ) == "partial"
    assert profile_discovery._profile_advance_pipeline_status(
        {"items": [{"id": 1}]},
        {"status": "partial", "items": []},
        {"status": "ready", "selected": 1},
    ) == "partial"
