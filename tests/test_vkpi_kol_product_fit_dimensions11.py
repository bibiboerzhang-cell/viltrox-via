"""Tests for dimensions_11_json bridge into KOL product-fit scoring."""
from __future__ import annotations

import json

from app.domains.kol import product_fit as kol_product_fit
from app.domains.kol.product_fit import (
    _catalog_product_for_sku,
    _catalog_products_for_match,
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


def test_catalog_product_for_sku_returns_compact_specs(monkeypatch):
    class _Result:
        def fetchone(self):
            return {
                "sku": "AF-35MM-F18-EVO-FE",
                "category_main": "Lens",
                "category_detail": "camera lens",
                "model_name": "Viltrox AF 35mm F1.8 EVO Full-Frame Lens for Sony E-Mount",
                "marketing_name": "AF 35mm F1.8 EVO FE",
                "price_usd": "395.00",
                "series": "EVO",
                "mount": "FE-mount",
                "product_url": "https://viltrox.com/products/af-35mm-f1-8-fe",
                "source_confidence": "1.00",
                "specs_json": json.dumps(
                    {
                        "lens_mount": "E-mount",
                        "focal_length": "f=35mm",
                        "aperture": "F1.8-F16",
                        "focus_motor": "STM+Lead screw",
                        "weight": "≈355g",
                        "filter_size": "Φ58mm",
                        "ignored": "not returned",
                    }
                ),
            }

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Result()

    monkeypatch.setattr(kol_product_fit, "_CATALOG_PRODUCT_BY_SKU", {})
    monkeypatch.setattr(kol_product_fit, "get_conn", lambda: _Conn())

    product = _catalog_product_for_sku("af-35mm-f18-evo-fe")

    assert product
    assert product["sku"] == "AF-35MM-F18-EVO-FE"
    assert product["mount"] == "FE-mount"
    assert product["price_usd"] == 395.0
    assert product["specs"]["focal_length"] == "f=35mm"
    assert product["specs"]["focus_motor"] == "STM+Lead screw"
    assert product["specs"]["filter_size"] == "Φ58mm"
    assert "ignored" not in product["specs"]


def test_catalog_products_for_match_returns_mount_variants(monkeypatch):
    class _Result:
        def fetchall(self):
            base = {
                "category_main": "Lens",
                "category_detail": "camera lens",
                "marketing_name": "",
                "price_usd": "999.00",
                "series": "LAB",
                "product_url": "https://viltrox.com/products/af-35mm-f1-2-fe",
                "source_confidence": "1.00",
                "specs_json": json.dumps({"focal_length": "f=35mm", "aperture": "F1.2-F16"}),
            }
            return [
                {**base, "sku": "AF-35MM-F12-LAB-FE", "model_name": "AF 35mm F1.2 LAB FE", "mount": "FE-mount"},
                {**base, "sku": "AF-35MM-F12-LAB-Z", "model_name": "AF 35mm F1.2 LAB Z", "mount": "Z-mount"},
            ]

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Result()

    monkeypatch.setattr(kol_product_fit, "_CATALOG_PRODUCTS", None)
    monkeypatch.setattr(kol_product_fit, "get_conn", lambda: _Conn())

    products = _catalog_products_for_match(
        {"sku": "AF-35MM-F12-LAB"},
        {"display_name": "AF 35mm F1.2 LAB", "identity_key": "af 35mm f1.2 lab"},
    )

    assert [product["sku"] for product in products] == ["AF-35MM-F12-LAB-FE", "AF-35MM-F12-LAB-Z"]
    assert {product["mount"] for product in products} == {"FE-mount", "Z-mount"}


def test_catalog_products_for_match_uses_family_when_no_dimensions11_match(monkeypatch):
    class _Result:
        def fetchall(self):
            base = {
                "category_main": "Lens",
                "category_detail": "camera lens",
                "marketing_name": "",
                "price_usd": "395.00",
                "series": "EVO",
                "product_url": "https://viltrox.com/products/af-35mm-f1-8-fe",
                "source_confidence": "1.00",
                "specs_json": json.dumps({"focal_length": "f=35mm", "aperture": "F1.8-F16"}),
            }
            return [
                {**base, "sku": "AF-35MM-F18-EVO-FE", "model_name": "AF 35mm F1.8 EVO FE", "mount": "FE-mount"},
                {**base, "sku": "AF-35MM-F18-EVO-Z", "model_name": "AF 35mm F1.8 EVO Z", "mount": "Z-mount"},
            ]

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Result()

    monkeypatch.setattr(kol_product_fit, "_CATALOG_PRODUCTS", None)
    monkeypatch.setattr(kol_product_fit, "get_conn", lambda: _Conn())

    products = _catalog_products_for_match(
        None,
        {"display_name": "AF 35mm F1.8 EVO", "identity_key": "af 35mm f1.8 evo"},
    )

    assert [product["sku"] for product in products] == ["AF-35MM-F18-EVO-FE", "AF-35MM-F18-EVO-Z"]
