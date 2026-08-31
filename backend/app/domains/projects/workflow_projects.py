"""Project list/create/status/delete operations for V-KPI workflow."""
from __future__ import annotations

import secrets
import logging
from typing import Any

from app.db.connection import PostgresCompatConnection, get_conn
from app.domains.access import scope
from app.domains.projects import workflow_project_create
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.projects.workflow_common import (
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
from app.shared.project_creator_lifecycle_ports import (
    ClaimLifecyclePort,
    RecommendationFeedbackSink,
    SearchSessionDraftPort,
)

logger = logging.getLogger(__name__)

# Behavior-preserving re-exports: ProjectCard stage constants + enrichment moved
# to workflow_projects_cards.py (function bodies unchanged → behavior unchanged).
from app.domains.projects.workflow_projects_cards import (  # noqa: E402
    ASSIGNMENT_STAGE_TO_CARD_STAGE,
    CARD_STAGE_INDEX,
    CARD_STAGE_LABELS,
    PROJECT_CARD_STAGE_KEYS,
    _clamp,
    _empty_stage_counts,
    _enrich_project_card_fields,
)

# Audit / staff-name / pool-occupancy helpers moved to workflow_projects_occupancy.py.
from app.domains.projects.workflow_projects_occupancy import (  # noqa: E402
    _current_user_id,
    _log_project_audit,
    _pool_claim_occupancy,
    _staff_display_names,
)


def _lock_owned_search_session_for_draft(
    conn: Any,
    *,
    session_id: int,
    owner_id: int,
) -> None:
    """Serialize one owner's draft creation without breaking SQLite tooling.

    Production connections are PostgreSQL compatibility wrappers, so the row
    lock is held until ``create_project`` commits on the same request-scoped
    connection.  SQLite and lightweight test connections use the same
    owner-scoped existence check without unsupported ``FOR UPDATE`` syntax.
    """
    sql = """
        SELECT id
        FROM vkpi_kol_search_sessions
        WHERE id=? AND created_by=?
    """
    if isinstance(conn, PostgresCompatConnection):
        sql += " FOR UPDATE"
    locked = conn.execute(sql, (int(session_id), int(owner_id))).fetchone()
    if not locked:
        # Preserve the public cross-employee contract: another employee's id
        # is indistinguishable from a missing session.
        raise LookupError(f"search session not found: {session_id}")


def list_projects(limit: int = 50, stage: str = "", *, staff: dict[str, Any] | None = None, staff_id_filter: int | None = None, starred_only: bool = False) -> dict[str, Any]:
    ensure_vkpi_schema()
    limit_i = max(1, min(200, int(limit or 50)))
    conn = get_conn()
    params: list[Any] = []
    current_user_id = _current_user_id(staff)
    # 项目看板必须是项目事实的统一入口。来源只用于溯源,不能把 cockpit UI、Launch、
    # manual 或 smart-search 已成功写入的项目从列表中静默隐藏。可见范围仍由下方
    # project_filter 统一收口,因此去掉来源白名单不会扩大员工的项目权限。
    where = "WHERE p.stage_status <> 'deleted'"
    if starred_only:
        where += " AND EXISTS (SELECT 1 FROM vkpi_project_stars ps_filter WHERE ps_filter.project_id = p.id AND ps_filter.user_id = ?)"
        params.append(current_user_id)
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
               CASE
                   WHEN COALESCE(pa.kol_count, 0) > 1 THEN CAST(pa.kol_count AS TEXT) || ' KOL'
                   ELSE COALESCE(pk.display_name, '')
               END AS kol_name,
               CASE
                   WHEN COALESCE(pa.kol_count, 0) > 1 THEN 'multi'
                   ELSE COALESCE(pk.platform, '')
               END AS kol_platform,
               pk.handle AS handle,
               pk.avatar_url AS kol_avatar,
               pa.primary_kol_pool_id AS kol_pool_id,
               COALESCE(pa.kol_count, 0) AS kol_count,
               COALESCE(pa.kol_with_evidence, 0) AS kol_with_evidence,
               COALESCE(ev.evidence_count, 0) AS evidence_count,
               COALESCE(ev.evidence_kol_count, 0) AS evidence_kol_count,
               ev.latest_publish_date AS latest_publish_date,
               COALESCE(ev.total_views, 0) AS total_views,
               COALESCE(s.name, assignment_owner.name) AS staff_name,
               CASE WHEN ps.user_id IS NULL THEN FALSE ELSE TRUE END AS is_starred,
               ps.created_at AS starred_at,
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
        LEFT JOIN (
            SELECT
                a.project_id,
                COUNT(DISTINCT a.kol_pool_id) AS kol_count,
                COUNT(DISTINCT CASE WHEN kp.has_video_evidence THEN a.kol_pool_id END) AS kol_with_evidence,
                MIN(a.kol_pool_id) AS primary_kol_pool_id
            FROM vkpi_project_kol_assignments a
            LEFT JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
            GROUP BY a.project_id
        ) pa ON pa.project_id = p.id
        LEFT JOIN (
            SELECT project_id, assigned_staff_id
            FROM (
                SELECT
                    project_id,
                    assigned_staff_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY project_id
                        ORDER BY COUNT(*) DESC, assigned_staff_id ASC
                    ) AS rn
                FROM vkpi_project_kol_assignments
                WHERE assigned_staff_id IS NOT NULL
                GROUP BY project_id, assigned_staff_id
            ) ranked_assignment_staff
            WHERE rn = 1
        ) assignment_owner_pick ON assignment_owner_pick.project_id = p.id
        LEFT JOIN vkpi_kol_pool pk ON pk.id = pa.primary_kol_pool_id
        LEFT JOIN (
            SELECT
                project_id,
                COUNT(*) AS evidence_count,
                COUNT(DISTINCT kol_pool_id) AS evidence_kol_count,
                MAX(publish_date) AS latest_publish_date,
                COALESCE(SUM(COALESCE(view_count, 0)), 0) AS total_views
            FROM vkpi_kol_video_evidence
            WHERE project_id IS NOT NULL
            GROUP BY project_id
        ) ev ON ev.project_id = p.id
        LEFT JOIN staff st ON st.id = p.assigned_staff_id
        LEFT JOIN users s ON s.id = st.user_id
        LEFT JOIN staff assignment_st ON assignment_st.id = assignment_owner_pick.assigned_staff_id
        LEFT JOIN users assignment_owner ON assignment_owner.id = assignment_st.user_id
        LEFT JOIN vkpi_project_stars ps ON ps.project_id = p.id AND ps.user_id = ?
        {where}
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT ?
        """,
        (current_user_id, *params, limit_i),
    ).fetchall()
    projects = [dict(row) for row in rows]
    _enrich_project_card_fields(conn, projects)
    return {"projects": projects, "scope": scope.scope_context(staff, staff_id_filter)}


def set_project_star(project_id: int, starred: bool, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=False)
    user_id = _current_user_id(staff)
    if not user_id:
        raise ValueError("user_id required")
    conn = get_conn()
    if starred:
        conn.execute(
            """
            INSERT INTO vkpi_project_stars (user_id, project_id)
            VALUES (?, ?)
            ON CONFLICT (user_id, project_id) DO NOTHING
            """,
            (user_id, int(project_id)),
        )
    else:
        conn.execute("DELETE FROM vkpi_project_stars WHERE user_id=? AND project_id=?", (user_id, int(project_id)))
    conn.commit()
    return {"project_id": int(project_id), "is_starred": bool(starred)}


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
    prepared = workflow_project_create.prepare_project(
        body,
        staff,
        normalize_stage=normalize_stage,
        project_stages=PROJECT_STAGES,
        staff_id=staff_id,
        to_int=_int,
        can_view_all=scope.can_view_all,
        utcnow=utcnow,
        token_hex=secrets.token_hex,
        normalize_products=_normalize_project_products,
    )
    conn = get_conn()
    project_id = workflow_project_create.persist_project(
        conn,
        body,
        prepared,
        terminal_stages=TERMINAL_STAGES,
        to_int=_int,
        json_dump=_json,
    )
    if project_id:
        _log_project_audit(
            staff=staff,
            action_type="project_create",
            project_id=project_id,
            detail=prepared["name"],
            metadata={
                "project_uid": prepared["project_uid"],
                "stage": prepared["stage"],
                "kol_id": _int(body.get("kol_id")) or None,
                "assigned_staff_id": prepared["assigned_staff_id"] or None,
                "product_skus": [
                    item["product_sku"] for item in prepared["products"]
                ],
                "source_type": str(body.get("source_type") or "manual"),
            },
        )
    return {
        "id": project_id,
        "project_uid": prepared["project_uid"],
        "project_name": prepared["name"],
        "stage": prepared["stage"],
    }


def create_project_draft_from_session(
    session_id: int,
    body: dict[str, Any] | None = None,
    *,
    staff: dict[str, Any] | None = None,
    search_session_port: SearchSessionDraftPort | None = None,
    feedback_sink: RecommendationFeedbackSink | None = None,
) -> dict[str, Any]:
    """Create or reuse a smart-search project draft from approved session KOLs."""
    if search_session_port is None:
        raise RuntimeError(
            "SearchSessionDraftPort is required at the composition boundary"
        )
    from app.domains.projects import cost_estimate as cost_estimate_engine
    from app.domains.projects.workflow_project_draft import (
        DraftOperations,
        create_project_draft_from_session as create_draft,
    )

    return create_draft(
        int(session_id),
        body,
        staff=staff,
        ops=DraftOperations(
            get_session=search_session_port.get_session,
            update_session_result_summary=search_session_port.update_result_summary,
            to_int=_int,
            staff_id=staff_id,
            loads=_loads,
            get_conn=get_conn,
            lock_owned_session=_lock_owned_search_session_for_draft,
            create_project=create_project,
            add_project_kols=lambda project_id, payload, **kwargs: add_project_kols(
                project_id,
                payload,
                feedback_sink=feedback_sink,
                **kwargs,
            ),
            estimate_cost_for_kols=cost_estimate_engine.estimate_cost_for_kols,
            warning=logger.warning,
        ),
    )


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
        "follow_status": ("follow_status", "followStatus"),
    }
    for column, keys in field_map.items():
        for key in keys:
            if key in body:
                updates[column] = str(body.get(key) or "").strip()
                break

    if "project_name" in updates and not updates["project_name"]:
        raise ValueError("project_name required")
    if "follow_status" in updates and updates["follow_status"] not in {"active", "paused"}:
        raise ValueError("follow_status must be active or paused")

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

    # P2:note 是契约里的死字段(vkpi_projects 无对应列)。收到时落一条业务 audit,
    # 不再静默丢弃;不阻塞主更新流程(_log_project_audit 自身 best-effort)。
    note = str(body.get("note") or "").strip()
    if note:
        _log_project_audit(
            staff=staff,
            action_type="project_update_note",
            project_id=int(project_id),
            detail=note[:240],
            metadata={"note": note[:2000]},
        )

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
    result = {"id": int(project_id), "status": "updated", "updated_fields": sorted(updates.keys())}
    if "follow_status" in updates:
        result["follow_status"] = updates["follow_status"]
    return result


# KOL availability + attach operations moved to workflow_projects_kols.py
# (function bodies unchanged → behavior unchanged). create_project_draft_from_session
# above calls add_project_kols via this re-exported name.
from app.domains.projects.workflow_projects_kols import (  # noqa: E402
    add_project_kols,
    list_available_project_kols,
)


def transition_project(
    project_id: int,
    body: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    claim_lifecycle: ClaimLifecyclePort | None = None,
) -> dict[str, Any]:
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
    # 乙案④(释放双轨之自动轨):项目推进入终态时,认领人=assignment 负责人的 active claim
    # 自动释放(claims.auto_release_claims_for_project 内部 audit 留痕);失败不阻塞推进。
    claim_auto_release: dict[str, Any] | None = None
    if to_stage in TERMINAL_STAGES:
        try:
            if claim_lifecycle is None:
                raise RuntimeError(
                    "ClaimLifecyclePort is required at the composition boundary"
                )
            claim_auto_release = dict(claim_lifecycle.auto_release_for_project(
                int(project_id), to_stage=to_stage, actor_staff_id=actor_staff_id
            ))
        except Exception as exc:  # claim release must not block workflow progress
            logger.warning("vkpi.claim_auto_release_failed", extra={"project_id": project_id, "error": str(exc)})
            claim_auto_release = {"status": "error", "reason": str(exc)}
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
            "claim_auto_release": claim_auto_release,
        },
    )
    return {
        "id": int(project_id),
        "from_stage": from_stage,
        "to_stage": to_stage,
        "auto_product_cost": auto_cost_result,
        "claim_auto_release": claim_auto_release,
    }

def delete_project(
    project_id: int,
    body: dict[str, Any] | None = None,
    *,
    staff: dict[str, Any] | None = None,
    claim_lifecycle: ClaimLifecyclePort | None = None,
) -> dict[str, Any]:
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
    # 乙案④:软删=进 cancelled 终态,同样触发认领自动释放(失败不阻塞删除)。
    claim_auto_release: dict[str, Any] | None = None
    try:
        if claim_lifecycle is None:
            raise RuntimeError(
                "ClaimLifecyclePort is required at the composition boundary"
            )
        claim_auto_release = dict(claim_lifecycle.auto_release_for_project(
            int(project_id), to_stage="cancelled", actor_staff_id=actor_staff_id
        ))
    except Exception as exc:
        logger.warning("vkpi.claim_auto_release_failed", extra={"project_id": project_id, "error": str(exc)})
        claim_auto_release = {"status": "error", "reason": str(exc)}
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
            "claim_auto_release": claim_auto_release,
        },
    )
    return {"id": int(project_id), "status": "deleted", "previous_stage": from_stage, "claim_auto_release": claim_auto_release}
