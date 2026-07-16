"""Dashboard and chart aggregate services for V-KPI."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.services.cache.memory_cache import cache_get, cache_set
from app.shared.vkpi_decision_common import (
    _active_project_filter,
    _confirmed_revenue_filter,
    _safe_rows,
    _summary,
)
from app.platform.db.schema import ensure_vkpi_schema

# 大聚合读缓存(60-300s)。dashboard() 是全局口径(无 staff 过滤),键只含窗口;
# dashboard_view() 的 staff 视角键含 staff_id → 绝不跨用户串(dashboard-scope-isolation)。
# 调用方(build_dashboard_summary)会就地改写返回 dict,故命中/落库都走 deepcopy,
# 缓存对象与交给调用方的对象互不污染。
_DECISION_CACHE_TTL = 120


def _day_bucket(*columns: str) -> str:
    """UTC 'YYYY-MM-DD' day string over the first present timestamp column.

    occurred_at/imported_at/incurred_at/clicked_at/published_at 在 Postgres 里是
    timestamptz;旧写法 substr()/NULLIF(col, '') 把它当文本,PG 抛
    "invalid input syntax for type timestamp with time zone" 被 _safe_rows 吞掉——
    每日销售趋势静默为空、员工销售额恒 $0。PG 用 to_char(... AT TIME ZONE 'UTC') 取
    UTC 自然日,SQLite 测试路径仍走 ISO 文本 substr;两侧产出同一 UTC 日,可安全用于
    GROUP BY 与 ">= start" 比较(照 kpi_ledger._day_where 既有范式)。
    """
    if is_postgres_runtime():
        ts = columns[0] if len(columns) == 1 else f"COALESCE({', '.join(columns)})"
        return f"to_char({ts} AT TIME ZONE 'UTC', 'YYYY-MM-DD')"
    if len(columns) == 1:
        return f"substr({columns[0]}, 1, 10)"
    ts = f"COALESCE(NULLIF({columns[0]}, ''), {', '.join(columns[1:])})"
    return f"substr({ts}, 1, 10)"


def dashboard(window_days: int = 30) -> dict[str, Any]:
    days = max(1, min(180, int(window_days or 30)))
    cache_key = f"dash_decision:base:window={days}"
    hit = cache_get(cache_key)
    if hit is not None:
        return copy.deepcopy(hit)
    result = _dashboard_impl(days)
    cache_set(cache_key, copy.deepcopy(result), _DECISION_CACHE_TTL)
    return result


def _dashboard_impl(window_days: int = 30) -> dict[str, Any]:
    ensure_vkpi_schema()
    days = max(1, min(180, int(window_days or 30)))
    conn = get_conn()
    by_stage = [dict(row) for row in conn.execute(
        "SELECT stage, COUNT(*) AS count FROM vkpi_projects GROUP BY stage ORDER BY count DESC"
    ).fetchall()]
    funnel = [dict(row) for row in conn.execute(
        """
        SELECT to_stage AS stage, COUNT(*) AS count
        FROM vkpi_project_stage_events
        GROUP BY to_stage
        ORDER BY count DESC
        """
    ).fetchall()]
    revenue_by_source = [dict(row) for row in conn.execute(
        f"""
        SELECT source_platform,
               confidence,
               COUNT(*) AS rows,
               COALESCE(SUM(revenue_cents), 0) AS revenue_cents
        FROM vkpi_sales_attributions sa
        WHERE {_active_project_filter('sa')}
          AND {_confirmed_revenue_filter('sa')}
        GROUP BY source_platform, confidence
        ORDER BY revenue_cents DESC
        """
    ).fetchall()]
    staff_leaderboard = [dict(row) for row in conn.execute(
        f"""
        SELECT sa.staff_id,
               COALESCE(u.name, u.email, 'Unassigned') AS staff_name,
               COUNT(DISTINCT sa.project_id) AS projects,
               COALESCE(SUM(sa.revenue_cents), 0) AS revenue_cents,
               COALESCE((
                   SELECT SUM(c.amount_cents)
                   FROM vkpi_cost_ledger c
                   WHERE c.staff_id = sa.staff_id
                     AND c.status='actual' AND c.approved_at IS NOT NULL
               ), 0) AS cost_cents
        FROM vkpi_sales_attributions sa
        LEFT JOIN staff st ON st.id = sa.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE {_active_project_filter('sa')}
          AND {_confirmed_revenue_filter('sa')}
        GROUP BY sa.staff_id, u.name, u.email
        ORDER BY revenue_cents DESC
        LIMIT 12
        """
    ).fetchall()]
    roi_by_project = [dict(row) for row in conn.execute(
        f"""
        SELECT p.id,
               p.project_name,
               p.stage,
               COALESCE(SUM(sa.revenue_cents), 0) AS revenue_cents,
               COALESCE((
                   SELECT SUM(c.amount_cents)
                   FROM vkpi_cost_ledger c
                   WHERE c.project_id = p.id
                     AND c.status='actual' AND c.approved_at IS NOT NULL
               ), 0) AS cost_cents
        FROM vkpi_projects p
        LEFT JOIN vkpi_sales_attributions sa
            ON sa.project_id = p.id AND {_confirmed_revenue_filter('sa')}
        GROUP BY p.id, p.project_name, p.stage
        ORDER BY revenue_cents DESC
        LIMIT 20
        """
    ).fetchall()]
    alerts = [dict(row) for row in conn.execute(
        "SELECT * FROM vkpi_alerts WHERE status='open' ORDER BY severity DESC, created_at DESC LIMIT 10"
    ).fetchall()]
    return {
        "window_days": days,
        "summary": _summary(conn),
        "by_stage": by_stage,
        "funnel": funnel,
        "revenue_by_source": revenue_by_source,
        "staff_leaderboard": [
            {
                **row,
                "net_contribution_cents": int(row.get("revenue_cents") or 0) - int(row.get("cost_cents") or 0),
                "roi": round(int(row.get("revenue_cents") or 0) / int(row.get("cost_cents") or 1), 4)
                if int(row.get("cost_cents") or 0)
                else 0,
                "net_roi": round((int(row.get("revenue_cents") or 0) - int(row.get("cost_cents") or 0)) / int(row.get("cost_cents") or 1), 4)
                if int(row.get("cost_cents") or 0)
                else 0,
            }
            for row in staff_leaderboard
        ],
        "roi_by_project": [
            {
                **row,
                "net_contribution_cents": int(row.get("revenue_cents") or 0) - int(row.get("cost_cents") or 0),
                "roi": round(int(row.get("revenue_cents") or 0) / int(row.get("cost_cents") or 1), 4)
                if int(row.get("cost_cents") or 0)
                else 0,
                "net_roi": round((int(row.get("revenue_cents") or 0) - int(row.get("cost_cents") or 0)) / int(row.get("cost_cents") or 1), 4)
                if int(row.get("cost_cents") or 0)
                else 0,
            }
            for row in roi_by_project
        ],
        "open_alerts": alerts,
        "refresh_contract": {
            "last_refresh": "query_time",
            "default_interval": "1h",
            "manual_refresh_endpoint": "/api/admin/vkpi/dashboard",
        },
        "decision_questions": [
            "Which KOL projects are stalled?",
            "Which links convert into Shopify or Amazon sales?",
            "Which staff owners create profitable net contribution?",
            "Which costs should reduce KPI credit?",
            "Which KOL claims should be released or reassigned?",
        ],
    }


def revenue_trend(window_days: int = 7, staff_id: int | None = None) -> dict[str, Any]:
    """Daily operational trend from real attribution/cost/click rows.

    This replaces frontend-fabricated seven-day lines. Missing days are returned
    as zero rows so the UI can render stable axes without inventing data.
    """
    ensure_vkpi_schema()
    days = max(1, min(180, int(window_days or 7)))
    end = datetime.now(timezone.utc).date()
    import datetime as _dt

    dates = [(end - _dt.timedelta(days=days - 1 - index)).isoformat() for index in range(days)]
    start = dates[0]
    conn = get_conn()
    sales_staff_clause = "AND sa.staff_id=?" if staff_id else ""
    sales_params: tuple[Any, ...] = (start, int(staff_id)) if staff_id else (start,)
    sales_day = _day_bucket('sa.occurred_at', 'sa.imported_at', 'sa.created_at')
    # 按 (day, currency) 分组;同一天多币种时下方按币种取主币种(gmv 最大,并列比单量),
    # 绝不把不同币种 cents 相加(无 FX 表)。confirmed + active 过滤不变。
    # NULLIF 施于 TEXT 的 currency 列(非 timestamptz)。
    sales_currency = "COALESCE(NULLIF(sa.currency, ''), 'USD')"
    sales_rows = _safe_rows(
        conn,
        f"""
        SELECT {sales_day} AS day,
               {sales_currency} AS currency,
               COALESCE(SUM(sa.revenue_cents), 0) AS gmv_cents,
               COUNT(*) AS orders
        FROM vkpi_sales_attributions sa
        WHERE {sales_day} >= ?
          AND {_active_project_filter('sa')}
          AND {_confirmed_revenue_filter('sa')}
          {sales_staff_clause}
        GROUP BY day, {sales_currency}
        """,
        sales_params,
    )
    cost_staff_clause = "AND c.staff_id=?" if staff_id else ""
    cost_params: tuple[Any, ...] = (start, int(staff_id)) if staff_id else (start,)
    cost_day = _day_bucket('c.incurred_at')
    cost_rows = _safe_rows(
        conn,
        f"""
        SELECT {cost_day} AS day,
               COALESCE(SUM(c.amount_cents), 0) AS cost_cents
        FROM vkpi_cost_ledger c
        WHERE c.status='actual' AND c.approved_at IS NOT NULL
          AND {cost_day} >= ?
          AND {_active_project_filter('c')}
          {cost_staff_clause}
        GROUP BY day
        """,
        cost_params,
    )
    click_staff_clause = "AND l.staff_id=?" if staff_id else ""
    click_params: tuple[Any, ...] = (start, int(staff_id)) if staff_id else (start,)
    click_day = _day_bucket('c.clicked_at')
    click_rows = _safe_rows(
        conn,
        f"""
        SELECT {click_day} AS day,
               COUNT(*) AS valid_clicks
        FROM vkpi_link_clicks c
        INNER JOIN vkpi_links l ON l.id = c.link_id
        WHERE COALESCE(c.is_bot, 0) = 0
          AND {click_day} >= ?
          {click_staff_clause}
        GROUP BY day
        """,
        click_params,
    )
    view_staff_clause = "AND p.assigned_staff_id=?" if staff_id else ""
    view_params: tuple[Any, ...] = (start, int(staff_id)) if staff_id else (start,)
    view_day = _day_bucket('cp.published_at', 'cp.created_at')
    view_rows = _safe_rows(
        conn,
        f"""
        SELECT {view_day} AS day,
               COALESCE(SUM(cp.views), 0) AS views,
               COALESCE(SUM(cp.likes), 0) AS likes,
               COALESCE(SUM(cp.comments), 0) AS comments,
               COUNT(*) AS published_content
        FROM vkpi_content_posts cp
        LEFT JOIN vkpi_projects p ON p.id = cp.project_id
        WHERE {view_day} >= ?
          AND (cp.project_id IS NULL OR COALESCE(p.stage_status, '') != 'deleted')
          {view_staff_clause}
        GROUP BY day
        """,
        view_params,
    )
    by_day: dict[str, dict[str, Any]] = {
        day: {
            "date": day,
            "gmv_cents": 0,
            "sales_cents": 0,
            "cost_cents": 0,
            "net_contribution_cents": 0,
            "orders": 0,
            "valid_clicks": 0,
            "views": 0,
            "likes": 0,
            "comments": 0,
            "published_content": 0,
        }
        for day in dates
    }
    # 每天可能落多币种行;按币种取主币种(gmv 最大,并列比单量),不跨币相加(无 FX 表)。
    sales_by_day: dict[str, dict[str, int]] = {}
    for row in sales_rows:
        day = str(row.get("day") or "")
        if not day:
            continue
        cand = {"gmv_cents": int(row.get("gmv_cents") or 0), "orders": int(row.get("orders") or 0)}
        prev = sales_by_day.get(day)
        if prev is None or (cand["gmv_cents"], cand["orders"]) > (prev["gmv_cents"], prev["orders"]):
            sales_by_day[day] = cand
    for day, agg in sales_by_day.items():
        if day in by_day:
            by_day[day]["gmv_cents"] = agg["gmv_cents"]
            by_day[day]["sales_cents"] = agg["gmv_cents"]
            by_day[day]["orders"] = agg["orders"]
    for row in cost_rows:
        day = str(row.get("day") or "")
        if day in by_day:
            by_day[day]["cost_cents"] = int(row.get("cost_cents") or 0)
    for row in click_rows:
        day = str(row.get("day") or "")
        if day in by_day:
            by_day[day]["valid_clicks"] = int(row.get("valid_clicks") or 0)
    for row in view_rows:
        day = str(row.get("day") or "")
        if day in by_day:
            by_day[day]["views"] = int(row.get("views") or 0)
            by_day[day]["likes"] = int(row.get("likes") or 0)
            by_day[day]["comments"] = int(row.get("comments") or 0)
            by_day[day]["published_content"] = int(row.get("published_content") or 0)
    rows = []
    for day in dates:
        item = by_day[day]
        item["net_contribution_cents"] = int(item["gmv_cents"]) - int(item["cost_cents"])
        rows.append(item)
    return {"window_days": days, "start": start, "end": dates[-1], "rows": rows}


def product_performance(window_days: int = 30, staff_id: int | None = None, limit: int = 20) -> dict[str, Any]:
    """Product-level sales/cost/content aggregation for management charts.

    This intentionally returns product rows, not project rows. The UI can then
    draw "cost vs sales" without abusing ROI as a bar height.
    """
    ensure_vkpi_schema()
    days = max(1, min(180, int(window_days or 30)))
    limit = max(1, min(100, int(limit or 20)))
    end = datetime.now(timezone.utc).date()
    import datetime as _dt

    start = (end - _dt.timedelta(days=days - 1)).isoformat()
    conn = get_conn()
    staff_clause = "AND p.assigned_staff_id=?" if staff_id else ""
    params: tuple[Any, ...] = (
        start,
        start,
        start,
        start,
        int(staff_id),
        limit,
    ) if staff_id else (
        start,
        start,
        start,
        start,
        limit,
    )
    sales_day = _day_bucket('occurred_at', 'imported_at', 'created_at')
    cost_day = _day_bucket('incurred_at', 'created_at')
    content_day = _day_bucket('published_at', 'created_at')
    project_day = _day_bucket('p.created_at', 'p.updated_at')
    rows = _safe_rows(
        conn,
        f"""
        WITH sales AS (
            SELECT project_id,
                   COALESCE(SUM(revenue_cents), 0) AS sales_cents,
                   COUNT(*) AS orders
            FROM vkpi_sales_attributions
            WHERE {sales_day} >= ?
              AND {_confirmed_revenue_filter()}
            GROUP BY project_id
        ),
        costs AS (
            SELECT project_id,
                   COALESCE(SUM(amount_cents), 0) AS cost_cents
            FROM vkpi_cost_ledger
            WHERE status='actual' AND approved_at IS NOT NULL AND {cost_day} >= ?
            GROUP BY project_id
        ),
        content AS (
            SELECT project_id,
                   COALESCE(SUM(views), 0) AS views,
                   COALESCE(SUM(likes), 0) AS likes,
                   COALESCE(SUM(comments), 0) AS comments,
                   COUNT(*) AS published_content
            FROM vkpi_content_posts
            WHERE {content_day} >= ?
            GROUP BY project_id
        )
        SELECT COALESCE(NULLIF(p.product_sku, ''), 'unknown') AS product_sku,
               COALESCE(NULLIF(p.product_name, ''), NULLIF(p.product_sku, ''), 'Unknown product') AS product_name,
               COUNT(DISTINCT p.id) AS project_count,
               COALESCE(SUM(s.sales_cents), 0) AS sales_cents,
               COALESCE(SUM(s.orders), 0) AS orders,
               COALESCE(SUM(c.cost_cents), 0) AS cost_cents,
               COALESCE(SUM(ct.views), 0) AS views,
               COALESCE(SUM(ct.likes), 0) AS likes,
               COALESCE(SUM(ct.comments), 0) AS comments,
               COALESCE(SUM(ct.published_content), 0) AS published_content
        FROM vkpi_projects p
        LEFT JOIN sales s ON s.project_id = p.id
        LEFT JOIN costs c ON c.project_id = p.id
        LEFT JOIN content ct ON ct.project_id = p.id
        WHERE COALESCE(p.stage_status, '') != 'deleted'
          AND (
            COALESCE(s.sales_cents, 0) != 0
            OR COALESCE(c.cost_cents, 0) != 0
            OR COALESCE(ct.views, 0) != 0
            OR {project_day} >= ?
          )
          {staff_clause}
        GROUP BY product_sku, product_name
        ORDER BY sales_cents DESC, views DESC, cost_cents DESC, project_count DESC
        LIMIT ?
        """,
        params,
    )
    normalized = []
    for row in rows:
        sales_cents = int(row.get("sales_cents") or 0)
        cost_cents = int(row.get("cost_cents") or 0)
        normalized.append({
            **row,
            "sales_cents": sales_cents,
            "revenue_cents": sales_cents,
            "gmv_cents": sales_cents,
            "cost_cents": cost_cents,
            "net_contribution_cents": sales_cents - cost_cents,
            "roi": round(sales_cents / cost_cents, 4) if cost_cents else 0,
            "net_roi": round((sales_cents - cost_cents) / cost_cents, 4) if cost_cents else 0,
        })
    return {"window_days": days, "start": start, "end": end.isoformat(), "rows": normalized}


def dashboard_view(view: str, *, window_days: int = 30, staff_id: int | None = None) -> dict[str, Any]:
    clean = str(view or "owner").strip().lower()
    days = max(1, min(180, int(window_days or 30)))
    cache_key = f"dash_decision:view={clean}:window={days}:staff={int(staff_id) if staff_id else 'global'}"
    hit = cache_get(cache_key)
    if hit is not None:
        return copy.deepcopy(hit)
    result = _dashboard_view_impl(view, window_days=window_days, staff_id=staff_id)
    cache_set(cache_key, copy.deepcopy(result), _DECISION_CACHE_TTL)
    return result


def _dashboard_view_impl(view: str, *, window_days: int = 30, staff_id: int | None = None) -> dict[str, Any]:
    base = dashboard(window_days=window_days)
    clean = str(view or "owner").strip().lower()
    conn = get_conn()
    if clean in {"owner", "boss", "management"}:
        return {
            "view": "owner",
            "summary": base["summary"],
            "staff_leaderboard": base["staff_leaderboard"],
            "revenue_by_source": base["revenue_by_source"],
            "open_alerts": base["open_alerts"],
            "decision_questions": base["decision_questions"],
        }
    if clean in {"staff", "employee"}:
        effective_staff_id = int(staff_id or 0)
        if not effective_staff_id:
            return {"view": "staff", "summary": _summary(conn), "projects": [], "links": [], "attribution": []}
        projects = [dict(row) for row in conn.execute(
            """
            SELECT p.*, k.channel_name AS kol_name
            FROM vkpi_projects p
            LEFT JOIN kols k ON k.id = p.kol_id
            WHERE p.assigned_staff_id=?
            ORDER BY p.updated_at DESC, p.id DESC
            LIMIT 50
            """,
            (effective_staff_id,),
        ).fetchall()]
        links = [dict(row) for row in conn.execute(
            "SELECT * FROM vkpi_links WHERE staff_id=? ORDER BY updated_at DESC, id DESC LIMIT 50",
            (effective_staff_id,),
        ).fetchall()]
        attributions = [dict(row) for row in conn.execute(
            f"SELECT * FROM vkpi_sales_attributions sa WHERE sa.staff_id=? AND {_active_project_filter('sa')} ORDER BY occurred_at DESC, id DESC LIMIT 50",
            (effective_staff_id,),
        ).fetchall()]
        return {
            "view": "staff",
            "staff_id": effective_staff_id,
            "summary": _summary(conn, effective_staff_id),
            "projects": projects,
            "links": links,
            "attribution": attributions,
        }
    if clean == "roi":
        return {
            "view": "roi",
            "summary": base["summary"],
            "roi_by_project": base["roi_by_project"],
            "staff_leaderboard": base["staff_leaderboard"],
            "revenue_by_source": base["revenue_by_source"],
        }
    if clean == "funnel":
        return {
            "view": "funnel",
            "summary": base["summary"],
            "by_stage": base["by_stage"],
            "funnel": base["funnel"],
            "open_alerts": base["open_alerts"],
        }
    raise ValueError("unsupported dashboard view")
