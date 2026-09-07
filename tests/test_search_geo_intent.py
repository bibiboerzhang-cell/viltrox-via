"""Hermetic operator geography contracts; no provider/profile facts are inferred."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.domains.kol.profile_discovery_candidates import (
    AMBIGUOUS_MARKET_CONSTRAINT,
    explicit_market_constraint,
    resolve_market_constraint,
)
from app.domains.kol.search_geo_intent import resolve_geo_intent
from app.domains.kol.smart_query_facets import _explicit_countries, propose_facets
from app.domains.kol.smart_query_planner_prompt import build_prompt


@pytest.mark.parametrize("query", [
    "英国创作者、美国受众、英语内容",
    "英国创作者美国受众英语内容",
    "UK creators with US audiences, English content",
    "creators based in UK with an audience in US",
    "US audience creators based in UK",
    "US-market relevance; creators based in UK",
    "creator country UK; audience country US",
])
def test_creator_residence_and_audience_are_separate(query):
    result = resolve_geo_intent(query)
    assert result["creator_countries"] == ["GB"]
    assert result["audience_markets"] == ["US"]
    assert result["creator_status"] == result["audience_status"] == "specified"
    assert result["creator_source"] == result["audience_source"] == "operator_text"
    assert result["ambiguity_reasons"] == []
    assert _explicit_countries(query)[0] == ["GB"]
    assert explicit_market_constraint(query, "US") == "gb"
    assert resolve_market_constraint(query, "GB") == "gb"


@pytest.mark.parametrize("query", [
    "全球作者美国受众",
    "Global creators with US audiences",
    "US audience relevance; creator residence need not be US",
    "美国受众，不要求作者在美国",
    "作者不必在美国，美国受众",
])
def test_unrestricted_author_does_not_inherit_audience_country(query):
    result = resolve_geo_intent(query)
    assert result["creator_countries"] == []
    assert result["creator_status"] == "unrestricted"
    assert result["audience_markets"] == ["US"]
    assert result["audience_status"] == "specified"
    assert resolve_market_constraint(query) == ""
    proposal = propose_facets(query, {"market": "US"}, raw_plan={"filter_proposal": {"countries": ["US"]}})
    assert proposal["facets"]["countries"]["values"] == []
    assert proposal["facets"]["countries"]["origin"] == "operator_explicit"


@pytest.mark.parametrize("query", ["US audience relevance", "美国受众英语内容", "面向美国市场的创作者", "面向美国的创作者"])
def test_audience_only_has_unknown_creator_location(query):
    result = resolve_geo_intent(query)
    assert result["creator_countries"] == []
    assert result["creator_status"] == "unknown"
    assert result["creator_unknown"] is True
    assert result["creator_source"] == "not_requested"
    assert result["audience_markets"] == ["US"]
    assert explicit_market_constraint(query, "US") == ""
    proposal = propose_facets(query, {"market": "US"}, raw_plan={"filter_proposal": {"countries": ["US"]}})
    assert proposal["facets"]["countries"]["values"] == []


@pytest.mark.parametrize("query,expected", [
    ("美国博主", "US"), ("找美国的摄影师", "US"),
    ("find creators in USA", "US"), ("find uk photographers", "GB"),
    ("filmmakers from AU", "AU"), ("filmmakers in PL", "PL"),
    ("wedding filmmakers in CA", "CA"), ("country:CA wedding filmmakers", "CA"),
    ("Find London photographers", "GB"), ("creators based in London", "GB"),
])
def test_explicit_creator_geography_preserves_existing_contract(query, expected):
    assert resolve_geo_intent(query)["creator_countries"] == [expected]


@pytest.mark.parametrize("query", [
    "English speaking creators", "英语内容的创作者", "find creators for us",
    "show us portrait creators", "Los Angeles CA wedding filmmakers",
    "find filmmakers using EPIC 65mm in PL mount",
    "creators photographing London", "在伦敦拍摄街景的博主",
    "creators filming in US", "拍摄美国街景的创作者",
    "do not infer US residence from English content",
    "可能来自美国的作者", "不一定是美国的博主",
    "creators perhaps based in UK", "unknown creator country",
])
def test_language_shooting_location_optional_and_unknown_are_not_hard_country(query):
    result = resolve_geo_intent(query)
    assert result["creator_countries"] == []
    assert result["creator_status"] == "unknown"
    assert result["audience_markets"] == []


@pytest.mark.parametrize("query", ["US and UK portrait creators", "美国英国摄影师", "不要美国博主", "non-US creators"])
def test_ambiguous_or_excluded_creator_countries_are_not_positive_filters(query):
    result = resolve_geo_intent(query)
    assert result["creator_countries"] == []
    assert result["creator_status"] == "ambiguous"
    assert result["ambiguity_reasons"]
    assert explicit_market_constraint(query, "US") == AMBIGUOUS_MARKET_CONSTRAINT
    with pytest.raises(ValueError):
        resolve_market_constraint(query)
    proposal = propose_facets(query, {"market": "US"})
    assert proposal["facets"]["countries"]["requires_clarification"] is True
    assert proposal["facets"]["countries"]["values"] == []


def test_global_audience_does_not_clear_explicit_creator_country():
    result = resolve_geo_intent("英国作者，全球受众")
    assert result["creator_countries"] == ["GB"]
    assert result["audience_status"] == "unrestricted"
    assert propose_facets("英国作者，全球受众", {})["facets"]["countries"]["values"] == ["GB"]


@pytest.mark.parametrize("query", ["US and UK audiences", "美国、英国受众", "面向美国和英国的创作者"])
def test_plain_country_list_shares_explicit_audience_relation(query):
    result = resolve_geo_intent(query)
    assert result["creator_countries"] == []
    assert result["creator_status"] == "unknown"
    assert result["audience_markets"] == ["US", "GB"]
    assert result["audience_status"] == "specified"


def test_structured_legacy_market_stays_creator_not_audience():
    body = {"market": "UK", "audience_markets": ["US"]}
    original = deepcopy(body)
    result = resolve_geo_intent("英语内容", body)
    assert result["creator_countries"] == ["GB"]
    assert result["audience_markets"] == ["US"]
    assert result["creator_source"] == result["audience_source"] == "operator_filter"
    assert body == original
    assert resolve_market_constraint("美国受众", "GB") == "gb"


def test_text_and_filters_confirm_same_independent_geographies():
    result = resolve_geo_intent("英国作者，美国受众", {"filters": {"countries": ["UK"]}, "audience_markets": ["US"]})
    assert result["creator_countries"] == ["GB"]
    assert result["audience_markets"] == ["US"]
    assert result["creator_source"] == result["audience_source"] == "operator_text_and_filter"


@pytest.mark.parametrize("query,body,reason", [
    ("英国作者，美国受众", {"country": "US"}, "conflicting_creator_text_and_filter"),
    ("全球作者，美国受众", {"market": "US"}, "conflicting_creator_unrestricted_and_country"),
    ("", {"market": "US", "country": "GB"}, "conflicting_creator_filters"),
    ("", {"country": "Atlantis"}, "unsupported_creator_country"),
    ("", {"creator_countries": [None]}, "unsupported_creator_country"),
    ("", {"filters": {"countries": {"values": ["US"], "mode": "invalid"}}}, "unsupported_creator_mode"),
])
def test_invalid_or_conflicting_creator_filter_is_surfaced(query, body, reason):
    result = resolve_geo_intent(query, body)
    assert result["creator_countries"] == []
    assert result["creator_status"] == "ambiguous"
    assert reason in result["ambiguity_reasons"]


def test_unrestricted_creator_conflicts_with_legacy_structured_country():
    with pytest.raises(ValueError, match="conflicting"):
        resolve_market_constraint("全球作者，美国受众", "US")


def test_audience_conflict_does_not_contaminate_creator_constraint():
    result = resolve_geo_intent("英国作者，美国受众", {"audience_markets": ["CA"]})
    assert result["creator_countries"] == ["GB"]
    assert result["creator_status"] == "specified"
    assert result["audience_markets"] == []
    assert result["audience_status"] == "ambiguous"
    assert "conflicting_audience_text_and_filter" in result["ambiguity_reasons"]


@pytest.mark.parametrize("mode", ["require", "include_unknown", "exclude"])
def test_structured_creator_modes_keep_existing_tri_state_contract(mode):
    result = resolve_geo_intent("美国受众", {"filters": {"countries": {"values": ["GB"], "mode": mode}}})
    assert result["creator_countries"] == ["GB"]
    assert result["creator_mode"] == mode
    assert result["creator_status"] == "specified"
    assert result["audience_markets"] == ["US"]
    assert result["audience_mode"] == "require"
    assert result["ambiguity_reasons"] == []


@pytest.mark.parametrize("mode", ["require", "include_unknown", "exclude"])
def test_structured_audience_modes_are_independent(mode):
    result = resolve_geo_intent("英国作者", {"audience_markets": {"values": ["US"], "mode": mode}})
    assert result["creator_countries"] == ["GB"]
    assert result["creator_mode"] == "require"
    assert result["audience_markets"] == ["US"]
    assert result["audience_mode"] == mode
    assert result["audience_status"] == "specified"


def test_conflicting_structured_modes_require_clarification():
    result = resolve_geo_intent("", {
        "creator_countries": {"values": ["US"], "mode": "require"},
        "filters": {"countries": {"values": ["US"], "mode": "exclude"}},
    })
    assert result["creator_countries"] == []
    assert result["creator_status"] == "ambiguous"
    assert "conflicting_creator_filter_modes" in result["ambiguity_reasons"]


@pytest.mark.parametrize("query", ["英国作者", "美国作者"])
def test_positive_text_and_structured_exclusion_are_not_silently_replaced(query):
    result = resolve_geo_intent(query, {"filters": {"countries": {"values": ["US"], "mode": "exclude"}}})
    assert result["creator_countries"] == []
    assert result["creator_status"] == "ambiguous"
    assert "conflicting_creator_text_and_filter_mode" in result["ambiguity_reasons"]


def test_explicit_structured_unknown_mode_is_not_silently_hardened():
    result = resolve_geo_intent("英国作者", {"creator_countries": {"values": ["GB"], "mode": "include_unknown"}})
    assert result["creator_countries"] == ["GB"]
    assert result["creator_mode"] == "include_unknown"
    assert result["creator_status"] == "specified"


@pytest.mark.parametrize("query,body", [(None, None), ([], []), ({}, {"unrelated": "US"}), ("", {})])
def test_missing_or_malformed_query_has_no_implicit_default(query, body):
    result = resolve_geo_intent(query, body)
    assert result["creator_countries"] == result["audience_markets"] == []
    assert result["creator_status"] == result["audience_status"] == "unknown"


def test_prompt_separates_geo_without_unrequested_us_or_language_exclusion():
    prompt = build_prompt("英国作者，美国受众，英语内容", resolved_product=None, body={})
    assert 'Set market to "US" unless' not in prompt
    assert 'Target the ENGLISH-speaking market' not in prompt
    assert 'Exclude Chinese-language creators.' not in prompt
    assert 'creator_countries=["GB"], audience_markets=["US"], market="GB"' in prompt
    assert 'Do not default any of them to US or English' in prompt


def test_explicit_language_only_verified_matches_while_inferred_can_include_unknown():
    explicit = propose_facets("英国作者，美国受众，英语内容", {})["facets"]["languages"]
    assert explicit["values"] == ["en"]
    assert explicit["mode"] == "require"
    assert explicit["relaxable"] is False
    assert "待核" in explicit["note"]
    assert "不算已匹配" in explicit["note"]
    inferred = propose_facets("创作者", {}, raw_plan={"filter_proposal": {"languages": ["en"]}})["facets"]["languages"]
    assert inferred["mode"] == "include_unknown"
