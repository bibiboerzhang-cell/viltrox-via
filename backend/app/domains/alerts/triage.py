"""Deterministic alert triage helpers."""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn
from app.domains.alerts.common import utcnow
from app.domains.access import scope
from app.services.vkpi._utils import json_dumps
from app.services.vkpi.schema import ensure_vkpi_schema


def _safe_limit(value: int, *, default: int = 50, ceiling: int = 200) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(ceiling, parsed))


def _json(value: Any) -> str:
    return json_dumps(value or {})


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


def _alert_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("alert_key", "rule_key", "target_type", "title", "body", "metadata_json")
    ).lower()


def _triage_suggestion(row: dict[str, Any]) -> dict[str, Any]:
    rule_key = str(row.get("rule_key") or "")
    text = _alert_text(row)
    reasons: list[str] = []
    if rule_key == "project.stalled_review" and "smoke" in text:
        reasons.append("smoke_fixture_stalled_project")
        return {"suggested_action": "resolve", "confidence": 0.95, "reasons": reasons}
    if rule_key == "project.stalled_review":
        reasons.append("stalled_project_needs_owner_review")
        return {"suggested_action": "keep_open", "confidence": 0.7, "reasons": reasons}
    if rule_key == "recommendation.review_gap":
        reasons.append("recommendation_run_needs_feedback")
        return {"suggested_action": "keep_open", "confidence": 0.9, "reasons": reasons}
    if rule_key == "content_brain.analysis_backlog":
        reasons.append("content_brain_posts_need_analysis")
        return {"suggested_action": "keep_open", "confidence": 0.9, "reasons": reasons}
    reasons.append("manual_review_required")
    return {"suggested_action": "keep_open", "confidence": 0.5, "reasons": reasons}


def build_alert_triage_suggestions(*, status: str = "open", limit: int = 100) -> dict[str, Any]:
    """Return deterministic alert triage suggestions without mutating alerts."""
    ensure_vkpi_schema()
    safe_limit = _safe_limit(limit, default=100)
    rows = get_conn().execute(
        """
        SELECT *
        FROM vkpi_alerts
        WHERE status=?
        ORDER BY
          CASE severity WHEN 'danger' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
          updated_at DESC,
          id DESC
        LIMIT ?
        """,
        (status, safe_limit),
    ).fetchall()
    suggestions = []
    action_counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    for row in rows:
        item = dict(row)
        suggestion = _triage_suggestion(item)
        action = str(suggestion.get("suggested_action") or "keep_open")
        rule_key = str(item.get("rule_key") or "manual")
        action_counts[action] = action_counts.get(action, 0) + 1
        rule_counts[rule_key] = rule_counts.get(rule_key, 0) + 1
        suggestions.append(
            {
                "alert_id": item.get("id"),
                "alert_key": item.get("alert_key"),
                "rule_key": rule_key,
                "severity": item.get("severity"),
                "target_type": item.get("target_type"),
                "target_id": item.get("target_id"),
                "title": item.get("title"),
                "suggested_action": action,
                "confidence": suggestion.get("confidence"),
                "reasons": suggestion.get("reasons") or [],
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            }
        )
    return {
        "scenario": "p7_alert_triage_suggestions",
        "provider_calls": False,
        "write_db": False,
        "filters": {"status": status, "limit": safe_limit},
        "count": len(suggestions),
        "suggested_actions": action_counts,
        "rule_distribution": rule_counts,
        "suggestions": suggestions,
    }


def _resolve_alert_for_cli(alert_id: int, *, metadata: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_alerts WHERE id=?", (int(alert_id),)).fetchone()
    if not row:
        raise LookupError("alert not found")
    data = dict(row)
    existing_metadata = _parse_metadata(data.get("metadata_json"))
    existing_metadata.setdefault("triage_history", [])
    history = existing_metadata.get("triage_history")
    if not isinstance(history, list):
        history = []
    history.append(metadata)
    existing_metadata["triage_history"] = history[-20:]
    existing_metadata["triage_decision"] = metadata
    conn.execute(
        """
        UPDATE vkpi_alerts
        SET status='resolved', resolved_at=?, updated_at=?, metadata_json=?
        WHERE id=?
        """,
        (now, now, _json(existing_metadata), int(alert_id)),
    )
    conn.commit()
    return {"id": int(alert_id), "status": "resolved"}


def _resolve_alert_for_staff(alert_id: int, *, staff: dict[str, Any]) -> dict[str, Any]:
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


def apply_alert_triage_suggestions(
    *,
    status: str = "open",
    suggested_action: str = "resolve",
    limit: int = 100,
    staff: dict[str, Any] | None = None,
    actor: str = "cli",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply or preview alert triage suggestions."""
    suggestions = build_alert_triage_suggestions(status=status, limit=limit)
    target_action = str(suggested_action or "resolve")
    candidates = [
        item
        for item in suggestions.get("suggestions") or []
        if str(item.get("suggested_action") or "") == target_action
    ]
    applied: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    now = utcnow()
    for item in candidates:
        alert_id = int(item.get("alert_id") or 0)
        if not alert_id:
            continue
        try:
            if not dry_run:
                metadata = {
                    "action": target_action,
                    "actor": actor,
                    "reasons": item.get("reasons") or [],
                    "confidence": item.get("confidence"),
                    "decided_at": now,
                    "source": "alert_triage_suggestions",
                }
                if staff is not None:
                    _resolve_alert_for_staff(alert_id, staff=staff)
                else:
                    _resolve_alert_for_cli(alert_id, metadata=metadata)
            applied.append(
                {
                    "alert_id": alert_id,
                    "alert_key": item.get("alert_key"),
                    "rule_key": item.get("rule_key"),
                    "title": item.get("title"),
                    "suggested_action": target_action,
                    "confidence": item.get("confidence"),
                    "dry_run": bool(dry_run),
                    "write_db": not bool(dry_run),
                }
            )
        except Exception as exc:
            errors.append({"alert_id": alert_id, "error": str(exc)})
    return {
        "scenario": "p7_alert_triage_apply_suggestions",
        "provider_calls": False,
        "write_db": not bool(dry_run),
        "dry_run": bool(dry_run),
        "filters": {"status": status, "suggested_action": target_action, "limit": _safe_limit(limit, default=100)},
        "candidate_count": len(candidates),
        "applied_count": len(applied),
        "error_count": len(errors),
        "applied": applied,
        "errors": errors,
    }
