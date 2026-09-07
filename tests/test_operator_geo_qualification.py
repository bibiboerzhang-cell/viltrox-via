"""Real pure-function integration of operator geo constraints and candidate gates.

All rows below are synthetic fixtures, not provider observations or human labels.
No inventory, database, planner, or provider is called; queue serialization and
worker recall preparation are exercised before the actual qualification chain.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.domains.kol import (
    operator_search_spec,
    profile_discovery_pipeline_stages as stages,
    profile_discovery_queue as queue,
    profile_online_evidence,
    profile_recall_qualification as qualification,
    smart_query_facets,
    targeted_search_runtime,
)
from app.domains.kol.discovery_filters import _int, _text


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
QUERY = "Find independent street photographers"


def _no_io(*_args: Any, **_kwargs: Any) -> None:
    pytest.fail("Pure geo qualification tests must never access database/provider services")


@pytest.fixture(autouse=True)
def _forbid_queue_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue, "get_conn", _no_io)


def _plan(query: str = QUERY, **extra: Any) -> dict[str, Any]:
    return {
        "original_query": query,
        "objective": "prospective_growth",
        "filter_proposal": smart_query_facets.propose_facets(query, {}),
        **extra,
    }


def _context(body: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
    return targeted_search_runtime.prepare_local_search(
        plan=plan or _plan(),
        body=body,
        recall_filters={},
        market="",
        platforms=["youtube"],
    )


def _row(*, country: str | None = "GB", raw: dict[str, Any] | None = None,
         language: str | None = "en", followers: int = 5_000) -> dict[str, Any]:
    return {
        "kol_pool_id": 1,
        "handle": "synthetic-street-photographer",
        "display_name": "Synthetic Street Photographer",
        "platform": "youtube",
        "profile_url": "https://example.test/synthetic-creator",
        "followers": followers,
        "country": country,
        "country_source": "operator_verified",
        "language": language,
        "profile_type": "creator",
        "profile_text": "Independent street photography field work",
        "bio": "Independent street photographer",
        "raw_platform_data": deepcopy(raw or {}),
    }


def _audience(market: str = "US", *, source: str = "platform_audience_analytics",
              verified: Any = True) -> dict[str, Any]:
    return {"audience_market_annotation": {
        "market": market, "source": source, "verified": verified,
        "evidence_ref": f"analytics:synthetic-audience-{market.lower()}", "observed_at": NOW.isoformat(),
    }}


def _evaluate(body: dict[str, Any], row: dict[str, Any], *,
              context: dict[str, Any] | None = None,
              trusted_registry: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    context = context or _context(body)
    item = {
        **deepcopy(row), "bucket": "creator", "recall_rank_score": 1.0,
        "match_evidence": [{"field": "profile_text", "term": "street photography",
                            "source": "synthetic_test_fixture"}],
    }
    items, _buckets, contract = qualification.qualify_local_candidates(
        buckets={"creator": [item], "reviewer": []},
        rows_by_id={1: deepcopy(row)},
        evidence_by_id={1: {"latest_real_video": {
            "posted_at": (NOW - timedelta(days=2)).isoformat(),
            "evidence_type": "video", "content_url": "https://example.test/synthetic-video",
            "source": "vkpi_kol_video_evidence.posted_at",
        }}},
        policy=context["local_qualification_policy"],
        creator_quota=1, reviewer_quota=0, target_count=1, as_of=NOW,
        audience_evidence_resolver=(
            {f"analytics:synthetic-audience-{market.lower()}": {
                 **_audience(market)["audience_market_annotation"],
                 "subject_key": "youtube:handle:synthetic-street-photographer"}
             for market in ("US", "CA", "GB")}.get if trusted_registry else None
        ),
    )
    evidence = (contract["gate_evidence"] or contract["rejected_evidence_sample"])[0]
    return items, evidence


def _dimension(evidence: dict[str, Any], name: str) -> dict[str, Any]:
    return next(value for value in evidence["market"]["dimensions"]
                if value["dimension"] == name)


def test_gb_creator_and_verified_us_audience_satisfy_separate_constraints() -> None:
    body = {"creator_countries": ["GB"], "audience_markets": ["US"]}
    items, evidence = _evaluate(body, _row(raw=_audience()))
    assert len(items) == 1
    assert evidence["passed"] is True
    creator = _dimension(evidence, "creator_country")
    audience = _dimension(evidence, "audience_market")
    assert (creator["value"], creator["status"]) == ("gb", "verified")
    assert (audience["value"], audience["status"]) == ("us", "verified")
    assert audience["proof"]["method"] == "verified_audience_evidence"


def test_author_country_us_alone_does_not_prove_us_audience() -> None:
    items, evidence = _evaluate({"audience_markets": ["US"]}, _row(country="US"))
    assert items == []
    assert evidence["rejection_reasons"] == ["audience_market_unknown"]
    assert _dimension(evidence, "audience_market")["status"] == "unknown"


def test_well_formed_provider_claim_without_server_evidence_binding_stays_unknown() -> None:
    items, evidence = _evaluate({"audience_markets": ["US"]}, _row(raw=_audience()), trusted_registry=False)
    assert items == []
    assert evidence["rejection_reasons"] == ["audience_market_unknown"]
    proof = _dimension(evidence, "audience_market")["proof"]
    assert "audience_reference_unresolved" in proof["missing_evidence_reasons"]


@pytest.mark.parametrize("source", ["llm", "profile_annotation_llm", "model_inference", "unknown"])
def test_generic_model_or_unrecognized_source_cannot_verify_audience(source: str) -> None:
    items, evidence = _evaluate(
        {"audience_markets": ["US"]}, _row(raw=_audience(source=source)),
    )
    assert items == []
    assert _dimension(evidence, "audience_market")["status"] == "unknown"


@pytest.mark.parametrize("verified", [False, None, "true", 1])
def test_audience_verified_requires_literal_boolean_true(verified: Any) -> None:
    items, evidence = _evaluate(
        {"audience_markets": ["US"]}, _row(raw=_audience(verified=verified)),
    )
    assert items == []
    assert _dimension(evidence, "audience_market")["status"] == "unknown"


def test_audience_unknown_is_not_a_known_contradiction() -> None:
    body = {"audience_markets": ["US"]}
    _, unknown = _evaluate(body, _row())
    _, contradicted = _evaluate(body, _row(raw=_audience("CA")))
    assert unknown["rejection_reasons"] == ["audience_market_unknown"]
    assert contradicted["rejection_reasons"] == ["audience_market_mismatch"]
    assert _dimension(unknown, "audience_market")["status"] == "unknown"
    assert _dimension(contradicted, "audience_market")["status"] == "contradicted"


def test_verified_audience_never_substitutes_for_unknown_creator_country() -> None:
    items, evidence = _evaluate(
        {"creator_countries": ["US"], "audience_markets": ["US"]},
        _row(country=None, raw=_audience()),
    )
    assert items == []
    assert evidence["rejection_reasons"] == ["creator_country_unknown"]
    assert _dimension(evidence, "audience_market")["status"] == "verified"


@pytest.mark.parametrize("field,dimension", [
    ("creator_countries", "creator_country"), ("audience_markets", "audience_market"),
])
@pytest.mark.parametrize("mode,state,passes", [
    ("require", "verified", True), ("require", "contradicted", False),
    ("require", "unknown", False),
    ("include_unknown", "verified", True), ("include_unknown", "contradicted", False),
    ("include_unknown", "unknown", True),
    ("exclude", "verified", False), ("exclude", "contradicted", True),
    ("exclude", "unknown", True),
])
def test_geo_modes_preserve_evidence_state_through_actual_gates(
    field: str, dimension: str, mode: str, state: str, passes: bool,
) -> None:
    value = {"verified": "US", "contradicted": "CA", "unknown": None}[state]
    row = (_row(country=value) if field == "creator_countries" else
           _row(raw=_audience(value) if value else {}))
    body = {field: {"values": ["US"], "mode": mode}}
    context = _context(body)
    mode_key = "creator_mode" if field == "creator_countries" else "audience_mode"
    assert context["operator_search_spec"]["policy_inputs"]["geo_constraints"][mode_key] == mode
    assert context["local_qualification_policy"]["geo_constraints"][mode_key] == mode
    items, evidence = _evaluate(body, row, context=context)
    assert bool(items) is passes
    assert evidence["market"]["passed"] is passes
    proof = _dimension(evidence, dimension)
    expected_status = "unknown" if state == "unknown" else "verified" if passes else "contradicted"
    assert proof["status"] == expected_status
    # Status evaluates the requested predicate, while observed facts stay intact:
    # exclude US + a verified CA annotation does not rewrite the annotation to US.
    assert proof["value"] == (value.lower() if value else None)
    if field == "audience_markets" and value:
        assert proof["proof"]["evidence_status"] == "verified"
        assert proof["proof"]["market"] == value.lower()


def test_geo_filter_modes_change_effective_constraint_hash() -> None:
    hashes = {_context({"audience_markets": {"values": ["US"], "mode": mode}})
              ["operator_search_spec"]["constraint_hash"]
              for mode in ("require", "include_unknown", "exclude")}
    assert len(hashes) == 3


def _queued_worker_context(body: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    body = {"query_text": QUERY, "platforms": ["youtube"], **deepcopy(body)}
    filters = queue._smart_profile_recall_filters(body)
    payload = queue._smart_profile_payload(
        query=body["query_text"], body=body, staff=None, session_id=17,
        recall_filters=filters, smart_local_30=False, smart_online_30=True,
        triggered_by_user_id=None,
    )
    payload.update(deepcopy(plan))
    # Client projections are not authoritative, even when they have a valid schema.
    payload["operator_search_spec"] = {
        "schema": operator_search_spec.SCHEMA,
        "constraint_hash": "forged",
        "policy_inputs": {"geo_constraints": {"audience_markets": ["CN"]}},
    }
    deps = SimpleNamespace(
        targeted_search_runtime=targeted_search_runtime,
        profile_recall_qualification=qualification,
        int_value=_int, text=_text,
    )
    planning = stages.PlanningState(
        query=body["query_text"], operator_query=body["query_text"],
        operator_anchor={}, operator_platforms=["youtube"], operator_market="",
    )
    setup = stages.prepare_recall(payload, planning, deps)
    return payload, setup.context


def test_queue_worker_preserves_geo_modes_and_rebuilds_same_operator_spec() -> None:
    body = {
        "query_text": QUERY, "platforms": ["youtube"], "search_mode": "fresh_network",
        "creator_countries": {"values": ["GB"], "mode": "require"},
        "audience_markets": {"values": ["US"], "mode": "include_unknown"},
        "filters": {"languages": {"values": ["en"], "mode": "include_unknown"}},
    }
    plan = _plan()
    preview = _context(body, plan)
    payload, worker = _queued_worker_context(body, plan)
    assert payload["search_mode"] == "fresh_network"
    assert payload["creator_countries"] == body["creator_countries"]
    assert payload["audience_markets"] == body["audience_markets"]
    assert worker["operator_search_spec"]["constraint_hash"] == preview["operator_search_spec"]["constraint_hash"]
    assert payload["operator_search_spec"] == worker["operator_search_spec"]
    assert worker["local_qualification_policy"]["geo_constraints"] == {
        "creator_countries": ["GB"], "audience_markets": ["US"],
        "creator_mode": "require", "audience_mode": "include_unknown",
    }
    assert worker["local_qualification_policy"]["operator_filters"]["languages"]["mode"] == "include_unknown"
    items, evidence = _evaluate(body, _row(language=None), context=worker)
    assert len(items) == 1
    assert _dimension(evidence, "audience_market")["status"] == "unknown"
    assert evidence["language"]["value_status"] == "unknown"


@pytest.mark.parametrize("body", [
    {"content_languages": ["fr"], "follower_min": 3_000},
    {"filters": {"content_languages": ["fr"], "follower_min": 3_000}},
    {"languages": ["de"], "follower_min": 20_000,
     "filters": {"languages": ["fr"], "followers_min": 3_000}},
])
def test_explicit_language_and_follower_aliases_win_after_queue_worker_roundtrip(body: dict[str, Any]) -> None:
    plan = _plan("Find English speaking street photographers", follower_filter={
        "followers_min": 100_000, "source": "llm_inference",
    })
    _, worker = _queued_worker_context(body, plan)
    assert worker["followers_min"] == 3_000
    assert worker["local_qualification_policy"]["languages"] == ["fr"]
    assert worker["recall_filters"]["followers_min"] == 3_000
    assert "follower_min" not in worker["recall_filters"]
    items, evidence = _evaluate(body, _row(language="fr", followers=5_000), context=worker)
    assert len(items) == 1
    assert evidence["followers"]["minimum"] == 3_000
    assert evidence["language"]["targets"] == ["fr"]


@pytest.mark.parametrize("source", ["platform_audience_analytics", "creator_shared_analytics"])
@pytest.mark.parametrize("location", ["top_level", "raw_platform_data", "qualification_annotations", "json"])
def test_online_adapter_preserves_verified_audience_evidence_only(location: str, source: str) -> None:
    annotation = {**_audience(source=source)["audience_market_annotation"],
                  "email": "must-not-be-forwarded@example.test"}
    raw = _row()
    # A valid synthetic account locator is required independently of audience proof.
    raw["profile_url"] = "https://www.youtube.com/@synthetic-street-photographer"
    if location == "top_level":
        raw["audience_market_annotation"] = annotation
    elif location == "raw_platform_data":
        raw["raw_platform_data"] = {"audience_market_annotation": annotation}
    elif location == "qualification_annotations":
        raw["raw_platform_data"] = {"qualification_annotations": {"audience_market": annotation}}
    else:
        raw["raw_platform_data"] = {"audience_market_annotation": annotation}
    raw["raw_platform_data"]["unrelated_private_blob"] = "must-not-be-forwarded"
    if location == "json":
        raw["raw_platform_data"] = json.dumps(raw["raw_platform_data"])
    adapted = profile_online_evidence._candidate_row(raw)
    assert qualification._audience_resolution(adapted)["market"] == ""
    trusted = {**_audience(source=source)["audience_market_annotation"],
               "subject_key": "youtube:handle:synthetic-street-photographer"}
    proof = qualification._audience_resolution(
        adapted, evidence_resolver={trusted["evidence_ref"]: trusted}.get,
    )
    assert proof["market"] == "us"
    assert proof["evidence_status"] == "verified"
    assert proof["source"] == source
    assert "unrelated_private_blob" not in str(adapted["raw_platform_data"])
    assert "must-not-be-forwarded" not in str(adapted["raw_platform_data"])


@pytest.mark.parametrize("source", ["llm", "model_inference", "profile_annotation_llm",
                                    "operator_verified", "manual_verified", "verified_annotation"])
def test_online_adapter_cannot_turn_generic_inference_into_verified_audience(source: str) -> None:
    raw = {**_row(country="US"), **_audience(source=source)}
    raw["raw_platform_data"] = _audience(source=source)
    adapted = profile_online_evidence._candidate_row(raw)
    proof = qualification._audience_resolution(adapted)
    assert proof["market"] == ""
    assert proof["evidence_status"] == "unknown"


@pytest.mark.parametrize("market,verified", [("ZZ", True), ("US maybe", True), ("", True),
                                           ("US", False), ("US", 1), ("US", "true")])
def test_online_analytics_require_legal_market_and_boolean_verified(market: str, verified: Any) -> None:
    raw = {**_row(), **_audience(market, verified=verified)}
    adapted = profile_online_evidence._candidate_row(raw)
    assert qualification._audience_resolution(adapted)["evidence_status"] == "unknown"
