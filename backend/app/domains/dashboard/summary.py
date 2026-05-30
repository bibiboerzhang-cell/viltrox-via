"""Dashboard summary assembly use cases."""
from __future__ import annotations

from typing import Any

from app.domains.dashboard.metric_maturity import (
    dashboard_metric_maturity_contract,
    dashboard_window_metrics_contract,
    normalize_dashboard_scope,
)
from app.domains.dashboard.account_picker import build_dashboard_kpi
from app.db.connection import get_conn
from app.domains.dashboard.recent_content import _dashboard_official_matrix_summary
from app.domains import lineage as metric_lineage
from app.domains.dashboard import decision_dashboard as decision_engine
from app.domains.access import scope
from app.domains.projects.workflow import staff_id as resolve_staff_id


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


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: _row_value(row, key) for key in row.keys()}
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    try:
        return dict(row)
    except Exception:
        return {}


def _fetch_dicts(sql: str) -> list[dict[str, Any]]:
    return [_row_dict(row) for row in get_conn().execute(sql).fetchall()]


def _metric_item(row: dict[str, Any], metric_key: str) -> dict[str, Any]:
    return {
        "kol_id": _as_int(row.get("kol_id")),
        "kol_name": row.get("kol_name"),
        "platform": row.get("platform"),
        "title": row.get("title"),
        "url": row.get("url"),
        "value": _as_int(row.get(metric_key)),
        "view_count": _as_int(row.get("view_count")),
        "like_count": _as_int(row.get("like_count")),
        "comment_count": _as_int(row.get("comment_count")),
        "publish_date": row.get("publish_date"),
    }


def _build_roster_detail(active_roster_by_scope: dict[str, int]) -> dict[str, Any]:
    total_pool_row = get_conn().execute("SELECT COUNT(*) AS count FROM vkpi_kol_pool").fetchone()
    total_pool = _as_int(_row_value(total_pool_row, "count"))

    platform_rows = _fetch_dicts(
        """
        SELECT COALESCE(NULLIF(platform, ''), 'unknown') AS platform, COUNT(*) AS count
        FROM vkpi_kol_pool
        WHERE has_video_evidence = TRUE
        GROUP BY COALESCE(NULLIF(platform, ''), 'unknown')
        ORDER BY COUNT(*) DESC, platform
        """
    )
    active_pool = sum(_as_int(row.get("count")) for row in platform_rows) or 1
    by_platform = [
        {
            "platform": row.get("platform"),
            "count": _as_int(row.get("count")),
            "pct": _as_int(row.get("count")) / active_pool,
        }
        for row in platform_rows
    ]

    bucket_rows = _fetch_dicts(
        """
        WITH per_kol AS (
          SELECT
            a.kol_pool_id,
            COUNT(DISTINCT a.project_id) AS project_count,
            COUNT(*) FILTER (WHERE a.stage IN ('content_posted','reviewed')) AS published_rows,
            COUNT(*) FILTER (WHERE a.stage = 'device_sent') AS device_rows,
            COUNT(*) FILTER (WHERE a.stage IN ('discovered','contacted','replied','agreed')) AS pending_rows,
            COUNT(*) FILTER (WHERE a.stage = 'churned') AS churned_rows,
            COALESCE(p.video_evidence_count, 0) AS video_evidence_count
          FROM vkpi_project_kol_assignments a
          JOIN vkpi_kol_pool p ON p.id = a.kol_pool_id
          GROUP BY a.kol_pool_id, p.video_evidence_count
        ), buckets AS (
          SELECT CASE
            WHEN project_count > 1 OR (published_rows > 0 AND video_evidence_count > 1) THEN 'long_term'
            WHEN published_rows > 0 THEN 'one_off'
            WHEN device_rows > 0 THEN 'in_production'
            WHEN pending_rows > 0 THEN 'pending'
            WHEN churned_rows > 0 THEN 'churned'
            ELSE 'pending'
          END AS bucket
          FROM per_kol
        )
        SELECT bucket, COUNT(*) AS count
        FROM buckets
        GROUP BY bucket
        """
    )
    bucket_counts = {row.get("bucket"): _as_int(row.get("count")) for row in bucket_rows}
    partnership_total = sum(bucket_counts.values()) or 1
    partnership_4tier = {
        "long_term": bucket_counts.get("long_term", 0),
        "in_production": bucket_counts.get("in_production", 0),
        "one_off": bucket_counts.get("one_off", 0),
        "pending": bucket_counts.get("pending", 0),
        "churned": bucket_counts.get("churned", 0),
    }
    partnership_4tier_pct = {
        key: value / partnership_total
        for key, value in partnership_4tier.items()
    }

    views_rows = _fetch_dicts(
        """
        SELECT p.id AS kol_id, p.display_name AS kol_name, p.platform,
               COALESCE(e.title, e.video_title, e.content_url) AS title,
               e.content_url AS url, e.view_count, e.like_count, e.comment_count, e.publish_date
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.view_count IS NOT NULL
        ORDER BY e.view_count DESC NULLS LAST
        LIMIT 10
        """
    )
    activity_rows = _fetch_dicts(
        """
        SELECT id AS kol_id, display_name AS kol_name, platform,
               video_evidence_count AS value
        FROM vkpi_kol_pool
        WHERE has_video_evidence = TRUE
        ORDER BY video_evidence_count DESC NULLS LAST, display_name
        LIMIT 10
        """
    )
    recent_rows = _fetch_dicts(
        """
        SELECT p.id AS kol_id, p.display_name AS kol_name, p.platform,
               COALESCE(e.title, e.video_title, e.content_url) AS title,
               e.content_url AS url, e.view_count, e.like_count, e.comment_count, e.publish_date
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.view_count IS NOT NULL
          AND e.publish_date >= NOW() - INTERVAL '30 days'
        ORDER BY e.view_count DESC NULLS LAST
        LIMIT 10
        """
    )
    engagement_rows = _fetch_dicts(
        """
        SELECT p.id AS kol_id, p.display_name AS kol_name, p.platform,
               COALESCE(e.title, e.video_title, e.content_url) AS title,
               e.content_url AS url, e.view_count, e.like_count, e.comment_count, e.publish_date,
               COALESCE(e.like_count, 0) + COALESCE(e.comment_count, 0) AS engagement_count
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.like_count IS NOT NULL OR e.comment_count IS NOT NULL
        ORDER BY COALESCE(e.like_count, 0) + COALESCE(e.comment_count, 0) DESC
        LIMIT 10
        """
    )

    company_trend_rows = _fetch_dicts(
        """
        SELECT snapshot_date::text AS date,
               COUNT(DISTINCT channel_id) AS account_count,
               COALESCE(SUM(total_views), 0) AS total_views,
               COALESCE(SUM(views_delta_24h), 0) AS views_delta_24h
        FROM vkpi_channel_metrics
        WHERE snapshot_date >= (SELECT MAX(snapshot_date) FROM vkpi_channel_metrics) - INTERVAL '6 days'
        GROUP BY snapshot_date
        ORDER BY snapshot_date
        """
    )
    company_trend = [
        {
            "date": row.get("date"),
            "account_count": _as_int(row.get("account_count")),
            "total_views": _as_int(row.get("total_views")),
            "views_delta_24h": _as_int(row.get("views_delta_24h")),
        }
        for row in company_trend_rows
    ]

    composition = {
        "signed": partnership_4tier["long_term"] + partnership_4tier["in_production"] + partnership_4tier["one_off"],
        "pending": partnership_4tier["pending"],
        "churned": partnership_4tier["churned"],
    }

    return {
        "total_pool": total_pool,
        "active_roster": active_roster_by_scope.get("all", 0),
        "composition": composition,
        "partnership_4tier": partnership_4tier,
        "partnership_4tier_pct": partnership_4tier_pct,
        "by_platform": by_platform,
        "movers_tabs": {
            "by_views": [_metric_item(row, "view_count") for row in views_rows],
            "by_activity": [
                {
                    "kol_id": _as_int(row.get("kol_id")),
                    "kol_name": row.get("kol_name"),
                    "platform": row.get("platform"),
                    "value": _as_int(row.get("value")),
                }
                for row in activity_rows
            ],
            "by_recent": [_metric_item(row, "view_count") for row in recent_rows],
            "by_engagement": [_metric_item(row, "engagement_count") for row in engagement_rows],
        },
        "trend": {
            "all": {"status": "accumulating", "snapshot_days": 7, "required_days": 30},
            "kol": {"status": "accumulating", "snapshot_days": 7, "required_days": 30},
            "company": {"status": "real", "window_days": 7, "points": company_trend},
        },
    }


def _build_evidence_metrics_summary() -> dict[str, Any]:
    active_roster_by_scope = {
        account_type: _as_int(build_dashboard_kpi(account_type=account_type).get("active_roster"))
        for account_type in ("all", "kol", "media", "company")
    }
    row = get_conn().execute(
        """
        SELECT
            COUNT(*) AS evidence_total,
            COUNT(*) FILTER (WHERE view_count IS NOT NULL) AS view_covered,
            COALESCE(SUM(COALESCE(view_count, 0)), 0) AS total_views,
            COALESCE(SUM(
                CASE
                    WHEN view_count IS NOT NULL THEN COALESCE(like_count, 0) + COALESCE(comment_count, 0)
                    ELSE 0
                END
            ), 0) AS total_engagement,
            MAX(metrics_scraped_at) AS last_refreshed_at
        FROM vkpi_kol_video_evidence
        """
    ).fetchone()
    evidence_total = _as_int(_row_value(row, "evidence_total"))
    view_covered = _as_int(_row_value(row, "view_covered"))
    total_views = _as_int(_row_value(row, "total_views"))
    total_engagement = _as_int(_row_value(row, "total_engagement"))
    engagement_rate = (total_engagement / total_views) if total_views > 0 else None
    view_coverage_pct = (view_covered / evidence_total) if evidence_total > 0 else 0.0
    return {
        "active_roster_by_scope": active_roster_by_scope,
        "total_exposure": total_views,
        "engagement": {
            "total_engagement": total_engagement,
            "total_views": total_views,
            "engagement_rate": engagement_rate,
        },
        "coverage": {
            "evidence_total": evidence_total,
            "view_covered": view_covered,
            "view_coverage_pct": view_coverage_pct,
            "last_refreshed_at": _row_value(row, "last_refreshed_at"),
        },
        "roster_detail": _build_roster_detail(active_roster_by_scope),
    }


def build_dashboard_summary(
    *,
    window_days: int = 30,
    staff_id: int | None = None,
    metric_scope: str = "all",
    staff: dict[str, Any],
) -> dict[str, Any]:
    normalized_scope = normalize_dashboard_scope(metric_scope)
    effective_staff_id = scope.effective_staff_id(staff, staff_id)
    result = (
        decision_engine.dashboard_view("staff", window_days=window_days, staff_id=effective_staff_id)
        if effective_staff_id
        else decision_engine.dashboard(window_days=window_days)
    )
    lineage = metric_lineage.dashboard_metrics(
        period_days=window_days,
        staff=staff,
        staff_id=effective_staff_id,
        generated_by_staff_id=resolve_staff_id(staff) or None,
    )
    result["metric_run"] = lineage.get("run") or {}
    result["metrics"] = lineage.get("metrics") or []
    maturity_contract = dashboard_metric_maturity_contract()
    scope_maturity = maturity_contract["scopes"][normalized_scope]
    result["metric_contract"] = maturity_contract
    result["metric_maturity"] = scope_maturity
    result["metric_maturity_by_scope"] = maturity_contract["scopes"]
    window_metrics = dashboard_window_metrics_contract(maturity_contract)
    official_summary = _dashboard_official_matrix_summary(limit=20)
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    summary["active_roster"] = int(build_dashboard_kpi(account_type="all").get("active_roster") or 0)
    summary["evidence_metrics"] = _build_evidence_metrics_summary()
    summary["metric_scope"] = normalized_scope
    summary["scope_label"] = scope_maturity["scope_label"]
    summary["snapshot_days"] = scope_maturity["snapshot_days"]
    summary["required_days"] = scope_maturity["required_days"]
    summary["maturity_label"] = scope_maturity["maturity_label"]
    summary["exposure_30d_by_scope"] = window_metrics["exposure_30d_by_scope"]
    summary["engagement_rate_by_scope"] = window_metrics["engagement_rate_by_scope"]
    summary["active_30d_by_scope"] = window_metrics["active_30d_by_scope"]
    if official_summary:
        result["official_matrix_summary"] = official_summary
        summary["official_account_count"] = official_summary["account_count"]
        summary["official_post_count"] = official_summary["post_count"]
        summary["official_total_views"] = official_summary["total_views"]
        summary["official_total_views_is_lifetime"] = True
        summary["official_total_views_30d"] = None
    result["summary"] = summary
    return result
