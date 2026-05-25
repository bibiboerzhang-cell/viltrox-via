"""DB-bound facade for P6.73 rule-based trend detection v0."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.trends.trend_detection import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_ROW_LIMIT,
    DEFAULT_TOP_SIGNALS,
    DETECTION_VERSION,
    RULES,
    build_trend_detection_report,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in get_conn().execute(sql, params).fetchall()]
    except Exception:
        return []


def _table_exists(table_name: str) -> bool:
    try:
        row = get_conn().execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = get_conn().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _load_post_metric_rows(limit: int = DEFAULT_ROW_LIMIT) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_channel_post_metrics"):
        return []
    return _safe_query(
        """
        SELECT
            m.channel_id, m.snapshot_date, m.platform, m.post_uid, m.post_url, m.title,
            m.posted_at, m.views, m.likes, m.comments, m.shares,
            m.views_delta, m.likes_delta, m.comments_delta, m.shares_delta,
            m.delta_method, m.first_seen_at, m.captured_at,
            c.account_handle, c.account_display_name
        FROM vkpi_channel_post_metrics m
        LEFT JOIN vkpi_employee_channels c ON c.id = m.channel_id
        ORDER BY m.captured_at DESC, m.snapshot_date DESC, m.id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    )


def _load_channel_metric_rows(limit: int = DEFAULT_ROW_LIMIT) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_channel_metrics"):
        return []
    return _safe_query(
        """
        SELECT
            m.channel_id, m.snapshot_date, m.followers, m.posts_count, m.total_views,
            m.total_likes, m.total_comments, m.total_shares,
            m.followers_delta, m.posts_delta, m.views_delta_24h, m.likes_delta_24h,
            m.engagement_rate, m.captured_at,
            c.platform, c.account_handle, c.account_display_name
        FROM vkpi_channel_metrics m
        LEFT JOIN vkpi_employee_channels c ON c.id = m.channel_id
        ORDER BY m.captured_at DESC, m.snapshot_date DESC, m.id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    )


def _load_market_signal_rows(limit: int = DEFAULT_ROW_LIMIT) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_competitor_signals"):
        return []
    return _safe_query(
        """
        SELECT
            signal_uid, brand, normalized_brand, signal_type, severity, score, platform,
            detail, source_url, review_status, created_at, updated_at
        FROM vkpi_competitor_signals
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    )


def build_trend_detection_v0(
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit: int = DEFAULT_ROW_LIMIT,
    top_signals: int = DEFAULT_TOP_SIGNALS,
) -> dict[str, Any]:
    """Build a read-only trend detection report from existing anchors."""
    bounded_limit = max(1, min(20000, int(limit or DEFAULT_ROW_LIMIT)))
    return build_trend_detection_report(
        post_rows=_load_post_metric_rows(bounded_limit),
        channel_rows=_load_channel_metric_rows(bounded_limit),
        market_rows=_load_market_signal_rows(bounded_limit),
        lookback_days=lookback_days,
        limit=bounded_limit,
        top_signals=top_signals,
        now=_now(),
    )


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_ROW_LIMIT",
    "DEFAULT_TOP_SIGNALS",
    "DETECTION_VERSION",
    "RULES",
    "build_trend_detection_v0",
]
