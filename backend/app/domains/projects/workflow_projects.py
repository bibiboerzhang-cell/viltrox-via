"""Project list/create/status/delete operations for V-KPI workflow."""
from __future__ import annotations

import secrets
import logging
from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope
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

logger = logging.getLogger(__name__)

PROJECT_CARD_STAGE_KEYS = [
    "discovery",
    "contacted",
    "replied",
    "agreed",
    "shipped",
    "received",
    "published",
    "measured",
    "closed",
]

ASSIGNMENT_STAGE_TO_CARD_STAGE = {
    "discovered": "discovery",
    "contacted": "contacted",
    "replied": "replied",
    "agreed": "agreed",
    "device_sent": "shipped",
    "arrived": "received",
    "content_posted": "published",
    "reviewed": "measured",
    "closed": "closed",
    "churned": "closed",
    # 双词表案读侧兼容:历史误写的项目词表行照常入桶(写侧已归一,此为存量保底)
    "shipped": "shipped",
    "received": "received",
    "published": "published",
    "measured": "measured",
}

CARD_STAGE_LABELS = {
    "discovered": "1.发现",
    "contacted": "2.已联系",
    "replied": "3.已回复",
    "agreed": "4.已合作",
    "device_sent": "5.已发货",
    "arrived": "6.已到货",
    "content_posted": "7.已发布",
    "reviewed": "8.已统计",
    "closed": "9.已关闭",
    "churned": "9.已关闭",
}

CARD_STAGE_INDEX = {key: index for index, key in enumerate(PROJECT_CARD_STAGE_KEYS)}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _empty_stage_counts() -> dict[str, int]:
    return {key: 0 for key in PROJECT_CARD_STAGE_KEYS}


def _enrich_project_card_fields(conn, projects: list[dict[str, Any]]) -> None:
    """Attach deliverable ProjectCard fields from assignment/kol_pool truth."""
    project_ids = [int(project.get("id") or 0) for project in projects if int(project.get("id") or 0)]
    if not project_ids:
        return
    placeholders = ",".join("?" for _ in project_ids)
    rows = conn.execute(
        f"""
        SELECT
            a.project_id,
            COALESCE(a.stage, '') AS stage,
            COALESCE(kp.platform, '') AS platform,
            COALESCE(kp.has_video_evidence, FALSE) AS has_video_evidence,
            COUNT(DISTINCT a.kol_pool_id) AS kol_count
        FROM vkpi_project_kol_assignments a
        LEFT JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
        WHERE a.project_id IN ({placeholders})
        GROUP BY a.project_id, COALESCE(a.stage, ''), COALESCE(kp.platform, ''), COALESCE(kp.has_video_evidence, FALSE)
        """,
        project_ids,
    ).fetchall()
    breakdowns: dict[int, dict[str, Any]] = {
        project_id: {
            "platforms": set(),
            "stage_counts": _empty_stage_counts(),
            "raw_stage_counts": {},
            "kol_count": 0,
            "kol_with_evidence": 0,
            "published_count": 0,
            "churned_count": 0,
            "stage_index_sum": 0,
            "stage_index_count": 0,
        }
        for project_id in project_ids
    }
    for row in rows:
        project_id = int(row["project_id"] or 0)
        data = breakdowns.get(project_id)
        if not data:
            continue
        count = int(row["kol_count"] or 0)
        raw_stage = str(row["stage"] or "").strip().lower()
        platform = str(row["platform"] or "").strip().lower()
        if platform:
            data["platforms"].add(platform)
        data["kol_count"] += count
        if bool(row["has_video_evidence"]):
            data["kol_with_evidence"] += count
        if raw_stage in ("content_posted", "published"):
            data["published_count"] += count
        if raw_stage == "churned":
            data["churned_count"] += count
        data["raw_stage_counts"][raw_stage] = data["raw_stage_counts"].get(raw_stage, 0) + count
        card_stage = ASSIGNMENT_STAGE_TO_CARD_STAGE.get(raw_stage)
        if card_stage:
            data["stage_counts"][card_stage] += count
            if raw_stage != "churned":
                data["stage_index_sum"] += CARD_STAGE_INDEX.get(card_stage, 0) * count
                data["stage_index_count"] += count

    for project in projects:
        project_id = int(project.get("id") or 0)
        data = breakdowns.get(project_id)
        if not data:
            project.update({
                "platforms": [],
                "stage_counts": _empty_stage_counts(),
                "published_count": 0,
                "health_score": 0,
                "health_basis": "simplified_no_reliable_time",
                "health_breakdown": {"output_score": 0, "progress_score": 0, "output_rate": 0, "avg_stage_index": 0},
                "needs_followup_count": None,
                "overdue_count": None,
                "current_focus": "暂无 KOL",
                "bottleneck": "暂无 KOL",
                "churned_count": 0,
            })
            continue
        kol_count = int(data["kol_count"] or 0)
        output_rate = (int(data["kol_with_evidence"] or 0) / kol_count) if kol_count else 0
        avg_stage_index = (float(data["stage_index_sum"]) / int(data["stage_index_count"])) if int(data["stage_index_count"] or 0) else 0
        output_score = output_rate * 60
        progress_score = (avg_stage_index / 8) * 40
        health_score = int(round(_clamp(output_score + progress_score, 0, 100)))
        bottleneck_stage = ""
        bottleneck_count = 0
        for stage, count in data["raw_stage_counts"].items():
            if stage in {"content_posted", "churned", "reviewed", "closed"}:
                continue
            if int(count or 0) > bottleneck_count:
                bottleneck_stage = stage
                bottleneck_count = int(count or 0)
        bottleneck = f"{CARD_STAGE_LABELS.get(bottleneck_stage, bottleneck_stage or '当前阶段')} {bottleneck_count}人" if bottleneck_count else "暂无瓶颈"
        project.update({
            "platforms": sorted(data["platforms"]),
            "stage_counts": data["stage_counts"],
            "published_count": int(data["published_count"] or 0),
            "health_score": health_score,
            "health_basis": "simplified_no_reliable_time",
            "health_breakdown": {
                "output_score": round(output_score, 1),
                "progress_score": round(progress_score, 1),
                "output_rate": round(output_rate, 4),
                "avg_stage_index": round(avg_stage_index, 2),
            },
            "needs_followup_count": None,
            "overdue_count": None,
            "current_focus": bottleneck,
            "bottleneck": bottleneck,
            "churned_count": int(data["churned_count"] or 0),
        })


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
        from app.domains import audit

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


def _current_user_id(staff: dict[str, Any] | None) -> int:
    return int((staff or {}).get("user_id") or 0)


def _staff_display_names(conn, staff_ids: list[int] | set[int]) -> dict[int, str]:
    """staff.id -> 真人姓名(users.name),查无名时回退「员工#id」。"""
    ids = sorted({int(value) for value in staff_ids if _int(value)})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT st.id AS staff_id, u.name AS user_name
        FROM staff st
        LEFT JOIN users u ON u.id = st.user_id
        WHERE st.id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    names: dict[int, str] = {}
    for row in rows:
        data = dict(row)
        sid = _int(data.get("staff_id"))
        if sid:
            names[sid] = str(data.get("user_name") or "").strip() or f"员工#{sid}"
    for sid in ids:
        names.setdefault(sid, f"员工#{sid}")
    return names


def _pool_claim_occupancy(conn, kol_pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    """乙案(项目维独占)事实独占源,选择器置灰与写入防绕过共用同一口径:

    1. active claim(vkpi_kol_claims,经 vkpi_kol_pool.linked_main_kol_id join,
       预研实测命中≈0 但为真实认领表,优先级最高);
    2. 在役 assignment 负责人(stage/stage_status 非终态、项目非 cancelled/deleted/
       restricted)——与 pool_favorites.list_favorites 的在役口径同源,另排除 closed。
    返回 {kol_pool_id: {staff_id, source('claim'|'assignment'), claim_id, project_id, stage}}。
    """
    ids = sorted({_int(value) for value in kol_pool_ids if _int(value)})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    occupancy: dict[int, dict[str, Any]] = {}
    assignment_rows = conn.execute(
        f"""
        SELECT a.kol_pool_id, a.assigned_staff_id, a.project_id, COALESCE(a.stage, '') AS stage
        FROM vkpi_project_kol_assignments a
        JOIN vkpi_projects pr ON pr.id = a.project_id
        WHERE a.kol_pool_id IN ({placeholders})
          AND a.assigned_staff_id IS NOT NULL
          AND COALESCE(a.stage, '') NOT IN ('churned', 'cancelled', 'lost', 'closed')
          AND COALESCE(a.stage_status, '') NOT IN ('lost', 'cancelled', 'released', 'deleted')
          AND pr.stage <> 'cancelled'
          AND COALESCE(pr.stage_status, '') <> 'deleted'
          AND COALESCE(pr.restricted, FALSE) = FALSE
        ORDER BY a.updated_at DESC, a.id DESC
        """,
        ids,
    ).fetchall()
    for row in assignment_rows:
        data = dict(row)
        pool_id = _int(data.get("kol_pool_id"))
        owner_staff_id = _int(data.get("assigned_staff_id"))
        if not pool_id or not owner_staff_id or pool_id in occupancy:
            continue
        occupancy[pool_id] = {
            "staff_id": owner_staff_id,
            "source": "assignment",
            "claim_id": None,
            "project_id": _int(data.get("project_id")) or None,
            "stage": str(data.get("stage") or ""),
        }
    claim_rows = conn.execute(
        f"""
        SELECT p.id AS kol_pool_id, c.id AS claim_id, c.staff_id, c.project_id
        FROM vkpi_kol_pool p
        JOIN vkpi_kol_claims c ON c.kol_id = p.linked_main_kol_id AND c.status = 'active'
        WHERE p.id IN ({placeholders})
        ORDER BY c.id ASC
        """,
        ids,
    ).fetchall()
    for row in claim_rows:
        data = dict(row)
        pool_id = _int(data.get("kol_pool_id"))
        owner_staff_id = _int(data.get("staff_id"))
        if not pool_id or not owner_staff_id:
            continue
        occupancy[pool_id] = {
            "staff_id": owner_staff_id,
            "source": "claim",
            "claim_id": _int(data.get("claim_id")) or None,
            "project_id": _int(data.get("project_id")) or None,
            "stage": "",
        }
    return occupancy


def list_projects(limit: int = 50, stage: str = "", *, staff: dict[str, Any] | None = None, staff_id_filter: int | None = None, starred_only: bool = False) -> dict[str, Any]:
    ensure_vkpi_schema()
    limit_i = max(1, min(200, int(limit or 50)))
    conn = get_conn()
    params: list[Any] = []
    current_user_id = _current_user_id(staff)
    where = "WHERE p.stage_status <> 'deleted' AND COALESCE(p.source_type, '') = 'excel_promo_plan'"
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


def list_available_project_kols(
    project_id: int,
    *,
    query: str = "",
    limit: int = 200,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return kol_pool rows not yet assigned to this project."""
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff)
    limit_i = max(1, min(500, int(limit or 200)))
    conn = get_conn()
    filters = [
        """
        NOT EXISTS (
            SELECT 1
            FROM vkpi_project_kol_assignments a
            WHERE a.project_id = ? AND a.kol_pool_id = p.id
        )
        """
    ]
    params: list[Any] = [int(project_id)]
    search = str(query or "").strip().lower()
    if search:
        # P2:LIKE 字面语义——转义用户输入中的 \ % _ 并显式 ESCAPE,防通配符注入/全表慢扫。
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        filters.append(
            """
            (
                LOWER(COALESCE(p.display_name, '')) LIKE ? ESCAPE '\\'
                OR LOWER(COALESCE(p.handle, '')) LIKE ? ESCAPE '\\'
                OR LOWER(COALESCE(p.platform, '')) LIKE ? ESCAPE '\\'
            )
            """
        )
        params.extend([like, like, like])
    rows = conn.execute(
        f"""
        SELECT
            p.id,
            p.handle,
            p.display_name,
            p.display_name AS channel_name,
            p.platform,
            p.profile_url,
            p.avatar_url,
            p.followers,
            p.followers AS follower_count,
            p.dashboard_account_type,
            p.dashboard_tier,
            p.has_video_evidence,
            p.video_evidence_count
        FROM vkpi_kol_pool p
        WHERE {' AND '.join(filters)}
        ORDER BY COALESCE(p.followers, 0) DESC, LOWER(COALESCE(p.display_name, p.handle, '')) ASC
        LIMIT ?
        """,
        (*params, limit_i),
    ).fetchall()
    items = [dict(row) for row in rows]
    # 乙案①(选择器置灰):候选行补 claim/在役占用字段。claim_staff_id/claim_staff_name/
    # active_claim_id 仅在「被他人占用」时下发(前端 buildKolOptions 据此映射 claimStaffId/
    # claimOwner/activeClaimId 灰显「已被 X 跟进」);本人占用与裸数据走 occupied_* 诚实字段。
    occupancy = _pool_claim_occupancy(conn, [_int(item.get("id")) for item in items])
    owner_names = _staff_display_names(conn, [occ["staff_id"] for occ in occupancy.values()])
    actor_staff_id = staff_id(staff)
    for item in items:
        occ = occupancy.get(_int(item.get("id")))
        owner_staff_id = _int(occ.get("staff_id")) if occ else 0
        owner_name = owner_names.get(owner_staff_id, "") if owner_staff_id else ""
        claimed_by_other = bool(owner_staff_id and owner_staff_id != actor_staff_id)
        item["occupied_by_staff_id"] = owner_staff_id or None
        item["occupied_by_name"] = owner_name
        item["claim_source"] = str(occ.get("source") or "") if occ else ""
        item["claimed_by_other"] = claimed_by_other
        if claimed_by_other:
            item["active_claim_id"] = occ.get("claim_id")
            item["claim_staff_id"] = owner_staff_id
            item["claim_staff_name"] = owner_name
    return {"kols": items, "project_id": int(project_id)}


def add_project_kols(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach existing kol_pool rows to a project as discovered assignments."""
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    ids = body.get("kol_pool_ids") or body.get("kolPoolIds") or body.get("kol_ids") or body.get("kolIds") or []
    if not isinstance(ids, list):
        raise ValueError("kol_pool_ids must be a list")
    kol_pool_ids = []
    seen: set[int] = set()
    for value in ids:
        kol_pool_id = _int(value)
        if kol_pool_id and kol_pool_id not in seen:
            seen.add(kol_pool_id)
            kol_pool_ids.append(kol_pool_id)
    if not kol_pool_ids:
        raise ValueError("kol_pool_ids required")
    conn = get_conn()
    existing_pool_ids = {
        int(row["id"])
        for row in conn.execute(
            f"SELECT id FROM vkpi_kol_pool WHERE id IN ({','.join('?' for _ in kol_pool_ids)})",
            kol_pool_ids,
        ).fetchall()
    }
    missing = [kol_pool_id for kol_pool_id in kol_pool_ids if kol_pool_id not in existing_pool_ids]
    actor_staff_id = staff_id(staff)
    requested_staff_id = _int(body.get("assigned_staff_id") or body.get("assignedStaffId"))
    assigned_staff_id = requested_staff_id if scope.can_view_all(staff) and requested_staff_id else actor_staff_id
    # 乙案②(写入侧防绕过):与选择器同口径校验——被他人认领/在役跟进的 KOL 拒绝写入,
    # ValueError 带占用人姓名(路由已映 400);admin 可 body.force=true 强制,audit 留痕。
    occupancy = _pool_claim_occupancy(conn, sorted(existing_pool_ids))
    blocked = {
        pool_id: occ
        for pool_id, occ in occupancy.items()
        if _int(occ.get("staff_id")) and _int(occ.get("staff_id")) != actor_staff_id
    }
    force = bool(body.get("force"))
    forced_conflicts: list[dict[str, Any]] = []
    if blocked:
        owner_names = _staff_display_names(conn, [occ["staff_id"] for occ in blocked.values()])
        if force and scope.can_view_all(staff):
            forced_conflicts = [
                {
                    "kol_pool_id": pool_id,
                    "occupied_by_staff_id": _int(occ.get("staff_id")),
                    "occupied_by_name": owner_names.get(_int(occ.get("staff_id")), ""),
                    "claim_source": str(occ.get("source") or ""),
                    "claim_id": occ.get("claim_id"),
                    "occupied_project_id": occ.get("project_id"),
                }
                for pool_id, occ in sorted(blocked.items())
            ]
        else:
            blocked_ids = sorted(blocked)
            placeholders = ",".join("?" for _ in blocked_ids)
            label_rows = conn.execute(
                f"SELECT id, COALESCE(display_name, handle, '') AS label FROM vkpi_kol_pool WHERE id IN ({placeholders})",
                blocked_ids,
            ).fetchall()
            labels = {_int(dict(row).get("id")): str(dict(row).get("label") or "").strip() for row in label_rows}
            parts = []
            for pool_id, occ in sorted(blocked.items()):
                kol_label = labels.get(pool_id) or f"KOL #{pool_id}"
                owner_staff_id = _int(occ.get("staff_id"))
                owner_label = owner_names.get(owner_staff_id) or f"员工#{owner_staff_id}"
                parts.append(f"「{kol_label}」已被 {owner_label} 跟进")
            raise ValueError(
                "以下 KOL 已被他人认领/跟进,未写入:" + ";".join(parts) + "。如确需加入,请管理员携 force=true 重试。"
            )
    now = utcnow()
    inserted = 0
    skipped_existing = 0
    for kol_pool_id in kol_pool_ids:
        if kol_pool_id not in existing_pool_ids:
            continue
        row = conn.execute(
            """
            INSERT INTO vkpi_project_kol_assignments (
                project_id, kol_pool_id, stage, stage_status, assigned_staff_id,
                source, source_ref, metadata_json, created_at, updated_at
            ) VALUES (?, ?, 'discovered', 'active', ?, 'manual', ?, ?, ?, ?)
            ON CONFLICT (project_id, kol_pool_id) DO NOTHING
            RETURNING id
            """,
            (
                int(project_id),
                int(kol_pool_id),
                assigned_staff_id or None,
                f"ui:add_kol:{project_id}",
                _json({"source": "project_detail_add_kol", "actor_staff_id": actor_staff_id}),
                now,
                now,
            ),
        ).fetchone()
        if row:
            inserted += 1
        else:
            skipped_existing += 1
    if inserted:
        conn.execute("UPDATE vkpi_projects SET updated_at=?, last_activity_at=? WHERE id=?", (now, now, int(project_id)))
    conn.commit()
    if inserted:
        _log_project_audit(
            staff=staff,
            action_type="project_add_kols",
            project_id=int(project_id),
            detail=f"added {inserted} KOL assignments" + (f" (admin force, {len(forced_conflicts)} 个越过他人占用)" if forced_conflicts else ""),
            metadata={
                "kol_pool_ids": [kol_pool_id for kol_pool_id in kol_pool_ids if kol_pool_id in existing_pool_ids],
                "assigned_staff_id": assigned_staff_id or None,
                "missing_kol_pool_ids": missing,
                "skipped_existing": skipped_existing,
                "force": bool(forced_conflicts),
                "forced_claim_conflicts": forced_conflicts,
            },
        )
    return {
        "project_id": int(project_id),
        "requested": len(kol_pool_ids),
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "missing_kol_pool_ids": missing,
        "forced_claim_conflicts": forced_conflicts,
    }


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
    # 乙案④(释放双轨之自动轨):项目推进入终态时,认领人=assignment 负责人的 active claim
    # 自动释放(claims.auto_release_claims_for_project 内部 audit 留痕);失败不阻塞推进。
    claim_auto_release: dict[str, Any] | None = None
    if to_stage in TERMINAL_STAGES:
        try:
            from app.domains.kol import claims as kol_claims

            claim_auto_release = kol_claims.auto_release_claims_for_project(
                int(project_id), to_stage=to_stage, actor_staff_id=actor_staff_id
            )
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
    # 乙案④:软删=进 cancelled 终态,同样触发认领自动释放(失败不阻塞删除)。
    claim_auto_release: dict[str, Any] | None = None
    try:
        from app.domains.kol import claims as kol_claims

        claim_auto_release = kol_claims.auto_release_claims_for_project(
            int(project_id), to_stage="cancelled", actor_staff_id=actor_staff_id
        )
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
