"""Alert evidence drilldown helpers."""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema


def _parse_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def get_alert_detail(alert_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return source rows behind an alert for evidence-first drilldown."""
    ensure_vkpi_schema()
    from app.domains.comments import collector as comments_collector
    import app.domains.comments.sentiment as sentiment

    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_alerts WHERE id=?", (int(alert_id),)).fetchone()
    if not row:
        raise LookupError("alert not found")
    alert = dict(row)
    if alert.get("staff_id"):
        scope.assert_staff_access(int(alert.get("staff_id") or 0), staff)
    elif not scope.can_view_all(staff):
        raise scope.ScopeDenied("alert scope denied")

    metadata = _parse_metadata(alert.get("metadata_json"))
    rule_key = str(alert.get("rule_key") or "")
    target_type = str(alert.get("target_type") or metadata.get("post_table") or "")
    target_id = int(alert.get("target_id") or metadata.get("post_id") or 0)
    post_table = str(metadata.get("post_table") or target_type or "industry_posts")

    post: dict[str, Any] | None = None
    account: dict[str, Any] | None = None
    comments: list[dict[str, Any]] = []

    if target_id and (target_type == "industry_posts" or post_table == "industry_posts" or rule_key.startswith("comment_intelligence")):
        post_row = conn.execute("SELECT * FROM vkpi_industry_posts WHERE id=?", (target_id,)).fetchone()
        post = dict(post_row) if post_row else None
        account_id = int((post or {}).get("account_id") or metadata.get("account_id") or 0)
        if account_id:
            account_row = conn.execute("SELECT * FROM vkpi_industry_accounts WHERE id=?", (account_id,)).fetchone()
            account = dict(account_row) if account_row else None

    if rule_key.startswith("comment_intelligence") and target_id:
        comments_collector.ensure_vkpi_comments_schema()
        sentiment.ensure_vkpi_sentiment_schema()
        comment_rows = conn.execute(
            """
            SELECT
              c.id,
              c.account_id,
              c.post_id,
              c.post_table,
              c.external_post_id,
              c.platform,
              c.external_comment_id,
              c.comment_text,
              c.language_detected,
              c.author_handle,
              c.author_id,
              c.likes_count,
              c.reply_count,
              c.created_at,
              c.fetched_at,
              s.sentiment,
              s.sentiment_confidence,
              s.emotion,
              s.emotion_confidence,
              s.brand_attitude,
              s.brand_attitude_confidence,
              s.llm_provider,
              s.llm_model,
              s.prompt_version,
              s.analyzed_at
            FROM vkpi_comments c
            LEFT JOIN vkpi_sentiment_results s ON s.comment_id = c.id
            WHERE c.post_id = ?
              AND c.post_table = ?
              AND (
                s.sentiment = 'negative'
                OR s.brand_attitude IN ('critical', 'hostile')
              )
            ORDER BY
              CASE WHEN s.brand_attitude = 'hostile' THEN 0 ELSE 1 END,
              CASE WHEN s.brand_attitude = 'critical' THEN 0 ELSE 1 END,
              CASE WHEN s.sentiment = 'negative' THEN 0 ELSE 1 END,
              s.analyzed_at DESC
            LIMIT 50
            """,
            (target_id, post_table or "industry_posts"),
        ).fetchall()
        comments = [dict(item) for item in comment_rows]

    return {
        "alert": alert,
        "metadata": metadata,
        "post": post,
        "account": account,
        "comments": comments,
        "source_summary": {
            "rule_key": rule_key,
            "target_type": target_type,
            "target_id": target_id,
            "post_table": post_table,
            "comment_count": len(comments),
            "has_post": bool(post),
            "has_account": bool(account),
        },
    }
