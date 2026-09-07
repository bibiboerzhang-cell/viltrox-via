"""Synthetic defensive evidence-binding contracts; no provider, DB or credentials."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.domains.kol.audience_evidence import (
    normalize_audience_observation, project_online_audience, resolve_audience_evidence,
)
from app.domains.kol.profile_recall_qualification import _audience_resolution

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def _observation(**changes):
    return {"market": "US", "source": "platform_audience_analytics", "verified": True,
            "evidence_ref": "analytics:synthetic-subject-report", "observed_at": NOW.isoformat(), **changes}


def _row(handle="synthetic-subject-a", **changes):
    return {"platform": "youtube", "handle": handle,
            "raw_platform_data": {"audience_market_annotation": _observation()}, **changes}


def test_mismatched_candidate_subject_cannot_use_another_subjects_observation():
    row = _row("synthetic-subject-b")
    trusted = _observation(subject_key="youtube:handle:synthetic-subject-a")
    before = deepcopy(row)
    result = _audience_resolution(row, as_of=NOW, evidence_resolver=lambda _ref: trusted)
    assert result["evidence_status"] == "unknown"
    assert result["missing_evidence_reasons"] == ["audience_subject_mismatch"]
    assert row == before


@pytest.mark.parametrize("trusted_subject", [None, "", "pool:0", "youtube:handle:", "private@example.test"])
def test_unbound_or_malformed_trusted_subject_remains_unknown(trusted_subject):
    result = _audience_resolution(_row(), as_of=NOW,
                                 evidence_resolver=lambda _ref: _observation(subject_key=trusted_subject))
    assert result["evidence_status"] == "unknown"
    assert "subject" in result["missing_evidence_reasons"][0]
    assert "private@" not in str(result)


def test_missing_candidate_identity_cannot_be_supplied_by_raw_annotation():
    raw = {"audience_market_annotation": _observation(subject_key="youtube:handle:synthetic-subject-a"),
           "platform": "youtube", "handle": "synthetic-subject-a"}
    calls = []
    result = _audience_resolution({"kol_pool_id": 1, "raw_platform_data": raw}, as_of=NOW,
        evidence_resolver=lambda ref: calls.append(ref) or _observation(subject_key="youtube:handle:synthetic-subject-a"))
    assert result["evidence_status"] == "unknown" and calls == []


@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59"])
def test_unrepresentable_utc_timestamp_is_invalid_not_an_exception(timestamp):
    observation, reason = normalize_audience_observation(_observation(observed_at=timestamp), as_of=NOW)
    assert observation is None and reason == "observation_timestamp_missing_or_invalid"


@pytest.mark.parametrize("identity,subject", [
    ({"platform": "YT", "handle": "@Synthetic-Subject-A"}, "youtube:handle:synthetic-subject-a"),
    ({"platform": "instagram", "handle": "摄影师"}, "instagram:handle:摄影师"),
    ({"channel_id": "UCsynthetic123456"}, "youtube:id:ucsynthetic123456"),
    ({"handle": "", "profile_url": "https://www.youtube.com/channel/UCsynthetic123456"},
     "youtube:id:ucsynthetic123456"),
])
def test_matching_server_subject_binds_canonical_identity_without_exposing_private_fields(identity, subject):
    row = _row(**identity)
    # Raw annotation identity is descriptive only and cannot override this row.
    row["raw_platform_data"]["channel_id"] = "UCdifferent123456"
    row["raw_platform_data"]["audience_market_annotation"]["subject_key"] = "youtube:handle:other"
    trusted = _observation(subject_key=subject, email="private@example.test", internal_note="not-public")
    before = deepcopy(trusted)
    result = _audience_resolution(row, as_of=NOW, evidence_resolver=lambda _ref: trusted)
    assert result["evidence_status"] == "verified" and result["subject_binding"] == "matched"
    assert result["observations"][0]["subject_binding"] == "matched"
    assert "subject_key" not in str(result) and "private@" not in str(result)
    assert trusted == before


def test_failed_identity_projection_remains_unknown_without_resolving_evidence():
    calls = []
    result = _audience_resolution(_row(identity_projection_passed=False), as_of=NOW,
        evidence_resolver=lambda reference: calls.append(reference) or _observation())
    assert result["missing_evidence_reasons"] == ["audience_subject_missing"] and not calls


def test_direct_legacy_call_without_expected_subject_stays_unknown_and_drops_raw_subject():
    claim = _observation(subject_key="youtube:handle:synthetic-subject-a")
    safe = project_online_audience({"audience_market_annotation": claim})
    assert "subject_key" not in str(safe)
    result = resolve_audience_evidence(safe, as_of=NOW, evidence_resolver=lambda _ref: claim)
    assert result["missing_evidence_reasons"] == ["audience_subject_missing"]
