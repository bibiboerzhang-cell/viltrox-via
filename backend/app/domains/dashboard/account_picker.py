"""Dashboard account picker metrics backed by the unified account pool view."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn

VALID_ACCOUNT_TYPES = {"all", "kol", "media", "company"}
ACTIVE_ACCOUNT_SQL = """
CASE
    WHEN account_type IN ('kol', 'media') THEN COALESCE(has_video_evidence, false)
    ELSE (COALESCE(posts_count, 0) > 0 OR COALESCE(total_views, 0) > 0)
END
"""


def _normalize_account_type(account_type: str | None) -> str:
    value = str(account_type or "all").strip().lower()
    if value not in VALID_ACCOUNT_TYPES:
        raise ValueError(f"unsupported account_type: {account_type}")
    return value


def _clean_selected_ids(selected_kol_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in selected_kol_ids or []:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return cleaned


def _numeric_ids(values: list[str]) -> list[int]:
    ids: list[int] = []
    for value in values:
        if value.isdigit():
            ids.append(int(value))
    return ids


def _placeholders(count: int) -> str:
    return ",".join(["?"] * count)


def _account_where(
    account_type: str,
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
    search: str | None = None,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if account_type != "all":
        where.append("account_type = ?")
        params.append(account_type)

    selected = _clean_selected_ids(selected_kol_ids)
    if selected:
        selected_clauses: list[str] = []
        selected_clauses.append(f"dashboard_id IN ({_placeholders(len(selected))})")
        params.extend(selected)
        numeric = _numeric_ids(selected)
        if numeric:
            selected_clauses.append(f"source_id IN ({_placeholders(len(numeric))})")
            params.extend(numeric)
        where.append("(" + " OR ".join(selected_clauses) + ")")

    term = str(search or "").strip().lower()
    if term:
        pattern = f"%{term}%"
        where.append(
            "("
            "LOWER(COALESCE(handle, '')) LIKE ? OR "
            "LOWER(COALESCE(display_name, '')) LIKE ? OR "
            "LOWER(COALESCE(platform, '')) LIKE ? OR "
            "LOWER(COALESCE(country, '')) LIKE ?"
            ")"
        )
        params.extend([pattern, pattern, pattern, pattern])

    if not where:
        return "", params
    return " WHERE " + " AND ".join(where), params


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _account_dict(row: Any) -> dict[str, Any]:
    total_views = _as_int(_row_value(row, "total_views"))
    total_likes = _as_int(_row_value(row, "total_likes"))
    total_comments = _as_int(_row_value(row, "total_comments"))
    total_shares = _as_int(_row_value(row, "total_shares"))
    return {
        "id": str(_row_value(row, "dashboard_id", "")),
        "dashboard_id": str(_row_value(row, "dashboard_id", "")),
        "source_id": _as_int(_row_value(row, "source_id")),
        "source_table": str(_row_value(row, "source_table", "") or ""),
        "handle": str(_row_value(row, "handle", "") or ""),
        "name": str(_row_value(row, "display_name", "") or ""),
        "platform": str(_row_value(row, "platform", "") or ""),
        "country": str(_row_value(row, "country", "") or ""),
        "tier": str(_row_value(row, "tier", "") or ""),
        "account_type": str(_row_value(row, "account_type", "") or ""),
        "followers": _as_int(_row_value(row, "followers")),
        "posts_count": _as_int(_row_value(row, "posts_count")),
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_shares": total_shares,
        "engagement_rate": _as_float(_row_value(row, "engagement_rate")),
        "profile_url": str(_row_value(row, "profile_url", "") or ""),
        "avatar_url": str(_row_value(row, "avatar_url", "") or ""),
        "latitude": _row_value(row, "latitude"),
        "longitude": _row_value(row, "longitude"),
        "last_synced_at": _row_value(row, "last_synced_at"),
        "is_official": bool(_row_value(row, "is_official", False)),
        "is_active": bool(_as_int(_row_value(row, "is_active"))),
        "interactions": total_likes + total_comments + total_shares,
    }


def build_dashboard_account_counts() -> dict[str, int]:
    rows = get_conn().execute(
        """
        SELECT account_type, COUNT(*) AS count
        FROM v_dashboard_account_pool
        GROUP BY account_type
        """
    ).fetchall()
    counts = {"all": 0, "kol": 0, "media": 0, "company": 0}
    for row in rows:
        account_type = str(_row_value(row, "account_type", "") or "")
        count = _as_int(_row_value(row, "count"))
        if account_type in counts:
            counts[account_type] = count
            counts["all"] += count
    return counts


def _shopify_configured() -> bool:
    """True when the Shopify Admin API env vars are present (no fabrication)."""
    import os

    shop_domain = str(os.environ.get("SHOPIFY_SHOP_DOMAIN") or "").strip()
    access_token = str(
        os.environ.get("SHOPIFY_ADMIN_ACCESS_TOKEN") or os.environ.get("SHOPIFY_API_ACCESS_TOKEN") or ""
    ).strip()
    return bool(shop_domain and access_token)


def _shopify_orders_gmv_cents(conn: Any) -> int:
    """Real GMV (cents) from the ingested Shopify order ledger, or 0.

    A7 / migration 138. Returns 0 when the table is absent (migration not yet run)
    or empty (no live creds yet) so the honest 待接入 path stays unchanged. Uses
    the commerce domain's summarize_gmv() — single source of truth for the sum.
    """
    try:
        from app.domains.commerce import shopify_orders

        summary = shopify_orders.summarize_gmv()
        return _as_int(summary.get("gmv_cents"))
    except Exception:
        # Table missing / serialization error -> behave as if no orders (honest).
        return 0


def _build_attributed_gmv_roi() -> dict[str, Any]:
    """Program-wide confirmed-Shopify GMV + ROI from the real attribution ledger.

    GMV  = SUM(vkpi_sales_attributions.revenue_cents) WHERE source_platform='shopify'
           AND confidence='confirmed'.
    cost = SUM(vkpi_cost_ledger.amount_cents) WHERE status != 'void' (the same
           formula the metric-lineage layer uses for marketing cost).
    ROI  = (gmv - cost) / cost when cost > 0, else None.

    The dashboard account-picker scope (v_dashboard_account_pool.source_id) maps to
    vkpi_kol_pool.id, while attribution.kol_id maps to kols.id — different id
    namespaces — so this is computed program-wide rather than per selected account.
    Numbers are real; when no confirmed attribution exists the cards stay honest
    (待接入 / awaiting_data).
    """
    conn = get_conn()

    # A7: when the real Shopify ingest ledger (vkpi_shopify_orders, migration 138)
    # has rows, GMV becomes real — SUM(total_price_cents) over ingested orders.
    # Additive + safe: the table is empty until live Shopify creds land, so this
    # branch is skipped and the EXACT honest 待接入 path below runs unchanged.
    shopify_gmv_cents = _shopify_orders_gmv_cents(conn)

    gmv_row = conn.execute(
        """
        SELECT COALESCE(SUM(revenue_cents), 0) AS gmv_cents
        FROM vkpi_sales_attributions
        WHERE source_platform = 'shopify'
          AND confidence = 'confirmed'
        """
    ).fetchone()
    cost_row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS cost_cents
        FROM vkpi_cost_ledger
        WHERE status != 'void'
        """
    ).fetchone()

    attribution_gmv_cents = _as_int(_row_value(gmv_row, "gmv_cents"))
    cost_cents = _as_int(_row_value(cost_row, "cost_cents"))

    # Prefer the real ingested-order ledger when it has data; otherwise fall back
    # to the confirmed-attribution sum (which is itself 0 until attribution lands).
    if shopify_gmv_cents > 0:
        gmv_cents = shopify_gmv_cents
        gmv_is_from_orders = True
    else:
        gmv_cents = attribution_gmv_cents
        gmv_is_from_orders = False

    gmv = round(gmv_cents / 100.0, 2)
    cost = round(cost_cents / 100.0, 2)

    has_gmv = gmv_cents > 0
    avg_roi: float | None = None
    if cost_cents > 0:
        avg_roi = round((gmv_cents - cost_cents) / cost_cents, 4)

    if has_gmv and gmv_is_from_orders:
        gmv_source = "vkpi_shopify_orders.total_price_cents[ingested]"
    elif has_gmv:
        gmv_source = "vkpi_sales_attributions.revenue_cents[shopify,confirmed]"
    elif _shopify_configured():
        gmv_source = "awaiting_data"
    else:
        gmv_source = "not_connected"

    if avg_roi is not None and has_gmv:
        roi_source = "(gmv-cost)/cost · vkpi_cost_ledger[status!=void]"
    elif _shopify_configured():
        roi_source = "awaiting_data"
    else:
        roi_source = "not_connected"

    return {
        "attributed_gmv": gmv,
        "attributed_gmv_cents": gmv_cents,
        "cost": cost,
        "cost_cents": cost_cents,
        "avg_roi": avg_roi,
        "gmv_source": gmv_source,
        "roi_source": roi_source,
    }


def build_dashboard_kpi(
    account_type: str = "all",
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
    *,
    staff_scope_id: int | None = None,
) -> dict[str, Any]:
    normalized = _normalize_account_type(account_type)
    where_sql, params = _account_where(normalized, selected_kol_ids)
    if staff_scope_id:
        # P1 隔离:非 owner/admin 的 KOL 行只算本人关注(收藏)∪ 本人项目里合作的 KOL;
        # 公司/官方账号(account_type<>'kol')是公共资产,保持全局可见。内联可信整数,非注入面。
        sid = int(staff_scope_id)
        scope_clause = (
            "(account_type <> 'kol' OR source_id IN ("
            f"SELECT kol_pool_id FROM vkpi_kol_pool_favorites WHERE staff_id={sid} "
            "UNION SELECT a.kol_pool_id FROM vkpi_project_kol_assignments a "
            f"WHERE a.project_id IN (SELECT id FROM vkpi_projects WHERE assigned_staff_id={sid} "
            f"OR created_by_staff_id={sid} OR id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id={sid}))"
            "))"
        )
        where_sql = (where_sql + " AND " + scope_clause) if where_sql else (" WHERE " + scope_clause)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    conn = get_conn()
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_roster,
            COUNT(*) FILTER (
                WHERE {ACTIVE_ACCOUNT_SQL}
            ) AS active_roster,
            COUNT(*) FILTER (
                WHERE last_synced_at IS NOT NULL
                  AND last_synced_at >= ?
                  AND {ACTIVE_ACCOUNT_SQL}
            ) AS active_30d,
            COALESCE(SUM(COALESCE(total_views, 0)), 0) AS total_exposure,
            COALESCE(SUM(COALESCE(followers, 0)), 0) AS total_followers,
            COALESCE(SUM(COALESCE(total_likes, 0)), 0) AS total_likes,
            COALESCE(SUM(COALESCE(total_comments, 0)), 0) AS total_comments,
            COALESCE(SUM(COALESCE(total_shares, 0)), 0) AS total_shares,
            CASE
                WHEN COALESCE(SUM(COALESCE(total_views, 0)), 0) > 0 THEN
                    (
                        SUM(COALESCE(total_likes, 0) + COALESCE(total_comments, 0) + COALESCE(total_shares, 0))::numeric
                        / NULLIF(SUM(COALESCE(total_views, 0)), 0)
                    ) * 100
                ELSE COALESCE(AVG(NULLIF(engagement_rate, 0)), 0)
            END AS engagement_rate
        FROM v_dashboard_account_pool
        {where_sql}
        """,
        [cutoff, *params],
    ).fetchone()

    counts = build_dashboard_account_counts()
    total_exposure = _as_int(_row_value(row, "total_exposure"))
    total_likes = _as_int(_row_value(row, "total_likes"))
    total_comments = _as_int(_row_value(row, "total_comments"))
    total_shares = _as_int(_row_value(row, "total_shares"))
    revenue = _build_attributed_gmv_roi()
    return {
        "account_type": normalized,
        "selected_count": len(_clean_selected_ids(selected_kol_ids)),
        "active_roster": _as_int(_row_value(row, "active_roster")),
        "total_roster": _as_int(_row_value(row, "total_roster")),
        "active_30d": _as_int(_row_value(row, "active_30d")),
        "total_exposure": total_exposure,
        "total_followers": _as_int(_row_value(row, "total_followers")),
        "engagement_rate": round(_as_float(_row_value(row, "engagement_rate")), 2),
        "attributed_gmv": revenue["attributed_gmv"],
        "attributed_gmv_cents": revenue["attributed_gmv_cents"],
        "attributed_cost": revenue["cost"],
        "avg_roi": revenue["avg_roi"],
        "counts": counts,
        "metric_source": {
            "exposure": "v_dashboard_account_pool.total_views",
            "engagement": "weighted_real_metrics",
            "gmv": revenue["gmv_source"],
            "roi": revenue["roi_source"],
        },
        "totals": {
            "likes": total_likes,
            "comments": total_comments,
            "shares": total_shares,
            "interactions": total_likes + total_comments + total_shares,
        },
    }


def list_dashboard_accounts(
    account_type: str = "all",
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_account_type(account_type)
    safe_page = max(1, int(page or 1))
    safe_page_size = min(200, max(1, int(page_size or 50)))
    offset = (safe_page - 1) * safe_page_size
    where_sql, params = _account_where(normalized, selected_kol_ids, search)
    conn = get_conn()
    total_row = conn.execute(
        f"SELECT COUNT(*) AS total FROM v_dashboard_account_pool{where_sql}",
        params,
    ).fetchone()
    rows = conn.execute(
        f"""
        SELECT
            *,
            CASE
                WHEN {ACTIVE_ACCOUNT_SQL} THEN 1
                ELSE 0
            END AS is_active
        FROM v_dashboard_account_pool
        {where_sql}
        ORDER BY
            CASE account_type WHEN 'company' THEN 0 WHEN 'kol' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
            COALESCE(total_views, 0) DESC,
            COALESCE(followers, 0) DESC,
            LOWER(COALESCE(display_name, handle, ''))
        LIMIT ? OFFSET ?
        """,
        [*params, safe_page_size, offset],
    ).fetchall()
    return {
        "account_type": normalized,
        "total": _as_int(_row_value(total_row, "total")),
        "page": safe_page,
        "page_size": safe_page_size,
        "kols": [_account_dict(row) for row in rows],
        "counts": build_dashboard_account_counts(),
    }


def build_dashboard_account_map(
    account_type: str = "all",
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_account_type(account_type)
    where_sql, params = _account_where(normalized, selected_kol_ids)
    extra = " AND " if where_sql else " WHERE "
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM v_dashboard_account_pool
        {where_sql}{extra}latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY COALESCE(followers, 0) DESC
        LIMIT 1000
        """,
        params,
    ).fetchall()
    country_counts: dict[str, int] = {}
    accounts = [_account_dict(row) for row in rows]
    for account in accounts:
        country = str(account.get("country") or "Unknown")
        country_counts[country] = country_counts.get(country, 0) + 1
    return {
        "account_type": normalized,
        "kols": accounts,
        "summary_by_country": [
            {"country": country, "count": count}
            for country, count in sorted(country_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }
