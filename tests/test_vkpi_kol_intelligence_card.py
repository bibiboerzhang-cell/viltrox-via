from __future__ import annotations

import json

from app.db.connection import get_conn
from app.domains.kol.competitor_detector import ensure_competitor_relation_schema
from app.domains.kol import intelligence_card as kol_intelligence_card
from app.services.vkpi import comment_intelligence, comments_collector, kol_pool, pillars, sentiment
from app.services.vkpi.refresh_tier import ensure_refresh_tier_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "vkpi-kol-intelligence-card-unit"


def _cleanup() -> None:
    conn = get_conn()
    try:
        comment_rows = conn.execute("SELECT id FROM vkpi_comments WHERE external_comment_id LIKE ?", (f"{MARKER}-%",)).fetchall()
        comment_ids = [int(row["id"]) for row in comment_rows]
        for comment_id in comment_ids:
            conn.execute("DELETE FROM vkpi_sentiment_results WHERE comment_id=?", (comment_id,))
        conn.execute("DELETE FROM vkpi_comments WHERE external_comment_id LIKE ?", (f"{MARKER}-%",))
        conn.execute("DELETE FROM vkpi_comment_intelligence_runs WHERE run_uid LIKE ?", (f"{MARKER}-%",))
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("DELETE FROM submissions WHERE extracted_handle=? OR title LIKE ?", (MARKER, f"%{MARKER}%"))
        conn.commit()
    except Exception:
        pass
    rows = conn.execute("SELECT id FROM vkpi_kol_pool WHERE source_ref=?", (MARKER,)).fetchall()
    for row in rows:
        kol_pool_id = int(row["id"])
        conn.execute("DELETE FROM vkpi_competitor_relation WHERE kol_pool_id=?", (kol_pool_id,))
        conn.execute("DELETE FROM vkpi_kol_refresh_tier WHERE kol_pool_id=?", (kol_pool_id,))
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=?", (MARKER,))
    conn.commit()
    kol_pool._clear_kol_pool_read_cache()


def _insert_card_row() -> int:
    conn = get_conn()
    now = "2026-05-23T07:00:00Z"
    raw = {
        "evidence_summary": {"cooperation_rows": 1, "evidence_count": 2, "risk_rows": 1},
        "videos": [
            {
                "id": "unit-video-1",
                "title": "Viltrox 35mm F1.2 LAB review vs Sigma",
                "url": "https://youtube.com/watch?v=unit-video-1",
                "publishedAt": "2026-05-20T00:00:00Z",
                "statistics": {"viewCount": "12000", "likeCount": "900"},
            }
        ]
    }
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool
          (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
           followers, following, posts_count, avg_views, avg_likes, avg_comments,
           engagement_rate, viltrox_fit_score, source_type, source_ref, raw_platform_data,
           brand_collaborations_json, recommended_product_lines_json, potential_concerns_json,
           created_by_staff_id, last_seen_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"{MARKER}-uid",
            "youtube",
            MARKER,
            f"https://youtube.com/@{MARKER}",
            "Intelligence Card Unit",
            "",
            "Camera lens reviews with Viltrox and Sigma comparisons",
            "",
            120000,
            None,
            16,
            42000,
            1800,
            95,
            0.045,
            82,
            "unit",
            MARKER,
            json.dumps(raw),
            json.dumps([{"brand": "Viltrox", "project": "35mm review"}]),
            json.dumps(["AF-35MM-F12-LAB"]),
            json.dumps(["unit risk note"]),
            None,
            now,
            now,
            now,
        ),
    )
    kol_pool_id = int(conn.execute("SELECT id FROM vkpi_kol_pool WHERE source_ref=?", (MARKER,)).fetchone()["id"])
    conn.execute(
        """
        INSERT INTO vkpi_kol_refresh_tier
          (kol_pool_id, tier, tier_reason, last_refresh_at, last_refresh_status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (kol_pool_id, "hot", "unit_test", now, "synced", now, now),
    )
    conn.commit()
    return kol_pool_id


def _insert_video_analysis_fixture() -> None:
    conn = get_conn()
    now = "2026-05-23T07:20:00Z"
    conn.execute(
        """
        INSERT INTO submissions (
          created_at, platform, url, extracted_handle, title, detection_status,
          final_score, creator_score, overall_score, memo, video_analysis
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            "youtube",
            "https://www.youtube.com/watch?v=unit-video-1",
            MARKER,
            f"{MARKER} Viltrox 35mm video review",
            "confirmed",
            88,
            72,
            80,
            "unit stored video analysis",
            json.dumps(
                {
                    "analyzed": True,
                    "method": "gemini_single_kol_unit",
                    "target_audience": "hybrid camera creators",
                    "production_quality": "clean studio review",
                    "quality_scores": {"lighting": 8, "audio": 7},
                    "quality_overall": 82,
                    "quality_summary": "Strong product demonstration with clear lens samples.",
                    "competitor_products": ["Sigma 35mm"],
                    "brand_integration_depth": "hands-on review",
                    "marketing_potential": "high",
                    "reference_value": "usable launch reference",
                    "timestamps": [{"t": "00:15", "note": "lens intro"}],
                    "improvements": ["add AF stress test"],
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()


def _insert_comment_intelligence_fixture() -> None:
    comments_collector.ensure_vkpi_comments_schema()
    sentiment.ensure_vkpi_sentiment_schema()
    pillars.ensure_vkpi_pillar_schema()
    comment_intelligence.ensure_vkpi_comment_intelligence_schema()
    conn = get_conn()
    now = "2026-05-23T07:10:00Z"
    comment_ids: list[int] = []
    comments = [
        (f"{MARKER}-comment-1", "I love this Viltrox review. Where can I buy the FE mount?"),
        (f"{MARKER}-comment-2", "Autofocus issue looks noisy in this test."),
    ]
    for index, (external_comment_id, text) in enumerate(comments, start=1):
        row = conn.execute(
            """
            INSERT INTO vkpi_comments (
              account_id, post_id, post_table, external_post_id, platform,
              external_comment_id, comment_text, author_handle, likes_count,
              reply_count, created_at, fetched_at, raw_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                0,
                91001,
                "industry_posts",
                "unit-video-1",
                "youtube",
                external_comment_id,
                text,
                "unit-viewer",
                index,
                0,
                now,
                now,
                "{}",
            ),
        ).fetchone()
        comment_ids.append(int(row["id"]))
    conn.execute(
        """
        INSERT INTO vkpi_sentiment_results (
          comment_id, sentiment, sentiment_confidence, emotion, emotion_confidence,
          brand_attitude, brand_attitude_confidence, llm_provider, llm_model,
          prompt_version, language_detected, analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            comment_ids[0],
            "positive",
            0.92,
            "curiosity",
            0.7,
            "supportive",
            0.83,
            "unit",
            "unit-model",
            sentiment.PROMPT_VERSION,
            "en",
            now,
        ),
    )
    pillar_id = int(conn.execute("SELECT id FROM vkpi_pillars WHERE pillar_key='lens_review'").fetchone()["id"])
    conn.execute(
        """
        INSERT INTO vkpi_post_pillars (
          post_id, post_table, pillar_id, is_primary, confidence, llm_provider,
          llm_model, prompt_version, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (91001, "industry_posts", pillar_id, True, 0.86, "unit", "unit-model", pillars.PROMPT_VERSION, now),
    )
    conn.execute(
        """
        INSERT INTO vkpi_comment_intelligence_runs (
          run_uid, post_id, post_table, status, triggered_by,
          params_json, steps_json, started_at, finished_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{MARKER}-run-1",
            91001,
            "industry_posts",
            "ok",
            "unit_test",
            json.dumps({"max_comments": 50, "comment_limit": 20}),
            json.dumps({
                "collection": {"status": "ok", "fetched_count": 2, "new_count": 2},
                "sentiment": {"status": "ok", "analyzed": 1},
                "pillar": {"status": "ok", "primary_pillar": "lens_review"},
            }),
            now,
            now,
            now,
        ),
    )
    conn.commit()


def test_kol_intelligence_card_aggregates_existing_evidence_without_provider_calls() -> None:
    ensure_vkpi_product_industry_schema()
    ensure_refresh_tier_schema()
    ensure_competitor_relation_schema()
    _cleanup()
    try:
        kol_pool_id = _insert_card_row()
        _insert_comment_intelligence_fixture()
        _insert_video_analysis_fixture()

        card = kol_intelligence_card.build_kol_pool_intelligence_card(kol_pool_id, include_product_fit=False)

        assert card["mode"] == "read_only_kol_intelligence_card_v0"
        assert card["provider_calls"] is False
        assert card["llm_calls"] is False
        assert card["write_db"] is False
        assert card["item"]["handle"] == MARKER
        assert card["freshness"]["tier"] == "hot"
        assert card["dimensions11"]["status"] == "ready"
        assert card["competitors"]["status"] == "ready"
        assert card["competitors"]["summary"]["competitor_brand"] == "sigma"
        assert card["competitors"]["evidence_count"] >= 1
        competitor_evidence = card["competitors"]["evidence"][0]
        assert competitor_evidence["source"] == "competitor_signal"
        assert competitor_evidence["source_table"] == "vkpi_kol_pool"
        assert competitor_evidence["competitor_brand"] == "sigma"
        assert competitor_evidence["risk_tier"] in {"avoid", "caution", "safe", "opportunity", "unknown"}
        assert competitor_evidence["confidence_method"] == "rule_v0"
        assert competitor_evidence["rebuttal_supported"] is True
        assert card["brand_signal"]["signal_count"] >= 2
        assert card["brand_signal"]["type_counts"]["mention_viltrox"] >= 1
        assert card["brand_signal"]["type_counts"]["mention_competitor"] >= 1
        assert card["comment_intelligence"]["status"] == "ready"
        assert card["comment_intelligence"]["provider_calls"] is False
        assert card["comment_intelligence"]["llm_calls"] is False
        assert card["comment_intelligence"]["write_db"] is False
        assert card["comment_intelligence"]["contract"] == {
            "declared": 50,
            "cached": 2,
            "cap": 12,
            "status": "cached_window",
        }
        assert card["comment_intelligence"]["run_count"] == 1
        assert card["comment_intelligence"]["cached_comment_count"] == 2
        assert card["comment_intelligence"]["counts"]["sentiment"]["positive"] >= 1
        assert card["comment_intelligence"]["counts"]["opportunities"] >= 1
        comment_evidence = card["comment_intelligence"]["evidence"][0]
        assert comment_evidence["source_table"] == "vkpi_comment_intelligence_runs"
        assert card["comment_intelligence"]["samples"][0]["source_table"] == "vkpi_comments"
        assert card["video_analysis"]["status"] == "ready"
        assert card["video_analysis"]["provider_calls"] is False
        assert card["video_analysis"]["llm_calls"] is False
        assert card["video_analysis"]["write_db"] is False
        assert card["video_analysis"]["analyzed_count"] == 1
        video_evidence = card["video_analysis"]["evidence"][0]
        assert video_evidence["source"] == "video_analysis"
        assert video_evidence["source_table"] == "submissions"
        assert video_evidence["source_url"] == "https://www.youtube.com/watch?v=unit-video-1"
        assert video_evidence["fields"]["target_audience"] == "hybrid camera creators"
        assert video_evidence["fields"]["production_quality"] == "clean studio review"
        assert video_evidence["fields"]["marketing_potential"] == "high"
        assert "target_audience" in video_evidence["field_names"]
        assert card["memory_card"]["status"] == "ready"
        assert card["memory_card"]["source_type"] == "unit"
        assert card["memory_card"]["history_match"]["cooperation_count"] >= 1
        assert card["memory_card"]["excel_record"]["brand_collaborations"]
        assert card["memory_card"]["recent_posts"][0]["title"] == "Viltrox 35mm F1.2 LAB review vs Sigma"
        assert card["product_fit"]["status"] == "skipped"
        assert card["decision_support"]["readiness"] in {"ready", "partial"}
        evidence_index = {row["section"]: row for row in card["evidence_index"]}
        assert set(evidence_index) == {
            "freshness",
            "dimensions11",
            "competitors",
            "brand_signal",
            "comment_intelligence",
            "video_analysis",
            "memory_card",
            "product_fit",
        }
        assert evidence_index["memory_card"]["label"] == "Memory Card"
        assert evidence_index["memory_card"]["evidence_count"] >= 2
        assert evidence_index["brand_signal"]["evidence_count"] >= 2
        assert evidence_index["competitors"]["evidence_count"] >= 1
        assert evidence_index["comment_intelligence"]["label"] == "Comment Intelligence"
        assert evidence_index["comment_intelligence"]["evidence_count"] >= 3
        assert evidence_index["video_analysis"]["label"] == "Video Analysis"
        assert evidence_index["video_analysis"]["evidence_count"] == 1
        assert "confidence" in evidence_index["dimensions11"]
    finally:
        _cleanup()


def test_product_fit_evidence_splits_official_catalog_from_discovery(monkeypatch) -> None:
    def _fake_preview(**_kwargs):
        return {
            "mode": "dry_run",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "items": [
                {
                    "product_family_uid": "pf-lab-35",
                    "product_family_name": "AF 35mm F1.2 LAB",
                    "score": 88.0,
                    "score_breakdown": {"dimensions11_product_fit": 16.2},
                    "matched_catalog_product": {
                        "sku": "AF-35MM-F12-LAB-FE",
                        "model_name": "AF 35mm F1.2 LAB FE",
                        "marketing_name": "AF 35mm F1.2 LAB",
                        "mount": "FE-mount",
                        "price_usd": 999.0,
                        "product_url": "https://viltrox.com/products/af-35mm-f12-lab-fe",
                        "source_confidence": 1.0,
                        "specs": {"focal_length": "f=35mm", "aperture": "F1.2-F16"},
                    },
                    "matched_catalog_products": [
                        {
                            "sku": "AF-35MM-F12-LAB-FE",
                            "model_name": "AF 35mm F1.2 LAB FE",
                            "mount": "FE-mount",
                            "price_usd": 999.0,
                            "specs": {"focal_length": "f=35mm"},
                        }
                    ],
                    "evidence_pro": [
                        {
                            "type": "dimensions11_product_fit",
                            "detail": "11D product fit matched AF-35MM-F12-LAB-FE",
                            "source_table": "vkpi_kol_profile_deep",
                            "source_id": 7,
                            "score_component": "dimensions11_product_fit",
                        }
                    ],
                    "evidence_con": [],
                },
                {
                    "product_family_uid": "pf-family-only",
                    "product_family_name": "AF 56mm family",
                    "score": 52.0,
                    "score_breakdown": {"adjacent_product_fit": 6},
                    "matched_catalog_products": [],
                    "evidence_pro": [],
                    "evidence_con": [{"type": "no_direct_history", "detail": "No direct history"}],
                },
            ],
        }

    monkeypatch.setattr(
        kol_intelligence_card.kol_product_fit,
        "build_kol_product_fit_preview",
        _fake_preview,
    )

    payload = kol_intelligence_card._product_fit(123, include_product_fit=True)

    assert payload["status"] == "ready"
    assert payload["provider_calls"] is False
    assert payload["llm_calls"] is False
    assert payload["write_db"] is False
    assert payload["official_catalog_count"] == 1
    assert payload["discovery_count"] == 1
    official = payload["official_catalog"][0]
    assert official["source"] == "official_catalog"
    assert official["sku"] == "AF-35MM-F12-LAB-FE"
    assert official["mount"] == "FE-mount"
    assert official["price_usd"] == 999.0
    assert official["specs"]["focal_length"] == "f=35mm"
    discovery = payload["discovery"][0]
    assert discovery["source"] == "rule_engine"
    assert discovery["confidence_method"] == "rule_v0_low_confidence"
    assert payload["rule_evidence_count"] >= 2


def test_decision_support_treats_freshness_payload_as_ready_without_status() -> None:
    payload = kol_intelligence_card._decision_support(
        {
            "freshness": {"tier": "hot", "reason": "fresh"},
            "dimensions11": {"status": "ready"},
            "competitors": {"status": "empty"},
            "brand_signal": {"status": "ready"},
            "comment_intelligence": {"status": "ready"},
            "video_analysis": {"status": "empty"},
            "memory_card": {"status": "ready"},
            "product_fit": {"status": "ready"},
        }
    )

    assert payload["readiness"] == "ready"
    assert payload["ready_sections"] == 8
    assert payload["gaps"] == []
