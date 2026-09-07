"""Narrow, read-only KPI truth contract; stored points are never rewritten here."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from app.shared.communication_truth import communication_evidence

COMMUNICATION_METRICS = frozenset({
    "recommendation_outreach_sent", "recommendation_reply_received", "stage_contacted", "stage_replied",
})
DERIVED_SCORE_METRICS = frozenset({"workload_score", "kpi_credit"})
KPI_LABEL_SEMANTICS = "operational_workload_without_unverified_communications_v1"
# Existing workload weights, with communication-only metrics excluded. These are
# operational bookkeeping weights, not a certificate of transport or payment.
KPI_OPERATIONAL_WEIGHTS = {
    "new_kol": 2, "project_created": 1, "link_created": 1, "published_content": 5,
    "valid_clicks": 0.02, "recommendation_shortlisted": 0.5,
    "recommendation_claimed": 1, "recommendation_project_created": 2,
    "recommendation_agreement_reached": 4, "recommendation_content_published": 5,
    "recommendation_order_attributed": 5, "stage_agreed": 4, "stage_shipped": 3,
    "stage_received": 1, "stage_published": 5, "stage_content_published": 5,
    "stage_measured": 3, "stage_closed": 1,
}


def is_communication_metric(key: Any) -> bool:
    return str(key or "").strip().lower() in COMMUNICATION_METRICS


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("metadata_json") or row.get("metadata")
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value) if isinstance(value, str) else None
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _component_value(component: Any, seen: set[str]) -> float | None:
    if not isinstance(component, dict):
        return None
    key = component.get("metric_key")
    if not isinstance(key, str) or key not in KPI_OPERATIONAL_WEIGHTS or key in seen:
        return None
    value, weight, contribution = (_number(component.get(name)) for name in ("metric_value", "weight", "contribution"))
    count = component.get("source_count")
    if value is None or contribution is None or weight != KPI_OPERATIONAL_WEIGHTS[key]:
        return None
    if type(count) is not int or count <= 0 or not math.isclose(value * weight, contribution, abs_tol=0.0001):
        return None
    seen.add(key)
    return contribution


def _derived_score_eligible(row: Mapping[str, Any], metric: str) -> bool:
    metadata = _metadata(row)
    components = metadata.get("components")
    if metadata.get("label_semantics") != KPI_LABEL_SEMANTICS or not isinstance(components, list):
        return False
    seen: set[str] = set()
    values = [_component_value(component, seen) for component in components]
    if any(value is None for value in values):
        return False
    total = sum(values)
    if metric == "kpi_credit":
        net = _number(metadata.get("net_contribution_cents"))
        bonus = _number(metadata.get("net_contribution_bonus"))
        if net is None or bonus is None or not math.isclose(max(net, 0) / 10000, bonus, abs_tol=0.0001):
            return False
        total += bonus
    actual = _number(row.get("metric_value"))
    return actual is not None and math.isclose(round(total, 4), actual, abs_tol=0.0001)


def kpi_score_eligibility(row: Mapping[str, Any]) -> bool:
    metric = str(row.get("metric_key") or "").strip().lower()
    if is_communication_metric(metric):
        return False
    if metric in DERIVED_SCORE_METRICS:
        return _derived_score_eligible(row, metric)
    return True


def project_kpi_source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    metric = str(result.get("metric_key") or "").strip().lower()
    if metric not in COMMUNICATION_METRICS | DERIVED_SCORE_METRICS:
        return result
    eligible = kpi_score_eligibility(result)
    result.setdefault("recorded_metric_value", result.get("metric_value"))
    result.setdefault("recorded_confidence", result.get("confidence"))
    result.update(aggregation_eligible=eligible, label_semantics=KPI_LABEL_SEMANTICS,
                  communication_evidence=communication_evidence())
    if not eligible:
        result.update(metric_value=None, current_metric_value=None, confidence="unverified",
                      business_truth_status="unverified", claim_status="descriptive_only",
                      metric_value_status="unknown", kpi_evidence_reason=(
                          "communication_receipt_unavailable" if is_communication_metric(metric)
                          else "derived_score_components_unverified"))
    else:
        result.update(current_metric_value=result.get("metric_value"), metric_value_status="operational",
                      confidence="operational", business_truth_status="operational", claim_status="descriptive_only")
    return result


def project_kpi_metric_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """SQL sums lose component provenance: never promote them to eligible points."""
    result = dict(row)
    metric = str(result.get("metric_key") or "").strip().lower()
    if metric not in COMMUNICATION_METRICS | DERIVED_SCORE_METRICS:
        return result
    result.setdefault("recorded_total_value", result.get("total_value"))
    result.setdefault("recorded_confidence", result.get("confidence"))
    result.update(total_value=None, current_total_value=None, confidence="unverified",
                  aggregation_eligible=False, metric_value_status="unknown", claim_status="descriptive_only",
                  business_truth_status="unverified", label_semantics=KPI_LABEL_SEMANTICS,
                  communication_evidence=communication_evidence())
    return result
