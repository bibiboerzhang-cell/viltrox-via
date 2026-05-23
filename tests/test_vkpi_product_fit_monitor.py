from __future__ import annotations

from app.services.vkpi import product_fit_monitor


def test_monitor_report_passes_with_visible_warnings(monkeypatch) -> None:
    monkeypatch.setattr(
        product_fit_monitor,
        "_coverage_counts",
        lambda: {
            "product_count": 100,
            "alias_rows": 300,
            "alias_skus": 100,
            "spec_rows": 100,
            "spec_skus": 100,
            "alias_sku_coverage": 100.0,
            "spec_sku_coverage": 100.0,
        },
    )
    monkeypatch.setattr(
        product_fit_monitor,
        "_missing_samples",
        lambda: {"products_without_aliases": [], "products_without_spec_facts": []},
    )
    monkeypatch.setattr(
        product_fit_monitor,
        "_ambiguous_aliases",
        lambda: {"count": 2, "sample": [{"alias_norm": "35mm f12", "sku_count": 2}]},
    )
    monkeypatch.setattr(
        product_fit_monitor,
        "_low_spec_completeness",
        lambda threshold=70.0: {"threshold": threshold, "count": 3, "sample": [{"sku": "LOW"}]},
    )
    monkeypatch.setattr(
        product_fit_monitor,
        "_launch_alias_probe",
        lambda: {"available": True, "launch_count": 1, "exact_alias_hits": 1, "misses": 0},
    )
    monkeypatch.setattr(
        product_fit_monitor.kol_sku_fit,
        "build_kol_sku_fit_report",
        lambda **kwargs: {
            "passed": True,
            "kol_pool_id": 101,
            "kol": {"platform": "youtube", "handle": "unit"},
            "summary": {"top_count": 1},
            "top_skus": [{"sku": "AF-35MM-F12-LAB-FE", "score": 80}],
        },
    )

    report = product_fit_monitor.build_monitor_report()

    assert report["passed"] is True
    assert report["status"] == "warning"
    assert report["write_db"] is False
    assert report["provider_calls"] is False
    assert report["warnings"]


def test_monitor_report_fails_when_coverage_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        product_fit_monitor,
        "_coverage_counts",
        lambda: {
            "product_count": 100,
            "alias_rows": 0,
            "alias_skus": 0,
            "spec_rows": 100,
            "spec_skus": 100,
            "alias_sku_coverage": 0.0,
            "spec_sku_coverage": 100.0,
        },
    )
    monkeypatch.setattr(product_fit_monitor, "_missing_samples", lambda: {"products_without_aliases": [], "products_without_spec_facts": []})
    monkeypatch.setattr(product_fit_monitor, "_ambiguous_aliases", lambda: {"count": 0, "sample": []})
    monkeypatch.setattr(product_fit_monitor, "_low_spec_completeness", lambda threshold=70.0: {"threshold": threshold, "count": 0, "sample": []})
    monkeypatch.setattr(product_fit_monitor, "_launch_alias_probe", lambda: {"available": False, "reason": "required_tables_missing"})
    monkeypatch.setattr(product_fit_monitor.kol_sku_fit, "build_kol_sku_fit_report", lambda **kwargs: {"passed": True})

    report = product_fit_monitor.build_monitor_report()

    assert report["passed"] is False
    assert report["checks"]["alias_coverage_ok"] is False
