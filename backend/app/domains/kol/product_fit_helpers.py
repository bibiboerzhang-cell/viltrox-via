"""Compatibility facade for product-fit helpers after the S3 seam split.

Production composition now imports public shared policy, repository, rendering,
and reason-port modules directly. These aliases remain for existing callers
without wildcard or cross-domain private imports.
"""
from __future__ import annotations

import json
from typing import Any

from app.platform import llm_production
from app.shared.product_fit_policy import (
    PRODUCT_FIT_REASON_BUDGET_SCOPE,
    PRODUCT_FIT_SCENARIO,
    adjacent_fit,
    append_history_fit_evidence,
    append_penalty_fit_evidence,
    as_row_dict,
    cooperation_depth,
    country_key,
    deterministic_reason,
    dimensions11_fit_for_family,
    entity_payload,
    evidence,
    evidence_count,
    fact_payload,
    family_product_ids,
    family_tokens,
    first_fact,
    freshness_score,
    historical_fit,
    json_default,
    latest_fact_value,
    load_json,
    lower,
    market_detail,
    market_signal_score,
    median_score,
    member_counts,
    normalize_product_fit_key,
    percentile,
    proved_family_ids,
    rank_product_fit_candidates,
    reason_failure_code,
    region_relevance,
    render_family_detail,
    risk_count,
    safe_float,
    safe_int,
    source_payload,
    text,
    valid_reason_payload,
)
from app.shared.product_fit_rendering import format_preview_summary, render_markdown

from app.domains.kol.product_fit_reason_adapter import (
    attach_reason,
    reason_model_binding,
)
from app.domains.kol.product_fit_repository import (
    SqlProductFitRepository,
    resolve_kol,
)


SCENARIO = PRODUCT_FIT_SCENARIO
REASON_BUDGET_SCOPE = PRODUCT_FIT_REASON_BUDGET_SCOPE
REASON_MODEL_TASK = "kol_product_fit_reason"


_member_counts = member_counts
_family_product_ids = family_product_ids
_proved_family_ids = proved_family_ids
_historical_fit = historical_fit
_adjacent_fit = adjacent_fit
_region_relevance = region_relevance
_cooperation_depth = cooperation_depth
_render_family_detail = render_family_detail
_normalize_product_fit_key = normalize_product_fit_key
_dimensions11_product_fit_for_family = dimensions11_fit_for_family
_append_history_fit_evidence = append_history_fit_evidence
_append_penalty_fit_evidence = append_penalty_fit_evidence
_country_key = country_key
_entity_payload = entity_payload
_evidence = evidence
_evidence_count = evidence_count
_fact_payload = fact_payload
_family_tokens = family_tokens
_first_fact = first_fact
_freshness_score = freshness_score
_json_default = json_default
_latest_fact_value = latest_fact_value
_load_json = load_json
_lower = lower
_market_detail = market_detail
_market_signal_score = market_signal_score
_median_score = median_score
_percentile = percentile
_risk_count = risk_count
_row_to_dict = as_row_dict
_safe_float = safe_float
_safe_int = safe_int
_source_payload = source_payload
_text = text
_deterministic_reason = deterministic_reason
_reason_failure_code = reason_failure_code
_valid_reason_payload = valid_reason_payload


def _rank_product_fit_candidates(
    eligible: list[dict[str, Any]],
    *,
    safe_limit: int,
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    return rank_product_fit_candidates(eligible, safe_limit_value=safe_limit)


def _repository() -> SqlProductFitRepository:
    return SqlProductFitRepository()


def _resolve_kol(
    *,
    kol_entity_uid: str = "",
    kol_pool_id: int = 0,
    platform: str = "",
    handle: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    return resolve_kol(
        _repository(),
        kol_entity_uid=kol_entity_uid,
        kol_pool_id=kol_pool_id,
        platform=platform,
        handle=handle,
    )


def _candidate_product_families() -> list[dict[str, Any]]:
    return _repository().candidate_families()


def _official_family_links() -> dict[int, list[dict[str, Any]]]:
    return _repository().official_family_links()


def _load_dimensions11_product_fit(kol_pool_id: int) -> dict[str, dict[str, Any]]:
    return _repository().dimensions11_fit(kol_pool_id)


def _reason_model_binding() -> tuple[str, str]:
    return reason_model_binding()


def _generate_reason_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return llm_production.generate_json(*args, **kwargs)


def _attach_reason(payload: dict[str, Any], item: dict[str, Any]) -> None:
    attach_reason(
        payload,
        item,
        binding_resolver=_reason_model_binding,
        generate_json=_generate_reason_json,
    )


def _parse_reason_text(value: str) -> dict[str, str] | None:
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        "short_reason": text(parsed.get("short_reason")),
        "pitch_angle": text(parsed.get("pitch_angle")),
        "caution_note": text(parsed.get("caution_note")),
    }


__all__ = [
    "SCENARIO",
    "REASON_BUDGET_SCOPE",
    "REASON_MODEL_TASK",
    "format_preview_summary",
    "render_markdown",
    "_adjacent_fit",
    "_append_history_fit_evidence",
    "_append_penalty_fit_evidence",
    "_attach_reason",
    "_candidate_product_families",
    "_cooperation_depth",
    "_country_key",
    "_deterministic_reason",
    "_dimensions11_product_fit_for_family",
    "_entity_payload",
    "_evidence",
    "_evidence_count",
    "_fact_payload",
    "_family_product_ids",
    "_family_tokens",
    "_first_fact",
    "_freshness_score",
    "_historical_fit",
    "_json_default",
    "_latest_fact_value",
    "_load_dimensions11_product_fit",
    "_load_json",
    "_lower",
    "_market_detail",
    "_market_signal_score",
    "_median_score",
    "_member_counts",
    "_normalize_product_fit_key",
    "_official_family_links",
    "_parse_reason_text",
    "_percentile",
    "_proved_family_ids",
    "_rank_product_fit_candidates",
    "_reason_failure_code",
    "_reason_model_binding",
    "_region_relevance",
    "_render_family_detail",
    "_resolve_kol",
    "_risk_count",
    "_row_to_dict",
    "_safe_float",
    "_safe_int",
    "_source_payload",
    "_text",
    "_valid_reason_payload",
]
