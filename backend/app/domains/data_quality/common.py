"""Shared helpers for V-KPI data quality checks."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains.access import scope

def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default

def _safe_rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception:
        return []

def _issue_key(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def ensure_data_quality_schema() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_data_quality_actions (
                id BIGSERIAL PRIMARY KEY,
                issue_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT DEFAULT '',
                staff_id BIGINT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vkpi_data_quality_actions_issue ON vkpi_data_quality_actions(issue_id, created_at DESC)"
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_data_quality_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT DEFAULT '',
                staff_id INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vkpi_data_quality_actions_issue ON vkpi_data_quality_actions(issue_id, created_at DESC)"
        )
    conn.commit()

def _append_issue(issues: list[dict[str, Any]], *, issue_type: str, severity: str, title: str, entity_type: str, entity_id: Any = None, staff_id: Any = None, project_id: Any = None, kol_id: Any = None, detail: str = "", evidence: dict[str, Any] | None = None) -> None:
    issues.append({
        "id": f"{issue_type}:{entity_type}:{entity_id or len(issues) + 1}",
        "issue_type": issue_type,
        "severity": severity,
        "title": title,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "staff_id": staff_id,
        "project_id": project_id,
        "kol_id": kol_id,
        "detail": detail,
        "evidence": evidence or {},
        "created_at": _utcnow(),
    })

def _staff_clause(column_sql: str, staff: dict[str, Any] | None) -> tuple[str, list[Any]]:
    if scope.can_view_all(staff):
        return "", []
    actor = scope.actor_staff_id(staff)
    if not actor:
        return " AND 1=0 ", []
    return f" AND {column_sql} = ? ", [actor]

def _project_clause(alias: str, staff: dict[str, Any] | None) -> tuple[str, list[Any]]:
    if scope.can_view_all(staff):
        return "", []
    actor = scope.actor_staff_id(staff)
    if not actor:
        return " AND 1=0 ", []
    prefix = f"{alias}." if alias else ""
    return f" AND ({prefix}assigned_staff_id = ? OR {prefix}created_by_staff_id = ?) ", [actor, actor]

def _active_project_filter_for_quality(alias: str) -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"({prefix}project_id IS NULL OR EXISTS ("
        f"SELECT 1 FROM vkpi_projects p WHERE p.id = {prefix}project_id "
        "AND COALESCE(p.stage_status, '') != 'deleted'))"
    )
