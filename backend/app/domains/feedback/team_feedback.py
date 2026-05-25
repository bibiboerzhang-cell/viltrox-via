"""Team feedback loop for P3 internal observation.

This module intentionally stays small: collect user-visible issues from the
running UI, store them in DB, and make them auditable for triage.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.services.vkpi.workflow import staff_id as resolve_staff_id

FEEDBACK_TYPES = {"bug", "suggestion", "question", "missing_data", "button_issue"}
SEVERITIES = {"low", "medium", "high", "critical"}
STATUSES = {"open", "triaged", "in_progress", "resolved", "closed"}
logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def ensure_team_feedback_schema() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_team_feedback (
                id SERIAL PRIMARY KEY,
                uid TEXT NOT NULL UNIQUE,
                staff_id INTEGER,
                feedback_type TEXT NOT NULL DEFAULT 'bug',
                severity TEXT NOT NULL DEFAULT 'medium',
                page_path TEXT DEFAULT '',
                title TEXT NOT NULL,
                detail TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_team_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL UNIQUE,
                staff_id INTEGER,
                feedback_type TEXT NOT NULL DEFAULT 'bug',
                severity TEXT NOT NULL DEFAULT 'medium',
                page_path TEXT DEFAULT '',
                title TEXT NOT NULL,
                detail TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_team_feedback_status ON vkpi_team_feedback(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_team_feedback_staff ON vkpi_team_feedback(staff_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_team_feedback_created ON vkpi_team_feedback(created_at)")
    conn.commit()


def _row_to_feedback(row: Any) -> dict[str, Any]:
    data = dict(row)
    try:
        data["metadata"] = json.loads(data.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        data["metadata"] = {}
    data.pop("metadata_json", None)
    return data


def create_feedback(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_team_feedback_schema()
    feedback_type = str(payload.get("feedback_type") or payload.get("feedbackType") or "bug").strip().lower()
    if feedback_type not in FEEDBACK_TYPES:
        feedback_type = "bug"
    severity = str(payload.get("severity") or "medium").strip().lower()
    if severity not in SEVERITIES:
        severity = "medium"
    title = _clip(payload.get("title"), 180)
    if not title:
        raise ValueError("feedback title is required")
    detail = _clip(payload.get("detail") or payload.get("description"), 5000)
    page_path = _clip(payload.get("page_path") or payload.get("pagePath"), 300)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    uid = f"fb_{uuid.uuid4().hex[:18]}"
    now = _now()
    staff_id = resolve_staff_id(staff)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_team_feedback
            (uid, staff_id, feedback_type, severity, page_path, title, detail, status, metadata_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (uid, int(staff_id or 0), feedback_type, severity, page_path, title, detail, "open", _json(metadata), now, now),
    )
    conn.commit()
    try:
        audit.log_business_event(
            staff_id=int(staff_id or 0),
            action_type="team_feedback_create",
            target_type="team_feedback",
            target_id=uid,
            detail=title,
            metadata={"feedback_type": feedback_type, "severity": severity, "page_path": page_path},
        )
    except Exception as exc:
        # Feedback must never fail because audit logging failed.
        logger.warning("team feedback create audit failed for %s: %s", uid, exc)
    row = conn.execute("SELECT * FROM vkpi_team_feedback WHERE uid=?", (uid,)).fetchone()
    return {"feedback": _row_to_feedback(row), "ok": True}


def list_feedback(*, status: str = "", limit: int = 100) -> dict[str, Any]:
    ensure_team_feedback_schema()
    safe_limit = max(1, min(500, int(limit or 100)))
    params: list[Any] = []
    where = ""
    clean_status = str(status or "").strip().lower()
    if clean_status:
        where = "WHERE status=?"
        params.append(clean_status)
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_team_feedback {where} ORDER BY created_at DESC, id DESC LIMIT ?",
        (*params, safe_limit),
    ).fetchall()
    return {"feedback": [_row_to_feedback(row) for row in rows], "count": len(rows)}


def update_feedback_status(uid: str, payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_team_feedback_schema()
    clean_uid = str(uid or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    if status not in STATUSES:
        raise ValueError("invalid feedback status")
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_team_feedback WHERE uid=?", (clean_uid,)).fetchone()
    if not row:
        raise KeyError("feedback not found")
    conn.execute("UPDATE vkpi_team_feedback SET status=?, updated_at=? WHERE uid=?", (status, _now(), clean_uid))
    conn.commit()
    try:
        audit.log_business_event(
            staff_id=int(resolve_staff_id(staff) or 0),
            action_type="team_feedback_status_update",
            target_type="team_feedback",
            target_id=clean_uid,
            detail=status,
            metadata={"previous_status": dict(row).get("status"), "status": status},
        )
    except Exception as exc:
        logger.warning("team feedback status audit failed for %s: %s", clean_uid, exc)
    updated = conn.execute("SELECT * FROM vkpi_team_feedback WHERE uid=?", (clean_uid,)).fetchone()
    return {"feedback": _row_to_feedback(updated), "ok": True}
