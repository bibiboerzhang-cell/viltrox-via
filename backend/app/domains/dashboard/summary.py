"""Dashboard summary assembly use cases."""
from __future__ import annotations

from typing import Any

from app.domains.dashboard.metric_maturity import (
    _OFFICIAL_CHANNEL_FILTER_SQL,
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


def _build_evidence_active_30d_summary(*, window_days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(window_days or 30), 90))
    external_rows = _fetch_dicts(
        f"""
        SELECT
            COALESCE(p.dashboard_account_type, 'kol') AS account_type,
            COUNT(DISTINCT p.id) AS active_accounts,
            COUNT(*) AS evidence_count
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE COALESCE(e.is_active, TRUE) = TRUE
          AND COALESCE(e.evidence_type, 'video') = 'video'
          AND e.publish_date IS NOT NULL
          AND e.publish_date >= NOW() - INTERVAL '{days} days'
        GROUP BY COALESCE(p.dashboard_account_type, 'kol')
        """
    )
    external_counts = {"kol": 0, "media": 0}
    external_evidence = {"kol": 0, "media": 0}
    for row in external_rows:
        account_type = str(row.get("account_type") or "kol")
        if account_type in external_counts:
            external_counts[account_type] = _as_int(row.get("active_accounts"))
            external_evidence[account_type] = _as_int(row.get("evidence_count"))

    official_row = get_conn().execute(
        f"""
        SELECT
            COUNT(DISTINCT channel_id) FILTER (
                WHERE posted_at::date >= CURRENT_DATE - INTERVAL '{days} days'
                   OR COALESCE(views_delta, 0) > 0
                   OR COALESCE(likes_delta, 0) > 0
                   OR COALESCE(comments_delta, 0) > 0
            ) AS active_accounts,
            COUNT(*) FILTER (
                WHERE posted_at::date >= CURRENT_DATE - INTERVAL '{days} days'
                   OR COALESCE(views_delta, 0) > 0
                   OR COALESCE(likes_delta, 0) > 0
                   OR COALESCE(comments_delta, 0) > 0
            ) AS signal_rows,
            COUNT(DISTINCT snapshot_date) AS snapshot_days
        FROM vkpi_channel_post_metrics pm
        JOIN vkpi_employee_channels c ON c.id = pm.channel_id
        WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        """
    ).fetchone()
    company_active = _as_int(_row_value(official_row, "active_accounts"))
    all_active = external_counts["kol"] + external_counts["media"] + company_active
    return {
        "all": all_active,
        "kol": external_counts["kol"],
        "media": external_counts["media"],
        "company": company_active,
        "owned": company_active,
        "window_days": days,
        "basis": {
            "kol_media": "vkpi_kol_video_evidence.publish_date within window",
            "company": "vkpi_channel_post_metrics posted_at or positive deltas within window",
        },
        "evidence_count_by_scope": external_evidence,
        "company_signal_rows": _as_int(_row_value(official_row, "signal_rows")),
        "company_snapshot_days": _as_int(_row_value(official_row, "snapshot_days")),
    }


def _build_evidence_metrics_summary(*, window_days: int = 30) -> dict[str, Any]:
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
        "active_30d_by_scope": _build_evidence_active_30d_summary(window_days=window_days),
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


def _build_active_campaigns_summary(*, window_days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(window_days or 30), 365))
    rows = _fetch_dicts(
        f"""
        WITH assignment_stats AS (
          SELECT
            project_id,
            COUNT(DISTINCT kol_pool_id) AS kol_count,
            COUNT(DISTINCT kol_pool_id) FILTER (
              WHERE stage IN ('device_sent', 'received', 'content_posted')
            ) AS execution_kol_count,
            COUNT(DISTINCT kol_pool_id) FILTER (
              WHERE stage = 'content_posted'
            ) AS published_kol_count,
            BOOL_OR(stage IN ('device_sent', 'received', 'content_posted')) AS has_execution_stage
          FROM vkpi_project_kol_assignments
          GROUP BY project_id
        ),
        evidence_stats AS (
          SELECT
            project_id,
            COUNT(*) AS evidence_count,
            COUNT(*) FILTER (
              WHERE created_at >= NOW() - INTERVAL '{days} days'
            ) AS recent_evidence_count,
            COALESCE(SUM(view_count), 0) AS total_views,
            COALESCE(SUM(view_count) FILTER (
              WHERE created_at >= NOW() - INTERVAL '{days} days'
            ), 0) AS recent_views,
            BOOL_OR(created_at >= NOW() - INTERVAL '{days} days') AS has_recent_evidence
          FROM vkpi_kol_video_evidence
          WHERE project_id IS NOT NULL
            AND COALESCE(is_active, TRUE) = TRUE
            AND COALESCE(evidence_type, 'video') = 'video'
          GROUP BY project_id
        ),
        active_projects AS (
          SELECT
            p.id,
            p.project_uid,
            p.project_name,
            p.product_sku,
            p.product_name,
            p.stage,
            p.stage_status,
            p.source_type,
            p.updated_at,
            COALESCE(a.kol_count, 0) AS kol_count,
            COALESCE(a.execution_kol_count, 0) AS execution_kol_count,
            COALESCE(a.published_kol_count, 0) AS published_kol_count,
            COALESCE(e.evidence_count, 0) AS evidence_count,
            COALESCE(e.recent_evidence_count, 0) AS recent_evidence_count,
            COALESCE(e.total_views, 0) AS total_views,
            COALESCE(e.recent_views, 0) AS recent_views,
            COALESCE(a.has_execution_stage, FALSE) AS has_execution_stage,
            COALESCE(e.has_recent_evidence, FALSE) AS has_recent_evidence
          FROM vkpi_projects p
          LEFT JOIN assignment_stats a ON a.project_id = p.id
          LEFT JOIN evidence_stats e ON e.project_id = p.id
          WHERE COALESCE(p.stage, '') NOT IN (
              'closed', 'churned', 'cancelled', 'canceled', 'done', 'deleted',
              '已关闭', '已取消', '已中止', '合作中止'
            )
            AND COALESCE(p.stage_status, '') NOT IN (
              'closed', 'churned', 'cancelled', 'canceled', 'done', 'deleted',
              '已关闭', '已取消', '已中止', '合作中止'
            )
            AND COALESCE(p.source_type, '') <> 'codex_test'
        )
        SELECT *
        FROM active_projects
        WHERE has_execution_stage = TRUE OR has_recent_evidence = TRUE
        ORDER BY recent_evidence_count DESC, execution_kol_count DESC, updated_at DESC NULLS LAST, id DESC
        """
    )

    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows[:8]):
        recent_evidence_count = _as_int(row.get("recent_evidence_count"))
        execution_kol_count = _as_int(row.get("execution_kol_count"))
        signals: list[str] = []
        if execution_kol_count:
            signals.append(f"实操阶段 {execution_kol_count} KOL")
        if recent_evidence_count:
            signals.append(f"近 {days} 天出片 {recent_evidence_count}")
        items.append(
            {
                "id": _as_int(row.get("id")),
                "project_uid": row.get("project_uid"),
                "name": row.get("project_name"),
                "product": row.get("product_name") or row.get("product_sku"),
                "status": "active",
                "status_label": "进行中",
                "icon_key": "camera",
                "icon_color": ["#a855f7", "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4"][index % 6],
                "kol_count": _as_int(row.get("kol_count")),
                "execution_kol_count": execution_kol_count,
                "published_count": _as_int(row.get("published_kol_count")),
                "recent_video_count": recent_evidence_count,
                "evidence_count": _as_int(row.get("evidence_count")),
                "total_views": _as_int(row.get("total_views")),
                "recent_views": _as_int(row.get("recent_views")),
                "active_signals": signals,
                "bottleneck_text": " · ".join(signals) or "符合当前 active campaign 口径",
            }
        )

    return {
        "active_count": len(rows),
        "items": items,
        "window_days": days,
        "criteria": {
            "project_status": "vkpi_projects.stage/stage_status not in closed/churned/cancelled/done/deleted/已关闭/已取消/已中止/合作中止",
            "signals": [
                "assignment stage in device_sent/received/content_posted",
                f"video evidence created in the last {days} days",
            ],
            "excluded_source_types": ["codex_test"],
        },
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
    summary["evidence_metrics"] = _build_evidence_metrics_summary(window_days=window_days)
    summary["active_campaigns"] = _build_active_campaigns_summary(window_days=window_days)
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
