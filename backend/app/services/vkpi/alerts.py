"""V-KPI alert accessors."""
from __future__ import annotations

from typing import Any
from datetime import datetime

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
