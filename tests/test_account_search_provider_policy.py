"""Pure/mock provider contract checks; no vendor or database connections."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.services.intelligence import account_scan_service
from app.services.intelligence import account_search_discovery as discovery
from app.services.intelligence.account_search_provider_policy import (
    annotate_discovery_result, build_provider_discovery_policy,
    validate_provider_discovery_policy, youtube_discovery_hints,
)
from app.services.intelligence.account_search_terms import _youtube_data_api_normalize

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def language(values, mode="require"):
    return {"requested": bool(values), "values": values, "mode": mode, "invalid": []}


def test_contract_is_canonical_versioned_and_does_not_mutate_input():
    raw = language(["fr", "en"])
    before = deepcopy(raw)
    policy = build_provider_discovery_policy(language_filter=raw, recent_activity_max_age_days=365)
    assert raw == before
    assert policy["historical_topic_max_age_days"] is None
    assert policy["discovery_max_age_days"] == 45
    assert policy["recent_activity_max_age_days"] == 365
    assert policy == build_provider_discovery_policy(language_filter=language(["en", "fr"]), recent_activity_max_age_days=365)
    assert validate_provider_discovery_policy(policy) == policy
    assert validate_provider_discovery_policy(None) is None


def test_builder_accepts_existing_server_normalized_language_fragment():
    from app.domains.kol.profile_recall_search_spec import operator_filter_spec
    fragment = operator_filter_spec(languages=["fr"])["languages"]
    assert "mode" not in fragment and fragment["maximum"] == 8
    policy = build_provider_discovery_policy(language_filter=fragment)
    assert policy["language_filter"] == language(["fr"])


@pytest.mark.parametrize("value", [True, 0, 46, 365, 1.5, "45", None])
def test_discovery_cannot_widen_or_silently_coerce_window(value):
    with pytest.raises(ValueError, match="invalid_window"):
        build_provider_discovery_policy(discovery_max_age_days=value)


@pytest.mark.parametrize("value", [
    {}, {"value": ["en"], "mode": "require"}, language(["en"], "typo"),
    {**language(["en"]), "mode": []}, {**language(["en"]), "requested": False},
    {**language(["en"]), "invalid": ["zz"]}, language([True]), language(["EN"]), language(["zz"]),
])
def test_malformed_filter_never_turns_into_unfiltered_discovery(value):
    with pytest.raises(ValueError, match="invalid_language_filter"):
        build_provider_discovery_policy(language_filter=value)


@pytest.mark.parametrize("mutation", [{"version": 2}, {"version": True}, {"policy_hash": "changed"}, {"discovery_max_age_days": 30}, {"extra": "unknown"}])
def test_contract_tampering_fails_before_provider_dispatch(monkeypatch, mutation):
    policy = {**build_provider_discovery_policy(), **mutation}
    monkeypatch.setattr(discovery, "_scan_service", lambda: pytest.fail("provider reached"))
    monkeypatch.setattr(discovery, "_youtube_fast_result", lambda *_a, **_k: pytest.fail("provider reached"))
    with pytest.raises(ValueError, match="invalid_contract"):
        asyncio.run(discovery.search_platform_content("youtube", "camera", provider_discovery_policy=policy))


@pytest.mark.parametrize("values,mode,expected", [
    (["fr"], "require", "fr"), (["fr"], "include_unknown", "fr"),
    (["fr"], "exclude", None), (["en", "fr"], "require", None),
    (["zh"], "require", None), ([], "require", None),
])
def test_official_language_is_only_a_single_positive_hint(values, mode, expected):
    policy = build_provider_discovery_policy(language_filter=language(values, mode))
    hints = youtube_discovery_hints(policy, video_evidence=True, now=NOW)
    assert hints.get("relevanceLanguage") == expected
    assert hints["publishedAfter"] == "2026-07-23T12:00:00Z"
    assert "publishedAfter" not in youtube_discovery_hints(policy, video_evidence=False, now=NOW)


def test_old_content_retained_as_history_and_independent_of_fetch_time():
    policy = build_provider_discovery_policy(recent_activity_max_age_days=365)
    original = {"items": [{"published": "2026-05-01T12:00:00Z", "sample_title": "historical topic"}]}
    result = annotate_discovery_result(original, policy, provider="apify", fetched_at=NOW)
    item = result["items"][0]
    assert item["historical_topic_evidence"]["status"] == "within_window"
    assert item["historical_topic_evidence"]["relevance_status"] == "not_assessed"
    assert item["recent_activity_evidence"]["status"] == "within_window"
    assert item["recent_activity_evidence"]["latest_content_proven"] is False
    assert item["discovery_window_evidence"]["status"] == "outside_window"
    assert item["fetched_at"] == "2026-09-06T12:00:00Z"
    assert item["published"] == "2026-05-01T12:00:00Z"
    assert "fetched_at" not in original["items"][0]


@pytest.mark.parametrize("published", [None, "", "not-a-date", "2027-01-01", "1788696000"])
def test_unknown_or_future_content_never_inherits_fetched_at(published):
    policy = build_provider_discovery_policy()
    result = annotate_discovery_result({"items": [{"published": published}]}, policy, provider="apify", fetched_at=NOW)
    item = result["items"][0]
    assert item["recent_activity_evidence"]["status"] == "unknown"
    assert item["discovery_window_evidence"]["status"] == "unknown"


def test_two_providers_share_discovery_window_without_claiming_actor_prefilter():
    policy = build_provider_discovery_policy()
    raw = {"items": [{"published": "2026-08-01T12:00:00Z"}]}
    official = annotate_discovery_result(raw, policy, provider="youtube_data_api", resource_kind="video", fetched_at=NOW)
    actor = annotate_discovery_result(raw, policy, provider="apify", fetched_at=NOW)
    assert official["items"][0]["discovery_window_evidence"]["status"] == actor["items"][0]["discovery_window_evidence"]["status"]
    assert official["metadata"]["discovery_window_enforced_by_provider"] is True
    assert actor["metadata"]["discovery_window_enforced_by_provider"] is False
    assert actor["metadata"]["discovery_postfilter_required"] is True
    assert actor["metadata"]["actor_hint_schema_status"] == "fixed_build_not_verified"
    assert actor["metadata"]["discovery_hint_parameters"] == {}


def test_channel_creation_is_not_published_video_or_recent_activity():
    rows = _youtube_data_api_normalize([
        {"id": {"channelId": "UC-fixture"}, "snippet": {"title": "Creator", "publishedAt": "2026-09-01T00:00:00Z"}},
    ], "camera", "", "youtube-data-api/search.list", 1)
    assert rows[0]["published"] == ""
    assert rows[0]["channel_created_at"] == "2026-09-01T00:00:00Z"
    result = annotate_discovery_result({"items": rows}, build_provider_discovery_policy(), provider="youtube_data_api", resource_kind="channel", fetched_at=NOW)
    assert result["items"][0]["recent_activity_evidence"]["status"] == "unknown"


def test_official_search_uses_operator_language_not_market_and_no_extra_calls(monkeypatch):
    from app.platform.industry_crawlers import youtube_crawler
    calls = []
    stamp = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

    class Crawler:
        api_key = "fake-not-a-real-key"
        def _request(self, endpoint, params):
            calls.append((endpoint, params))
            return {"items": [{"id": {"videoId": "fixture"}, "snippet": {
                "channelId": "UC-fixture", "channelTitle": "Creator", "title": "camera motorsport", "publishedAt": stamp,
            }}]}
        def _should_use_apify_fallback(self, _payload):
            return False

    monkeypatch.setattr(youtube_crawler, "YouTubeCrawler", Crawler)
    monkeypatch.setattr(discovery, "_youtube_channel_statistics", lambda *_: {"UC-fixture": {"subscribers": 10000}})
    monkeypatch.setattr(discovery, "youtube_video_statistics", lambda *_: {"fixture": {"views": 10000, "likes": 100, "comments": 10}})
    monkeypatch.setattr(account_scan_service, "_run_actor", lambda *_a, **_k: pytest.fail("no Actor fallback"))
    policy = build_provider_discovery_policy(language_filter=language(["fr"]))
    result = asyncio.run(discovery.search_platform_content(
        "youtube", "camera motorsport", market="US", relevance_language="en", max_results=7,
        exact_query=True, strict_evidence=True, provider_discovery_policy=policy,
    ))
    assert len(calls) == 1
    assert calls[0][1]["relevanceLanguage"] == "fr"
    assert calls[0][1]["q"] == "camera motorsport"
    assert calls[0][1]["maxResults"] == 7
    assert calls[0][1]["type"] == "video"
    assert result["metadata"]["youtube_search_calls"] == 1
    assert result["metadata"]["youtube_api_calls"] == 3
    assert result["metadata"]["provider_discovery_policy"] == policy
    assert result["metadata"]["language_filter_enforced_by_provider"] is False
    assert result["items"][0]["recent_activity_evidence"]["status"] == "within_window"


def test_actor_contract_does_not_guess_language_date_fields_or_refill(monkeypatch):
    calls = []
    async def no_official(*_a, **_kw):
        return None
    async def actor(actor_id, payload, timeout):
        calls.append((actor_id, deepcopy(payload), timeout))
        return [{"channelId": "UC-fixture", "channelName": "Creator", "title": "old camera topic", "uploadDate": "2020-01-01"}]
    monkeypatch.setattr(discovery, "_youtube_data_api_strict_video_search", no_official)
    monkeypatch.setattr(account_scan_service, "provider_ready", lambda: True)
    monkeypatch.setattr(account_scan_service, "_run_actor", actor)
    policy = build_provider_discovery_policy(language_filter=language(["fr"]))
    result = asyncio.run(discovery.search_platform_content(
        "youtube", "camera motorsport", max_results=7, strict_evidence=True,
        provider_discovery_policy=policy,
    ))
    assert calls == [("streamers/youtube-scraper", {"searchQueries": ["camera motorsport"], "maxResults": 7, "maxResultsShorts": 0, "maxResultStreams": 0}, 240)]
    assert len(result["items"]) == 1
    assert result["items"][0]["discovery_window_evidence"]["status"] == "outside_window"
    assert result["items"][0]["historical_topic_evidence"]["status"] == "within_window"
    assert result["metadata"]["language_postfilter_required"] is True
    assert result["metadata"]["provider_input_expanded"] is False
