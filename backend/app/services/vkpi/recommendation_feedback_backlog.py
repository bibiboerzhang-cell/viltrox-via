"""Read-only P10 recommendation feedback backlog snapshot."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


SCENARIO = "p10_recommendation_feedback_backlog"
OUTCOME_FLAGS = (
    ("was_shortlisted", "shortlisted"),
    ("was_rejected", "rejected"),
    ("was_claimed", "claimed"),
    ("project_created", "project_created"),
    ("outreach_sent", "outreach_sent"),
    ("reply_received", "reply_received"),
    ("agreement_reached", "agreement_reached"),
    ("content_published", "content_published"),
    ("order_attributed", "order_attributed"),
)


def _safe_limit(value: int | None) -> int:
    try:
        parsed = int(value or 100)
    except (TypeError, ValueError):
        parsed = 100
    return max(1, min(500, parsed))


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _outcome_status(row: dict[str, Any]) -> dict[str, Any]:
    flags = [label for column, label in OUTCOME_FLAGS if _truthy(row.get(column))]
    first_action_at = str(row.get("first_action_at") or "")
    return {
        "has_outcome": bool(flags),
        "flags": flags,
        "first_action_at": first_action_at,
        "reject_reason": str(row.get("reject_reason") or ""),
    }


def _suggestion(row: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    flags = set(outcome.get("flags") or [])
    status = str(row.get("recommendation_status") or "").strip().lower()
    reasons: list[str] = []
    confidence = 0.5
    suggested_action = "needs_human_review"
    suggested_feedback_type = "needs_review"

    if "rejected" in flags or status == "rejected":
        suggested_action = "capture_rejection_feedback"
        suggested_feedback_type = "reject"
        confidence = 0.9 if "rejected" in flags else 0.75
        reasons.append("recommendation_has_rejection_outcome" if "rejected" in flags else "recommendation_status_rejected")
    elif "shortlisted" in flags or status == "shortlisted":
        suggested_action = "capture_shortlist_feedback"
        suggested_feedback_type = "shortlist"
        confidence = 0.9 if "shortlisted" in flags else 0.75
        reasons.append("recommendation_has_shortlist_outcome" if "shortlisted" in flags else "recommendation_status_shortlisted")
    elif flags.intersection({"claimed", "project_created", "outreach_sent", "reply_received", "agreement_reached", "content_published", "order_attributed"}):
        suggested_action = "review_positive_business_signal"
        suggested_feedback_type = "positive_signal"
        confidence = 0.75
        reasons.append("business_outcome_exists_without_feedback")
    else:
        reasons.append("no_feedback_or_business_action")

    if status in {"recommended", "previewed"}:
        reasons.append(f"status_{status}")
    if not row.get("feedback_rows"):
        reasons.append("feedback_rows_zero")
    return {
        "suggested_action": suggested_action,
        "suggested_feedback_type": suggested_feedback_type,
        "confidence": confidence,
        "reasons": reasons,
        "write_allowed": False,
        "operator_note": "Review in the recommendation surface before writing feedback.",
    }


def _run_where(run_uid: str) -> tuple[str, list[Any]]:
    clean = str(run_uid or "").strip()
    if not clean:
        return "", []
    return "AND r.run_uid=?", [clean]


def _missing_feedback_rows(*, run_uid: str = "", limit: int = 100) -> list[dict[str, Any]]:
    where_run, params = _run_where(run_uid)
    rows = get_conn().execute(
        f"""
        SELECT
          rec.id AS recommendation_id,
          rec.recommendation_uid,
          rec.run_id,
          rec.launch_id,
          rec.kol_pool_id,
          rec.platform,
          rec.handle,
          rec.display_name,
          rec.score,
          rec.rank,
          rec.status AS recommendation_status,
          rec.feature_snapshot_json,
          rec.scoring_breakdown_json,
          rec.explanation_json,
          rec.created_at AS recommendation_created_at,
          rec.updated_at AS recommendation_updated_at,
          r.run_uid,
          r.strategy_version,
          r.status AS run_status,
          r.candidate_count,
          r.recommendation_count,
          r.filters_json,
          r.created_at AS run_created_at,
          r.completed_at AS run_completed_at,
          l.name AS launch_name,
          l.product_name AS launch_product_name,
          l.product_sku AS launch_product_sku,
          COUNT(DISTINCT fb.id) AS feedback_rows,
          MAX(CASE WHEN o.was_shortlisted THEN 1 ELSE 0 END) AS was_shortlisted,
          MAX(CASE WHEN o.was_rejected THEN 1 ELSE 0 END) AS was_rejected,
          MAX(CASE WHEN o.was_claimed THEN 1 ELSE 0 END) AS was_claimed,
          MAX(CASE WHEN o.project_created THEN 1 ELSE 0 END) AS project_created,
          MAX(CASE WHEN o.outreach_sent THEN 1 ELSE 0 END) AS outreach_sent,
          MAX(CASE WHEN o.reply_received THEN 1 ELSE 0 END) AS reply_received,
          MAX(CASE WHEN o.agreement_reached THEN 1 ELSE 0 END) AS agreement_reached,
          MAX(CASE WHEN o.content_published THEN 1 ELSE 0 END) AS content_published,
          MAX(CASE WHEN o.order_attributed THEN 1 ELSE 0 END) AS order_attributed,
          MIN(o.first_action_at) AS first_action_at,
          MAX(o.reject_reason) AS reject_reason
        FROM vkpi_kol_recommendations rec
        INNER JOIN vkpi_kol_recommendation_runs r ON r.id = rec.run_id
        LEFT JOIN vkpi_product_launches l ON l.id = rec.launch_id
        LEFT JOIN vkpi_recommendation_feedback fb ON fb.recommendation_id = rec.id
        LEFT JOIN vkpi_recommendation_outcomes o ON o.recommendation_id = rec.id
        WHERE r.status IN ('previewed', 'completed')
          {where_run}
        GROUP BY
          rec.id, rec.recommendation_uid, rec.run_id, rec.launch_id, rec.kol_pool_id,
          rec.platform, rec.handle, rec.display_name, rec.score, rec.rank, rec.status,
          rec.feature_snapshot_json, rec.scoring_breakdown_json, rec.explanation_json,
          rec.created_at, rec.updated_at, r.run_uid, r.strategy_version, r.status,
          r.candidate_count, r.recommendation_count, r.filters_json, r.created_at,
          r.completed_at, l.name, l.product_name, l.product_sku
        HAVING COUNT(DISTINCT fb.id) = 0
        ORDER BY r.created_at DESC, rec.rank ASC, rec.id ASC
        LIMIT ?
        """,
        (*params, _safe_limit(limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def _summary_counts(*, run_uid: str = "") -> dict[str, Any]:
    where_run, params = _run_where(run_uid)
    row = get_conn().execute(
        f"""
        SELECT
          COUNT(DISTINCT rec.id) AS recommendation_rows,
          COUNT(DISTINCT CASE WHEN fb.id IS NULL THEN rec.id END) AS missing_feedback_rows,
          COUNT(DISTINCT CASE WHEN fb.id IS NOT NULL THEN rec.id END) AS with_feedback_rows,
          COUNT(DISTINCT r.id) AS run_count
        FROM vkpi_kol_recommendations rec
        INNER JOIN vkpi_kol_recommendation_runs r ON r.id = rec.run_id
        LEFT JOIN vkpi_recommendation_feedback fb ON fb.recommendation_id = rec.id
        WHERE r.status IN ('previewed', 'completed')
          {where_run}
        """,
        tuple(params),
    ).fetchone()
    if not row:
        return {"recommendation_rows": 0, "missing_feedback_rows": 0, "with_feedback_rows": 0, "run_count": 0}
    return {key: int(row[key] or 0) for key in row.keys()}


def _run_counts(*, run_uid: str = "") -> list[dict[str, Any]]:
    where_run, params = _run_where(run_uid)
    rows = get_conn().execute(
        f"""
        SELECT
          r.id,
          r.run_uid,
          r.strategy_version,
          r.status,
          COUNT(DISTINCT rec.id) AS recommendation_rows,
          COUNT(DISTINCT fb.id) AS feedback_rows,
          COUNT(DISTINCT CASE WHEN fb.id IS NULL THEN rec.id END) AS missing_feedback_rows
        FROM vkpi_kol_recommendation_runs r
        LEFT JOIN vkpi_kol_recommendations rec ON rec.run_id = r.id
        LEFT JOIN vkpi_recommendation_feedback fb ON fb.recommendation_id = rec.id
        WHERE r.status IN ('previewed', 'completed')
          {where_run}
        GROUP BY r.id, r.run_uid, r.strategy_version, r.status
        HAVING COUNT(DISTINCT rec.id) > 0
        ORDER BY missing_feedback_rows DESC, r.created_at DESC, r.id DESC
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def build_recommendation_feedback_backlog(
    *,
    run_uid: str = "",
    limit: int = 100,
    json_out: str = "",
    md_out: str = "",
) -> dict[str, Any]:
    """Build a read-only backlog of recommendations that still need feedback."""

    ensure_vkpi_product_industry_schema()
    rows = _missing_feedback_rows(run_uid=run_uid, limit=limit)
    items: list[dict[str, Any]] = []
    action_counts: dict[str, int] = {}
    for row in rows:
        feature_snapshot = _loads(row.get("feature_snapshot_json"), {}) or {}
        scoring_breakdown = _loads(row.get("scoring_breakdown_json"), {}) or {}
        explanation = _loads(row.get("explanation_json"), {}) or {}
        filters = _loads(row.get("filters_json"), {}) or {}
        outcome = _outcome_status(row)
        suggestion = _suggestion(row, outcome)
        action = str(suggestion.get("suggested_action") or "needs_human_review")
        action_counts[action] = action_counts.get(action, 0) + 1
        items.append(
            {
                "recommendation_id": int(row.get("recommendation_id") or 0),
                "recommendation_uid": row.get("recommendation_uid") or "",
                "run_uid": row.get("run_uid") or "",
                "strategy_version": row.get("strategy_version") or "",
                "rank": int(row.get("rank") or 0),
                "score": float(row.get("score") or 0),
                "status": row.get("recommendation_status") or "",
                "kol": {
                    "kol_pool_id": row.get("kol_pool_id"),
                    "platform": row.get("platform") or "",
                    "handle": row.get("handle") or "",
                    "display_name": row.get("display_name") or "",
                },
                "launch": {
                    "launch_id": row.get("launch_id"),
                    "name": row.get("launch_name") or "",
                    "product_name": row.get("launch_product_name") or "",
                    "product_sku": row.get("launch_product_sku") or "",
                    "filters": filters,
                },
                "feedback": {
                    "feedback_rows": int(row.get("feedback_rows") or 0),
                    "missing_feedback": True,
                },
                "outcome": outcome,
                "suggestion": suggestion,
                "evidence": {
                    "feature_snapshot": feature_snapshot,
                    "score_breakdown": scoring_breakdown,
                    "evidence_pro": explanation.get("evidence_pro") or [],
                    "evidence_con": explanation.get("evidence_con") or [],
                    "recommendation_reason": explanation.get("recommendation_reason") or {},
                },
            }
        )
    payload: dict[str, Any] = {
        "scenario": SCENARIO,
        "provider_calls": False,
        "write_db": False,
        "run_uid": str(run_uid or ""),
        "limit": _safe_limit(limit),
        "summary": _summary_counts(run_uid=run_uid),
        "runs": _run_counts(run_uid=run_uid),
        "suggested_actions": action_counts,
        "items": items,
    }
    markdown = format_recommendation_feedback_backlog(payload)
    payload["markdown"] = markdown
    if json_out:
        Path(json_out).write_text(json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if md_out:
        Path(md_out).write_text(markdown, encoding="utf-8")
    return payload


def format_recommendation_feedback_backlog(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# P10 Recommendation Feedback Backlog",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
        f"run_uid={payload.get('run_uid') or 'all'}",
        f"recommendation_rows={int(summary.get('recommendation_rows') or 0)}",
        f"missing_feedback_rows={int(summary.get('missing_feedback_rows') or 0)}",
        f"with_feedback_rows={int(summary.get('with_feedback_rows') or 0)}",
        f"returned={len(payload.get('items') or [])}",
    ]
    for action, count in sorted((payload.get("suggested_actions") or {}).items()):
        lines.append(f"suggested.{action}={int(count or 0)}")
    lines.extend(["```", "", "## Runs", ""])
    for run in payload.get("runs") or []:
        lines.append(
            f"- {run.get('run_uid')}: status={run.get('status')} "
            f"strategy={run.get('strategy_version')} missing_feedback={int(run.get('missing_feedback_rows') or 0)} "
            f"feedback_rows={int(run.get('feedback_rows') or 0)}"
        )
    if not payload.get("runs"):
        lines.append("- none")
    lines.extend(["", "## Backlog", ""])
    for item in payload.get("items") or []:
        kol = item.get("kol") or {}
        suggestion = item.get("suggestion") or {}
        outcome = item.get("outcome") or {}
        reasons = ",".join(suggestion.get("reasons") or [])
        flags = ",".join(outcome.get("flags") or []) or "none"
        lines.append(
            f"- rec_id={item.get('recommendation_id')} run={item.get('run_uid')} "
            f"rank={item.get('rank')} score={float(item.get('score') or 0):.2f} "
            f"kol={kol.get('platform')}:{kol.get('handle')} "
            f"action={suggestion.get('suggested_action')} confidence={float(suggestion.get('confidence') or 0):.2f} "
            f"outcome={flags} reasons={reasons}"
        )
    if not payload.get("items"):
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"
