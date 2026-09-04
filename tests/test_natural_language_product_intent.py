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
    assert cells[0]["primary_query"] == "portrait photographer"


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
    assert plan["query_cells"][0]["primary_query"] == "film photographer"


@pytest.mark.parametrize(
    ("query", "segment", "primary_query"),
    [
        ("找街拍摄影师", "street", "street photographer"),
        ("找风光摄影师", "landscape", "landscape photographer"),
        ("找时尚摄影师", "fashion", "fashion photographer"),
        ("找美食短视频博主", "food", "food content creator"),
        ("找婚礼摄像师", "wedding", "wedding videographer"),
    ],
)
def test_people_and_content_format_drive_the_first_round_query(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    segment: str,
    primary_query: str,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})

    assert plan["status"] != "needs_clarification"
    assert plan.get("resolved_product") is None
    assert [(cell["segment"], cell["primary_query"]) for cell in plan["query_cells"]] == [
        (segment, primary_query)
    ]


def test_generic_camera_body_request_keeps_the_requested_photographer_role(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free("找 Canon R5 Pro 摄影师", body={})

    assert plan["status"] != "needs_clarification"
    assert plan["query_cells"][0]["primary_query"] == "professional photographer"
    assert "Professional photographers" in plan["target_persona"]


def test_product_specs_never_replace_the_human_target_persona(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(
        "给35焦段1.2光圈找婚礼人像摄影师",
        body={},
    )

    assert "Wedding portrait photographers" in plan["target_persona"]
    assert "portrait lens" in plan["target_persona"]
    assert "category:" not in plan["target_persona"]
    assert "$" not in plan["target_persona"]
    assert "SKU" not in plan["target_persona"]


@pytest.mark.parametrize(
    ("query", "expected_cells"),
    [
        (
            "找纪录片导演和婚礼摄影师，不限器材",
            [("documentary", "documentary director"), ("wedding", "wedding photographer")],
        ),
        (
            "找婚礼视频团队里会用外接监视器的摄影指导",
            [("wedding", "wedding cinematographer")],
        ),
        (
            "找美食摄影师和餐厅视频创作者",
            [("food", "food photographer"), ("food", "food content creator")],
        ),
        (
            "EPIC整套找纪录片导演和摄影指导",
            [("documentary", "documentary director"), ("cinematography", "cinematographer")],
        ),
    ],
)
def test_each_people_segment_keeps_its_own_local_role(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_cells: list[tuple[str, str]],
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    actual = [
        (cell["segment"], cell["primary_query"].removesuffix(" tutorial camera gear"))
        for cell in plan["query_cells"]
    ]

    assert actual == expected_cells


@pytest.mark.parametrize(
    ("query", "expected_segments"),
    [
        ("Find natural-light portrait photographers", ["portrait"]),
        ("找擅长街头纪实、城市夜景的摄影师", ["street"]),
        ("找拍篮球比赛和场边故事的摄影师", ["sports"]),
        ("找专业拍鸟摄影师，长焦经验丰富", ["wildlife"]),
        ("Find real-estate and interior photographers", ["real_estate"]),
    ],
)
def test_common_people_phrases_map_to_controlled_scene_intents(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_segments: list[str],
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})

    assert [cell["segment"] for cell in plan["query_cells"]] == expected_segments
    for cell in plan["query_cells"]:
        groups = {group["kind"]: group for group in cell["locked_term_groups"]["groups"]}
        assert groups["scene"]["alias_policy"] == "static_allowlist"
    if "natural-light" in query:
        assert all(
            group["canonical_term"] != "on-camera flash"
            for cell in plan["query_cells"]
            for group in cell["locked_term_groups"]["groups"]
        )
    if "街头纪实" in query:
        assert plan["query_cells"][0]["required_scene_terms"] == ["street", "night"]
        assert plan["query_cells"][0]["scene_match_mode"] == "all"


def test_vague_quality_only_creator_request_asks_for_people_context_not_sku(
    catalog: None,
) -> None:
    plan = smart_query_planner.plan_text_query_provider_free("找一些靠谱达人", body={})

    assert plan["status"] == "needs_clarification"
    assert plan["reason"] == "missing_people_intent"
    assert "行业" in plan["clarification"]["message"]
    assert "不需要输入 SKU" in plan["clarification"]["message"]


def test_vague_people_request_is_guarded_before_provider_and_cache(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_called = False
    cache_called = False

    def _provider(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal provider_called
        provider_called = True
        return {}

    def _cache(*_args: object, **_kwargs: object) -> None:
        nonlocal cache_called
        cache_called = True
        return None

    monkeypatch.setattr(smart_query_planner.llm_gateway, "invoke_json", _provider)
    monkeypatch.setattr(
        "app.domains.analysis.cache_repo.get_analysis_cache_entry",
        _cache,
    )

    direct = smart_query_planner._plan_text_query_impl("推荐几个靠谱的创作者", body={})
    cached = smart_query_planner.plan_text_query("推荐几个靠谱的创作者", body={})

    assert direct["reason"] == "missing_people_intent"
    assert cached["reason"] == "missing_people_intent"
    assert provider_called is False
    assert cache_called is False


def test_vague_text_can_use_explicit_people_filter_without_a_sku(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(
        "找一些靠谱的人",
        body={"filters": {"industries": ["wedding"]}},
    )

    assert plan["status"] != "needs_clarification"
    assert plan["query_cells"][0]["segment"] == "wedding"
    assert plan["query_cells"][0]["primary_query"].startswith("wedding photographer")


def test_explicit_people_intent_overrides_product_heavy_provider_persona(
    catalog: None,
) -> None:
    raw_plan = {
        "search_query": "Viltrox AF 85mm lens",
        "product_focus": ["portrait photographer"],
        "target_persona": "A premium 85mm F1.4 lens with advanced optics and E-mount support.",
        "platforms": ["youtube"],
    }
    product = {
        "sku": "AF 85/1.4 FE",
        "marketing_name": "Viltrox AF 85mm F1.4 Pro FE",
        "category_main": "Lens",
        "specs_line": "85mm F1.4 portrait lens",
    }

    plan = smart_query_planner._normalise_plan(
        "找婚礼人像摄影师",
        raw_plan,
        {"provider": "google", "model": "test"},
        product,
        {},
    )

    assert "Wedding portrait photographers" in plan["target_persona"]
    assert "advanced optics" not in plan["target_persona"]
    assert "E-mount" not in plan["target_persona"]


@pytest.mark.parametrize(
    "query",
    [
        "找赛车和赛道摄影师，不限镜头",
        "Find documentary-style wedding photographers, no gear requirement",
    ],
)
def test_no_gear_language_never_becomes_a_product_requirement(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    product_terms = [
        group["canonical_term"]
        for cell in plan["query_cells"]
        for group in cell["locked_term_groups"]["groups"]
        if group["kind"] == "product"
    ]

    assert set(product_terms) <= {"creator gear"}
    assert "lens review" not in plan["search_query"]
    assert [cell["segment"] for cell in plan["query_cells"]] == (
        ["motorsport"] if "赛车" in query else ["wedding"]
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Find wedding photographers except portrait photographers", ["wedding"]),
        ("Find portrait photographers who don't shoot weddings", ["portrait"]),
        ("Find portrait photographers avoiding weddings", ["portrait"]),
        ("Find portrait photographers but avoid wedding work", ["portrait"]),
        ("Find photographers who do neither weddings nor portraits", ["photography_role"]),
    ],
)
def test_negative_people_clauses_never_become_positive_cells(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected: list[str],
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})

    assert [cell["segment"] for cell in plan["query_cells"]] == expected
    assert all("wedding portrait" not in cell["primary_query"] for cell in plan["query_cells"])


@pytest.mark.parametrize(
    ("query", "expected_segments", "expected_scenes", "expected_role"),
    [
        ("Find photographers without flash who shoot weddings", ["wedding"], [["wedding"]], "photographer"),
        (
            "Find wedding photographers without gear requirements who also shoot portraits",
            ["wedding"],
            [["wedding", "portrait"]],
            "photographer",
        ),
        (
            "Find street photographers without a specific lens who shoot at night",
            ["street"],
            [["street", "night"]],
            "photographer",
        ),
        ("找不用闪光灯拍婚礼的摄影师", ["wedding"], [["wedding"]], "photographer"),
        (
            "找婚礼摄影师，不要器材评测博主，还要会拍人像",
            ["wedding"],
            [["wedding", "portrait"]],
            "photographer",
        ),
        (
            "Find photographers who shoot not only weddings but also portraits",
            ["wedding"],
            [["wedding", "portrait"]],
            "photographer",
        ),
        ("Find portrait photographers, excluding gear reviewers", ["portrait"], [["portrait"]], "photographer"),
        ("Find portrait photographers, exclude wedding photographers", ["portrait"], [["portrait"]], "photographer"),
        ("Find street photographers who aren’t wedding photographers", ["street"], [["street"]], "photographer"),
        (
            "Find portrait photographers avoiding weddings and shooting at night",
            ["portrait"],
            [["portrait", "night"]],
            "photographer",
        ),
        ("Find reviewers excluding wedding photographers", ["review"], [["review"]], "reviewer"),
    ],
)
def test_negative_constraints_keep_following_people_and_scene_intent(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_segments: list[str],
    expected_scenes: list[list[str]],
    expected_role: str,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    cells = plan["query_cells"]

    assert [cell["segment"] for cell in cells] == expected_segments
    assert [cell["required_scene_terms"] for cell in cells] == expected_scenes
    assert all(cell["required_role_terms"] == [expected_role] for cell in cells)
    assert all(cell["product_evidence_required"] is False for cell in cells)
    assert all(
        group["kind"] != "product"
        for cell in cells
        for group in cell["locked_term_groups"]["groups"]
    )


@pytest.mark.parametrize(
    ("query", "expected_segment"),
    [
        ("Find natural-light portrait photographers, no flash", "portrait"),
        ("Find wedding photographers who never use flash", "wedding"),
        ("Find no-flash street photographers", "street"),
    ],
)
def test_negative_flash_preference_never_becomes_product_capability(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_segment: str,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    cells = plan["query_cells"]

    assert [cell["segment"] for cell in cells] == [expected_segment]
    assert cells[0]["required_role_terms"] == ["photographer"]
    assert cells[0]["product_evidence_required"] is False
    assert all(
        group["kind"] != "product"
        for group in cells[0]["locked_term_groups"]["groups"]
    )


@pytest.mark.parametrize(
    "query",
    [
        "Find creators who review camera monitors",
        "Find camera monitor review channels",
        "Find field monitor reviewers",
    ],
)
def test_monitor_review_phrasings_resolve_to_people_not_product_text(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})

    assert [cell["segment"] for cell in plan["query_cells"]] == ["review"]
    assert plan["query_cells"][0]["primary_query"] == "camera monitor reviewer"
    assert plan["query_cells"][0]["product_evidence_basis"] == "operator_capability"


@pytest.mark.parametrize(
    ("query", "primary"),
    [
        ("Find flash photographers", "flash photographer"),
        ("Find strobe photographers", "strobe photographer"),
        ("找闪光摄影师", "flash photographer"),
    ],
)
def test_bare_flash_capability_is_kept_when_a_visual_role_is_explicit(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    primary: str,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    cell = plan["query_cells"][0]

    assert cell["primary_query"] == primary
    assert cell["product_evidence_basis"] == "operator_capability"
    assert cell["locked_term_groups"]["groups"][0]["canonical_term"] == "on-camera flash"


def test_existing_product_review_intent_survives_product_resolution(
    catalog: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(
        "Find creators already reviewing the Viltrox 35mm F1.2 LAB",
        body={"objective": "existing_evidence"},
    )

    assert [cell["segment"] for cell in plan["query_cells"]] == ["review"]
    assert "reviewer" in plan["query_cells"][0]["primary_query"]
    assert "viltrox" in plan["query_cells"][0]["primary_query"].lower()
