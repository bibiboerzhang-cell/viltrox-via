"""Conservative read-side truth projection for KOL metrics.

This module never infers a missing metric from another metric and never turns a
missing value into zero.  A stored zero is returned only when the persisted raw
provider payload (pool metrics) or scrape receipt (video evidence metrics)
proves that zero was actually observed.  Manually declared non-zero values may
remain visible, but are labelled ``declared`` rather than factual/observed.

Audience estimates remain in their explicitly-estimated field only when a real
sample and method receipt exist.  Planned or unsupported brand collaborations
are removed from the factual collaboration list.
"""
from __future__ import annotations

import json
import math
import urllib.parse
from typing import Any, Iterable, Mapping

from app.domains.kol.metric_truth_index import build_raw_metric_evidence_index

VERSION = "kol_metric_truth_v1"
CLAIM_STATUS = "descriptive_only"
UNKNOWN_TOKENS = frozenset(
    {"", "unknown", "n/a", "na", "none", "null", "nil", "-", "--", "未知", "未提供", "待补全"}
)
SUCCESS_STATUSES = frozenset({"ok", "success", "succeeded", "synced", "ready", "complete", "completed"})
FAILURE_STATUSES = frozenset({"error", "failed", "failure", "not_found", "no_results", "timeout", "blocked"})

POOL_NUMERIC_FIELDS = (
    "followers",
    "avg_views",
    "avg_likes",
    "avg_comments",
    "engagement_rate",
)
EVIDENCE_NUMERIC_FIELDS = ("view_count", "like_count", "comment_count", "share_count")

_FIELD_ALIASES = {
    "followers": {
        "followers",
        "followerscount",
        "followercount",
        "follower_count",
        "subscribercount",
        "subscriber_count",
        "subscribers",
        "fans",
        "fanscount",
    },
    "avg_views": {"avg_views", "averageviews", "average_views", "meanviews", "mean_views"},
    "avg_likes": {"avg_likes", "averagelikes", "average_likes", "meanlikes", "mean_likes"},
    "avg_comments": {
        "avg_comments",
        "averagecomments",
        "average_comments",
        "meancomments",
        "mean_comments",
    },
    "engagement_rate": {"engagement_rate", "engagementrate", "engagement", "er"},
}
_CONTENT_ALIASES = {
    "avg_views": {"viewcount", "view_count", "views", "playcount", "play_count", "videoplaycount"},
    "avg_likes": {"likecount", "like_count", "likes", "diggcount", "digg_count"},
    "avg_comments": {"commentcount", "comment_count", "comments", "replycount", "reply_count"},
}
_STATUS_KEYS = {
    "provider_status",
    "providerstatus",
    "sync_status",
    "syncstatus",
    "scrape_status",
    "scrapestatus",
    "kpi_status",
    "kpistatus",
    "status",
}
_HIDDEN_FOLLOWER_KEYS = {"hiddensubscribercount", "hidden_subscriber_count", "followershidden"}
_PLAN_STATUSES = {"planned", "plan", "proposed", "proposal", "recommended", "potential", "draft", "pending"}
_CONFIRMED_STATUSES = {"confirmed", "completed", "published", "observed", "active", "contracted", "paid"}
_COLLAB_EVIDENCE_KEYS = {
    "evidence_url",
    "content_url",
    "published_url",
    "observed_at",
    "published_at",
    "evidence_id",
    "contract_id",
    "campaign_id",
    "project_id",
    "source_ref",
}
_OBSERVED_AT_KEYS = {
    "metrics_scraped_at",
    "metricsscrapedat",
    "scraped_at",
    "scrapedat",
    "fetched_at",
    "fetchedat",
    "observed_at",
    "observedat",
    "collected_at",
    "collectedat",
    "updated_at",
    "updatedat",
}


def _key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _known_text(value: Any) -> bool:
    return _text(value).casefold() not in UNKNOWN_TOKENS


def _public_source_type(value: Any) -> str | None:
    if not _known_text(value):
        return None
    safe = "".join(character for character in _text(value) if character.isalnum() or character in "_.:-")
    return safe[:64] or None


def _public_source_ref(value: Any) -> str | None:
    """Return a useful provenance hint without leaking paths, query tokens, or credentials."""

    if not _known_text(value):
        return None
    text = _text(value)
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme and parsed.netloc:
        try:
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
        except ValueError:
            host, port = "", ""
        secret_markers = (
            "password",
            "passwd",
            "secret",
            "bearer",
            "api_key",
            "apikey",
            "token",
            "authorization",
        )
        safe_path = "" if any(marker in parsed.path.casefold() for marker in secret_markers) else parsed.path[:120]
        return f"{parsed.scheme}://{host}{port}{safe_path}"[:180] if host else "redacted_source_ref"
    token_free = text.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    basename = token_free.rsplit("/", 1)[-1]
    lowered = basename.casefold()
    if "@" in basename or any(
        secret in lowered
        for secret in ("password", "passwd", "secret", "bearer", "api_key", "apikey", "token", "authorization")
    ):
        return "redacted_source_ref"
    return basename[:120] or "source_ref_present"


def _public_timestamp(value: Any) -> str | None:
    return _text(value)[:64] if _known_text(value) else None


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _number(value: Any, *, percent: bool = False) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and value.strip().casefold() in UNKNOWN_TOKENS:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    if percent and parsed > 100:
        return None
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _walk(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Iterable[dict[str, Any]]:
    if budget is None:
        budget = [5000]
    if depth > 10 or budget[0] <= 0:
        return
    if isinstance(value, dict):
        budget[0] -= 1
        yield value
        for nested in value.values():
            yield from _walk(nested, depth=depth + 1, budget=budget)
    elif isinstance(value, list):
        for nested in value[:500]:
            yield from _walk(nested, depth=depth + 1, budget=budget)


def _values_for_keys(value: Any, aliases: set[str]) -> list[int | float]:
    values: list[int | float] = []
    normalized_aliases = {_key(alias) for alias in aliases}
    for record in _walk(value):
        if _record_has_failure_marker(record):
            continue
        for raw_key, raw_value in record.items():
            if _key(raw_key) not in normalized_aliases:
                continue
            parsed = _number(raw_value)
            if parsed is not None:
                values.append(parsed)
    return values


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).casefold() in {"1", "true", "yes", "y"}


def _record_has_failure_marker(record: Mapping[str, Any]) -> bool:
    for raw_key, raw_value in record.items():
        normalized = _key(raw_key)
        token = _text(raw_value).casefold().replace("-", "_").replace(" ", "_")
        if normalized in _STATUS_KEYS and token in FAILURE_STATUSES:
            return True
        if normalized in {"error", "error_code", "errorcode", "reason"} and (
            token in FAILURE_STATUSES
            or "no_result" in token
            or "not_found" in token
            or "failed" in token
        ):
            return True
    return False


def _content_record(record: Mapping[str, Any]) -> bool:
    if _record_has_failure_marker(record):
        return False
    kind = _text(record.get("kind") or record.get("type") or record.get("mediaType")).casefold()
    if "channel" in kind or "profile" in kind or "user" == kind:
        return False
    if any(token in kind for token in ("video", "post", "tweet", "reel", "short", "clip")):
        return True
    keys = {_key(key) for key in record}
    has_identity = bool(keys & {"id", "url", "videourl", "webvideourl", "content_url", "shortcode"})
    has_content = bool(keys & {"title", "text", "caption", "createtime", "publishedat", "timestamp"})
    has_metric = bool(keys & set().union(*_CONTENT_ALIASES.values()))
    return has_identity and (has_content or has_metric)


def _content_metric_values(raw: Any, field: str) -> list[int | float]:
    aliases = _CONTENT_ALIASES.get(field) or set()
    values: list[int | float] = []
    if not aliases:
        return values
    for record in _walk(raw):
        if not _content_record(record):
            continue
        values.extend(_values_for_keys(record, aliases))
    return values


def _raw_metric_evidence(
    raw: Any,
    field: str,
) -> tuple[list[int | float], list[int | float]]:
    """Collect field evidence once for all truth checks on one Pool card."""
    return (
        _values_for_keys(raw, _FIELD_ALIASES.get(field) or set()),
        _content_metric_values(raw, field),
    )


def _matches(value: int | float, expected: int | float, *, tolerance: float) -> bool:
    return abs(float(value) - float(expected)) <= tolerance


def _raw_metric_match(
    raw: Any,
    field: str,
    stored: int | float,
    *,
    explicit_values: list[int | float] | None = None,
    content_values: list[int | float] | None = None,
) -> tuple[bool, str | None, int]:
    """Require field-level agreement; raw field presence alone is not a receipt."""

    explicit = (
        explicit_values
        if explicit_values is not None
        else _values_for_keys(raw, _FIELD_ALIASES.get(field) or set())
    )
    if field == "followers":
        matched = any(_matches(value, stored, tolerance=0.5) for value in explicit)
        return matched, "raw_profile_value" if matched else None, len(explicit)

    if field == "engagement_rate":
        tolerance = max(0.01, abs(float(stored)) * 0.02)
        matched = any(_matches(value, stored, tolerance=tolerance) for value in explicit)
        return matched, "raw_explicit_engagement_rate" if matched else None, len(explicit)

    tolerance = max(1.0, abs(float(stored)) * 0.02)
    if any(_matches(value, stored, tolerance=tolerance) for value in explicit):
        return True, "raw_explicit_average", len(explicit)

    content = content_values if content_values is not None else _content_metric_values(raw, field)
    if not content:
        return False, None, 0
    sample_mean = sum(float(value) for value in content) / len(content)
    mean_tolerance = max(1.0, abs(sample_mean) * 0.03)
    matched = _matches(sample_mean, stored, tolerance=mean_tolerance)
    return matched, "raw_content_sample_mean" if matched else None, len(content)


def _raw_source_state(raw: Any, *, records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(raw, (dict, list)):
        return {"present": False, "successful": False, "source": None}
    statuses: list[str] = []
    hidden_followers = False
    source = ""
    observed_at = ""
    for record in records if records is not None else _walk(raw):
        if not source:
            source = _text(record.get("source") or record.get("provider_source") or record.get("metrics_source"))
        for raw_key, value in record.items():
            normalized = _key(raw_key)
            if not observed_at and normalized in _OBSERVED_AT_KEYS and _known_text(value):
                observed_at = _text(value)
            if normalized in _STATUS_KEYS and _known_text(value):
                statuses.append(_text(value).casefold().replace("-", "_").replace(" ", "_"))
            if normalized in _HIDDEN_FOLLOWER_KEYS and _truthy(value):
                hidden_followers = True
        if _record_has_failure_marker(record):
            statuses.append("failure")
    has_success = any(status in SUCCESS_STATUSES for status in statuses)
    has_failure = any(status in FAILURE_STATUSES for status in statuses)
    recognized_source = bool(source and any(token in source.casefold() for token in (
        "youtube", "instagram", "tiktok", "facebook", "reddit", "apify", "crawler", "api", "profile_crawl"
    )))
    return {
        "present": True,
        "successful": bool(has_success or (recognized_source and not has_failure)),
        "source": _public_source_ref(source),
        "observed_at": _public_timestamp(observed_at),
        "hidden_followers": hidden_followers,
        "statuses": sorted(set(statuses)),
    }


def _pool_metric_projection(
    item: Mapping[str, Any],
    field: str,
    raw: Any,
    *,
    source_state: dict[str, Any] | None = None,
    raw_evidence: tuple[list[int | float], list[int | float]] | None = None,
) -> tuple[Any, dict[str, Any]]:
    parsed = _number(item.get(field), percent=field == "engagement_rate")
    source_type = _text(item.get("source_type"))
    source_ref = _text(item.get("source_ref"))
    source_state = source_state if source_state is not None else _raw_source_state(raw)
    source_label = (
        source_state.get("source")
        or _public_source_ref(source_ref)
        or _public_source_type(source_type)
    )
    recorded_at = _public_timestamp(item.get("last_seen_at") or item.get("updated_at"))
    base = {
        "source": source_label,
        "recorded_at": recorded_at,
        "factual": False,
        "displayable": False,
        "zero_verified": False,
    }
    if field == "engagement_rate":
        base.update(
            {
                "metric_identity": "legacy_engagement_rate",
                "not_equivalent_to": "real_er",
                "verified_real_er": False,
            }
        )
    if parsed is None:
        status = "unknown" if item.get(field) in (None, "") or _text(item.get(field)).casefold() in UNKNOWN_TOKENS else "invalid"
        return None, {**base, "status": status, "reason": "missing_or_invalid_numeric_value"}

    explicit, content = raw_evidence if raw_evidence is not None else _raw_metric_evidence(raw, field)
    raw_values = explicit + content
    value_matches_raw, verification_basis, raw_sample_n = _raw_metric_match(
        raw,
        field,
        parsed,
        explicit_values=explicit,
        content_values=content,
    )
    raw_observed = bool(value_matches_raw and source_state.get("successful"))
    if field == "followers" and source_state.get("hidden_followers"):
        raw_observed = False
    if parsed == 0:
        zero_observed = bool(raw_observed and raw_values and all(float(value) == 0 for value in raw_values))
        if not zero_observed:
            return None, {
                **base,
                "status": "zero_sentinel_suppressed",
                "reason": "stored_zero_without_matching_successful_raw_observation",
            }
        return parsed, {
            **base,
            "status": "observed_zero",
            "factual": True,
            "displayable": True,
            "zero_verified": True,
            "verification_basis": verification_basis,
            "raw_sample_n": raw_sample_n,
            "observed_at": source_state.get("observed_at") or recorded_at,
            "reason": "successful_raw_source_explicitly_observed_zero",
        }

    if raw_observed:
        return parsed, {
            **base,
            "status": "observed",
            "factual": True,
            "displayable": True,
            "verification_basis": verification_basis,
            "raw_sample_n": raw_sample_n,
            "observed_at": source_state.get("observed_at") or recorded_at,
            "reason": "successful_raw_source_contains_field_evidence",
        }
    declared_source = bool(
        _known_text(source_ref)
        or (_known_text(source_type) and source_type.casefold() not in {"manual", "unknown", "default"})
    )
    if declared_source:
        return parsed, {
            **base,
            "status": "declared",
            "displayable": True,
            "reason": "stored_nonzero_with_pool_source_but_without_field_level_raw_receipt",
        }
    return None, {
        **base,
        "status": "unverified_suppressed",
        "reason": "manual_or_unknown_metric_without_source_ref_or_matching_raw_receipt",
    }


def _real_er_projection(item: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    parsed = _number(item.get("real_er"), percent=True)
    sample_n = _number(item.get("real_er_sample_n"))
    computed_at = _text(item.get("real_er_computed_at"))
    method = _text(item.get("real_er_method"))
    base = {
        "metric_identity": "real_er",
        "denominator": "views",
        "source": method or None,
        "factual": False,
        "displayable": False,
        "zero_verified": False,
        "sample_n": int(sample_n) if sample_n is not None else None,
        "computed_at": computed_at or None,
        "observed_at": _public_timestamp(computed_at),
        "method": method or None,
    }
    if parsed is None:
        return None, {**base, "status": "unknown", "reason": "real_er_missing_or_invalid"}
    if (
        not isinstance(sample_n, int)
        or sample_n <= 0
        or not _known_text(computed_at)
        or not _known_text(method)
    ):
        return None, {
            **base,
            "status": "receipt_incomplete_suppressed",
            "reason": "real_er_requires_positive_sample_computed_at_and_method",
        }
    return parsed, {
        **base,
        "status": "observed_zero" if parsed == 0 else "observed",
        "factual": True,
        "displayable": True,
        "zero_verified": parsed == 0,
        "confidence": "low_sample" if sample_n < 5 else "sample_backed",
        "reason": "sample_backed_view_denominator_real_er_receipt",
    }


def _audience_projection(value: Any) -> tuple[Any, dict[str, Any]]:
    parsed = _json(value, None)
    if not isinstance(parsed, dict):
        return None, {
            "status": "unknown",
            "source": None,
            "factual": False,
            "displayable": False,
            "reason": "audience_payload_missing_or_invalid",
        }
    method = _text(parsed.get("method")).casefold()
    sample_size = _number(parsed.get("sample_size"))
    confidence = _number(parsed.get("confidence"))
    valid = method == "ensemble_v1" and isinstance(sample_size, int) and sample_size > 0
    if not valid:
        return None, {
            "status": "estimate_suppressed",
            "source": method or None,
            "factual": False,
            "displayable": False,
            "reason": "audience_estimate_requires_ensemble_v1_and_positive_sample",
        }
    projected = json.dumps(parsed, ensure_ascii=False) if isinstance(value, str) else parsed
    return projected, {
        "status": "estimated",
        "source": "audience_estimated_json:ensemble_v1",
        "factual": False,
        "displayable": True,
        "sample_size": int(sample_size),
        "confidence": confidence,
        "reason": "sample_backed_estimate_not_platform_official_audience_fact",
    }

def _collaboration_items(value: Any) -> tuple[list[Any], bool]:
    parsed = _json(value, [])
    if isinstance(parsed, dict):
        for key in ("items", "collaborations", "brands", "list"):
            if isinstance(parsed.get(key), list):
                return list(parsed[key]), isinstance(value, str)
        return [], isinstance(value, str)
    return (list(parsed), isinstance(value, str)) if isinstance(parsed, list) else ([], isinstance(value, str))


def _confirmed_collaboration(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    brand = _text(value.get("brand") or value.get("brand_name") or value.get("name"))
    if not _known_text(brand):
        return False
    status = _text(value.get("status")).casefold()
    if status in _PLAN_STATUSES:
        return False
    evidence = any(_known_text(value.get(key)) for key in _COLLAB_EVIDENCE_KEYS)
    return bool(evidence or status in _CONFIRMED_STATUSES)


def _collaboration_projection(value: Any, item: Mapping[str, Any]) -> tuple[Any, list[Any], dict[str, Any]]:
    items, was_string = _collaboration_items(value)
    confirmed = [item for item in items if _confirmed_collaboration(item)]
    source_type = _text(item.get("source_type"))
    source_ref = _text(item.get("source_ref"))
    declared_source = bool(
        _known_text(source_ref)
        or (_known_text(source_type) and source_type.casefold() not in {"manual", "unknown", "default"})
    )
    declared = (
        [
            candidate
            for candidate in items
            if candidate not in confirmed
            and (
                (
                    isinstance(candidate, Mapping)
                    and _known_text(
                        candidate.get("brand")
                        or candidate.get("brand_name")
                        or candidate.get("name")
                    )
                )
                or (not isinstance(candidate, Mapping) and _known_text(candidate))
            )
            and not (
                isinstance(candidate, Mapping)
                and _text(candidate.get("status")).casefold() in _PLAN_STATUSES
            )
        ]
        if declared_source
        else []
    )
    visible = [*confirmed, *declared]
    projected: Any = json.dumps(visible, ensure_ascii=False) if was_string else visible
    if confirmed and declared:
        status = "mixed"
    elif confirmed:
        status = "observed"
    elif declared:
        status = "declared"
    else:
        status = "unknown"
    return projected, confirmed, {
        "status": status,
        "source": _public_source_ref(source_ref) or _public_source_type(source_type) or "brand_collaborations_json",
        "factual": bool(confirmed) and not declared,
        "displayable": bool(visible),
        "observed_count": len(confirmed),
        "declared_count": len(declared),
        "suppressed_unverified_or_planned_count": max(0, len(items) - len(visible)),
        "reason": "confirmed_records_are_factual; sourced_legacy_claims_remain_declared_only",
    }


def project_pool_item_truth(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with honest pool metrics and a per-field truth receipt."""

    projected = dict(item)
    relevant_keys = {
        *POOL_NUMERIC_FIELDS,
        "real_er",
        "real_er_sample_n",
        "real_er_computed_at",
        "real_er_method",
        "audience_estimated_json",
        "brand_collaborations_json",
        "raw_platform_data",
        "metric_truth_raw_platform_data",
    }
    if not any(key in projected for key in relevant_keys):
        return projected
    raw = _json(
        projected.get("metric_truth_raw_platform_data", projected.get("raw_platform_data")),
        None,
    )
    active_metric_fields = [field for field in POOL_NUMERIC_FIELDS
                            if _number(projected.get(field), percent=field == "engagement_rate") is not None]
    raw_records, raw_evidence_by_field = build_raw_metric_evidence_index(
        raw,
        active_metric_fields,
        field_aliases=_FIELD_ALIASES,
        content_aliases=_CONTENT_ALIASES,
        walk=_walk,
        normalize_key=_key,
        parse_number=_number,
        record_failed=_record_has_failure_marker,
        content_record=_content_record,
    )
    source_state = _raw_source_state(raw, records=raw_records)
    fields: dict[str, Any] = {}
    suppressed: list[str] = []
    for field in POOL_NUMERIC_FIELDS:
        raw_evidence = raw_evidence_by_field.get(field, ([], []))
        value, receipt = _pool_metric_projection(
            projected,
            field,
            raw,
            source_state=source_state,
            raw_evidence=raw_evidence,
        )
        projected[field] = value
        fields[field] = receipt
        if not receipt["displayable"]:
            suppressed.append(field)

    real_er, real_er_receipt = _real_er_projection(projected)
    projected["real_er"] = real_er
    fields["real_er"] = real_er_receipt
    if not real_er_receipt["displayable"]:
        suppressed.append("real_er")

    audience, audience_receipt = _audience_projection(projected.get("audience_estimated_json"))
    projected["audience_estimated_json"] = audience
    fields["audience_estimated"] = audience_receipt
    if not audience_receipt["displayable"]:
        suppressed.append("audience_estimated")

    collaborations, factual_collaborations, collaboration_receipt = _collaboration_projection(
        projected.get("brand_collaborations_json"), projected
    )
    projected["brand_collaborations_json"] = collaborations
    projected["brand_collaborations_factual_json"] = factual_collaborations
    fields["brand_collaborations"] = collaboration_receipt
    if not collaboration_receipt["displayable"]:
        suppressed.append("brand_collaborations")

    projected.pop("metric_truth_raw_platform_data", None)
    existing = projected.get("data_truth") if isinstance(projected.get("data_truth"), dict) else {}
    projected["data_truth"] = {
        **existing,
        "version": VERSION,
        "claim_status": CLAIM_STATUS,
        "source_type": _public_source_type(projected.get("source_type")),
        "source_ref": _public_source_ref(projected.get("source_ref")),
        "metric_observed_at": max(
            (
                str(receipt.get("observed_at"))
                for receipt in fields.values()
                if isinstance(receipt, Mapping)
                and receipt.get("factual")
                and receipt.get("observed_at")
            ),
            default=None,
        ),
        "metric_recorded_at": _public_timestamp(
            projected.get("last_seen_at") or projected.get("updated_at")
        ),
        "fields": {**(existing.get("fields") or {}), **fields},
        "suppressed_fields": sorted(set([*(existing.get("suppressed_fields") or []), *suppressed])),
        "rule": "zero_requires_field_level_observation; missing/default/estimated/planned values never become factual",
    }
    return projected


def _evidence_source_receipt(item: Mapping[str, Any]) -> tuple[bool, str | None]:
    raw_source = item.get("metrics_source") or item.get("scrape_source")
    metrics_source = _text(raw_source) if _known_text(raw_source) else ""
    scraped_at = _text(item.get("metrics_scraped_at"))
    scrape_status = _text(item.get("scrape_status")).casefold()
    strong = bool(
        metrics_source
        and _known_text(scraped_at)
        and (not scrape_status or scrape_status in SUCCESS_STATUSES)
    )
    source = _public_source_type(metrics_source or item.get("source"))
    return strong, source


def project_evidence_item_truth(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return one video-evidence DTO with zero values gated by scrape receipts."""

    projected = dict(item)
    strong_receipt, source = _evidence_source_receipt(projected)
    has_persisted_source = bool(source or (_known_text(projected.get("content_url")) and projected.get("id")))
    fields: dict[str, Any] = {}
    suppressed: list[str] = []
    for field in EVIDENCE_NUMERIC_FIELDS:
        parsed = _number(projected.get(field))
        base = {
            "source": source,
            "observed_at": _public_timestamp(projected.get("metrics_scraped_at")) if strong_receipt else None,
            "factual": False,
            "displayable": False,
            "zero_verified": False,
        }
        if parsed is None:
            projected[field] = None
            fields[field] = {**base, "status": "unknown", "reason": "metric_missing_or_invalid"}
            suppressed.append(field)
        elif parsed == 0 and not strong_receipt:
            projected[field] = None
            fields[field] = {
                **base,
                "status": "zero_sentinel_suppressed",
                "reason": "evidence_zero_requires_metrics_scraped_at_or_successful_metrics_source",
            }
            suppressed.append(field)
        elif parsed == 0:
            projected[field] = parsed
            fields[field] = {
                **base,
                "status": "observed_zero",
                "factual": True,
                "displayable": True,
                "zero_verified": True,
                "reason": "scrape_receipt_explicitly_persisted_zero",
            }
        elif strong_receipt:
            projected[field] = parsed
            fields[field] = {
                **base,
                "status": "observed",
                "factual": True,
                "displayable": True,
                "reason": "scrape_receipt_present",
            }
        elif has_persisted_source:
            projected[field] = parsed
            fields[field] = {
                **base,
                "status": "declared",
                "displayable": True,
                "reason": "persisted_nonzero_evidence_without_metric_timestamp",
            }
        else:
            projected[field] = None
            fields[field] = {**base, "status": "unverified_suppressed", "reason": "no_evidence_source"}
            suppressed.append(field)

    existing = projected.get("data_truth") if isinstance(projected.get("data_truth"), dict) else {}
    projected["data_truth"] = {
        **existing,
        "version": VERSION,
        "claim_status": CLAIM_STATUS,
        "fields": {**(existing.get("fields") or {}), **fields},
        "suppressed_fields": sorted(set([*(existing.get("suppressed_fields") or []), *suppressed])),
        "rule": "evidence zero requires a metric scrape receipt",
    }
    return projected
