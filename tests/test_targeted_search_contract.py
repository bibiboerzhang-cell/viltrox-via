from __future__ import annotations

import pytest

from app.domains.kol import smart_query_planner
from app.domains.kol import targeted_search_contract as contract


FLASH = {
    "sku": "VINTAGE-Z1-PRO-TTL-RETRO-ON-CAMERA-FLASH",
    "model_name": "Vintage Z1 Pro",
    "marketing_name": "Viltrox Vintage Z1 Pro TTL Retro On-Camera Flash",
    "category_main": "Flash",
    "category_detail": "On-Camera Flash",
    "series": "Vintage",
    "description": "TTL retro on-camera flash for portrait and event photography",
}

LENS_135 = {
    "sku": "AF-135MM-F18-LAB-FE",
    "model_name": "AF 135mm F1.8 LAB",
    "marketing_name": "Viltrox AF 135mm F1.8 LAB Full-Frame Lens",
    "category_main": "Lens",
    "category_detail": "Full-Frame Prime Lens",
    "series": "LAB",
    "description": "Professional telephoto prime for portrait photography",
}


def _plan(query: str, *, body: dict | None = None) -> dict:
    return smart_query_planner._normalise_plan(
        query,
        {
            "search_query": "Viltrox Z1 Pro portrait lighting creator",
            "search_queries": ["Viltrox Z1 Pro portrait lighting"],
            "product_focus": ["portrait photographer", "lighting educator"],
            "platforms": ["youtube", "instagram"],
        },
        {"provider": "google", "model": "gemini", "status": "success"},
        FLASH,
        body or {},
    )


def test_default_objective_is_prospective_growth() -> None:
    assert contract.normalize_objective() == "prospective_growth"
    plan = _plan("给 Z1 Pro 找赛车和餐饮创作者")
    assert plan["objective"] == "prospective_growth"
    assert plan["product_anchor_required"] is False
    assert plan["brand_or_model_ranking_weight"] == 0
    assert plan["ranking_claim_status"] == "descriptive_only"
    assert "viltrox" not in plan["search_query"].lower()
    assert "z1" not in plan["search_query"].lower()
    assert " pro " not in f" {plan['search_query'].lower()} "


def test_explicit_industries_become_locked_independent_first_round_cells() -> None:
    plan = _plan("给 Z1 Pro 找赛车和厨师餐饮创作者")
    cells = plan["query_cells"]
    assert [cell["segment"] for cell in cells] == ["motorsport", "food"]
    assert plan["authoritative_query_field"] == "query_cells"
    assert plan["first_round_strategy"] == "independent_query_cells"
    for cell in cells:
        assert cell["segment_locked"] is True
        assert cell["segment_source"] == "operator_text"
        assert cell["round"] == 1
        assert 10 <= cell["raw_limit"] <= 15
        assert cell["independent_raw_quota"] is True
        assert cell["brand_or_model_required"] is False
        assert cell["brand_or_model_ranking_weight"] == 0
        lowered = cell["primary_query"].lower()
        assert "viltrox" not in lowered
        assert "z1" not in lowered
        assert "tutorial" in lowered
        assert "camera gear" in lowered
        assert "on-camera flash" not in lowered
        assert cell["discovery_intent"] == "segment_creator_education_gear"
        assert cell["capability_in_primary_query"] is False
        assert cell["capability_verification_policy"] == "post_retrieval_locked_evidence"
    assert "motorsport" in cells[0]["primary_query"]
    assert "food" in cells[1]["primary_query"]
    locked = cells[0]["locked_term_groups"]
    assert locked["schema"] == "targeted_locked_term_groups_v1"
    assert locked["version"] == 1
    assert locked["source"] == "server_targeted_contract"
    assert locked["groups"][0]["canonical_term"] == "on-camera flash"
    assert "speedlight" in locked["groups"][0]["aliases"]
    assert locked["groups"][1]["canonical_term"] == "motorsport"
    assert "racing" in locked["groups"][1]["aliases"]
    brief = plan["search_brief"]
    assert brief["search_spec_version"] == "targeted_search_v2"
    assert brief["objective"] == "prospective_growth"
    assert brief["product"] == {
        "resolved_sku": FLASH["sku"],
        "capability": "on-camera flash",
        "brand_or_model_required": False,
    }
    assert brief["explicit_segments"] == plan["explicit_segments"]
    assert brief["follower_filter"] == plan["follower_filter"]
    assert brief["platforms"] == ["youtube", "instagram"]
    assert brief["claim_status"] == "descriptive_only"
    assert brief["authoritative_query_field"] == "query_cells"
    assert brief["query_cells"] == cells


def test_operator_industry_fields_are_locked_and_raw_limit_is_clamped() -> None:
    plan = _plan(
        "find creators for the launch",
        body={
            "industries": ["racing", "dental photography"],
            "first_round_raw_limit": 99,
        },
    )
    cells = plan["query_cells"]
    assert [cell["segment"] for cell in cells] == ["motorsport", "dental_photography"]
    assert all(cell["segment_source"] == "operator_filter" for cell in cells)
    assert all(cell["segment_locked"] is True for cell in cells)
    assert all(cell["raw_limit"] == 15 for cell in cells)


@pytest.mark.parametrize(
    ("industries", "expected_raw_limit"),
    [
        (["racing"], 15),
        (["racing", "food"], 15),
        (["racing", "food", "wedding"], 10),
        (["racing", "food", "wedding", "pet"], 10),
    ],
)
def test_default_raw_limit_allocates_thirty_targets_across_cells(
    industries: list[str],
    expected_raw_limit: int,
) -> None:
    plan = _plan("find creators for the launch", body={"industries": industries})
    assert len(plan["query_cells"]) == len(industries)
    assert all(cell["raw_limit"] == expected_raw_limit for cell in plan["query_cells"])


def test_explicit_operator_raw_limit_still_wins_over_default_allocation() -> None:
    plan = _plan(
        "find creators for the launch",
        body={"industries": ["racing", "food", "wedding"], "raw_limit": 13},
    )
    assert all(cell["raw_limit"] == 13 for cell in plan["query_cells"])


def test_operator_platform_filter_overrides_planner_platforms_in_plan_and_cells() -> None:
    plan = _plan(
        "给 Z1 Pro 找赛车和餐饮创作者",
        body={"platforms": ["youtube"]},
    )

    assert plan["platforms"] == ["youtube"]
    assert plan["search_brief"]["platforms"] == ["youtube"]
    assert {tuple(cell["platforms"]) for cell in plan["query_cells"]} == {("youtube",)}


def test_135mm_prospective_query_defers_capability_to_locked_verification() -> None:
    cells = contract.build_query_cells(
        query="find prospective portrait creators for the 135mm LAB launch",
        body={},
        product=LENS_135,
        product_focus=["135mm F1.8 LAB portrait photographer"],
        platforms=["youtube"],
        legacy_queries=["Viltrox AF 135mm F1.8 LAB portrait lens creator"],
    )
    assert len(cells) == 1
    primary = cells[0]["primary_query"].lower()
    assert primary == "portrait photographer tutorial camera gear"
    assert "telephoto portrait lens" not in primary
    assert "viltrox" not in primary
    assert "135mm" not in primary
    assert "f1.8" not in primary
    assert "lab" not in primary
    assert cells[0]["brand_or_model_required"] is False
    assert cells[0]["locked_term_groups"]["groups"][0]["canonical_term"] == (
        "telephoto portrait lens"
    )


def test_135mm_existing_evidence_query_keeps_brand_and_model_anchor() -> None:
    cells = contract.build_query_cells(
        query="find creators already using the 135mm LAB",
        body={"objective": "existing_evidence"},
        product=LENS_135,
        product_focus=["portrait photographer"],
        platforms=["youtube"],
        legacy_queries=["Viltrox AF 135mm F1.8 LAB portrait lens creator"],
    )
    assert len(cells) == 1
    primary = cells[0]["primary_query"].lower()
    assert "viltrox" in primary
    assert "135mm" in primary
    assert "lab" in primary
    assert cells[0]["brand_or_model_required"] is True
    assert cells[0]["capability_in_primary_query"] is True
    assert cells[0]["capability_verification_policy"] == (
        "anchored_query_and_locked_evidence"
    )


def test_existing_evidence_mode_preserves_anchored_queries() -> None:
    plan = _plan(
        "find creators already using Z1 Pro",
        body={"objective": "existing_evidence"},
    )
    assert plan["objective"] == "existing_evidence"
    assert plan["product_anchor_required"] is True
    assert plan["query_cells"]
    for cell in plan["query_cells"]:
        lowered = cell["primary_query"].lower()
        assert cell["brand_or_model_required"] is True
        assert "viltrox" in lowered
        assert "z1" in lowered


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("粉丝10万到50万", 100_000, 500_000),
        ("3千-10万粉丝", 3_000, 100_000),
        ("50k to 1m followers", 50_000, 1_000_000),
        ("至少5w粉丝", 50_000, None),
        ("followers under 100k", None, 100_000),
    ],
)
def test_chinese_and_english_follower_ranges(text: str, low: int | None, high: int | None) -> None:
    parsed = contract.parse_follower_range(text)
    assert parsed["followers_min"] == low
    assert parsed["followers_max"] == high
    assert parsed["source"] == "operator_text"
    assert parsed["locked"] is True
    assert parsed["valid"] is True


def test_explicit_filter_range_wins_over_text_and_reaches_every_cell() -> None:
    body = {"filters": {"followers_min": "50k", "followers_max": "1m"}}
    plan = _plan("找10万到20万粉丝的赛车摄影师", body=body)
    assert plan["follower_filter"] == {
        "followers_min": 50_000,
        "followers_max": 1_000_000,
        "source": "operator_filter",
        "locked": True,
        "valid": True,
        "error": "",
        "matched_text": "",
    }
    assert all(cell["follower_filter"] == plan["follower_filter"] for cell in plan["query_cells"])


def test_inverted_follower_range_fails_closed_instead_of_swapping_values() -> None:
    plan = _plan(
        "find racing creators",
        body={"filters": {"followers_min": 500_000, "followers_max": 50_000}},
    )
    assert plan["status"] == "needs_clarification"
    assert plan["reason"] == "followers_min_exceeds_max"
    assert plan["query_cells"] == []
    assert plan["include_new_discovery"] is False


def test_year_range_is_not_misread_as_a_follower_filter() -> None:
    parsed = contract.parse_follower_range("videos published in 2024-2026")
    assert parsed["source"] == "unspecified"
    assert parsed["followers_min"] is None
    assert parsed["followers_max"] is None


def test_segment_matching_uses_word_boundaries() -> None:
    assert contract.extract_explicit_segments("competitor research") == []


def test_locked_term_projection_rebuilds_aliases_and_drops_unknown_expansion() -> None:
    locked = contract.build_locked_term_groups(
        capability="on-camera flash",
        segment="motorsport",
    )
    locked["groups"][0]["aliases"].append("zoomlight")
    locked["groups"][0]["use_suitability_terms"].append("generic creator")
    locked["groups"][1]["aliases"].append("trackshoot")

    projected = contract.project_locked_term_groups(locked)

    assert projected is not None
    assert "zoomlight" not in projected["groups"][0]["aliases"]
    assert "generic creator" not in projected["groups"][0]["use_suitability_terms"]
    assert "trackshoot" not in projected["groups"][1]["aliases"]
    assert contract.project_locked_term_groups({
        "schema": "client_locked_terms_v1",
        "version": 1,
        "source": "client",
        "groups": locked["groups"],
    }) is None


def test_z1_first_round_covers_requested_workflows_without_brand_or_model_terms() -> None:
    cells = contract.build_query_cells(
        query="给 Z1 Pro 找赛车汽车、厨师餐饮美食、婚礼活动创作者，粉丝5万到50万",
        body={"platforms": ["youtube", "instagram"]},
        product=FLASH,
        product_focus=[],
        platforms=["youtube", "instagram"],
    )

    assert [cell["segment"] for cell in cells] == [
        "motorsport", "food", "wedding", "event",
    ]
    assert [cell["primary_query"] for cell in cells] == [
        "motorsport photographer tutorial camera gear",
        "food photographer tutorial camera gear",
        "wedding photographer tutorial camera gear",
        "event photographer tutorial camera gear",
    ]
    for cell in cells:
        primary = cell["primary_query"].lower()
        assert not {"viltrox", "z1", "vintage", "pro"}.intersection(primary.split())
        assert cell["platforms"] == ["youtube", "instagram"]
        assert cell["follower_filter"]["followers_min"] == 50_000
        assert cell["follower_filter"]["followers_max"] == 500_000
        assert cell["follower_filter"]["locked"] is True


def test_135mm_first_round_covers_each_requested_use_case_without_product_identity() -> None:
    cells = contract.build_query_cells(
        query="给 135mm 找体育赛车、舞台演唱会、婚礼、野生动物和人像摄影师",
        body={},
        product=LENS_135,
        product_focus=[],
        platforms=[],
    )

    assert [cell["segment"] for cell in cells] == [
        "motorsport", "wedding", "stage", "wildlife", "portrait", "sports",
    ]
    assert all(cell["platforms"] == [] for cell in cells)
    assert all(cell["follower_filter"]["locked"] is False for cell in cells)
    for cell in cells:
        primary = cell["primary_query"].lower()
        assert "tutorial camera gear" in primary
        assert "telephoto portrait lens" not in primary
        assert not {"viltrox", "135mm", "f1", "lab"}.intersection(primary.split())
        assert cell["locked_term_groups"]["groups"][0]["canonical_term"] == (
            "telephoto portrait lens"
        )


def test_static_use_map_requires_specific_creator_workflow_not_generic_role() -> None:
    flash_terms = set(contract.controlled_capability_use_terms_for("on-camera flash"))
    assert {
        "motorsport photographer",
        "automotive photographer",
        "food photographer",
        "restaurant photographer",
        "wedding photographer",
        "event photographer",
    } <= flash_terms
    assert "chef" not in flash_terms
    assert "creator" not in flash_terms
    assert "photographer" not in flash_terms

    telephoto_terms = set(contract.controlled_capability_use_terms_for("telephoto portrait lens"))
    assert {
        "sports photographer",
        "motorsport photographer",
        "concert photographer",
        "wedding photographer",
        "wildlife photographer",
        "portrait photographer",
    } <= telephoto_terms
