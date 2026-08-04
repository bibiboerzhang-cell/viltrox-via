from __future__ import annotations

import asyncio
import sqlite3

import pytest

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import profile_discovery_candidates, profile_recall
from app.domains.kol import profile_recall_precision as precision


def _lexical_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            handle TEXT,
            display_name TEXT,
            bio TEXT,
            primary_topic TEXT,
            content_style TEXT,
            followers INTEGER,
            country TEXT,
            language TEXT,
            duplicate_of_id INTEGER
        );
        CREATE TABLE vkpi_kol_profile_index_entries (
            kol_pool_id INTEGER,
            collection_name TEXT,
            method TEXT,
            status TEXT,
            profile_text TEXT,
            type_reason TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER,
            title TEXT,
            video_title TEXT,
            content_url TEXT,
            is_active INTEGER
        );
        CREATE TABLE vkpi_analysis_cache (
            target_type TEXT,
            target_id TEXT,
            derive_method TEXT,
            status TEXT,
            result TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
        [
            (1, "youtube", "factual", "Factual", "low light portrait photographer", "camera", "review", 20000, "US", "en"),
            (2, "youtube", "derived", "Derived", "generic creator", "lifestyle", "daily", 25000, "US", "en"),
            (3, "instagram", "wrong-platform", "IG", "low light portrait photographer", "camera", "review", 30000, "US", "en"),
            (4, "youtube", "partial", "Partial", "street photographer", "camera", "review", 22000, "US", "en"),
            (5, "youtube", "complete", "Complete", "street photographer", "camera", "review", 23000, "US", "en"),
            (6, "youtube", "chair", "Chair", "portrait photographer with a chair", "portrait", "photo", 24000, "US", "en"),
            (7, "youtube", "flash", "Flash", "lighting reviewer", "camera", "review", 26000, "US", "en"),
            (8, "youtube", "monitor", "Monitor", "cinematic video creator", "filmmaking", "tutorial", 27000, "US", "en"),
            (9, "youtube", "derived-video", "Derived Video", "cinematic video creator", "filmmaking", "tutorial", 28000, "US", "en"),
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_profile_index_entries VALUES (?,?,?,?,?,?)",
        [
            (2, "vkpi_kol_profile_index_v1", "vector_recall", "ready", "35mm lowlight portrait reviewer", "derived product fit"),
            (9, "vkpi_kol_profile_index_v1", "vector_recall", "ready", "field monitor video creator", "derived monitor fit"),
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_video_evidence VALUES (?,?,?,?,?,1)",
        [
            (11, 1, "Viltrox 35mm low light portrait review", "", "https://example/11"),
            (12, 3, "Viltrox 35mm low light portrait review", "", "https://example/12"),
            (13, 4, "26mm street photography test", "", "https://example/13"),
            (14, 5, "26mm EVO street photography test", "", "https://example/14"),
            (15, 7, "Viltrox Z1 flash hands-on", "", "https://example/15"),
            (16, 8, "Viltrox camera monitor setup for video creators", "", "https://example/16"),
        ],
    )
    return conn


def test_operator_terms_are_fused_with_generic_planner_and_platform_is_explicit() -> None:
    terms = precision.build_lexical_terms(
        "videographer photographer camera gear",
        "35mm 低光人像 YouTube 摄影师",
    )
    tokens = {term.token for term in terms}

    assert {"35mm", "低光", "人像", "youtube", "摄影师"} <= tokens
    assert precision.explicit_platforms_from_query("35mm 低光人像 YouTube 摄影师") == ["youtube"]
    assert precision.explicit_platforms_from_query("35mm 低光人像摄影师") == []


def test_factual_anchor_and_scene_make_strict_derived_profile_cannot(monkeypatch) -> None:
    conn = _lexical_db()
    monkeypatch.setattr(precision, "is_postgres_runtime", lambda: False)

    result = precision.lexical_recall_candidates(
        "videographer photographer camera gear",
        operator_query="35mm 低光人像 YouTube 摄影师",
        candidate_limit=30,
        conn=conn,
        hard_filters={"platforms": ["youtube"]},
    )
    by_id = {item["kol_pool_id"]: item for item in result["items"]}

    assert by_id[1]["retrieval_tier"] == "strict"
    assert by_id[2]["retrieval_tier"] == "relaxed"
    assert by_id[2]["derived_profile_strict_eligible"] is False
    assert by_id[2]["lexical_score"] < by_id[1]["lexical_score"]
    assert 3 not in by_id
    assert result["query_count"] <= 4


def test_all_independent_product_anchors_are_required_and_ascii_has_boundaries(monkeypatch) -> None:
    conn = _lexical_db()
    monkeypatch.setattr(precision, "is_postgres_runtime", lambda: False)

    result = precision.lexical_recall_candidates(
        "camera gear photographer",
        operator_query="26mm EVO 街头摄影",
        candidate_limit=30,
        conn=conn,
    )
    by_id = {item["kol_pool_id"]: item for item in result["items"]}

    assert by_id[4]["retrieval_tier"] == "relaxed"
    assert by_id[4]["factual_anchor_terms"] == ["26mm"]
    assert by_id[5]["retrieval_tier"] == "strict"
    assert set(by_id[5]["factual_anchor_terms"]) == {"26mm", "evo"}

    spaced = precision.lexical_recall_candidates(
        "camera gear photographer",
        operator_query="26 mm EVO 街头摄影",
        candidate_limit=30,
        conn=conn,
    )
    spaced_by_id = {item["kol_pool_id"]: item for item in spaced["items"]}
    assert spaced_by_id[5]["retrieval_tier"] == "strict"

    air = precision.lexical_recall_candidates(
        "portrait",
        operator_query="AIR portrait",
        candidate_limit=30,
        conn=conn,
    )
    air_by_id = {item["kol_pool_id"]: item for item in air["items"]}
    assert air_by_id[6]["retrieval_tier"] == "relaxed"
    assert "air" not in air_by_id[6]["factual_anchor_terms"]


def test_flash_and_monitor_categories_require_factual_product_evidence(monkeypatch) -> None:
    conn = _lexical_db()
    monkeypatch.setattr(precision, "is_postgres_runtime", lambda: False)

    flash = precision.lexical_recall_candidates(
        "适合 Viltrox Z1 闪光灯的 KOL",
        operator_query="适合 Viltrox Z1 闪光灯的 KOL",
        candidate_limit=30,
        conn=conn,
    )
    flash_by_id = {item["kol_pool_id"]: item for item in flash["items"]}
    assert flash_by_id[7]["retrieval_tier"] == "strict"
    assert {"z1", "flash"} <= set(flash_by_id[7]["factual_anchor_terms"])

    monitor = precision.lexical_recall_candidates(
        "适合唯卓仕监视器的视频创作者",
        operator_query="适合唯卓仕监视器的视频创作者",
        candidate_limit=30,
        conn=conn,
    )
    monitor_by_id = {item["kol_pool_id"]: item for item in monitor["items"]}
    assert monitor_by_id[8]["retrieval_tier"] == "strict"
    assert {"viltrox", "monitor"} <= set(monitor_by_id[8]["factual_anchor_terms"])
    assert monitor_by_id[9]["retrieval_tier"] == "relaxed"
    assert monitor_by_id[9]["derived_profile_strict_eligible"] is False


def test_rrf_is_deterministic_deduped_and_old_three_arg_hits_remain_valid() -> None:
    vectors = [
        profile_recall.RecallHit(1, 0.90, "q1"),
        profile_recall.RecallHit(1, 0.80, "q1-duplicate"),
        profile_recall.RecallHit(2, 0.85, "q2"),
    ]
    assert all(hit.retrieval_tier == "relaxed" for hit in vectors)
    lexicals = [
        profile_recall.RecallHit(2, None, "", 0.9, 0.9, precision.LEXICAL_METHOD, "strict"),
        profile_recall.RecallHit(3, None, "", 0.8, 0.8, precision.LEXICAL_METHOD, "relaxed"),
    ]

    first = profile_recall._hybrid_fuse_hits(
        vectors,
        lexicals,
        limit=10,
        factual_anchor_required=False,
    )
    second = profile_recall._hybrid_fuse_hits(
        vectors,
        lexicals,
        limit=10,
        factual_anchor_required=False,
    )

    assert [hit.kol_pool_id for hit in first] == [hit.kol_pool_id for hit in second]
    assert len({hit.kol_pool_id for hit in first}) == len(first)
    assert first[0].kol_pool_id == 2
    assert first[0].retrieval_method == precision.HYBRID_METHOD

    vector_only = profile_recall._hybrid_fuse_hits(
        [profile_recall.RecallHit(9, 0.7, "q9")],
        [],
        limit=10,
        factual_anchor_required=True,
    )
    assert vector_only[0].retrieval_tier == "relaxed"


def test_missing_values_are_omitted_and_lane_targets_drive_selection() -> None:
    score, missing, coverage = precision.missingness_aware_weighted_score(
        (("retrieval", 0.8, 0.6), ("type", None, 0.2), ("quality", None, 0.2))
    )
    assert score == 0.8
    assert missing == ["type", "quality"]
    assert coverage == 0.6

    candidates = []
    for lane, count in (("core_vertical", 4), ("expansion", 4), ("exploration", 4)):
        for index in range(count):
            candidates.append(
                {
                    "kol_pool_id": len(candidates) + 1,
                    "candidate_bucket": lane,
                    "bucket": "creator" if index % 2 else "reviewer",
                    "match_tier": "strict" if lane != "exploration" else "relaxed",
                    "display_rank_score": 0.9 - len(candidates) / 100,
                    "ranking_confidence": {"score": 0.8},
                    "followers": None,
                }
            )
    selected, diagnostics = precision.select_with_business_lane_quotas(
        candidates,
        limit=6,
        bucket_policy={"core_vertical": 2, "expansion": 2, "exploration": 2},
        creator_quota=3,
        reviewer_quota=3,
        allow_backfill=True,
    )

    assert len(selected) == 6
    assert diagnostics["lane_selected"] == {
        "core_vertical": 4,
        "expansion": 2,
        "exploration": 0,
    }
    assert diagnostics["lane_policy"]["exploration"] == "maximum_backfill_only"
    assert diagnostics["lane_contract_satisfied"] is True


def test_exploration_is_explicit_overflow_only_when_stricter_supply_is_short() -> None:
    candidates = []
    for lane, count in (("core_vertical", 1), ("expansion", 1), ("exploration", 5)):
        for index in range(count):
            candidates.append(
                {
                    "kol_pool_id": len(candidates) + 1,
                    "candidate_bucket": lane,
                    "bucket": "creator",
                    "match_tier": "backfill" if lane == "exploration" else "strict",
                    "display_rank_score": 0.9 - index / 100,
                    "ranking_confidence": {"score": 0.8},
                }
            )

    selected, diagnostics = precision.select_with_business_lane_quotas(
        candidates,
        limit=6,
        bucket_policy={"core_vertical": 2, "expansion": 2, "exploration": 2},
        creator_quota=6,
        reviewer_quota=0,
        allow_backfill=True,
    )

    assert len(selected) == 6
    assert diagnostics["lane_selected"] == {
        "core_vertical": 1,
        "expansion": 1,
        "exploration": 4,
    }
    assert diagnostics["exploration_overflow"] == 2
    assert diagnostics["lane_contract_satisfied"] is False


def test_unknown_profile_type_is_neutral_with_provisional_content_lane() -> None:
    row = {
        "kol_pool_id": 7,
        "profile_type": "unknown",
        "creator_type_score": None,
        "reviewer_type_score": None,
        "bio": "Independent camera gear reviewer",
        "primary_topic": "lens comparison",
        "followers": 10_000,
    }
    item = profile_recall._format_item(
        profile_recall.RecallHit(7, None, "", 0.7, 0.7, precision.LEXICAL_METHOD, "strict"),
        row,
        profile_recall._bucket_for(row, "dominant"),
        vector_weight=0.85,
        type_weight=0.15,
        type_boost_enabled=True,
        evidence={"video_evidence_count": 4, "with_view_count": 3, "deep_analysis_count": 1},
    )

    assert item["bucket"] == "unknown"
    assert item["type_label"] == "未分类"
    assert item["type_rank_score"] is None
    assert item["provisional_profile_lane"] == "reviewer"
    assert item["profile_type_confidence"] == "low"
    assert item["evidence_quality"]["claim_status"] == "coverage_only_not_accuracy"


def test_search_dto_includes_safe_metric_provenance_and_freshness() -> None:
    row = {
        "kol_pool_id": 41,
        "profile_type": "creator",
        "creator_type_score": 80,
        "reviewer_type_score": 20,
        "followers": 0,
        "source_type": "legacy_excel_p2d",
        "source_ref": "/private/imports/kol.xlsx?api_key=must-not-leak",
        "last_seen_at": "2026-08-03T13:00:00Z",
        "updated_at": "2026-08-03T13:01:00Z",
        "raw_platform_data": {
            "source": "youtube_api",
            "provider_status": "success",
            "fetched_at": "2026-08-03T12:59:00Z",
            "profile": {
                "kind": "youtube#channel",
                "statistics": {"subscriberCount": 0},
            },
        },
    }
    item = profile_recall._format_item(
        profile_recall.RecallHit(
            kol_pool_id=41,
            vector_score=None,
            qdrant_point_id="",
            lexical_score=0.8,
            retrieval_score=0.8,
            retrieval_method=precision.LEXICAL_METHOD,
            retrieval_tier="strict",
        ),
        row,
        "creator",
        vector_weight=0.85,
        type_weight=0.15,
        type_boost_enabled=True,
        evidence={},
    )

    assert item["followers"] == 0
    assert item["source_type"] == "legacy_excel_p2d"
    assert item["source_ref"] == "kol.xlsx"
    assert item["metric_observed_at"] == "2026-08-03T12:59:00Z"
    assert item["metric_recorded_at"] == "2026-08-03T13:00:00Z"
    assert item["last_seen_at"] == "2026-08-03T13:00:00Z"
    assert item["updated_at"] == "2026-08-03T13:01:00Z"
    assert "must-not-leak" not in str(item["data_truth"])


def test_router_turns_literal_youtube_into_hard_filter_not_planner_default(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "fallback",
            "search_query": "videographer photographer camera gear",
            "platforms": ["youtube", "instagram", "tiktok"],
            "creator_quota": 15,
            "reviewer_quota": 15,
        },
    )

    def fake_recall(**kwargs):
        captured.update(kwargs)
        return {
            "items": [
                {"kol_pool_id": 1, "platform": "youtube", "match_tier": "strict"},
                {"kol_pool_id": 2, "platform": "instagram", "match_tier": "strict"},
            ],
            "buckets": {"creator": [], "reviewer": [], "unknown": []},
            "business_buckets": {},
            "diagnostics": {"requested_count": 30},
        }

    monkeypatch.setattr(vkpi_kol_pool_search.kol_profile_recall, "recall_kol_profiles", fake_recall)
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    response = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {
                "input": "35mm 低光人像 YouTube 摄影师",
                "create_session": False,
                "result_limit": 30,
            },
            staff={"id": 42},
        )
    )

    assert captured["filters"]["platforms"] == ["youtube"]
    assert captured["operator_query_text"] == "35mm 低光人像 YouTube 摄影师"
    assert [item["platform"] for item in response["result"]["items"]] == ["youtube"]
    assert response["result"]["diagnostics"]["shortfall"] == 29


def test_platform_post_filter_reconciles_business_lanes_and_diagnostics() -> None:
    result = {
        "items": [
            {
                "kol_pool_id": 1,
                "platform": "youtube",
                "bucket": "creator",
                "candidate_bucket": "core_vertical",
                "match_tier": "strict",
            },
            {
                "kol_pool_id": 2,
                "platform": "instagram",
                "bucket": "reviewer",
                "candidate_bucket": "expansion",
                "match_tier": "relaxed",
            },
        ],
        "buckets": {
            "creator": [{"kol_pool_id": 1, "platform": "youtube"}],
            "reviewer": [{"kol_pool_id": 2, "platform": "instagram"}],
            "unknown": [],
        },
        "business_buckets": {
            "core_vertical": [{"kol_pool_id": 1, "platform": "youtube"}],
            "expansion": [{"kol_pool_id": 2, "platform": "instagram"}],
            "exploration": [],
        },
        "diagnostics": {
            "requested_count": 2,
            "business_bucket_counts": {"core_vertical": 1, "expansion": 1, "exploration": 0},
            "lane_selection": {
                "lane_targets": {"core_vertical": 1, "expansion": 1, "exploration": 0},
                "lane_selected": {"core_vertical": 1, "expansion": 1, "exploration": 0},
                "profile_counts": {"creator": 1, "reviewer": 1, "unknown": 0},
            },
        },
    }

    filtered = profile_discovery_candidates.filter_recall_result_platforms(result, ["youtube"])

    diagnostics = filtered["diagnostics"]
    assert diagnostics["final_count"] == 1
    assert diagnostics["business_bucket_counts"] == {
        "core_vertical": 1,
        "expansion": 0,
        "exploration": 0,
    }
    assert diagnostics["lane_selection"]["lane_selected"] == diagnostics["business_bucket_counts"]
    assert diagnostics["lane_selection"]["profile_counts"] == {
        "creator": 1,
        "reviewer": 0,
        "unknown": 0,
    }
    assert diagnostics["lane_selection"]["lane_contract_satisfied"] is False

    no_op = profile_discovery_candidates.filter_recall_result_platforms(
        {
            "items": [result["items"][0]],
            "buckets": {"creator": [result["items"][0]], "reviewer": [], "unknown": []},
            "business_buckets": {
                "core_vertical": [result["items"][0]],
                "expansion": [],
                "exploration": [],
            },
            "diagnostics": {
                "requested_count": 1,
                "business_bucket_counts": {"core_vertical": 1, "expansion": 0, "exploration": 0},
                "lane_selection": {
                    "lane_targets": {"core_vertical": 1, "expansion": 0, "exploration": 0},
                    "lane_available": {"core_vertical": 99, "expansion": 7, "exploration": 2},
                    "lane_selected": {"core_vertical": 1, "expansion": 0, "exploration": 0},
                },
            },
        },
        ["youtube"],
    )
    assert no_op["diagnostics"]["lane_selection"]["lane_available"]["core_vertical"] == 99


def test_pool_backfill_pushes_platform_filter_before_limit(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            followers INTEGER,
            duplicate_of_id INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool VALUES (?,?,?,NULL)",
        [
            *((item_id, "youtube", 1_000_000 - item_id) for item_id in range(1, 151)),
            *((item_id, "instagram", 10_000 - item_id) for item_id in range(151, 186)),
        ],
    )
    monkeypatch.setattr(profile_recall, "get_conn", lambda: conn)

    hits = profile_recall._pool_text_fallback_hits(
        "",
        30,
        include_relevance_backfill=True,
        filters={"platforms": ["instagram"]},
    )

    assert len(hits) == 30
    assert all(hit.kol_pool_id >= 151 for hit in hits)
    assert all(hit.retrieval_tier == "backfill" for hit in hits)
