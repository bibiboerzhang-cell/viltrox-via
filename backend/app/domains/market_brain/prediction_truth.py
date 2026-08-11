"""Pure truth contracts for immutable prediction runs and outcome evaluation.

This module has no database, provider, or model dependency.  It canonicalizes
the complete prediction payload used for append-only replay checks and parses
the explicit contract required before a finalized outcome may count as an
actual.  Historical runs without this contract remain descriptive-only.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

EVALUATION_CONTRACT_SCHEMA = "vkpi_prediction_evaluation_contract/v1"
EVALUATION_UNITS = {"count", "views", "cents", "ratio", "percent", "seconds"}
EVALUATION_FIELDS = {"actual_result", "window_7d", "window_14d", "window_28d"}
EVALUABLE_CONFIDENCE = {"low", "medium", "high"}
EVALUABLE_SOURCE_STEPS = {"baseline", "model", "rule", "human_override"}

# Only server-owned combinations may become outcome-bound learning evidence.
# The first bounded producer uses the existing provider-free GTM outreach rule
# baseline.  Event timestamps make the 7-day binary reply actual stable even when
# the observation job runs late; mutable cumulative view counters are excluded.
GTM_EVALUATION_REGISTRY: dict[str, dict[str, Any]] = {
    "kol_outreach_reply_outcome_7d": {
        "outcome_action_type": "kol_outreach",
        "task_type": "kol_outreach_reply_probability",
        "metric_key": "reply_outcome",
        "metric_path": "metrics.reply_outcome",
        "unit": "ratio",
        "evidence_field": "window_7d",
        "horizon_days": 7,
    },
}


def _text(value: Any, limit: int) -> str | None:
    normalized = " ".join(str(value or "").replace("\x00", " ").split())[:limit]
    return normalized or None


def _int(value: Any) -> int | None:
    try:
        if isinstance(value, bool) or value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        if isinstance(value, bool) or value in (None, ""):
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def json_value(value: Any, *, empty: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return empty
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return empty


def canonical_prediction_payload(
    *,
    model_name: str,
    model_version: str,
    task_type: str,
    product_sku: str | None,
    market: str | None,
    channel: str | None,
    horizon_days: int | None,
    input_fingerprint: str,
    input_summary: Any,
    prediction: Any,
    p10: Any,
    p50: Any,
    p90: Any,
    confidence: str,
    confidence_score: Any,
    missing_data: Any,
    basis: Any,
    baseline_value: Any,
    source_step: Any,
) -> dict[str, Any]:
    return {
        "model_name": model_name,
        "model_version": model_version,
        "task_type": task_type,
        "product_sku": product_sku,
        "market": market,
        "channel": channel,
        "horizon_days": horizon_days,
        "input_fingerprint": input_fingerprint,
        "input_summary": json_value(input_summary, empty={}),
        "prediction": json_value(prediction, empty={}),
        "p10": _float(p10),
        "p50": _float(p50),
        "p90": _float(p90),
        "confidence": confidence,
        "confidence_score": _float(confidence_score),
        "missing_data": json_value(missing_data, empty=[]),
        "basis": json_value(basis, empty=[]),
        "baseline_value": _float(baseline_value),
        "source_step": _text(source_step, 40),
    }


def prediction_payload_matches(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    observed = canonical_prediction_payload(
        model_name=str(row.get("model_name") or ""),
        model_version=str(row.get("model_version") or ""),
        task_type=str(row.get("task_type") or ""),
        product_sku=_text(row.get("product_sku"), 120),
        market=_text(row.get("market"), 40),
        channel=_text(row.get("channel"), 60),
        horizon_days=_int(row.get("horizon_days")),
        input_fingerprint=str(row.get("input_fingerprint") or ""),
        input_summary=row.get("input_summary"),
        prediction=row.get("prediction"),
        p10=row.get("p10"),
        p50=row.get("p50"),
        p90=row.get("p90"),
        confidence=str(row.get("confidence") or ""),
        confidence_score=row.get("confidence_score"),
        missing_data=row.get("missing_data"),
        basis=row.get("basis"),
        baseline_value=row.get("baseline_value"),
        source_step=row.get("source_step"),
    )
    return observed == expected


def parse_evaluation_contract(run: dict[str, Any]) -> dict[str, Any] | None:
    """Return a complete immutable evaluation contract or ``None``.

    The contract lives inside ``input_summary.evaluation_contract`` so it is
    frozen together with the append-only prediction.  The outcome id and metric
    path are selected at forecast time, not after the result is visible.
    """
    summary = json_value(run.get("input_summary"), empty={})
    contract = summary.get("evaluation_contract") if isinstance(summary, dict) else None
    if not isinstance(contract, dict):
        return None
    normalized = {
        "schema": _text(contract.get("schema"), 80),
        "registry_key": _text(contract.get("registry_key"), 100),
        "target_action_inbox_id": _int(contract.get("target_action_inbox_id")),
        "outcome_action_type": _text(contract.get("outcome_action_type"), 80),
        "task_type": _text(contract.get("task_type"), 80),
        "metric_key": _text(contract.get("metric_key"), 80),
        "metric_path": _text(contract.get("metric_path"), 200),
        "unit": _text(contract.get("unit"), 40),
        "evidence_field": _text(contract.get("evidence_field"), 40),
        "horizon_days": _int(contract.get("horizon_days")),
        "observation_start_at": _text(contract.get("observation_start_at"), 80),
    }
    if normalized["schema"] != EVALUATION_CONTRACT_SCHEMA:
        return None
    if not normalized["target_action_inbox_id"] or normalized["target_action_inbox_id"] <= 0:
        return None
    if not normalized["outcome_action_type"] or not normalized["task_type"]:
        return None
    if not normalized["metric_key"] or not normalized["metric_path"]:
        return None
    if re.fullmatch(r"[A-Za-z0-9_.-]+", str(normalized["metric_path"])) is None:
        return None
    if normalized["unit"] not in EVALUATION_UNITS:
        return None
    if normalized["evidence_field"] not in EVALUATION_FIELDS:
        return None
    if not normalized["horizon_days"] or normalized["horizon_days"] <= 0:
        return None
    registry_key = normalized["registry_key"]
    spec = GTM_EVALUATION_REGISTRY.get(str(registry_key or ""))
    if spec is None:
        return None
    for key in (
        "outcome_action_type", "task_type", "metric_key", "metric_path",
        "unit", "evidence_field", "horizon_days",
    ):
        if normalized[key] != spec[key]:
            return None
    start = parse_iso_datetime(normalized["observation_start_at"])
    if start is None:
        return None
    normalized["observation_start_at"] = start.isoformat()
    return normalized


def build_registered_gtm_evaluation_contract(
    registry_key: str,
    *,
    target_action_inbox_id: int,
    observation_start_at: Any,
) -> dict[str, Any]:
    """Build a complete contract from a server registry entry.

    Callers choose only the registry key, target action and server timestamp;
    the task/action/metric/path/unit/horizon tuple cannot be supplied or
    overridden by a client payload.
    """
    spec = GTM_EVALUATION_REGISTRY.get(str(registry_key or ""))
    action_id = _int(target_action_inbox_id)
    start = parse_iso_datetime(observation_start_at)
    if spec is None:
        raise ValueError("unknown_prediction_evaluation_registry_key")
    if action_id is None or action_id <= 0:
        raise ValueError("target_action_inbox_id_required")
    if start is None:
        raise ValueError("observation_start_at_required")
    return {
        "schema": EVALUATION_CONTRACT_SCHEMA,
        "registry_key": str(registry_key),
        "target_action_inbox_id": action_id,
        **spec,
        "observation_start_at": start.isoformat(),
    }


def evaluable_prediction_error(payload: dict[str, Any]) -> str | None:
    """Validate the stricter write contract only when an eval contract exists."""
    contract = parse_evaluation_contract(payload)
    if contract is None:
        summary = json_value(payload.get("input_summary"), empty={})
        if isinstance(summary, dict) and "evaluation_contract" in summary:
            return "prediction_evaluation_contract_invalid"
        return None
    prediction = payload.get("prediction")
    if not isinstance(prediction, dict):
        return "evaluable_prediction_object_required"
    if str(payload.get("task_type") or "") != str(contract["task_type"]):
        return "evaluable_prediction_task_mismatch"
    if _int(payload.get("horizon_days")) != int(contract["horizon_days"]):
        return "evaluable_prediction_horizon_mismatch"
    if str(prediction.get("metric_key") or "") != str(contract["metric_key"]):
        return "evaluable_prediction_metric_mismatch"
    if str(prediction.get("unit") or "") != str(contract["unit"]):
        return "evaluable_prediction_unit_mismatch"
    p10 = _float(payload.get("p10"))
    p50 = _float(payload.get("p50"))
    p90 = _float(payload.get("p90"))
    if p50 is None:
        return "evaluable_prediction_p50_required"
    point_value = _float(prediction.get("value"))
    if point_value is None or point_value != p50:
        return "evaluable_prediction_point_mismatch"
    if (p10 is None) != (p90 is None):
        return "evaluable_prediction_interval_incomplete"
    if p10 is not None and p90 is not None and not p10 <= p50 <= p90:
        return "evaluable_prediction_interval_invalid"
    if str(contract.get("task_type") or "") == "kol_outreach_reply_probability":
        prediction_p10 = _float(prediction.get("p10"))
        prediction_p50 = _float(prediction.get("p50"))
        prediction_p90 = _float(prediction.get("p90"))
        if (
            p10 is None or p90 is None
            or prediction_p10 is None or prediction_p50 is None or prediction_p90 is None
            or not 0.0 <= p10 <= p50 <= p90 <= 1.0
            or (prediction_p10, prediction_p50, prediction_p90) != (p10, p50, p90)
        ):
            return "evaluable_binary_probability_invalid"
    confidence = str(payload.get("confidence") or "").strip().lower()
    if confidence not in EVALUABLE_CONFIDENCE:
        return "evaluable_prediction_confidence_invalid"
    confidence_score = payload.get("confidence_score")
    if confidence_score is not None:
        score = _float(confidence_score)
        if score is None or not 0.0 <= score <= 1.0:
            return "evaluable_prediction_confidence_score_invalid"
    if str(payload.get("source_step") or "") not in EVALUABLE_SOURCE_STEPS:
        return "evaluable_prediction_source_step_invalid"
    return None


def parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def outcome_window_is_closed(
    evidence: Any,
    *,
    run_created_at: Any,
    outcome_decided_at: Any,
) -> bool:
    payload = json_value(evidence, empty={})
    if not isinstance(payload, dict) or str(payload.get("status") or "").lower() != "filled":
        return False
    start = parse_iso_datetime(payload.get("window_start"))
    end = parse_iso_datetime(payload.get("window_end"))
    filled = parse_iso_datetime(payload.get("filled_at"))
    created = parse_iso_datetime(run_created_at)
    decided = parse_iso_datetime(outcome_decided_at)
    return bool(
        start and end and filled and created and decided
        and created <= start < end <= filled <= decided
        and end - start == timedelta(days=max(1, int((end - start).days)))
    )


def outcome_evidence_is_closed(
    evidence: Any,
    *,
    evidence_field: str,
    horizon_days: int,
    run_created_at: Any,
    outcome_decided_at: Any,
    observation_start_at: Any = None,
) -> bool:
    """Prove that the contracted observation window closed before review."""
    if evidence_field in {"window_7d", "window_14d", "window_28d"}:
        payload = json_value(evidence, empty={})
        start = parse_iso_datetime(payload.get("window_start")) if isinstance(payload, dict) else None
        end = parse_iso_datetime(payload.get("window_end")) if isinstance(payload, dict) else None
        if not start or not end or end - start != timedelta(days=max(1, int(horizon_days))):
            return False
        contracted_start = parse_iso_datetime(observation_start_at)
        if contracted_start is not None and start != contracted_start:
            return False
        return outcome_window_is_closed(
            evidence,
            run_created_at=run_created_at,
            outcome_decided_at=outcome_decided_at,
        )
    payload = json_value(evidence, empty={})
    if not isinstance(payload, dict) or str(payload.get("status") or "").lower() != "filled":
        return False
    created = parse_iso_datetime(run_created_at)
    observed = parse_iso_datetime(payload.get("observed_at") or payload.get("filled_at"))
    decided = parse_iso_datetime(outcome_decided_at)
    return bool(
        created and observed and decided
        and created + timedelta(days=max(1, int(horizon_days))) <= observed <= decided
    )


__all__ = [
    "EVALUATION_CONTRACT_SCHEMA", "GTM_EVALUATION_REGISTRY",
    "build_registered_gtm_evaluation_contract", "canonical_prediction_payload", "evaluable_prediction_error",
    "outcome_evidence_is_closed", "outcome_window_is_closed", "parse_evaluation_contract",
    "prediction_payload_matches",
]
