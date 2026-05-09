"""Dashboard endpoints for KOL Operations."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.api.routers.kol_ops_helpers import _content_rollup_sql, _dict, _items
from app.db.connection import get_conn
from app.services.kol.metrics import engagement_rate, roi


router = APIRouter()


@router.get("/dashboard/staff-performance")
def staff_performance(staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute(
        """
        SELECT
            COALESCE(k.assigned_staff_id, 0) AS staff_id,
            COALESCE(NULLIF(u.name, ''), NULLIF(k.owner_name, ''), 'Unassigned') AS staff_name,
            COUNT(DISTINCT k.id) AS kol_count,
            COUNT(DISTINCT c.id) AS campaign_count,
            COALESCE(SUM(c.cost_cents), 0) AS total_cost_cents,
            COALESCE(SUM(co.views), 0) AS total_views,
            COALESCE(SUM(co.likes), 0) AS total_likes,
            COALESCE(SUM(co.comments), 0) AS total_comments,
            COALESCE(SUM(co.shares), 0) AS total_shares,
            COALESCE(SUM(at.attributed_revenue_cents), 0) AS total_revenue_cents,
            COALESCE(AVG(co.ai_quality_score), 0) AS avg_quality_score
        FROM kols k
        LEFT JOIN staff s ON s.id = k.assigned_staff_id
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN kol_campaigns c ON c.kol_id = k.id
        LEFT JOIN kol_content co ON co.campaign_id = c.id
        LEFT JOIN kol_attribution at ON at.content_id = co.id
        GROUP BY COALESCE(k.assigned_staff_id, 0), COALESCE(NULLIF(u.name, ''), NULLIF(k.owner_name, ''), 'Unassigned')
        ORDER BY kol_count DESC
        """
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["avg_engagement_rate"] = engagement_rate(item.get("total_likes", 0), item.get("total_comments", 0), item.get("total_shares", 0), item.get("total_views", 0))
        item["roi"] = roi(item.get("total_cost_cents", 0), item.get("total_revenue_cents", 0))
        items.append(item)
    return {"items": items}


@router.get("/dashboard/staff-activity")
def staff_activity(
    date_from: str | None = None,
    date_to: str | None = None,
    staff=Depends(require_tab("kol_ops", "read")),
):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    start = f"{date_from or today}T00:00:00Z"
    end = f"{date_to or today}T23:59:59Z"
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(staff_name, ''), 'Unknown') AS staff_name,
            COALESCE(staff_id, 0) AS staff_id,
            COALESCE(user_id, 0) AS user_id,
            COUNT(*) AS actions,
            COALESCE(SUM(CASE WHEN action_type='platform_search' THEN 1 ELSE 0 END), 0) AS searches,
            COALESCE(SUM(CASE WHEN action_type='view_kol' THEN 1 ELSE 0 END), 0) AS kol_views,
            COALESCE(SUM(CASE WHEN action_type IN ('candidate_review','candidate_promote') THEN 1 ELSE 0 END), 0) AS reviews,
            COALESCE(SUM(CASE WHEN action_type='import_sheet' THEN 1 ELSE 0 END), 0) AS imports,
            COALESCE(SUM(CASE WHEN action_type IN ('data_update','content_create') THEN 1 ELSE 0 END), 0) AS data_updates,
            COALESCE(SUM(CASE WHEN SUBSTR(action_type, 1, 3) = 'ai_' THEN 1 ELSE 0 END), 0) AS ai_actions,
            COALESCE(SUM(api_calls), 0) AS api_calls,
            COALESCE(SUM(result_count), 0) AS result_count,
            MAX(created_at) AS last_action_at
        FROM kol_activity_log
        WHERE created_at >= ? AND created_at <= ?
        GROUP BY staff_name, staff_id, user_id
        ORDER BY actions DESC, last_action_at DESC
        """,
        (start, end),
    ).fetchall()
    recent = conn.execute(
        """
        SELECT *
        FROM kol_activity_log
        WHERE created_at >= ? AND created_at <= ?
        ORDER BY created_at DESC
        LIMIT 80
        """,
        (start, end),
    ).fetchall()
    totals = conn.execute(
        """
        SELECT
            COUNT(*) AS actions,
            COALESCE(SUM(CASE WHEN action_type='platform_search' THEN 1 ELSE 0 END), 0) AS searches,
            COALESCE(SUM(CASE WHEN action_type='view_kol' THEN 1 ELSE 0 END), 0) AS kol_views,
            COALESCE(SUM(CASE WHEN action_type IN ('candidate_review','candidate_promote') THEN 1 ELSE 0 END), 0) AS reviews,
            COALESCE(SUM(CASE WHEN action_type='import_sheet' THEN 1 ELSE 0 END), 0) AS imports,
            COALESCE(SUM(CASE WHEN action_type IN ('data_update','content_create') THEN 1 ELSE 0 END), 0) AS data_updates,
            COALESCE(SUM(CASE WHEN SUBSTR(action_type, 1, 3) = 'ai_' THEN 1 ELSE 0 END), 0) AS ai_actions,
            COALESCE(SUM(api_calls), 0) AS api_calls,
            COALESCE(SUM(result_count), 0) AS result_count
        FROM kol_activity_log
        WHERE created_at >= ? AND created_at <= ?
        """,
        (start, end),
    ).fetchone()
    return {
        "window": {"date_from": date_from or today, "date_to": date_to or today},
        "totals": _dict(totals),
        "items": _items(rows),
        "recent": _items(recent),
    }


@router.get("/dashboard/cross-filter")
def cross_filter(
    staff_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    country: str | None = None,
    platform: str | None = None,
    product_sku: str | None = None,
    staff=Depends(require_tab("kol_ops", "read")),
):
    where, params = [], []
    if staff_id:
        where.append("k.assigned_staff_id = ?"); params.append(staff_id)
    if country:
        where.append("k.country = ?"); params.append(country)
    if platform:
        where.append("k.platform = ?"); params.append(platform)
    if product_sku:
        where.append("ca.product_sku = ?"); params.append(product_sku)
    if date_from:
        where.append("COALESCE(co.posted_at, ca.started_at, ca.created_at, k.created_at) >= ?"); params.append(date_from)
    if date_to:
        where.append("COALESCE(co.posted_at, ca.started_at, ca.created_at, k.created_at) <= ?"); params.append(date_to)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    conn = get_conn()
    kols = conn.execute(
        f"""
        SELECT DISTINCT k.*, u.name AS assigned_staff_name
        FROM kols k
        LEFT JOIN staff s ON s.id = k.assigned_staff_id
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN kol_campaigns ca ON ca.kol_id = k.id
        LEFT JOIN kol_content co ON co.campaign_id = ca.id
        {where_sql}
        ORDER BY k.updated_at DESC
        LIMIT 200
        """,
        params,
    ).fetchall()
    content = conn.execute(
        f"""
        SELECT co.*, k.channel_name, ca.product_sku
        FROM kols k
        JOIN kol_campaigns ca ON ca.kol_id = k.id
        JOIN kol_content co ON co.campaign_id = ca.id
        {where_sql}
        ORDER BY COALESCE(co.posted_at, co.created_at) DESC
        LIMIT 200
        """,
        params,
    ).fetchall()
    return {
        "kpis": _content_rollup_sql(where_sql, params),
        "kols": _items(kols),
        "content": _items(content),
        "performance_breakdown": {
            "country": country or "all",
            "platform": platform or "all",
            "product_sku": product_sku or "all",
        },
    }
