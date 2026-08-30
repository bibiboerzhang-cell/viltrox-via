"""Staff workspace and KPI aggregate services for V-KPI."""
from __future__ import annotations

from typing import Any

from app.core.staff_avatars import serialize_staff_avatar_url
from app.db.connection import get_conn
from app.domains import business_truth
from app.domains.access import scope
from app.domains.dashboard.decision_dashboard import _day_bucket
from app.domains.staff.decision_staff_kpi import build_staff_kpi as _build_staff_kpi
from app.shared.vkpi_decision_common import (
    _KPI_LABELS,
    _active_project_filter,
    _parse_json,
    _safe_rows,
    _staff_kpi_breakdown,
    _summary,
    _window_start,
)
from app.platform.db.schema import ensure_vkpi_schema


def staff_directory() -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    rows = _safe_rows(
        conn,
        """
        SELECT st.id AS staff_id,
               st.user_id,
               COALESCE(u.name, u.email, 'Staff') AS staff_name,
               COALESCE(u.email, '') AS email,
               COALESCE(u.creator_code, '') AS employee_code,
               COALESCE(u.avatar_url, '') AS avatar_url,
               COALESCE(st.role, 'readonly') AS role,
               COALESCE(st.active, 1) AS active,
               COALESCE(st.permissions_json, '{}') AS permissions_json,
               st.invited_at,
               st.accepted_at,
               st.last_active_at
        FROM staff st
        LEFT JOIN users u ON u.id = st.user_id
        ORDER BY active DESC, staff_name ASC, staff_id ASC
        """,
    )
    if not rows:
        rows = _safe_rows(
            conn,
            """
            SELECT u.id AS staff_id,
                   u.id AS user_id,
                   COALESCE(u.name, u.email, 'Staff') AS staff_name,
                   COALESCE(u.email, '') AS email,
                   COALESCE(u.creator_code, '') AS employee_code,
                   COALESCE(u.avatar_url, '') AS avatar_url,
                   COALESCE(u.role, 'readonly') AS role,
                   1 AS active,
                   '{}' AS permissions_json,
                   u.created_at AS invited_at,
                   NULL AS accepted_at,
                   u.last_login AS last_active_at
            FROM users u
            WHERE LOWER(COALESCE(u.role, '')) IN ('admin', 'ops', 'operations', 'analyst', 'readonly')
            ORDER BY staff_name ASC, staff_id ASC
            """,
        )
    normalized = []
    for row in rows:
        email = str(row.get("email") or "")
        code = str(row.get("employee_code") or "").strip()
        if not code:
            code = (email.split("@")[0] if email else f"staff-{row.get('staff_id')}").strip()
        normalized.append(
            {
                **row,
                "employee_code": code,
                "avatar_url": serialize_staff_avatar_url(row.get("avatar_url")),
            }
        )
    return {"staff": normalized}


def _merge_metric(target: dict[int, dict[str, Any]], row: dict[str, Any], key: str, source_key: str = "value") -> None:
    staff = int(row.get("staff_id") or 0)
    if not staff:
        return
    target.setdefault(staff, {
        "staff_id": staff,
        "staff_name": f"Staff {staff}",
        "employee_code": f"staff-{staff}",
        "kol_claims": 0,
        "projects": 0,
        "active_projects": 0,
        "contacted": 0,
        "replied": 0,
        "agreed": 0,
        "shipped": 0,
        "published": 0,
        "measured": 0,
        "links_created": 0,
        "valid_clicks": 0,
        "bot_clicks": 0,
        "content_views": 0,
        "content_likes": 0,
        "gmv_cents": None,
        "cost_cents": None,
        "net_contribution_cents": None,
        "roi": None,
        "net_roi": None,
        "gmv_data_status": "awaiting_source",
        "cost_data_status": "awaiting_source",
        "net_contribution_data_status": "awaiting_source",
        "roi_data_status": "awaiting_source",
        "financial_data_status": "awaiting_source",
    })[key] = int(row.get(source_key) or 0)


def staff_kpi(window: str = "month", staff_id: int | None = None) -> dict[str, Any]:
    return _build_staff_kpi(
        window,
        staff_id,
        ensure_schema=ensure_vkpi_schema,
        window_start=_window_start,
        get_connection=get_conn,
        staff_directory=staff_directory,
        safe_rows=_safe_rows,
        merge_metric=_merge_metric,
        day_bucket=_day_bucket,
        active_project_filter=_active_project_filter,
        verified_attribution_sql=business_truth.verified_shopify_attribution_sql,
        approved_cost_sql=business_truth.approved_actual_cost_sql,
        current_kpi_ledger_sql=business_truth.current_kpi_ledger_sql,
    )


def employee_workspace(staff_id: int) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    effective_staff_id = int(staff_id or 0)
    if not effective_staff_id:
        return {"staff_id": 0, "summary": {}, "projects": [], "claims": [], "tasks": []}
    member = next((row for row in staff_directory().get("staff", []) if int(row.get("staff_id") or 0) == effective_staff_id), {})
    month_kpi = staff_kpi("month", staff_id=effective_staff_id)
    projects = _safe_rows(
        conn,
        f"""
        SELECT p.*, k.channel_name AS kol_name, k.platform AS kol_platform
        FROM vkpi_projects p
        LEFT JOIN kols k ON k.id = p.kol_id
        WHERE p.assigned_staff_id=?
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT 80
        """,
        (effective_staff_id,),
    )
    claims = _safe_rows(
        conn,
        """
        SELECT c.*, k.channel_name AS kol_name, k.platform
        FROM vkpi_kol_claims c
        LEFT JOIN kols k ON k.id = c.kol_id
        WHERE c.staff_id=? AND c.status='active'
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT 80
        """,
        (effective_staff_id,),
    )
    tasks = []
    for project in projects:
        stage = str(project.get("stage") or "")
        if stage in {"claimed", "contacted", "replied", "agreed", "sample_preparing", "shipped", "received", "content_due", "published"}:
            tasks.append({
                "type": "next_step",
                "project_id": project.get("id"),
                "project_name": project.get("project_name"),
                "stage": stage,
                "title": f"推进 {project.get('project_name') or 'project'}",
                "body": f"当前阶段 {stage}，需要记录下一步动作或更新时间线。",
            })
    return {
        "staff_id": effective_staff_id,
        "staff": member,
        "summary": (month_kpi.get("rows") or [{}])[0] if month_kpi.get("rows") else {},
        "projects": projects,
        "claims": claims,
        "tasks": tasks[:20],
        "watermark": member.get("employee_code") or member.get("email") or f"staff-{effective_staff_id}",
    }


def staff_profile(staff_id: int, *, staff: dict[str, Any] | None = None, window: str = "month", limit: int = 80) -> dict[str, Any]:
    """Return one employee's V-KPI operating profile with real source rows.

    Manager/finance users can view costs. Operators can only view their own
    profile and never receive internal cost rows from this endpoint.
    """
    ensure_vkpi_schema()
    target_staff_id = int(staff_id or scope.actor_staff_id(staff) or 0)
    if not target_staff_id:
        raise ValueError("staff_id required")
    scope.assert_staff_access(target_staff_id, staff)

    conn = get_conn()
    limit = max(1, min(300, int(limit or 80)))
    member = next((row for row in staff_directory().get("staff", []) if int(row.get("staff_id") or 0) == target_staff_id), {})
    costs_visible = scope.can_view_all(staff, domain="cost")
    audit_visible = scope.can_view_all(staff)
    summary_rows = staff_kpi(window, staff_id=target_staff_id).get("rows") or []
    summary = summary_rows[0] if summary_rows else {
        "staff_id": target_staff_id,
        "gmv_cents": 0,
        "cost_cents": 0 if costs_visible else None,
        "projects": 0,
        "kol_claims": 0,
        "workload_score": 0,
    }

    projects = _safe_rows(
        conn,
        """
        SELECT p.*, k.channel_name AS kol_name, k.platform AS kol_platform, k.avatar_url AS kol_avatar,
               COALESCE(u.name, u.email, '') AS staff_name
        FROM vkpi_projects p
        LEFT JOIN kols k ON k.id = p.kol_id
        LEFT JOIN staff st ON st.id = p.assigned_staff_id OR st.user_id = p.assigned_staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE p.assigned_staff_id=? OR p.created_by_staff_id=?
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT ?
        """,
        (target_staff_id, target_staff_id, limit),
    )
    claims = _safe_rows(
        conn,
        """
        SELECT c.*, k.channel_name AS kol_name, k.channel_url, k.platform, k.avatar_url,
               k.follower_count, k.avg_views, k.contact_email
        FROM vkpi_kol_claims c
        LEFT JOIN kols k ON k.id = c.kol_id
        WHERE c.staff_id=?
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT ?
        """,
        (target_staff_id, limit),
    )
    links = _safe_rows(
        conn,
        """
        SELECT l.*, p.project_name, k.channel_name AS kol_name
        FROM vkpi_links l
        LEFT JOIN vkpi_projects p ON p.id = l.project_id
        LEFT JOIN kols k ON k.id = l.kol_id
        WHERE l.staff_id=? OR l.created_by_staff_id=?
        ORDER BY l.updated_at DESC, l.id DESC
        LIMIT ?
        """,
        (target_staff_id, target_staff_id, limit),
    )
    attributions = _safe_rows(
        conn,
        f"""
        SELECT sa.*, p.project_name, k.channel_name AS kol_name,
               os.order_name, os.order_number, os.financial_status,
               CASE WHEN {business_truth.verified_shopify_attribution_sql('sa')}
                    THEN 1 ELSE 0 END AS is_verified_business_truth
        FROM vkpi_sales_attributions sa
        LEFT JOIN vkpi_projects p ON p.id = sa.project_id
        LEFT JOIN kols k ON k.id = sa.kol_id
        LEFT JOIN vkpi_shopify_order_snapshots os ON os.id = sa.shopify_order_snapshot_id
        WHERE sa.staff_id=? AND {_active_project_filter('sa')}
        ORDER BY COALESCE(sa.occurred_at, sa.imported_at, sa.created_at) DESC, sa.id DESC
        LIMIT ?
        """,
        (target_staff_id, limit),
    )
    costs = _safe_rows(
        conn,
        f"""
        SELECT c.*, p.project_name, k.channel_name AS kol_name,
               CASE WHEN {business_truth.approved_actual_cost_sql('c')}
                    THEN 1 ELSE 0 END AS is_approved_actual
        FROM vkpi_cost_ledger c
        LEFT JOIN vkpi_projects p ON p.id = c.project_id
        LEFT JOIN kols k ON k.id = c.kol_id
        WHERE c.staff_id=? AND c.status!='void' AND {_active_project_filter('c')}
        ORDER BY c.incurred_at DESC, c.id DESC
        LIMIT ?
        """,
        (target_staff_id, limit),
    ) if costs_visible else []
    kpi_ledger = _safe_rows(
        conn,
        f"""
        SELECT kl.*, p.project_name, k.channel_name AS kol_name
        FROM vkpi_kpi_ledger kl
        LEFT JOIN vkpi_projects p ON p.id = kl.project_id
        LEFT JOIN kols k ON k.id = kl.kol_id
        WHERE kl.staff_id=?
          AND {business_truth.current_kpi_ledger_sql('kl')}
        ORDER BY kl.ledger_date DESC, kl.id DESC
        LIMIT ?
        """,
        (target_staff_id, limit),
    )
    for row in kpi_ledger:
        key = str(row.get("metric_key") or "")
        row["metric_label"] = _KPI_LABELS.get(key, key)
        row["metadata"] = _parse_json(row.get("metadata_json"))
    kpi_breakdown = _staff_kpi_breakdown(
        conn,
        target_staff_id,
        start=_window_start(window),
        limit=limit,
    )
    channels = _safe_rows(
        conn,
        """
        SELECT id, platform, account_handle, account_display_name, account_url, avatar_url,
               status, last_sync_status AS sync_status, last_sync_status, last_sync_at,
               self_reported_followers AS latest_followers,
               self_reported_posts AS latest_posts,
               0 AS latest_views,
               updated_at
        FROM vkpi_employee_channels
        WHERE staff_id=? AND deleted_at IS NULL
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (target_staff_id, limit),
    )
    audit_events = _safe_rows(
        conn,
        """
        SELECT id, action_type, target_type, target_id, detail, created_at, metadata_json
        FROM vkpi_business_audit_logs
        WHERE staff_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (target_staff_id, 50),
    ) if audit_visible else []

    for row in attributions:
        row["business_truth_status"] = (
            "provider_verified" if int(row.get("is_verified_business_truth") or 0) == 1 else "reference_only"
        )
    for row in costs:
        row["business_truth_status"] = (
            "approved_actual"
            if int(row.get("is_approved_actual") or 0) == 1
            else "reference_only"
        )
    verified_attributions = [
        row for row in attributions if int(row.get("is_verified_business_truth") or 0) == 1
    ]
    approved_costs = [
        row
        for row in costs
        if int(row.get("is_approved_actual") or 0) == 1
    ]
    total_revenue = sum(int(row.get("revenue_cents") or 0) for row in verified_attributions)
    total_cost = sum(int(row.get("amount_cents") or 0) for row in approved_costs) if costs_visible else None
    summary = {
        **summary,
        "project_count": len(projects),
        "claim_count": len(claims),
        "link_count": len(links),
        "attribution_count": len(attributions),
        "verified_attribution_count": len(verified_attributions),
        "cost_count": len(costs) if costs_visible else None,
        "approved_cost_count": len(approved_costs) if costs_visible else None,
        "channel_count": len(channels),
        "kpi_entry_count": len(kpi_ledger),
        "kpi_source_count": int(kpi_breakdown.get("source_count") or 0),
        "recommendation_kpi_source_count": len(kpi_breakdown.get("recommendation_source_rows") or []),
        "profile_revenue_cents": total_revenue,
        "profile_cost_cents": total_cost,
        "financials_hidden": not costs_visible,
    }
    return {
        "staff": member or {"staff_id": target_staff_id},
        "summary": summary,
        "projects": projects,
        "claims": claims,
        "links": links,
        "attributions": attributions,
        "costs": costs,
        "kpi_ledger": kpi_ledger,
        "kpi_breakdown": kpi_breakdown,
        "channels": channels,
        "audit_events": audit_events,
        "visibility": {
            "costs_visible": costs_visible,
            "audit_visible": audit_visible,
            "scope": "all" if scope.can_view_all(staff) else "self",
        },
    }
