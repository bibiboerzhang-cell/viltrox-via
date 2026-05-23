from __future__ import annotations

import json

from app.db.connection import get_conn
from app.services.vkpi import kol_intelligence_card, kol_pool
from app.services.vkpi.kol_competitor_detector import ensure_competitor_relation_schema
from app.services.vkpi.refresh_tier import ensure_refresh_tier_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "vkpi-kol-intelligence-card-unit"


def _cleanup() -> None:
    conn = get_conn()
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


def test_kol_intelligence_card_aggregates_existing_evidence_without_provider_calls() -> None:
    ensure_vkpi_product_industry_schema()
    ensure_refresh_tier_schema()
    ensure_competitor_relation_schema()
    _cleanup()
    try:
        kol_pool_id = _insert_card_row()

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
        assert card["brand_signal"]["signal_count"] >= 2
        assert card["brand_signal"]["type_counts"]["mention_viltrox"] >= 1
        assert card["brand_signal"]["type_counts"]["mention_competitor"] >= 1
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
            "memory_card",
            "product_fit",
        }
        assert evidence_index["memory_card"]["label"] == "Memory Card"
        assert evidence_index["memory_card"]["evidence_count"] >= 2
        assert evidence_index["brand_signal"]["evidence_count"] >= 2
        assert "confidence" in evidence_index["dimensions11"]
    finally:
        _cleanup()
