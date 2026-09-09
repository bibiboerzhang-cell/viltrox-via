"""Server-built queries retain scene meaning before the real runtime gate."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.domains.kol import smart_query_planner, targeted_search_contract
from app.domains.kol.search_plan_semantics import validate_query_cell_execution
from app.domains.kol.targeted_search_runtime import prepare_local_search
from app.domains.kol.targeted_search_scene_query import preserve_controlled_scene_phrases


def _prepare(query: str, *, objective: str = "prospective_growth", body=None):
    request = {"objective": objective, "input": query, "market": "US", **(body or {})}
    plan = smart_query_planner._fallback_plan(query, reason="provider_free_initial", body=request)
    context = prepare_local_search(
        plan=plan, body=request, recall_filters={}, market="US", platforms=plan["platforms"],
    )
    return plan, context


@pytest.mark.parametrize("objective", ["prospective_growth", "existing_evidence"])
def test_natural_macro_product_request_reaches_real_runtime_with_both_scenes(objective):
    plan, context = _prepare("美国全平台微距与产品摄影创作者", objective=objective)
    cells = context["query_cells"]
    assert len(cells) == 1
    cell = cells[0]
    assert set(cell["required_scene_terms"]) == {"macro", "product_photography"}
    assert cell["scene_match_mode"] == "all"
    assert cell["required_role_terms"] == ["content creator"]
    assert "全平台" not in cell["primary_query"]
    assert cell["platforms"] == plan["platforms"]
    assert cell["platforms"] == ["youtube", "instagram", "tiktok"]
    for query in [cell["primary_query"], *cell["fallback_queries"]]:
        check = validate_query_cell_execution(cell, query)
        assert check["execution_allowed"] is True
        assert check["scene"]["status"] == "covered"
        assert check["role"]["status"] == "covered"
        assert check["candidate_qualification"] == "not_evaluated"
    assert plan["search_brief"]["product"]["resolved_sku"] == ""
    assert context["resolved_product"] == {}


@pytest.mark.parametrize("query,scenes,role", [
    ("product photography content creators", {"product_photography"}, "content creator"),
    ("产品摄影讲师", {"product_photography"}, "educator"),
    ("night photography content creators", {"night"}, "content creator"),
    ("macro photography educators", {"macro"}, "educator"),
    ("film photography content creators", {"film_photography"}, "content creator"),
    ("night street content creators", {"night", "street"}, "content creator"),
    ("macro photography and product photography creators", {"macro", "product_photography"}, "content creator"),
    ("product photography and off-camera lighting educators", {"product_photography", "lighting"}, "educator"),
])
@pytest.mark.parametrize("objective", ["prospective_growth", "existing_evidence"])
def test_role_replacement_and_compounds_keep_controlled_scenes(query, scenes, role, objective):
    _, context = _prepare(query, objective=objective)
    cell = context["query_cells"][0]
    assert set(cell["required_scene_terms"]) == scenes
    assert role in cell["required_role_terms"]
    for outbound in [cell["primary_query"], *cell["fallback_queries"]]:
        validation = validate_query_cell_execution(cell, outbound)
        assert validation["status"] == "covered"
        assert validation["candidate_qualification"] == "not_evaluated"


@pytest.mark.parametrize("query", [
    "为微距镜头找产品摄影创作者", "微距镜头与产品摄影创作者", "不要微距摄影，找产品摄影创作者",
])
def test_shared_suffix_does_not_turn_product_or_negated_words_into_macro_scene(query):
    _, context = _prepare(query)
    assert context["query_cells"]
    assert all("macro" not in cell["required_scene_terms"] for cell in context["query_cells"])


@pytest.mark.parametrize("query,scenes", [
    ("夜景与产品摄影创作者", {"night", "product_photography"}),
    ("微距或产品摄影创作者", {"macro", "product_photography"}),
])
def test_shared_suffix_is_not_specific_to_one_request_or_connector(query, scenes):
    _, context = _prepare(query)
    assert len(context["query_cells"]) == 2
    assert {scene for cell in context["query_cells"] for scene in cell["required_scene_terms"]} == scenes
    assert all(validate_query_cell_execution(cell, cell["primary_query"])["status"] == "covered"
               for cell in context["query_cells"])


def test_explicit_scene_filter_keeps_scope_and_fallback_semantics():
    _, context = _prepare("find creators", body={
        "industries": ["product photography content creators"], "platforms": ["instagram"],
        "filters": {"followers_min": 5000, "followers_max": 50000},
    })
    cell = context["query_cells"][0]
    assert cell["segment_source"] == "operator_filter"
    assert cell["platforms"] == ["instagram"]
    assert context["followers_min"] == 5000 and context["followers_max"] == 50000
    assert all(validate_query_cell_execution(cell, value)["status"] == "covered"
               for value in [cell["primary_query"], *cell["fallback_queries"]])


def test_correct_queries_and_exact_unknown_scene_are_not_expanded():
    for query, expected in [
        ("street photographers", "street photographer"),
        ("product photographers", "product photographer"),
        ("dental photographers", "dental photographer"),
    ]:
        cells = targeted_search_contract.build_query_cells(
            query=query, body={}, product=None, product_focus=[], platforms=[],
        )
        assert cells[0]["primary_query"] == expected


def test_unresolved_family_request_does_not_invent_a_default_sku():
    plan, context = _prepare("为 EVO 镜头系列找产品摄影创作者")
    assert plan["search_brief"]["product"]["resolved_sku"] == ""
    assert context["resolved_product"] == {}
    assert all(cell["brand_or_model_required"] is False for cell in context["query_cells"])
    assert all("evo" not in cell["primary_query"].lower() for cell in context["query_cells"])


def test_family_product_shape_stays_unselected_and_has_no_brand_gate():
    family = {
        "sku": "", "model_name": "Viltrox 35mm F1.2 LAB",
        "marketing_name": "Viltrox 35mm F1.2 LAB", "category_main": "Lens",
        "category_detail": "Auto Focus Lens", "series": "LAB",
        "resolution_kind": "focal_family", "resolution_basis": "focal_aperture_family",
        "focal_mm": 35, "focal_family_size": 2, "focal_family_mounts": ["FE-mount", "Z-mount"],
        "focal_family_skus": ["AF-35MM-F12-LAB-FE", "AF-35MM-F12-LAB-Z"],
    }
    before = deepcopy(family)
    cells = targeted_search_contract.build_query_cells(
        query="找 35mm LAB 产品摄影创作者", body={}, product=family,
        product_focus=[], platforms=["youtube", "instagram"],
    )
    assert len(cells) == 1
    cell = cells[0]
    assert cell["required_scene_terms"] == ["product_photography"]
    assert cell["brand_or_model_required"] is False
    assert cell["brand_or_model_ranking_weight"] == 0
    assert not {"viltrox", "35mm", "f1.2", "lab"}.intersection(cell["primary_query"].lower().split())
    assert validate_query_cell_execution(cell, cell["primary_query"])["status"] == "covered"
    assert family == before and family["sku"] == ""


@pytest.mark.parametrize("query,scenes", [
    ("night content creator", ["night"]),
    ("product content creator", ["product_photography"]),
    ("macro educator", ["macro"]),
    ("unknown niche creator", ["unknown_niche"]),
    ("street-style creator", ["street"]),
])
def test_scene_phrase_preservation_is_idempotent_and_does_not_add_photographer_role(query, scenes):
    result = preserve_controlled_scene_phrases(query, scenes)
    assert preserve_controlled_scene_phrases(result, scenes) == result
    assert "photographer" not in result
    if scenes == ["unknown_niche"]:
        assert result == query


def test_bad_executed_query_is_still_rejected_not_repaired_by_validator():
    _, context = _prepare("product photography content creators")
    cell = context["query_cells"][0]
    before = deepcopy(cell)
    check = validate_query_cell_execution(cell, "product content creator")
    assert check["execution_allowed"] is False
    assert "scene_terms_missing_from_executed_query" in check["issues"]
    assert cell == before
