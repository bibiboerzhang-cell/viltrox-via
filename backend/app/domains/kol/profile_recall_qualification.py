"""Server-owned qualification contract for Smart local KOL recall."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import re
from time import perf_counter
from typing import Any

from app.domains.kol.discovery_filters import _brand_official_verdict, _is_discovery_garbage
from app.domains.kol.identity import (
    canonical_creator_aliases as _shared_creator_aliases,
    canonical_creator_key as _shared_creator_key,
)
from app.domains.kol.profile_discovery_candidates import (
    _is_own_brand_account,
    normalize_market_constraint,
)
from app.domains.kol.profile_recall_activity_gate import (
    DEFERRED_ACTIVITY_STATUS,
    UNKNOWN_ACTIVITY_DEFER,
    UNKNOWN_ACTIVITY_POLICY_KEY,
    UNKNOWN_ACTIVITY_REASON,
    select_deferred_backfill,
    unknown_activity_mode,
)
from app.domains.kol.profile_recall_candidate_pipeline import (
    CandidateGateHooks,
    CandidateGatePolicy,
    evaluate_candidate_pool,
)
from app.domains.kol.profile_recall_qualification_projection import (
    _project_gate_evidence,
    _project_smart_local_item,
    _strip_private_smart_local_values,
)
from app.domains.kol.profile_follower_filter import (
    FOLLOWERS_UNKNOWN_ALLOW,
    FOLLOWERS_UNKNOWN_PENDING,
    FOLLOWERS_UNKNOWN_REJECT,
    effective_follower_filter,
    follower_filter_policy,
)
from app.domains.kol.profile_recall_search_spec import (
    operator_filter_spec,
)


SMART_LOCAL_TARGET = 30
SMART_LOCAL_MIN_FOLLOWERS = 3_000
SMART_LOCAL_FRESH_DAYS = 30
SMART_LOCAL_MAX_VIDEO_AGE_DAYS = 45
SMART_LOCAL_CANDIDATE_LIMIT = 500
SMART_LOCAL_SCHEMA = "smart_local_qualified_v2"
SMART_LOCAL_GATE_SCHEMA = "smart_local_gate_evidence_v2"
SMART_LOCAL_VECTOR_WEIGHT = 0.85
SMART_LOCAL_TYPE_WEIGHT = 0.15
_APPROVED_DECLARED_MARKET_SOURCES = {
    "declared",
    "declaration",
    "declared_profile",
    "manual_annotation",
    "manual_verified",
    "operator_verified",
    "platform_profile",
    "platform_declared",
    "user_declared",
    "verified",
    "verified_annotation",
    "vkpi_kol_pool.country",
}
_FORBIDDEN_MARKET_SOURCES = {
    "ai",
    "ai_inference",
    "llm",
    "llm_inference",
    "model",
    "model_inference",
    "profile_annotation",
    "profile_inference",
}
_AUDIENCE_MARKET_SOURCE_RE = re.compile(r"(?:^|[_-])audience(?:[_-]|$)")
_APPROVED_AUDIENCE_MARKET_SOURCES = {
    "audience_profile_distribution",
    "audience_multi_signal_v1",
    "audience_ensemble_v1",
    "audience_comments_sample_v1",
}


def _effective_follower_filter(policy: dict[str, Any]) -> dict[str, Any]:
    return effective_follower_filter(policy, legacy_minimum=SMART_LOCAL_MIN_FOLLOWERS)


def smart_local_policy(
    *,
    market: Any = "",
    platforms: Any = None,
    languages: Any = None,
    profile_types: Any = None,
) -> dict[str, Any]:
    """Build the immutable Smart-local policy; callers cannot lower its gates."""
    raw_platforms = platforms if isinstance(platforms, (list, tuple, set)) else [platforms]
    normalized_platforms = sorted(
        {
            str(value or "").strip().lower()
            for value in raw_platforms
            if str(value or "").strip() and str(value or "").strip().lower() not in {"all", "*"}
        }
    )
    normalized_market = normalize_market_constraint(market) if str(market or "").strip() else ""
    filter_spec = operator_filter_spec(languages=languages, profile_types=profile_types)
    return {
        "schema": SMART_LOCAL_SCHEMA,
        "policy_version": 2,
        "server_owned": True,
        "target_count": SMART_LOCAL_TARGET,
        "candidate_limit": SMART_LOCAL_CANDIDATE_LIMIT,
        "min_followers": SMART_LOCAL_MIN_FOLLOWERS,
        "fresh_priority_days": SMART_LOCAL_FRESH_DAYS,
        "max_video_age_days": SMART_LOCAL_MAX_VIDEO_AGE_DAYS,
        "market": normalized_market,
        "platforms": normalized_platforms,
        "languages": list(filter_spec["languages"]["values"]),
        "profile_types": list(filter_spec["profile_types"]["values"]),
        "operator_filters": filter_spec,
        "excluded_account_types": [
            "own_brand",
            "brand_official",
            "retailer",
            "garbage",
        ],
        "allow_unknown_followers": False,
        # Live knob, read by ``qualify_local_candidates`` through
        # ``unknown_activity_mode``.  It replaces the former
        # ``allow_unknown_or_stale_video`` flag, which nothing in the
        # repository ever read while reading as if it could open the gate.
        # "defer": a creator we simply never crawled waits behind every
        # qualified candidate and is labelled as pending a re-crawl.
        # "reject": the pre-2026-08 behaviour, a hard rejection.
        # Stale / future / non-video rows are outside this knob's vocabulary
        # and stay hard rejections under either value.
        UNKNOWN_ACTIVITY_POLICY_KEY: UNKNOWN_ACTIVITY_DEFER,
        "allow_unknown_market": not bool(normalized_market),
        "allow_unknown_language": not bool(filter_spec["languages"]["requested"]),
        "allow_unknown_profile_type": not bool(filter_spec["profile_types"]["requested"]),
        "allow_low_quality_backfill": False,
        "canonical_dedupe": True,
    }


def project_smart_local_result(result: dict[str, Any]) -> dict[str, Any]:
    """Remove raw/contact values at the Smart-local API/session boundary only."""
    contract = result.get("local_qualification") if isinstance(result.get("local_qualification"), dict) else {}
    if contract.get("schema") != SMART_LOCAL_SCHEMA:
        return result
    projected = dict(result)
    projected["items"] = [
        _project_smart_local_item(item)
        for item in result.get("items") or []
        if isinstance(item, dict)
    ]
    buckets = result.get("buckets") if isinstance(result.get("buckets"), dict) else {}
    projected["buckets"] = {
        key: [_project_smart_local_item(item) for item in values if isinstance(item, dict)]
        for key, values in buckets.items()
        if isinstance(values, list)
    }
    safe_contract = _strip_private_smart_local_values(contract)
    safe_contract["gate_evidence"] = [
        _project_gate_evidence(item)
        for item in contract.get("gate_evidence") or []
        if isinstance(item, dict)
    ]
    safe_contract["rejected_evidence_sample"] = [
        _project_gate_evidence(item)
        for item in contract.get("rejected_evidence_sample") or []
        if isinstance(item, dict)
    ]
    projected["local_qualification"] = safe_contract
    return projected


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_market(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = normalize_market_constraint(raw)
    if normalized:
        return normalized
    return raw.lower() if re.fullmatch(r"[A-Za-z]{2}", raw) else ""


def _market_candidate_parts(candidate: Any) -> tuple[str, float, str]:
    if not isinstance(candidate, dict):
        return "", 0.0, ""
    market = _normalize_market(
        candidate.get("value")
        or candidate.get("market")
        or candidate.get("country")
        or candidate.get("code")
    )
    try:
        confidence = float(candidate.get("confidence") or candidate.get("country_conf") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    source = str(
        candidate.get("source")
        or candidate.get("method")
        or candidate.get("country_source")
        or ""
    ).strip().lower()
    return market, confidence, source


def _forbidden_market_source(source: Any) -> bool:
    normalized = str(source or "").strip().lower()
    return bool(
        normalized in _FORBIDDEN_MARKET_SOURCES
        or normalized.startswith("profile_annotation")
        or re.search(
            r"(?:^|[_-])(?:ai|llm|model|profile_annotation|profile_inference)(?:[_-]|$)",
            normalized,
        )
    )


def _untrusted_inference_source(raw_platform_data: Any) -> str:
    raw = _json_dict(raw_platform_data)
    annotations = raw.get("qualification_annotations")
    annotation_dict = annotations if isinstance(annotations, dict) else {}
    candidates = [
        raw.get("market_inference"),
        raw.get("country_inference"),
        raw.get("market_annotation"),
        raw.get("country_annotation"),
        raw.get("audience_market_inference"),
        raw.get("audience_country_inference"),
        raw.get("audience_geo"),
        annotation_dict.get("market"),
        annotation_dict.get("country"),
        annotation_dict.get("audience_market"),
        annotation_dict.get("audience_country"),
    ]
    if raw.get("inferred_country_source"):
        candidates.append({"source": raw.get("inferred_country_source")})
    for candidate in candidates:
        _, _, source = _market_candidate_parts(candidate)
        if _forbidden_market_source(source):
            return source
    return ""


def _declared_market_annotation(raw_platform_data: Any) -> dict[str, Any]:
    """Resolve only verified/declaration annotations; model labels never qualify."""
    raw = _json_dict(raw_platform_data)
    annotations = raw.get("qualification_annotations")
    annotation_dict = annotations if isinstance(annotations, dict) else {}
    candidates = [
        raw.get("market_annotation"),
        raw.get("country_annotation"),
        raw.get("declared_market"),
        raw.get("declared_country"),
        annotation_dict.get("market"),
        annotation_dict.get("country"),
    ]
    for candidate in candidates:
        market, confidence, source = _market_candidate_parts(candidate)
        if market and source in _APPROVED_DECLARED_MARKET_SOURCES:
            return {
                "market": market,
                "method": "verified_or_declared_annotation",
                "confidence": round(confidence, 3) if confidence else 1.0,
                "source": source,
            }
    return {}


def _strong_market_inference(raw_platform_data: Any) -> dict[str, Any]:
    """Accept only audience inference with confidence >= 0.8.

    ``profile_annotation`` and generic LLM/model inference remain descriptive;
    declaring them "strong" must not promote them into a hard market fact.
    """
    raw = _json_dict(raw_platform_data)
    annotations = raw.get("qualification_annotations")
    annotation_dict = annotations if isinstance(annotations, dict) else {}
    contextual_candidates = [
        (raw.get("audience_market_inference"), "audience_market_inference"),
        (raw.get("audience_country_inference"), "audience_country_inference"),
        (raw.get("audience_geo"), "audience_geo"),
        (annotation_dict.get("audience_market"), "audience_market"),
        (annotation_dict.get("audience_country"), "audience_country"),
        (raw.get("market_inference"), ""),
        (raw.get("country_inference"), ""),
    ]
    if raw.get("inferred_country"):
        contextual_candidates.append(({
            "value": raw.get("inferred_country"),
            "confidence": raw.get("inferred_country_confidence"),
            "source": raw.get("inferred_country_source"),
        }, ""))
    for candidate, contextual_source in contextual_candidates:
        market, confidence, source = _market_candidate_parts(candidate)
        source = source or contextual_source
        if (
            market
            and confidence >= 0.8
            and source in _APPROVED_AUDIENCE_MARKET_SOURCES
            and _AUDIENCE_MARKET_SOURCE_RE.search(source)
            and not _forbidden_market_source(source)
        ):
            return {
                "market": market,
                "method": "strong_audience_inference",
                "confidence": round(confidence, 3),
                "source": source,
            }
    return {}


def _market_resolution(row: dict[str, Any]) -> dict[str, Any]:
    """Resolve a hard-gate market value with explicit provenance."""
    raw = _json_dict(row.get("raw_platform_data"))
    explicit_market = _normalize_market(row.get("country"))
    explicit_source = str(
        row.get("country_source")
        or raw.get("country_source")
        or raw.get("market_source")
        or raw.get("inferred_country_source")
        or ""
    ).strip().lower()
    inferred_source = _untrusted_inference_source(raw)
    rejected_source = ""
    if explicit_market:
        # Legacy pool rows predate provenance storage.  The dedicated country
        # column remains an explicit source unless an inference source is
        # actually recorded alongside it.
        source = explicit_source or inferred_source or "vkpi_kol_pool.country"
        if source in _APPROVED_DECLARED_MARKET_SOURCES:
            return {
                "market": explicit_market,
                "method": "explicit_country",
                "confidence": 1.0,
                "source": source,
            }
        rejected_source = source

    declared = _declared_market_annotation(raw)
    if declared:
        return declared
    audience = _strong_market_inference(raw)
    if audience:
        return audience
    untrusted_source = rejected_source or inferred_source
    return {
        "market": "",
        "method": "unknown",
        "confidence": None,
        "source": None,
        "rejected_source": untrusted_source or None,
        "rejected_as_inference": bool(
            untrusted_source and _forbidden_market_source(untrusted_source)
        ),
    }


def _account_quality_verdict(item: dict[str, Any], row: dict[str, Any]) -> str:
    merged = dict(row)
    for key, value in item.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    if row.get("identity_projection_passed") is False:
        return "unsafe_identity"
    if _is_own_brand_account(merged):
        return "own_brand"
    if _brand_official_verdict(merged):
        return "brand_official"
    if _is_discovery_garbage(merged):
        handle = str(merged.get("handle") or "").strip()
        name = str(merged.get("channel_name") or "").strip().lower()
        return "garbage" if not handle and name in {"", "unknown creator"} else "retailer"
    return ""


def canonical_creator_aliases(item: dict[str, Any]) -> set[str]:
    """Compatibility export for the shared discovery/session/pool identity."""
    return _shared_creator_aliases(item)


def _canonical_key(item: dict[str, Any]) -> str:
    return _shared_creator_key(item) or "pool:0"


def canonical_creator_key(item: dict[str, Any]) -> str:
    """Return the shared server canonical identity for local and online lanes."""
    return _canonical_key(item)


def _claim_identity_aliases(seen: set[str], aliases: set[str]) -> bool:
    """Claim one observed creator once, even when its strongest key changes."""
    if aliases.intersection(seen):
        return False
    seen.update(aliases)
    return True


def _score_key(item: dict[str, Any]) -> tuple[float, float, float]:
    gate = item.get("qualification_evidence") if isinstance(item.get("qualification_evidence"), dict) else {}
    activity = gate.get("activity") if isinstance(gate.get("activity"), dict) else {}
    try:
        age = float(activity.get("age_days"))
    except (TypeError, ValueError):
        age = 10_000.0
    fresh_bucket = 1.0 if age <= SMART_LOCAL_FRESH_DAYS else 0.0

    def _number(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return (
        fresh_bucket,
        _number(item.get("display_rank_score")),
        _number(item.get("recall_rank_score")),
    )


def qualify_local_candidates(
    *,
    buckets: dict[str, list[dict[str, Any]]],
    rows_by_id: dict[int, dict[str, Any]],
    evidence_by_id: dict[int, dict[str, Any]],
    policy: dict[str, Any],
    creator_quota: int,
    reviewer_quota: int,
    as_of: datetime | None = None,
    target_count: int | None = None,
    excluded_canonical_keys: set[str] | None = None,
    excluded_identity_reason: str = "duplicate_canonical_identity",
    excluded_identity_aliases: set[str] | None = None,
    identity_aliases_fn: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Gate before limit, then soft-fill unused type quota from the other bucket."""
    started = perf_counter()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    target = max(1, min(int(target_count or SMART_LOCAL_TARGET), SMART_LOCAL_TARGET))
    excluded_identities = {
        str(value or "").strip()
        for value in (excluded_canonical_keys or set())
        if str(value or "").strip()
    }
    excluded_identities.update({
        str(value or "").strip()
        for value in (excluded_identity_aliases or set())
        if str(value or "").strip()
    })
    target_market = str(policy.get("market") or "")
    target_platforms = set(policy.get("platforms") or [])
    target_languages = set(policy.get("languages") or [])
    target_profile_types = set(policy.get("profile_types") or [])
    follower_filter = _effective_follower_filter(policy)
    operator_filters = policy.get("operator_filters") if isinstance(policy.get("operator_filters"), dict) else {}
    language_filter = (
        operator_filters.get("languages")
        if isinstance(operator_filters.get("languages"), dict)
        else {}
    )
    profile_type_filter = (
        operator_filters.get("profile_types")
        if isinstance(operator_filters.get("profile_types"), dict)
        else {}
    )
    language_requested = bool(language_filter.get("requested") or target_languages)
    profile_type_requested = bool(profile_type_filter.get("requested") or target_profile_types)
    invalid_languages = list(language_filter.get("invalid") or [])
    invalid_profile_types = list(profile_type_filter.get("invalid") or [])
    evidence_sources = (
        policy.get("evidence_sources")
        if isinstance(policy.get("evidence_sources"), dict)
        else {}
    )
    unknown_activity = unknown_activity_mode(policy)
    gate_policy = CandidateGatePolicy(
        now=now,
        target_market=target_market,
        target_platforms=frozenset(target_platforms),
        target_languages=frozenset(target_languages),
        target_profile_types=frozenset(target_profile_types),
        follower_filter=dict(follower_filter),
        language_requested=language_requested,
        profile_type_requested=profile_type_requested,
        invalid_languages=tuple(invalid_languages),
        invalid_profile_types=tuple(invalid_profile_types),
        evidence_sources=dict(evidence_sources),
        unknown_activity=unknown_activity,
        excluded_identities=frozenset(excluded_identities),
        excluded_identity_reason=excluded_identity_reason,
        excluded_account_types=tuple(policy.get("excluded_account_types") or []),
        require_trusted_market=policy.get("require_trusted_market") is True,
        max_video_age_days=SMART_LOCAL_MAX_VIDEO_AGE_DAYS,
        fresh_priority_days=SMART_LOCAL_FRESH_DAYS,
        gate_schema=SMART_LOCAL_GATE_SCHEMA,
    )
    gate_result = evaluate_candidate_pool(
        buckets=buckets,
        rows_by_id=rows_by_id,
        evidence_by_id=evidence_by_id,
        policy=gate_policy,
        hooks=CandidateGateHooks(
            canonical_key=_canonical_key,
            canonical_aliases=canonical_creator_aliases,
            account_quality_verdict=_account_quality_verdict,
            market_resolution=_market_resolution,
            identity_aliases=identity_aliases_fn if callable(identity_aliases_fn) else None,
        ),
        legacy_minimum_followers=SMART_LOCAL_MIN_FOLLOWERS,
    )
    funnel = gate_result.funnel
    rejected_by_reason = gate_result.rejected_by_reason
    audit = gate_result.audit
    qualified = gate_result.qualified
    deferred = gate_result.deferred
    qualified_identity_aliases = gate_result.qualified_identity_aliases
    for values in qualified.values():
        values.sort(key=_score_key, reverse=True)
    funnel["qualified"] = len(qualified["creator"]) + len(qualified["reviewer"])

    creator_target = min(max(0, int(creator_quota)), target)
    reviewer_target = min(max(0, int(reviewer_quota)), max(0, target - creator_target))
    selected_creator = qualified["creator"][:creator_target]
    selected_reviewer = qualified["reviewer"][:reviewer_target]
    selected_ids = {id(item) for item in [*selected_creator, *selected_reviewer]}
    remaining = [
        item
        for item in [*qualified["creator"], *qualified["reviewer"]]
        if id(item) not in selected_ids
    ]
    remaining.sort(key=_score_key, reverse=True)
    for item in remaining[: max(0, target - len(selected_ids))]:
        if item.get("bucket") == "reviewer":
            selected_reviewer.append(item)
        else:
            selected_creator.append(item)
    items = [*selected_creator, *selected_reviewer]
    items.sort(key=_score_key, reverse=True)

    # Deferred backfill: unknown-activity creators fill the slots that the
    # qualified pool could not, never more, and always behind every qualified
    # candidate — hence the concatenation instead of a shared sort.
    deferred_selected, deferred_superseded = select_deferred_backfill(
        deferred_items=[*deferred["creator"], *deferred["reviewer"]],
        qualified_aliases=qualified_identity_aliases,
        capacity=max(0, target - len(items)),
        sort_key=_score_key,
    )
    for entry in deferred_superseded:
        proof = entry.get("qualification_evidence")
        if isinstance(proof, dict):
            proof["passed"] = False
            proof["deferred"] = False
            proof["rejection_reasons"] = ["duplicate_canonical_identity"]
        rejected_by_reason["duplicate_canonical_identity"] = (
            rejected_by_reason.get("duplicate_canonical_identity", 0) + 1
        )
    funnel["deferred_available"] = len(
        [*deferred["creator"], *deferred["reviewer"]]
    ) - len(deferred_superseded)
    funnel["deferred_returned"] = len(deferred_selected)
    for entry in deferred_selected:
        if entry.get("bucket") == "reviewer":
            selected_reviewer.append(entry)
        else:
            selected_creator.append(entry)
    items.extend(deferred_selected)
    funnel["returned"] = len(items)
    # The deferred bucket is a *separate zone*, not part of the 30-person
    # target: an unknown-activity creator has not satisfied the activity gate,
    # so counting it here would report a gap of 0 while zero candidates
    # actually qualified.  The gap is therefore measured against the qualified
    # rows alone, exactly as it was before the bucket existed.
    qualified_returned = len(items) - len(deferred_selected)
    shortfall = max(0, target - qualified_returned)
    contract = {
        "schema": SMART_LOCAL_SCHEMA,
        "status": "ready" if not shortfall else "shortfall",
        "policy": dict(policy),
        "qualified_count": funnel["qualified"],
        "returned_count": len(items),
        "qualified_returned_count": qualified_returned,
        "shortfall": shortfall,
        "shortfall_reason": "" if not shortfall else "qualified_candidates_exhausted",
        # Unknown activity is a crawl gap, so it is reported separately and
        # never folded into the freshness numbers above.
        "deferred_activity": {
            "policy": unknown_activity,
            "reason_code": UNKNOWN_ACTIVITY_REASON,
            "status": DEFERRED_ACTIVITY_STATUS,
            "available": funnel["deferred_available"],
            "returned": funnel["deferred_returned"],
            # Never part of the target count, but the operator may still tick
            # one deliberately — occupying a slot implies selectable, not the
            # other way round.  Both halves are asserted end to end.
            "counts_toward_target": False,
            "selectable": True,
            "max_video_age_days": SMART_LOCAL_MAX_VIDEO_AGE_DAYS,
            "fresh_priority_days": SMART_LOCAL_FRESH_DAYS,
        },
        "funnel": funnel,
        "rejected_by_reason": rejected_by_reason,
        # Per-returned-item proof is complete; rejected rows are summarized
        # and sampled so a 500-row recall cannot inflate the first response.
        "gate_evidence_scope": "returned_candidates",
        "gate_evidence": [item["qualification_evidence"] for item in items],
        "rejected_evidence_sample": [
            entry for entry in audit if not entry["passed"] and not entry.get("deferred")
        ][:30],
        "evaluated_count": len(audit),
        "stage_timing": {
            "qualification_ms": round((perf_counter() - started) * 1000.0, 3),
        },
        "ratio_policy": {
            "policy": "soft",
            "creator_target": creator_target,
            "reviewer_target": reviewer_target,
            "unused_quota_backfilled": (
                len(items)
                - min(len(qualified["creator"]), creator_target)
                - min(len(qualified["reviewer"]), reviewer_target)
            ),
        },
    }
    return items, {"creator": selected_creator, "reviewer": selected_reviewer}, contract
