"""Message timeline operations for V-KPI evidence resources."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains import audit
from app.domains.access import scope
from app.domains.evidence.common import (
    _actor_id,
    _assert_kol_access,
    _assert_message_access,
    _int,
    _json,
    _project_context,
    _row,
    _rows,
    _utcnow,
)
from app.platform.db.schema import ensure_vkpi_schema

def list_messages(
    *,
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = 100,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    where: list[str] = []
    params: list[Any] = []
    if project_id:
        _project_context(int(project_id), staff)
        where.append("m.project_id = ?")
        params.append(int(project_id))
    if kol_id:
        _assert_kol_access(int(kol_id), staff)
        where.append("m.kol_id = ?")
        params.append(int(kol_id))
    scoped_staff = scope.effective_staff_id(staff, staff_id)
    if scoped_staff:
        where.append(
            """
            (
                m.staff_id = ?
                OR p.assigned_staff_id = ?
                OR p.created_by_staff_id = ?
                OR c.staff_id = ?
            )
            """
        )
        params.extend([scoped_staff, scoped_staff, scoped_staff, scoped_staff])
    sql = """
        SELECT
            m.*,
            p.project_name,
            p.stage AS project_stage,
            k.channel_name AS kol_name,
            k.channel_url AS kol_url,
            u.name AS staff_name,
            u.email AS staff_email,
            COALESCE(a.attachment_count, 0) AS attachment_count
        FROM vkpi_messages m
        LEFT JOIN vkpi_projects p ON p.id = m.project_id
        LEFT JOIN kols k ON k.id = m.kol_id
        LEFT JOIN staff s ON s.id = m.staff_id
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN vkpi_kol_claims c ON c.kol_id = m.kol_id AND c.status = 'active'
        LEFT JOIN (
            SELECT message_id, COUNT(*) AS attachment_count
            FROM vkpi_message_attachments
            GROUP BY message_id
        ) a ON a.message_id = m.id
    """
    if where:
        sql += " WHERE " + " AND ".join(f"({item})" for item in where)
    sql += " ORDER BY m.captured_at DESC, m.id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit or 100))))
    rows = _rows(conn.execute(sql, params).fetchall())
    return {"messages": rows, "count": len(rows)}

def create_message(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    project_id = _int(body.get("project_id"))
    kol_id = _int(body.get("kol_id"))
    project = _project_context(project_id, staff, write=True) if project_id else {}
    if project and not kol_id:
        kol_id = _int(project.get("kol_id"))
    if kol_id and not project_id:
        _assert_kol_access(kol_id, staff, write=True)
    if not project_id and not kol_id and not scope.can_view_all(staff):
        raise scope.ScopeDenied("message must be attached to a project or KOL")
    actor = _actor_id(staff)
    now = _utcnow()
    message_body = str(body.get("body") or body.get("message") or body.get("snippet") or "").strip()
    # P2:INSERT 取行一律 RETURNING——并发下 ORDER BY id DESC 会取到别人的行。
    row = conn.execute(
        """
        INSERT INTO vkpi_messages (
            project_id, kol_id, staff_id, source, direction, sender, receiver,
            body, snippet, evidence_url, follow_up_due_at, captured_at, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        RETURNING *
        """,
        (
            project_id or None,
            kol_id or None,
            actor or _int(body.get("staff_id")) or _int(project.get("assigned_staff_id")) or None,
            str(body.get("source") or "manual"),
            str(body.get("direction") or "outbound"),
            str(body.get("sender") or ""),
            str(body.get("receiver") or ""),
            message_body,
            str(body.get("snippet") or message_body[:240]),
            str(body.get("evidence_url") or ""),
            body.get("follow_up_due_at"),
            str(body.get("captured_at") or now),
            _json(body.get("metadata")),
            now,
        ),
    ).fetchone()
    conn.commit()
    item = _row(row)
    if item:
        audit.log_business_event(
            staff_id=actor,
            action_type="message_capture",
            target_type="message",
            target_id=item.get("id"),
            detail=str(body.get("source") or "manual"),
            metadata={"project_id": project_id or None, "kol_id": kol_id or None},
        )
    return item

def get_message(message_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    item = _assert_message_access(int(message_id), staff)
    attachments = _rows(
        get_conn()
        .execute(
            "SELECT * FROM vkpi_message_attachments WHERE message_id=? ORDER BY created_at DESC, id DESC",
            (int(message_id),),
        )
        .fetchall()
    )
    return {"message": item, "attachments": attachments}

def add_message_attachment(message_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    message = _assert_message_access(int(message_id), staff, write=True)
    file_url = str(body.get("file_url") or body.get("url") or "").strip()
    if not file_url:
        raise ValueError("file_url required")
    conn = get_conn()
    now = _utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_message_attachments (message_id, file_url, file_type, metadata_json, created_at)
        VALUES (?,?,?,?,?)
        """,
        (int(message_id), file_url, str(body.get("file_type") or ""), _json(body.get("metadata")), now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM vkpi_message_attachments WHERE message_id=? ORDER BY id DESC LIMIT 1",
        (int(message_id),),
    ).fetchone()
    item = _row(row)
    if item:
        audit.log_business_event(
            staff_id=_actor_id(staff),
            action_type="message_attachment_add",
            target_type="message",
            target_id=message_id,
            detail=file_url,
            metadata={"attachment_id": item.get("id"), "project_id": message.get("project_id"), "kol_id": message.get("kol_id")},
        )
    return item
