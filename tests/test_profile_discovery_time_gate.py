"""Synthetic, fixed-clock verification. No network, DB or paid provider calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.domains.kol import profile_online_qualification as online
from app.domains.kol import profile_discovery_pipeline_online as pipeline
from app.domains.kol.operator_search_spec import build_operator_search_spec
from app.domains.kol.profile_discovery_time_gate import discovery_time_gate
from app.services.intelligence.account_search_provider_policy import build_provider_discovery_policy

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _provider():
    return build_provider_discovery_policy(recent_activity_max_age_days=365)


def _raw(*, days=2, source="platform_video_api"):
    return {
        "platform": "youtube", "handle": "temporalfixture", "channel_id": "UCtemporalfixture",
        "profile_url": "https://www.youtube.com/@temporalfixture", "followers": 8000,
        "country": "US", "country_source": "platform_profile", "language": "en",
        "language_source": "platform_profile", "profile_type": "creator",
        "profile_type_source": "provider_declared", "bio": "portrait lighting studio tutorial creator",
        "latest_real_video": {
            "posted_at": (NOW - timedelta(days=days)).isoformat(), "video_id": "temporal123",
            "platform": "youtube", "title": "portrait lighting studio tutorial", "source": source,
        },
    }


def _qualify(raw, *, provider=True):
    policy = online.online_policy(
        market="US", platforms=["youtube"], languages=["en"], profile_types=["creator"],
        provider_discovery_policy=_provider() if provider else None,
    )
    return online.qualify_online_candidates([raw], query_text="portrait lighting", policy=policy, as_of=NOW)


@pytest.mark.parametrize("source", ["platform_video_api", "provider_video_item", "platform_content_search"])
def test_recent_sample_passes_same_posterior_gate_for_each_provider_source(source):
    result = _qualify(_raw(source=source))
    proof = result["accepted"][0]["qualification_evidence"]
    assert proof["activity"]["maximum_age_days"] == 365
    assert proof["discovery_window"]["maximum_age_days"] == 45
    assert proof["discovery_window"]["passed"] is True
    assert proof["discovery_window"]["latest_content_proven"] is False
    assert proof["discovery_window"]["provider_status_trusted"] is False


def test_old_relevant_sample_is_not_reclassified_recent_when_fetched_today():
    raw = _raw(days=100)
    raw.update(fetched_at=NOW.isoformat(), discovery_window_evidence={"status": "within_window"})
    assert len(_qualify(raw, provider=False)["accepted"]) == 1
    result = _qualify(raw)
    assert result["accepted"] == []
    assert result["counts"]["rejected"] == 1
    assert result["rejected_by_reason"]["discovery_content_outside_window"] == 1


@pytest.mark.parametrize("days", [None, -2, 46])
def test_unknown_future_and_outside_window_do_not_pass(days):
    raw = _raw(days=days or 2)
    if days is None:
        raw.pop("latest_real_video")
        raw["channel_created_at"] = NOW.isoformat()
        raw["fetched_at"] = NOW.isoformat()
    result = _qualify(raw)
    assert result["accepted"] == []


def test_window_boundary_does_not_round_into_acceptance():
    raw = _raw(days=45)
    assert len(_qualify(raw)["accepted"]) == 1
    raw["latest_real_video"]["posted_at"] = (NOW - timedelta(days=45, seconds=1)).isoformat()
    assert _qualify(raw)["accepted"] == []


def test_fetched_time_and_status_alone_have_no_qualification_authority():
    proof = discovery_time_gate(
        {"fetched_at": NOW.isoformat(), "discovery_window_evidence": {"status": "within_window"}},
        policy=_provider(), now=NOW,
    )
    assert proof["passed"] is False
    assert proof["status"] == "pending"
    assert proof["reason"] == "discovery_content_date_unknown"


def test_invalid_policy_cannot_silently_disable_the_extra_gate():
    policy = _provider()
    policy["discovery_max_age_days"] = 365
    with pytest.raises(ValueError):
        online.online_policy(provider_discovery_policy=policy)


@pytest.mark.parametrize("mode,days", [("strict", 45), ("relaxed", 365)])
def test_online_pipeline_builds_policy_from_rebuilt_spec_not_client_policy(mode, days):
    payload = {"query_text": "portrait lighting", "filters": {"languages": ["ja"]}, "gate_mode": mode}
    payload["operator_search_spec"] = build_operator_search_spec(
        plan={}, body=payload, recall_filters={}, market="US", platforms=["youtube"],
    )
    payload["provider_discovery_policy"] = {"discovery_max_age_days": 5000}
    before = deepcopy(payload)
    policy = pipeline._provider_policy_kwargs(payload)["provider_discovery_policy"]
    assert policy["recent_activity_max_age_days"] == days
    assert policy["discovery_max_age_days"] == 45
    assert policy["language_filter"]["values"] == ["ja"]
    assert payload == before


def test_legacy_without_rebuilt_operator_spec_omits_new_optional_argument():
    assert pipeline._provider_policy_kwargs({"provider_discovery_policy": _provider()}) == {}


@pytest.mark.parametrize("payload,expected", [
    ({"search_mode": "hybrid", "include_new_discovery": True}, {"lane_only": True}),
    ({"search_mode": "hybrid"}, {}),
    ({"search_mode": "saved", "include_new_discovery": True}, {}),
    ({"search_mode": "fresh_network", "include_new_discovery": True}, {}),
])
def test_only_explicit_hybrid_uses_lane_only_session_writes(payload, expected):
    assert pipeline._hybrid_lane_kwargs(payload) == expected


def test_round_gate_and_fetch_share_scalar_reservations_deadline_and_stop_state():
    ledger = pipeline._EvidenceLedger()
    first = ledger.targeted_state()
    first.update(targeted_reserved_estimated_usd=1.2, targeted_started_monotonic=123,
                 targeted_gate_stopped_by="budget", targeted_preflights={"1": {"allowed": True}})
    second = ledger.targeted_state()
    assert second is first
    assert second["targeted_reserved_estimated_usd"] == 1.2
    assert second["targeted_gate_stopped_by"] == "budget"
    assert second["targeted_started_monotonic"] == 123
    assert second["targeted_preflights"] == {"1": {"allowed": True}}
