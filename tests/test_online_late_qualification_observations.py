"""Real qualification reducers, fake provider transport/materialization only."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import MappingProxyType

import pytest

from app.domains.kol import profile_online_qualification as online
from app.domains.kol.targeted_search_contract import build_locked_term_groups

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _raw():
    return {"platform": "youtube", "handle": "lateproof", "channel_id": "UClateproof",
            "profile_url": "https://www.youtube.com/@lateproof", "followers": 8000,
            "country": "US", "country_source": "platform_profile", "language": "en",
            "language_source": "platform_profile", "profile_type": "creator",
            "profile_type_source": "provider_declared", "bio": "portrait lighting studio tutorial creator",
            "latest_real_video": {"posted_at": "2026-09-04T00:00:00Z", "video_id": "latevideo123",
                                  "platform": "youtube", "title": "portrait lighting studio tutorial",
                                  "source": "platform_video_api"}}


def _audience():
    # A synthetic, source-asserted contract fixture, not real account analytics.
    return {"market": "US", "source": "platform_audience_analytics", "verified": True,
            "evidence_ref": "analytics:synthetic-late-proof", "observed_at": "2026-09-05T00:00:00Z"}


def _run(rounds, *, audience=True, followers=False, resolve=True):
    calls, enrolled, observations = [], [], []

    async def fetch_batch(*, round_no, limit, **_kwargs):
        calls.append(limit)
        return {"new_creators": [deepcopy(rounds[round_no - 1])], "provider_calls": True,
                "has_more": round_no < len(rounds), "next_cursor": {"round": round_no}}

    def enroll(raw):
        enrolled.append(deepcopy(raw))
        return {"kol_pool_id": 991}

    policy = online.online_policy(
        platforms=["youtube"], languages=["en"], profile_types=["creator"],
        geo_constraints={"audience_markets": ["US"], "audience_mode": "require"} if audience else None,
        followers_min=1000 if followers else None,
    )
    registry = MappingProxyType({_audience()["evidence_ref"]: MappingProxyType({
        **_audience(), "subject_key": "youtube:handle:lateproof"})})
    def resolver(reference):
        value = registry.get(reference)
        return dict(value) if value is not None else None

    result = asyncio.run(online.collect_strict_online_candidates(
        query_text="portrait lighting", policy=policy, local_canonical_keys=set(), fetch_batch=fetch_batch,
        enroll_candidate=enroll, round_observer=lambda payload: observations.append(payload),
        candidate_budget=150, max_provider_rounds=3, as_of=NOW,
        audience_evidence_resolver=resolver if resolve else None,
    ))
    return result, calls, enrolled, observations


@pytest.mark.parametrize("location", ["audience_market_annotation", "audience_geo", "qualification_annotations", "raw_platform_data", "raw_platform_data_json"])
def test_late_traceable_audience_reaches_real_collector_gate_with_unchanged_video(location):
    first, second = _raw(), _raw()
    if location == "qualification_annotations":
        second[location] = {"audience_market": _audience()}
    elif location.startswith("raw_platform_data"):
        payload = {"audience_market_annotation": _audience()}
        second["raw_platform_data"] = json.dumps(payload) if location.endswith("json") else payload
    else:
        second[location] = _audience()
    result, calls, enrolled, observations = _run([first, second])
    assert first["latest_real_video"] == second["latest_real_video"]
    assert calls == [150, 149] and len(observations) == 2
    assert result["pending_count"] == 1 and result["returned_count"] == 1
    assert len(enrolled) == 1
    proof = result["items"][0]["qualification_evidence"]
    assert proof["market"]["passed"] is True and proof["passed"] is True


def test_late_follower_count_alias_reaches_real_follower_gate():
    first, second = _raw(), _raw()
    first.pop("followers")
    second.pop("followers")
    second["follower_count"] = 8000
    result, calls, enrolled, _ = _run([first, second], audience=False, followers=True)
    assert calls == [150, 149] and len(enrolled) == 1
    assert result["pending_count"] == 1 and result["returned_count"] == 1
    assert result["items"][0]["qualification_evidence"]["followers"]["passed"] is True


def test_fetch_clock_or_private_blob_alone_still_stops_as_exact_replay():
    first, second, third = _raw(), _raw(), _raw()
    second.update(fetched_at=NOW.isoformat(), raw_platform_data={"email": "private@example.test", "token": "private"})
    third["audience_market_annotation"] = _audience()
    result, calls, enrolled, observations = _run([first, second, third])
    assert calls == [150, 149] and len(observations) == 1
    assert not enrolled and result["returned_count"] == 0
    assert result["candidate_budget_used"] == 2 and result["duplicate_online_count"] == 1
    assert "private@example.test" not in json.dumps(result)


@pytest.mark.parametrize("unknown_ref", [False, True])
def test_unresolved_or_unbound_audience_reference_remains_pending(unknown_ref):
    first, second = _raw(), _raw()
    proof = _audience()
    if unknown_ref:
        proof["evidence_ref"] = "analytics:not-in-fixture-registry"
    second["audience_market_annotation"] = proof
    result, calls, enrolled, observations = _run([first, second], resolve=unknown_ref)
    assert not enrolled and result["returned_count"] == 0
    if unknown_ref:
        assert calls == [150, 149] and len(observations) == 2
        assert result["pending_count"] == 2
    else:
        assert not calls and not observations
        assert result["status"] == "blocked" and result["exhausted"] is False
        assert result["round_gate"]["stopped_by"] == "audience_evidence_source_unavailable"
        assert result["candidate_budget_used"] == result["evaluated_count"] == result["provider_calls"] == 0
        assert result["provider_calls_performed"] is False


@pytest.mark.parametrize("mode", ["include_unknown", "exclude"])
def test_non_require_audience_mode_does_not_block_fetch_without_resolver(mode):
    calls = []
    async def fetch_batch(**_kwargs):
        calls.append(1)
        return {"new_creators": [], "provider_calls": False, "has_more": False}
    result = asyncio.run(online.collect_strict_online_candidates(
        query_text="portrait", policy=online.online_policy(geo_constraints={"audience_markets": ["US"], "audience_mode": mode}),
        local_canonical_keys=set(), fetch_batch=fetch_batch, enroll_candidate=lambda _raw: None,
    ))
    assert calls == [1]
    assert result["round_gate"]["stopped_by"] != "audience_evidence_source_unavailable"


def test_pure_qualification_without_trusted_source_is_pending_not_provider_blocked():
    raw = _raw()
    raw["audience_market_annotation"] = _audience()
    result = online.qualify_online_candidates(
        [raw], query_text="portrait lighting", as_of=NOW,
        policy=online.online_policy(platforms=["youtube"], languages=["en"], profile_types=["creator"],
                                    geo_constraints={"audience_markets": ["US"], "audience_mode": "require"}),
    )
    assert result["counts"] == {"pending": 1}
    assert not result["accepted"]


def test_trusted_resolver_reaches_both_prospective_calibration_and_final_qualification():
    raw = _raw()
    raw.update(bio="food creator using on-camera flash", avg_views=20_000, avg_comments=180,
               engagement_rate=0.08, activation_sample_count=5,
               activation_metrics_source="fixture.recent_video_aggregate", activation_metrics_scope="recent_video_aggregate_45d",
               audience_market_annotation=_audience(), matched_query_cells=[{
                   "query_cell_id": "food", "objective": "prospective_growth", "segment": "food",
                   "primary_query": "food photographer on-camera flash",
                   "required_evidence_groups": ["product_use_fit", "segment_use_case", "market_activation"],
                   "locked_term_groups": build_locked_term_groups(capability="on-camera flash", segment="food"),
               }])
    registry = MappingProxyType({_audience()["evidence_ref"]: MappingProxyType({
        **_audience(), "subject_key": "youtube:handle:lateproof"})})
    calls = []
    def resolver(reference):
        calls.append(reference)
        return dict(registry[reference]) if reference in registry else None
    result = online.qualify_online_candidates(
        [raw], query_text="food photographer on-camera flash", as_of=NOW,
        policy=online.online_policy(platforms=["youtube"], languages=["en"], profile_types=["creator"],
                                    geo_constraints={"audience_markets": ["US"], "audience_mode": "require"}),
        search_brief={"objective": "prospective_growth", "product": {"capability": "on-camera flash"}},
        audience_evidence_resolver=resolver,
    )
    assert len(calls) >= 2  # actual preliminary and final gate paths, not a mocked qualifier
    assert result["counts"] == {"selected": 1}
    assert result["accepted"][0]["qualification_evidence"]["market"]["passed"] is True


def test_session_wrapper_transmits_resolver_only_as_internal_callable(monkeypatch):
    from app.db import connection
    seen = {}
    resolver = lambda _ref: None
    monkeypatch.setattr(connection, "get_conn", lambda: object())
    monkeypatch.setattr(online.profile_online_inventory, "local_identity_snapshot_for_session",
                        lambda *_args, **_kwargs: {"aliases": [], "unique_count": 0})
    monkeypatch.setattr(online.profile_online_inventory, "inventory_alias_snapshot",
                        lambda *_args, **_kwargs: {"aliases": [], "row_count": 0, "db_reads": 0})
    async def collect(**kwargs):
        seen.update(kwargs)
        return {"fixture": True}
    monkeypatch.setattr(online, "collect_strict_online_candidates", collect)
    result = asyncio.run(online.collect_strict_online_for_session(
        session_id=1, query_text="portrait", policy={}, fetch_batch=lambda **_: {},
        audience_evidence_resolver=resolver,
    ))
    assert result == {"fixture": True}
    assert seen["audience_evidence_resolver"] is resolver
    assert "audience_evidence_resolver" not in seen["policy"]
