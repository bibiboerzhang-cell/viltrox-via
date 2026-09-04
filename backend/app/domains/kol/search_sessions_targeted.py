"""Bounded public projections for targeted-search session history."""
from __future__ import annotations

import re
from typing import Any

from app.domains.kol.targeted_search_contract import rebuild_locked_term_groups_for_cell


_CODE_RE = re.compile(r"^[a-zA-Z0-9_.:/-]{1,120}$")
_CONTACT_RE = re.compile(
    r"(?:https?://|www\.|[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,})",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d().\s-]{5,}\d)(?!\w)")
_OBJECTIVES = {"prospective_growth", "existing_evidence"}


def _text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text or _CONTACT_RE.search(text):
        return ""
    phone = _PHONE_RE.search(text)
    if phone and len(re.sub(r"\D", "", phone.group(0))) >= 7:
        return ""
    return text[:limit]


def _code(value: Any) -> str:
    text = str(value or "").strip()[:120]
    return text if _CODE_RE.fullmatch(text) else ""


def _number(value: Any, *, minimum: int = 0, maximum: int = 5_000_000_000) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _strings(value: Any, *, count: int, limit: int = 160) -> list[str]:
    raw_values = value if isinstance(value, (list, tuple, set)) else [value] if value not in (None, "") else []
    output: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        text = _text(raw, limit=limit)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
        if len(output) >= count:
            break
    return output


def project_follower_filter(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    raw = value
    output = {
        "followers_min": _number(raw.get("followers_min")),
        "followers_max": _number(raw.get("followers_max")),
        "source": _code(raw.get("source")),
        "locked": raw.get("locked") is True,
        "valid": raw.get("valid") is not False,
        "error": _code(raw.get("error")),
        "matched_text": _text(raw.get("matched_text"), limit=160),
    }
    return {key: item for key, item in output.items() if item not in (None, "")}


def _project_query_cell(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    cell_id = _code(raw.get("query_cell_id"))
    primary = _text(raw.get("primary_query"), limit=500)
    if not cell_id or not primary:
        return {}
    raw_limit = _number(raw.get("raw_limit"), minimum=10, maximum=15)
    weight = raw.get("brand_or_model_ranking_weight")
    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not 0 <= float(weight) <= 1:
        weight = None
    locked_term_groups = rebuild_locked_term_groups_for_cell(raw)
    return {
        "query_cell_id": cell_id,
        "objective": _code(raw.get("objective")),
        "segment": _code(raw.get("segment")),
        "segment_label": _text(raw.get("segment_label"), limit=240),
        "segment_source": _code(raw.get("segment_source")),
        "segment_locked": raw.get("segment_locked") is True,
        "required_scene_terms": [
            _text(item, limit=120)
            for item in _strings(raw.get("required_scene_terms"), count=6, limit=120)
            if _text(item, limit=120)
        ],
        "scene_match_mode": "all" if raw.get("scene_match_mode") == "all" else "any",
        "required_role_terms": [
            _text(item, limit=120)
            for item in _strings(raw.get("required_role_terms"), count=4, limit=120)
            if _text(item, limit=120)
        ],
        "role_match_mode": "all" if raw.get("role_match_mode") == "all" else "any",
        "primary_query": primary,
        "fallback_queries": _strings(raw.get("fallback_queries"), count=3, limit=500),
        "platforms": [_code(item) for item in _strings(raw.get("platforms"), count=3, limit=40) if _code(item)],
        "round": 1,
        "raw_limit": raw_limit or 12,
        "independent_raw_quota": raw.get("independent_raw_quota") is True,
        "required_evidence_groups": [
            _code(item)
            for item in _strings(raw.get("required_evidence_groups"), count=8, limit=80)
            if _code(item)
        ],
        "product_evidence_required": raw.get("product_evidence_required") is not False,
        "product_evidence_basis": _code(raw.get("product_evidence_basis")),
        "brand_or_model_required": raw.get("brand_or_model_required") is True,
        "brand_or_model_ranking_weight": weight,
        "follower_filter": project_follower_filter(raw.get("follower_filter")),
        **({"locked_term_groups": locked_term_groups} if locked_term_groups else {}),
    }


def project_candidate_query_context(value: Any) -> dict[str, Any]:
    """Project the server-owned QueryCell lineage attached to one candidate."""

    raw = value if isinstance(value, dict) else {}
    output = {
        "query_cell_id": _code(raw.get("query_cell_id")),
        "query_cell_segment": _code(raw.get("query_cell_segment")),
        "query_cell_query": _text(raw.get("query_cell_query"), limit=500),
    }
    matches = [
        cell
        for raw_cell in (raw.get("matched_query_cells") if isinstance(raw.get("matched_query_cells"), list) else [])[:8]
        if (cell := _project_query_cell(raw_cell))
    ]
    if matches:
        output["matched_query_cells"] = matches
    return {key: item for key, item in output.items() if item not in (None, "", [], {})}


def project_targeted_search_summary(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    output: dict[str, Any] = {}
    for name in ("search_spec_version", "objective", "first_round_strategy", "claim_status"):
        code = _code(raw.get(name))
        if code:
            output[name] = code
    for name in ("query_cells_requested", "query_cells_executed", "query_cells_omitted"):
        number = _number(raw.get(name), maximum=100)
        if number is not None:
            output[name] = number
    if isinstance(raw.get("fallback_queries_used"), bool):
        output["fallback_queries_used"] = raw["fallback_queries_used"]
    return output


def _score(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if 0 <= number <= 100 else None


def _project_selection_rationale(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    if (
        raw.get("schema") != "prospective_candidate_rationale_v1"
        or raw.get("claim_status") != "descriptive_only"
    ):
        return {}
    cards: list[dict[str, Any]] = []
    for source in raw.get("reason_cards") if isinstance(raw.get("reason_cards"), list) else []:
        if not isinstance(source, dict):
            continue
        code = _code(source.get("code"))
        status = _code(source.get("status"))
        if not code or status not in {"observed", "pending"}:
            continue
        cards.append({
            "code": code,
            "label": _text(source.get("label"), limit=120),
            "status": status,
            "summary": _text(source.get("summary"), limit=360),
            "score": _score(source.get("score")),
            "evidence_terms": _strings(source.get("evidence_terms"), count=8, limit=120),
            "evidence_fields": [_code(item) for item in _strings(source.get("evidence_fields"), count=8, limit=80) if _code(item)],
        })
        if len(cards) >= 4:
            break
    missing: list[dict[str, Any]] = []
    for source in raw.get("missing_evidence") if isinstance(raw.get("missing_evidence"), list) else []:
        if not isinstance(source, dict):
            continue
        code = _code(source.get("code"))
        action = _code(source.get("next_action"))
        if code and action:
            missing.append({
                "code": code,
                "label": _text(source.get("label"), limit=360),
                "next_action": action,
                "blocks_strict_qualification": source.get("blocks_strict_qualification") is True,
            })
        if len(missing) >= 4:
            break
    next_action = raw.get("next_action") if isinstance(raw.get("next_action"), dict) else {}
    raw_activation = (
        raw.get("activation_evidence")
        if isinstance(raw.get("activation_evidence"), dict)
        else {}
    )
    metric_counts = (
        raw_activation.get("metric_sample_counts")
        if isinstance(raw_activation.get("metric_sample_counts"), dict)
        else {}
    )
    metric_sufficient = (
        raw_activation.get("metric_sample_sufficient")
        if isinstance(raw_activation.get("metric_sample_sufficient"), dict)
        else {}
    )
    floor_results = (
        raw_activation.get("floor_results")
        if isinstance(raw_activation.get("floor_results"), dict)
        else {}
    )
    metric_names = ("avg_views", "engagement", "views_per_follower", "comments_per_follower")
    return {
        "schema": "prospective_candidate_rationale_v1",
        "objective": "prospective_growth",
        "purpose": _text(raw.get("purpose"), limit=360),
        "decision_readiness": _code(raw.get("decision_readiness")),
        "strict_gate_status": _code(raw.get("strict_gate_status")),
        "why_find_this_creator": _strings(raw.get("why_find_this_creator"), count=4, limit=360),
        "reason_cards": cards,
        "activation_evidence": {
            "status": _code(raw_activation.get("status")),
            "sample_count": _number(raw_activation.get("sample_count"), maximum=10_000),
            "minimum_sample_count": _number(
                raw_activation.get("minimum_sample_count"), maximum=100
            ),
            "metric_sample_counts": {
                name: _number(metric_counts.get(name), maximum=10_000)
                for name in metric_names
            },
            "metric_sample_sufficient": {
                name: metric_sufficient.get(name) is True
                for name in metric_names
            },
            "floor_results": {
                name: floor_results.get(name) is True
                for name in metric_names
            },
            "claim_status": "descriptive_only",
        },
        "missing_evidence": missing,
        "next_action": {
            "code": _code(next_action.get("code")),
            "label": _text(next_action.get("label"), limit=360),
        },
        "evidence_confidence": _score(raw.get("evidence_confidence")),
        "claim_status": "descriptive_only",
        "conversion_claim": False,
        "outreach_decision": False,
    }


def project_growth_candidate_context(value: Any) -> dict[str, Any]:
    """Project descriptive growth scores and their bounded evidence boundary."""

    raw = value if isinstance(value, dict) else {}
    if raw.get("claim_status") != "descriptive_only":
        return {}
    output: dict[str, Any] = {
        "claim_status": "descriptive_only",
        "product_scene_evidence_pass": raw.get("product_scene_evidence_pass") is True,
        "market_activation_pass": raw.get("market_activation_pass") is True,
        "market_activation_status": _code(raw.get("market_activation_status")),
        "growth_qualification_pass": raw.get("growth_qualification_pass") is True,
    }
    for name in (
        "product_use_fit",
        "market_activation",
        "audience_fit",
        "content_execution",
        "evidence_confidence",
        "growth_candidate_score",
    ):
        score = _score(raw.get(name))
        if score is not None:
            output[name] = score

    contract = raw.get("growth_candidate_scoring") if isinstance(raw.get("growth_candidate_scoring"), dict) else {}
    if contract.get("claim_status") != "descriptive_only":
        return output
    evidence = contract.get("evidence_contract") if isinstance(contract.get("evidence_contract"), dict) else {}
    confidence = contract.get("confidence") if isinstance(contract.get("confidence"), dict) else {}
    outcome = contract.get("real_outcome") if isinstance(contract.get("real_outcome"), dict) else {}
    activation = contract.get("platform_activation") if isinstance(contract.get("platform_activation"), dict) else {}
    activation_gate = activation.get("strict_gate") if isinstance(activation.get("strict_gate"), dict) else {}
    rationale = _project_selection_rationale(
        raw.get("selection_rationale") or contract.get("selection_rationale")
    )
    safe_contract = {
        "version": _code(contract.get("version")),
        "objective": _code(contract.get("objective")),
        "missing_dimensions": [_code(item) for item in _strings(contract.get("missing_dimensions"), count=8, limit=80) if _code(item)],
        "missing_value_policy": _code(contract.get("missing_value_policy")),
        "evidence_contract": {
            "passed": evidence.get("passed") is True,
            "required_product_terms": _strings(evidence.get("required_product_terms"), count=12, limit=120),
            "required_scene_terms": _strings(evidence.get("required_scene_terms"), count=12, limit=120),
            "required_role_terms": _strings(evidence.get("required_role_terms"), count=8, limit=120),
            "matched_product_terms": _strings(evidence.get("matched_product_terms"), count=12, limit=120),
            "matched_scene_terms": _strings(evidence.get("matched_scene_terms"), count=12, limit=120),
            "matched_role_terms": _strings(evidence.get("matched_role_terms"), count=8, limit=120),
            "matched_fields": [_code(item) for item in _strings(evidence.get("matched_fields"), count=12, limit=80) if _code(item)],
            "missing_groups": [_code(item) for item in _strings(evidence.get("missing_groups"), count=8, limit=80) if _code(item)],
            "missing_role_terms": _strings(evidence.get("missing_role_terms"), count=8, limit=120),
            "brand_history_used": evidence.get("brand_history_used") is True,
            "brand_history_weight": 0.0,
        },
        "confidence": {
            "score": _score(confidence.get("score")),
            "level": _code(confidence.get("level")),
            "decision_mode": _code(confidence.get("decision_mode")),
            "dimension_coverage": confidence.get("dimension_coverage") if isinstance(confidence.get("dimension_coverage"), (int, float)) else None,
            "sample_depth": confidence.get("sample_depth") if isinstance(confidence.get("sample_depth"), (int, float)) else None,
        },
        "followers_policy": _code(contract.get("followers_policy")),
        "brand_history_weight": 0.0,
        "real_outcome": {
            "available": outcome.get("available") is True,
            "fields": [_code(item) for item in _strings(outcome.get("fields"), count=20, limit=80) if _code(item)],
            "included_in_score": False,
            "weight": 0.0,
        },
        "market_activation_gate": {
            "passed": activation_gate.get("passed") is True,
            "status": _code(activation_gate.get("status")),
            "sample_count": _number(activation_gate.get("sample_count"), maximum=10_000),
            "minimum_sample_count": _number(activation_gate.get("minimum_sample_count"), maximum=100),
            "metric_sample_counts": {
                name: _number(
                    (activation_gate.get("metric_sample_counts") or {}).get(name),
                    maximum=10_000,
                )
                for name in (
                    "avg_views",
                    "engagement",
                    "views_per_follower",
                    "comments_per_follower",
                )
                if isinstance(activation_gate.get("metric_sample_counts"), dict)
            },
            "claim_status": "descriptive_only",
        },
        "selection_rationale": rationale,
        "claim_status": "descriptive_only",
    }
    output["growth_candidate_scoring"] = safe_contract
    if rationale:
        output["selection_rationale"] = rationale
    return output


def _project_segments(value: Any) -> list[dict[str, Any]]:
    raw_values = value if isinstance(value, list) else []
    output: list[dict[str, Any]] = []
    for raw in raw_values[:8]:
        if not isinstance(raw, dict):
            continue
        item = {
            "key": _code(raw.get("key")),
            "label": _text(raw.get("label"), limit=240),
            "query_term": _text(raw.get("query_term"), limit=240),
            "component_segments": [
                _code(value)
                for value in _strings(raw.get("component_segments"), count=6, limit=120)
                if _code(value)
            ],
            "segment_match_mode": "all" if raw.get("segment_match_mode") == "all" else "any",
            "source": _code(raw.get("source")),
            "locked": raw.get("locked") is True,
        }
        if item["key"]:
            output.append(item)
    return output


def project_targeted_plan(value: Any) -> dict[str, Any]:
    """Return the replay-safe V2 SearchBrief/QueryCell portion of a plan."""

    raw = value if isinstance(value, dict) else {}
    output: dict[str, Any] = {}
    objective = _code(raw.get("objective"))
    if objective in _OBJECTIVES:
        output["objective"] = objective
    for name in (
        "search_spec_version",
        "first_round_strategy",
        "authoritative_query_field",
        "ranking_claim_status",
    ):
        code = _code(raw.get(name))
        if code:
            output[name] = code
    search_queries = _strings(raw.get("search_queries"), count=8, limit=500)
    if search_queries:
        output["search_queries"] = search_queries
    cells = [
        cell
        for raw_cell in (raw.get("query_cells") if isinstance(raw.get("query_cells"), list) else [])[:8]
        if (cell := _project_query_cell(raw_cell))
    ]
    if cells:
        output["query_cells"] = cells
    segments = _project_segments(raw.get("explicit_segments"))
    if segments:
        output["explicit_segments"] = segments
    follower_filter = project_follower_filter(raw.get("follower_filter"))
    if follower_filter:
        output["follower_filter"] = follower_filter
    for name in ("product_anchor_required",):
        if isinstance(raw.get(name), bool):
            output[name] = raw[name]
    weight = raw.get("brand_or_model_ranking_weight")
    if isinstance(weight, (int, float)) and not isinstance(weight, bool) and 0 <= float(weight) <= 1:
        output["brand_or_model_ranking_weight"] = float(weight)

    brief = raw.get("search_brief") if isinstance(raw.get("search_brief"), dict) else {}
    if brief:
        product = brief.get("product") if isinstance(brief.get("product"), dict) else {}
        safe_product = {
            "resolved_sku": _text(product.get("resolved_sku"), limit=240),
            "capability": _text(product.get("capability"), limit=240),
            "evidence_required": (
                product.get("evidence_required")
                if isinstance(product.get("evidence_required"), bool)
                else None
            ),
            "evidence_basis": _code(product.get("evidence_basis")),
            "brand_or_model_required": product.get("brand_or_model_required") is True,
        }
        brief_cells = [
            cell
            for raw_cell in (brief.get("query_cells") if isinstance(brief.get("query_cells"), list) else [])[:8]
            if (cell := _project_query_cell(raw_cell))
        ]
        safe_brief = {
            "search_spec_version": _code(brief.get("search_spec_version")),
            "objective": _code(brief.get("objective")),
            "product": {key: item for key, item in safe_product.items() if item not in (None, "")},
            "explicit_segments": _project_segments(brief.get("explicit_segments")),
            "follower_filter": project_follower_filter(brief.get("follower_filter")),
            "platforms": [_code(item) for item in _strings(brief.get("platforms"), count=3, limit=40) if _code(item)],
            "claim_status": _code(brief.get("claim_status")),
            "first_round_strategy": _code(brief.get("first_round_strategy")),
            "fallback_policy": _code(brief.get("fallback_policy")),
            "authoritative_query_field": _code(brief.get("authoritative_query_field")),
            "query_cells": brief_cells,
        }
        output["search_brief"] = {
            key: item for key, item in safe_brief.items() if item not in (None, "", [], {})
        }
    return output


def project_targeted_session_input(value: Any) -> dict[str, Any]:
    """Project only operator-owned targeted inputs; never accept a client plan."""

    raw = value if isinstance(value, dict) else {}
    output: dict[str, Any] = {}
    objective = _code(raw.get("objective") or raw.get("search_objective"))
    if objective in _OBJECTIVES:
        output["objective"] = objective
    for name in ("segments", "industries", "use_cases"):
        source = raw.get(name)
        if name == "use_cases" and source in (None, ""):
            source = raw.get("useCases")
        values = _strings(source, count=8, limit=240)
        if values:
            output[name] = values
    industry = _text(raw.get("industry"), limit=240)
    if industry:
        output["industry"] = industry
    raw_limit = _number(raw.get("first_round_raw_limit"), minimum=10, maximum=15)
    if raw_limit is not None:
        output["first_round_raw_limit"] = raw_limit

    filters = raw.get("filters") if isinstance(raw.get("filters"), dict) else {}
    safe_filters: dict[str, Any] = {}
    for name in ("platforms", "countries", "languages", "verticals"):
        values = _strings(filters.get(name), count=12, limit=120)
        if values:
            safe_filters[name] = values
    for name in ("followers_min", "followers_max", "follower_min", "follower_max"):
        number = _number(filters.get(name) if filters.get(name) not in (None, "") else raw.get(name))
        if number is not None:
            safe_filters[name] = number
    gear = _code(filters.get("gear_content") or raw.get("gear_content"))
    if gear in {"any", "yes", "no"}:
        safe_filters["gear_content"] = gear
    if safe_filters:
        output["filters"] = safe_filters
    return output


__all__ = [
    "project_candidate_query_context",
    "project_follower_filter",
    "project_growth_candidate_context",
    "project_targeted_plan",
    "project_targeted_search_summary",
    "project_targeted_session_input",
]
