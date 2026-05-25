from __future__ import annotations

from typing import Any

from app.domains.kol import sku_fit as kol_sku_fit


def _fact(**overrides: Any) -> dict[str, Any]:
    base = {
        "sku": "AF-35MM-F12-LAB-FE",
        "category_main": "Lens",
        "category_detail": "camera lens",
        "series": "LAB",
        "mount": "FE-mount",
        "mount_norm": "sony_e",
        "focal_length_label": "35mm",
        "focal_length_min_mm": 35,
        "focal_length_max_mm": 35,
        "max_aperture_label": "F1.2",
        "max_aperture_f": 1.2,
        "weight_grams": 910,
        "price_usd": 999,
        "product_url": "https://viltrox.com/products/af-35mm-f1-2-fe",
        "source_confidence": 1,
        "completeness_score": 100,
        "missing_fields_json": "[]",
    }
    base.update(overrides)
    return base


def _alias(sku: str = "AF-35MM-F12-LAB-FE", alias: str = "Viltrox AF 35mm F1.2 LAB FE") -> dict[str, Any]:
    return {
        "sku": sku,
        "alias": alias,
        "alias_norm": "viltrox af 35mm f12 lab fe",
        "alias_type": "compact_brand",
        "confidence": 0.88,
    }


def test_score_sku_uses_alias_spec_and_viltrox_context() -> None:
    corpus = "creator reviewed viltrox af 35mm f12 lab fe camera lens bokeh"
    result = kol_sku_fit._score_sku(corpus, [_alias()], _fact(), {})

    assert result["sku"] == "AF-35MM-F12-LAB-FE"
    assert result["score"] > 50
    assert result["confidence"] > 0.5
    assert result["score_breakdown"]["alias"] > 0
    assert result["score_breakdown"]["spec"] > 0
    assert result["score_breakdown"]["viltrox_context"] == 5
    assert result["evidence"]


def test_profile_component_matches_dimensions11_alias() -> None:
    profile_fit = {
        "af 35mm f12 lab": {
            "label": "AF-35MM-F12-LAB",
            "score": 90,
            "confidence": 0.9,
            "profile_deep_id": 7,
        }
    }
    score, evidence = kol_sku_fit._profile_component(profile_fit, [_alias()], _fact())

    assert score > 0
    assert evidence[0]["type"] == "dimensions11_product_fit"


def test_build_kol_sku_fit_report_is_read_only(monkeypatch) -> None:
    monkeypatch.setattr(
        kol_sku_fit,
        "_fetch_kol",
        lambda kol_pool_id: {
            "id": kol_pool_id,
            "platform": "youtube",
            "handle": "unit",
            "display_name": "Unit",
            "bio": "Viltrox AF 35mm F1.2 LAB FE review for camera lens creators",
            "raw_platform_data": "{}",
            "brand_collaborations_json": "[]",
            "recommended_product_lines_json": "[]",
            "potential_concerns_json": "[]",
        },
    )
    monkeypatch.setattr(kol_sku_fit, "_fetch_sku_facts", lambda limit: [_fact()])
    monkeypatch.setattr(kol_sku_fit, "_fetch_aliases_by_sku", lambda skus: {skus[0]: [_alias()]})
    monkeypatch.setattr(kol_sku_fit, "_load_profile_product_fit", lambda kol_pool_id: {})

    report = kol_sku_fit.build_kol_sku_fit_report(kol_pool_id=101, top_n=5)

    assert report["passed"] is True
    assert report["write_db"] is False
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["summary"]["top_count"] == 1
    assert report["top_skus"][0]["sku"] == "AF-35MM-F12-LAB-FE"
