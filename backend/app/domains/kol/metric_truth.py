"""Conservative read-side truth projection for KOL metrics.

This module never infers a missing metric from another metric and never turns a
missing value into zero.  A stored zero is returned only when the persisted raw
provider payload (pool metrics) or scrape receipt (video evidence metrics)
proves that zero was actually observed.  Manually declared non-zero values may
remain visible, but are labelled ``declared`` rather than factual/observed.

Audience estimates remain in their explicitly-estimated field only when a real
sample and method receipt exist.  Planned or unsupported brand collaborations
are removed from the factual collaboration list.

底层原语(常量/标量解析/raw 游走/字段对账/来源状态)住在 metric_truth_base.py
(CC 战役 2026-08-30 平移,行为逐字节不变);本模块保留投影编排与全部历史名字。
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from app.domains.kol.metric_truth_base import (  # noqa: F401  (历史名字保留给调用方/测试)
    FAILURE_STATUSES,
    SUCCESS_STATUSES,
    UNKNOWN_TOKENS,
    _COLLAB_EVIDENCE_KEYS,
    _CONFIRMED_STATUSES,
    _CONTENT_ALIASES,
    _FIELD_ALIASES,
    _HIDDEN_FOLLOWER_KEYS,
    _OBSERVED_AT_KEYS,
    _PLAN_STATUSES,
    _STATUS_KEYS,
    _content_metric_values,
    _content_record,
    _json,
    _key,
    _known_text,
    _matches,
    _number,
    _public_source_ref,
    _public_source_type,
    _public_timestamp,
    _raw_metric_evidence,
    _raw_metric_match,
    _raw_source_state,
    _record_has_failure_marker,
    _text,
    _truthy,
    _values_for_keys,
    _walk,
)
from app.domains.kol.metric_truth_index import build_raw_metric_evidence_index

VERSION = "kol_metric_truth_v1"
CLAIM_STATUS = "descriptive_only"

POOL_NUMERIC_FIELDS = (
    "followers",
    "avg_views",
    "avg_likes",
    "avg_comments",
    "engagement_rate",
)
EVIDENCE_NUMERIC_FIELDS = ("view_count", "like_count", "comment_count", "share_count")

_POOL_TRUTH_RELEVANT_KEYS = frozenset(
    {
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
)


def _pool_receipt_base(item: Mapping[str, Any], field: str, source_state: dict[str, Any]) -> dict[str, Any]:
    source_label = (
        source_state.get("source")
        or _public_source_ref(_text(item.get("source_ref")))
        or _public_source_type(_text(item.get("source_type")))
    )
    base = {
        "source": source_label,
        "recorded_at": _public_timestamp(item.get("last_seen_at") or item.get("updated_at")),
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
    return base


def _missing_numeric_status(stored: Any) -> str:
    if stored in (None, "") or _text(stored).casefold() in UNKNOWN_TOKENS:
        return "unknown"
    return "invalid"


def _pool_raw_observed(field: str, source_state: dict[str, Any], value_matches_raw: bool) -> bool:
    raw_observed = bool(value_matches_raw and source_state.get("successful"))
    if field == "followers" and source_state.get("hidden_followers"):
        raw_observed = False
    return raw_observed


def _zero_pool_projection(
    parsed: Any,
    base: dict[str, Any],
    *,
    raw_observed: bool,
    raw_values: list[int | float],
    verification_basis: str | None,
    raw_sample_n: int,
    observed_at: Any,
) -> tuple[Any, dict[str, Any]]:
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
        "observed_at": observed_at,
        "reason": "successful_raw_source_explicitly_observed_zero",
    }


def _pool_declared_source(item: Mapping[str, Any]) -> bool:
    source_type = _text(item.get("source_type"))
    return bool(
        _known_text(item.get("source_ref"))
        or (_known_text(source_type) and source_type.casefold() not in {"manual", "unknown", "default"})
    )


def _pool_metric_projection(
    item: Mapping[str, Any],
    field: str,
    raw: Any,
    *,
    source_state: dict[str, Any] | None = None,
    raw_evidence: tuple[list[int | float], list[int | float]] | None = None,
) -> tuple[Any, dict[str, Any]]:
    parsed = _number(item.get(field), percent=field == "engagement_rate")
    source_state = source_state if source_state is not None else _raw_source_state(raw)
    base = _pool_receipt_base(item, field, source_state)
    if parsed is None:
        status = _missing_numeric_status(item.get(field))
        return None, {**base, "status": status, "reason": "missing_or_invalid_numeric_value"}

    explicit, content = raw_evidence if raw_evidence is not None else _raw_metric_evidence(raw, field)
    value_matches_raw, verification_basis, raw_sample_n = _raw_metric_match(
        raw,
        field,
        parsed,
        explicit_values=explicit,
        content_values=content,
    )
    raw_observed = _pool_raw_observed(field, source_state, value_matches_raw)
    observed_at = source_state.get("observed_at") or base["recorded_at"]
    if parsed == 0:
        return _zero_pool_projection(
            parsed,
            base,
            raw_observed=raw_observed,
            raw_values=explicit + content,
            verification_basis=verification_basis,
            raw_sample_n=raw_sample_n,
            observed_at=observed_at,
        )
    if raw_observed:
        return parsed, {
            **base,
            "status": "observed",
            "factual": True,
            "displayable": True,
            "verification_basis": verification_basis,
            "raw_sample_n": raw_sample_n,
            "observed_at": observed_at,
            "reason": "successful_raw_source_contains_field_evidence",
        }
    if _pool_declared_source(item):
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


def _real_er_receipt_incomplete(sample_n: Any, computed_at: str, method: str) -> bool:
    return (
        not isinstance(sample_n, int)
        or sample_n <= 0
        or not _known_text(computed_at)
        or not _known_text(method)
    )


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
    if _real_er_receipt_incomplete(sample_n, computed_at, method):
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


def _declared_collaboration(candidate: Any, confirmed: list[Any]) -> bool:
    """来源背书下仍可展示的 declared 条目(与老 comprehension 条件逐字节等价)。"""
    if candidate in confirmed:
        return False
    if isinstance(candidate, Mapping):
        if _text(candidate.get("status")).casefold() in _PLAN_STATUSES:
            return False
        return bool(
            _known_text(candidate.get("brand") or candidate.get("brand_name") or candidate.get("name"))
        )
    return bool(_known_text(candidate))


def _collaboration_status(confirmed: list[Any], declared: list[Any]) -> str:
    if confirmed and declared:
        return "mixed"
    if confirmed:
        return "observed"
    if declared:
        return "declared"
    return "unknown"


def _collaboration_projection(value: Any, item: Mapping[str, Any]) -> tuple[Any, list[Any], dict[str, Any]]:
    items, was_string = _collaboration_items(value)
    confirmed = [item for item in items if _confirmed_collaboration(item)]
    declared = (
        [candidate for candidate in items if _declared_collaboration(candidate, confirmed)]
        if _pool_declared_source(item)
        else []
    )
    visible = [*confirmed, *declared]
    projected: Any = json.dumps(visible, ensure_ascii=False) if was_string else visible
    source_label = (
        _public_source_ref(_text(item.get("source_ref")))
        or _public_source_type(_text(item.get("source_type")))
        or "brand_collaborations_json"
    )
    return projected, confirmed, {
        "status": _collaboration_status(confirmed, declared),
        "source": source_label,
        "factual": bool(confirmed) and not declared,
        "displayable": bool(visible),
        "observed_count": len(confirmed),
        "declared_count": len(declared),
        "suppressed_unverified_or_planned_count": max(0, len(items) - len(visible)),
        "reason": "confirmed_records_are_factual; sourced_legacy_claims_remain_declared_only",
    }


def _apply_receipt(
    projected: dict[str, Any],
    fields: dict[str, Any],
    suppressed: list[str],
    *,
    column: str | None,
    receipt_key: str,
    value: Any,
    receipt: dict[str, Any],
) -> None:
    """落值 + 记收据;不可展示的字段名进 suppressed(与老 4 段 if 逐字节同序)。"""
    if column is not None:
        projected[column] = value
    fields[receipt_key] = receipt
    if not receipt["displayable"]:
        suppressed.append(receipt_key)


def _pool_raw_evidence_index(projected: dict[str, Any], raw: Any) -> tuple[Any, dict[str, Any]]:
    active_metric_fields = [
        field
        for field in POOL_NUMERIC_FIELDS
        if _number(projected.get(field), percent=field == "engagement_rate") is not None
    ]
    return build_raw_metric_evidence_index(
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


def _pool_data_truth(
    projected: dict[str, Any], fields: dict[str, Any], suppressed: list[str]
) -> dict[str, Any]:
    existing = projected.get("data_truth") if isinstance(projected.get("data_truth"), dict) else {}
    return {
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


def project_pool_item_truth(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with honest pool metrics and a per-field truth receipt."""

    projected = dict(item)
    if not any(key in projected for key in _POOL_TRUTH_RELEVANT_KEYS):
        return projected
    raw = _json(
        projected.get("metric_truth_raw_platform_data", projected.get("raw_platform_data")),
        None,
    )
    raw_records, raw_evidence_by_field = _pool_raw_evidence_index(projected, raw)
    source_state = _raw_source_state(raw, records=raw_records)
    fields: dict[str, Any] = {}
    suppressed: list[str] = []
    for field in POOL_NUMERIC_FIELDS:
        value, receipt = _pool_metric_projection(
            projected,
            field,
            raw,
            source_state=source_state,
            raw_evidence=raw_evidence_by_field.get(field, ([], [])),
        )
        _apply_receipt(
            projected, fields, suppressed, column=field, receipt_key=field, value=value, receipt=receipt
        )

    real_er, real_er_receipt = _real_er_projection(projected)
    _apply_receipt(
        projected, fields, suppressed,
        column="real_er", receipt_key="real_er", value=real_er, receipt=real_er_receipt,
    )

    audience, audience_receipt = _audience_projection(projected.get("audience_estimated_json"))
    _apply_receipt(
        projected, fields, suppressed,
        column="audience_estimated_json", receipt_key="audience_estimated",
        value=audience, receipt=audience_receipt,
    )

    collaborations, factual_collaborations, collaboration_receipt = _collaboration_projection(
        projected.get("brand_collaborations_json"), projected
    )
    projected["brand_collaborations_factual_json"] = factual_collaborations
    _apply_receipt(
        projected, fields, suppressed,
        column="brand_collaborations_json", receipt_key="brand_collaborations",
        value=collaborations, receipt=collaboration_receipt,
    )

    projected.pop("metric_truth_raw_platform_data", None)
    projected["data_truth"] = _pool_data_truth(projected, fields, suppressed)
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


def _evidence_field_projection(
    parsed: Any,
    base: dict[str, Any],
    *,
    strong_receipt: bool,
    has_persisted_source: bool,
) -> tuple[Any, dict[str, Any]]:
    """单个 evidence 数值列的三态判定(与老 if-elif 链逐字节同序/同文案)。"""
    if parsed is None:
        return None, {**base, "status": "unknown", "reason": "metric_missing_or_invalid"}
    if parsed == 0 and not strong_receipt:
        return None, {
            **base,
            "status": "zero_sentinel_suppressed",
            "reason": "evidence_zero_requires_metrics_scraped_at_or_successful_metrics_source",
        }
    if parsed == 0:
        return parsed, {
            **base,
            "status": "observed_zero",
            "factual": True,
            "displayable": True,
            "zero_verified": True,
            "reason": "scrape_receipt_explicitly_persisted_zero",
        }
    if strong_receipt:
        return parsed, {
            **base,
            "status": "observed",
            "factual": True,
            "displayable": True,
            "reason": "scrape_receipt_present",
        }
    if has_persisted_source:
        return parsed, {
            **base,
            "status": "declared",
            "displayable": True,
            "reason": "persisted_nonzero_evidence_without_metric_timestamp",
        }
    return None, {**base, "status": "unverified_suppressed", "reason": "no_evidence_source"}


def project_evidence_item_truth(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return one video-evidence DTO with zero values gated by scrape receipts."""

    projected = dict(item)
    strong_receipt, source = _evidence_source_receipt(projected)
    has_persisted_source = bool(source or (_known_text(projected.get("content_url")) and projected.get("id")))
    fields: dict[str, Any] = {}
    suppressed: list[str] = []
    base_template = {
        "source": source,
        "observed_at": _public_timestamp(projected.get("metrics_scraped_at")) if strong_receipt else None,
        "factual": False,
        "displayable": False,
        "zero_verified": False,
    }
    for field in EVIDENCE_NUMERIC_FIELDS:
        value, receipt = _evidence_field_projection(
            _number(projected.get(field)),
            dict(base_template),
            strong_receipt=strong_receipt,
            has_persisted_source=has_persisted_source,
        )
        _apply_receipt(
            projected, fields, suppressed, column=field, receipt_key=field, value=value, receipt=receipt
        )

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
