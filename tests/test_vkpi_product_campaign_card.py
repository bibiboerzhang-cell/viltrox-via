from __future__ import annotations

from app.domains.products import product_campaign_card


def test_product_campaign_card_is_read_only(monkeypatch) -> None:
    monkeypatch.setattr(
        product_campaign_card,
        "_fetch_sku",
        lambda sku="": {
            "sku": "AF-35/1.8-FE",
            "series": "AF",
            "mount": "FE Mount",
            "focal_length_label": "35mm",
            "max_aperture_label": "F1.8",
            "price_usd": 399,
            "completeness_score": 95,
            "missing_fields_json": "[]",
        },
    )
    monkeypatch.setattr(
        product_campaign_card,
        "_fetch_aliases",
        lambda sku: [{"alias": "35mm F1.8 FE", "alias_norm": "35mm f18 fe", "confidence": 0.95}],
    )
    monkeypatch.setattr(
        product_campaign_card,
        "_kol_rows",
        lambda limit: [
            {
                "id": 1,
                "platform": "youtube",
                "handle": "creator",
                "display_name": "Creator",
                "bio": "Sony FE 35mm f1.8 lens reviews and Viltrox tests",
                "followers": 100000,
                "avg_views": 25000,
                "viltrox_fit_score": 80,
            }
        ],
    )
    monkeypatch.setattr(
        product_campaign_card,
        "_competitor_rows",
        lambda limit: [{"brand": "sigma", "normalized_brand": "sigma", "signal_type": "pricing_sensitive", "score": 20, "detail": "35mm price issue", "product_hints": ["35mm"]}],
    )

    report = product_campaign_card.build_product_campaign_card()

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["summary"]["sku"] == "AF-35/1.8-FE"
    assert report["kol_candidates"]
    assert report["market_risk"]["risk_tier"] in {"low", "medium", "high"}
    assert report["policy"]["no_project_created"] is True


def test_product_campaign_card_fails_without_sku(monkeypatch) -> None:
    monkeypatch.setattr(product_campaign_card, "_fetch_sku", lambda sku="": {})
    monkeypatch.setattr(product_campaign_card, "_fetch_aliases", lambda sku: [])
    monkeypatch.setattr(product_campaign_card, "_kol_rows", lambda limit: [])
    monkeypatch.setattr(product_campaign_card, "_competitor_rows", lambda limit: [])

    report = product_campaign_card.build_product_campaign_card()

    assert report["passed"] is False
    assert report["checks"]["sku_selected"] is False
