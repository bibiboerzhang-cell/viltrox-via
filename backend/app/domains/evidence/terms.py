"""Terms and deliverables operations for V-KPI evidence resources."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains import audit
from app.services.vkpi import scope, workflow
from app.domains.evidence.common import (
    _actor_id,
    _assert_deliverable_access,
    _assert_kol_access,
    _assert_terms_access,
    _int,
    _project_context,
    _row,
    _rows,
    _utcnow,
)
from app.services.vkpi.schema import ensure_vkpi_schema

def _sync_deliverables(project_id: int, deliverables: Any, *, replace: bool = True) -> list[dict[str, Any]]:
    if not isinstance(deliverables, list):
        return []
    conn = get_conn()
    now = _utcnow()
    if replace:
        conn.execute("DELETE FROM vkpi_project_deliverables WHERE project_id=?", (int(project_id),))
    inserted: list[dict[str, Any]] = []
    for item in deliverables:
        if not isinstance(item, dict):
            continue
        conn.execute(
            """
            INSERT INTO vkpi_project_deliverables (
                project_id, deliverable_type, quantity, status, due_at,
                delivered_at, evidence_url, note, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(project_id),
                str(item.get("deliverable_type") or item.get("type") or "video"),
                _int(item.get("quantity"), 1) or 1,
                str(item.get("status") or "planned"),
                item.get("due_at"),
                item.get("delivered_at"),
                str(item.get("evidence_url") or ""),
                str(item.get("note") or ""),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM vkpi_project_deliverables WHERE project_id=? ORDER BY id DESC LIMIT 1", (int(project_id),)).fetchone()
        if row:
            inserted.append(dict(row))
    return inserted

def list_terms(
    *,
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = 100,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    where: list[str] = []
    params: list[Any] = []
    if project_id:
        _project_context(int(project_id), staff)
        where.append("t.project_id = ?")
        params.append(int(project_id))
    if kol_id:
        _assert_kol_access(int(kol_id), staff)
        where.append("p.kol_id = ?")
        params.append(int(kol_id))
    scoped_staff = scope.effective_staff_id(staff, staff_id)
    if scoped_staff:
        where.append("(p.assigned_staff_id = ? OR p.created_by_staff_id = ?)")
        params.extend([scoped_staff, scoped_staff])
    sql = """
        SELECT t.*, p.project_name, p.kol_id, p.product_sku, p.product_name,
               k.channel_name AS kol_name, u.name AS staff_name
        FROM vkpi_project_terms t
        LEFT JOIN vkpi_projects p ON p.id = t.project_id
        LEFT JOIN kols k ON k.id = p.kol_id
        LEFT JOIN staff s ON s.id = p.assigned_staff_id
        LEFT JOIN users u ON u.id = s.user_id
    """
    if where:
        sql += " WHERE " + " AND ".join(f"({item})" for item in where)
    sql += " ORDER BY t.updated_at DESC, t.id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit or 100))))
    rows = _rows(conn.execute(sql, params).fetchall())
    terms_ids = [int(item["id"]) for item in rows if item.get("id")]
    deliverables_by_project: dict[int, list[dict[str, Any]]] = {}
    if rows:
        project_ids = [int(item["project_id"]) for item in rows if item.get("project_id")]
        if project_ids:
            ph = ",".join("?" for _ in project_ids)
            for item in conn.execute(f"SELECT * FROM vkpi_project_deliverables WHERE project_id IN ({ph}) ORDER BY due_at ASC, id ASC", project_ids).fetchall():
                row = dict(item)
                deliverables_by_project.setdefault(int(row["project_id"]), []).append(row)
    for item in rows:
        item["deliverables"] = deliverables_by_project.get(int(item.get("project_id") or 0), [])
    return {"terms": rows, "count": len(rows), "terms_ids": terms_ids}

def upsert_terms(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    project_id = _int(body.get("project_id"))
    if not project_id:
        raise ValueError("project_id required")
    _project_context(project_id, staff, write=True)
    result = workflow.upsert_project_terms(project_id, body, staff=staff)
    inserted = _sync_deliverables(project_id, body.get("deliverables"), replace=True)
    get_conn().commit()
    audit.log_business_event(
        staff_id=_actor_id(staff),
        action_type="terms_upsert",
        target_type="project",
        target_id=project_id,
        detail=str(body.get("note") or body.get("usage_rights") or ""),
        metadata={"project_id": project_id, "terms_id": result.get("id"), "deliverable_count": len(inserted)},
    )
    return {"terms": result, "deliverables": inserted}

def get_terms(terms_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    terms = _assert_terms_access(int(terms_id), staff)
    deliverables = _rows(
        get_conn()
        .execute(
            "SELECT * FROM vkpi_project_deliverables WHERE project_id=? ORDER BY due_at ASC, id ASC",
            (int(terms["project_id"]),),
        )
        .fetchall()
    )
    return {"terms": terms, "deliverables": deliverables}

def add_deliverable(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    project_id = _int(body.get("project_id"))
    if not project_id:
        raise ValueError("project_id required")
    _project_context(project_id, staff, write=True)
    inserted = _sync_deliverables(project_id, [body], replace=False)
    get_conn().commit()
    item = inserted[0] if inserted else {}
    if item:
        audit.log_business_event(
            staff_id=_actor_id(staff),
            action_type="deliverable_add",
            target_type="deliverable",
            target_id=item.get("id"),
            detail=str(body.get("deliverable_type") or body.get("type") or "video"),
            metadata={"project_id": project_id},
        )
    return item

def update_deliverable(deliverable_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    item = _assert_deliverable_access(int(deliverable_id), staff, write=True)
    conn = get_conn()
    now = _utcnow()
    conn.execute(
        """
        UPDATE vkpi_project_deliverables
        SET deliverable_type=?, quantity=?, status=?, due_at=?, delivered_at=?,
            evidence_url=?, note=?, updated_at=?
        WHERE id=?
        """,
        (
            str(body.get("deliverable_type") or body.get("type") or item.get("deliverable_type") or "video"),
            _int(body.get("quantity"), _int(item.get("quantity"), 1)) or 1,
            str(body.get("status") or item.get("status") or "planned"),
            body.get("due_at", item.get("due_at")),
            body.get("delivered_at", item.get("delivered_at")),
            str(body.get("evidence_url") if body.get("evidence_url") is not None else item.get("evidence_url") or ""),
            str(body.get("note") if body.get("note") is not None else item.get("note") or ""),
            now,
            int(deliverable_id),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_project_deliverables WHERE id=?", (int(deliverable_id),)).fetchone()
    updated = _row(row)
    audit.log_business_event(
        staff_id=_actor_id(staff),
        action_type="deliverable_update",
        target_type="deliverable",
        target_id=deliverable_id,
        detail=str(updated.get("status") or ""),
        metadata={"project_id": updated.get("project_id")},
    )
    return updated
