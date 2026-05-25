from __future__ import annotations

from typing import Any

from app.domains.products import product_aliases


def _product(**overrides: Any) -> dict[str, Any]:
    base = {
        "sku": "AF-35MM-F18-EVO-FE",
        "category_main": "Lens",
        "category_detail": "camera lens",
        "model_name": "Viltrox AF 35mm F1.8 EVO Full-Frame Lens for Sony E-Mount",
        "marketing_name": "AF 35mm F1.8 EVO Full-Frame Lens for Sony E-Mount",
        "series": "EVO",
        "mount": "FE-mount",
        "product_url": "https://viltrox.com/products/af-35mm-f1-8-fe",
        "source_confidence": 1,
        "specs_json": '{"official_handle":"af-35mm-f1-8-fe","focal_length":"35mm","aperture":"F1.8-F16"}',
        "fit_tags_json": '["af","evo","fe-mount","full frame","35mm","f=35mm","f1.8-f16"]',
    }
    base.update(overrides)
    return base


def test_normalize_alias_collapses_aperture_and_sku_punctuation() -> None:
    assert product_aliases.normalize_alias("AF 35mm F1.8 EVO FE") == "af 35mm f18 evo fe"
    assert product_aliases.normalize_alias("AF-35MM-F18-EVO-FE") == "af 35mm f18 evo fe"
    assert product_aliases.normalize_alias("f=35mm") == "35mm"


def test_generated_aliases_include_sku_model_handle_and_spec_combos() -> None:
    aliases = product_aliases.generated_aliases_for_product(_product())
    by_norm = {item["alias_norm"]: item for item in aliases}

    assert by_norm["af 35mm f18 evo fe"]["alias_type"] == "sku"
    assert by_norm["viltrox af 35mm f18 evo full frame lens for sony e mount"]["alias_type"] == "model"
    assert by_norm["af 35mm f18 fe"]["alias_type"] == "official_handle"
    assert by_norm["35mm f18 fe mount"]["alias_type"] == "spec_combo_mount"
    assert by_norm["viltrox af 35mm f18 evo fe"]["alias_type"] == "compact_brand"
    assert "af" not in by_norm
    assert "full frame" not in by_norm


def test_build_alias_readiness_report_is_read_only_by_default(monkeypatch) -> None:
    products = [
        _product(),
        _product(sku="AF-35MM-F18-EVO-Z", mount="Z-mount", product_url="https://viltrox.com/products/af-35mm-f1-8-z"),
    ]
    monkeypatch.setattr(product_aliases, "_fetch_products", lambda limit: products)
    monkeypatch.setattr(product_aliases, "_table_exists", lambda table: table == "vkpi_product_aliases")
    monkeypatch.setattr(
        product_aliases,
        "_launch_probe",
        lambda alias_rows: {"available": True, "launch_count": 2, "exact_alias_hits": 1, "misses": 1, "miss_sample": []},
    )

    def _blocked_upsert(rows: list[dict[str, Any]]) -> int:
        raise AssertionError("dry-run report must not write aliases")

    monkeypatch.setattr(product_aliases, "_upsert_aliases", _blocked_upsert)

    report = product_aliases.build_alias_readiness_report(limit=10)

    assert report["passed"] is True
    assert report["write_db"] is False
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["summary"]["product_count"] == 2
    assert report["summary"]["generated_alias_count"] > 0
    assert report["summary"]["ambiguous_alias_norm_count"] > 0


def test_build_alias_readiness_report_apply_upserts_aliases(monkeypatch) -> None:
    products = [_product()]
    written: dict[str, int] = {}
    monkeypatch.setattr(product_aliases, "_fetch_products", lambda limit: products)
    monkeypatch.setattr(product_aliases, "_table_exists", lambda table: table == "vkpi_product_aliases")
    monkeypatch.setattr(product_aliases, "_launch_probe", lambda alias_rows: {"available": False, "reason": "vkpi_product_launches_missing"})

    def _fake_upsert(rows: list[dict[str, Any]]) -> int:
        written["count"] = len(rows)
        return len(rows)

    monkeypatch.setattr(product_aliases, "_upsert_aliases", _fake_upsert)

    report = product_aliases.build_alias_readiness_report(limit=10, apply=True)

    assert report["passed"] is True
    assert report["write_db"] is True
    assert report["summary"]["aliases_written"] == written["count"]
    assert written["count"] > 0
