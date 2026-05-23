from __future__ import annotations

from typing import Any

from app.services.vkpi import product_specs


def _product(**overrides: Any) -> dict[str, Any]:
    base = {
        "sku": "AF-35MM-F12-LAB-FE",
        "category_main": "Lens",
        "category_detail": "camera lens",
        "model_name": "Viltrox AF 35mm F1.2 LAB Full-Frame Lens for Sony E-Mount",
        "marketing_name": "AF 35mm F1.2 LAB Full-Frame Lens for Sony E-Mount",
        "series": "LAB",
        "mount": "FE-mount",
        "price_usd": 999,
        "product_url": "https://viltrox.com/products/af-35mm-f1-2-fe",
        "source_confidence": 1,
        "specs_json": '{"focal_length":"f=35mm","aperture":"F1.2-F16","lens_mount":"E-mount","variant_weight_grams":920,"weight":"≈920g(bare lens)","filter_size":"Φ77mm","official_product_type":"camera lens"}',
        "fit_tags_json": '["af","lab","fe-mount","full frame","35mm","f1.2-f16"]',
    }
    base.update(overrides)
    return base


def test_normalized_spec_fact_extracts_lens_core_fields() -> None:
    fact = product_specs.normalized_spec_fact(_product())

    assert fact["sku"] == "AF-35MM-F12-LAB-FE"
    assert fact["lens_like"] is True
    assert fact["mount_norm"] == "sony_e"
    assert fact["lens_mount_norm"] == "sony_e"
    assert fact["focal_length_min_mm"] == 35
    assert fact["focal_length_max_mm"] == 35
    assert fact["max_aperture_f"] == 1.2
    assert fact["min_aperture_label"] == "F16"
    assert fact["weight_grams"] == 920
    assert fact["filter_size_mm"] == 77
    assert fact["completeness_score"] == 100


def test_normalized_spec_fact_reports_missing_lens_fields() -> None:
    fact = product_specs.normalized_spec_fact(
        _product(
            sku="UNIT-LENS-MISSING",
            model_name="Unit Lens Missing Specs",
            marketing_name="Unit Lens Missing Specs",
            mount="",
            price_usd=None,
            product_url="",
            source_confidence=0,
            specs_json='{"official_product_type":"camera lens"}',
            fit_tags_json="[]",
        )
    )

    assert fact["lens_like"] is True
    assert fact["completeness_score"] < 100
    assert "mount" in fact["missing_fields_json"]
    assert "focal_length" in fact["missing_fields_json"]
    assert "max_aperture" in fact["missing_fields_json"]
    assert "weight_grams" in fact["missing_fields_json"]


def test_build_spec_readiness_report_is_read_only_by_default(monkeypatch) -> None:
    products = [_product(), _product(sku="AF-55MM-F18-EVO-Z", mount="Z-mount")]
    monkeypatch.setattr(product_specs, "_fetch_products", lambda limit: products)
    monkeypatch.setattr(product_specs, "_table_exists", lambda table: table == "vkpi_product_spec_facts")

    def _blocked_upsert(rows: list[dict[str, Any]]) -> int:
        raise AssertionError("dry-run report must not write facts")

    monkeypatch.setattr(product_specs, "_upsert_facts", _blocked_upsert)

    report = product_specs.build_spec_readiness_report(limit=10)

    assert report["passed"] is True
    assert report["write_db"] is False
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["summary"]["fact_count"] == 2
    assert report["summary"]["complete_lens_count"] >= 1


def test_build_spec_readiness_report_apply_upserts(monkeypatch) -> None:
    products = [_product()]
    written: dict[str, int] = {}
    monkeypatch.setattr(product_specs, "_fetch_products", lambda limit: products)
    monkeypatch.setattr(product_specs, "_table_exists", lambda table: table == "vkpi_product_spec_facts")

    def _fake_upsert(rows: list[dict[str, Any]]) -> int:
        written["count"] = len(rows)
        return len(rows)

    monkeypatch.setattr(product_specs, "_upsert_facts", _fake_upsert)

    report = product_specs.build_spec_readiness_report(limit=10, apply=True)

    assert report["passed"] is True
    assert report["write_db"] is True
    assert report["summary"]["facts_written"] == written["count"]
