"""Transactional primitive for opening one delivered-shipment observation window.

The caller owns commit/rollback and any event emission.  This module is kept
separate from the legacy observation-window application service so Agent
execution can make the business row, Action ledger, tool receipt, and plan
transition one transaction.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.db.connection import is_postgres_runtime
from app.domains.access import scope

_WINDOW_START_OFFSET_DAYS = 7
_WINDOW_END_OFFSET_DAYS = 45
_WINDOW_ACTIVE_STATUSES = ("pending", "scanning", "matched")


def _nullable_int(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_to_window(row: Any) -> dict[str, Any]:
    item = dict(row)
    raw = item.get("metadata_json")
    if isinstance(raw, str):
        try:
            item["metadata_json"] = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            item["metadata_json"] = {}
    for column in ("starts_at", "ends_at", "last_scan_at", "created_at", "updated_at"):
        value = item.get(column)
        if value is not None and not isinstance(value, str):
            item[column] = str(value)
    return item


def open_window_for_delivered_in_transaction(
    conn: Any,
    project_id: int,
    assignment_id: int | None,
    kol_pool_id: int | None,
    delivered_at: Any,
    staff: dict[str, Any] | None = None,
    *,
    source_shipment_id: int | None = None,
) -> dict[str, Any]:
    """Create one exact delivered-assignment window without commit or event."""
    pid = int(project_id or 0)
    if pid <= 0:
        return {"status": "error", "error": "project_id required"}

    delivered = delivered_at
    if isinstance(delivered, str):
        try:
            delivered = datetime.fromisoformat(delivered.replace("Z", "+00:00"))
        except ValueError:
            delivered = None
    if not isinstance(delivered, datetime):
        return {"status": "error", "error": "valid delivered_at required"}

    aid = _nullable_int(assignment_id)
    kpid = _nullable_int(kol_pool_id)
    shipment_id = _nullable_int(source_shipment_id)
    starts_at = delivered + timedelta(days=_WINDOW_START_OFFSET_DAYS)
    ends_at = delivered + timedelta(days=_WINDOW_END_OFFSET_DAYS)

    postgres = is_postgres_runtime()
    db_starts_at = starts_at if postgres else starts_at.isoformat(sep=" ")
    db_ends_at = ends_at if postgres else ends_at.isoformat(sep=" ")
    lock_clause = " FOR UPDATE" if postgres else ""
    project = conn.execute(
        f"SELECT id FROM vkpi_projects WHERE id = ?{lock_clause}",
        (pid,),
    ).fetchone()
    if project is None:
        return {"status": "error", "error": "project_not_found"}
    if shipment_id is not None:
        sourced = conn.execute(
            "SELECT id,project_id,assignment_id,kol_pool_id,status "
            "FROM vkpi_project_content_observation_windows WHERE source_shipment_id=?",
            (shipment_id,),
        ).fetchone()
        if sourced is not None:
            item = dict(sourced)
            if (
                int(item.get("project_id") or 0) != pid
                or _nullable_int(item.get("assignment_id")) != aid
                or _nullable_int(item.get("kol_pool_id")) != kpid
            ):
                return {"status": "error", "error": "shipment_window_contract_conflict"}
            return {
                "status": "skipped",
                "reason": "duplicate_source_shipment",
                "window_id": int(item["id"]),
                "window_status": str(item.get("status") or "existing"),
            }

    status_placeholders = ",".join(["?"] * len(_WINDOW_ACTIVE_STATUSES))
    where_parts = ["project_id = ?"]
    params: list[Any] = [pid]
    if aid is None:
        where_parts.append("assignment_id IS NULL")
    else:
        where_parts.append("assignment_id = ?")
        params.append(aid)
    if kpid is None:
        where_parts.append("kol_pool_id IS NULL")
    else:
        where_parts.append("kol_pool_id = ?")
        params.append(kpid)
    exact = conn.execute(
        f"SELECT id,status FROM vkpi_project_content_observation_windows "
        f"WHERE {' AND '.join(where_parts)} AND starts_at=? AND ends_at=? "
        "ORDER BY id LIMIT 1",
        (*params, db_starts_at, db_ends_at),
    ).fetchone()
    if exact is not None:
        exact_item = dict(exact)
        return {
            "status": "skipped",
            "reason": "duplicate_exact_window",
            "window_id": int(exact_item["id"]),
            "window_status": str(exact_item.get("status") or "existing"),
        }
    where_parts.append(f"status IN ({status_placeholders})")
    params.extend(_WINDOW_ACTIVE_STATUSES)

    existing = conn.execute(
        f"""
        SELECT id,status FROM vkpi_project_content_observation_windows
        WHERE {' AND '.join(where_parts)}
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if existing is not None:
        existing_item = dict(existing)
        return {
            "status": "skipped",
            "reason": "duplicate_active_window",
            "window_id": int(existing_item["id"]),
            "window_status": str(existing_item.get("status") or "existing"),
        }

    metadata = json.dumps(
        {
            "delivered_at": str(delivered),
            "opened_by_staff_id": scope.actor_staff_id(staff) or None,
            "source_shipment_id": shipment_id,
            "window_offset_days": [_WINDOW_START_OFFSET_DAYS, _WINDOW_END_OFFSET_DAYS],
        },
        ensure_ascii=False,
    )
    if shipment_id is None:
        cursor = conn.execute(
            """
            INSERT INTO vkpi_project_content_observation_windows
                (project_id, assignment_id, kol_pool_id, starts_at, ends_at, status, metadata_json)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            RETURNING *
            """,
            (pid, aid, kpid, db_starts_at, db_ends_at, metadata),
        )
    else:
        cursor = conn.execute(
            """
            INSERT INTO vkpi_project_content_observation_windows
                (project_id, assignment_id, kol_pool_id, starts_at, ends_at, status,
                 metadata_json, source_shipment_id)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(source_shipment_id) WHERE source_shipment_id IS NOT NULL DO NOTHING
            RETURNING *
            """,
            (pid, aid, kpid, db_starts_at, db_ends_at, metadata, shipment_id),
        )
    row = cursor.fetchone()
    if row is None and shipment_id is not None:
        existing = conn.execute(
            "SELECT id,status FROM vkpi_project_content_observation_windows "
            "WHERE source_shipment_id=?",
            (shipment_id,),
        ).fetchone()
        if existing is None:
            return {"status": "error", "error": "source_shipment_conflict_without_row"}
        return {
            "status": "skipped",
            "reason": "duplicate_source_shipment",
            "window_id": int(dict(existing)["id"]),
            "window_status": str(dict(existing).get("status") or "existing"),
        }
    return {"status": "created", "window": _row_to_window(row)}


__all__ = ["open_window_for_delivered_in_transaction"]
