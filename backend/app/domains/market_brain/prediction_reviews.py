"""Atomic human verification receipt for outcome-bound prediction actuals."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.db.connection import is_postgres_runtime
from app.domains.platform import event_ledger

_EVALS = "vkpi_prediction_evals"
_EVENTS = "vkpi_event_ledger"
_SOURCE = "prediction_ledger.human_actual_review"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float(value: Any) -> float | None:
    try:
        return None if isinstance(value, bool) or value is None else float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "t", "true", "yes"}:
        return True
    if text in {"0", "f", "false", "no"}:
        return False
    return None


def _payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest()


def _event_matches(
    conn: Any,
    *,
    eval_id: int,
    actor_id: int,
    payload: dict[str, Any],
    provenance: dict[str, Any],
) -> bool:
    rows = conn.execute(
        f"""
        SELECT actor_id, payload_json, provenance_json
        FROM {_EVENTS}
        WHERE event_type='prediction_actual_verified'
          AND entity_type='prediction_eval'
          AND entity_id=? AND source=?
        ORDER BY id
        """,
        (str(eval_id), _SOURCE),
    ).fetchall()
    if len(rows) != 1:
        return False
    row = dict(rows[0])
    return (
        str(row.get("actor_id") or "") == str(actor_id)
        and _loads(row.get("payload_json")) == payload
        and _loads(row.get("provenance_json")) == provenance
    )


def _existing_matches(
    row: dict[str, Any],
    *,
    actual_value: float,
    actual_json: dict[str, Any],
    metrics: dict[str, Any],
    notes: str | None,
) -> bool:
    return (
        _float(row.get("actual_value")) == actual_value
        and _loads(row.get("actual_json")) == actual_json
        and _float(row.get("error_abs")) == _float(metrics.get("error_abs"))
        and _float(row.get("error_pct")) == _float(metrics.get("error_pct"))
        and _bool(row.get("interval_hit")) == _bool(metrics.get("interval_hit"))
        and _bool(row.get("direction_hit")) == _bool(metrics.get("direction_hit"))
        and (str(row.get("notes") or "") or None) == notes
    )


def record_verified_eval(
    conn: Any,
    *,
    organization_id: str,
    run_id: str,
    outcome_id: int,
    actual_value: float,
    actual_json: dict[str, Any],
    metrics: dict[str, Any],
    notes: str | None,
    actor_id: int,
    correlation_id: str,
    run_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Insert eval + immutable verification event in one transaction."""
    event_payload = {
        "run_id": run_id,
        "outcome_id": outcome_id,
        "correlation_id": correlation_id,
        "actual_binding_sha256": str(actual_json.get("binding_sha256") or ""),
        "run_snapshot_sha256": str(run_snapshot.get("sha256") or ""),
        "outcome_evidence_sha256": str(actual_json.get("outcome_evidence_sha256") or ""),
    }
    provenance = {
        "kind": "human_review",
        "evidence_verification": "server_resolved_outcome_contract",
        "prediction_run_immutable": True,
        "payload_sha256": _payload_sha256(event_payload),
    }
    existing = conn.execute(
        f"""
        SELECT id, actual_value, actual_json, error_abs, error_pct,
               interval_hit, direction_hit, notes
        FROM {_EVALS}
        WHERE organization_id=? AND run_id=? AND outcome_id=?
        """,
        (organization_id, run_id, outcome_id),
    ).fetchone()
    if existing is not None:
        existing_row = dict(existing)
        eval_id = int(existing_row["id"])
        same = _existing_matches(
            existing_row,
            actual_value=actual_value,
            actual_json=actual_json,
            metrics=metrics,
            notes=notes,
        ) and _event_matches(
            conn,
            eval_id=eval_id,
            actor_id=actor_id,
            payload=event_payload,
            provenance=provenance,
        )
        conn.rollback()
        if not same:
            return {"ok": False, "id": eval_id, "deduped": False, "reason": "actual_evidence_conflict"}
        return {"ok": True, "id": eval_id, "deduped": True, **metrics}

    json_param = "?::jsonb" if is_postgres_runtime() else "?"
    inserted = conn.execute(
        f"""
        INSERT INTO {_EVALS} (
            organization_id, run_id, outcome_id, actual_value, actual_json,
            error_abs, error_pct, interval_hit, direction_hit, calibrated_bucket, notes
        ) VALUES (?,?,?,?,{json_param}, ?,?,?,?, NULL,?)
        ON CONFLICT (organization_id, run_id, outcome_id) DO NOTHING
        RETURNING id
        """,
        (
            organization_id, run_id, outcome_id, actual_value, _dumps(actual_json),
            metrics.get("error_abs"), metrics.get("error_pct"), metrics.get("interval_hit"),
            metrics.get("direction_hit"), notes,
        ),
    ).fetchone()
    if inserted is None:
        raced = conn.execute(
            f"""
            SELECT id, actual_value, actual_json, error_abs, error_pct,
                   interval_hit, direction_hit, notes
            FROM {_EVALS}
            WHERE organization_id=? AND run_id=? AND outcome_id=?
            """,
            (organization_id, run_id, outcome_id),
        ).fetchone()
        raced_row = dict(raced) if raced is not None else {}
        raced_id = int(raced_row["id"]) if raced_row.get("id") is not None else None
        same = bool(
            raced_id is not None
            and _existing_matches(
                raced_row,
                actual_value=actual_value,
                actual_json=actual_json,
                metrics=metrics,
                notes=notes,
            )
            and _event_matches(
                conn,
                eval_id=raced_id,
                actor_id=actor_id,
                payload=event_payload,
                provenance=provenance,
            )
        )
        conn.rollback()
        if same:
            return {"ok": True, "id": raced_id, "deduped": True, **metrics}
        return {
            "ok": False,
            "id": raced_id,
            "deduped": False,
            "reason": "actual_evidence_conflict",
        }
    eval_id = int(dict(inserted)["id"])
    event_ledger.insert_required(
        conn,
        "prediction_actual_verified",
        entity_type="prediction_eval",
        entity_id=eval_id,
        actor_type="staff",
        actor_id=actor_id,
        source=_SOURCE,
        payload=event_payload,
        trace_id=event_ledger.new_trace_id("prediction_eval", eval_id),
        provenance=provenance,
        organization_id=1,
    )
    conn.commit()
    return {"ok": True, "id": eval_id, "deduped": False, **metrics}


__all__ = ["record_verified_eval"]
