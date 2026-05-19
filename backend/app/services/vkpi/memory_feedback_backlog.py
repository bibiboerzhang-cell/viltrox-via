"""Read-only P10 Memory feedback backlog snapshot."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.memory import ensure_memory_schema


SCENARIO = "p10_memory_feedback_backlog"
RELEVANT_FACT_TYPES = (
    "sync_status",
    "weak_label",
    "review_state",
    "contact_status",
    "risk_flag",
    "evidence_count",
)


def _safe_limit(value: int | None) -> int:
    try:
        parsed = int(value or 100)
    except (TypeError, ValueError):
        parsed = 100
    return max(1, min(500, parsed))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "0")))
    except Exception:
        return 0


def _feedback_counts() -> dict[int, dict[str, int]]:
    rows = get_conn().execute(
        """
        SELECT entity_id,
               COUNT(*) AS total,
               SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_count,
               SUM(CASE WHEN status IN ('resolved', 'dismissed') THEN 1 ELSE 0 END) AS closed_count
        FROM vkpi_memory_feedback
        WHERE entity_id IS NOT NULL
        GROUP BY entity_id
        """
    ).fetchall()
    return {
        int(row["entity_id"]): {
            "total": int(row["total"] or 0),
            "open": int(row["open_count"] or 0),
            "closed": int(row["closed_count"] or 0),
        }
        for row in rows
    }


def _load_entity_fact_rows(entity_type: str) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in RELEVANT_FACT_TYPES)
    rows = get_conn().execute(
        f"""
        SELECT
          e.id AS entity_id,
          e.entity_uid,
          e.entity_type,
          e.identity_key,
          e.display_name,
          e.status AS entity_status,
          e.confidence_score,
          e.identity_json,
          e.metadata_json,
          e.source_table,
          e.source_id,
          e.updated_at,
          f.id AS fact_id,
          f.fact_type,
          f.fact_key,
          f.fact_value_text,
          f.confidence_score AS fact_confidence_score,
          f.source_ref,
          f.source_table AS fact_source_table,
          f.source_id AS fact_source_id
        FROM vkpi_memory_entities e
        LEFT JOIN vkpi_memory_facts f
          ON f.entity_id=e.id
         AND f.fact_type IN ({placeholders})
        WHERE e.entity_type=?
        ORDER BY e.updated_at DESC, e.id DESC, f.fact_type, f.id
        """,
        (*RELEVANT_FACT_TYPES, _text(entity_type) or "kol"),
    ).fetchall()
    return [dict(row) for row in rows]


def _entity_backlog_item(entity: dict[str, Any], feedback_counts: dict[str, int]) -> dict[str, Any] | None:
    facts = entity.get("facts") or {}
    risk_flags = facts.get("risk_flag") or []
    sync_status = _text((facts.get("sync_status") or [{}])[0].get("value") if facts.get("sync_status") else "")
    weak_label = _text((facts.get("weak_label") or [{}])[0].get("value") if facts.get("weak_label") else "")
    review_state = _text((facts.get("review_state") or [{}])[0].get("value") if facts.get("review_state") else "")
    contact_status = _text((facts.get("contact_status") or [{}])[0].get("value") if facts.get("contact_status") else "")
    evidence_values = [_int(item.get("value")) for item in facts.get("evidence_count") or []]
    evidence_count = max(evidence_values) if evidence_values else 0
    reasons: list[str] = []
    priority_score = 0
    suggested_action = ""
    suggested_feedback_type = ""
    severity = "low"

    if risk_flags or weak_label == "risk_review":
        priority_score += 100
        severity = "high"
        suggested_action = "review_risk_memory"
        suggested_feedback_type = "risk_review"
        reasons.append("risk_flag_or_risk_review")
    if sync_status == "needs_human_review" or review_state == "needs_human_review":
        priority_score += 80
        severity = "high" if severity != "high" else severity
        suggested_action = suggested_action or "verify_memory_entity"
        suggested_feedback_type = suggested_feedback_type or "entity_review"
        reasons.append("needs_human_review")
    if weak_label == "profile_missing_review":
        priority_score += 50
        suggested_action = suggested_action or "verify_legacy_resolution"
        suggested_feedback_type = suggested_feedback_type or "resolution_review"
        reasons.append("profile_missing_review")
    if contact_status in {"missing", "unknown"}:
        priority_score += 15 if contact_status == "missing" else 5
        suggested_action = suggested_action or "add_contact_context"
        suggested_feedback_type = suggested_feedback_type or "contact_update"
        reasons.append(f"contact_{contact_status}")
    if evidence_count <= 1:
        priority_score += 10
        suggested_action = suggested_action or "verify_low_evidence_memory"
        suggested_feedback_type = suggested_feedback_type or "evidence_review"
        reasons.append("low_evidence_count")
    if feedback_counts.get("open", 0) > 0:
        priority_score -= 20
        reasons.append("already_has_open_feedback")

    if priority_score <= 0 or not suggested_action:
        return None
    if severity == "low" and priority_score >= 80:
        severity = "medium"
    return {
        "entity_id": entity.get("entity_id"),
        "entity_uid": entity.get("entity_uid"),
        "entity_type": entity.get("entity_type"),
        "identity_key": entity.get("identity_key"),
        "display_name": entity.get("display_name"),
        "entity_status": entity.get("entity_status"),
        "confidence_score": float(entity.get("confidence_score") or 0),
        "identity": _loads(entity.get("identity_json"), {}) or {},
        "metadata": _loads(entity.get("metadata_json"), {}) or {},
        "source": {
            "source_table": entity.get("source_table") or "",
            "source_id": entity.get("source_id") or "",
            "updated_at": entity.get("updated_at") or "",
        },
        "signals": {
            "sync_status": sync_status,
            "weak_label": weak_label,
            "review_state": review_state,
            "contact_status": contact_status,
            "risk_flags": risk_flags,
            "evidence_count": evidence_count,
        },
        "feedback": feedback_counts,
        "suggestion": {
            "suggested_action": suggested_action,
            "suggested_feedback_type": suggested_feedback_type,
            "priority_score": priority_score,
            "severity": severity,
            "reasons": reasons,
            "write_allowed": False,
            "operator_note": "Create or resolve Memory feedback manually after reviewing the entity.",
        },
    }


def build_memory_feedback_backlog(
    *,
    entity_type: str = "kol",
    limit: int = 100,
    json_out: str = "",
    md_out: str = "",
) -> dict[str, Any]:
    ensure_memory_schema()
    feedback_by_entity = _feedback_counts()
    entity_rows: dict[int, dict[str, Any]] = {}
    for row in _load_entity_fact_rows(_text(entity_type) or "kol"):
        entity_id = int(row.get("entity_id") or 0)
        if not entity_id:
            continue
        current = entity_rows.setdefault(
            entity_id,
            {
                "entity_id": entity_id,
                "entity_uid": row.get("entity_uid"),
                "entity_type": row.get("entity_type"),
                "identity_key": row.get("identity_key"),
                "display_name": row.get("display_name"),
                "entity_status": row.get("entity_status"),
                "confidence_score": row.get("confidence_score"),
                "identity_json": row.get("identity_json"),
                "metadata_json": row.get("metadata_json"),
                "source_table": row.get("source_table"),
                "source_id": row.get("source_id"),
                "updated_at": row.get("updated_at"),
                "facts": defaultdict(list),
            },
        )
        if row.get("fact_id"):
            current["facts"][row.get("fact_type")].append(
                {
                    "fact_id": row.get("fact_id"),
                    "key": row.get("fact_key"),
                    "value": row.get("fact_value_text"),
                    "confidence_score": row.get("fact_confidence_score"),
                    "source_ref": row.get("source_ref"),
                    "source_table": row.get("fact_source_table"),
                    "source_id": row.get("fact_source_id"),
                }
            )
    items = []
    action_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for entity in entity_rows.values():
        feedback_counts = feedback_by_entity.get(int(entity.get("entity_id") or 0), {"total": 0, "open": 0, "closed": 0})
        item = _entity_backlog_item(entity, feedback_counts)
        if not item:
            continue
        action = str((item.get("suggestion") or {}).get("suggested_action") or "unknown")
        severity = str((item.get("suggestion") or {}).get("severity") or "low")
        action_counts[action] = action_counts.get(action, 0) + 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        items.append(item)
    items.sort(
        key=lambda item: (
            int((item.get("suggestion") or {}).get("priority_score") or 0),
            str((item.get("source") or {}).get("updated_at") or ""),
        ),
        reverse=True,
    )
    safe_limit = _safe_limit(limit)
    limited = items[:safe_limit]
    feedback_rows = get_conn().execute("SELECT COUNT(*) AS count FROM vkpi_memory_feedback").fetchone()
    payload: dict[str, Any] = {
        "scenario": SCENARIO,
        "provider_calls": False,
        "write_db": False,
        "entity_type": _text(entity_type) or "kol",
        "limit": safe_limit,
        "summary": {
            "entity_rows": len(entity_rows),
            "backlog_candidates": len(items),
            "returned": len(limited),
            "memory_feedback_rows": int(feedback_rows["count"] or 0) if feedback_rows else 0,
        },
        "suggested_actions": action_counts,
        "severity_counts": severity_counts,
        "items": limited,
    }
    markdown = format_memory_feedback_backlog(payload)
    payload["markdown"] = markdown
    if json_out:
        Path(json_out).write_text(json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if md_out:
        Path(md_out).write_text(markdown, encoding="utf-8")
    return payload


def format_memory_feedback_backlog(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# P10 Memory Feedback Backlog",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
        f"entity_type={payload.get('entity_type') or ''}",
        f"entity_rows={int(summary.get('entity_rows') or 0)}",
        f"backlog_candidates={int(summary.get('backlog_candidates') or 0)}",
        f"memory_feedback_rows={int(summary.get('memory_feedback_rows') or 0)}",
        f"returned={int(summary.get('returned') or 0)}",
    ]
    for severity, count in sorted((payload.get("severity_counts") or {}).items()):
        lines.append(f"severity.{severity}={int(count or 0)}")
    for action, count in sorted((payload.get("suggested_actions") or {}).items()):
        lines.append(f"suggested.{action}={int(count or 0)}")
    lines.extend(["```", "", "## Backlog", ""])
    for item in payload.get("items") or []:
        signals = item.get("signals") or {}
        suggestion = item.get("suggestion") or {}
        reasons = ",".join(suggestion.get("reasons") or [])
        lines.append(
            f"- entity={item.get('entity_uid')} name={item.get('display_name') or item.get('identity_key')} "
            f"action={suggestion.get('suggested_action')} severity={suggestion.get('severity')} "
            f"priority={int(suggestion.get('priority_score') or 0)} "
            f"sync={signals.get('sync_status')} weak={signals.get('weak_label')} "
            f"review={signals.get('review_state')} contact={signals.get('contact_status')} "
            f"risk_flags={len(signals.get('risk_flags') or [])} evidence={int(signals.get('evidence_count') or 0)} "
            f"reasons={reasons}"
        )
    if not payload.get("items"):
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"
