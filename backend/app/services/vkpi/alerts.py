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


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)


def _resolve_open_alert(conn: Any, alert_key: str) -> bool:
    now = utcnow()
    existing_open = conn.execute(
        "SELECT id FROM vkpi_alerts WHERE alert_key=? AND status='open'",
        (alert_key,),
    ).fetchone()
    conn.execute(
        """
        UPDATE vkpi_alerts
        SET status='resolved', resolved_at=?, updated_at=?
        WHERE alert_key=? AND status='open'
        """,
        (now, now, alert_key),
    )
    return bool(existing_open)


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


def generate_content_brain_backlog_alerts(
    *,
    min_pending: int = 5,
    coverage_warning: float = 0.8,
) -> dict[str, Any]:
    """Create or clear an alert when P6 content brain analysis has pending backlog."""
    ensure_vkpi_schema()
    alert_key = "content-brain-analysis-backlog"
    rule_key = "content_brain.analysis_backlog"
    conn = get_conn()
    if not _table_exists("vkpi_industry_posts"):
        cleared = _resolve_open_alert(conn, alert_key)
        conn.commit()
        return {
            "alerts": [],
            "count": 0,
            "cleared": [alert_key] if cleared else [],
            "cleared_count": 1 if cleared else 0,
            "schema_ready": False,
            "rule_key": rule_key,
        }

    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(analysis_status, ''), 'pending') AS status,
               COUNT(*) AS count
        FROM vkpi_industry_posts
        GROUP BY COALESCE(NULLIF(analysis_status, ''), 'pending')
        """
    ).fetchall()
    status_counts = {str(row["status"]): int(row["count"] or 0) for row in rows}
    post_count = sum(status_counts.values())
    done_count = status_counts.get("done", 0)
    pending_count = post_count - done_count
    coverage_ratio = round(done_count / post_count, 4) if post_count else 1.0
    pending_row = conn.execute(
        """
        SELECT MIN(published_at) AS oldest_pending_at,
               MAX(published_at) AS newest_pending_at
        FROM vkpi_industry_posts
        WHERE COALESCE(NULLIF(analysis_status, ''), 'pending') != 'done'
        """
    ).fetchone()
    should_alert = bool(post_count) and (pending_count >= int(min_pending) or coverage_ratio < float(coverage_warning))
    if not should_alert:
        cleared = _resolve_open_alert(conn, alert_key)
        conn.commit()
        return {
            "alerts": [],
            "count": 0,
            "cleared": [alert_key] if cleared else [],
            "cleared_count": 1 if cleared else 0,
            "post_count": post_count,
            "pending_count": pending_count,
            "coverage_ratio": coverage_ratio,
            "rule_key": rule_key,
        }

    severity = "danger" if pending_count >= max(10, int(min_pending) * 2) or coverage_ratio < 0.25 else "warning"
    alert = upsert_alert(
        alert_key=alert_key,
        severity=severity,
        target_type="content_brain",
        target_id=None,
        title=f"Content brain analysis backlog: {pending_count} pending posts",
        body=(
            f"{pending_count} of {post_count} industry posts are not analyzed. "
            f"Coverage is {coverage_ratio:.0%}; warning threshold is {float(coverage_warning):.0%}."
        ),
        rule_key=rule_key,
        metadata_json=json.dumps(
            {
                "post_count": post_count,
                "done_count": done_count,
                "pending_count": pending_count,
                "coverage_ratio": coverage_ratio,
                "status_counts": status_counts,
                "min_pending": int(min_pending),
                "coverage_warning": float(coverage_warning),
                "oldest_pending_at": (dict(pending_row) if pending_row else {}).get("oldest_pending_at"),
                "newest_pending_at": (dict(pending_row) if pending_row else {}).get("newest_pending_at"),
            },
            ensure_ascii=False,
            default=str,
        ),
    )
    return {
        "alerts": [alert],
        "count": 1,
        "post_count": post_count,
        "pending_count": pending_count,
        "coverage_ratio": coverage_ratio,
        "severity": severity,
        "rule_key": rule_key,
    }


def generate_budget_guard_alerts() -> dict[str, Any]:
    """Create or clear alerts for AI/provider budget warning and hard-stop scopes."""
    ensure_vkpi_schema()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT scope, cap_usd, current_spend, warning_at, hard_stop_at,
               reset_at, fallback_action, metadata_json
        FROM vkpi_provider_budget_caps
        ORDER BY scope
        """
    ).fetchall()
    created: list[dict[str, Any]] = []
    cleared: list[str] = []
    for row in rows:
        data = dict(row)
        scope_key = str(data.get("scope") or "").strip()
        if not scope_key:
            continue
        cap = float(data.get("cap_usd") or 0)
        spend = float(data.get("current_spend") or 0)
        warning_at = float(data.get("warning_at") or 0.8)
        hard_stop_at = float(data.get("hard_stop_at") or 1.0)
        ratio = (spend / cap) if cap > 0 else 0.0
        hard_stopped = cap > 0 and ratio >= hard_stop_at
        warning = cap > 0 and ratio >= warning_at
        alert_key = f"budget-guard-{scope_key}"
        if not hard_stopped and not warning:
            if _resolve_open_alert(conn, alert_key):
                cleared.append(alert_key)
            continue
        severity = "danger" if hard_stopped else "warning"
        state = "hard stop" if hard_stopped else "warning"
        created.append(
            upsert_alert(
                alert_key=alert_key,
                severity=severity,
                target_type="budget_scope",
                target_id=None,
                title=f"Budget Guard {state}: {scope_key}",
                body=(
                    f"{scope_key} spend is ${spend:.4f} of ${cap:.2f} "
                    f"({ratio:.0%}). Fallback action: {data.get('fallback_action') or '-'}."
                ),
                rule_key="budget_guard.warning_or_hard_stop",
                metadata_json=json.dumps(
                    {
                        "scope": scope_key,
                        "cap_usd": cap,
                        "current_spend": spend,
                        "usage_ratio": ratio,
                        "warning_at": warning_at,
                        "hard_stop_at": hard_stop_at,
                        "hard_stopped": hard_stopped,
                        "warning": warning,
                        "reset_at": data.get("reset_at"),
                        "fallback_action": data.get("fallback_action"),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        )
    conn.commit()
    return {
        "alerts": created,
        "count": len(created),
        "cleared": cleared,
        "cleared_count": len(cleared),
        "rule_key": "budget_guard.warning_or_hard_stop",
    }


def generate_alerts() -> dict[str, Any]:
    """Run all currently enabled alert rules."""
    stalled = generate_stalled_project_alerts()
    comment_intelligence = generate_comment_intelligence_alerts()
    content_brain = generate_content_brain_backlog_alerts()
    budget_guard = generate_budget_guard_alerts()
    alerts = (
        (stalled.get("alerts") or [])
        + (comment_intelligence.get("alerts") or [])
        + (content_brain.get("alerts") or [])
        + (budget_guard.get("alerts") or [])
    )
    return {
        "alerts": alerts,
        "count": len(alerts),
        "stalled_projects": stalled,
        "comment_intelligence": comment_intelligence,
        "content_brain": content_brain,
        "budget_guard": budget_guard,
    }
