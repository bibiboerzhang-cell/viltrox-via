"""Active campaign aggregate used by the dashboard summary."""
from __future__ import annotations

from typing import Any

from app.domains.dashboard.summary_rows import _as_int, _fetch_dicts
from app.domains.dashboard.summary_scope import (
    _EXECUTION_STAGES_SQL,
    _FUNNEL_PUBLISHED_STAGES_SQL,
)


def build_active_campaigns_summary(
    *,
    window_days: int = 30,
    staff_scope_id: int | None = None,
) -> dict[str, Any]:
    days = max(1, min(int(window_days or 30), 365))
    # Members see only assigned/created/shared projects; management has no row filter.
    project_scope = (
        f"AND (p.assigned_staff_id={int(staff_scope_id)} OR p.created_by_staff_id={int(staff_scope_id)} "
        f"OR p.id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id={int(staff_scope_id)}))"
        if staff_scope_id else ""
    )
    rows = _fetch_dicts(
        f"""
        WITH assignment_stats AS (
          SELECT
            project_id,
            COUNT(DISTINCT kol_pool_id) AS kol_count,
            COUNT(DISTINCT kol_pool_id) FILTER (
              WHERE stage IN {_EXECUTION_STAGES_SQL}
            ) AS execution_kol_count,
            COUNT(DISTINCT kol_pool_id) FILTER (
              WHERE stage IN {_FUNNEL_PUBLISHED_STAGES_SQL}
            ) AS published_kol_count,
            BOOL_OR(stage IN {_EXECUTION_STAGES_SQL}) AS has_execution_stage
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
            {project_scope}
        )
        SELECT *
        FROM active_projects
        WHERE has_execution_stage = TRUE OR has_recent_evidence = TRUE
        ORDER BY recent_evidence_count DESC, execution_kol_count DESC,
                 updated_at DESC NULLS LAST, id DESC
        """
    )

    items: list[dict[str, Any]] = []
    icon_colors = ["#a855f7", "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4"]
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
                "icon_color": icon_colors[index % len(icon_colors)],
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


__all__ = ["build_active_campaigns_summary"]
