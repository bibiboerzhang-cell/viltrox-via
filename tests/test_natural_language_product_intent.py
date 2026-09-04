"""Regression coverage for search requests that intentionally omit an exact SKU."""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import product_resolver, smart_query_planner
from app.domains.kol import targeted_search_contract as contract


def _product(
    sku: str,
    name: str,
    *,
    category: str = "Lens",
    detail: str = "Prime Lens",
    series: str = "",
    mount: str = "",
    description: str = "",
    status: str = "official",
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "sku": sku,
        "model_name": name,
        "marketing_name": name,
        "category_main": category,
        "category_detail": detail,
        "series": series,
        "mount": mount,
        "description": description,
        "price_usd": 399,
        "status": status,
        "source_confidence": confidence,
    }


CATALOG = [
    _product(
        "AF-35MM-F12-LAB-FE",
        "Viltrox AF 35mm F1.2 LAB Full-Frame Lens for Sony E-Mount",
        series="LAB",
        mount="FE-mount",
        description="Large F1.2 aperture with soft bokeh for photo and video.",
    ),
    _product(
        "AF-35MM-F12-LAB-Z",
        "Viltrox AF 35mm F1.2 LAB Full-Frame Lens for Nikon Z-Mount",
        series="LAB",
        mount="Z-mount",
        description="Large F1.2 aperture with soft bokeh for photo and video.",
    ),
    _product(
        "AF-35MM-F18-EVO-FE",
        "Viltrox AF 35mm F1.8 EVO Full-Frame Lens",
        series="EVO",
        mount="FE-mount",
    ),
    _product(
        "DC-A1-2800-NITS-7-INCH-CAMERA-MONITOR",
        "Viltrox DC-A1 2800 Nits 7-Inch Camera Monitor",
        category="Monitor",
        detail="Camera Monitor",
        description="Official field monitor with waveform, LUT and focus peaking.",
    ),
    _product(
        "VL-MON015",
        "DC-A1",
        category="Monitor",
        detail="Monitor",
        description="Legacy catalog record for DC-A1.",
        status="priced",
        confidence=0.0,
    ),
    _product(
        "VL-MON007",
        "Viltrox DC-X3 Camera Monitor",
        category="Monitor",
        detail="Camera Monitor",
    ),
    _product(
        "EPIC-MEMENTO-25MM-35MM-50MM-65MM-75MM-100MM-135MM-SET",
        "Viltrox EPIC Memento 25mm/35mm/50mm/65mm/75mm/100mm/135mm Anamorphic Cine Lens Set",
        detail="Cine Lenses",
        series="Cine",
        description="Seven-lens cinema set; only the 65mm member is tailored for macro work.",
    ),
    _product(
        "EPIC-65MM-MACRO",
        "Viltrox EPIC 65mm Macro Anamorphic Cine Lens",
        detail="Cine Lenses",
        series="Cine",
        description="Dedicated macro cinema lens.",
    ),
]


@pytest.fixture()
def catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [dict(row) for row in CATALOG]},
    )


def test_35mm_f12_without_mount_resolves_shared_family_not_one_sku(catalog: None) -> None:
    resolved = product_resolver.resolve_product("给 35mm f/1.2 找人像摄影师")

    assert resolved is not None
    assert resolved["sku"] == ""
    assert resolved["resolution_basis"] == "focal_aperture_family"
    assert resolved["requested_aperture"] == "F1.2"
    assert set(resolved["focal_family_skus"]) == {
        "AF-35MM-F12-LAB-FE",
        "AF-35MM-F12-LAB-Z",
    }
    assert "F1.2" in resolved["marketing_name"]


def test_provider_free_search_understands_35mm_portrait_need_without_sku_or_clarification(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(
        "给 35mm f/1.2 找人像摄影师",
        body={"platforms": ["youtube"]},
    )

    assert plan["status"] != "needs_clarification"
    assert plan["resolved_product"]["sku"] == ""
    assert plan["search_brief"]["product"]["capability"] == "portrait lens"
    assert [cell["segment"] for cell in plan["query_cells"]] == ["portrait"]
    assert plan["query_cells"][0]["locked_term_groups"]["groups"][0]["canonical_term"] == "portrait lens"


@pytest.mark.parametrize("query", ["DC-A1", "DCA1", "DC-A1 监视器"])
def test_dc_a1_compact_code_resolves_the_official_catalog_identity(
    catalog: None,
    query: str,
) -> None:
    resolved = product_resolver.resolve_product(query)

    assert resolved is not None
    assert resolved["sku"] == "DC-A1-2800-NITS-7-INCH-CAMERA-MONITOR"
    assert resolved["resolved_model_code"] == "DC-A1"
    assert resolved["resolution_kind"] == "model_code_exact"


def test_other_hyphenated_compact_model_codes_are_not_split_apart(catalog: None) -> None:
    resolved = product_resolver.resolve_product("DC-X3 适合什么视频创作者")

    assert resolved is not None
    assert resolved["sku"] == "VL-MON007"
    assert resolved["category_main"] == "Monitor"


def test_multi_lens_cine_set_does_not_inherit_one_members_macro_capability() -> None:
    cine_set = next(row for row in CATALOG if "MEMENTO" in row["sku"])
    individual_macro = next(row for row in CATALOG if row["sku"] == "EPIC-65MM-MACRO")

    assert contract._product_capability(cine_set, []) == "cinema lens"
    assert contract._product_capability(individual_macro, []) == "macro cinema lens"


def test_explicit_portrait_intent_outranks_coarse_35mm_bucket() -> None:
    product = {
        "sku": "",
        "model_name": "Viltrox 35mm family",
        "marketing_name": "Viltrox 35mm family",
        "category_main": "Lens",
        "description": "35mm full-frame prime lens",
    }

    cells = contract.build_query_cells(
        query="找 35mm 人像摄影师",
        body={},
        product=product,
        product_focus=[],
        platforms=["youtube"],
    )

    assert len(cells) == 1
    assert cells[0]["segment"] == "portrait"
    assert cells[0]["locked_term_groups"]["groups"][0]["canonical_term"] == "portrait lens"
    assert cells[0]["primary_query"] == "portrait photographer tutorial camera gear"


def test_named_epic_memento_set_resolves_from_natural_language(catalog: None) -> None:
    resolved = product_resolver.resolve_product("EPIC Memento 整套电影镜头找纪录片导演")

    assert resolved is not None
    assert resolved["sku"].startswith("EPIC-MEMENTO-")
    assert contract._product_capability(resolved, []) == "cinema lens"


@pytest.mark.parametrize("camera", ["Z8", "Z6", "Z50"])
def test_nikon_z_camera_body_is_not_treated_as_missing_viltrox_sku(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    camera: str,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)
    query = f"找 Nikon {camera} 婚礼摄影师"

    assert product_resolver.resolve_product(query) is None
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})

    assert plan["status"] != "needs_clarification"
    assert plan.get("resolved_product") is None
    assert [cell["segment"] for cell in plan["query_cells"]] == ["wedding"]


@pytest.mark.parametrize(
    "query",
    [
        "找 35mm film photographers",
        "找拍 35mm 胶片的摄影师",
        "find photographers shooting 50mm equivalent",
        "find photographers shooting equivalent to 50mm",
    ],
)
def test_film_format_and_equivalent_focal_language_do_not_bind_a_lens_family(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    assert product_resolver.resolve_product(query) is None
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})

    assert plan["status"] != "needs_clarification"
    assert plan.get("resolved_product") is None
    if "equivalent" in query:
        primary = plan["query_cells"][0]["primary_query"]
        assert "50mm" in primary and "equivalent" in primary


@pytest.mark.parametrize(
    "query",
    [
        "找 35mm F1.2 film look 人像摄影师",
        "找 35mm 镜头拍胶片的摄影师",
    ],
)
def test_explicit_lens_anchor_keeps_real_35mm_product_resolution(
    catalog: None,
    query: str,
) -> None:
    resolved = product_resolver.resolve_product(query)

    assert resolved is not None
    assert resolved["focal_mm"] == 35


@pytest.mark.parametrize("query", ["找 35mm film photographers", "找拍 35mm 胶片的摄影师"])
def test_film_photography_intent_survives_into_query_cells(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})

    assert [cell["segment"] for cell in plan["query_cells"]] == ["film_photography"]
    assert plan["query_cells"][0]["primary_query"] == "film photographer tutorial camera gear"
