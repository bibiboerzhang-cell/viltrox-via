"""Action writer for V-KPI data quality issues."""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import audit
from app.services.vkpi import scope
from app.domains.data_quality.common import _utcnow, ensure_data_quality_schema

logger = get_logger(__name__)

def act_on_issue(issue_id: str, *, action: str, body: dict[str, Any] | None = None, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    clean_issue_id = str(issue_id or "").strip()
    clean_action = str(action or "").strip().lower()
    if not clean_issue_id:
        raise ValueError("issue_id required")
    if clean_action not in {"resolve", "ignore", "assign", "rerun", "evidence", "reopen"}:
        raise ValueError("unsupported data quality action")
    ensure_data_quality_schema()
    payload = body or {}
    reason = str(payload.get("reason") or payload.get("note") or "")
    metadata = payload.get("metadata") or {}
    if clean_action == "assign":
        assigned_to = payload.get("assigned_to_staff_id") or payload.get("staff_id") or scope.actor_staff_id(staff)
        metadata = {**metadata, "assigned_to_staff_id": assigned_to}
        if not reason:
            reason = f"assigned_to:{assigned_to or ''}"
    elif clean_action == "rerun" and not reason:
        reason = "rerun requested"
    elif clean_action == "evidence" and not reason:
        reason = "evidence attached"
    elif clean_action == "reopen" and not reason:
        reason = "reopened for review"
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_data_quality_actions
            (issue_id, action, reason, staff_id, metadata_json, created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            clean_issue_id,
            clean_action,
            reason,
            scope.actor_staff_id(staff) or None,
            json.dumps(metadata, ensure_ascii=False, default=str),
            _utcnow(),
        ),
    )
    conn.commit()
    try:
        audit.log_business_event(
            staff_id=scope.actor_staff_id(staff) or 0,
            action_type=f"data_quality_{clean_action}",
            target_type="data_quality_issue",
            target_id=clean_issue_id,
            detail=reason or f"{clean_action} data quality issue",
            metadata={
                "issue_id": clean_issue_id,
                "action": clean_action,
                "metadata": metadata,
            },
        )
    except Exception as exc:
        # Audit must not block data-quality remediation actions.
        logger.warning("data quality action audit failed for %s/%s: %s", clean_issue_id, clean_action, exc)
    return {"status": "ok", "issue_id": clean_issue_id, "action": clean_action}
