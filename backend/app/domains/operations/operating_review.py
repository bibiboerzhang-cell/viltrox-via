"""Read-only operating review snapshot after v5.3.1 completion."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.db.connection import get_conn


SCENARIO = "vkpi_operating_review"


def _safe_rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        rows = get_conn().execute(sql, params).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _safe_count(table_name: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> int:
    try:
        row = get_conn().execute(f"SELECT COUNT(*) AS count FROM {table_name} {where_sql}", params).fetchone()
    except Exception:
        return 0
    data = dict(row) if row else {}
    return int(data.get("count") or 0)


def _group_counts(table_name: str, column: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> dict[str, int]:
    rows = _safe_rows(
        f"""
        SELECT COALESCE(NULLIF({column}, ''), 'unknown') AS key,
               COUNT(*) AS count
        FROM {table_name}
        {where_sql}
        GROUP BY COALESCE(NULLIF({column}, ''), 'unknown')
        ORDER BY count DESC, key
        """,
        params,
    )
    return {str(row.get("key") or "unknown"): int(row.get("count") or 0) for row in rows}


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed


def _severity_rank_sql(column: str = "severity") -> str:
    return (
        f"CASE LOWER(COALESCE(NULLIF({column}, ''), 'info')) "
        "WHEN 'critical' THEN 0 "
        "WHEN 'danger' THEN 1 "
        "WHEN 'high' THEN 2 "
        "WHEN 'warning' THEN 3 "
        "WHEN 'medium' THEN 4 "
        "ELSE 5 END"
    )


def _open_alerts(limit: int) -> list[dict[str, Any]]:
    rows = _safe_rows(
        f"""
        SELECT id, alert_key, severity, rule_key, target_type, target_id,
               staff_id, title, body, due_at, created_at, updated_at, metadata_json
        FROM vkpi_alerts
        WHERE status='open'
        ORDER BY {_severity_rank_sql()}, created_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    for row in rows:
        row["metadata"] = _loads(row.get("metadata_json"), {})
        row.pop("metadata_json", None)
    return rows


def _pending_competitor_signals(limit: int) -> list[dict[str, Any]]:
    rows = _safe_rows(
        f"""
        SELECT id, normalized_brand, brand, signal_type, severity, score,
               review_status, platform, source_table, source_id, source_row,
               source_url, created_at, product_hints_json, evidence_json
        FROM vkpi_competitor_signals
        WHERE COALESCE(NULLIF(review_status, ''), 'pending_review')='pending_review'
        ORDER BY {_severity_rank_sql()}, score DESC, created_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    for row in rows:
        row["product_hints"] = _loads(row.get("product_hints_json"), [])
        row["evidence"] = _loads(row.get("evidence_json"), {})
        row.pop("product_hints_json", None)
        row.pop("evidence_json", None)
    return rows


def _recommendation_runs_without_feedback(limit: int) -> list[dict[str, Any]]:
    return _safe_rows(
        """
        SELECT
          r.id,
          r.run_uid,
          r.strategy_version,
          r.status,
          r.candidate_count,
          r.recommendation_count,
          r.created_at,
          r.completed_at,
          COUNT(DISTINCT rec.id) AS recommendation_rows,
          COUNT(DISTINCT fb.id) AS feedback_rows
        FROM vkpi_kol_recommendation_runs r
        LEFT JOIN vkpi_kol_recommendations rec ON rec.run_id = r.id
        LEFT JOIN vkpi_recommendation_feedback fb ON fb.recommendation_id = rec.id
        WHERE r.status IN ('previewed', 'completed')
        GROUP BY
          r.id, r.run_uid, r.strategy_version, r.status, r.candidate_count,
          r.recommendation_count, r.created_at, r.completed_at
        HAVING COUNT(DISTINCT rec.id) > 0
           AND COUNT(DISTINCT fb.id) = 0
        ORDER BY r.created_at ASC, r.id ASC
        LIMIT ?
        """,
        (int(limit),),
    )


def _top_work_items(open_alerts: list[dict[str, Any]], pending_signals: list[dict[str, Any]], run_gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for alert in open_alerts[:8]:
        items.append(
            {
                "kind": "open_alert",
                "priority": str(alert.get("severity") or "warning"),
                "source_table": "vkpi_alerts",
                "source_id": alert.get("id"),
                "title": alert.get("title") or alert.get("alert_key"),
                "reason": alert.get("rule_key") or "alert_open",
            }
        )
    for signal in pending_signals[:8]:
        brand = signal.get("normalized_brand") or signal.get("brand") or "unknown"
        items.append(
            {
                "kind": "competitor_signal_review",
                "priority": str(signal.get("severity") or "medium"),
                "source_table": "vkpi_competitor_signals",
                "source_id": signal.get("id"),
                "title": f"{brand} / {signal.get('signal_type') or 'signal'}",
                "reason": "pending_review",
            }
        )
    for run in run_gaps[:5]:
        items.append(
            {
                "kind": "recommendation_feedback_gap",
                "priority": "warning",
                "source_table": "vkpi_kol_recommendation_runs",
                "source_id": run.get("id"),
                "title": run.get("run_uid") or f"run:{run.get('id')}",
                "reason": f"{int(run.get('recommendation_rows') or 0)} recommendations without feedback",
            }
        )
    return items


def build_operating_review(*, limit: int = 25, json_out: str = "", md_out: str = "") -> dict[str, Any]:
    safe_limit = max(1, min(100, int(limit or 25)))
    open_alerts = _open_alerts(safe_limit)
    pending_signals = _pending_competitor_signals(safe_limit)
    run_gaps = _recommendation_runs_without_feedback(safe_limit)
    counts = {
        "open_alerts": _safe_count("vkpi_alerts", "WHERE status='open'"),
        "competitor_signals": _safe_count("vkpi_competitor_signals"),
        "pending_competitor_signals": _safe_count(
            "vkpi_competitor_signals",
            "WHERE COALESCE(NULLIF(review_status, ''), 'pending_review')='pending_review'",
        ),
        "recommendation_feedback": _safe_count("vkpi_recommendation_feedback"),
        "recommendation_outcomes": _safe_count("vkpi_recommendation_outcomes"),
        "memory_feedback": _safe_count("vkpi_memory_feedback"),
    }
    gaps = []
    if counts["open_alerts"] > 0:
        gaps.append("open_alerts_need_resolution")
    if counts["pending_competitor_signals"] > 0:
        gaps.append("competitor_signals_pending_review")
    if counts["recommendation_feedback"] == 0:
        gaps.append("recommendation_feedback_empty")
    if counts["memory_feedback"] == 0:
        gaps.append("memory_feedback_empty")
    if run_gaps:
        gaps.append("recommendation_runs_without_feedback")
    payload: dict[str, Any] = {
        "scenario": SCENARIO,
        "provider_calls": False,
        "write_db": False,
        "counts": counts,
        "alert_status": _group_counts("vkpi_alerts", "status"),
        "alert_rules_open": _group_counts("vkpi_alerts", "rule_key", "WHERE status='open'"),
        "competitor_review_status": _group_counts("vkpi_competitor_signals", "review_status"),
        "competitor_brands_pending": _group_counts(
            "vkpi_competitor_signals",
            "COALESCE(NULLIF(normalized_brand, ''), brand)",
            "WHERE COALESCE(NULLIF(review_status, ''), 'pending_review')='pending_review'",
        ),
        "open_alerts": open_alerts,
        "pending_competitor_signals": pending_signals,
        "recommendation_runs_without_feedback": run_gaps,
        "top_work_items": _top_work_items(open_alerts, pending_signals, run_gaps),
        "gaps": gaps,
    }
    markdown = format_operating_review(payload)
    payload["markdown"] = markdown
    if json_out:
        Path(json_out).write_text(
            json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if md_out:
        Path(md_out).write_text(markdown, encoding="utf-8")
    return payload


def format_operating_review(payload: dict[str, Any]) -> str:
    lines = [
        "# V-KPI Operating Review",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
    ]
    for key, value in sorted((payload.get("counts") or {}).items()):
        lines.append(f"{key}={int(value or 0)}")
    lines.append("```")
    lines.extend(["", "## Top Work Items", ""])
    for idx, item in enumerate(payload.get("top_work_items") or [], start=1):
        lines.append(
            f"{idx}. [{item.get('kind')}] {item.get('title')} "
            f"source={item.get('source_table')}:{item.get('source_id')} reason={item.get('reason')}"
        )
    if not payload.get("top_work_items"):
        lines.append("- none")
    lines.extend(["", "## Gaps", ""])
    for gap in payload.get("gaps") or []:
        lines.append(f"- {gap}")
    if not payload.get("gaps"):
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"
