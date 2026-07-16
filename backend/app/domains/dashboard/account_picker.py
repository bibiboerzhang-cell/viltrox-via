"""Dashboard account picker metrics backed by the unified account pool view."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains import business_truth
from app.services.cache.memory_cache import cache_get, cache_set

# dashboard 聚合 30s 短缓存。键必含 staff_scope_id(见 _dash_cache_key)——
# 公司视角(global)与每个成员各自一份,绝不把 A 的聚合喂给 B(W0 隔离红线在缓存层延续)。
_DASH_CACHE_TTL = 30


def _dash_cache_key(name: str, staff_scope_id: int | None, **parts: Any) -> str:
    sid = str(staff_scope_id) if staff_scope_id else "global"
    kp = ":".join(
        f"{k}={','.join(map(str, v)) if isinstance(v, (list, tuple)) else v}"
        for k, v in sorted(parts.items())
    )
    return f"dash:{name}:scope={sid}:{kp}"


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


def _staff_scope_clause(staff_scope_id: int | None) -> tuple[str, list[Any]]:
    """P1 隔离的 KOL 行口径(参数化,审计 S2:不再内联整数进 SQL)。

    返回 (sql 片段, params)。owner/admin(staff_scope_id=None)→('', []) 全局;
    员工→只算本人关注(收藏)∪ 本人项目里合作的 KOL;公司/官方账号(account_type<>'kol')
    属公共资产保持全局。供 build_dashboard_kpi / list_dashboard_accounts /
    build_dashboard_account_map 共用同一口径。"""
    if not staff_scope_id:
        return "", []
    sql = (
        "(account_type <> 'kol' OR source_id IN ("
        "SELECT kol_pool_id FROM vkpi_kol_pool_favorites WHERE staff_id=? "
        "UNION SELECT a.kol_pool_id FROM vkpi_project_kol_assignments a "
        "WHERE a.project_id IN (SELECT id FROM vkpi_projects WHERE assigned_staff_id=? "
        "OR created_by_staff_id=? OR id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id=?))"
        "))"
    )
    sid = int(staff_scope_id)
    return sql, [sid, sid, sid, sid]


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
    """True when the canonical encrypted-DB-or-env resolver has credentials."""
    try:
        from app.domains.commerce import shopify_connect

        status = shopify_connect.connection_status()
        return bool(status.get("shop_domain") and status.get("token_configured"))
    except Exception:
        return False


def _shopify_orders_gmv_cents(conn: Any) -> int:
    """Real GMV (cents) from the *ingested* Shopify order ledger only, or 0.

    A7 / migration 138. Reads summarize_gmv().order_ledger_gmv_cents — NOT the reconciled
    gmv_cents. summarize_gmv() folds the attribution ledger in as a fallback when no orders
    are ingested, so returning its reconciled gmv_cents here made the caller mislabel
    attribution-sourced GMV as "[ingested]" (gmv_is_from_orders=True) with zero real orders.
    order_ledger_gmv_cents stays 0 while the order table is empty, so the honest
    待接入 / confirmed-attribution branch below reports its true source. Table absent /
    serialization error -> 0 (honest, behaves as no orders).
    """
    try:
        from app.domains.commerce import shopify_orders

        summary = shopify_orders.summarize_gmv()
        return _as_int(summary.get("order_ledger_gmv_cents"))
    except Exception:
        # Table missing / serialization error -> behave as if no orders (honest).
        return 0


def _build_attributed_gmv_roi() -> dict[str, Any]:
    """Program-wide confirmed-Shopify GMV + ROI from the real attribution ledger.

    GMV  = SUM(vkpi_sales_attributions.revenue_cents) WHERE source_platform='shopify'
           AND confidence='confirmed'.
    cost = SUM(vkpi_cost_ledger.amount_cents) WHERE status='actual' and an
           auditable approval/verified auto-post timestamp exists.
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

    # Group by currency so mixed-currency confirmed rows are not added together (no FX table);
    # report the dominant currency only. NULLIF on the TEXT currency column (not a timestamptz).
    gmv_rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(currency, ''), 'USD') AS currency,
               COALESCE(SUM(revenue_cents), 0) AS gmv_cents,
               COUNT(*) AS order_count
        FROM vkpi_sales_attributions
        WHERE {business_truth.verified_shopify_attribution_sql()}
          AND confidence = 'confirmed'
        GROUP BY COALESCE(NULLIF(currency, ''), 'USD')
        ORDER BY gmv_cents DESC
        """
    ).fetchall()
    cost_row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS cost_cents
        FROM vkpi_cost_ledger
        WHERE status = 'actual' AND approved_at IS NOT NULL
        """
    ).fetchone()

    attr_by_currency = [
        {
            "currency": str(_row_value(r, "currency", "USD") or "USD"),
            "gmv_cents": _as_int(_row_value(r, "gmv_cents")),
            "order_count": _as_int(_row_value(r, "order_count")),
        }
        for r in gmv_rows
    ]
    attr_primary = max(
        attr_by_currency, key=lambda b: (b["gmv_cents"], b["order_count"]), default=None
    )
    attribution_gmv_cents = _as_int((attr_primary or {}).get("gmv_cents"))
    attribution_currency = (attr_primary or {}).get("currency")
    attribution_mixed_currency = len(attr_by_currency) > 1
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
        roi_source = "(gmv-cost)/cost · vkpi_cost_ledger[actual+approved_at]"
    elif _shopify_configured():
        roi_source = "awaiting_data"
    else:
        roi_source = "not_connected"

    # Currency is known for the confirmed-attribution branch (grouped above). The ingested-order
    # branch surfaces its own currency via summarize_gmv; not re-reported here.
    gmv_currency = None if gmv_is_from_orders else attribution_currency
    gmv_mixed_currency = False if gmv_is_from_orders else attribution_mixed_currency
    return {
        "attributed_gmv": gmv,
        "attributed_gmv_cents": gmv_cents,
        "cost": cost,
        "cost_cents": cost_cents,
        "avg_roi": avg_roi,
        "gmv_source": gmv_source,
        "gmv_currency": gmv_currency,
        "gmv_mixed_currency": gmv_mixed_currency,
        "roi_source": roi_source,
    }


def _build_dashboard_kpi_impl(
    account_type: str = "all",
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
    *,
    staff_scope_id: int | None = None,
) -> dict[str, Any]:
    normalized = _normalize_account_type(account_type)
    where_sql, params = _account_where(normalized, selected_kol_ids)
    scope_sql, scope_params = _staff_scope_clause(staff_scope_id)
    if scope_sql:
        where_sql = (where_sql + " AND " + scope_sql) if where_sql else (" WHERE " + scope_sql)
        params = [*params, *scope_params]
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
                            CAST(
                                SUM(COALESCE(total_likes, 0) + COALESCE(total_comments, 0) + COALESCE(total_shares, 0))
                                AS NUMERIC
                            )
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


def _list_dashboard_accounts_impl(
    account_type: str = "all",
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
    staff_scope_id: int | None = None,
) -> dict[str, Any]:
    normalized = _normalize_account_type(account_type)
    safe_page = max(1, int(page or 1))
    safe_page_size = min(200, max(1, int(page_size or 50)))
    offset = (safe_page - 1) * safe_page_size
    where_sql, params = _account_where(normalized, selected_kol_ids, search)
    # P1 隔离(S1 漏网修复):此前 /kols 端点不带 scope → 员工看全量。
    scope_sql, scope_params = _staff_scope_clause(staff_scope_id)
    if scope_sql:
        where_sql = (where_sql + " AND " + scope_sql) if where_sql else (" WHERE " + scope_sql)
        params = [*params, *scope_params]
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


def _build_dashboard_account_map_impl(
    account_type: str = "all",
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
    *,
    staff_scope_id: int | None = None,
) -> dict[str, Any]:
    normalized = _normalize_account_type(account_type)
    where_sql, params = _account_where(normalized, selected_kol_ids)
    # P1 隔离(S1 漏网修复):地图端点此前不带 scope → 员工看全量 KOL 分布。
    scope_sql, scope_params = _staff_scope_clause(staff_scope_id)
    if scope_sql:
        where_sql = (where_sql + " AND " + scope_sql) if where_sql else (" WHERE " + scope_sql)
        params = [*params, *scope_params]
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


# ── scope-keyed 30s 缓存包装(公开名不变;键含 staff_scope_id → 跨用户隔离在缓存层延续)──
def build_dashboard_kpi(
    account_type: str = "all",
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
    *,
    staff_scope_id: int | None = None,
) -> dict[str, Any]:
    ck = _dash_cache_key("kpi", staff_scope_id, acct=account_type, sel=tuple(selected_kol_ids or ()))
    hit = cache_get(ck)
    if hit is not None:
        return hit
    result = _build_dashboard_kpi_impl(account_type, selected_kol_ids, staff_scope_id=staff_scope_id)
    cache_set(ck, result, _DASH_CACHE_TTL)
    return result


def list_dashboard_accounts(
    account_type: str = "all",
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
    staff_scope_id: int | None = None,
) -> dict[str, Any]:
    ck = _dash_cache_key(
        "kols", staff_scope_id, acct=account_type, page=page, size=page_size,
        q=(search or ""), sel=tuple(selected_kol_ids or ()),
    )
    hit = cache_get(ck)
    if hit is not None:
        return hit
    result = _list_dashboard_accounts_impl(
        account_type, page=page, page_size=page_size, search=search,
        selected_kol_ids=selected_kol_ids, staff_scope_id=staff_scope_id,
    )
    cache_set(ck, result, _DASH_CACHE_TTL)
    return result


def build_dashboard_account_map(
    account_type: str = "all",
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
    *,
    staff_scope_id: int | None = None,
) -> dict[str, Any]:
    ck = _dash_cache_key("map", staff_scope_id, acct=account_type, sel=tuple(selected_kol_ids or ()))
    hit = cache_get(ck)
    if hit is not None:
        return hit
    result = _build_dashboard_account_map_impl(account_type, selected_kol_ids, staff_scope_id=staff_scope_id)
    cache_set(ck, result, _DASH_CACHE_TTL)
    return result
