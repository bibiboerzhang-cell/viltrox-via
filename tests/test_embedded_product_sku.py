"""Exact catalog SKU inside natural operator text must keep its full identity."""
from copy import deepcopy

import pytest

from app.domains.kol import product_resolver, smart_query_planner


@pytest.fixture
def catalog(monkeypatch):
    rows = [{"sku": sku, "model_name": name, "marketing_name": name, "mount": mount,
             "series": series, "category_main": "Lens", "category_detail": "Prime Lens",
             "status": "official", "source_confidence": 1.0}
            for sku, name, mount, series in (
                ("AF-35MM-F12-LAB-FE", "AF 35mm F1.2 LAB", "FE-mount", "LAB"),
                ("AF-35MM-F12-LAB-Z", "AF 35mm F1.2 LAB", "Z-mount", "LAB"),
                ("AF-35MM-F18-EVO-FE", "AF 35mm F1.8 EVO", "FE-mount", "EVO"),
            )]
    monkeypatch.setattr(product_resolver, "list_product_catalog", lambda **_kwargs: {"products": deepcopy(rows)})
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_args, **_kwargs: None)


@pytest.mark.parametrize("query", [
    "AF-35MM-F12-LAB-FE", "找适合 AF-35MM-F12-LAB-FE 的美国街拍摄影师",
    "找适合AF-35MM-F12-LAB-FE的美国街拍摄影师", "Find street photographers for af-35mm-f12-lab-fe.",
])
def test_exact_catalog_sku_survives_bare_and_embedded_operator_text(catalog, query):
    resolved = product_resolver.resolve_product(query)
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    assert resolved is not None
    assert resolved["sku"] == "AF-35MM-F12-LAB-FE"
    assert resolved["mount"] == "FE-mount"
    assert "F1.2" in resolved["model_name"]
    assert plan["resolved_product"]["sku"] == "AF-35MM-F12-LAB-FE"
    assert plan["resolved_product"]["mount"] == "FE-mount"


def test_family_without_mount_does_not_invent_one_sku(catalog):
    query = "给 35mm f/1.2 找美国街拍摄影师"
    resolved = product_resolver.resolve_product(query)
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    assert resolved["sku"] == "" and resolved["mount"] == ""
    assert resolved["requested_aperture"] == "F1.2"
    assert set(resolved["focal_family_skus"]) == {"AF-35MM-F12-LAB-FE", "AF-35MM-F12-LAB-Z"}
    assert plan["resolved_product"]["sku"] == ""


def test_people_only_query_does_not_bind_catalog_sku(catalog):
    query = "找美国街拍摄影师"
    assert product_resolver.resolve_product(query) is None
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    assert plan.get("resolved_product") is None
    assert plan["status"] != "needs_clarification"
    assert any(cell["segment"] == "street" for cell in plan["query_cells"])


@pytest.mark.parametrize("other", ["AF-35MM-F12-LAB-Z", "AF-35MM-F12-LAB-UNKNOWN"])
def test_multiple_explicit_skus_do_not_silently_pick_the_known_or_first_one(catalog, other):
    query = f"比较 AF-35MM-F12-LAB-FE 和 {other} 找街拍摄影师"
    assert product_resolver.resolve_product(query) is None
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    assert not plan.get("resolved_product")
    assert plan["status"] == "needs_clarification"


def test_negated_full_sku_does_not_become_selected_product(catalog):
    query = "不要 AF-35MM-F12-LAB-FE，找美国街拍摄影师"
    assert product_resolver.resolve_product(query) is None
    assert smart_query_planner.plan_text_query_provider_free(query, body={}).get("resolved_product") is None
    contrasted = "不要 AF-35MM-F12-LAB-Z，要 AF-35MM-F12-LAB-FE 的美国街拍摄影师"
    assert product_resolver.resolve_product(contrasted)["sku"] == "AF-35MM-F12-LAB-FE"


def test_full_sku_does_not_override_conflicting_mount_in_prose(catalog):
    query = "给 AF-35MM-F12-LAB-FE 找富士 X 卡口街拍摄影师"
    assert product_resolver.resolve_product(query) is None


def test_full_sku_does_not_override_multiple_focal_request(catalog):
    query = "给 AF-35MM-F12-LAB-FE 以及 35mm 或 85mm 找街拍摄影师"
    assert product_resolver.resolve_product(query) is None


@pytest.mark.parametrize("sku", ["AF-35MM-F12-LAB-FEX", "PREFIX-AF-35MM-F12-LAB-FE"])
def test_partial_identifier_overlap_is_not_exact_sku(catalog, sku):
    resolved = product_resolver.resolve_product(f"给 {sku} 找街拍摄影师")
    assert not resolved or resolved.get("sku") != "AF-35MM-F12-LAB-FE"
