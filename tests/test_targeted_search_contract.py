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
        assert "tutorial" not in lowered
        assert "camera gear" not in lowered
        assert "on-camera flash" not in lowered
        assert cell["discovery_intent"] == "operator_people_intent"
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
    scene_group = next(group for group in locked["groups"] if group["kind"] == "scene")
    assert scene_group["canonical_term"] == "motorsport"
    assert "racing" in scene_group["aliases"]
    role_group = next(group for group in locked["groups"] if group["kind"] == "role")
    assert role_group["canonical_term"] == "photographer"
    brief = plan["search_brief"]
    assert brief["search_spec_version"] == "targeted_search_v2"
    assert brief["objective"] == "prospective_growth"
    assert brief["product"] == {
        "resolved_sku": FLASH["sku"],
        "capability": "on-camera flash",
        "evidence_required": True,
        "evidence_basis": "resolved_product",
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
    assert primary == "portrait content creator"
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

    assert [cell["segment"] for cell in cells] == ["motorsport", "food", "wedding"]
    assert [cell["primary_query"] for cell in cells] == [
        "motorsport photographer",
        "food chef content creator",
        "wedding event content creator",
    ]
    assert cells[2]["required_scene_terms"] == ["wedding", "event"]
    assert cells[2]["scene_match_mode"] == "all"
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
        "motorsport", "sports", "stage", "wedding", "wildlife", "portrait",
    ]
    assert all(cell["platforms"] == [] for cell in cells)
    assert all(cell["follower_filter"]["locked"] is False for cell in cells)
    for cell in cells:
        primary = cell["primary_query"].lower()
        assert "tutorial camera gear" not in primary
        assert "telephoto portrait lens" not in primary
        assert not {"viltrox", "135mm", "f1", "lab"}.intersection(primary.split())
        assert cell["locked_term_groups"]["groups"][0]["canonical_term"] == (
            "telephoto portrait lens"
        )


def test_connectors_preserve_any_all_and_shared_role_semantics() -> None:
    def cells(query: str) -> list[dict]:
        return contract.build_query_cells(
            query=query,
            body={},
            product=None,
            product_focus=[],
            platforms=[],
        )

    alternatives = cells("找婚礼或人像摄影师")
    assert [(cell["segment"], cell["scene_match_mode"]) for cell in alternatives] == [
        ("wedding", "any"),
        ("portrait", "any"),
    ]

    implicit_intersection = cells("找会拍街头和夜景的摄影师")
    assert len(implicit_intersection) == 1
    assert implicit_intersection[0]["required_scene_terms"] == ["street", "night"]
    assert implicit_intersection[0]["scene_match_mode"] == "all"
    assert implicit_intersection[0]["primary_query"] == "street night photographer"

    explicit_intersection = cells("找同时做赛车和美食的创作者")
    assert len(explicit_intersection) == 1
    assert explicit_intersection[0]["required_scene_terms"] == ["motorsport", "food"]
    assert explicit_intersection[0]["scene_match_mode"] == "all"

    independent_list = cells("找赛车和美食创作者")
    assert [cell["segment"] for cell in independent_list] == ["motorsport", "food"]

    shared_role = cells("找拍篮球比赛和场边故事的摄影师")
    assert [(cell["segment"], cell["primary_query"]) for cell in shared_role] == [
        ("sports", "basketball photographer")
    ]

    positive_after_negation = cells(
        "Find portrait photographers avoiding weddings and shooting at night"
    )
    assert len(positive_after_negation) == 1
    assert positive_after_negation[0]["required_scene_terms"] == ["portrait", "night"]
    assert positive_after_negation[0]["scene_match_mode"] == "all"

    relative_clause = cells(
        "Find wedding filmmakers who make documentary films"
    )
    assert len(relative_clause) == 1
    assert relative_clause[0]["required_scene_terms"] == ["wedding", "documentary"]
    assert relative_clause[0]["scene_match_mode"] == "all"
    assert relative_clause[0]["primary_query"] == "wedding documentary filmmaker"

    not_only = cells("Find photographers who shoot not only weddings but also portraits")
    assert len(not_only) == 1
    assert not_only[0]["required_scene_terms"] == ["wedding", "portrait"]
    assert not_only[0]["scene_match_mode"] == "all"


@pytest.mark.parametrize(
    ("query", "segment", "primary", "role"),
    [
        ("Find off-camera lighting educators", "lighting", "off-camera flash educator", "educator"),
        ("Find basketball storytellers", "sports", "basketball storyteller", "storyteller"),
        ("Find basketball sideline reporters", "sports", "basketball sideline reporter", "reporter"),
        ("Find portrait retouchers", "portrait", "portrait retoucher", "retoucher"),
        ("Find fashion stylists", "fashion", "fashion stylist", "stylist"),
    ],
)
def test_explicit_people_occupation_is_never_replaced_by_scene_default(
    query: str,
    segment: str,
    primary: str,
    role: str,
) -> None:
    [cell] = contract.build_query_cells(
        query=query,
        body={},
        product=None,
        product_focus=[],
        platforms=[],
    )

    assert cell["segment"] == segment
    assert cell["primary_query"] == primary
    assert cell["required_role_terms"] == [role]
    role_group = next(
        group for group in cell["locked_term_groups"]["groups"]
        if group["kind"] == "role"
    )
    assert role_group["canonical_term"] == role


@pytest.mark.parametrize(
    ("query", "primary", "scenes", "role"),
    [
        ("Find dental photographers", "dental photographer", ["dental"], "photographer"),
        ("Find underwater photographers", "underwater photographer", ["underwater"], "photographer"),
        ("Find architecture photographers", "architecture photographer", ["architecture"], "photographer"),
        ("Find aerial photographers", "aerial photographer", ["aerial"], "photographer"),
        ("Find newborn photographers", "newborn photographer", ["newborn"], "photographer"),
        ("Find medical photographers", "medical photographer", ["medical"], "photographer"),
        ("Find London photographers", "london photographer", ["london"], "photographer"),
        ("Find camera assistants", "camera assistant", [], "camera assistant"),
        (
            "Find Atlanta portrait photographers",
            "atlanta portrait photographer",
            ["atlanta", "portrait"],
            "photographer",
        ),
    ],
)
def test_explicit_unregistered_people_intent_is_preserved_exactly(
    query: str,
    primary: str,
    scenes: list[str],
    role: str,
) -> None:
    [cell] = contract.build_query_cells(
        query=query,
        body={},
        product=None,
        product_focus=[],
        platforms=[],
    )

    assert cell["primary_query"] == primary
    assert cell["required_scene_terms"] == scenes
    assert cell["scene_match_mode"] == ("all" if len(scenes) > 1 else "any")
    assert cell["required_role_terms"] == [role]
    exact_groups = [
        group for group in cell["locked_term_groups"]["groups"]
        if group["canonical_term"] in {*scenes, role}
    ]
    assert exact_groups
    for group in exact_groups:
        if group["canonical_term"] not in {"portrait", "photographer"}:
            assert group["alias_policy"] == "exact_only"
            assert group["aliases"] == [group["canonical_term"]]


@pytest.mark.parametrize(
    ("query", "primary", "scene", "role"),
    [
        ("Find production designers", "production designers", None, "production designer"),
        ("Find drone pilots", "drone pilots", None, "drone pilot"),
        ("Find nature storytellers", "nature storyteller", "nature", "storyteller"),
        ("Find sports commentators", "sports commentator", "sports", "commentator"),
        ("Find wedding planners", "wedding planner", "wedding", "planner"),
    ],
)
def test_unmapped_people_roles_never_fall_back_to_an_invented_generic_role(
    query: str,
    primary: str,
    scene: str | None,
    role: str,
) -> None:
    [cell] = contract.build_query_cells(
        query=query, body={}, product=None, product_focus=[], platforms=[]
    )
    assert cell["primary_query"] == primary
    assert cell["required_scene_terms"] == ([scene] if scene else [])
    assert cell["required_role_terms"] == [role]
    role_group = next(
        group for group in cell["locked_term_groups"]["groups"]
        if group["kind"] == "role"
    )
    assert role_group["canonical_term"] == role
    if role not in {"storyteller"}:
        assert role_group["alias_policy"] == "exact_only"


@pytest.mark.parametrize("query", ["Find best photographers", "Find new photographers"])
def test_ranking_or_recency_words_do_not_become_exact_scenes(query: str) -> None:
    [cell] = contract.build_query_cells(
        query=query, body={}, product=None, product_focus=[], platforms=[]
    )
    assert cell["primary_query"] == "professional photographer"
    assert cell["required_scene_terms"] == []


@pytest.mark.parametrize(
    ("query", "primary", "role"),
    [
        ("Find photographers", "professional photographer", "photographer"),
        ("Find 85 photographers", "professional photographer", "photographer"),
        ("Find directors", "director", "director"),
        ("Find bloggers", "blogger", "content creator"),
        ("Find influencers", "influencer", "content creator"),
        ("Find content creators", "content creator", "content creator"),
        ("找摄影师", "professional photographer", "photographer"),
        ("找创作者", "content creator", "content creator"),
    ],
)
def test_pure_people_roles_never_invent_a_scene(
    query: str,
    primary: str,
    role: str,
) -> None:
    [cell] = contract.build_query_cells(
        query=query, body={}, product=None, product_focus=[], platforms=[]
    )

    assert cell["primary_query"] == primary
    assert cell["required_scene_terms"] == []
    assert cell["required_role_terms"] == [role]
    assert cell["required_evidence_groups"] == ["people_role", "market_activation"]
    assert [group["kind"] for group in cell["locked_term_groups"]["groups"]] == ["role"]


@pytest.mark.parametrize(
    "query",
    [
        "Find photographers who never shoot weddings but shoot portraits",
        "Find non-wedding portrait photographers",
        "找不接婚礼的人像摄影师",
    ],
)
def test_negated_wedding_never_becomes_a_positive_people_constraint(query: str) -> None:
    [cell] = contract.build_query_cells(
        query=query, body={}, product=None, product_focus=[], platforms=[]
    )

    assert cell["primary_query"] == "portrait photographer"
    assert cell["required_scene_terms"] == ["portrait"]
    assert cell["required_role_terms"] == ["photographer"]


def test_platform_word_is_not_an_exact_scene_and_two_occupations_are_all() -> None:
    [platform_cell] = contract.build_query_cells(
        query="Find top YouTube photographers",
        body={}, product=None, product_focus=[], platforms=["youtube"],
    )
    assert platform_cell["required_scene_terms"] == []
    assert platform_cell["platforms"] == ["youtube"]

    [multi_role] = contract.build_query_cells(
        query="Find photographers who are also filmmakers",
        body={}, product=None, product_focus=[], platforms=[],
    )
    assert multi_role["primary_query"] == "photographer filmmaker"
    assert multi_role["required_role_terms"] == ["photographer", "filmmaker"]
    assert multi_role["role_match_mode"] == "all"


def test_exact_scene_keeps_a_second_explicit_location() -> None:
    [cell] = contract.build_query_cells(
        query="Find architecture photographers in London",
        body={}, product=None, product_focus=[], platforms=[],
    )

    assert cell["primary_query"] == "architecture london photographer"
    assert cell["required_scene_terms"] == ["architecture", "london"]
    assert cell["required_role_terms"] == ["photographer"]
    assert cell["scene_match_mode"] == "all"


def test_product_launch_campaign_is_not_reduced_to_bare_product() -> None:
    [cell] = contract.build_query_cells(
        query="Find photographers for a product launch campaign",
        body={}, product=None, product_focus=[], platforms=[],
    )

    assert cell["primary_query"] == "product launch photographer"
    assert cell["required_scene_terms"] == ["product_launch"]
    assert cell["required_role_terms"] == ["photographer"]
    scene_group = next(
        group for group in cell["locked_term_groups"]["groups"]
        if group["kind"] == "scene"
    )
    assert scene_group["canonical_term"] == "product_launch"
    assert "product launch" in scene_group["aliases"]
    assert "product" not in scene_group["aliases"]


def test_negated_role_never_becomes_a_positive_multi_role_requirement() -> None:
    [cell] = contract.build_query_cells(
        query="Find creators who are not photographers but filmmakers",
        body={}, product=None, product_focus=[], platforms=[],
    )

    assert cell["required_role_terms"] == ["content creator", "filmmaker"]
    assert "photographer" not in cell["required_role_terms"]
    assert cell["role_match_mode"] == "all"


def test_operator_capability_without_sku_is_secondary_but_still_auditable() -> None:
    monitor = contract.build_query_cells(
        query="Find camera monitor reviewers",
        body={},
        product=None,
        product_focus=["camera monitor", "camera gear reviewer"],
        platforms=[],
    )[0]
    assert monitor["primary_query"] == "camera monitor reviewer"
    assert monitor["product_evidence_required"] is True
    assert monitor["product_evidence_basis"] == "operator_capability"
    assert monitor["locked_term_groups"]["groups"][0]["canonical_term"] == "camera monitor"

    lighting = contract.build_query_cells(
        query="找 300W 闪光灯的评测博主",
        body={},
        product=None,
        product_focus=["300W", "portable lighting", "gear reviewer"],
        platforms=[],
    )[0]
    assert lighting["primary_query"] == "300W studio lighting reviewer"
    assert lighting["product_evidence_basis"] == "operator_capability"
    assert lighting["locked_term_groups"]["groups"][0]["canonical_term"] == (
        "300w studio lighting"
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
