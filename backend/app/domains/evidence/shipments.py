"""Shipment and sample operations for V-KPI evidence resources."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains import audit
from app.services.vkpi import scope, workflow
from app.domains.evidence.common import (
    _actor_id,
    _assert_kol_access,
    _assert_shipment_access,
    _int,
    _json,
    _project_context,
    _row,
    _rows,
    _utcnow,
)
from app.services.vkpi.schema import ensure_vkpi_schema

def list_shipments(
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
        where.append("sh.project_id = ?")
        params.append(int(project_id))
    if kol_id:
        _assert_kol_access(int(kol_id), staff)
        where.append("p.kol_id = ?")
        params.append(int(kol_id))
    scoped_staff = scope.effective_staff_id(staff, staff_id, domain="cost")
    if scoped_staff:
        where.append("(p.assigned_staff_id = ? OR p.created_by_staff_id = ?)")
        params.extend([scoped_staff, scoped_staff])
    sql = """
        SELECT sh.*, sa.kol_id, sa.product_sku, sa.product_name, sa.serial_number,
               sa.sample_cost_cents, sa.return_required, sa.status AS sample_status,
               p.project_name, k.channel_name AS kol_name, u.name AS staff_name
        FROM vkpi_shipments sh
        LEFT JOIN vkpi_sample_assets sa ON sa.id = sh.sample_asset_id
        LEFT JOIN vkpi_projects p ON p.id = sh.project_id
        LEFT JOIN kols k ON k.id = COALESCE(sa.kol_id, p.kol_id)
        LEFT JOIN staff s ON s.id = p.assigned_staff_id
        LEFT JOIN users u ON u.id = s.user_id
    """
    if where:
        sql += " WHERE " + " AND ".join(f"({item})" for item in where)
    sql += " ORDER BY sh.created_at DESC, sh.id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit or 100))))
    rows = _rows(conn.execute(sql, params).fetchall())
    if not scope.can_view_all(staff, domain="cost"):
        for row in rows:
            row["sample_cost_cents"] = None
    return {"shipments": rows, "count": len(rows)}

def create_shipment(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    project_id = _int(body.get("project_id"))
    if not project_id:
        raise ValueError("project_id required")
    result = workflow.add_project_shipment(project_id, body, staff=staff)
    audit.log_business_event(
        staff_id=_actor_id(staff),
        action_type="shipment_add",
        target_type="shipment",
        target_id=result.get("id"),
        detail=str(result.get("tracking_number") or body.get("tracking_number") or ""),
        metadata={"project_id": project_id, "sample_asset_id": result.get("sample_asset_id")},
    )
    if not scope.can_view_all(staff, domain="cost"):
        result["sample_cost_cents"] = None
    return result

def get_shipment(shipment_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    item = _assert_shipment_access(int(shipment_id), staff)
    sample = _row(get_conn().execute("SELECT * FROM vkpi_sample_assets WHERE id=?", (_int(item.get("sample_asset_id")),)).fetchone()) if item.get("sample_asset_id") else {}
    if sample and not scope.can_view_all(staff, domain="cost"):
        sample["sample_cost_cents"] = None
    return {"shipment": item, "sample": sample}

def update_shipment(shipment_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    item = _assert_shipment_access(int(shipment_id), staff, write=True)
    conn = get_conn()
    now = _utcnow()
    conn.execute(
        """
        UPDATE vkpi_shipments
        SET carrier=?, tracking_number=?, status=?, shipping_cost_cents=?,
            currency=?, shipped_at=?, delivered_at=?, evidence_url=?, note=?,
            metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (
            str(body.get("carrier") if body.get("carrier") is not None else item.get("carrier") or ""),
            str(body.get("tracking_number") if body.get("tracking_number") is not None else item.get("tracking_number") or ""),
            str(body.get("status") or body.get("shipping_status") or item.get("status") or "planned"),
            _int(body.get("shipping_cost_cents"), _int(item.get("shipping_cost_cents"))) if body.get("shipping_cost_usd") is None and body.get("shipping_cost") is None else workflow._amount_cents(body.get("shipping_cost_usd", body.get("shipping_cost", 0))),
            str(body.get("currency") or item.get("currency") or "USD"),
            body.get("shipped_at", item.get("shipped_at")),
            body.get("delivered_at", item.get("delivered_at")),
            str(body.get("evidence_url") if body.get("evidence_url") is not None else item.get("evidence_url") or ""),
            str(body.get("note") if body.get("note") is not None else item.get("note") or ""),
            _json(body.get("metadata")),
            now,
            int(shipment_id),
        ),
    )
    if item.get("sample_asset_id"):
        sample_status = str(body.get("sample_status") or body.get("status") or "")
        if sample_status:
            conn.execute(
                "UPDATE vkpi_sample_assets SET status=?, received_at=COALESCE(?, received_at), updated_at=? WHERE id=?",
                (sample_status, body.get("delivered_at") or body.get("received_at"), now, _int(item.get("sample_asset_id"))),
            )
    conn.commit()
    updated = _assert_shipment_access(int(shipment_id), staff)
    audit.log_business_event(
        staff_id=_actor_id(staff),
        action_type="shipment_update",
        target_type="shipment",
        target_id=shipment_id,
        detail=str(updated.get("status") or ""),
        metadata={"project_id": updated.get("project_id"), "sample_asset_id": updated.get("sample_asset_id")},
    )
    return updated

def mark_shipment_received(shipment_id: int, body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(body or {})
    payload.setdefault("status", "delivered")
    payload.setdefault("sample_status", "received")
    payload.setdefault("delivered_at", _utcnow())
    return update_shipment(shipment_id, payload, staff=staff)

def list_samples(
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
        where.append("sa.project_id = ?")
        params.append(int(project_id))
    if kol_id:
        _assert_kol_access(int(kol_id), staff)
        where.append("sa.kol_id = ?")
        params.append(int(kol_id))
    scoped_staff = scope.effective_staff_id(staff, staff_id, domain="cost")
    if scoped_staff:
        where.append("(p.assigned_staff_id = ? OR p.created_by_staff_id = ?)")
        params.extend([scoped_staff, scoped_staff])
    sql = """
        SELECT sa.*, p.project_name, k.channel_name AS kol_name, u.name AS staff_name
        FROM vkpi_sample_assets sa
        LEFT JOIN vkpi_projects p ON p.id = sa.project_id
        LEFT JOIN kols k ON k.id = sa.kol_id
        LEFT JOIN staff s ON s.id = p.assigned_staff_id
        LEFT JOIN users u ON u.id = s.user_id
    """
    if where:
        sql += " WHERE " + " AND ".join(f"({item})" for item in where)
    sql += " ORDER BY sa.created_at DESC, sa.id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit or 100))))
    rows = _rows(conn.execute(sql, params).fetchall())
    if not scope.can_view_all(staff, domain="cost"):
        for row in rows:
            row["sample_cost_cents"] = None
    return {"samples": rows, "count": len(rows)}
