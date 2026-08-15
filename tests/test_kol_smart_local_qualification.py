from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import (
    profile_discovery_pipeline,
    profile_recall,
    profile_recall_qualification,
)


def _row(
    item_id: int,
    *,
    handle: str | None = None,
    followers: Any = 5_000,
    country: str | None = "US",
    platform: str = "youtube",
    profile_type: str = "creator",
    raw_platform_data: Any = None,
) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": handle or f"lens-reviewer-{item_id}",
        "display_name": f"Lens Reviewer {item_id}",
        "platform": platform,
        "profile_url": f"https://example.test/{item_id}",
        "followers": followers,
        "country": country,
        "language": "en",
        "profile_type": profile_type,
        "creator_type_score": 90 if profile_type == "creator" else 10,
        "reviewer_type_score": 90 if profile_type == "reviewer" else 10,
        "profile_text": "Independent 35mm low-light portrait lens review",
        "type_reason": "fixture",
        "bio": "Independent 35mm low-light portrait lens review",
        "primary_topic": "portrait photography",
        "content_style": "review",
        "raw_platform_data": raw_platform_data or {},
    }


def _evidence(age_days: int) -> dict[str, Any]:
    posted_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {
        "representative_evidence": [
            {
                "title": "Lens review field test",
                "content_url": "https://example.test/video",
            }
        ],
        "latest_real_video": {
            "posted_at": posted_at.isoformat(),
            "evidence_type": "video",
            "source": "vkpi_kol_video_evidence.posted_at",
        },
    }


_PRIVATE_MARKER = "SMART_LOCAL_PRIVATE_MARKER@example.test +1-202-555-0199"


def _leaking_smart_item() -> dict[str, Any]:
    match = {
        "field": "bio",
        "term": "lens",
        "source": "server_profile_evidence",
        "value": _PRIVATE_MARKER,
    }
    private_match = {
        "field": "bio",
        "term": _PRIVATE_MARKER,
        "source": "server_profile_evidence",
    }
    gate = {
        "schema": "smart_local_gate_evidence_v1",
        "passed": True,
        "market": {"value": "us", "passed": True},
        "platform": {"value": "youtube", "passed": True},
        "relevance": {"passed": True, "evidence": [match, private_match]},
    }
    return {
        "kol_pool_id": 1,
        "handle": "safe-handle",
        "display_name": "Safe Creator",
        "platform": "youtube",
        "profile_url": "https://example.test/safe-handle",
        "followers": 10_000,
        "bio": f"lens review {_PRIVATE_MARKER}",
        "profile_text": _PRIVATE_MARKER,
        "raw_platform_data": {"business_email": _PRIVATE_MARKER},
        "email": _PRIVATE_MARKER,
        "other_contacts_json": json.dumps({"phone": _PRIVATE_MARKER}),
        "contact_channels": [{"value": _PRIVATE_MARKER}],
        "bucket": "creator",
        "match_evidence": [match, private_match],
        "why_fit": _PRIVATE_MARKER,
        "candidate_facets": {
            "platform": "youtube",
            "country": "us",
            "language": "en",
            "profile_type": "creator",
            "contact_available": "yes",
            "video_evidence": "yes",
        },
        "qualification_evidence": gate,
    }


def _leaking_smart_result() -> dict[str, Any]:
    item = _leaking_smart_item()
    gate = item["qualification_evidence"]
    return {
        "method": "test",
        "items": [item],
        "buckets": {"creator": [item], "reviewer": []},
        "diagnostics": {"returned_count": 1, "evidence_gate_enabled": True},
        "local_qualification": {
            "schema": "smart_local_qualified_v1",
            "returned_count": 1,
            "shortfall": 29,
            "gate_evidence": [gate],
            "rejected_evidence_sample": [],
        },
    }


def _install_recall(
    monkeypatch: pytest.MonkeyPatch,
    rows: dict[int, dict[str, Any]],
    evidence: dict[int, dict[str, Any]],
) -> None:
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **_kwargs: ("35mm low-light portrait", {"query_profile": "", "query_text_provided": True}),
    )
    monkeypatch.setattr(
        profile_recall,
        "_pool_text_fallback_hits",
        lambda *_args, **_kwargs: [
            profile_recall.RecallHit(item_id, 1.0 - item_id / 10_000, f"point-{item_id}")
            for item_id in rows
        ],
    )
    monkeypatch.setattr(
        profile_recall,
        "_entry_rows",
        lambda ids: {item_id: dict(rows[item_id]) for item_id in ids if item_id in rows},
    )
    monkeypatch.setattr(
        profile_recall,
        "_evidence_summaries",
        lambda ids: {item_id: dict(evidence[item_id]) for item_id in ids if item_id in evidence},
    )
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})


def test_smart_local_returns_30_and_soft_quota_fills_from_other_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {item_id: _row(item_id, profile_type="creator") for item_id in range(1, 36)}
    evidence = {
        item_id: _evidence(40 if item_id == 1 else 10)
        for item_id in rows
    }
    _install_recall(monkeypatch, rows, evidence)

    result = profile_recall.recall_kol_profiles(
        query_text="35mm low-light portrait",
        provider_free=True,
        candidate_limit=1,
        limit=1,
        creator_quota=15,
        reviewer_quota=15,
        allow_backfill=True,
        local_qualification_policy=profile_recall_qualification.smart_local_policy(
            market="US",
            platforms=["youtube"],
        ),
    )

    assert len(result["items"]) == 30
    assert len(result["buckets"]["creator"]) == 30
    assert result["buckets"]["reviewer"] == []
    assert result["query"]["candidate_limit"] == 500
    assert result["query"]["limit"] == 30
    assert result["query"]["allow_backfill"] is False
    contract = result["local_qualification"]
    assert contract["status"] == "ready"
    assert contract["shortfall"] == 0
    assert contract["ratio_policy"]["unused_quota_backfilled"] == 15
    assert contract["funnel"]["returned"] == 30
    assert contract["stage_timing"]["total_ms"] >= 0
    assert contract["total_ms"] == contract["stage_timing"]["total_ms"]
    ages = [item["qualification_evidence"]["activity"]["age_days"] for item in result["items"]]
    assert ages == sorted(ages)


def test_smart_local_gates_are_before_limit_and_shortfall_is_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strong_us = {
        "market_inference": {
            "value": "US",
            "confidence": 0.94,
            "strength": "strong",
            "source": "profile_annotation",
        }
    }
    weak_us = {
        "market_inference": {
            "value": "US",
            "confidence": 0.79,
            "strength": "strong",
            "source": "profile_annotation",
        }
    }
    rows = {
        1: _row(1),
        2: _row(2, country=None, raw_platform_data=strong_us),
        3: _row(3, followers=2_999),
        4: _row(4),
        5: _row(5, country=None),
        6: _row(6, country=None, raw_platform_data=weak_us),
        7: _row(7, country="GB"),
        8: _row(8, platform="instagram"),
        9: _row(9, handle="lens-reviewer-1"),
        10: _row(10, followers=None),
        11: _row(11),
    }
    evidence = {item_id: _evidence(10) for item_id in rows}
    evidence[4] = _evidence(46)
    evidence[11] = {}
    _install_recall(monkeypatch, rows, evidence)

    result = profile_recall.recall_kol_profiles(
        query_text="35mm low-light portrait",
        provider_free=True,
        creator_quota=15,
        reviewer_quota=15,
        local_qualification_policy=profile_recall_qualification.smart_local_policy(
            market="US",
            platforms=["youtube"],
        ),
    )

    assert [item["kol_pool_id"] for item in result["items"]] == [1, 2]
    contract = result["local_qualification"]
    assert contract["status"] == "shortfall"
    assert contract["qualified_count"] == 2
    assert contract["returned_count"] == 2
    assert contract["shortfall"] == 28
    assert contract["shortfall_reason"] == "qualified_candidates_exhausted"
    assert contract["rejected_by_reason"] == {
        "followers_below_3000": 1,
        "followers_unknown": 1,
        "latest_video_stale": 1,
        "latest_video_unknown": 1,
        "market_unknown": 2,
        "market_mismatch": 1,
        "platform_mismatch": 1,
        "duplicate_canonical_identity": 1,
    }
    inferred = result["items"][1]["qualification_evidence"]["market"]
    assert inferred == {
        "value": "us",
        "target": "us",
        "method": "strong_annotated_inference",
        "confidence": 0.94,
        "source": "profile_annotation",
        "passed": True,
    }
    assert contract["funnel"] == {
        "evidence_relevant": 11,
        "canonical_unique": 10,
        "followers_pass": 8,
        "fresh_video_pass": 6,
        "market_pass": 3,
        "platform_pass": 2,
        "qualified": 2,
        "returned": 2,
    }


def test_ordinary_recall_remains_legacy_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = {1: _row(1)}
    _install_recall(monkeypatch, rows, {1: {}})

    result = profile_recall.recall_kol_profiles(
        query_text="35mm low-light portrait",
        provider_free=True,
        candidate_limit=1,
        limit=1,
        creator_quota=1,
        reviewer_quota=0,
    )

    assert result["query"]["candidate_limit"] == 1
    assert result["query"]["limit"] == 1
    assert result["query"]["allow_backfill"] is True
    assert "local_qualification" not in result


def test_smart_local_engine_projection_removes_private_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(1)
    row.update({
        "bio": f"35mm low-light portrait {_PRIVATE_MARKER}",
        "profile_text": f"35mm low-light portrait {_PRIVATE_MARKER}",
        "email": _PRIVATE_MARKER,
        "other_contacts_json": json.dumps({"phone": _PRIVATE_MARKER}),
        "raw_platform_data": {"business_email": _PRIVATE_MARKER},
    })
    _install_recall(monkeypatch, {1: row}, {1: _evidence(10)})

    result = profile_recall.recall_kol_profiles(
        query_text="35mm low-light portrait",
        provider_free=True,
        creator_quota=1,
        reviewer_quota=0,
        local_qualification_policy=profile_recall_qualification.smart_local_policy(
            market="US",
            platforms=["youtube"],
        ),
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert _PRIVATE_MARKER not in serialized
    assert result["local_qualification"]["schema"] == "smart_local_qualified_v1"
    assert result["items"][0]["match_evidence"]
    assert result["items"][0]["why_fit"]
    assert result["items"][0]["candidate_facets"]["contact_available"] == "yes"


def test_legacy_kol_recall_route_preserves_existing_bio_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = {"items": [{"kol_pool_id": 1, "bio": _PRIVATE_MARKER}]}
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: legacy,
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: None,
    )

    result = vkpi_kol_pool_search.recall_kol_profiles(
        query_text="lens review",
        product_sku="",
        candidate_limit=1,
        limit=1,
        creator_quota=1,
        reviewer_quota=0,
        ratio_policy="soft",
        mixed_policy="dominant",
        dedupe=True,
        vector_weight=0.85,
        type_weight=0.15,
        type_boost_enabled=True,
        exclude_chinese=True,
        session_id=None,
        create_session=False,
        staff={"id": 42},
    )

    assert result["items"][0]["bio"] == _PRIVATE_MARKER


def test_smart_preview_response_strips_raw_contact_values_and_explanation_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "search_query": "lens review",
            "resolved_product": {},
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: _leaking_smart_result(),
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    response = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {
                "input": "US YouTube lens review",
                "create_session": False,
            },
            staff={"id": 42},
        )
    )

    serialized = json.dumps(response, ensure_ascii=False)
    item = response["result"]["items"][0]
    assert _PRIVATE_MARKER not in serialized
    assert not {"bio", "profile_text", "raw_platform_data", "email", "other_contacts_json", "contact_channels"}.intersection(item)
    assert item["match_evidence"] == [{
        "field": "bio", "term": "lens", "source": "server_profile_evidence",
    }]
    assert item["why_fit"] == "bio 命中 lens"
    assert item["candidate_facets"]["contact_available"] == "yes"


def test_smart_preview_owns_30_target_and_filter_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "search_query": "lens review",
            "resolved_product": {},
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        lambda **kwargs: captured.update(kwargs) or {
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0, "evidence_gate_enabled": True},
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {
                "input": "US YouTube lens review",
                "limit": 1,
                "candidate_limit": 2,
                "create_session": False,
            },
            staff={"id": 42},
        )
    )

    assert captured["limit"] == 30
    assert captured["candidate_limit"] == 500
    assert captured["allow_backfill"] is False
    assert captured["dedupe"] is True
    assert captured["local_qualification_policy"] == profile_recall_qualification.smart_local_policy(
        market="us",
        platforms=["youtube"],
    )


def test_smart_worker_owns_same_local_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    advance_call: dict[str, Any] = {}
    local_contract = {
        "status": "shortfall",
        "returned_count": 0,
        "shortfall": 30,
        "stage_timing": {"total_ms": 1.25},
    }
    monkeypatch.setattr(
        profile_discovery_pipeline.profile_recall,
        "recall_kol_profiles",
        lambda **kwargs: captured.update(kwargs) or {
            "method": "test",
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0},
            "local_qualification": local_contract,
        },
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "filter_recall_result_platforms",
        lambda result, _value: result,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "filter_recall_result_market",
        lambda result, _value: result,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "attach_recall_result",
        lambda _session_id, _result: {"id": 77},
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "advance_search_session_items",
        lambda **kwargs: advance_call.update(kwargs) or {
            "status": "empty",
            "selected": 0,
            "counts": {},
            "items": [],
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "_profile_advance_pipeline_status",
        lambda *_args: "partial",
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: {},
    )

    result = asyncio.run(
        profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=77,
            payload={
                "query_text": "US YouTube 35mm portrait creator",
                "_worker_planned": True,
                "limit": 1,
                "candidate_limit": 2,
                "advance_limit": 30,
                "_smart_local_30_contract": True,
                "include_new_discovery": False,
                "include_content_fit": False,
                "include_lazy_video_backfill": False,
            },
        )
    )

    assert captured["limit"] == 30
    assert captured["candidate_limit"] == 500
    assert captured["allow_backfill"] is False
    assert captured["dedupe"] is True
    assert captured["local_qualification_policy"] == profile_recall_qualification.smart_local_policy(
        market="us",
        platforms=["youtube"],
    )
    assert advance_call["smart_local_contract"] is True
    assert advance_call["body"]["limit"] == 30
    assert result["recall"]["local_qualification"] is local_contract


def test_smart_worker_session_and_response_strip_private_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attached: dict[str, Any] = {}
    monkeypatch.setattr(
        profile_discovery_pipeline.profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: _leaking_smart_result(),
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "filter_recall_result_platforms",
        lambda result, _value: result,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "filter_recall_result_market",
        lambda result, _value: result,
    )

    def _attach(_session_id: int, result: dict[str, Any]) -> dict[str, Any]:
        attached.update(result)
        return result

    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "attach_recall_result",
        _attach,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "advance_search_session_items",
        lambda **_kwargs: {
            "status": "empty",
            "selected": 0,
            "counts": {},
            "items": [],
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "_profile_advance_pipeline_status",
        lambda *_args: "partial",
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: {},
    )

    response = asyncio.run(
        profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=77,
            payload={
                "query_text": "US YouTube lens review",
                "_worker_planned": True,
                "market": "US",
                "platforms": ["youtube"],
                "_smart_local_30_contract": True,
                "include_new_discovery": False,
                "include_content_fit": False,
                "include_lazy_video_backfill": False,
            },
        )
    )

    assert _PRIVATE_MARKER not in json.dumps(attached, ensure_ascii=False)
    assert _PRIVATE_MARKER not in json.dumps(response, ensure_ascii=False)
    assert attached["items"][0]["candidate_facets"]["contact_available"] == "yes"
    assert attached["items"][0]["why_fit"] == "bio 命中 lens"
