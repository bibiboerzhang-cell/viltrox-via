"""DB-bound facade for P6.76 read-only 24h new signal digest."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.intelligence.today_signals import (
    DEFAULT_LIMIT,
    DEFAULT_LOOKBACK_HOURS,
    DIGEST_VERSION,
    COMMENT_OPPORTUNITY_KEYWORDS,
    build_today_new_signals_report,
)
from app.services.vkpi import trend_detection_v0


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _safe_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in get_conn().execute(sql, params).fetchall()]
    except Exception:
        return []


def _comment_rows(limit: int) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_comments"):
        return []
    sentiment_join = ""
    select_sentiment = "'' AS sentiment, '' AS brand_attitude, '' AS emotion, NULL AS analyzed_at"
    if _table_exists("vkpi_sentiment_results"):
        sentiment_join = "LEFT JOIN vkpi_sentiment_results s ON s.id = c.sentiment_id OR s.comment_id = c.id"
        select_sentiment = "s.sentiment, s.brand_attitude, s.emotion, s.analyzed_at"
    return _safe_query(
        f"""
        SELECT
            c.id, c.platform, c.account_id, c.post_id, c.post_table, c.external_post_id,
            c.external_comment_id, c.comment_text, c.author_handle, c.likes_count,
            c.reply_count, c.created_at, c.fetched_at, {select_sentiment}
        FROM vkpi_comments c
        {sentiment_join}
        ORDER BY c.fetched_at DESC, c.id DESC
        LIMIT ?
        """,
        (max(1, min(5000, int(limit or 500))),),
    )


def _market_rows(limit: int) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_competitor_signals"):
        return []
    return _safe_query(
        """
        SELECT
            id, signal_uid, brand, normalized_brand, signal_type, severity, score,
            platform, detail, source_url, review_status, created_at, updated_at
        FROM vkpi_competitor_signals
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, min(1000, int(limit or 200))),),
    )


def build_today_new_signals_v0(
    *,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    bounded_hours = max(1, min(168, int(lookback_hours or DEFAULT_LOOKBACK_HOURS)))
    bounded_limit = max(1, min(500, int(limit or DEFAULT_LIMIT)))
    trend_report = trend_detection_v0.build_trend_detection_v0(
        lookback_days=max(1, int((bounded_hours + 23) / 24)),
        limit=5000,
        top_signals=max(100, bounded_limit),
    )
    return build_today_new_signals_report(
        trend_report=trend_report,
        market_rows=_market_rows(500),
        comment_rows=_comment_rows(5000),
        lookback_hours=bounded_hours,
        limit=bounded_limit,
        now=_now(),
    )


__all__ = [
    "COMMENT_OPPORTUNITY_KEYWORDS",
    "DEFAULT_LIMIT",
    "DEFAULT_LOOKBACK_HOURS",
    "DIGEST_VERSION",
    "_comment_rows",
    "_market_rows",
    "_now",
    "build_today_new_signals_v0",
    "trend_detection_v0",
]
