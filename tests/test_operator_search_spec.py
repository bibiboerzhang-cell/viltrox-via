from copy import deepcopy

import pytest

from app.domains.kol import operator_search_spec as spec
from app.domains.kol import smart_query_facets, targeted_search_runtime
from app.domains.kol.profile_recall_candidate_pipeline import _language_gate
from app.domains.kol.profile_recall_search_spec import parse_operator_languages
from types import SimpleNamespace


def plan(query="Find English speaking street photographers", **extra):
    return {"filter_proposal": smart_query_facets.propose_facets(query, {}), **extra}


def context(p=None, body=None, filters=None):
    return targeted_search_runtime.prepare_local_search(
        plan=p or plan(), body=body or {}, recall_filters=filters or {},
        market="", platforms=["youtube"],
    )


def test_natural_language_qualification_is_not_lost_after_preview():
    p = plan()
    first = context(p)
    queued = context({"filter_proposal": deepcopy(p["filter_proposal"])}, {"filters": {}})
    assert first["local_qualification_policy"]["languages"] == ["en"]
    assert queued["local_qualification_policy"]["languages"] == ["en"]
    assert first["operator_search_spec"]["constraint_hash"] == queued["operator_search_spec"]["constraint_hash"]
    online = spec.online_policy_inputs({"operator_search_spec": queued["operator_search_spec"]})
    assert parse_operator_languages(online["languages"])["values"] == ["en"]


def test_chip_and_language_aliases_have_same_effective_contract():
    natural = context()
    chip = context(plan("street photographers"), {"filters": {"languages": ["English"]}})
    assert natural["operator_search_spec"]["constraint_hash"] == chip["operator_search_spec"]["constraint_hash"]


def test_explicit_ui_wins_and_model_filters_remain_opt_in():
    p = plan("street photographers")
    p["filter_proposal"] = smart_query_facets.propose_facets(
        "street photographers", {}, raw_plan={"filter_proposal": {"countries": ["US"], "languages": ["en"]}},
    )
    result = context(p, {"filters": {"languages": ["fr"]}})
    assert result["local_qualification_policy"]["languages"] == ["fr"]
    assert "countries" not in result["recall_filters"]
    assert result["operator_search_spec"]["model_additions_dropped"]


def test_filter_mapping_is_copied_and_incoming_spec_never_trusted():
    body = {"filters": {"languages": ["en"]}, "operator_search_spec": {"constraint_hash": "forged"}}
    before = deepcopy(body)
    result = context(plan("street photographers"), body)
    body["filters"]["languages"].append("fr")
    assert result["local_qualification_policy"]["languages"] == ["en"]
    assert result["operator_search_spec"]["constraint_hash"] != "forged"
    assert before["filters"]["languages"] == ["en"]


@pytest.mark.parametrize("cells", ["bad", "", {}, 0, False, [{"query_cell_id": "x", "primary_query": "street photo", "required_scene_terms": "street"}], [None]])
def test_malformed_cells_never_fall_back_to_legacy_recall(cells):
    with pytest.raises(ValueError, match="invalid_query_cells"):
        context(plan(query_cells=cells))


@pytest.mark.parametrize("mode,value,passed", [
    ("require", "en", True), ("require", None, False), ("require", "fr", False),
    ("include_unknown", None, True), ("include_unknown", "fr", False),
    ("exclude", "en", False), ("exclude", "fr", True), ("exclude", None, True),
])
def test_language_modes_survive_shared_qualification(mode, value, passed):
    parsed = parse_operator_languages({"values": ["English"], "mode": mode})
    policy = SimpleNamespace(
        invalid_languages=parsed["invalid"], target_languages=frozenset(parsed["values"]),
        language_mode=parsed.get("mode", "require"), language_requested=parsed["requested"],
    )
    verdict = _language_gate({"language": value}, {}, policy)
    assert verdict["passed"] is passed
    if value is None:
        assert verdict["values"] == []


def test_invalid_mode_cannot_silently_widen_a_constraint():
    parsed = parse_operator_languages({"values": ["en"], "mode": "allow_everything"})
    assert parsed["invalid"] == ["unsupported_filter_mode"]


@pytest.mark.parametrize("value", [{"value": ["en"]}, {"mode": "require"}, {"values": {"en": True}}, {"values": [False]}])
def test_malformed_language_objects_are_invalid_not_no_filter(value):
    parsed = parse_operator_languages(value)
    assert parsed["requested"] is True
    assert "invalid_filter_shape" in parsed["invalid"]


def test_literal_alias_overrides_natural_proposal():
    result = context(plan(), {"content_languages": ["fr"]})
    assert result["local_qualification_policy"]["languages"] == ["fr"]


def test_model_follower_hint_does_not_become_a_hard_filter():
    p = plan(follower_filter={"source": "planner_inferred", "followers_min": 100000})
    assert context(p)["followers_min"] is None
    result = context(p, {"follower_min": 3000})
    assert result["followers_min"] == 3000
    assert result["follower_source"] == "operator_filter"
    assert result["follower_filter"]["source"] == "operator_filter"
    assert result["operator_search_spec"]["filters"]["followers_min"] == 3000


def test_constraint_hash_covers_effective_freshness_and_favorite_policy():
    default = context()["operator_search_spec"]["constraint_hash"]
    strict = context(body={"strict_gates": True})["operator_search_spec"]["constraint_hash"]
    hide = context(body={"hide_team_favorites": True})["operator_search_spec"]["constraint_hash"]
    assert len({default, strict, hide}) == 3


def test_context_filter_mutation_cannot_modify_frozen_spec():
    result = context()
    original = deepcopy(result["operator_search_spec"])
    result["recall_filters"]["languages"].append("fr")
    assert result["operator_search_spec"] == original


def test_online_legacy_payload_uses_nested_filters_too():
    assert spec.online_policy_inputs({"filters": {"languages": ["en"]}})["languages"] == ["en"]
