from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import profile_recall


def _install_recall_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: dict[int, dict[str, Any]],
    hits: list[profile_recall.RecallHit],
    resolved_text: str = "camera reviewer",
) -> None:
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **_kwargs: (resolved_text, {"query_profile": ""}),
    )
    monkeypatch.setattr(profile_recall, "_embed_query", lambda _text: ([0.1], {}))
    monkeypatch.setattr(profile_recall, "_search_qdrant", lambda _vector, _limit: hits)
    monkeypatch.setattr(
        profile_recall,
        "_entry_rows",
        lambda ids: {item_id: dict(rows[item_id]) for item_id in ids if item_id in rows},
    )
    monkeypatch.setattr(profile_recall, "_evidence_summaries", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})


def _row(item_id: int, *, platform: str = "youtube", profile_type: str = "creator") -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": f"creator-{item_id}",
        "display_name": f"Creator {item_id}",
        "platform": platform,
        "profile_type": profile_type,
        "creator_type_score": 80,
        "reviewer_type_score": 80,
        "followers": 10_000 + item_id,
        "country": "US",
        "language": "en",
        "primary_topic": "camera lens review",
        "bio": "Camera gear reviewer and filmmaker",
    }


def test_filtered_search_returns_30_and_labels_relevance_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {
        item_id: _row(item_id, profile_type="creator" if item_id % 2 else "reviewer")
        for item_id in range(1, 41)
    }
    hits = [
        profile_recall.RecallHit(
            kol_pool_id=item_id,
            vector_score=0.9 - item_id / 1000 if item_id <= 12 else 0.0,
            qdrant_point_id=f"q-{item_id}" if item_id <= 12 else "pool_relevance_backfill",
        )
        for item_id in rows
    ]
    _install_recall_fixture(monkeypatch, rows=rows, hits=hits)

    result = profile_recall.recall_kol_profiles(
        query_text="camera reviewer",
        candidate_limit=40,
        limit=30,
        creator_quota=15,
        reviewer_quota=15,
        filters={
            "platforms": ["youtube"],
            "countries": ["United States"],
            "languages": ["English"],
            "followers_min": 5_000,
            "verticals": ["camera"],
            "gear_content": "yes",
        },
        bucket_policy={"core_vertical": 18, "expansion": 9, "exploration": 3},
    )

    diagnostics = result["diagnostics"]
    assert len(result["items"]) == 30
    assert diagnostics["requested_count"] == 30
    assert diagnostics["strict_count"] == 0
    assert diagnostics["relaxed_count"] == 12
    assert diagnostics["backfill_count"] == 18
    assert diagnostics["final_count"] == 30
    assert diagnostics["shortfall"] == 0
    assert diagnostics["result_contract_satisfied"] is True
    assert diagnostics["unsupported_filters"] == []
    assert all(item["platform"] == "youtube" for item in result["items"])
    assert all(item["match_tier"] in {"relaxed", "backfill"} for item in result["items"])
    assert all(item["relaxed_filters"] == ["query_relevance"] for item in result["items"][12:])
    assert sum(diagnostics["business_bucket_counts"].values()) == 30
    assert result["evaluation_status"] == {
        "state": "not_evaluated",
        "evaluation_contract": "kol_search_relevance_eval_v1",
        "gold_set_id": None,
        "dataset_version": "kol_search_business_queries_v1",
        "algorithm_version": "kol_robust_rank_v1",
        "code_version": None,
        "dataset_snapshot_id": None,
        "filter_policy_version": "kol_search_hard_filters_and_lanes_v1",
        "target_count": 360,
        "labeled_count": 0,
        "dual_review_target": 180,
        "dual_reviewed_count": 0,
        "disagreement_count": 0,
        "claim_status": "not_evaluated",
        "metrics": None,
        "note": "检索排序分不是准确率；完成固定 6 类 Top-30 真人标注后才发布离线相关性指标。",
    }


def test_hard_filters_are_never_relaxed_and_shortfall_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {
        item_id: _row(item_id, platform="youtube" if item_id <= 24 else "instagram")
        for item_id in range(1, 41)
    }
    hits = [
        profile_recall.RecallHit(item_id, 0.9 - item_id / 1000, f"q-{item_id}")
        for item_id in rows
    ]
    _install_recall_fixture(monkeypatch, rows=rows, hits=hits)

    result = profile_recall.recall_kol_profiles(
        query_text="camera reviewer",
        candidate_limit=40,
        limit=30,
        creator_quota=15,
        reviewer_quota=15,
        filters={"platforms": ["youtube"]},
    )

    assert len(result["items"]) == 24
    assert all(item["platform"] == "youtube" for item in result["items"])
    assert result["diagnostics"]["hard_filter_rejected_by"] == {"platforms": 16}
    assert result["diagnostics"]["final_count"] == 24
    assert result["diagnostics"]["shortfall"] == 6
    assert result["diagnostics"]["result_contract_satisfied"] is False


def test_missing_reviewer_quota_refills_from_strict_creator_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {item_id: _row(item_id, profile_type="creator") for item_id in range(1, 31)}
    hits = [
        profile_recall.RecallHit(item_id, 0.9 - item_id / 1000, f"q-{item_id}")
        for item_id in rows
    ]
    _install_recall_fixture(monkeypatch, rows=rows, hits=hits)

    result = profile_recall.recall_kol_profiles(
        query_text="camera reviewer",
        candidate_limit=30,
        limit=30,
        creator_quota=15,
        reviewer_quota=15,
    )

    assert len(result["items"]) == 30
    assert result["diagnostics"]["strict_count"] == 0
    assert result["diagnostics"]["relaxed_count"] == 30
    assert result["diagnostics"]["backfill_count"] == 0
    assert result["diagnostics"]["profile_quota_refill_count"] == 15
    assert result["diagnostics"]["shortfall"] == 0


def test_smart_search_forwards_result_limit_filters_and_bucket_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {"status": "ready", "search_query": "camera reviewer"},
    )

    def fake_recall(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "items": [{"kol_pool_id": item_id, "platform": "youtube"} for item_id in range(1, 31)],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 30, "final_count": 30, "shortfall": 0},
        }

    monkeypatch.setattr(vkpi_kol_pool_search.kol_profile_recall, "recall_kol_profiles", fake_recall)
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    result = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {
                "input": "camera reviewer",
                "create_session": False,
                "result_limit": 30,
                "search_strategy": "balanced",
                "filters": {"platforms": ["youtube"], "followers_min": 5_000},
                "bucket_policy": {"core_vertical": 18, "expansion": 9, "exploration": 3},
            },
            staff={"id": 42},
        )
    )

    assert result["status"] == "ready"
    assert captured["limit"] == 30
    assert captured["filters"] == {"platforms": ["youtube"], "followers_min": 5_000}
    assert captured["search_strategy"] == "balanced"
    assert captured["bucket_policy"] == {"core_vertical": 18, "expansion": 9, "exploration": 3}
    assert captured["candidate_limit"] == 500
    assert captured["allow_backfill"] is False
    assert captured["dedupe"] is True
    assert captured["local_qualification_policy"]["policy_version"] == 2


def test_ui_vertical_filter_id_matches_human_readable_profile_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {
        1: {
            **_row(1),
            "primary_topic": "Lens review and camera comparison",
            "bio": "Independent optical reviewer",
        },
        2: {
            **_row(2),
            "primary_topic": "Lifestyle travel vlog",
            "bio": "Daily travel stories",
        },
    }
    hits = [
        profile_recall.RecallHit(1, 0.9, "q-1"),
        profile_recall.RecallHit(2, 0.8, "q-2"),
    ]
    _install_recall_fixture(
        monkeypatch,
        rows=rows,
        hits=hits,
        resolved_text="lens review",
    )

    result = profile_recall.recall_kol_profiles(
        query_text="lens review",
        candidate_limit=2,
        limit=2,
        creator_quota=2,
        reviewer_quota=0,
        filters={"verticals": ["lens_review"]},
        allow_backfill=False,
    )

    assert [item["kol_pool_id"] for item in result["items"]] == [1]
    assert result["items"][0]["candidate_bucket"] == "expansion"
    assert result["diagnostics"]["shortfall"] == 1


def test_strict_product_anchor_candidate_is_core_even_without_generic_vertical_words() -> None:
    lane, reason = profile_recall._natural_business_lane(
        {
            "match_tier": "strict",
            "primary_topic": "creator",
            "bio": "visual storyteller",
            "source_fields": {
                "retrieval_meta": {"factual_anchor_terms": ["z1", "flash"]}
            },
        }
    )

    assert lane == "core_vertical"
    assert "产品锚点" in reason
