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
