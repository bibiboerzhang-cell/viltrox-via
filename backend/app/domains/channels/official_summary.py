"""Compact official-channel totals for Dashboard reads."""
from __future__ import annotations

from typing import Any

from app.domains.channels.common import *
from app.domains.channels.official import _extract_posts


def _latest_channel_summary_rows() -> list[dict[str, Any]]:
    """Read latest totals while omitting provider payloads unless posts are zero."""
    ensure_vkpi_channels_schema()
    rows = get_conn().execute(
        """
        SELECT c.id,
               c.platform,
               c.metadata_json,
               c.account_handle,
               c.account_display_name,
               c.account_url,
               c.avatar_url,
               c.last_sync_at,
               m.posts_count AS metric_posts,
               m.total_views AS metric_views,
               m.total_likes AS metric_likes,
               m.total_comments AS metric_comments,
               m.total_shares AS metric_shares,
               CASE
                 WHEN COALESCE(m.posts_count, 0) = 0 THEN m.raw_payload_json
                 ELSE NULL
               END AS metric_raw_payload_json,
               m.captured_at AS metric_captured_at
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
            SELECT id FROM vkpi_channel_metrics mm
            WHERE mm.channel_id = c.id
            ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
            LIMIT 1
        )
        WHERE c.deleted_at IS NULL AND c.status='active'
        ORDER BY c.platform ASC, c.account_handle ASC, c.id ASC
        """,
    ).fetchall()
    return [dict(row) for row in rows]


def _summary_account_post_count(row: dict[str, Any], *, limit: int) -> int:
    """Match full-matrix zero-post fallback without constructing media cards."""
    raw_count = row.get("metric_posts")
    if raw_count:
        try:
            return int(raw_count)
        except (TypeError, ValueError):
            pass

    from app.domains.channels.posts import _posts_from_package

    safe_limit = max(1, min(50, int(limit or 50)))
    raw_payload = _parse_json(row.get("metric_raw_payload_json"))
    package_posts = _posts_from_package(
        _text(raw_payload.get("package_dir")),
        limit=safe_limit,
        enrich_raw=False,
    )
    posts = package_posts if package_posts else _extract_posts(row, per_account_limit=limit)
    return _int(raw_count, len(posts))


def official_account_matrix_summary(*, limit: int = 20) -> dict[str, int]:
    """Return official account, post, view and platform totals only."""
    rows = [row for row in _latest_channel_summary_rows() if _is_official_channel_row(row)]
    platforms = {str(row.get("platform") or "other").lower() for row in rows}
    return {
        "account_count": len(rows),
        "post_count": sum(_summary_account_post_count(row, limit=limit) for row in rows),
        "total_views": sum(_int(row.get("metric_views")) for row in rows),
        "platform_count": len(platforms),
    }
