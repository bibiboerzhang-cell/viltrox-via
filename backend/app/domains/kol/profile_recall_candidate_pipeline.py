"""Candidate-by-candidate gates for Smart local KOL qualification.

This module deliberately owns the mechanical evaluation pipeline while
``profile_recall_qualification`` remains the public compatibility facade and
owns quota selection plus the response contract.  Keeping every gate explicit
is important: an unknown follower count, an untrusted market inference, and an
unknown activity window are three different business facts and must never be
collapsed into a generic score.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from app.domains.kol.profile_follower_filter import FOLLOWERS_UNKNOWN_PENDING
from app.domains.kol.profile_recall_activity_gate import (
    activity_gate_evidence,
    evaluate_activity,
    mark_deferred_item,
    should_defer_activity,
)
from app.domains.kol.profile_recall_language_gate import (
    SELF_REPORTED_SOURCE as LANGUAGE_SELF_REPORTED_SOURCE,
    language_gate_evidence,
    resolve_candidate_language,
)
from app.domains.kol.profile_recall_search_spec import (
    normalize_operator_languages,
    normalize_operator_profile_types,
)


@dataclass(frozen=True)
class CandidateGatePolicy:
    """Normalized, immutable inputs shared by every candidate gate."""

    now: datetime
    target_market: str
    target_platforms: frozenset[str]
    target_languages: frozenset[str]
    target_profile_types: frozenset[str]
    follower_filter: dict[str, Any]
    language_requested: bool
    profile_type_requested: bool
    invalid_languages: tuple[str, ...]
    invalid_profile_types: tuple[str, ...]
    evidence_sources: dict[str, Any]
    unknown_activity: str
    excluded_identities: frozenset[str]
    excluded_identity_reason: str
    excluded_account_types: tuple[str, ...]
    require_trusted_market: bool
    max_video_age_days: int
    fresh_priority_days: int
    gate_schema: str


@dataclass(frozen=True)
class CandidateGateHooks:
    """Facade-owned helpers injected to avoid a circular import."""

    canonical_key: Callable[[dict[str, Any]], str]
    canonical_aliases: Callable[[dict[str, Any]], set[str]]
    account_quality_verdict: Callable[[dict[str, Any], dict[str, Any]], str]
    market_resolution: Callable[[dict[str, Any]], dict[str, Any]]
    identity_aliases: Callable[[dict[str, Any]], Any] | None = None


def _new_funnel() -> dict[str, int]:
    return {
        "candidates_evaluated": 0,
        "evidence_relevant": 0,
        "canonical_unique": 0,
        "account_quality_pass": 0,
        "followers_pass": 0,
        "fresh_video_pass": 0,
        "activity_unknown_deferred": 0,
        "activity_stage_pass": 0,
        "market_pass": 0,
        "language_pass": 0,
        "profile_type_pass": 0,
        "platform_pass": 0,
        "qualified": 0,
        "deferred_available": 0,
        "deferred_returned": 0,
        "returned": 0,
    }


@dataclass
class CandidateGateResult:
    """Evaluation facts consumed by quota selection and contract assembly."""

    funnel: dict[str, int] = field(default_factory=_new_funnel)
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    audit: list[dict[str, Any]] = field(default_factory=list)
    qualified: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"creator": [], "reviewer": []}
    )
    deferred: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"creator": [], "reviewer": []}
    )
    qualified_identity_aliases: set[str] = field(default_factory=set)
    deferred_identity_aliases: set[str] = field(default_factory=set)
    seen_identities: set[str] = field(default_factory=set)
    stage_identities: dict[str, set[str]] = field(
        default_factory=lambda: {
            "account_quality_pass": set(),
            "followers_pass": set(),
            "fresh_video_pass": set(),
            "activity_unknown_deferred": set(),
            "activity_stage_pass": set(),
            "market_pass": set(),
            "language_pass": set(),
            "profile_type_pass": set(),
            "platform_pass": set(),
        }
    )


def _claim_identity_aliases(seen: set[str], aliases: set[str]) -> bool:
    if aliases.intersection(seen):
        return False
    seen.update(aliases)
    return True


def _record_unique_stage(
    result: CandidateGateResult,
    stage: str,
    aliases: set[str],
    *conditions: bool,
) -> None:
    if all(conditions) and _claim_identity_aliases(result.stage_identities[stage], aliases):
        result.funnel[stage] += 1


def _identity(
    item: dict[str, Any], hooks: CandidateGateHooks
) -> tuple[str, set[str]]:
    canonical = hooks.canonical_key(item)
    raw_aliases = (
        hooks.identity_aliases(item)
        if callable(hooks.identity_aliases)
        else hooks.canonical_aliases(item)
    )
    aliases = set(raw_aliases or set())
    return canonical, aliases or {canonical}


def _followers_gate(
    item: dict[str, Any],
    row: dict[str, Any],
    follower_filter: dict[str, Any],
    *,
    legacy_minimum: int,
) -> dict[str, Any]:
    raw = row.get("followers", item.get("followers"))
    try:
        followers = int(raw) if raw is not None and not isinstance(raw, bool) else None
    except (TypeError, ValueError):
        followers = None

    if not follower_filter["requested"]:
        return {"value": followers, "passed": True, "pending": False, "reason": ""}
    if followers is None:
        pending = follower_filter["unknown_policy"] == FOLLOWERS_UNKNOWN_PENDING
        reason = (
            "followers_unknown"
            if pending or follower_filter["legacy"]
            else "followers_unknown_rejected"
        )
        return {"value": None, "passed": False, "pending": pending, "reason": reason}
    if follower_filter["minimum"] is not None and followers < follower_filter["minimum"]:
        reason = (
            "followers_below_3000"
            if follower_filter["legacy"] and follower_filter["minimum"] == legacy_minimum
            else "followers_below_minimum"
        )
        return {"value": followers, "passed": False, "pending": False, "reason": reason}
    if follower_filter["maximum"] is not None and followers > follower_filter["maximum"]:
        return {
            "value": followers,
            "passed": False,
            "pending": False,
            "reason": "followers_above_maximum",
        }
    return {"value": followers, "passed": True, "pending": False, "reason": ""}


def _activity_gate(evidence: dict[str, Any], policy: CandidateGatePolicy) -> dict[str, Any]:
    activity = evaluate_activity(
        latest=evidence.get("latest_real_video"),
        now=policy.now,
        max_video_age_days=policy.max_video_age_days,
        fresh_priority_days=policy.fresh_priority_days,
    )
    passed = bool(activity["passed"])
    deferred = should_defer_activity(activity, policy.unknown_activity)
    return {
        "evidence": activity,
        "passed": passed,
        "deferred": deferred,
        "stage_passed": passed or deferred,
    }


def _market_gate(row: dict[str, Any], policy: CandidateGatePolicy, hooks: CandidateGateHooks) -> dict[str, Any]:
    market = hooks.market_resolution(row)
    value = str(market.get("market") or "")
    passed = bool(value) if policy.require_trusted_market else True
    if policy.target_market:
        passed = bool(value and value == policy.target_market)
    if passed:
        reason = ""
    elif market.get("rejected_source"):
        reason = "market_untrusted_source"
    elif not value:
        reason = "market_unknown"
    else:
        reason = "market_mismatch"
    return {
        "resolution": market,
        "value": value,
        "method": str(market.get("method") or "unknown"),
        "passed": passed,
        "reason": reason,
    }


def _language_gate(
    row: dict[str, Any], item: dict[str, Any], policy: CandidateGatePolicy
) -> dict[str, Any]:
    resolution = resolve_candidate_language(row, item, normalize=normalize_operator_languages)
    values = list(resolution["values"])
    passed = not policy.invalid_languages and (
        not policy.language_requested or bool(policy.target_languages.intersection(values))
    )
    if passed:
        reason = ""
    elif policy.invalid_languages:
        reason = "language_filter_invalid"
    elif not values:
        reason = "language_unknown"
    else:
        reason = "language_mismatch"
    return {"resolution": resolution, "values": values, "passed": passed, "reason": reason}


def _profile_type_gate(
    row: dict[str, Any], item: dict[str, Any], policy: CandidateGatePolicy
) -> dict[str, Any]:
    facets = item.get("candidate_facets") if isinstance(item.get("candidate_facets"), dict) else {}
    values = normalize_operator_profile_types(
        row.get("profile_type") or item.get("profile_type") or facets.get("profile_type")
    )
    passed = not policy.invalid_profile_types and (
        not policy.profile_type_requested
        or bool(policy.target_profile_types.intersection(values))
    )
    if passed:
        reason = ""
    elif policy.invalid_profile_types:
        reason = "profile_type_filter_invalid"
    elif not values:
        reason = "profile_type_unknown"
    else:
        reason = "profile_type_mismatch"
    return {"values": values, "passed": passed, "reason": reason}


def _platform_gate(
    row: dict[str, Any], item: dict[str, Any], policy: CandidateGatePolicy
) -> dict[str, Any]:
    value = str(item.get("platform") or row.get("platform") or "").strip().lower()
    passed = bool(value) and (
        not policy.target_platforms or value in policy.target_platforms
    )
    return {
        "value": value,
        "passed": passed,
        "reason": "" if passed else "platform_unknown" if not value else "platform_mismatch",
    }


def _claim_final_identity(
    *,
    reasons: list[str],
    aliases: set[str],
    activity_deferred: bool,
    policy: CandidateGatePolicy,
    result: CandidateGateResult,
) -> None:
    if reasons:
        return
    if aliases.intersection(policy.excluded_identities):
        reasons.append(policy.excluded_identity_reason)
    elif activity_deferred:
        if aliases.intersection(result.deferred_identity_aliases):
            reasons.append("duplicate_canonical_identity")
        else:
            result.deferred_identity_aliases.update(aliases)
    elif aliases.intersection(result.qualified_identity_aliases):
        reasons.append("duplicate_canonical_identity")
    else:
        result.qualified_identity_aliases.update(aliases)


def _gate_evidence(
    *,
    item: dict[str, Any],
    kol_id: int,
    canonical: str,
    aliases: set[str],
    reasons: list[str],
    relevance_pass: bool,
    account_verdict: str,
    account_quality_pass: bool,
    followers: dict[str, Any],
    activity: dict[str, Any],
    market: dict[str, Any],
    language: dict[str, Any],
    profile_type: dict[str, Any],
    platform: dict[str, Any],
    policy: CandidateGatePolicy,
) -> dict[str, Any]:
    market_resolution = market["resolution"]
    return {
        "schema": policy.gate_schema,
        "kol_pool_id": kol_id,
        "canonical_key": canonical,
        "canonical_aliases": sorted(aliases),
        "passed": not reasons and not activity["deferred"],
        "deferred": activity["deferred"],
        "deferred_reason": activity["evidence"]["reason"] if activity["deferred"] else None,
        "rejection_reasons": reasons,
        "account_quality": {
            "verdict": account_verdict or "eligible_creator_account",
            "excluded_types": list(policy.excluded_account_types),
            "passed": account_quality_pass,
            "source": "existing_discovery_classifiers",
        },
        "followers": {
            "value": followers["value"],
            "minimum": policy.follower_filter["minimum"],
            "maximum": policy.follower_filter["maximum"],
            "known": followers["value"] is not None,
            "filter_requested": policy.follower_filter["requested"],
            "filter_source": policy.follower_filter["source"],
            "unknown_policy": policy.follower_filter["unknown_policy"],
            "status": (
                "passed" if followers["passed"] else "pending" if followers["pending"] else "rejected"
            ),
            "reason": followers["reason"] or None,
            "passed": followers["passed"],
            "source": policy.evidence_sources.get("followers") or "vkpi_kol_pool.followers",
        },
        "activity": activity_gate_evidence(
            activity["evidence"],
            maximum_age_days=policy.max_video_age_days,
            deferred=activity["deferred"],
        ),
        "market": {
            "value": market["value"] or None,
            "target": policy.target_market or None,
            "method": market["method"],
            "confidence": market_resolution.get("confidence"),
            "source": market_resolution.get("source"),
            **(
                {"rejected_source": market_resolution.get("rejected_source")}
                if market_resolution.get("rejected_source")
                else {}
            ),
            "passed": market["passed"],
        },
        "language": language_gate_evidence(
            language["resolution"],
            targets=sorted(policy.target_languages),
            filter_requested=policy.language_requested,
            invalid_targets=list(policy.invalid_languages),
            passed=language["passed"],
            self_source=policy.evidence_sources.get("language") or LANGUAGE_SELF_REPORTED_SOURCE,
        ),
        "profile_type": {
            "values": profile_type["values"],
            "targets": sorted(policy.target_profile_types),
            "filter_requested": policy.profile_type_requested,
            "invalid_targets": list(policy.invalid_profile_types),
            "passed": profile_type["passed"],
            "source": policy.evidence_sources.get("profile_type")
            or "vkpi_kol_profile_embeddings.profile_type",
        },
        "platform": {
            "value": platform["value"] or None,
            "targets": sorted(policy.target_platforms),
            "passed": platform["passed"],
            "source": policy.evidence_sources.get("platform") or "vkpi_kol_pool.platform",
        },
        "relevance": {
            "passed": relevance_pass,
            "evidence": list(item.get("match_evidence") or []),
            "source": "field_level_match_evidence",
        },
    }


def _evaluate_candidate(
    *,
    item: dict[str, Any],
    row: dict[str, Any],
    evidence: dict[str, Any],
    policy: CandidateGatePolicy,
    hooks: CandidateGateHooks,
    result: CandidateGateResult,
    legacy_minimum_followers: int,
) -> None:
    reasons: list[str] = []
    canonical, aliases = _identity(item, hooks)

    relevance_pass = bool(item.get("match_evidence"))
    if relevance_pass:
        result.funnel["evidence_relevant"] += 1
        if _claim_identity_aliases(result.seen_identities, aliases):
            result.funnel["canonical_unique"] += 1
    else:
        reasons.append("low_relevance")

    account_verdict = hooks.account_quality_verdict(item, row)
    account_quality_pass = not account_verdict
    _record_unique_stage(result, "account_quality_pass", aliases, relevance_pass, account_quality_pass)
    if not account_quality_pass:
        reasons.append(f"account_{account_verdict}")

    followers = _followers_gate(
        item,
        row,
        policy.follower_filter,
        legacy_minimum=legacy_minimum_followers,
    )
    _record_unique_stage(
        result,
        "followers_pass",
        aliases,
        relevance_pass,
        account_quality_pass,
        followers["passed"],
    )
    if not followers["passed"]:
        reasons.append(followers["reason"])

    activity = _activity_gate(evidence, policy)
    common_activity_inputs = (relevance_pass, account_quality_pass, followers["passed"])
    _record_unique_stage(
        result, "fresh_video_pass", aliases, *common_activity_inputs, activity["passed"]
    )
    _record_unique_stage(
        result,
        "activity_unknown_deferred",
        aliases,
        *common_activity_inputs,
        activity["deferred"],
    )
    _record_unique_stage(
        result,
        "activity_stage_pass",
        aliases,
        *common_activity_inputs,
        activity["stage_passed"],
    )
    if not activity["stage_passed"]:
        reasons.append(activity["evidence"]["reason"])

    market = _market_gate(row, policy, hooks)
    _record_unique_stage(
        result,
        "market_pass",
        aliases,
        *common_activity_inputs,
        activity["stage_passed"],
        market["passed"],
    )
    if not market["passed"]:
        reasons.append(market["reason"])

    language = _language_gate(row, item, policy)
    _record_unique_stage(
        result,
        "language_pass",
        aliases,
        *common_activity_inputs,
        activity["stage_passed"],
        market["passed"],
        language["passed"],
    )
    if not language["passed"]:
        reasons.append(language["reason"])

    profile_type = _profile_type_gate(row, item, policy)
    _record_unique_stage(
        result,
        "profile_type_pass",
        aliases,
        *common_activity_inputs,
        activity["stage_passed"],
        market["passed"],
        language["passed"],
        profile_type["passed"],
    )
    if not profile_type["passed"]:
        reasons.append(profile_type["reason"])

    platform = _platform_gate(row, item, policy)
    _record_unique_stage(
        result,
        "platform_pass",
        aliases,
        *common_activity_inputs,
        activity["stage_passed"],
        market["passed"],
        language["passed"],
        profile_type["passed"],
        platform["passed"],
    )
    if not platform["passed"]:
        reasons.append(platform["reason"])

    _claim_final_identity(
        reasons=reasons,
        aliases=aliases,
        activity_deferred=activity["deferred"],
        policy=policy,
        result=result,
    )
    gate_evidence = _gate_evidence(
        item=item,
        kol_id=int(item.get("kol_pool_id") or 0),
        canonical=canonical,
        aliases=aliases,
        reasons=reasons,
        relevance_pass=relevance_pass,
        account_verdict=account_verdict,
        account_quality_pass=account_quality_pass,
        followers=followers,
        activity=activity,
        market=market,
        language=language,
        profile_type=profile_type,
        platform=platform,
        policy=policy,
    )
    item["qualification_evidence"] = gate_evidence
    result.audit.append(gate_evidence)

    if reasons:
        for reason in set(reasons):
            result.rejected_by_reason[reason] = result.rejected_by_reason.get(reason, 0) + 1
        return
    bucket = "reviewer" if item.get("bucket") == "reviewer" else "creator"
    if activity["deferred"]:
        result.deferred[bucket].append(mark_deferred_item(item))
    else:
        result.qualified[bucket].append(item)


def evaluate_candidate_pool(
    *,
    buckets: dict[str, list[dict[str, Any]]],
    rows_by_id: dict[int, dict[str, Any]],
    evidence_by_id: dict[int, dict[str, Any]],
    policy: CandidateGatePolicy,
    hooks: CandidateGateHooks,
    legacy_minimum_followers: int,
) -> CandidateGateResult:
    """Evaluate every candidate before quota/limit selection."""
    result = CandidateGateResult()
    candidates = [*buckets.get("creator", []), *buckets.get("reviewer", [])]
    result.funnel["candidates_evaluated"] = len(candidates)
    for item in candidates:
        kol_id = int(item.get("kol_pool_id") or 0)
        _evaluate_candidate(
            item=item,
            row=rows_by_id.get(kol_id, {}),
            evidence=evidence_by_id.get(kol_id, {}),
            policy=policy,
            hooks=hooks,
            result=result,
            legacy_minimum_followers=legacy_minimum_followers,
        )
    return result
