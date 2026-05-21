"""Tests for dimensions_11_json bridge into KOL product-fit scoring."""
from __future__ import annotations

from app.services.vkpi.kol_product_fit import (
    _dimensions11_product_fit_for_family,
    _normalize_product_fit_key,
)


def test_normalize_product_fit_key_matches_sku_and_family_text():
    assert _normalize_product_fit_key("AF-35MM-F12-LAB") == _normalize_product_fit_key("AF 35mm F1.2 LAB")


def test_dimensions11_product_fit_for_family_uses_confident_exact_sku_match():
    component, match = _dimensions11_product_fit_for_family(
        {"display_name": "AF 35mm F1.2 LAB", "identity_key": "af 35mm f1.2 lab"},
        {
            "AF-35MM-F12-LAB": {
                "sku": "AF-35MM-F12-LAB",
                "normalized": _normalize_product_fit_key("AF-35MM-F12-LAB"),
                "score": 90,
                "confidence": 0.9,
                "profile_deep_id": 7,
            }
        },
    )

    assert component == 16.2
    assert match
    assert match["sku"] == "AF-35MM-F12-LAB"
    assert match["match_type"] == "sku_family_exact"


def test_dimensions11_product_fit_for_family_does_not_broad_match_unrelated_family():
    component, match = _dimensions11_product_fit_for_family(
        {"display_name": "AF 85mm F1.8", "identity_key": "af 85mm f1.8"},
        {
            "AF-35MM-F12-LAB": {
                "sku": "AF-35MM-F12-LAB",
                "normalized": _normalize_product_fit_key("AF-35MM-F12-LAB"),
                "score": 90,
                "confidence": 0.9,
                "profile_deep_id": 7,
            }
        },
    )

    assert component == 0
    assert match is None
