"""Verified outcome resolution for the prediction ledger facade."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

@dataclass(frozen=True)
class OutcomeEvalDependencies:
    """Late-bound prediction-ledger helpers retained for monkeypatchability."""

    text_or_none: Any
    int_or_none: Any
    bool_or_none: Any
    json_object: Any
    float_or_none: Any
    verified_binding: Any
    compute_metrics: Any
    truth: Any


def _failure(reason: str) -> dict[str, Any]:
    return {"ok": False, "id": None, "deduped": False, "reason": reason}


def _load_outcome(
    conn: Any,
    request: dict[str, Any],
    deps: OutcomeEvalDependencies,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    row = conn.execute(
        """
        SELECT decision, decided_at, decided_by, action_type, action_inbox_id,
               product_sku, market, channel,
               actual_result, window_7d, window_14d, window_28d
        FROM vkpi_gtm_outcomes
        WHERE id = ?
        """,
        (request["outcome_id"],),
    ).fetchone()
    if row is None:
        return None, _failure("outcome_not_found")
    outcome = dict(row)
    if (
        deps.text_or_none(outcome.get("decision"), 20) in (None, "open")
        or outcome.get("decided_at") is None
        or (deps.int_or_none(outcome.get("decided_by")) or 0) <= 0
    ):
        return None, _failure("outcome_not_finalized")
    return outcome, None


def _evidence_failure(
    conn: Any,
    outcome: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any] | None:
    from app.domains.market_brain import data_readiness

    field = request["evidence_field"]
    selected = {
        "actual_result": {},
        "window_7d": {},
        "window_14d": {},
        "window_28d": {},
        field: outcome.get(field),
    }
    observed = data_readiness.has_observed_outcome_evidence(selected)
    verified = observed and data_readiness.has_verified_outcome_evidence(
        conn,
        {**outcome, "id": request["outcome_id"]},
        evidence_field=field,
    )
    return None if verified else _failure("outcome_missing_observed_evidence")


def _load_run(
    conn: Any,
    outcome: dict[str, Any],
    request: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    row = conn.execute(
        """
        SELECT model_name, model_version, task_type, product_sku, market, channel,
               horizon_days, input_fingerprint, input_summary, prediction,
               p10, p50, p90, created_at,
               (created_at < ?) AS chronology_valid
        FROM vkpi_prediction_runs
        WHERE organization_id = ? AND run_id = ?
        """,
        (outcome.get("decided_at"), request["organization_id"], request["run_id"]),
    ).fetchone()
    if row is None:
        return None, _failure("run_not_found")
    return dict(row), None


def _contract_failure(
    contract: dict[str, Any],
    run: dict[str, Any],
    outcome: dict[str, Any],
    request: dict[str, Any],
    deps: OutcomeEvalDependencies,
) -> dict[str, Any] | None:
    if int(contract["target_action_inbox_id"]) != (
        deps.int_or_none(outcome.get("action_inbox_id")) or 0
    ):
        return _failure("actual_outcome_mismatch")
    if str(contract["task_type"]) != str(run.get("task_type") or ""):
        return _failure("actual_task_mismatch")
    if str(contract["outcome_action_type"]) != str(outcome.get("action_type") or ""):
        return _failure("actual_action_mismatch")
    path = request["metric_path"]
    if (
        str(contract["evidence_field"]) != request["evidence_field"]
        or str(contract["metric_path"]) != path
        or str(contract["metric_key"]) != path.split(".")[-1]
    ):
        return _failure("actual_metric_contract_mismatch")
    return None


def _dimension_failure(
    run: dict[str, Any],
    outcome: dict[str, Any],
    deps: OutcomeEvalDependencies,
) -> dict[str, Any] | None:
    for dimension in ("product_sku", "market", "channel"):
        run_value = deps.text_or_none(run.get(dimension), 120)
        outcome_value = deps.text_or_none(outcome.get(dimension), 120)
        if not run_value or not outcome_value or run_value.casefold() != outcome_value.casefold():
            return _failure(f"actual_{dimension}_mismatch")
    return None


def _horizon_failure(
    run: dict[str, Any],
    contract: dict[str, Any],
    request: dict[str, Any],
    deps: OutcomeEvalDependencies,
) -> tuple[int | None, dict[str, Any] | None]:
    expected = {"window_7d": 7, "window_14d": 14, "window_28d": 28}.get(
        request["evidence_field"]
    )
    horizon = deps.int_or_none(run.get("horizon_days"))
    invalid = (
        horizon is None
        or horizon <= 0
        or (expected is not None and horizon != expected)
        or horizon != int(contract["horizon_days"])
    )
    return (None, _failure("actual_horizon_mismatch")) if invalid else (horizon, None)


def _timing_failure(
    run: dict[str, Any],
    outcome: dict[str, Any],
    contract: dict[str, Any],
    request: dict[str, Any],
    horizon: int,
    deps: OutcomeEvalDependencies,
) -> dict[str, Any] | None:
    if deps.bool_or_none(run.get("chronology_valid")) is not True:
        return _failure("actual_chronology_invalid")
    if deps.truth.parse_iso_datetime(contract.get("observation_start_at")) != (
        deps.truth.parse_iso_datetime(run.get("created_at"))
    ):
        return _failure("actual_observation_anchor_invalid")
    field = request["evidence_field"]
    closed = deps.truth.outcome_evidence_is_closed(
        outcome.get(field),
        evidence_field=field,
        horizon_days=horizon,
        run_created_at=run.get("created_at"),
        outcome_decided_at=outcome.get("decided_at"),
        observation_start_at=contract.get("observation_start_at"),
    )
    return None if closed else _failure("actual_window_not_closed")


def _resolve_actual(
    outcome: dict[str, Any],
    request: dict[str, Any],
    deps: OutcomeEvalDependencies,
) -> tuple[float | None, dict[str, Any] | None]:
    node: Any = deps.json_object(outcome.get(request["evidence_field"]))
    for segment in request["metric_path"].split("."):
        if not segment or not isinstance(node, dict) or segment not in node:
            return None, _failure("actual_metric_not_found")
        node = node[segment]
    actual = deps.float_or_none(node)
    if actual is None:
        return None, _failure("actual_metric_not_numeric")
    return actual, None


def _run_snapshot(
    run: dict[str, Any],
    request: dict[str, Any],
    horizon: int,
    deps: OutcomeEvalDependencies,
) -> dict[str, Any]:
    snapshot = {
        "run_id": request["run_id"],
        "model_name": str(run.get("model_name") or ""),
        "model_version": str(run.get("model_version") or ""),
        "task_type": str(run.get("task_type") or ""),
        "input_fingerprint": str(run.get("input_fingerprint") or ""),
        "product_sku": run.get("product_sku"),
        "market": run.get("market"),
        "channel": run.get("channel"),
        "horizon_days": horizon,
        "p10": deps.float_or_none(run.get("p10")),
        "p50": deps.float_or_none(run.get("p50")),
        "p90": deps.float_or_none(run.get("p90")),
        "created_at": str(run.get("created_at") or ""),
        "prediction": deps.truth.json_value(run.get("prediction"), empty={}),
    }
    snapshot["sha256"] = _sha256(snapshot)
    return snapshot


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _actual_binding(
    outcome: dict[str, Any],
    run_snapshot: dict[str, Any],
    contract: dict[str, Any],
    request: dict[str, Any],
    actual: float,
    deps: OutcomeEvalDependencies,
) -> dict[str, Any] | None:
    binding = deps.verified_binding(
        {
            "outcome_id": request["outcome_id"],
            "evidence_field": request["evidence_field"],
            "metric_path": request["metric_path"],
            "metric_key": contract["metric_key"],
            "unit": contract["unit"],
            "task_type": contract["task_type"],
            "evaluation_contract_schema": contract["schema"],
            "evaluation_registry_key": contract["registry_key"],
            "observation_start_at": contract["observation_start_at"],
            "value": actual,
            "source": "server_resolved_finalized_outcome",
            "reviewed_by_staff_id": request["actor_id"],
            "outcome_decided_by_staff_id": int(outcome["decided_by"]),
            "correlation_id": request["correlation_id"],
            "run_snapshot_sha256": run_snapshot["sha256"],
        },
        outcome_row=outcome,
        outcome_id=request["outcome_id"],
        actual_value=actual,
    )
    if binding is None:
        return None
    field = request["evidence_field"]
    binding["outcome_evidence_sha256"] = _sha256(
        deps.truth.json_value(outcome.get(field), empty={})
    )
    binding["binding_sha256"] = _sha256(binding)
    return binding


def resolve_finalized_outcome_eval(
    conn: Any,
    *,
    request: dict[str, Any],
    deps: OutcomeEvalDependencies,
) -> dict[str, Any]:
    outcome, error = _load_outcome(conn, request, deps)
    if error is not None:
        return error
    error = _evidence_failure(conn, outcome, request)
    if error is not None:
        return error
    run, error = _load_run(conn, outcome, request)
    if error is not None:
        return error
    contract = deps.truth.parse_evaluation_contract(run)
    if contract is None:
        return _failure("prediction_evaluation_contract_missing")
    error = _contract_failure(contract, run, outcome, request, deps)
    if error is None:
        error = _dimension_failure(run, outcome, deps)
    if error is not None:
        return error
    horizon, error = _horizon_failure(run, contract, request, deps)
    if error is None:
        error = _timing_failure(run, outcome, contract, request, horizon, deps)
    if error is not None:
        return error
    actual, error = _resolve_actual(outcome, request, deps)
    if error is not None:
        return error
    snapshot = _run_snapshot(run, request, horizon, deps)
    binding = _actual_binding(outcome, snapshot, contract, request, actual, deps)
    if binding is None:
        return _failure("actual_evidence_binding_required")
    from app.domains.market_brain import prediction_reviews
    metrics = deps.compute_metrics(run.get("p10"), run.get("p50"), run.get("p90"), actual, None)
    return prediction_reviews.record_verified_eval(
        conn,
        organization_id=request["organization_id"],
        run_id=request["run_id"],
        outcome_id=request["outcome_id"],
        actual_value=actual,
        actual_json=binding,
        metrics=metrics,
        notes=request["notes"],
        actor_id=request["actor_id"],
        correlation_id=request["correlation_id"],
        run_snapshot=snapshot,
    )


__all__ = ["OutcomeEvalDependencies", "resolve_finalized_outcome_eval"]
