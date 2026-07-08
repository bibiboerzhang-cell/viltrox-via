"""Staff workspace and KPI aggregate services for V-KPI."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.dashboard.decision_dashboard import _day_bucket
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
        normalized.append({**row, "employee_code": code})
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
        "gmv_cents": 0,
        "cost_cents": 0,
    })[key] = int(row.get(source_key) or 0)


def staff_kpi(window: str = "month", staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    start = _window_start(window)
    conn = get_conn()
    base_staff = staff_directory().get("staff", [])
    rows_by_staff: dict[int, dict[str, Any]] = {}
    for member in base_staff:
        sid = int(member.get("staff_id") or 0)
        if staff_id and sid != int(staff_id):
            continue
        if sid:
            rows_by_staff[sid] = {
                "staff_id": sid,
                "staff_name": member.get("staff_name") or member.get("email") or f"Staff {sid}",
                "email": member.get("email") or "",
                "employee_code": member.get("employee_code") or "",
                "avatar_url": member.get("avatar_url") or "",
                "role": member.get("role") or "readonly",
                "active": int(member.get("active") or 0),
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
                "gmv_cents": 0,
                "cost_cents": 0,
                "net_contribution_cents": 0,
                "roi": 0,
                "ledger_workload_score": 0,
                "kpi_credit": 0,
                "recommendation_source_rows": 0,
                "recommendation_projects": 0,
                "recommendation_published": 0,
                "recommendation_orders": 0,
                "recommendation_clicks": 0,
                "recommendation_gmv_cents": 0,
                "recommendation_cost_cents": 0,
            }

    staff_filter = " AND staff_id=?" if staff_id else ""
    params = (start, int(staff_id)) if staff_id else (start,)
    for row in _safe_rows(conn, f"SELECT staff_id, COUNT(*) AS value FROM vkpi_kol_claims WHERE created_at >= ?{staff_filter} GROUP BY staff_id", params):
        _merge_metric(rows_by_staff, row, "kol_claims")
    for row in _safe_rows(conn, f"SELECT assigned_staff_id AS staff_id, COUNT(*) AS value FROM vkpi_projects WHERE created_at >= ?{' AND assigned_staff_id=?' if staff_id else ''} GROUP BY assigned_staff_id", params):
        _merge_metric(rows_by_staff, row, "projects")
    for row in _safe_rows(conn, f"SELECT assigned_staff_id AS staff_id, COUNT(*) AS value FROM vkpi_projects WHERE stage_status='active' AND updated_at >= ?{' AND assigned_staff_id=?' if staff_id else ''} GROUP BY assigned_staff_id", params):
        _merge_metric(rows_by_staff, row, "active_projects")
    for row in _safe_rows(
        conn,
        f"""
        SELECT actor_staff_id AS staff_id, to_stage, COUNT(*) AS value
        FROM vkpi_project_stage_events
        WHERE effective_at >= ?{' AND actor_staff_id=?' if staff_id else ''}
        GROUP BY actor_staff_id, to_stage
        """,
        params,
    ):
        stage = str(row.get("to_stage") or "").strip().lower()
        if stage in {"contacted", "replied", "agreed", "shipped", "published", "measured"}:
            _merge_metric(rows_by_staff, row, stage)
    for row in _safe_rows(conn, f"SELECT staff_id, COUNT(*) AS value FROM vkpi_links WHERE created_at >= ?{staff_filter} GROUP BY staff_id", params):
        _merge_metric(rows_by_staff, row, "links_created")
    for row in _safe_rows(
        conn,
        f"""
        SELECT l.staff_id,
               COALESCE(SUM(CASE WHEN COALESCE(c.is_bot, 0) = 0 THEN 1 ELSE 0 END), 0) AS valid_clicks,
               COALESCE(SUM(CASE WHEN COALESCE(c.is_bot, 0) = 1 THEN 1 ELSE 0 END), 0) AS bot_clicks
        FROM vkpi_link_clicks c
        INNER JOIN vkpi_links l ON l.id = c.link_id
        WHERE c.clicked_at >= ?{' AND l.staff_id=?' if staff_id else ''}
        GROUP BY l.staff_id
        """,
        params,
    ):
        _merge_metric(rows_by_staff, row, "valid_clicks", "valid_clicks")
        _merge_metric(rows_by_staff, row, "bot_clicks", "bot_clicks")
    for row in _safe_rows(
        conn,
        f"""
        SELECT p.assigned_staff_id AS staff_id,
               COALESCE(SUM(kp.views), 0) AS content_views,
               COALESCE(SUM(kp.likes), 0) AS content_likes
        FROM vkpi_projects p
        INNER JOIN kol_posts kp ON kp.kol_id = p.kol_id
        WHERE kp.created_at >= ?{' AND p.assigned_staff_id=?' if staff_id else ''}
        GROUP BY p.assigned_staff_id
        """,
        params,
    ):
        _merge_metric(rows_by_staff, row, "content_views", "content_views")
        _merge_metric(rows_by_staff, row, "content_likes", "content_likes")
    for row in _safe_rows(
        conn,
        f"""
        SELECT sa.staff_id, COALESCE(SUM(sa.revenue_cents), 0) AS value
        FROM vkpi_sales_attributions sa
        WHERE {_day_bucket('sa.occurred_at', 'sa.imported_at', 'sa.created_at')} >= ?{staff_filter.replace('staff_id', 'sa.staff_id')}
          AND {_active_project_filter('sa')}
        GROUP BY sa.staff_id
        """,
        params,
    ):
        _merge_metric(rows_by_staff, row, "gmv_cents")
    for row in _safe_rows(
        conn,
        f"""
        SELECT c.staff_id, COALESCE(SUM(c.amount_cents), 0) AS value
        FROM vkpi_cost_ledger c
        WHERE c.status!='void' AND c.incurred_at >= ?{staff_filter.replace('staff_id', 'c.staff_id')}
          AND {_active_project_filter('c')}
        GROUP BY c.staff_id
        """,
        params,
    ):
        _merge_metric(rows_by_staff, row, "cost_cents")

    ledger_staff_filter = " AND staff_id=?" if staff_id else ""
    ledger_params = (start, int(staff_id)) if staff_id else (start,)
    for row in _safe_rows(
        conn,
        f"""
        SELECT staff_id,
               COALESCE(SUM(CASE WHEN metric_key='workload_score' THEN metric_value ELSE 0 END), 0) AS ledger_workload_score,
               COALESCE(SUM(CASE WHEN metric_key='kpi_credit' THEN metric_value ELSE 0 END), 0) AS kpi_credit,
               COALESCE(SUM(CASE WHEN substr(metric_key, 1, 15)='recommendation_' THEN 1 ELSE 0 END), 0) AS recommendation_source_rows,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_project_created' THEN metric_value ELSE 0 END), 0) AS recommendation_projects,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_content_published' THEN metric_value ELSE 0 END), 0) AS recommendation_published,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_order_attributed' THEN metric_value ELSE 0 END), 0) AS recommendation_orders,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_clicks' THEN metric_value ELSE 0 END), 0) AS recommendation_clicks,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_gmv_cents' THEN metric_value ELSE 0 END), 0) AS recommendation_gmv_cents,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_cost_cents' THEN metric_value ELSE 0 END), 0) AS recommendation_cost_cents
        FROM vkpi_kpi_ledger
        WHERE ledger_date >= ?{ledger_staff_filter}
        GROUP BY staff_id
        """,
        ledger_params,
    ):
        sid = int(row.get("staff_id") or 0)
        if sid not in rows_by_staff:
            continue
        target = rows_by_staff[sid]
        for key in (
            "ledger_workload_score",
            "kpi_credit",
            "recommendation_source_rows",
            "recommendation_projects",
            "recommendation_published",
            "recommendation_orders",
            "recommendation_clicks",
            "recommendation_gmv_cents",
            "recommendation_cost_cents",
        ):
            target[key] = row.get(key) or 0

    result_rows = []
    for row in rows_by_staff.values():
        gmv = int(row.get("gmv_cents") or 0)
        cost = int(row.get("cost_cents") or 0)
        row["net_contribution_cents"] = gmv - cost
        row["roi"] = round(gmv / cost, 4) if cost else 0
        row["net_roi"] = round((gmv - cost) / cost, 4) if cost else 0
        row["workload_score"] = (
            int(row.get("kol_claims") or 0) * 2
            + int(row.get("contacted") or 0)
            + int(row.get("replied") or 0) * 2
            + int(row.get("agreed") or 0) * 4
            + int(row.get("shipped") or 0) * 3
            + int(row.get("published") or 0) * 5
            + int(row.get("measured") or 0) * 3
        )
        row["legacy_workload_score"] = row["workload_score"]
        if float(row.get("ledger_workload_score") or 0):
            row["workload_score"] = round(float(row.get("ledger_workload_score") or 0), 4)
        result_rows.append(row)
    result_rows.sort(key=lambda item: (int(item.get("gmv_cents") or 0), int(item.get("workload_score") or 0)), reverse=True)
    return {
        "window": window,
        "start": start,
        "staff_id": staff_id,
        "rows": result_rows,
        "kpi_formula": {
            "kol_claim": 2,
            "contacted": 1,
            "replied": 2,
            "agreed": 4,
            "shipped": 3,
            "published": 5,
            "measured": 3,
            "financial_credit": "net_contribution = gmv - cost, roi = gmv / cost",
        },
    }


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
        """
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
        """
        SELECT sa.*, p.project_name, k.channel_name AS kol_name,
               os.order_name, os.order_number, os.financial_status
        FROM vkpi_sales_attributions sa
        LEFT JOIN vkpi_projects p ON p.id = sa.project_id
        LEFT JOIN kols k ON k.id = sa.kol_id
        LEFT JOIN vkpi_shopify_order_snapshots os ON os.id = sa.shopify_order_snapshot_id
        WHERE sa.staff_id=? AND {_active_filter}
        ORDER BY COALESCE(sa.occurred_at, sa.imported_at, sa.created_at) DESC, sa.id DESC
        LIMIT ?
        """.format(_active_filter=_active_project_filter("sa")),
        (target_staff_id, limit),
    )
    costs = _safe_rows(
        conn,
        """
        SELECT c.*, p.project_name, k.channel_name AS kol_name
        FROM vkpi_cost_ledger c
        LEFT JOIN vkpi_projects p ON p.id = c.project_id
        LEFT JOIN kols k ON k.id = c.kol_id
        WHERE c.staff_id=? AND c.status!='void' AND {_active_filter}
        ORDER BY c.incurred_at DESC, c.id DESC
        LIMIT ?
        """.format(_active_filter=_active_project_filter("c")),
        (target_staff_id, limit),
    ) if costs_visible else []
    kpi_ledger = _safe_rows(
        conn,
        """
        SELECT kl.*, p.project_name, k.channel_name AS kol_name
        FROM vkpi_kpi_ledger kl
        LEFT JOIN vkpi_projects p ON p.id = kl.project_id
        LEFT JOIN kols k ON k.id = kl.kol_id
        WHERE kl.staff_id=?
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

    total_revenue = sum(int(row.get("revenue_cents") or 0) for row in attributions)
    total_cost = sum(int(row.get("amount_cents") or 0) for row in costs) if costs_visible else None
    summary = {
        **summary,
        "project_count": len(projects),
        "claim_count": len(claims),
        "link_count": len(links),
        "attribution_count": len(attributions),
        "cost_count": len(costs) if costs_visible else None,
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
