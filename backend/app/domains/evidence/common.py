"""Shared access and helper utilities for V-KPI evidence resources."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default

def _row(row: Any | None) -> dict[str, Any]:
    return dict(row) if row else {}

def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in rows]

def _actor_id(staff: dict[str, Any] | None) -> int:
    return scope.actor_staff_id(staff)

def _assert_kol_access(kol_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> None:
    if not kol_id or scope.can_view_all(staff):
        return
    actor = _actor_id(staff)
    if not actor:
        raise scope.ScopeDenied("kol scope denied")
    conn = get_conn()
    row = conn.execute(
        """
        SELECT k.assigned_staff_id, k.created_by_staff_id, c.staff_id AS claim_staff_id
        FROM kols k
        LEFT JOIN vkpi_kol_claims c
            ON c.kol_id = k.id AND c.status = 'active'
        WHERE k.id=?
        ORDER BY c.id DESC
        LIMIT 1
        """,
        (int(kol_id),),
    ).fetchone()
    if not row:
        return
    allowed = {
        _int(row["assigned_staff_id"]),
        _int(row["created_by_staff_id"]),
        _int(row["claim_staff_id"]),
    }
    if actor in allowed:
        return
    raise scope.ScopeDenied("kol scope denied")

def _project_context(project_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> dict[str, Any]:
    if not project_id:
        return {}
    scope.assert_project_access(project_id, staff, write=write)
    row = get_conn().execute(
        "SELECT id, kol_id, assigned_staff_id, created_by_staff_id, platform FROM vkpi_projects WHERE id=?",
        (int(project_id),),
    ).fetchone()
    if not row:
        raise LookupError("project not found")
    return dict(row)

def _assert_message_access(message_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_messages WHERE id=?", (int(message_id),)).fetchone()
    if not row:
        raise LookupError("message not found")
    item = dict(row)
    if item.get("project_id"):
        scope.assert_project_access(int(item["project_id"]), staff, write=write)
    elif item.get("kol_id"):
        _assert_kol_access(int(item["kol_id"]), staff, write=write)
    elif not scope.can_view_all(staff) and _int(item.get("staff_id")) != _actor_id(staff):
        raise scope.ScopeDenied("message scope denied")
    return item

def _assert_content_access(post_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_content_posts WHERE id=?", (int(post_id),)).fetchone()
    if not row:
        raise LookupError("content post not found")
    item = dict(row)
    if item.get("project_id"):
        scope.assert_project_access(int(item["project_id"]), staff, write=write)
    elif item.get("kol_id"):
        _assert_kol_access(int(item["kol_id"]), staff, write=write)
    elif not scope.can_view_all(staff):
        raise scope.ScopeDenied("content scope denied")
    return item

def _assert_terms_access(terms_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_project_terms WHERE id=?", (int(terms_id),)).fetchone()
    if not row:
        raise LookupError("terms not found")
    item = dict(row)
    scope.assert_project_access(int(item["project_id"]), staff, write=write)
    return item

def _assert_deliverable_access(deliverable_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_project_deliverables WHERE id=?", (int(deliverable_id),)).fetchone()
    if not row:
        raise LookupError("deliverable not found")
    item = dict(row)
    scope.assert_project_access(int(item["project_id"]), staff, write=write)
    return item

def _assert_sample_access(sample_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_sample_assets WHERE id=?", (int(sample_id),)).fetchone()
    if not row:
        raise LookupError("sample asset not found")
    item = dict(row)
    if item.get("project_id"):
        scope.assert_project_access(int(item["project_id"]), staff, write=write)
    elif item.get("kol_id"):
        _assert_kol_access(int(item["kol_id"]), staff, write=write)
    elif not scope.can_view_all(staff, domain="cost"):
        raise scope.ScopeDenied("sample scope denied")
    return item

def _assert_shipment_access(shipment_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_shipments WHERE id=?", (int(shipment_id),)).fetchone()
    if not row:
        raise LookupError("shipment not found")
    item = dict(row)
    if item.get("project_id"):
        scope.assert_project_access(int(item["project_id"]), staff, write=write)
    elif not scope.can_view_all(staff, domain="cost"):
        raise scope.ScopeDenied("shipment scope denied")
    return item
