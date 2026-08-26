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
    activity_gate_evidence,
    evaluate_activity,
    mark_deferred_item,
    select_deferred_backfill,
    should_defer_activity,
    unknown_activity_mode,
)
from app.domains.kol.profile_recall_qualification_projection import (
    _project_gate_evidence,
    _project_smart_local_item,
    _strip_private_smart_local_values,
)
from app.domains.kol.profile_recall_search_spec import (
    normalize_operator_languages,
    normalize_operator_profile_types,
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
    funnel = {
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
    unknown_activity = unknown_activity_mode(policy)
    rejected_by_reason: dict[str, int] = {}
    audit: list[dict[str, Any]] = []
    qualified: dict[str, list[dict[str, Any]]] = {"creator": [], "reviewer": []}
    deferred: dict[str, list[dict[str, Any]]] = {"creator": [], "reviewer": []}
    seen_identities: set[str] = set()
    qualified_identity_aliases: set[str] = set()
    deferred_identity_aliases: set[str] = set()
    account_quality_identities: set[str] = set()
    followers_identities: set[str] = set()
    fresh_video_identities: set[str] = set()
    deferred_activity_identities: set[str] = set()
    activity_stage_identities: set[str] = set()
    market_identities: set[str] = set()
    language_identities: set[str] = set()
    profile_type_identities: set[str] = set()
    platform_identities: set[str] = set()

    candidates = [*buckets.get("creator", []), *buckets.get("reviewer", [])]
    funnel["candidates_evaluated"] = len(candidates)
    for item in candidates:
        kol_id = int(item.get("kol_pool_id") or 0)
        row = rows_by_id.get(kol_id, {})
        evidence = evidence_by_id.get(kol_id, {})
        reasons: list[str] = []
        canonical = _canonical_key(item)
        identity_aliases = (
            set(identity_aliases_fn(item))
            if callable(identity_aliases_fn)
            else canonical_creator_aliases(item)
        ) or {canonical}
        relevance_pass = bool(item.get("match_evidence"))
        if relevance_pass:
            funnel["evidence_relevant"] += 1
        if not relevance_pass:
            reasons.append("low_relevance")
        canonical_first_seen = relevance_pass and _claim_identity_aliases(
            seen_identities, identity_aliases
        )
        if canonical_first_seen:
            funnel["canonical_unique"] += 1

        account_verdict = _account_quality_verdict(item, row)
        account_quality_pass = not account_verdict
        if relevance_pass and account_quality_pass and _claim_identity_aliases(
            account_quality_identities, identity_aliases
        ):
            funnel["account_quality_pass"] += 1
        if not account_quality_pass:
            reasons.append(f"account_{account_verdict}")

        followers_raw = row.get("followers", item.get("followers"))
        try:
            followers = (
                int(followers_raw)
                if followers_raw is not None and not isinstance(followers_raw, bool)
                else None
            )
        except (TypeError, ValueError):
            followers = None
        followers_pass = followers is not None and followers >= SMART_LOCAL_MIN_FOLLOWERS
        if relevance_pass and account_quality_pass and followers_pass and _claim_identity_aliases(
            followers_identities, identity_aliases
        ):
            funnel["followers_pass"] += 1
        if not followers_pass:
            reasons.append("followers_unknown" if followers is None else "followers_below_3000")

        activity = evaluate_activity(
            latest=evidence.get("latest_real_video"),
            now=now,
            max_video_age_days=SMART_LOCAL_MAX_VIDEO_AGE_DAYS,
            fresh_priority_days=SMART_LOCAL_FRESH_DAYS,
        )
        activity_pass = bool(activity["passed"])
        # "We never crawled this creator" is a data gap, not a stale verdict.
        # It leaves the hard-rejection path and waits in the deferred bucket;
        # every other activity failure still fails closed below.
        activity_deferred = should_defer_activity(activity, unknown_activity)
        # Downstream stage counters describe "still in the running", which now
        # includes the deferred bucket.  ``fresh_video_pass`` keeps its exact
        # old meaning so the freshness number can never be read as widened.
        activity_stage_pass = activity_pass or activity_deferred
        if (
            account_quality_pass
            and relevance_pass
            and followers_pass
            and activity_pass
            and _claim_identity_aliases(fresh_video_identities, identity_aliases)
        ):
            funnel["fresh_video_pass"] += 1
        if (
            account_quality_pass
            and relevance_pass
            and followers_pass
            and activity_deferred
            and _claim_identity_aliases(deferred_activity_identities, identity_aliases)
        ):
            funnel["activity_unknown_deferred"] += 1
        if (
            account_quality_pass
            and relevance_pass
            and followers_pass
            and activity_stage_pass
            and _claim_identity_aliases(activity_stage_identities, identity_aliases)
        ):
            funnel["activity_stage_pass"] += 1
        if not activity_stage_pass:
            reasons.append(activity["reason"])

        market = _market_resolution(row)
        market_value = str(market.get("market") or "")
        market_method = str(market.get("method") or "unknown")
        require_trusted_market = policy.get("require_trusted_market") is True
        market_pass = bool(market_value) if require_trusted_market else True
        if target_market:
            market_pass = bool(market_value and market_value == target_market)
        if (
            account_quality_pass
            and relevance_pass
            and followers_pass
            and activity_stage_pass
            and market_pass
            and _claim_identity_aliases(market_identities, identity_aliases)
        ):
            funnel["market_pass"] += 1
        if not market_pass:
            reasons.append(
                "market_untrusted_source"
                if market.get("rejected_source")
                else "market_unknown"
                if not market_value
                else "market_mismatch"
            )

        candidate_languages = normalize_operator_languages(
            row.get("language")
            or item.get("language")
            or (
                item.get("candidate_facets", {}).get("language")
                if isinstance(item.get("candidate_facets"), dict)
                else None
            )
        )
        language_pass = (
            not invalid_languages
            and (
                not language_requested
                or bool(target_languages.intersection(candidate_languages))
            )
        )
        if (
            account_quality_pass
            and relevance_pass
            and followers_pass
            and activity_stage_pass
            and market_pass
            and language_pass
            and _claim_identity_aliases(language_identities, identity_aliases)
        ):
            funnel["language_pass"] += 1
        if not language_pass:
            reasons.append(
                "language_filter_invalid"
                if invalid_languages
                else "language_unknown"
                if not candidate_languages
                else "language_mismatch"
            )

        candidate_profile_types = normalize_operator_profile_types(
            row.get("profile_type")
            or item.get("profile_type")
            or (
                item.get("candidate_facets", {}).get("profile_type")
                if isinstance(item.get("candidate_facets"), dict)
                else None
            )
        )
        profile_type_pass = (
            not invalid_profile_types
            and (
                not profile_type_requested
                or bool(target_profile_types.intersection(candidate_profile_types))
            )
        )
        if (
            account_quality_pass
            and relevance_pass
            and followers_pass
            and activity_stage_pass
            and market_pass
            and language_pass
            and profile_type_pass
            and _claim_identity_aliases(profile_type_identities, identity_aliases)
        ):
            funnel["profile_type_pass"] += 1
        if not profile_type_pass:
            reasons.append(
                "profile_type_filter_invalid"
                if invalid_profile_types
                else "profile_type_unknown"
                if not candidate_profile_types
                else "profile_type_mismatch"
            )

        platform = str(item.get("platform") or row.get("platform") or "").strip().lower()
        platform_pass = bool(platform) and (not target_platforms or platform in target_platforms)
        if (
            account_quality_pass
            and relevance_pass
            and followers_pass
            and activity_stage_pass
            and market_pass
            and language_pass
            and profile_type_pass
            and platform_pass
            and _claim_identity_aliases(platform_identities, identity_aliases)
        ):
            funnel["platform_pass"] += 1
        if not platform_pass:
            reasons.append("platform_unknown" if not platform else "platform_mismatch")

        # An invalid duplicate must not reserve the identity and hide a later
        # valid row for the same account.  Only a candidate that passed every
        # non-identity gate claims the canonical key.
        # A deferred row must never reserve the canonical key ahead of a fully
        # qualified row for the same creator, so the two claims use separate
        # alias registers and the deferred bucket is filtered against the
        # qualified register once the whole pass is done.
        if not reasons:
            if identity_aliases.intersection(excluded_identities):
                reasons.append(excluded_identity_reason)
            elif activity_deferred:
                if identity_aliases.intersection(deferred_identity_aliases):
                    reasons.append("duplicate_canonical_identity")
                else:
                    deferred_identity_aliases.update(identity_aliases)
            elif identity_aliases.intersection(qualified_identity_aliases):
                reasons.append("duplicate_canonical_identity")
            else:
                qualified_identity_aliases.update(identity_aliases)

        gate_evidence = {
            "schema": SMART_LOCAL_GATE_SCHEMA,
            "kol_pool_id": kol_id,
            "canonical_key": canonical,
            "canonical_aliases": sorted(identity_aliases),
            "passed": not reasons and not activity_deferred,
            "deferred": activity_deferred,
            "deferred_reason": activity["reason"] if activity_deferred else None,
            "rejection_reasons": reasons,
            "account_quality": {
                "verdict": account_verdict or "eligible_creator_account",
                "excluded_types": list(policy.get("excluded_account_types") or []),
                "passed": account_quality_pass,
                "source": "existing_discovery_classifiers",
            },
            "followers": {
                "value": followers,
                "minimum": SMART_LOCAL_MIN_FOLLOWERS,
                "known": followers is not None,
                "passed": followers_pass,
                "source": evidence_sources.get("followers") or "vkpi_kol_pool.followers",
            },
            "activity": activity_gate_evidence(
                activity,
                maximum_age_days=SMART_LOCAL_MAX_VIDEO_AGE_DAYS,
                deferred=activity_deferred,
            ),
            "market": {
                "value": market_value or None,
                "target": target_market or None,
                "method": market_method,
                "confidence": market.get("confidence"),
                "source": market.get("source"),
                **(
                    {"rejected_source": market.get("rejected_source")}
                    if market.get("rejected_source")
                    else {}
                ),
                "passed": market_pass,
            },
            "language": {
                "values": candidate_languages,
                "targets": sorted(target_languages),
                "filter_requested": language_requested,
                "invalid_targets": invalid_languages,
                "passed": language_pass,
                "source": evidence_sources.get("language") or "vkpi_kol_profiles.language",
            },
            "profile_type": {
                "values": candidate_profile_types,
                "targets": sorted(target_profile_types),
                "filter_requested": profile_type_requested,
                "invalid_targets": invalid_profile_types,
                "passed": profile_type_pass,
                "source": evidence_sources.get("profile_type") or "vkpi_kol_profile_embeddings.profile_type",
            },
            "platform": {
                "value": platform or None,
                "targets": sorted(target_platforms),
                "passed": platform_pass,
                "source": evidence_sources.get("platform") or "vkpi_kol_pool.platform",
            },
            "relevance": {
                "passed": relevance_pass,
                "evidence": list(item.get("match_evidence") or []),
                "source": "field_level_match_evidence",
            },
        }
        item["qualification_evidence"] = gate_evidence
        audit.append(gate_evidence)
        if reasons:
            for reason in set(reasons):
                rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
            continue
        bucket = "reviewer" if item.get("bucket") == "reviewer" else "creator"
        if activity_deferred:
            deferred[bucket].append(mark_deferred_item(item))
        else:
            qualified[bucket].append(item)

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
