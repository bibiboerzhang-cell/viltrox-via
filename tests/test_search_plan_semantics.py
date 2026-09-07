"""Pure local semantic checks; no provider, database, or inventory fixtures."""
from __future__ import annotations

import copy
import json

import pytest

from app.domains.kol.search_plan_semantics import assess_search_plan_semantics, normalize_search_branch


def plan(*queries, market="US"):
    return {"intent": "Find relevant creators", "platform": "youtube", "market": market,
            "queries": list(queries), "qualification_checks": [], "limitations": []}


def codes(result):
    return {issue["code"] for issue in result["issues"]}


def test_comparison_only_filler_removal_preserves_original_and_meaningful_tokens():
    query = "YouTube Creator 35mm Night Photography -filmmaking"
    branch = normalize_search_branch(query, platform="youtube")
    assert branch["original_query"] == query
    assert branch["comparison_query"] == "35mm night photography -filmmaking"
    assert "night" in branch["comparison_tokens"]


def test_other_platform_names_and_negated_roles_are_not_removed():
    branch = normalize_search_branch("Instagram street photography not creators -youtube", platform="youtube")
    assert branch["comparison_query"] == "instagram street photography not creators -youtube"


@pytest.mark.parametrize("queries", [
    ("street photography YouTube", "STREET Photography creators"),
    ("street photography creator", "street photography channel youtube"),
    ("street   photography", "street photography"),
    ("ＳＴＲＥＥＴ photography", "street photography"),
])
def test_filler_case_unicode_and_spacing_do_not_create_branch_diversity(queries):
    original = plan(*queries)
    before = copy.deepcopy(original)
    result = assess_search_plan_semantics(original)
    assert result["status"] == "invalid"
    assert "duplicate_query_branch" in codes(result)
    assert result["original_plan"] == original == before
    assert result["queries_rewritten"] is False and result["hard_constraints_modified"] is False
    assert [row["original_query"] for row in result["branches"]] == list(queries)


def test_real_505247_shape_rejects_market_fragment_and_flags_overlap_without_editing():
    original = plan(
        "independent street photography night photography YouTube",
        "street photography low light photography creator YouTube",
        market="US audience relevance; creator's",
    )
    result = assess_search_plan_semantics(original)
    assert result["status"] == "invalid"
    assert codes(result) == {"market_not_atomic_code", "possible_query_branch_overlap"}
    assert result["market"]["code"] is None
    assert result["original_plan"] == original
    assert "low light" in result["branches"][1]["comparison_query"]
    assert "night" in result["branches"][0]["comparison_query"]


def test_night_and_low_light_are_possible_overlap_not_equivalent():
    result = assess_search_plan_semantics(plan("street night photography", "street low light photography"))
    assert result["status"] == "needs_review"
    assert "possible_query_branch_overlap" in codes(result)
    assert "duplicate_query_branch" not in codes(result)


@pytest.mark.parametrize("queries", [
    ("street night photography tutorial", "street night photography lens review"),
    ("street night photography 35mm review", "street night photography 85mm review"),
    ("street night photography filmmaking", "street night photography -filmmaking"),
    ("street night photography with flash", "street night photography without flash"),
])
def test_explicit_different_angles_numbers_and_negations_are_not_collapsed(queries):
    result = assess_search_plan_semantics(plan(*queries))
    assert result["status"] == "qualified"
    assert not result["issues"]
    assert len(result["branches"]) == 2


def test_identical_scene_angle_metadata_does_not_by_itself_prove_duplicate():
    metadata = [{"scene": "street", "angle": "practical"}] * 2
    result = assess_search_plan_semantics(plan("street composition", "urban lighting"), branch_metadata=metadata)
    assert result["status"] == "needs_review"
    assert codes(result) == {"scene_angle_reused"}
    assert len(result["branches"]) == 2


def test_distinct_declared_scene_angle_preserved_even_with_identical_text():
    metadata = [{"scene": "night", "angle": "instruction"}, {"scene": "night", "angle": "field review"}]
    result = assess_search_plan_semantics(plan("night photography", "night photography creator"), branch_metadata=metadata)
    assert result["status"] == "needs_review"
    assert codes(result) == {"declared_intent_not_distinguished_in_query"}
    assert "duplicate_query_branch" not in codes(result)


def test_distinct_declared_angles_are_not_blocked_by_lexical_similarity():
    metadata = [{"scene": "street", "angle": "instruction"}, {"scene": "street", "angle": "field review"}]
    result = assess_search_plan_semantics(plan("street night photography", "street low light photography"), branch_metadata=metadata)
    assert result["status"] == "qualified"


@pytest.mark.parametrize("market, expected", [("US", "US"), ("us", "US"), (" UK ", "GB"), ("GB", "GB"), ("JP", "JP")])
def test_market_accepts_complete_supported_codes_without_changing_original(market, expected):
    original = plan("street photography", market=market)
    result = assess_search_plan_semantics(original)
    assert result["status"] == "qualified"
    assert result["market"]["code"] == expected
    assert result["market"]["dimension"] == "unspecified"
    assert result["market"]["evidence_verified"] is False
    assert result["original_plan"]["market"] == market


@pytest.mark.parametrize("market", [None, "unknown", " UNKNOWN "])
def test_unknown_market_is_not_guessed_from_queries_or_intent(market):
    original = plan("US night photography", market=market)
    original["intent"] = "Find creators for a US audience"
    result = assess_search_plan_semantics(original)
    assert result["market"]["status"] == "unknown"
    assert result["market"]["code"] is None
    assert "not_creator_qualification" in result["qualification_scope"]


@pytest.mark.parametrize("market", ["", "  ", 123, ["US"], {"code": "US"}])
def test_incomplete_market_stays_invalid_without_a_default(market):
    result = assess_search_plan_semantics(plan("street photography", market=market))
    assert result["status"] == "invalid"
    assert "market_incomplete" in codes(result)
    assert result["market"]["code"] is None


@pytest.mark.parametrize("market", ["US audience relevance; creator's", "US/CA", "US audience", "United States", "US:"])
def test_market_prose_or_fragments_are_not_truncated_to_codes(market):
    result = assess_search_plan_semantics(plan("street photography", market=market))
    assert "market_not_atomic_code" in codes(result)
    assert result["market"]["code"] is None


def test_taxonomy_is_explicit_and_unknown_code_is_not_guessed():
    result = assess_search_plan_semantics(plan("street photography", market="ZZ"))
    assert result["status"] == "needs_review"
    assert result["market"]["code"] is None
    assert codes(result) == {"market_code_unrecognized"}
    custom = assess_search_plan_semantics(plan("street photography", market="CN"), supported_market_codes={"CN"})
    assert custom["market"]["code"] == "CN"


@pytest.mark.parametrize("original", [None, [], {}, {"queries": []}, {"queries": [""]}, {"queries": [None]}, plan("YouTube creator")])
def test_invalid_input_returns_issues_not_repaired_queries(original):
    result = assess_search_plan_semantics(original)
    assert result["status"] == "invalid"
    assert result["issues"]
    assert result["original_plan"] == original
    json.dumps(result)


def test_single_branch_is_not_required_to_fake_diversity():
    result = assess_search_plan_semantics(plan("street photography"))
    assert result["status"] == "qualified"
    assert len(result["branches"]) == 1


def test_traceable_plan_is_a_deep_copy_and_output_is_serializable():
    original = plan("street photography")
    result = assess_search_plan_semantics(original)
    result["original_plan"]["queries"].append("changed downstream")
    assert original["queries"] == ["street photography"]
    json.dumps(result)


def test_branch_metadata_must_remain_index_aligned():
    result = assess_search_plan_semantics(plan("street photography", "portrait lighting"), branch_metadata=[{"scene": "street"}])
    assert result["status"] == "invalid"
    assert "branch_metadata_misaligned" in codes(result)


@pytest.mark.parametrize("metadata", ["not metadata", 1, {"scene": "street"}, [None]])
def test_malformed_branch_metadata_returns_issue(metadata):
    result = assess_search_plan_semantics(plan("street photography"), branch_metadata=metadata)
    assert result["status"] == "invalid"
    assert "branch_metadata_misaligned" in codes(result)
