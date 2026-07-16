"""真·活动共享成员 CRUD(2026-06-14,ADDITIVE,镜像 projects/project_members.py)。

把活动显式共享给某员工,落表 vkpi_event_members(viewer 只读 / editor 可写)。
scope.assert_event_access / service.list_events 已据本表「加宽」读可见性、并按 role 区分写 ——
本模块只负责成员行的增删查,绝不改 vkpi_events/staff 任何业务列,也绝不动既有 owner/team_ids。

红线:
- 只写 vkpi_event_members。绝不碰 vkpi_events.owner_id/team_ids、staff、viltrox_fit_score、rule_v0。
- 加成员的权限由调用方(路由)先行 assert(同组织活动 owner 或 can_view_all 才可加);
  本模块的 add 再兜底校验活动/目标 staff 同组织 + role 合法。
- self-share 无意义但无害(owner 已全权);幂等:同 (event_id, staff_id) 再加→更新 role。
- event_id 是 VARCHAR(64) 串 id(与 vkpi_events.id 一致),不强转 int。
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope, share_audit

_VALID_ROLES = {"viewer", "editor"}


def _row_to_member(row: Any) -> dict[str, Any]:
    item = dict(row)
    for col in ("created_at",):
        val = item.get(col)
        if val is not None and not isinstance(val, str):
            item[col] = str(val)
    for col in ("id", "staff_id", "added_by_staff_id"):
        if col in item and item.get(col) is not None:
            try:
                item[col] = int(item[col])
            except (TypeError, ValueError):
                pass
    if "event_id" in item and item.get("event_id") is not None:
        item["event_id"] = str(item["event_id"])
    return item


def _event_context(
    conn: Any,
    event_id: str,
    staff: dict[str, Any] | None,
) -> tuple[int, bool, Any | None]:
    """Resolve and load the parent Event inside the actor's workspace."""
    organization_id, organization_scoped = scope.event_organization_context(staff, conn)
    if organization_scoped:
        row = conn.execute(
            "SELECT id, owner_id FROM vkpi_events WHERE id=? AND organization_id=?",
            (str(event_id), organization_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, owner_id FROM vkpi_events WHERE id=?",
            (str(event_id),),
        ).fetchone()
    return organization_id, organization_scoped, row


def list_members(event_id: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """列某活动的共享成员(含展示名/邮箱/角色)。纯 SELECT,无副作用。"""
    eid = str(event_id or "").strip()
    if not eid:
        return {"status": "error", "error": "event_id required"}
    conn = get_conn()
    organization_id, organization_scoped, event = _event_context(conn, eid, staff)
    if event is None:
        raise LookupError("event not found")
    if organization_scoped:
        rows = conn.execute(
            """
            SELECT m.id,
                   m.event_id,
                   m.staff_id,
                   m.role,
                   m.added_by_staff_id,
                   m.created_at,
                   COALESCE(u.name, u.email, 'Staff') AS staff_name,
                   COALESCE(u.email, '') AS email
            FROM vkpi_event_members m
            JOIN vkpi_events e ON e.id = m.event_id AND e.organization_id = ?
            LEFT JOIN staff st ON st.id = m.staff_id
            LEFT JOIN users u ON u.id = st.user_id
            WHERE m.event_id = ?
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (organization_id, eid),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT m.id,
                   m.event_id,
                   m.staff_id,
                   m.role,
                   m.added_by_staff_id,
                   m.created_at,
                   COALESCE(u.name, u.email, 'Staff') AS staff_name,
                   COALESCE(u.email, '') AS email
            FROM vkpi_event_members m
            LEFT JOIN staff st ON st.id = m.staff_id
            LEFT JOIN users u ON u.id = st.user_id
            WHERE m.event_id = ?
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (eid,),
        ).fetchall()
    items = [_row_to_member(r) for r in rows]
    return {"status": "ok", "event_id": eid, "count": len(items), "items": items}


def add_member(
    event_id: str,
    staff_id: int,
    role: str = "viewer",
    added_by_staff_id: int | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把活动共享给 staff_id(viewer/editor)。幂等:已存在则更新 role。

    红线:只写 vkpi_event_members。路由层须先校验调用方有权加(owner/admin)。
    """
    eid = str(event_id or "").strip()
    sid = int(staff_id or 0)
    if not eid:
        return {"status": "error", "error": "event_id required"}
    if sid <= 0:
        return {"status": "error", "error": "staff_id required"}
    role_key = str(role or "viewer").strip().lower()
    if role_key not in _VALID_ROLES:
        return {"status": "error", "error": f"invalid role: {role_key}"}

    conn = get_conn()
    organization_id, _organization_scoped, event = _event_context(conn, eid, staff)
    if event is None:
        return {"status": "error", "error": "event not found"}
    if not scope.staff_belongs_to_event_organization(conn, sid, organization_id):
        return {"status": "error", "error": "staff organization mismatch"}

    added_by = int(added_by_staff_id) if added_by_staff_id not in (None, "", 0, "0") else None

    existing = conn.execute(
        "SELECT id FROM vkpi_event_members WHERE event_id=? AND staff_id=?",
        (eid, sid),
    ).fetchone()
    # The read is only for response/audit semantics.  The write itself is one
    # atomic UPSERT, so two concurrent share requests cannot race into a unique
    # constraint error or overwrite a newer role with a stale INSERT branch.
    cursor = conn.execute(
        """
        INSERT INTO vkpi_event_members (event_id, staff_id, role, added_by_staff_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (event_id, staff_id) DO UPDATE SET
          role=excluded.role,
          added_by_staff_id=excluded.added_by_staff_id
        RETURNING *
        """,
        (eid, sid, role_key, added_by),
    )
    row = cursor.fetchone()
    conn.commit()
    action = "role_change" if existing is not None else "share"
    share_audit.record(target_type="event", target_id=eid, action=action,
                       member_staff_id=sid, role=role_key, actor_staff_id=added_by)
    return {"status": "updated" if existing is not None else "created", "member": _row_to_member(row)}


def remove_member(
    event_id: str,
    staff_id: int,
    *,
    removed_by_staff_id: int | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """撤销共享:删一条成员行。仅触本表 —— 不改活动/任务/费用。撤销记入分享审计(谁/何时)。"""
    eid = str(event_id or "").strip()
    sid = int(staff_id or 0)
    if not eid:
        return {"status": "error", "error": "event_id required"}
    if sid <= 0:
        return {"status": "error", "error": "staff_id required"}
    conn = get_conn()
    _organization_id, _organization_scoped, event = _event_context(conn, eid, staff)
    if event is None:
        return {"status": "error", "error": "event not found"}
    existing = conn.execute(
        "SELECT id FROM vkpi_event_members WHERE event_id=? AND staff_id=?",
        (eid, sid),
    ).fetchone()
    if existing is None:
        return {"status": "not_found", "event_id": eid, "staff_id": sid}
    conn.execute(
        "DELETE FROM vkpi_event_members WHERE event_id=? AND staff_id=?",
        (eid, sid),
    )
    conn.commit()
    share_audit.record(target_type="event", target_id=eid, action="revoke",
                       member_staff_id=sid, actor_staff_id=removed_by_staff_id)
    return {"status": "removed", "event_id": eid, "staff_id": sid}


def assert_can_manage_members(event_id: str, staff: dict[str, Any] | None) -> None:
    """只有活动 owner_id 或 can_view_all 才可改成员名单(把别人加进来/踢出)。

    裁决(镜像 project 侧 assert_can_manage_members):管理成员名单是「转授」动作,
    比内容编辑更高一级 —— 共享 editor 能写活动内容,但不能再把别人加进来。
    故这里只认 owner / admin(can_view_all),不接受「仅共享 editor / team 成员」。
    """
    conn = get_conn()
    _organization_id, _organization_scoped, row = _event_context(conn, str(event_id), staff)
    if row is None:
        raise scope.ScopeDenied("event not found")
    # Manager/admin authority is organization-local.  Resolve and constrain the
    # Event before applying the global-role shortcut.
    if scope.can_view_all(staff):
        return
    actor = scope.actor_staff_id(staff)
    if not actor:
        raise scope.ScopeDenied("event scope denied")
    item = dict(row)
    try:
        owner = int(item.get("owner_id") or 0)
    except (TypeError, ValueError):
        owner = 0
    if actor == owner:
        return
    raise scope.ScopeDenied("event scope denied")
