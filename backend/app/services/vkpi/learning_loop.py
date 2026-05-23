"""P10 read-only learning loop snapshot."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn


SCENARIO = "p10_learning_snapshot"
logger = get_logger(__name__)


def _count(table_name: str) -> int:
    try:
        return int(get_conn().execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()["count"] or 0)
    except Exception as exc:
        logger.warning("vkpi learning snapshot count failed for %s: %s", table_name, exc)
        return 0


def _group_counts(table_name: str, column: str) -> dict[str, int]:
    try:
        rows = get_conn().execute(
            f"""
            SELECT COALESCE(NULLIF({column}, ''), 'unknown') AS key,
                   COUNT(*) AS count
            FROM {table_name}
            GROUP BY COALESCE(NULLIF({column}, ''), 'unknown')
            ORDER BY count DESC, key
            """
        ).fetchall()
    except Exception as exc:
        logger.warning("vkpi learning snapshot group count failed for %s.%s: %s", table_name, column, exc)
        return {}
    return {str(row["key"]): int(row["count"] or 0) for row in rows}


def _outcome_counts() -> dict[str, int]:
    try:
        row = get_conn().execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN was_shortlisted THEN 1 ELSE 0 END) AS shortlisted,
              SUM(CASE WHEN was_rejected THEN 1 ELSE 0 END) AS rejected,
              SUM(CASE WHEN was_claimed THEN 1 ELSE 0 END) AS claimed,
              SUM(CASE WHEN project_created THEN 1 ELSE 0 END) AS project_created,
              SUM(CASE WHEN outreach_sent THEN 1 ELSE 0 END) AS outreach_sent,
              SUM(CASE WHEN reply_received THEN 1 ELSE 0 END) AS reply_received,
              SUM(CASE WHEN content_published THEN 1 ELSE 0 END) AS content_published,
              SUM(CASE WHEN order_attributed THEN 1 ELSE 0 END) AS order_attributed
            FROM vkpi_recommendation_outcomes
            """
        ).fetchone()
    except Exception as exc:
        logger.warning("vkpi learning snapshot outcome count failed: %s", exc)
        return {}
    return {key: int(row[key] or 0) for key in row.keys()}


def build_learning_snapshot(*, json_out: str = "", md_out: str = "") -> dict[str, Any]:
    recommendation_feedback = _count("vkpi_recommendation_feedback")
    memory_feedback = _count("vkpi_memory_feedback")
    competitor_signals = _count("vkpi_competitor_signals")
    competitor_review = _group_counts("vkpi_competitor_signals", "review_status")
    alerts = _group_counts("vkpi_alerts", "status")
    outcomes = _outcome_counts()
    gaps = []
    if recommendation_feedback == 0:
        gaps.append("recommendation_feedback_empty")
    if memory_feedback == 0:
        gaps.append("memory_feedback_empty")
    if int(competitor_review.get("pending_review", 0)) > 0:
        gaps.append("competitor_signals_pending_review")
    if int(alerts.get("open", 0)) > 0:
        gaps.append("open_alerts_need_resolution")
    if int(outcomes.get("total", 0)) > 0 and int(outcomes.get("shortlisted", 0)) == 0:
        gaps.append("recommendation_outcomes_have_no_shortlist_actions")
    payload = {
        "scenario": SCENARIO,
        "provider_calls": False,
        "write_db": False,
        "counts": {
            "recommendation_feedback": recommendation_feedback,
            "memory_feedback": memory_feedback,
            "competitor_signals": competitor_signals,
        },
        "competitor_review": competitor_review,
        "alerts": alerts,
        "recommendation_outcomes": outcomes,
        "readiness": {
            "recommendation_feedback_ready": recommendation_feedback > 0,
            "memory_feedback_ready": memory_feedback > 0,
            "competitor_review_ready": competitor_signals > 0 and int(competitor_review.get("pending_review", 0)) == 0,
            "outcome_data_ready": int(outcomes.get("total", 0)) > 0,
        },
        "gaps": gaps,
    }
    markdown = format_learning_snapshot(payload)
    payload["markdown"] = markdown
    if json_out:
        Path(json_out).write_text(json.dumps({k: v for k, v in payload.items() if k != "markdown"}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if md_out:
        Path(md_out).write_text(markdown, encoding="utf-8")
    return payload


def format_learning_snapshot(payload: dict[str, Any]) -> str:
    lines = [
        "# P10 Learning Snapshot",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
    ]
    for key, value in sorted((payload.get("counts") or {}).items()):
        lines.append(f"{key}={int(value or 0)}")
    for key, value in sorted((payload.get("readiness") or {}).items()):
        lines.append(f"readiness.{key}={str(bool(value)).lower()}")
    lines.append("```")
    lines.extend(["", "## Gaps", ""])
    for gap in payload.get("gaps") or []:
        lines.append(f"- {gap}")
    if not payload.get("gaps"):
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"
