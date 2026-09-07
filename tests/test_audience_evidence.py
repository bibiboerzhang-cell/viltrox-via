from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domains.kol.audience_evidence import normalize_audience_observation, project_online_audience, resolve_audience_evidence
from app.domains.kol.profile_recall_candidate_pipeline import _market_gate

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
SUBJECT = "youtube:handle:synthetic-creator"


def observation(**changes):
    return {"market": "US", "source": "platform_audience_analytics", "verified": True,
            "evidence_ref": "analytics:synthetic-report", "observed_at": NOW.isoformat(), **changes}


def trusted_observation(**changes):
    return observation(subject_key=SUBJECT, **changes)


@pytest.mark.parametrize("changes,reason", [
    ({"evidence_ref": None}, "missing_or_invalid_evidence_reference"),
    ({"evidence_ref": "https://example.test/?token=private"}, "missing_or_invalid_evidence_reference"),
    ({"evidence_ref": "someone@example.test"}, "missing_or_invalid_evidence_reference"),
    ({"observed_at": None}, "observation_timestamp_missing_or_invalid"),
    ({"observed_at": "2026-09-06"}, "observation_timestamp_missing_or_invalid"),
    ({"observed_at": (NOW + timedelta(hours=1)).isoformat()}, "observation_in_future"),
    ({"source": "llm"}, "unsupported_source"),
    ({"source": {}}, "unsupported_source"),
    ({"verified": "true"}, "verification_not_asserted"),
    ({"market": "US maybe"}, "market_invalid"),
])
def test_unsupported_untraceable_or_future_claim_is_unknown(changes, reason):
    value, rejection = normalize_audience_observation(observation(**changes), as_of=NOW)
    assert value is None and rejection == reason
    proof = resolve_audience_evidence({"audience_market_annotation": observation(**changes)}, as_of=NOW)
    assert proof["market"] == "" and proof["evidence_status"] == "unknown"
    assert reason in proof["missing_evidence_reasons"]


def test_source_flag_alone_is_not_audience_qualification():
    claim = {"market": "US", "source": "platform_audience_analytics", "verified": True}
    assert project_online_audience({"audience_market_annotation": claim}) == {}


def test_anchored_observation_is_not_a_claim_of_currentness_or_independent_verification():
    value, reason = normalize_audience_observation(observation(observed_at="2022-01-01T00:00:00Z"), as_of=NOW)
    assert reason == ""
    assert value["freshness_status"] == "not_evaluated"
    assert value["verification_basis"] == "source_asserted_with_trace"


def test_safe_projection_retains_trace_but_not_arbitrary_private_blob():
    raw = {"audience_market_annotation": observation(evidence_url="https://private.test/?token=secret", email="private@example.test")}
    before = deepcopy(raw)
    safe = project_online_audience(raw)
    assert raw == before
    assert safe["audience_market_annotation"]["evidence_ref"] == "analytics:synthetic-report"
    assert "private" not in str(safe) and "secret" not in str(safe)


@pytest.mark.parametrize("mode,passes", [("require", True), ("include_unknown", True), ("exclude", False)])
def test_multi_market_audience_cannot_be_misclassified_by_first_country(mode, passes):
    raw = {"audience_market_annotation": observation(market="CA"),
           "audience_market_evidence": [observation(market="US", evidence_ref="analytics:second-observation")]}
    registry = {"analytics:synthetic-report": trusted_observation(market="CA"),
                "analytics:second-observation": trusted_observation(evidence_ref="analytics:second-observation")}
    proof = resolve_audience_evidence(raw, as_of=NOW, evidence_resolver=registry.get,
                                      expected_subject_key=SUBJECT)
    assert proof["markets"] == ["ca", "us"]
    policy = SimpleNamespace(geo_constraints={"audience_markets": ["US"], "audience_mode": mode})
    hooks = SimpleNamespace(creator_country_resolution=None, audience_market_resolution=lambda _: proof)
    gate = _market_gate({}, policy, hooks)
    assert gate["passed"] is passes
    assert gate["resolution"]["dimensions"][0]["values"] == ["ca", "us"]


def test_online_multi_observations_roundtrip_without_widening_sources():
    raw = {"audience_market_annotation": observation(market="CA", evidence_ref="analytics:ca-report"),
           "raw_platform_data": {"audience_market_evidence": [observation(), observation(source="operator_verified", market="GB")]}}
    safe = project_online_audience(raw)
    registry = {"analytics:ca-report": trusted_observation(market="CA", evidence_ref="analytics:ca-report"),
                "analytics:synthetic-report": trusted_observation()}
    assert resolve_audience_evidence(safe, as_of=NOW, evidence_resolver=registry.get,
                                     expected_subject_key=SUBJECT)["markets"] == ["ca", "us"]


@pytest.mark.parametrize("resolver", [None, {}.get])
def test_reference_syntax_and_provider_verified_flag_never_authenticate_a_claim(resolver):
    result = resolve_audience_evidence({"audience_market_annotation": observation()}, as_of=NOW, evidence_resolver=resolver)
    assert result["market"] == "" and result["evidence_status"] == "unknown"


@pytest.mark.parametrize("changes", [{"market": "CA"}, {"source": "operator_verified"},
                                       {"observed_at": "2026-09-01T00:00:00Z"}, {"evidence_ref": "analytics:different"}])
def test_resolver_record_must_bind_every_claimed_identity_field(changes):
    result = resolve_audience_evidence({"audience_market_annotation": observation()}, as_of=NOW,
                                       evidence_resolver=lambda _ref: trusted_observation(**changes),
                                       expected_subject_key=SUBJECT)
    assert result["market"] == ""
    assert result["missing_evidence_reasons"] == ["audience_reference_mismatch"]


def test_resolver_exception_fails_closed_without_exposing_private_error():
    def unavailable(_ref):
        raise RuntimeError("secret provider token")
    result = resolve_audience_evidence({"audience_market_annotation": observation()}, as_of=NOW,
                                       evidence_resolver=unavailable, expected_subject_key=SUBJECT)
    assert result["market"] == "" and "secret" not in str(result)
    assert result["missing_evidence_reasons"] == ["audience_reference_unavailable"]
