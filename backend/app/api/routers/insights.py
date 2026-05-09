"""
api/routers/insights.py — Admin data center.

These endpoints intentionally degrade to empty metrics when optional modules
such as commerce, activities, or KOL Ops have not been migrated yet. The UI
should show real zeros/empty tables, not placeholder copy or backend 500s.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin/insights", tags=["insights"])


def _cutoff(window: str) -> str | None:
    days = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}.get(str(window or "all"))
    if not days:
        return None
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _table_exists(conn, name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _columns(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"] if hasattr(row, "keys") else row[1]) for row in rows}
    except Exception:
        return set()


def _scalar(conn, sql: str, params: tuple[Any, ...] = (), default: int | float = 0) -> int | float:
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return default
        value = row[0] if not hasattr(row, "keys") else row[0]
        return value if value is not None else default
    except Exception:
        logger.debug("insights.scalar_failed", extra={"sql": sql[:120]}, exc_info=True)
        return default


def _rows(conn, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception:
        logger.debug("insights.rows_failed", extra={"sql": sql[:120]}, exc_info=True)
        return []


def _date_series(conn, table: str, date_col: str, value_sql: str = "COUNT(*)", where: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not _table_exists(conn, table) or date_col not in _columns(conn, table):
        return []
    where_sql = f"WHERE {where}" if where else ""
    return _rows(
        conn,
        f"""
        SELECT substr(COALESCE({date_col}, ''), 1, 10) AS d, {value_sql} AS n
        FROM {table}
        {where_sql}
        GROUP BY substr(COALESCE({date_col}, ''), 1, 10)
        HAVING d != ''
        ORDER BY d ASC
        """,
        params,
    )


def _orders_totals(conn, cutoff: str | None = None) -> dict[str, int]:
    if not _table_exists(conn, "orders"):
        return {"orders": 0, "gmv_cents": 0, "commission_cents": 0, "buyers": 0}
    cols = _columns(conn, "orders")
    date_col = "placed_at" if "placed_at" in cols else "created_at"
    where = []
    params: list[Any] = []
    if cutoff and date_col in cols:
        where.append(f"{date_col} >= ?")
        params.append(cutoff)
    if "status" in cols:
        where.append("LOWER(COALESCE(status, '')) NOT IN ('cancelled', 'refunded', 'void')")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    buyer_expr = "COUNT(DISTINCT attribution_user_id)" if "attribution_user_id" in cols else "0"
    commission_expr = "COALESCE(SUM(commission_cents), 0)" if "commission_cents" in cols else "0"
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS orders,
               COALESCE(SUM(subtotal_cents), 0) AS gmv_cents,
               {commission_expr} AS commission_cents,
               {buyer_expr} AS buyers
        FROM orders
        {where_sql}
        """,
        tuple(params),
    ).fetchone()
    return dict(row) if row else {"orders": 0, "gmv_cents": 0, "commission_cents": 0, "buyers": 0}


@router.get("/overview")
def overview(window: str = "30d", _staff=Depends(require_tab("insights", "read"))):
    conn = get_conn()
    cutoff = _cutoff(window)
    user_cols = _columns(conn, "users")
    sub_cols = _columns(conn, "submissions")
    total_users = int(_scalar(conn, "SELECT COUNT(*) FROM users") if _table_exists(conn, "users") else 0)
    total_submissions = int(_scalar(conn, "SELECT COUNT(*) FROM submissions") if _table_exists(conn, "submissions") else 0)
    recent_users = int(_scalar(conn, "SELECT COUNT(*) FROM users WHERE created_at >= ?", (cutoff,)) if cutoff and "created_at" in user_cols else 0)
    recent_submissions = int(_scalar(conn, "SELECT COUNT(*) FROM submissions WHERE created_at >= ?", (cutoff,)) if cutoff and "created_at" in sub_cols else 0)
    orders_all = _orders_totals(conn)
    orders_window = _orders_totals(conn, cutoff=cutoff)
    approved_where = "final_score >= 60" if "final_score" in sub_cols else "LOWER(COALESCE(status, '')) IN ('approved', 'passed')"
    approved = int(_scalar(conn, f"SELECT COUNT(*) FROM submissions WHERE {approved_where}") if _table_exists(conn, "submissions") else 0)

    return {
        "window": window,
        "kpi": {
            "total_users": total_users,
            "users_window": recent_users,
            "total_submissions": total_submissions,
            "submissions_window": recent_submissions,
            "total_gmv_usd": round(int(orders_all.get("gmv_cents") or 0) / 100, 2),
            "gmv_window_usd": round(int(orders_window.get("gmv_cents") or 0) / 100, 2),
            "avg_gmv_per_user_usd": round(int(orders_all.get("gmv_cents") or 0) / max(total_users, 1) / 100, 2),
        },
        "daily_users": _date_series(conn, "users", "created_at"),
        "daily_submissions": _date_series(conn, "submissions", "created_at"),
        "daily_gmv": _date_series(conn, "orders", "placed_at", "COALESCE(SUM(subtotal_cents), 0)"),
        "funnel": {
            "stage_register": total_users,
            "stage_submit": int(_scalar(conn, "SELECT COUNT(DISTINCT user_id) FROM submissions") if _table_exists(conn, "submissions") and "user_id" in sub_cols else total_submissions),
            "stage_approve": approved,
            "stage_buy": int(orders_all.get("buyers") or 0),
        },
    }


@router.get("/users")
def users(window: str = "30d", _staff=Depends(require_tab("insights", "read"))):
    conn = get_conn()
    cutoff = _cutoff(window)
    total_users = int(_scalar(conn, "SELECT COUNT(*) FROM users") if _table_exists(conn, "users") else 0)
    source_distribution: list[dict[str, Any]] = []
    if _table_exists(conn, "parties") and "origin_source" in _columns(conn, "parties"):
        source_distribution = _rows(
            conn,
            """
            SELECT COALESCE(NULLIF(origin_source, ''), 'unknown') AS source, COUNT(*) AS n
            FROM parties
            GROUP BY COALESCE(NULLIF(origin_source, ''), 'unknown')
            ORDER BY n DESC
            LIMIT 12
            """,
        )
    elif _table_exists(conn, "users") and "role" in _columns(conn, "users"):
        source_distribution = _rows(conn, "SELECT COALESCE(NULLIF(role, ''), 'unknown') AS source, COUNT(*) AS n FROM users GROUP BY role ORDER BY n DESC")

    sub_cols = _columns(conn, "submissions")
    active_expr = "COUNT(DISTINCT user_id)" if "user_id" in sub_cols else "COUNT(*)"
    dau = int(_scalar(conn, f"SELECT {active_expr} FROM submissions WHERE created_at >= ?", ((datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),)) if _table_exists(conn, "submissions") and "created_at" in sub_cols else 0)
    wau = int(_scalar(conn, f"SELECT {active_expr} FROM submissions WHERE created_at >= ?", ((datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),)) if _table_exists(conn, "submissions") and "created_at" in sub_cols else 0)
    mau = int(_scalar(conn, f"SELECT {active_expr} FROM submissions WHERE created_at >= ?", ((datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),)) if _table_exists(conn, "submissions") and "created_at" in sub_cols else 0)
    window_users = int(_scalar(conn, "SELECT COUNT(*) FROM users WHERE created_at >= ?", (cutoff,)) if cutoff and _table_exists(conn, "users") and "created_at" in _columns(conn, "users") else total_users)

    return {
        "window": window,
        "source_distribution": source_distribution,
        "activity": {"dau": dau, "wau": wau, "mau": mau, "dau_mau_ratio": round(dau / max(mau, 1), 3)},
        "new_users": window_users,
        "churn_rate": round(max(total_users - mau, 0) / max(total_users, 1) * 100, 1),
    }


@router.get("/content")
def content(window: str = "30d", _staff=Depends(require_tab("insights", "read"))):
    conn = get_conn()
    if not _table_exists(conn, "submissions"):
        return {"window": window, "score_distribution": [], "rejection_reasons": [], "top_products": [], "platform_distribution": []}
    cols = _columns(conn, "submissions")
    score_distribution = []
    if "final_score" in cols:
        score_distribution = _rows(
            conn,
            """
            SELECT printf('%02d-%02d', CAST(final_score / 10 AS INTEGER) * 10, CAST(final_score / 10 AS INTEGER) * 10 + 9) AS bucket,
                   COUNT(*) AS n
            FROM submissions
            WHERE final_score IS NOT NULL
            GROUP BY CAST(final_score / 10 AS INTEGER)
            ORDER BY CAST(final_score / 10 AS INTEGER)
            """,
        )
    rejection_col = "rejection_reason" if "rejection_reason" in cols else "error_message" if "error_message" in cols else "status"
    product_col = "matched_product_sku" if "matched_product_sku" in cols else "product_sku" if "product_sku" in cols else "product_series" if "product_series" in cols else ""
    platform_distribution = _rows(conn, "SELECT COALESCE(NULLIF(platform, ''), 'unknown') AS platform, COUNT(*) AS n FROM submissions GROUP BY platform ORDER BY n DESC") if "platform" in cols else []
    return {
        "window": window,
        "score_distribution": score_distribution,
        "rejection_reasons": _rows(conn, f"SELECT COALESCE(NULLIF({rejection_col}, ''), 'other') AS reason, COUNT(*) AS n FROM submissions WHERE COALESCE({rejection_col}, '') != '' GROUP BY COALESCE(NULLIF({rejection_col}, ''), 'other') ORDER BY n DESC LIMIT 12"),
        "top_products": _rows(conn, f"SELECT COALESCE(NULLIF({product_col}, ''), 'unknown') AS sku, COUNT(*) AS n FROM submissions GROUP BY COALESCE(NULLIF({product_col}, ''), 'unknown') ORDER BY n DESC LIMIT 20") if product_col else [],
        "platform_distribution": platform_distribution,
    }


@router.get("/commerce")
def commerce(window: str = "30d", _staff=Depends(require_tab("insights", "read"))):
    conn = get_conn()
    totals = _orders_totals(conn, cutoff=_cutoff(window))
    total_users = int(_scalar(conn, "SELECT COUNT(*) FROM users") if _table_exists(conn, "users") else 0)
    sub_cols = _columns(conn, "submissions")
    submitters = int(_scalar(conn, "SELECT COUNT(DISTINCT user_id) FROM submissions") if _table_exists(conn, "submissions") and "user_id" in sub_cols else 0)
    approved = int(_scalar(conn, "SELECT COUNT(DISTINCT user_id) FROM submissions WHERE final_score >= 60") if _table_exists(conn, "submissions") and {"user_id", "final_score"}.issubset(sub_cols) else 0)
    buyers = int(totals.get("buyers") or 0)
    top_creators: list[dict[str, Any]] = []
    if _table_exists(conn, "orders") and "attribution_user_id" in _columns(conn, "orders"):
        top_creators = _rows(
            conn,
            """
            SELECT u.id, u.creator_code, u.email,
                   COUNT(DISTINCT o.id) AS order_count,
                   COALESCE(SUM(o.subtotal_cents), 0) AS gmv_cents,
                   COALESCE(SUM(o.commission_cents), 0) AS commission_cents
            FROM orders o
            LEFT JOIN users u ON u.id = o.attribution_user_id
            WHERE o.attribution_user_id IS NOT NULL
            GROUP BY u.id, u.creator_code, u.email
            ORDER BY gmv_cents DESC
            LIMIT 20
            """,
        )
    return {
        "window": window,
        "aov_usd": round(int(totals.get("gmv_cents") or 0) / max(int(totals.get("orders") or 0), 1) / 100, 2),
        "order_count": int(totals.get("orders") or 0),
        "gmv_usd": round(int(totals.get("gmv_cents") or 0) / 100, 2),
        "commission_usd": round(int(totals.get("commission_cents") or 0) / 100, 2),
        "funnel": {
            "stage_1_register": total_users,
            "stage_2_submit": submitters,
            "stage_3_approve": approved,
            "stage_4_buy": buyers,
            "rate_register_to_submit": round(submitters / max(total_users, 1) * 100, 1),
            "rate_submit_to_approve": round(approved / max(submitters, 1) * 100, 1),
            "rate_approve_to_buy": round(buyers / max(approved, 1) * 100, 1),
            "rate_overall": round(buyers / max(total_users, 1) * 100, 1),
        },
        "top_creators": top_creators,
    }


@router.get("/channels")
def channels(window: str = "30d", _staff=Depends(require_tab("insights", "read"))):
    conn = get_conn()
    utm_sources: list[dict[str, Any]] = []
    if _table_exists(conn, "orders"):
        cols = _columns(conn, "orders")
        source_col = "utm_source" if "utm_source" in cols else "attribution_source" if "attribution_source" in cols else ""
        if source_col:
            utm_sources = _rows(
                conn,
                f"""
                SELECT COALESCE(NULLIF({source_col}, ''), 'direct') AS source,
                       COUNT(*) AS order_count,
                       COALESCE(SUM(subtotal_cents), 0) AS gmv_cents
                FROM orders
                GROUP BY COALESCE(NULLIF({source_col}, ''), 'direct')
                ORDER BY gmv_cents DESC
                LIMIT 12
                """,
            )
    activity_attribution = []
    if _table_exists(conn, "activities") and _table_exists(conn, "activity_attributions"):
        activity_attribution = _rows(
            conn,
            """
            SELECT a.activity_id, a.title, a.activity_type, a.market, a.platform,
                   COUNT(DISTINCT aa.user_id) AS user_count,
                   COALESCE(SUM(aa.order_count), 0) AS order_count,
                   COALESCE(SUM(aa.revenue_cents), 0) AS gmv_cents,
                   COALESCE(SUM(aa.commission_cents), 0) AS commission_cents,
                   a.actual_spend_cents,
                   a.planned_budget_cents,
                   CASE
                     WHEN COALESCE(NULLIF(a.actual_spend_cents, 0), a.planned_budget_cents, 0) > 0
                     THEN (COALESCE(SUM(aa.revenue_cents), 0) - COALESCE(NULLIF(a.actual_spend_cents, 0), a.planned_budget_cents, 0)) * 100.0
                          / COALESCE(NULLIF(a.actual_spend_cents, 0), a.planned_budget_cents, 0)
                     ELSE NULL
                   END AS roi_pct
            FROM activities a
            LEFT JOIN activity_attributions aa ON aa.activity_id = a.id
            WHERE a.status IN ('live', 'ended', 'paused')
            GROUP BY a.id, a.activity_id, a.title, a.activity_type, a.market, a.platform, a.actual_spend_cents, a.planned_budget_cents
            ORDER BY gmv_cents DESC, user_count DESC
            LIMIT 10
            """,
        )
    kol_attribution = []
    if _table_exists(conn, "kols") and _table_exists(conn, "kol_attribution"):
        kol_attribution = _rows(
            conn,
            """
            SELECT k.id, k.channel_name, k.platform,
                   COALESCE(SUM(ka.attributed_revenue_cents), 0) AS gmv_cents
            FROM kols k
            LEFT JOIN kol_campaigns kc ON kc.kol_id = k.id
            LEFT JOIN kol_content co ON co.campaign_id = kc.id
            LEFT JOIN kol_attribution ka ON ka.content_id = co.id
            GROUP BY k.id, k.channel_name, k.platform
            ORDER BY gmv_cents DESC
            LIMIT 10
            """,
        )
    return {"window": window, "utm_sources": utm_sources, "activity_attribution": activity_attribution, "kol_attribution": kol_attribution}


@router.get("/cohorts")
def cohorts(weeks: int = Query(default=12, ge=4, le=52), _staff=Depends(require_tab("insights", "read"))):
    conn = get_conn()
    if not _table_exists(conn, "users"):
        return {"weeks": weeks, "cohorts": []}
    rows = _rows(
        conn,
        """
        SELECT substr(COALESCE(created_at, ''), 1, 10) AS d, COUNT(*) AS users
        FROM users
        WHERE COALESCE(created_at, '') != ''
        GROUP BY substr(COALESCE(created_at, ''), 1, 10)
        ORDER BY d DESC
        LIMIT ?
        """,
        (int(weeks) * 7,),
    )
    return {"weeks": weeks, "cohorts": rows}


@router.get("/health")
def health(_staff=Depends(require_tab("insights", "read"))):
    conn = get_conn()
    total_users = int(_scalar(conn, "SELECT COUNT(*) FROM users") if _table_exists(conn, "users") else 0)
    users_7d = int(_scalar(conn, "SELECT COUNT(*) FROM users WHERE created_at >= ?", ((datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),)) if _table_exists(conn, "users") and "created_at" in _columns(conn, "users") else 0)
    ai_error_rate = 0.0
    if _table_exists(conn, "ai_usage_log"):
        row = conn.execute(
            """
            SELECT COUNT(*) AS total, SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS errors
            FROM ai_usage_log
            WHERE called_at >= ?
            """,
            ((datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),),
        ).fetchone()
        if row and int(row["total"] or 0):
            ai_error_rate = round(int(row["errors"] or 0) / int(row["total"] or 1) * 100, 2)
    return {
        "growth": {"weekly_new_users": users_7d, "weekly_growth_pct": round(users_7d / max(total_users - users_7d, 1) * 100, 1)},
        "ops": {"ai_error_rate_7d": ai_error_rate},
    }


@router.get("/staff-kpi")
def staff_kpi(window: str = "30d", _staff=Depends(require_tab("insights", "read"))):
    conn = get_conn()
    cutoff = _cutoff(window) or "1970-01-01T00:00:00Z"
    audit = []
    if _table_exists(conn, "admin_audit_log"):
        audit = _rows(
            conn,
            """
            SELECT actor_id AS staff_id,
                   COALESCE(actor_name, CAST(actor_id AS TEXT), 'unknown') AS staff_name,
                   COUNT(*) AS action_count,
                   SUM(CASE WHEN action LIKE 'redemption_%' THEN 1 ELSE 0 END) AS redemption_actions,
                   SUM(CASE WHEN action LIKE '%staff%' THEN 1 ELSE 0 END) AS staff_actions
            FROM admin_audit_log
            WHERE occurred_at >= ?
            GROUP BY actor_id, COALESCE(actor_name, CAST(actor_id AS TEXT), 'unknown')
            ORDER BY action_count DESC
            LIMIT 50
            """,
            (cutoff,),
        )
    kol_perf = {}
    if _table_exists(conn, "kol_campaigns") and _table_exists(conn, "kol_attribution"):
        for row in _rows(
            conn,
            """
            SELECT c.staff_id,
                   COUNT(DISTINCT c.id) AS campaign_count,
                   COALESCE(SUM(c.cost_cents), 0) AS cost_cents,
                   COALESCE(SUM(a.attributed_revenue_cents), 0) AS revenue_cents
            FROM kol_campaigns c
            LEFT JOIN kol_content co ON co.campaign_id = c.id
            LEFT JOIN kol_attribution a ON a.content_id = co.id
            GROUP BY c.staff_id
            """,
        ):
            kol_perf[int(row.get("staff_id") or 0)] = row
    merged = []
    for row in audit:
        sid = int(row.get("staff_id") or 0)
        k = kol_perf.get(sid, {})
        cost = int(k.get("cost_cents") or 0)
        revenue = int(k.get("revenue_cents") or 0)
        merged.append({
            **row,
            "kol_campaign_count": int(k.get("campaign_count") or 0),
            "kol_cost_usd": round(cost / 100, 2),
            "kol_revenue_usd": round(revenue / 100, 2),
            "kol_roi": round((revenue - cost) / max(cost, 1), 3) if cost else 0,
        })
    return {"window": window, "staff_kpi": merged}
