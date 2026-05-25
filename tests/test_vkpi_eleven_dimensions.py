"""Tests for rule-only V-KPI 11-dimension scoring honesty guards."""
from __future__ import annotations

import json

import app.domains.kol.eleven_dimensions as eleven_dimensions


def _base_row(**overrides):
    row = {
        "id": 1,
        "display_name": "Quiet Name",
        "handle": "quietname",
        "bio": "",
        "primary_topic": "",
        "secondary_topics_json": "",
        "content_style": "",
        "recommended_product_lines_json": "",
        "posts_count": 0,
        "raw_platform_data": "{}",
        "followers": 0,
        "avg_views": 0,
        "engagement_rate": 0,
        "last_seen_at": "",
        "brand_collaborations_json": "[]",
        "other_contacts_json": "[]",
        "email": "",
        "profile_url": "",
    }
    row.update(overrides)
    return row


def test_dimensions11_does_not_fill_missing_evidence(monkeypatch):
    monkeypatch.setattr(eleven_dimensions, "_kol_pool_row", lambda kol_pool_id: _base_row(id=kol_pool_id))
    monkeypatch.setattr(eleven_dimensions, "_posts", lambda row: [])
    monkeypatch.setattr(
        eleven_dimensions,
        "evaluate_kol_competitors",
        lambda kol_pool_id, prefer_persisted=True: {"relations": [], "summary": {}, "persisted": False},
    )

    payload = eleven_dimensions.compose_dimensions_11(123)

    assert payload["block1_content"]["content_specialty"] == {}
    assert payload["block1_content"]["confidence"]["content_specialty"] == 0
    assert payload["block4_specialty"]["industry_cluster"] == []
    assert payload["block4_specialty"]["product_fit"] == {}
    assert payload["block4_specialty"]["confidence"]["product_fit"] == 0
    assert payload["overall_score"] == 0
    assert payload["confidence"]["overall"] == 0


def test_dimensions11_marks_rule_evidence_with_confidence(monkeypatch):
    row = _base_row(
        id=55,
        display_name="35mm Street Review",
        bio="35mm street photography review tutorial with Viltrox samples",
        primary_topic="photography",
        posts_count=24,
        followers=120000,
        avg_views=35000,
        engagement_rate=0.08,
        last_seen_at="2026-05-20T10:00:00Z",
        email="creator@example.com",
        brand_collaborations_json='[{"brand":"Viltrox","project":"35mm review"}]',
        profile_url="https://example.com/creator",
    )
    monkeypatch.setattr(eleven_dimensions, "_kol_pool_row", lambda kol_pool_id: row)
    monkeypatch.setattr(eleven_dimensions, "_posts", lambda row: [{"title": "Viltrox 35mm street review"}])
    monkeypatch.setattr(eleven_dimensions, "_load_clusters", lambda: {"photography": ["photography", "street"]})
    monkeypatch.setattr(
        eleven_dimensions,
        "evaluate_kol_competitors",
        lambda kol_pool_id, prefer_persisted=True: {
            "persisted": True,
            "relations": [{"competitor_brand": "sigma"}],
            "summary": {"risk_score": 2.5, "risk_tier": "safe"},
        },
    )

    payload = eleven_dimensions.compose_dimensions_11(55)

    assert payload["block1_content"]["content_specialty"]
    assert payload["block1_content"]["confidence"]["content_specialty"] >= 0.4
    assert "AF-35MM-F12-LAB" in payload["block4_specialty"]["product_fit"]
    assert payload["block4_specialty"]["product_fit_confidence"]["AF-35MM-F12-LAB"] >= 0.35
    assert payload["block3_business"]["competitor_risk_score"] == 25
    assert payload["block3_business"]["confidence"]["competitor_risk_score"] == 0.9
    assert 0 < payload["overall_score"] <= 100
    assert payload["confidence"]["overall"] > 0


def test_product_fit_keywords_extend_from_official_catalog(monkeypatch):
    class _Result:
        def fetchall(self):
            return [
                {
                    "sku": "AF-55MM-F18-EVO-Z",
                    "category_main": "Lens",
                    "category_detail": "Autofocus Lens",
                    "model_name": "Viltrox AF 55mm F1.8 EVO Z",
                    "marketing_name": "AF 55mm F1.8 EVO Z",
                    "series": "EVO",
                    "mount": "Z-mount",
                    "specs_json": json.dumps(
                        {
                            "lens_mount": "Z-mount",
                            "focal_length": "f=55mm",
                            "aperture": "F1.8-F16",
                            "variant_title": "Viltrox AF 55mm F1.8 EVO Full-Frame Lens for Nikon Z",
                        }
                    ),
                    "fit_tags_json": json.dumps(["portrait", "full frame"]),
                }
            ]

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Result()

    monkeypatch.setattr(eleven_dimensions, "_CATALOG_PRODUCT_FIT_KEYWORDS", None)
    monkeypatch.setattr(eleven_dimensions, "_table_exists", lambda table: table == "vkpi_products")
    monkeypatch.setattr(eleven_dimensions, "get_conn", lambda: _Conn())

    keywords = eleven_dimensions._catalog_product_fit_keywords()

    assert "AF-55MM-F18-EVO-Z" in keywords
    assert "55mm" in keywords["AF-55MM-F18-EVO-Z"]
    assert "f1.8" in keywords["AF-55MM-F18-EVO-Z"]
    assert "af 55mm f1.8" in keywords["AF-55MM-F18-EVO-Z"]
    assert "af 55mm f1" not in keywords["AF-55MM-F18-EVO-Z"]
    assert "evo" not in keywords["AF-55MM-F18-EVO-Z"]
