"""Server-owned shipment identity and approval checks; no provider operations."""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import PostgresCompatConnection

DISPATCHED = frozenset({
    "shipped", "device_sent", "in_transit", "intransit", "delivered",
    "received", "arrived", "content_posted", "content_published", "reviewed",
})


def _error(reason: str) -> None:
    from app.domains.projects.shipment_approval import ShipmentNotApproved

    raise ShipmentNotApproved(f"shipment_approval_required:{reason}")


def _id(value: Any) -> int:
    if isinstance(value, bool):
        _error("invalid_identity")
    try:
        result = int(str(value or "0"))
    except (TypeError, ValueError):
        _error("invalid_identity")
    if result < 0:
        _error("invalid_identity")
    return result if result > 0 else 0


def lock_row(conn: Any, table: str, row_id: int) -> dict[str, Any]:
    """All shipment writers lock their existing owner row before read-modify-write."""
    if table not in {"vkpi_projects", "vkpi_project_kol_assignments", "vkpi_shipments"}:
        raise ValueError("unsupported shipment owner")
    suffix = " FOR NO KEY UPDATE" if isinstance(conn, PostgresCompatConnection) else ""
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?{suffix}", (int(row_id),)).fetchone()
    if row is None:
        raise LookupError("shipment owner not found")
    return dict(row)


def lock_assignment(conn: Any, assignment_id: int, project_id: int) -> dict[str, Any]:
    row = lock_row(conn, "vkpi_project_kol_assignments", assignment_id)
    if _id(row.get("project_id")) != _id(project_id):
        _error("assignment_identity_mismatch")
    return row


def shipment_assignment_column(conn: Any) -> bool:
    """Inspect metadata without a failed SQL probe that can roll back held locks."""
    if isinstance(conn, PostgresCompatConnection):
        row = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('vkpi_shipments') "
            "AND attname='assignment_id' AND attnum>0 AND NOT attisdropped) AS present"
        ).fetchone()
        return bool(row and row["present"])
    return any(row["name"] == "assignment_id" for row in conn.execute("PRAGMA table_info(vkpi_shipments)").fetchall())


def _pool_subject(conn: Any, project: dict[str, Any], pool_id: int, assignment_id: int) -> dict[str, Any]:
    suffix = " FOR SHARE" if isinstance(conn, PostgresCompatConnection) else ""
    pool = conn.execute(
        f"SELECT id, linked_main_kol_id FROM vkpi_kol_pool WHERE id=?{suffix}", (pool_id,),
    ).fetchone()
    if pool is None:
        _error("creator_missing")
    linked_id = _id(dict(pool).get("linked_main_kol_id"))
    assignment = conn.execute(
        "SELECT id FROM vkpi_project_kol_assignments WHERE project_id=? AND kol_pool_id=?"
        + (" AND id=?" if assignment_id else "") + " ORDER BY id LIMIT 1" + suffix,
        (project["id"], pool_id, assignment_id) if assignment_id else (project["id"], pool_id),
    ).fetchone()
    if assignment_id and assignment is None:
        _error("assignment_identity_mismatch")
    if assignment is None and (not linked_id or linked_id != _id(project.get("kol_id"))):
        _error("creator_not_in_project")
    return {
        "project_id": int(project["id"]), "kol_pool_id": pool_id,
        "kol_id": linked_id or None,
        "assignment_id": int(assignment["id"]) if assignment else None,
    }


def resolve_subject(conn: Any, project_id: int, *, kol_pool_id: Any = None, assignment_id: Any = None) -> dict[str, Any]:
    """Never confuse a main KOL id with a pool id, including equal-number collisions."""
    pid, kid, aid = _id(project_id), _id(kol_pool_id), _id(assignment_id)
    if not pid:
        _error("project_identity_missing")
    project = lock_row(conn, "vkpi_projects", pid)
    if aid:
        assignment = lock_row(conn, "vkpi_project_kol_assignments", aid)
        if int(assignment["project_id"]) != pid or (kid and kid != _id(assignment.get("kol_pool_id"))):
            _error("assignment_identity_mismatch")
        kid = _id(assignment.get("kol_pool_id"))
    if not kid:
        main_id = _id(project.get("kol_id"))
        if main_id:
            rows = conn.execute(
                "SELECT id FROM vkpi_kol_pool WHERE linked_main_kol_id=? ORDER BY id", (main_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT kol_pool_id AS id FROM vkpi_project_kol_assignments WHERE project_id=?",
                (pid,),
            ).fetchall()
        ids = {_id(row["id"]) for row in rows} - {0}
        if len(ids) != 1:
            _error("creator_identity_missing_or_ambiguous")
        kid = ids.pop()
    return _pool_subject(conn, project, kid, aid)


def assert_new_dispatch(conn: Any, project_id: int, *, staff: Any = None, kol_pool_id: Any = None, assignment_id: Any = None) -> dict[str, Any]:
    from app.domains.projects import shipment_approval

    try:
        subject = resolve_subject(conn, project_id, kol_pool_id=kol_pool_id, assignment_id=assignment_id)
        shipment_approval.assert_shippable(project_id, subject["kol_pool_id"], staff=staff, conn=conn)
        return subject
    except shipment_approval.ShipmentNotApproved:
        raise
    except Exception as exc:
        raise shipment_approval.ShipmentNotApproved("shipment_approval_required:verification_unavailable") from exc


def _dispatched(row: dict[str, Any]) -> bool:
    return any(str(row.get(key) or "").strip().lower() in DISPATCHED for key in ("stage", "status", "sample_status"))


def needs_dispatch_approval(before: dict[str, Any], changes: dict[str, Any]) -> bool:
    """Only an existing server-owned, non-placeholder, same tracking is an update."""
    after = {**before, **changes}
    old_tracking = str(before.get("tracking_number") or "").strip()
    tracking = str(after.get("tracking_number") or "").strip()
    if tracking and tracking != old_tracking:
        return True
    changed = any(after.get(key) != before.get(key) for key in ("stage", "status", "sample_status", "shipped_at"))
    declaring = changed and (_dispatched(after) or bool(after.get("shipped_at")))
    if not declaring:
        return False
    same_existing = old_tracking and tracking == old_tracking and _dispatched(before) and not before.get("is_placeholder_tracking")
    return not bool(same_existing)


def guard_project_change(conn: Any, project: dict[str, Any], changes: dict[str, Any], *, staff: Any = None) -> None:
    if needs_dispatch_approval(project, changes):
        assert_new_dispatch(conn, int(project["id"]), staff=staff)


def guard_assignment_change(conn: Any, assignment: dict[str, Any], changes: dict[str, Any], *, staff: Any = None) -> None:
    if needs_dispatch_approval(assignment, changes):
        assert_new_dispatch(conn, int(assignment["project_id"]), assignment_id=assignment["id"], staff=staff)


def guard_shipment_change(conn: Any, shipment: dict[str, Any], changes: dict[str, Any], *, staff: Any = None) -> None:
    if not needs_dispatch_approval(shipment, changes):
        return
    raw = shipment.get("metadata_json") or {}
    try:
        meta = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        meta = {}
    meta = meta if isinstance(meta, dict) else {}
    assert_new_dispatch(
        conn, shipment["project_id"], staff=staff,
        assignment_id=shipment.get("assignment_id") or meta.get("assignment_id"),
        kol_pool_id=meta.get("kol_pool_id"),
    )


def reject_dispatched_creation(body: dict[str, Any]) -> None:
    if _dispatched(body) or str(body.get("tracking_number") or "").strip() or body.get("shipped_at"):
        _error("create_draft_before_shipping")


def preserve_subject_metadata(before: dict[str, Any], incoming: Any) -> dict[str, Any]:
    raw = before.get("metadata_json") or {}
    try:
        saved = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    protected = {"project_id", "kol_pool_id", "kol_id", "assignment_id"}
    merged = {**saved, **(incoming if isinstance(incoming, dict) else {})}
    for key in protected:
        merged.pop(key, None)
        if key in saved:
            merged[key] = saved[key]
    return merged


def existing_shipment(conn: Any, project_id: int, tracking: str, subject: dict[str, Any]) -> dict[str, Any] | None:
    """An existing tracking may be reused, never reassigned to another creator."""
    if not tracking:
        return None
    rows = conn.execute(
        "SELECT sh.*, sa.kol_id AS sample_kol_id, sa.product_sku, sa.product_name, "
        "sa.serial_number, sa.sample_cost_cents FROM vkpi_shipments sh "
        "LEFT JOIN vkpi_sample_assets sa ON sa.id=sh.sample_asset_id "
        "WHERE sh.project_id=? AND sh.tracking_number=? ORDER BY sh.id",
        (project_id, tracking),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        _error("tracking_identity_ambiguous")
    row = dict(rows[0])
    meta = preserve_subject_metadata(row, {})
    aid, kid = row.get("assignment_id") or meta.get("assignment_id"), meta.get("kol_pool_id")
    identifiers = ((aid, subject.get("assignment_id")), (kid, subject.get("kol_pool_id")), (row.get("sample_kol_id"), subject.get("kol_id")))
    comparisons = [str(left) == str(right) for left, right in identifiers if left and right]
    if not comparisons or not all(comparisons):
        _error("tracking_identity_conflict")
    return row
