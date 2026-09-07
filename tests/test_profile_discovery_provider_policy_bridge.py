"""Provider-policy bridge and evidence projection; mocked I/O only."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import inspect
import logging

import pytest

from app.domains.kol import history_match, profile_discovery as facade
from app.domains.kol import profile_discovery_provider as provider
from app.domains.kol import profile_discovery_provider_flow as flow
from app.services.intelligence.account_search_provider_policy import (
    annotate_discovery_result, build_provider_discovery_policy,
)


def _policy(values=("fr",), mode="require"):
    return build_provider_discovery_policy(language_filter={
        "requested": bool(values), "values": list(values), "invalid": [], "mode": mode,
    })


def _plan(events, **changes):
    def market(value):
        events.append(("market", value))
        return "ja", "JP"
    def localize(query, language):
        events.append(("localize", query, language))
        return "localized: " + query
    args = dict(query_text="locked camera query", platforms=["youtube"], platform_hint="",
        market="JP", limit=3, per_platform_limit=3, per_platform_limits=None,
        search_query_en="alternate query", product_focus=None, ideal_creator_types=None,
        verticals=None, auto_enroll=False, exclude_chinese=False, page_cursors=None,
        exact_query=False, text_value=lambda value: str(value or "").strip(),
        int_value=provider._int, market_to_language=market, localize_search_terms=localize,
        has_cjk=lambda value: "中文" in value, persona_positive_terms=lambda *_: ["fallback query"],
        strict_platforms=lambda values, **_: values, sanitize_limits=lambda _value: {},
        resolve_limit=lambda _platform, default, _limits: default, normalize_leg_cursors=lambda _value: {})
    return flow.prepare_discovery_plan(**(args | changes))


def test_policy_language_overrides_market_without_changing_query_count():
    events = []
    plan = _plan(events, provider_discovery_policy=_policy())
    assert events == [("localize", "alternate query", "fr")]
    assert plan.relevance_language == "fr"
    assert plan.search_term == "localized: alternate query"
    assert plan.leg_limits == {"youtube": 3}


@pytest.mark.parametrize("values,mode", [((), "require"), (("en", "fr"), "require"), (("fr",), "exclude"), (("zh",), "require")])
def test_no_single_positive_hint_does_not_fall_back_to_market_or_translate(values, mode):
    events = []
    plan = _plan(events, provider_discovery_policy=_policy(values, mode))
    assert events == []
    assert plan.relevance_language == ""
    assert plan.search_term == "alternate query"


@pytest.mark.parametrize("policy", [None, _policy(), _policy(("en",))])
def test_exact_query_uses_locked_text_and_never_localizes_or_cjk_falls_back(policy):
    events = []
    plan = _plan(events, query_text='中文 camera "night"', exact_query=True, provider_discovery_policy=policy)
    assert plan.query == plan.search_term == '中文 camera "night"'
    assert not any(event[0] == "localize" for event in events)
    assert plan.provider_discovery_policy == policy


def test_legacy_nonexact_market_localization_is_unchanged():
    events = []
    plan = _plan(events)
    assert events == [("market", "JP"), ("localize", "alternate query", "ja")]
    assert plan.provider_discovery_policy is None
    assert plan.search_term == "localized: alternate query"


def test_invalid_policy_fails_before_market_translation_or_dispatch():
    events = []
    with pytest.raises(ValueError, match="invalid_contract"):
        _plan(events, provider_discovery_policy={**_policy(), "policy_hash": "tampered"})
    assert events == []


async def _sequential(platforms, search):
    return [await search(platform) for platform in platforms]


def test_each_leg_gets_an_independent_policy_and_legacy_omits_new_kwarg():
    calls = []
    policy = _policy()
    plan = _plan([], platforms=["youtube", "instagram"], exact_query=True, provider_discovery_policy=policy)
    async def fake_search(platform, query, **kwargs):
        calls.append((platform, query, deepcopy(kwargs)))
        if kwargs.get("provider_discovery_policy") is not None:
            kwargs["provider_discovery_policy"]["language_filter"]["values"].append("de")
        return {"status": "empty", "items": []}
    def run(plan):
        return asyncio.run(flow.search_provider_legs(plan, enrich_prefilter=None,
            search_platform=fake_search, annotate_platform_items=lambda rows, **_: rows,
            canonicalize_candidates=lambda rows, **_: rows, platform_signals=lambda _row: set(),
            run_legs=_sequential, deadline_seconds=lambda _platform: 3, logger=logging.getLogger("test")))
    run(plan)
    assert len(calls) == 2
    assert all(call[2]["provider_discovery_policy"] == policy for call in calls)
    assert plan.provider_discovery_policy == policy
    run(_plan([], exact_query=True))
    assert "provider_discovery_policy" not in calls[-1][2]


def test_facade_signature_explicitly_mirrors_optional_provider_policy():
    for function in (facade.discover_new_creators, provider.discover_new_creators):
        parameter = inspect.signature(function).parameters["provider_discovery_policy"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None


def test_full_facade_keeps_evidence_through_real_annotation_and_canonicalization(monkeypatch):
    policy = _policy()
    channel_id = "UC" + "A" * 22
    base = {"platform": "youtube", "channel_id": channel_id, "channel_url": f"https://www.youtube.com/channel/{channel_id}",
        "source_url": "https://www.youtube.com/watch?v=fixture", "sample_title": "camera filmmaking motorsport",
        "bio": "camera photography creator", "followers": 10000, "views": 5000,
        "likes": 100, "comments": 10, "avg_views": 5000, "published": "2026-09-01T00:00:00Z"}
    raw = annotate_discovery_result({"status": "done", "items": [
        {**base, "handle": channel_id, "channel_name": "Creator"},
        {**base, "handle": "realcreator", "channel_name": "Real Creator"},
    ]}, policy, provider="youtube_data_api", resource_kind="video",
        fetched_at=datetime(2026, 9, 6, tzinfo=timezone.utc))
    before = deepcopy(raw)
    calls = []
    async def fake_search(platform, query, **kwargs):
        calls.append((platform, query, deepcopy(kwargs)))
        return raw
    monkeypatch.setattr(facade, "search_platform_content", fake_search)
    monkeypatch.setattr(facade, "_market_to_language", lambda *_: pytest.fail("operator policy cannot use market language"))
    monkeypatch.setattr(facade, "_localize_search_terms", lambda *_: pytest.fail("locked query cannot translate"))
    monkeypatch.setattr(provider, "discovery_wall_verdict", lambda _item: "")
    # Exercise the real annotate_platform_items body while replacing every DB
    # boundary with in-memory stubs. No database connection is constructed.
    monkeypatch.setattr(history_match, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(history_match, "get_conn", lambda: object())
    monkeypatch.setattr(history_match, "prepare_pool_read_selection", lambda *_a, **_k: object())
    monkeypatch.setattr(history_match, "find_history_match", lambda *_a, **_k: None)
    result = asyncio.run(facade.discover_new_creators(
        query_text='camera motorsport "night"', search_query_en="must not replace the cell",
        platforms=["youtube"], market="JP", auto_enroll=False, exclude_chinese=False,
        exact_query=True, per_platform_limit=2, provider_discovery_policy=policy,
    ))
    assert len(calls) == 1
    assert calls[0][1] == 'camera motorsport "night"'
    assert calls[0][2]["provider_discovery_policy"] == policy
    assert calls[0][2]["relevance_language"] == "fr"
    assert len(result["new_creators"]) == 1
    creator = result["new_creators"][0]
    assert creator["handle"] == "realcreator"
    for key in ("historical_topic_evidence", "recent_activity_evidence", "discovery_window_evidence", "fetched_at"):
        assert creator[key] == before["items"][1][key]
    creator["recent_activity_evidence"]["status"] = "mutated output"
    assert raw == before
    assert policy == _policy()
