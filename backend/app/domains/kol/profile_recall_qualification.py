"""Server-owned qualification contract for Smart local KOL recall."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from time import perf_counter
from typing import Any

from app.domains.kol.profile_discovery_candidates import normalize_market_constraint
from app.domains.kol.profile_recall_match_evidence import why_fit_from_match_evidence


SMART_LOCAL_TARGET = 30
SMART_LOCAL_MIN_FOLLOWERS = 3_000
SMART_LOCAL_FRESH_DAYS = 30
SMART_LOCAL_MAX_VIDEO_AGE_DAYS = 45
SMART_LOCAL_CANDIDATE_LIMIT = 500
_STRONG_MARKET_SOURCES = {
    "declared_profile",
    "platform_profile",
    "profile_annotation",
    "verified_annotation",
    "manual_annotation",
}
_SMART_LOCAL_PRIVATE_ITEM_FIELDS = {
    "bio",
    "profile_text",
    "raw",
    "raw_data",
    "raw_platform_data",
    "email",
    "business_email",
    "contact_email",
    "phone",
    "phone_number",
    "other_contacts_json",
    "contact",
    "contacts",
    "contact_channels",
    "contact_details",
    "contact_methods",
    "wechat",
    "whatsapp",
    "telegram",
    "line",
}
_SMART_LOCAL_FACET_FIELDS = {
    "platform",
    "country",
    "language",
    "profile_type",
    "contact_available",
    "video_evidence",
}
_SMART_LOCAL_EVIDENCE_FIELDS = {
    "handle",
    "display_name",
    "bio",
    "primary_topic",
    "content_style",
    "secondary_topics_json",
    "profile_text",
    "type_reason",
    "representative_evidence.title",
}
_SMART_LOCAL_EVIDENCE_SOURCES = {"server_profile_evidence"}
_CONTACT_TERM_RE = re.compile(r"@|(?:^|\D)\+?\d(?:[\s().-]*\d){6,}(?:\D|$)")


def smart_local_policy(*, market: Any = "", platforms: Any = None) -> dict[str, Any]:
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
    return {
        "schema": "smart_local_qualified_v1",
        "server_owned": True,
        "target_count": SMART_LOCAL_TARGET,
        "candidate_limit": SMART_LOCAL_CANDIDATE_LIMIT,
        "min_followers": SMART_LOCAL_MIN_FOLLOWERS,
        "fresh_priority_days": SMART_LOCAL_FRESH_DAYS,
        "max_video_age_days": SMART_LOCAL_MAX_VIDEO_AGE_DAYS,
        "market": normalized_market,
        "platforms": normalized_platforms,
        "allow_unknown_followers": False,
        "allow_unknown_or_stale_video": False,
        "allow_unknown_market": False,
        "allow_low_quality_backfill": False,
        "canonical_dedupe": True,
    }


def _private_smart_local_field(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    return bool(
        normalized in _SMART_LOCAL_PRIVATE_ITEM_FIELDS
        or normalized.startswith("raw_")
        or normalized.endswith("_email")
        or normalized.endswith("_phone")
        or ("contact" in normalized and normalized != "contact_available")
    )


def _strip_private_smart_local_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_private_smart_local_values(nested)
            for key, nested in value.items()
            if not _private_smart_local_field(key)
        }
    if isinstance(value, list):
        return [_strip_private_smart_local_values(item) for item in value]
    return value


def _project_match_evidence(value: Any) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        field = str(raw.get("field") or "").strip()
        term = str(raw.get("term") or "").strip()
        source = str(raw.get("source") or "").strip()
        if (
            field not in _SMART_LOCAL_EVIDENCE_FIELDS
            or not term
            or _CONTACT_TERM_RE.search(term)
            or (source and source not in _SMART_LOCAL_EVIDENCE_SOURCES)
        ):
            continue
        evidence = {"field": field, "term": term}
        if source:
            evidence["source"] = source
        projected.append(evidence)
    return projected[:12]


def _project_gate_evidence(value: Any) -> dict[str, Any]:
    gate = _strip_private_smart_local_values(value) if isinstance(value, dict) else {}
    relevance = gate.get("relevance") if isinstance(gate.get("relevance"), dict) else None
    if relevance is not None:
        safe_evidence = _project_match_evidence(relevance.get("evidence"))
        gate["relevance"] = {
            **relevance,
            "passed": bool(safe_evidence),
            "evidence": safe_evidence,
        }
    return gate


def _project_smart_local_item(value: Any) -> dict[str, Any]:
    item = _strip_private_smart_local_values(value) if isinstance(value, dict) else {}
    match_evidence = _project_match_evidence(item.get("match_evidence"))
    item["match_evidence"] = match_evidence
    item["why_fit"] = why_fit_from_match_evidence(match_evidence)
    facets = item.get("candidate_facets") if isinstance(item.get("candidate_facets"), dict) else {}
    item["candidate_facets"] = {
        key: str(facets.get(key) or "unknown")
        for key in _SMART_LOCAL_FACET_FIELDS
    }
    if isinstance(item.get("qualification_evidence"), dict):
        item["qualification_evidence"] = _project_gate_evidence(item["qualification_evidence"])
    return item


def project_smart_local_result(result: dict[str, Any]) -> dict[str, Any]:
    """Remove raw/contact values at the Smart-local API/session boundary only."""
    contract = result.get("local_qualification") if isinstance(result.get("local_qualification"), dict) else {}
    if contract.get("schema") != "smart_local_qualified_v1":
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


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _strong_market_inference(raw_platform_data: Any) -> dict[str, Any]:
    """Accept only an explicit, high-confidence annotation, never free text."""
    raw = _json_dict(raw_platform_data)
    annotations = raw.get("qualification_annotations")
    annotation_dict = annotations if isinstance(annotations, dict) else {}
    candidates = [
        raw.get("market_inference"),
        raw.get("country_inference"),
        raw.get("market_annotation"),
        raw.get("country_annotation"),
        annotation_dict.get("market"),
        annotation_dict.get("country"),
    ]
    if raw.get("inferred_country"):
        candidates.append(
            {
                "value": raw.get("inferred_country"),
                "confidence": raw.get("inferred_country_confidence"),
                "source": raw.get("inferred_country_source"),
                "strength": raw.get("inferred_country_strength"),
            }
        )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        market = _normalize_market(
            candidate.get("value") or candidate.get("market") or candidate.get("country") or candidate.get("code")
        )
        try:
            confidence = float(candidate.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        source = str(candidate.get("source") or "").strip().lower()
        strength = str(candidate.get("strength") or candidate.get("tier") or "").strip().lower()
        if market and confidence >= 0.8 and (
            strength in {"strong", "verified", "declared"} or source in _STRONG_MARKET_SOURCES
        ):
            return {
                "market": market,
                "method": "strong_annotated_inference",
                "confidence": round(confidence, 3),
                "source": source or "annotated_strong",
            }
    return {}


def _canonical_key(item: dict[str, Any]) -> str:
    platform = re.sub(r"[^a-z0-9]", "", str(item.get("platform") or "").lower())
    handle = re.sub(r"[^a-z0-9]", "", str(item.get("handle") or "").lower().lstrip("@"))
    if platform and handle:
        return f"{platform}:{handle}"
    return f"pool:{int(item.get('kol_pool_id') or 0)}"


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
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Gate before limit, then soft-fill unused type quota from the other bucket."""
    started = perf_counter()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    target = SMART_LOCAL_TARGET
    target_market = str(policy.get("market") or "")
    target_platforms = set(policy.get("platforms") or [])
    funnel = {
        "evidence_relevant": 0,
        "canonical_unique": 0,
        "followers_pass": 0,
        "fresh_video_pass": 0,
        "market_pass": 0,
        "platform_pass": 0,
        "qualified": 0,
        "returned": 0,
    }
    rejected_by_reason: dict[str, int] = {}
    audit: list[dict[str, Any]] = []
    qualified: dict[str, list[dict[str, Any]]] = {"creator": [], "reviewer": []}
    seen_identities: set[str] = set()
    qualified_identities: set[str] = set()
    followers_identities: set[str] = set()
    fresh_video_identities: set[str] = set()
    market_identities: set[str] = set()
    platform_identities: set[str] = set()

    candidates = [*buckets.get("creator", []), *buckets.get("reviewer", [])]
    funnel["evidence_relevant"] = len(candidates)
    for item in candidates:
        kol_id = int(item.get("kol_pool_id") or 0)
        row = rows_by_id.get(kol_id, {})
        evidence = evidence_by_id.get(kol_id, {})
        reasons: list[str] = []
        canonical = _canonical_key(item)
        canonical_first_seen = canonical not in seen_identities
        if canonical_first_seen:
            seen_identities.add(canonical)
            funnel["canonical_unique"] += 1

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
        if followers_pass and canonical not in followers_identities:
            followers_identities.add(canonical)
            funnel["followers_pass"] += 1
        if not followers_pass:
            reasons.append("followers_unknown" if followers is None else "followers_below_3000")

        latest = evidence.get("latest_real_video") if isinstance(evidence.get("latest_real_video"), dict) else {}
        posted_at = _parse_datetime(latest.get("posted_at"))
        future_video = bool(posted_at and posted_at > now + timedelta(minutes=5))
        age_days = max(0.0, (now - posted_at).total_seconds() / 86_400.0) if posted_at else None
        activity_pass = (
            age_days is not None
            and not future_video
            and age_days <= SMART_LOCAL_MAX_VIDEO_AGE_DAYS
        )
        if followers_pass and activity_pass and canonical not in fresh_video_identities:
            fresh_video_identities.add(canonical)
            funnel["fresh_video_pass"] += 1
        if not activity_pass:
            reasons.append(
                "latest_video_unknown"
                if age_days is None
                else "latest_video_in_future"
                if future_video
                else "latest_video_stale"
            )

        explicit_market = _normalize_market(row.get("country"))
        inferred = {} if explicit_market else _strong_market_inference(row.get("raw_platform_data"))
        market_value = explicit_market or str(inferred.get("market") or "")
        market_method = "explicit_country" if explicit_market else str(inferred.get("method") or "unknown")
        market_pass = bool(market_value) and (not target_market or market_value == target_market)
        if (
            followers_pass
            and activity_pass
            and market_pass
            and canonical not in market_identities
        ):
            market_identities.add(canonical)
            funnel["market_pass"] += 1
        if not market_pass:
            reasons.append("market_unknown" if not market_value else "market_mismatch")

        platform = str(item.get("platform") or row.get("platform") or "").strip().lower()
        platform_pass = bool(platform) and (not target_platforms or platform in target_platforms)
        if (
            followers_pass
            and activity_pass
            and market_pass
            and platform_pass
            and canonical not in platform_identities
        ):
            platform_identities.add(canonical)
            funnel["platform_pass"] += 1
        if not platform_pass:
            reasons.append("platform_unknown" if not platform else "platform_mismatch")

        # An invalid duplicate must not reserve the identity and hide a later
        # valid row for the same account.  Only a candidate that passed every
        # non-identity gate claims the canonical key.
        if not reasons:
            if canonical in qualified_identities:
                reasons.append("duplicate_canonical_identity")
            else:
                qualified_identities.add(canonical)

        gate_evidence = {
            "schema": "smart_local_gate_evidence_v1",
            "kol_pool_id": kol_id,
            "canonical_key": canonical,
            "passed": not reasons,
            "rejection_reasons": reasons,
            "followers": {
                "value": followers,
                "minimum": SMART_LOCAL_MIN_FOLLOWERS,
                "known": followers is not None,
                "passed": followers_pass,
                "source": "vkpi_kol_pool.followers",
            },
            "activity": {
                "posted_at": posted_at.isoformat() if posted_at else None,
                "age_days": round(age_days, 3) if age_days is not None else None,
                "future_timestamp": future_video,
                "fresh_priority": bool(
                    activity_pass and age_days is not None and age_days <= SMART_LOCAL_FRESH_DAYS
                ),
                "maximum_age_days": SMART_LOCAL_MAX_VIDEO_AGE_DAYS,
                "passed": activity_pass,
                "source": latest.get("source") or "vkpi_kol_video_evidence.posted_at",
            },
            "market": {
                "value": market_value or None,
                "target": target_market or None,
                "method": market_method,
                "confidence": inferred.get("confidence") if inferred else 1.0 if explicit_market else None,
                "source": inferred.get("source") if inferred else "vkpi_kol_pool.country" if explicit_market else None,
                "passed": market_pass,
            },
            "platform": {
                "value": platform or None,
                "targets": sorted(target_platforms),
                "passed": platform_pass,
                "source": "vkpi_kol_pool.platform",
            },
            "relevance": {
                "passed": bool(item.get("match_evidence")),
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
    funnel["returned"] = len(items)
    shortfall = max(0, target - len(items))
    contract = {
        "schema": "smart_local_qualified_v1",
        "status": "ready" if not shortfall else "shortfall",
        "policy": dict(policy),
        "qualified_count": funnel["qualified"],
        "returned_count": len(items),
        "shortfall": shortfall,
        "shortfall_reason": "" if not shortfall else "qualified_candidates_exhausted",
        "funnel": funnel,
        "rejected_by_reason": rejected_by_reason,
        # Per-returned-item proof is complete; rejected rows are summarized
        # and sampled so a 500-row recall cannot inflate the first response.
        "gate_evidence_scope": "returned_candidates",
        "gate_evidence": [item["qualification_evidence"] for item in items],
        "rejected_evidence_sample": [entry for entry in audit if not entry["passed"]][:30],
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
