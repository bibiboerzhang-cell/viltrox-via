"""Dashboard time-window metric maturity contract.

This module keeps the UI honest while post-level metric snapshots accumulate.
Lifetime totals may still exist for lineage or account overview, but they are
not a valid replacement for 30-day deltas.
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn

REQUIRED_SNAPSHOT_DAYS = 30
VALID_DASHBOARD_SCOPES = {"owned", "kol", "all"}
_SCOPE_LABELS = {
    "owned": "Owned",
    "kol": "KOL",
    "all": "K + O",
}
_EMPTY_SCOPE_VALUES = {"owned": None, "kol": None, "all": None}
_OFFICIAL_CHANNEL_FILTER_SQL = """
    c.deleted_at IS NULL
    AND c.status='active'
    AND (
        POSITION('official_account' IN COALESCE(c.metadata_json::text, '')) > 0
        OR POSITION('official_list_20260516' IN COALESCE(c.metadata_json::text, '')) > 0
        OR POSITION('official_account_list_2026_05_16' IN COALESCE(c.metadata_json::text, '')) > 0
    )
"""


def normalize_dashboard_scope(scope: str | None) -> str:
    raw = (scope or "all").strip().lower()
    if raw in {"company", "official", "owned_matrix"}:
        return "owned"
    if raw in VALID_DASHBOARD_SCOPES:
        return raw
    return "all"


def maturity_from_days(snapshot_days: int | None, scope: str = "all") -> dict[str, Any]:
    normalized = normalize_dashboard_scope(scope)
    days = max(0, min(int(snapshot_days or 0), REQUIRED_SNAPSHOT_DAYS))
    ready = days >= REQUIRED_SNAPSHOT_DAYS
    return {
        "scope": normalized,
        "scope_label": _SCOPE_LABELS[normalized],
        "snapshot_days": days,
        "required_days": REQUIRED_SNAPSHOT_DAYS,
        "is_ready": ready,
        "maturity_label": "真实 · 30d ready" if ready else f"累积中 {days}/{REQUIRED_SNAPSHOT_DAYS}",
        "data_window": "30d",
        "data_status": "ready" if ready else "accumulating",
    }


def maturity_payload_for_days(days_by_scope: dict[str, int | None] | None = None) -> dict[str, Any]:
    source = days_by_scope or {}
    owned_days = int(source.get("owned") or 0)
    kol_days = int(source.get("kol") or 0)
    all_days = int(source.get("all") if source.get("all") is not None else min(owned_days, kol_days))
    scopes = {
        "owned": maturity_from_days(owned_days, "owned"),
        "kol": maturity_from_days(kol_days, "kol"),
        "all": maturity_from_days(all_days, "all"),
    }
    return {
        "required_days": REQUIRED_SNAPSHOT_DAYS,
        "scopes": scopes,
        "rules": {
            "scope_boundary": "owned_matrix and external_kol are aggregated separately; all is explicit K + O.",
            "window_boundary": "30d metrics require post-level snapshots and never use lifetime totals as a substitute.",
            "kol_visibility": "KOL metrics require signed/trial status plus at least one posted Viltrox-product video.",
            "attribution": "A post is counted once for its owner account; reshared content must not double-count exposure.",
        },
    }


def _count_distinct_snapshot_dates(table_name: str) -> int:
    try:
        conn = get_conn()
        row = conn.execute(f"SELECT COUNT(DISTINCT snapshot_date) AS days FROM {table_name}").fetchone()
        if not row:
            return 0
        try:
            return int(row["days"] or 0)
        except Exception:
            return int(row[0] or 0)
    except Exception:
        try:
            conn.rollback()  # type: ignore[name-defined]
        except Exception:
            pass
        return 0


def snapshot_days_by_scope() -> dict[str, int]:
    owned_days = _count_distinct_snapshot_dates("vkpi_channel_post_metrics")
    # KOL video snapshots do not have a production table yet. Keeping this zero
    # prevents external KOL lifetime fields from leaking into 30d dashboard KPIs.
    kol_days = _count_distinct_snapshot_dates("video_metrics_snapshot")
    return {
        "owned": owned_days,
        "kol": kol_days,
        "all": min(owned_days, kol_days),
    }


def dashboard_metric_maturity_contract() -> dict[str, Any]:
    return maturity_payload_for_days(snapshot_days_by_scope())


def _empty_window_metrics() -> dict[str, dict[str, Any]]:
    return {
        "active_30d_by_scope": dict(_EMPTY_SCOPE_VALUES),
        "exposure_30d_by_scope": dict(_EMPTY_SCOPE_VALUES),
        "engagement_rate_by_scope": dict(_EMPTY_SCOPE_VALUES),
    }


def _owned_snapshot_window_metrics() -> dict[str, Any] | None:
    try:
        conn = get_conn()
        row = conn.execute(
            f"""
            WITH latest AS (
                SELECT MAX(pm.snapshot_date) AS latest_date
                FROM vkpi_channel_post_metrics pm
                JOIN vkpi_employee_channels c ON c.id=pm.channel_id
                WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
            ),
            windowed AS (
                SELECT pm.*, latest.latest_date
                FROM vkpi_channel_post_metrics pm
                JOIN vkpi_employee_channels c ON c.id=pm.channel_id
                CROSS JOIN latest
                WHERE latest.latest_date IS NOT NULL
                  AND {_OFFICIAL_CHANNEL_FILTER_SQL}
                  AND pm.snapshot_date > latest.latest_date - INTERVAL '30 days'
                  AND pm.snapshot_date <= latest.latest_date
            )
            SELECT
                COUNT(DISTINCT channel_id) FILTER (
                    WHERE COALESCE(views_delta, 0) > 0
                       OR COALESCE(likes_delta, 0) > 0
                       OR COALESCE(comments_delta, 0) > 0
                       OR posted_at::date > latest_date - INTERVAL '30 days'
                ) AS active_30d,
                COALESCE(SUM(GREATEST(COALESCE(views_delta, 0), 0)), 0) AS exposure_30d,
                COALESCE(SUM(GREATEST(COALESCE(likes_delta, 0), 0)), 0) AS likes_30d,
                COALESCE(SUM(GREATEST(COALESCE(comments_delta, 0), 0)), 0) AS comments_30d
            FROM windowed
            """
        ).fetchone()
        if not row:
            return None
        try:
            data = dict(row)
        except Exception:
            data = {
                "active_30d": row[0],
                "exposure_30d": row[1],
                "likes_30d": row[2],
                "comments_30d": row[3],
            }
        exposure = int(data.get("exposure_30d") or 0)
        likes = int(data.get("likes_30d") or 0)
        comments = int(data.get("comments_30d") or 0)
        engagement = ((likes + comments) / exposure * 100) if exposure > 0 else None
        return {
            "active_30d": int(data.get("active_30d") or 0),
            "exposure_30d": exposure,
            "engagement_rate": engagement,
        }
    except Exception:
        try:
            conn.rollback()  # type: ignore[name-defined]
        except Exception:
            pass
        return None


def dashboard_window_metrics_contract(maturity_contract: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    contract = maturity_contract or dashboard_metric_maturity_contract()
    metrics = _empty_window_metrics()
    owned_ready = bool(((contract.get("scopes") or {}).get("owned") or {}).get("is_ready"))
    kol_ready = bool(((contract.get("scopes") or {}).get("kol") or {}).get("is_ready"))
    all_ready = bool(((contract.get("scopes") or {}).get("all") or {}).get("is_ready"))
    owned = _owned_snapshot_window_metrics() if owned_ready else None
    if owned:
        metrics["active_30d_by_scope"]["owned"] = owned.get("active_30d")
        metrics["exposure_30d_by_scope"]["owned"] = owned.get("exposure_30d")
        metrics["engagement_rate_by_scope"]["owned"] = owned.get("engagement_rate")
    # External KOL video snapshot infrastructure is not connected yet. Keep KOL
    # and All null until KOL is independently ready, so mixed scope cannot hide
    # missing KOL windows behind owned data.
    if all_ready and owned and kol_ready:
        metrics["active_30d_by_scope"]["all"] = owned.get("active_30d")
        metrics["exposure_30d_by_scope"]["all"] = owned.get("exposure_30d")
        metrics["engagement_rate_by_scope"]["all"] = owned.get("engagement_rate")
    return metrics
