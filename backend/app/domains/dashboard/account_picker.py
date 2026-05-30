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


def build_dashboard_kpi(
    account_type: str = "all",
    selected_kol_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_account_type(account_type)
    where_sql, params = _account_where(normalized, selected_kol_ids)
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
    return {
        "account_type": normalized,
        "selected_count": len(_clean_selected_ids(selected_kol_ids)),
        "active_roster": _as_int(_row_value(row, "active_roster")),
        "total_roster": _as_int(_row_value(row, "total_roster")),
        "active_30d": _as_int(_row_value(row, "active_30d")),
        "total_exposure": total_exposure,
        "total_followers": _as_int(_row_value(row, "total_followers")),
        "engagement_rate": round(_as_float(_row_value(row, "engagement_rate")), 2),
        "attributed_gmv": 0.0,
        "avg_roi": None,
        "counts": counts,
        "metric_source": {
            "exposure": "v_dashboard_account_pool.total_views",
            "engagement": "weighted_real_metrics",
            "gmv": "not_connected",
            "roi": "not_connected",
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
