"""真·项目共享成员 CRUD(2026-06-14,ADDITIVE)。

把项目显式共享给某员工,落表 vkpi_project_members(viewer 只读 / editor 可写)。
scope.project_filter / scope.assert_project_access 已据本表「加宽」可见性 —— 本模块只
负责成员行的增删查,绝不改 vkpi_projects/staff 任何业务列,也绝不动 own-only 既有限制。

红线:
- 只写 vkpi_project_members。绝不碰 vkpi_projects.stage/assigned/restricted、staff、
  viltrox_fit_score、rule_v0。
- 加成员的权限由调用方(路由)先行 assert(项目 owner/creator 或 can_view_all 才可加);
  本模块的 add 再兜底校验项目存在 + role 合法。
- self-share 无意义但无害(own 已全权);幂等:同 (project_id, staff_id) 再加→更新 role。
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope

_VALID_ROLES = {"viewer", "editor"}


def _row_to_member(row: Any) -> dict[str, Any]:
    item = dict(row)
    for col in ("created_at",):
        val = item.get(col)
        if val is not None and not isinstance(val, str):
            item[col] = str(val)
    for col in ("id", "project_id", "staff_id", "added_by_staff_id"):
        if col in item and item.get(col) is not None:
            try:
                item[col] = int(item[col])
            except (TypeError, ValueError):
                pass
    return item


def _project_exists(conn: Any, project_id: int) -> bool:
    row = conn.execute(
        "SELECT id FROM vkpi_projects WHERE id=?",
        (int(project_id),),
    ).fetchone()
    return row is not None


def list_members(project_id: int) -> dict[str, Any]:
    """列某项目的共享成员(含展示名/邮箱/角色)。纯 SELECT,无副作用。"""
    pid = int(project_id or 0)
    if pid <= 0:
        return {"status": "error", "error": "project_id required"}
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT m.id,
               m.project_id,
               m.staff_id,
               m.role,
               m.added_by_staff_id,
               m.created_at,
               COALESCE(u.name, u.email, 'Staff') AS staff_name,
               COALESCE(u.email, '') AS email
        FROM vkpi_project_members m
        LEFT JOIN staff st ON st.id = m.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE m.project_id = ?
        ORDER BY m.created_at ASC, m.id ASC
        """,
        (pid,),
    ).fetchall()
    items = [_row_to_member(r) for r in rows]
    return {"status": "ok", "project_id": pid, "count": len(items), "items": items}


def add_member(
    project_id: int,
    staff_id: int,
    role: str = "viewer",
    added_by_staff_id: int | None = None,
) -> dict[str, Any]:
    """把项目共享给 staff_id(viewer/editor)。幂等:已存在则更新 role。

    红线:只写 vkpi_project_members。路由层须先校验调用方有权加(owner/creator/admin)。
    """
    pid = int(project_id or 0)
    sid = int(staff_id or 0)
    if pid <= 0:
        return {"status": "error", "error": "project_id required"}
    if sid <= 0:
        return {"status": "error", "error": "staff_id required"}
    role_key = str(role or "viewer").strip().lower()
    if role_key not in _VALID_ROLES:
        return {"status": "error", "error": f"invalid role: {role_key}"}

    conn = get_conn()
    if not _project_exists(conn, pid):
        return {"status": "error", "error": "project not found"}

    added_by = int(added_by_staff_id) if added_by_staff_id not in (None, "", 0, "0") else None

    existing = conn.execute(
        "SELECT id FROM vkpi_project_members WHERE project_id=? AND staff_id=?",
        (pid, sid),
    ).fetchone()
    if existing is not None:
        cursor = conn.execute(
            "UPDATE vkpi_project_members SET role=? WHERE project_id=? AND staff_id=? RETURNING *",
            (role_key, pid, sid),
        )
        row = cursor.fetchone()
        conn.commit()
        return {"status": "updated", "member": _row_to_member(row)}

    cursor = conn.execute(
        """
        INSERT INTO vkpi_project_members (project_id, staff_id, role, added_by_staff_id)
        VALUES (?, ?, ?, ?)
        RETURNING *
        """,
        (pid, sid, role_key, added_by),
    )
    row = cursor.fetchone()
    conn.commit()
    return {"status": "created", "member": _row_to_member(row)}


def remove_member(project_id: int, staff_id: int) -> dict[str, Any]:
    """撤销共享:删一条成员行。仅触本表 —— 不改项目/派单/费用。"""
    pid = int(project_id or 0)
    sid = int(staff_id or 0)
    if pid <= 0:
        return {"status": "error", "error": "project_id required"}
    if sid <= 0:
        return {"status": "error", "error": "staff_id required"}
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM vkpi_project_members WHERE project_id=? AND staff_id=?",
        (pid, sid),
    ).fetchone()
    if existing is None:
        return {"status": "not_found", "project_id": pid, "staff_id": sid}
    conn.execute(
        "DELETE FROM vkpi_project_members WHERE project_id=? AND staff_id=?",
        (pid, sid),
    )
    conn.commit()
    return {"status": "removed", "project_id": pid, "staff_id": sid}


def assert_can_manage_members(project_id: int, staff: dict[str, Any] | None) -> None:
    """只有项目 owner/creator(assigned/creator)或 can_view_all 才可改成员名单。

    复用 assert_project_access(write=True):own/admin 通过;共享 editor 也算「可写该项目」
    —— 但管理成员名单(把别人加进来/踢出)是更高一级动作,这里要求 write 级且非纯共享。
    简化裁决(可追认):editor 共享成员能写项目内容,但不能再转授(加/删别人)——
    故这里不接受「仅共享 editor」,只认 own/admin。用 project_filter own 子句 + admin 判定。
    """
    if scope.can_view_all(staff):
        return
    actor = scope.actor_staff_id(staff)
    if not actor:
        raise scope.ScopeDenied("project scope denied")
    row = get_conn().execute(
        "SELECT assigned_staff_id, created_by_staff_id FROM vkpi_projects WHERE id=?",
        (int(project_id),),
    ).fetchone()
    if row is None:
        raise scope.ScopeDenied("project not found")
    item = dict(row)
    if actor in {int(item.get("assigned_staff_id") or 0), int(item.get("created_by_staff_id") or 0)}:
        return
    raise scope.ScopeDenied("project scope denied")
