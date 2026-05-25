"""Project list/create/status/delete operations for V-KPI workflow."""
from __future__ import annotations

import secrets
import logging
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import audit, scope
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.workflow_common import (
    PROJECT_STAGES,
    TERMINAL_STAGES,
    _int,
    _json,
    _loads,
    _validate_transition,
    normalize_stage,
    staff_id,
    utcnow,
)

logger = logging.getLogger(__name__)


def _log_project_audit(
    *,
    staff: dict[str, Any] | None,
    action_type: str,
    project_id: int,
    detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort business audit for project lifecycle actions."""
    try:
        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type=action_type,
            target_type="project",
            target_id=int(project_id),
            detail=detail,
            metadata=metadata or {},
        )
    except Exception as exc:  # pragma: no cover - audit must not block workflow actions
        logger.warning("vkpi.workflow_project_audit_failed", extra={"action_type": action_type, "project_id": project_id, "error": str(exc)})


def list_projects(limit: int = 50, stage: str = "", *, staff: dict[str, Any] | None = None, staff_id_filter: int | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    limit_i = max(1, min(200, int(limit or 50)))
    conn = get_conn()
    params: list[Any] = []
    where = "WHERE p.stage_status <> 'deleted'"
    if stage:
        where += " AND p.stage = ?"
        params.append(stage)
    scope_clause, scope_params = scope.project_filter("p", staff, staff_id_filter)
    if scope_clause:
        where += f" AND {scope_clause}"
        params.extend(scope_params)
    rows = conn.execute(
        f"""
        SELECT p.*,
               k.channel_name AS kol_name,
               k.platform AS kol_platform,
               s.name AS staff_name,
               (
                   SELECT MIN(e.effective_at)
                   FROM vkpi_project_stage_events e
                   WHERE e.project_id = p.id
               ) AS first_event_at,
               (
                   SELECT MAX(e.effective_at)
                   FROM vkpi_project_stage_events e
                   WHERE e.project_id = p.id AND e.to_stage = p.stage
               ) AS current_stage_started_at,
               (
                   SELECT COUNT(*)
                   FROM vkpi_project_stage_events e
                   WHERE e.project_id = p.id
               ) AS stage_event_count
        FROM vkpi_projects p
        LEFT JOIN kols k ON k.id = p.kol_id
        LEFT JOIN staff st ON st.id = p.assigned_staff_id
        LEFT JOIN users s ON s.id = st.user_id
        {where}
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT ?
        """,
        (*params, limit_i),
    ).fetchall()
    return {"projects": [dict(row) for row in rows], "scope": scope.scope_context(staff, staff_id_filter)}


def _normalize_project_products(body: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize selected products from UI without adding a new relation table yet."""
    products: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_product(sku: Any, name: Any = "") -> None:
        product_sku = str(sku or "").strip()
        product_name = str(name or "").strip()
        if not product_sku or product_sku in seen:
            return
        seen.add(product_sku)
        products.append({"product_sku": product_sku, "product_name": product_name})

    for item in body.get("products") or []:
        if not isinstance(item, dict):
            continue
        add_product(item.get("product_sku") or item.get("productSku") or item.get("sku"), item.get("product_name") or item.get("productName") or item.get("name"))

    for sku in body.get("product_skus") or body.get("productSkus") or []:
        add_product(sku)

    add_product(body.get("product_sku") or body.get("productSku"), body.get("product_name") or body.get("productName"))
    return products


def create_project(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    name = str(body.get("project_name") or body.get("name") or "").strip()
    if not name:
        raise ValueError("project_name required")
    stage = normalize_stage(str(body.get("stage") or "discovery"))
    if stage not in PROJECT_STAGES:
        raise ValueError("unsupported stage")
    actor_staff_id = staff_id(staff)
    assigned_staff_id = _int(body.get("assigned_staff_id"), actor_staff_id)
    if not scope.can_view_all(staff):
        assigned_staff_id = actor_staff_id
    now = utcnow()
    project_uid = str(body.get("project_uid") or f"VKPI-{secrets.token_hex(5).upper()}").strip()
    products = _normalize_project_products(body)
    primary_product = products[0] if products else {}
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    if products:
        metadata = {**metadata, "products": products, "product_skus": [item["product_sku"] for item in products]}
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_projects (
            project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
            product_sku, product_name, platform, marketplace, stage, stage_status,
            priority, source_type, shopify_discount_code, shopify_link, amazon_asin,
            amazon_attribution_link, amazon_associates_link, sample_status, tracking_number,
            target_post_date, due_at, started_at, last_activity_at, metadata_json,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            project_uid,
            name,
            _int(body.get("kol_id")) or None,
            assigned_staff_id or None,
            actor_staff_id or None,
            str(primary_product.get("product_sku") or body.get("product_sku") or ""),
            str(primary_product.get("product_name") or body.get("product_name") or ""),
            str(body.get("platform") or ""),
            str(body.get("marketplace") or ""),
            stage,
            "closed" if stage in TERMINAL_STAGES else str(body.get("stage_status") or "active"),
            str(body.get("priority") or "normal"),
            str(body.get("source_type") or "manual"),
            str(body.get("shopify_discount_code") or ""),
            str(body.get("shopify_link") or ""),
            str(body.get("amazon_asin") or ""),
            str(body.get("amazon_attribution_link") or ""),
            str(body.get("amazon_associates_link") or ""),
            str(body.get("sample_status") or "not_required"),
            str(body.get("tracking_number") or ""),
            body.get("target_post_date"),
            body.get("due_at"),
            now,
            now,
            _json(metadata),
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (project_uid,)).fetchone()
    project_id = int(row["id"]) if row else 0
    if project_id:
        conn.execute(
            """
            INSERT INTO vkpi_project_stage_events
                (project_id, from_stage, to_stage, event_type, actor_staff_id, note, effective_at, metadata_json, created_at)
            VALUES (?, '', ?, 'created', ?, ?, ?, '{}', ?)
            """,
            (project_id, stage, actor_staff_id or None, str(body.get("note") or ""), now, now),
        )
        if _int(body.get("kol_id")) and assigned_staff_id:
            existing_claim = conn.execute(
                "SELECT id FROM vkpi_kol_claims WHERE kol_id=? AND status='active' LIMIT 1",
                (_int(body.get("kol_id")),),
            ).fetchone()
            if not existing_claim:
                conn.execute(
                    """
                    INSERT INTO vkpi_kol_claims (
                        kol_id, staff_id, project_id, status, claimed_at, last_effective_touch_at,
                        metadata_json, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        _int(body.get("kol_id")),
                        assigned_staff_id,
                        project_id,
                        "active",
                        now,
                        now,
                        _json({"source": "project_create"}),
                        now,
                        now,
                    ),
                )
    conn.commit()
    if project_id:
        _log_project_audit(
            staff=staff,
            action_type="project_create",
            project_id=project_id,
            detail=name,
            metadata={
                "project_uid": project_uid,
                "stage": stage,
                "kol_id": _int(body.get("kol_id")) or None,
                "assigned_staff_id": assigned_staff_id or None,
                "product_skus": [item["product_sku"] for item in products],
                "source_type": str(body.get("source_type") or "manual"),
            },
        )
    return {"id": project_id, "project_uid": project_uid, "stage": stage}


def update_project(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Update editable project profile fields without touching stage transitions."""
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not row:
        raise LookupError("project not found")
    now = utcnow()
    updates: dict[str, Any] = {}

    field_map = {
        "project_name": ("project_name", "name", "projectName"),
        "product_sku": ("product_sku", "productSku"),
        "product_name": ("product_name", "productName"),
        "platform": ("platform",),
        "marketplace": ("marketplace",),
        "priority": ("priority",),
        "source_type": ("source_type", "sourceType"),
        "shopify_discount_code": ("shopify_discount_code", "shopifyDiscountCode"),
        "shopify_link": ("shopify_link", "shopifyLink", "shopify_url"),
        "amazon_asin": ("amazon_asin", "amazonAsin"),
        "amazon_attribution_link": ("amazon_attribution_link", "amazonAttributionLink"),
        "amazon_associates_link": ("amazon_associates_link", "amazonAssociatesLink"),
        "sample_status": ("sample_status", "sampleStatus"),
        "tracking_number": ("tracking_number", "trackingNumber"),
        "target_post_date": ("target_post_date", "targetPostDate"),
        "due_at": ("due_at", "dueAt"),
    }
    for column, keys in field_map.items():
        for key in keys:
            if key in body:
                updates[column] = str(body.get(key) or "").strip()
                break

    if "project_name" in updates and not updates["project_name"]:
        raise ValueError("project_name required")

    if "assigned_staff_id" in body or "assignedStaffId" in body:
        assigned_staff_id = _int(body.get("assigned_staff_id") or body.get("assignedStaffId"))
        if scope.can_view_all(staff):
            updates["assigned_staff_id"] = assigned_staff_id or None

    products = _normalize_project_products(body)
    metadata = _loads(row["metadata_json"])
    metadata_changed = False
    if products:
        primary_product = products[0]
        updates["product_sku"] = primary_product.get("product_sku") or updates.get("product_sku") or ""
        updates["product_name"] = primary_product.get("product_name") or updates.get("product_name") or ""
        metadata["products"] = products
        metadata["product_skus"] = [item["product_sku"] for item in products]
        metadata_changed = True
    if isinstance(body.get("metadata"), dict):
        metadata.update(body["metadata"])
        metadata_changed = True
    if metadata_changed:
        updates["metadata_json"] = _json(metadata)

    if not updates:
        return {"id": int(project_id), "status": "unchanged"}

    updates["updated_at"] = now
    assignments = ", ".join(f"{column}=?" for column in updates)
    conn.execute(
        f"UPDATE vkpi_projects SET {assignments} WHERE id=?",
        (*updates.values(), int(project_id)),
    )
    conn.commit()
    _log_project_audit(
        staff=staff,
        action_type="project_update",
        project_id=int(project_id),
        detail=str(updates.get("project_name") or row["project_name"] or "")[:240],
        metadata={
            "updated_fields": sorted(updates.keys()),
            "product_skus": [item["product_sku"] for item in products],
        },
    )
    return {"id": int(project_id), "status": "updated", "updated_fields": sorted(updates.keys())}

def transition_project(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    to_stage = normalize_stage(str(body.get("to_stage") or body.get("stage") or ""))
    if to_stage not in PROJECT_STAGES:
        raise ValueError("unsupported stage")
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not row:
        raise LookupError("project not found")
    now = utcnow()
    from_stage = str(row["stage"] or "")
    _validate_transition(from_stage, to_stage, body)
    actor_staff_id = staff_id(staff)
    stage_status = "closed" if to_stage in TERMINAL_STAGES else str(body.get("stage_status") or row["stage_status"] or "active")
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    tracking_number = str(body.get("tracking_number") or metadata.get("tracking_number") or row["tracking_number"] or "")
    sample_status = str(body.get("sample_status") or metadata.get("sample_status") or row["sample_status"] or "")
    if to_stage == "shipped":
        sample_status = sample_status or "shipped"
    closed_at = now if to_stage in TERMINAL_STAGES else row["closed_at"]
    conn.execute(
        """
        UPDATE vkpi_projects
        SET stage=?, stage_status=?, sample_status=?, tracking_number=?,
            closed_at=?, last_activity_at=?, updated_at=?
        WHERE id=?
        """,
        (to_stage, stage_status, sample_status, tracking_number, closed_at, now, now, int(project_id)),
    )
    conn.execute(
        """
        INSERT INTO vkpi_project_stage_events
            (project_id, from_stage, to_stage, event_type, actor_staff_id, note,
             source_ref_type, source_ref_id, effective_at, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            from_stage,
            to_stage,
            str(body.get("event_type") or "stage_change"),
            actor_staff_id or None,
            str(body.get("note") or ""),
            str(body.get("source_ref_type") or ""),
            str(body.get("source_ref_id") or ""),
            now,
            _json(body.get("metadata")),
            now,
        ),
    )
    auto_cost_result: dict[str, Any] | None = None
    if to_stage == "shipped":
        from app.domains import costs

        try:
            auto_cost_result = costs.record_shipped_product_cost(int(project_id), staff=staff)
        except Exception as exc:  # cost catalog issues must not block workflow progress
            auto_cost_result = {"status": "error", "reason": str(exc)}
    conn.commit()
    _log_project_audit(
        staff=staff,
        action_type="project_stage_transition",
        project_id=int(project_id),
        detail=f"{from_stage} -> {to_stage}",
        metadata={
            "from_stage": from_stage,
            "to_stage": to_stage,
            "stage_status": stage_status,
            "event_type": str(body.get("event_type") or "stage_change"),
            "sample_status": sample_status,
            "tracking_number": tracking_number,
            "source_ref_type": str(body.get("source_ref_type") or ""),
            "source_ref_id": str(body.get("source_ref_id") or ""),
            "auto_product_cost": auto_cost_result,
        },
    )
    return {"id": int(project_id), "from_stage": from_stage, "to_stage": to_stage, "auto_product_cost": auto_cost_result}

def delete_project(project_id: int, body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Soft-delete a project while preserving attribution, cost, and audit history."""
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    payload = body or {}
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not row:
        raise LookupError("project not found")
    project = dict(row)
    if str(project.get("stage_status") or "") == "deleted":
        return {"id": int(project_id), "status": "already_deleted"}
    now = utcnow()
    actor_staff_id = staff_id(staff)
    from_stage = str(project.get("stage") or "")
    reason = str(payload.get("reason") or payload.get("note") or "项目删除").strip()
    metadata = _loads(project.get("metadata_json"))
    metadata["deleted"] = {
        "deleted_at": now,
        "deleted_by_staff_id": actor_staff_id or None,
        "reason": reason,
        "previous_stage": from_stage,
        "previous_stage_status": str(project.get("stage_status") or ""),
    }
    conn.execute(
        """
        UPDATE vkpi_projects
        SET stage='cancelled', stage_status='deleted', closed_at=?, last_activity_at=?,
            metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (now, now, _json(metadata), now, int(project_id)),
    )
    conn.execute(
        """
        INSERT INTO vkpi_project_stage_events
            (project_id, from_stage, to_stage, event_type, actor_staff_id, note,
             source_ref_type, source_ref_id, effective_at, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            from_stage,
            "cancelled",
            "deleted",
            actor_staff_id or None,
            reason,
            "project",
            str(project_id),
            now,
            _json({"delete": True, "previous_stage_status": project.get("stage_status")}),
            now,
        ),
    )
    conn.execute(
        "UPDATE vkpi_links SET status='paused', updated_at=? WHERE project_id=? AND status='live'",
        (now, int(project_id)),
    )
    conn.commit()
    _log_project_audit(
        staff=staff,
        action_type="project_delete",
        project_id=int(project_id),
        detail=reason,
        metadata={
            "previous_stage": from_stage,
            "previous_stage_status": project.get("stage_status"),
            "reason": reason,
            "paused_live_links": True,
        },
    )
    return {"id": int(project_id), "status": "deleted", "previous_stage": from_stage}
