"""V-KPI alert accessors."""
from __future__ import annotations

import json
from typing import Any
from datetime import datetime, timedelta

from app.db.connection import get_conn
from app.services.vkpi import scope
from app.services.vkpi.schema import ensure_vkpi_schema


def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def list_alerts(status: str = "open", limit: int = 50, *, staff: dict[str, Any] | None = None, staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    limit_i = max(1, min(200, int(limit or 50)))
    params: list[Any] = []
    where_parts: list[str] = []
    if status:
        where_parts.append("status=?")
        params.append(status)
    scoped_staff_id = scope.effective_staff_id(staff, staff_id)
    if scoped_staff_id:
        where_parts.append("(staff_id=? OR staff_id IS NULL)")
        params.append(int(scoped_staff_id))
    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM vkpi_alerts
        {where}
        ORDER BY severity DESC, created_at DESC
        LIMIT ?
        """,
        (*params, limit_i),
    ).fetchall()
    return {"alerts": [dict(row) for row in rows]}


def upsert_alert(
    *,
    alert_key: str,
    title: str,
    body: str = "",
    severity: str = "info",
    target_type: str = "",
    target_id: int | None = None,
    staff_id: int | None = None,
    rule_key: str = "",
    due_at: str | None = None,
    metadata_json: str = "{}",
) -> dict[str, Any]:
    ensure_vkpi_schema()
    now = utcnow()
    conn = get_conn()
    existing = conn.execute("SELECT id FROM vkpi_alerts WHERE alert_key=?", (alert_key,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE vkpi_alerts
            SET severity=?, status='open', target_type=?, target_id=?, staff_id=?,
                title=?, body=?, rule_key=?, due_at=?, metadata_json=?, updated_at=?
            WHERE alert_key=?
            """,
            (severity, target_type, target_id, staff_id, title, body, rule_key, due_at, metadata_json, now, alert_key),
        )
    else:
        conn.execute(
            """
            INSERT INTO vkpi_alerts (
                alert_key, severity, status, target_type, target_id, staff_id,
                title, body, rule_key, due_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (alert_key, severity, "open", target_type, target_id, staff_id, title, body, rule_key, due_at, metadata_json, now, now),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_alerts WHERE alert_key=?", (alert_key,)).fetchone()
    return dict(row) if row else {}


def resolve_alert(alert_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    now = utcnow()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_alerts WHERE id=?", (int(alert_id),)).fetchone()
    if not row:
        raise LookupError("alert not found")
    data = dict(row)
    if data.get("staff_id"):
        scope.assert_staff_access(int(data.get("staff_id") or 0), staff)
    elif not scope.can_view_all(staff):
        raise scope.ScopeDenied("alert scope denied")
    conn.execute("UPDATE vkpi_alerts SET status='resolved', resolved_at=?, updated_at=? WHERE id=?", (now, now, int(alert_id)))
    conn.commit()
    return {"id": int(alert_id), "status": "resolved"}


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
    from app.services.vkpi import comments_collector, sentiment

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


def generate_stalled_project_alerts() -> dict[str, Any]:
    ensure_vkpi_schema()
    # SQLite and the Postgres compat layer both store ISO-like timestamps here.
    rows = get_conn().execute(
        """
        SELECT id, project_name, assigned_staff_id, stage, last_activity_at, updated_at
        FROM vkpi_projects
        WHERE stage_status='active'
          AND stage NOT IN ('closed', 'released', 'lost', 'cancelled')
        ORDER BY updated_at ASC
        LIMIT 200
        """
    ).fetchall()
    created = []
    for row in rows:
        data = dict(row)
        last_touch = str(data.get("last_activity_at") or data.get("updated_at") or "")
        # String check is intentionally conservative for local SQLite. Scheduler
        # can replace this with DB-native interval checks in production.
        if not last_touch:
            continue
        key = f"stalled-project-{data['id']}"
        created.append(
            upsert_alert(
                alert_key=key,
                severity="warning",
                target_type="project",
                target_id=int(data["id"]),
                staff_id=data.get("assigned_staff_id"),
                title=f"Project may be stalled: {data.get('project_name') or data['id']}",
                body=f"Stage {data.get('stage')} last touched at {last_touch}. Review, follow up, release, or reassign.",
                rule_key="project.stalled_review",
            )
        )
    return {"alerts": created, "count": len(created)}


def generate_comment_intelligence_alerts(
    *,
    days: int | None = None,
    min_negative: int | None = None,
    min_critical: int | None = None,
    min_hostile: int | None = None,
) -> dict[str, Any]:
    """Create alerts from analyzed comments without invoking crawlers or LLMs."""
    ensure_vkpi_schema()
    from app.services.vkpi import comments_collector, platform_crawl_settings, sentiment

    comments_collector.ensure_vkpi_comments_schema()
    sentiment.ensure_vkpi_sentiment_schema()

    global_settings = (platform_crawl_settings.comment_alert_settings().get("settings") or {})
    explicit_override = any(value is not None for value in (days, min_negative, min_critical, min_hostile))
    if not bool(global_settings.get("enabled", True)) and not explicit_override:
        return {
            "alerts": [],
            "count": 0,
            "disabled": True,
            "rule_key": "comment_intelligence.negative_or_hostile",
        }

    safe_days = max(1, min(90, int(days or global_settings.get("window_days") or 7)))
    min_negative_i = max(1, min(999, int(min_negative or global_settings.get("min_negative") or 3)))
    min_critical_i = max(1, min(999, int(min_critical or global_settings.get("min_critical") or 2)))
    min_hostile_i = max(1, min(999, int(min_hostile or global_settings.get("min_hostile") or 1)))
    cutoff = (datetime.utcnow() - timedelta(days=safe_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
          c.post_id,
          c.post_table,
          c.platform,
          c.external_post_id,
          COUNT(*) AS flagged_comments,
          SUM(CASE WHEN s.sentiment = 'negative' THEN 1 ELSE 0 END) AS negative_count,
          SUM(CASE WHEN s.brand_attitude = 'critical' THEN 1 ELSE 0 END) AS critical_count,
          SUM(CASE WHEN s.brand_attitude = 'hostile' THEN 1 ELSE 0 END) AS hostile_count,
          MAX(s.analyzed_at) AS latest_analyzed_at
        FROM vkpi_sentiment_results s
        JOIN vkpi_comments c ON c.id = s.comment_id
        WHERE s.analyzed_at >= ?
          AND (
            s.sentiment = 'negative'
            OR s.brand_attitude IN ('critical', 'hostile')
          )
        GROUP BY c.post_id, c.post_table, c.platform, c.external_post_id
        HAVING
          SUM(CASE WHEN s.sentiment = 'negative' THEN 1 ELSE 0 END) >= ?
          OR SUM(CASE WHEN s.brand_attitude = 'critical' THEN 1 ELSE 0 END) >= ?
          OR SUM(CASE WHEN s.brand_attitude = 'hostile' THEN 1 ELSE 0 END) >= ?
        ORDER BY hostile_count DESC, critical_count DESC, negative_count DESC, latest_analyzed_at DESC
        LIMIT 100
        """,
        (cutoff, min_negative_i, min_critical_i, min_hostile_i),
    ).fetchall()

    created: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        post_id = int(data.get("post_id") or 0)
        post_table = str(data.get("post_table") or "industry_posts")
        platform = str(data.get("platform") or "unknown")
        hostile_count = int(data.get("hostile_count") or 0)
        critical_count = int(data.get("critical_count") or 0)
        negative_count = int(data.get("negative_count") or 0)
        severity = "danger" if hostile_count >= min_hostile_i else "warning"
        key_ref = post_id or str(data.get("external_post_id") or "unknown")
        alert_key = f"comment-intelligence-{post_table}-{key_ref}"
        created.append(
            upsert_alert(
                alert_key=alert_key,
                severity=severity,
                target_type=post_table,
                target_id=post_id or None,
                title=f"Comment intelligence risk on {platform} post",
                body=(
                    f"{negative_count} negative, {critical_count} critical, "
                    f"{hostile_count} hostile comments in the last {safe_days} days. "
                    "Review the post and decide whether to respond, suppress, or escalate."
                ),
                rule_key="comment_intelligence.negative_or_hostile",
                metadata_json=json.dumps(
                    {
                        "platform": platform,
                        "post_table": post_table,
                        "post_id": post_id,
                        "external_post_id": data.get("external_post_id"),
                        "negative_count": negative_count,
                        "critical_count": critical_count,
                        "hostile_count": hostile_count,
                        "flagged_comments": int(data.get("flagged_comments") or 0),
                        "window_days": safe_days,
                        "thresholds": {
                            "min_negative": min_negative_i,
                            "min_critical": min_critical_i,
                            "min_hostile": min_hostile_i,
                        },
                        "latest_analyzed_at": data.get("latest_analyzed_at"),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        )

    return {
        "alerts": created,
        "count": len(created),
        "window_days": safe_days,
        "thresholds": {
            "min_negative": min_negative_i,
            "min_critical": min_critical_i,
            "min_hostile": min_hostile_i,
        },
        "rule_key": "comment_intelligence.negative_or_hostile",
    }


def generate_alerts() -> dict[str, Any]:
    """Run all currently enabled alert rules."""
    stalled = generate_stalled_project_alerts()
    comment_intelligence = generate_comment_intelligence_alerts()
    alerts = (stalled.get("alerts") or []) + (comment_intelligence.get("alerts") or [])
    return {
        "alerts": alerts,
        "count": len(alerts),
        "stalled_projects": stalled,
        "comment_intelligence": comment_intelligence,
    }
