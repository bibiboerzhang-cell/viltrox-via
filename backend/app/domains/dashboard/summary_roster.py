"""Roster mover aggregates used by the dashboard summary."""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains.dashboard.summary_rows import (
    _as_int,
    _fetch_dicts,
    _metric_item,
    _row_value,
)


def _json_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _serial_mover_rows(
    viltrox_evidence: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Portable fallback used by SQLite and hermetic tests."""

    by_views = _fetch_dicts(
        f"""
        SELECT p.id AS kol_id, COALESCE(NULLIF(p.display_name, ''), p.handle) AS kol_name,
               p.handle, p.profile_url, p.platform,
               COALESCE(e.title, e.video_title, e.content_url) AS title,
               e.content_url AS url, e.view_count, e.like_count, e.comment_count, e.publish_date
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.view_count IS NOT NULL
          AND {viltrox_evidence}
        ORDER BY e.view_count DESC NULLS LAST
        LIMIT 10
        """
    )
    by_activity = _fetch_dicts(
        f"""
        SELECT p.id AS kol_id, COALESCE(NULLIF(p.display_name, ''), p.handle) AS kol_name,
               p.handle, p.profile_url, p.platform,
               COUNT(*) AS value
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE {viltrox_evidence}
        GROUP BY p.id, p.display_name, p.handle, p.profile_url, p.platform
        ORDER BY COUNT(*) DESC, p.display_name
        LIMIT 10
        """
    )
    by_recent = _fetch_dicts(
        f"""
        SELECT p.id AS kol_id, COALESCE(NULLIF(p.display_name, ''), p.handle) AS kol_name,
               p.handle, p.profile_url, p.platform,
               COALESCE(e.title, e.video_title, e.content_url) AS title,
               e.content_url AS url, e.view_count, e.like_count, e.comment_count, e.publish_date
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.view_count IS NOT NULL
          AND e.publish_date >= NOW() - INTERVAL '30 days'
          AND {viltrox_evidence}
        ORDER BY e.view_count DESC NULLS LAST
        LIMIT 10
        """
    )
    by_engagement = _fetch_dicts(
        f"""
        SELECT p.id AS kol_id, COALESCE(NULLIF(p.display_name, ''), p.handle) AS kol_name,
               p.handle, p.profile_url, p.platform,
               COALESCE(e.title, e.video_title, e.content_url) AS title,
               e.content_url AS url, e.view_count, e.like_count, e.comment_count, e.publish_date,
               COALESCE(e.like_count, 0) + COALESCE(e.comment_count, 0) AS engagement_count
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE (e.like_count IS NOT NULL OR e.comment_count IS NOT NULL)
          AND {viltrox_evidence}
        ORDER BY COALESCE(e.like_count, 0) + COALESCE(e.comment_count, 0) DESC
        LIMIT 10
        """
    )
    return by_views, by_activity, by_recent, by_engagement


def _postgres_mover_rows(
    viltrox_evidence: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build all four tabs from one materialized evidence scan."""

    row = get_conn().execute(
        f"""
        WITH base AS MATERIALIZED (
          SELECT p.id AS kol_id,
                 COALESCE(NULLIF(p.display_name, ''), p.handle) AS kol_name,
                 p.display_name, p.handle, p.profile_url, p.platform,
                 COALESCE(e.title, e.video_title, e.content_url) AS title,
                 e.content_url AS url,
                 e.view_count, e.like_count, e.comment_count,
                 e.publish_date AS publish_date_raw,
                 CASE
                   WHEN e.publish_date IS NULL THEN NULL
                   ELSE to_char(e.publish_date AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                 END AS publish_date,
                 COALESCE(e.like_count, 0) + COALESCE(e.comment_count, 0) AS engagement_count
          FROM vkpi_kol_video_evidence e
          JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
          WHERE {viltrox_evidence}
        ),
        by_views AS (
          SELECT kol_id, kol_name, handle, profile_url, platform, title, url,
                 view_count, like_count, comment_count, publish_date
          FROM base
          WHERE view_count IS NOT NULL
          ORDER BY view_count DESC NULLS LAST
          LIMIT 10
        ),
        by_activity AS (
          SELECT kol_id, MAX(kol_name) AS kol_name, MAX(handle) AS handle,
                 MAX(profile_url) AS profile_url, MAX(platform) AS platform,
                 MAX(display_name) AS display_name, COUNT(*) AS value
          FROM base
          GROUP BY kol_id
          ORDER BY COUNT(*) DESC, MAX(display_name)
          LIMIT 10
        ),
        by_recent AS (
          SELECT kol_id, kol_name, handle, profile_url, platform, title, url,
                 view_count, like_count, comment_count, publish_date
          FROM base
          WHERE view_count IS NOT NULL
            AND publish_date_raw >= NOW() - INTERVAL '30 days'
          ORDER BY view_count DESC NULLS LAST
          LIMIT 10
        ),
        by_engagement AS (
          SELECT kol_id, kol_name, handle, profile_url, platform, title, url,
                 view_count, like_count, comment_count, publish_date, engagement_count
          FROM base
          WHERE like_count IS NOT NULL OR comment_count IS NOT NULL
          ORDER BY engagement_count DESC
          LIMIT 10
        )
        SELECT
          (SELECT COALESCE(
             jsonb_agg(to_jsonb(v) ORDER BY v.view_count DESC NULLS LAST),
             '[]'::jsonb
           ) FROM by_views v) AS by_views,
          (SELECT COALESCE(
             jsonb_agg(to_jsonb(a) ORDER BY a.value DESC, a.display_name),
             '[]'::jsonb
           ) FROM by_activity a) AS by_activity,
          (SELECT COALESCE(
             jsonb_agg(to_jsonb(r) ORDER BY r.view_count DESC NULLS LAST),
             '[]'::jsonb
           ) FROM by_recent r) AS by_recent,
          (SELECT COALESCE(
             jsonb_agg(to_jsonb(g) ORDER BY g.engagement_count DESC),
             '[]'::jsonb
           ) FROM by_engagement g) AS by_engagement
        """
    ).fetchone()
    return (
        _json_rows(_row_value(row, "by_views")),
        _json_rows(_row_value(row, "by_activity")),
        _json_rows(_row_value(row, "by_recent")),
        _json_rows(_row_value(row, "by_engagement")),
    )


def build_roster_movers_tabs(viltrox_evidence: str) -> dict[str, list[dict[str, Any]]]:
    rows = (
        _postgres_mover_rows(viltrox_evidence)
        if is_postgres_runtime()
        else _serial_mover_rows(viltrox_evidence)
    )
    by_views, by_activity, by_recent, by_engagement = rows
    return {
        "by_views": [_metric_item(row, "view_count") for row in by_views],
        "by_activity": [
            {
                "kol_id": _as_int(row.get("kol_id")),
                "kol_name": row.get("kol_name"),
                "handle": row.get("handle"),
                "profile_url": row.get("profile_url"),
                "platform": row.get("platform"),
                "value": _as_int(row.get("value")),
            }
            for row in by_activity
        ],
        "by_recent": [_metric_item(row, "view_count") for row in by_recent],
        "by_engagement": [
            _metric_item(row, "engagement_count") for row in by_engagement
        ],
    }


__all__ = ["build_roster_movers_tabs"]
