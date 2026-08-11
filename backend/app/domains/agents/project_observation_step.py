"""Zero-provider plan tool for one exact project/assignment observation window.

The caller holds the Action/plan transaction.  This module never commits,
contacts a provider, invokes an LLM, or changes project/assignment state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import is_postgres_runtime
from app.domains.access import scope
from app.domains.platform import review_contract
from app.domains.projects import observation_window_open

_AFFECTED_TABLE = "vkpi_project_content_observation_windows"


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 and str(value).strip() == str(parsed) else None


def _utc_datetime(value: Any) -> datetime | None:
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = datetime.fromisoformat(parsed.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(parsed, datetime):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def execute(
    conn: Any,
    action: dict[str, Any],
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Open at most one exact window and return server-observed delta evidence."""
    if review_contract.reviewer_context(staff) is None or not scope.can_view_all(staff):
        return {"outcome": "skipped", "reason": "manager_execution_required", "detail": {}}
    payload = action.get("payload_json") if isinstance(action.get("payload_json"), dict) else {}
    project_id = _positive_int(payload.get("project_id"))
    assignment_id = _positive_int(payload.get("assignment_id"))
    if project_id is None or assignment_id is None:
        return {"outcome": "skipped", "reason": "exact_project_assignment_required", "detail": {}}

    # Defense in depth: the locked plan already binds the project, but the
    # business write path independently asserts write access and exact FK scope.
    scope.assert_project_access(project_id, staff, write=True)
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    project = conn.execute(
        f"SELECT id FROM vkpi_projects WHERE id=?{lock}", (project_id,),
    ).fetchone()
    if project is None:
        return {"outcome": "skipped", "reason": "project_missing", "detail": {}}
    assignment = conn.execute(
        "SELECT id, kol_pool_id FROM vkpi_project_kol_assignments "
        f"WHERE id = ? AND project_id = ?{lock}",
        (assignment_id, project_id),
    ).fetchone()
    if assignment is None:
        return {
            "outcome": "skipped",
            "reason": "assignment_project_mismatch",
            "detail": {"project_id": project_id, "assignment_id": assignment_id},
        }
    assignment_row = dict(assignment)
    shipment = conn.execute(
        "SELECT id, delivered_at FROM vkpi_shipments "
        "WHERE project_id = ? AND assignment_id = ? AND delivered_at IS NOT NULL "
        f"ORDER BY delivered_at ASC, id ASC LIMIT 1{lock}",
        (project_id, assignment_id),
    ).fetchone()
    if shipment is None:
        return {
            "outcome": "skipped",
            "reason": "delivered_shipment_required",
            "detail": {"project_id": project_id, "assignment_id": assignment_id},
        }
    shipment_row = dict(shipment)
    delivered_at = _utc_datetime(shipment_row.get("delivered_at"))
    if delivered_at is None:
        raise RuntimeError("delivered_at_invalid")
    if delivered_at > datetime.now(timezone.utc) - timedelta(days=7):
        return {
            "outcome": "skipped",
            "reason": "observation_window_not_due",
            "detail": {
                "project_id": project_id,
                "assignment_id": assignment_id,
                "shipment_id": int(shipment_row["id"]),
            },
        }

    result = observation_window_open.open_window_for_delivered_in_transaction(
        conn,
        project_id,
        assignment_id,
        assignment_row.get("kol_pool_id"),
        delivered_at,
        staff,
        source_shipment_id=int(shipment_row["id"]),
    )
    status = str(result.get("status") or "")
    base_detail = {
        "project_id": project_id,
        "assignment_id": assignment_id,
        "shipment_id": int(shipment_row["id"]),
        "affected_tables": [_AFFECTED_TABLE],
    }
    if status == "created":
        window = result.get("window") if isinstance(result.get("window"), dict) else {}
        window_id = _positive_int(window.get("id"))
        if window_id is None:
            raise RuntimeError("observation_window_insert_missing_id")
        return {
            "outcome": "success",
            "reason": "",
            "detail": {
                **base_detail,
                "created_windows": [window_id],
                "idempotent": False,
                "state_delta": {
                    "target": f"project:{project_id}:assignment:{assignment_id}",
                    "before": "missing",
                    "after": "pending",
                    "rows_created": 1,
                },
            },
        }
    if status == "skipped" and str(result.get("reason") or "") in {
        "duplicate_active_window", "duplicate_exact_window", "duplicate_source_shipment",
    }:
        existing_status = str(result.get("window_status") or "existing")
        return {
            "outcome": "success",
            "reason": "",
            "detail": {
                **base_detail,
                "created_windows": [],
                "existing_window_id": _positive_int(result.get("window_id")),
                "idempotent": True,
                "state_delta": {
                    "target": f"project:{project_id}:assignment:{assignment_id}",
                    "before": existing_status,
                    "after": existing_status,
                    "rows_created": 0,
                },
            },
        }
    raise RuntimeError(str(result.get("error") or result.get("reason") or "observation_window_write_failed"))


__all__ = ["execute"]
