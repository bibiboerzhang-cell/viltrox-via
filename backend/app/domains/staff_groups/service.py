"""V-KPI Staff Groups domain service — 员工分组(团队)CRUD + 成员增删。

迁移 123 的单表 vkpi_staff_groups。id 用 VARCHAR(grp_<ms> 串生成)。
DB 走 get_conn(? 占位)应用路径;jsonb 列(member_ids/permissions_json)
写时 ?::jsonb、读时 _loads 解析成对象。
member_ids = jsonb bigint 数组,镜像 vkpi_events.team_ids 口径。
created_by = scope.actor_staff_id(staff);为 0/None 时落 NULL(FK-safe)。
绝不碰 viltrox_fit_score / rule_v0;与 KOL Pool 物理隔离。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope


_DEFAULT_PERMISSIONS: dict[str, Any] = {
    "shared_projects": [],
    "shared_kol_pool": "",
    "kpi_goal": "",
    "reminder_rule": "",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_id(prefix: str) -> str:
    # 时间戳毫秒生成,镜像 events 的 _gen_id。
    return f"{prefix}_{int(_now().timestamp() * 1000)}"


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else None, default=str, ensure_ascii=False)


def _staff_id(staff: dict[str, Any] | None) -> int | None:
    """created_by 用:取 actor staff id;0/取不到 → None(FK-safe,落 NULL)。"""
    try:
        sid = scope.actor_staff_id(staff)
    except Exception:
        sid = (staff or {}).get("staff_id") or (staff or {}).get("id")
    try:
        sid = int(sid) if sid is not None else None
    except (TypeError, ValueError):
        sid = None
    return sid or None


def _group_row(r: Any) -> dict[str, Any]:
    """解析 jsonb 列:member_ids(默认 [])+ permissions_json→'permissions'(默认全空口径)。"""
    row = dict(r)
    row["member_ids"] = _loads(row.get("member_ids"), [])
    perms = _loads(row.pop("permissions_json", None), {})
    if not isinstance(perms, dict):
        perms = {}
    row["permissions"] = {**_DEFAULT_PERMISSIONS, **perms}
    return row


def _normalize_members(value: Any) -> list[Any]:
    """member_ids 去重保序;镜像 events team_ids 的 bigint 数组口径。"""
    out: list[Any] = []
    for v in (value or []):
        if v not in out:
            out.append(v)
    return out


# ── Staff Groups ────────────────────────────────────────────────────────────
def list_groups(staff: dict[str, Any] | None, *, limit: int = 200) -> dict[str, Any]:
    """列分组:全部可见(分组是协作单元,不按员工 scope 收窄)。"""
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 200), 500))
    rows = conn.execute(
        "SELECT * FROM vkpi_staff_groups ORDER BY updated_at DESC, created_at DESC LIMIT ?",
        (safe_limit,),
    ).fetchall()
    return {"items": [_group_row(r) for r in rows]}


def get_group(group_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    return {"item": _group_row(row) if row else None}


def create_group(payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    gid = str(payload.get("id") or _gen_id("grp"))
    permissions = {**_DEFAULT_PERMISSIONS, **(payload.get("permissions") or {})}
    conn.execute(
        """
        INSERT INTO vkpi_staff_groups
          (id, name, description, member_ids, permissions_json, created_by, created_at, updated_at)
        VALUES (?,?,?,?::jsonb,?::jsonb,?, NOW(), NOW())
        """,
        (
            gid,
            str(payload.get("name") or "未命名分组"),
            str(payload.get("description") or ""),
            _dumps(_normalize_members(payload.get("member_ids"))),
            _dumps(permissions),
            _staff_id(staff),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_staff_groups WHERE id = ?", (gid,)).fetchone()
    return {"item": _group_row(row) if row else None}


_GROUP_UPDATABLE = {"name": str, "description": str}


def update_group(group_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    sets: list[str] = []
    vals: list[Any] = []
    for key, caster in _GROUP_UPDATABLE.items():
        if key in payload:
            v = payload[key]
            sets.append(f"{key} = ?")
            vals.append(caster(v) if v is not None else v)
    if "member_ids" in payload:
        sets.append("member_ids = ?::jsonb")
        vals.append(_dumps(_normalize_members(payload["member_ids"])))
    if "permissions" in payload:
        permissions = {**_DEFAULT_PERMISSIONS, **(payload.get("permissions") or {})}
        sets.append("permissions_json = ?::jsonb")
        vals.append(_dumps(permissions))
    if not sets:
        row = conn.execute("SELECT * FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
        return {"item": _group_row(row) if row else None}
    sets.append("updated_at = NOW()")
    vals.append(str(group_id))
    conn.execute(f"UPDATE vkpi_staff_groups SET {', '.join(sets)} WHERE id = ?", tuple(vals))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    return {"item": _group_row(row) if row else None}


def delete_group(group_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_staff_groups WHERE id = ?", (str(group_id),))
    conn.commit()
    return {"ok": True, "id": str(group_id)}


# ── Members(成员增删,镜像 events team_ids)────────────────────────────────
def _set_members(conn: Any, group_id: str, members: list[Any]) -> dict[str, Any]:
    conn.execute(
        "UPDATE vkpi_staff_groups SET member_ids = ?::jsonb, updated_at = NOW() WHERE id = ?",
        (_dumps(members), str(group_id)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    return {"item": _group_row(row) if row else None}


def add_member(group_id: str, staff_id: Any, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT member_ids FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    members = _loads(dict(row).get("member_ids"), []) if row else []
    if staff_id not in members:
        members.append(staff_id)
    return _set_members(conn, group_id, members)


def remove_member(group_id: str, staff_id: Any, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT member_ids FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    members = [m for m in (_loads(dict(row).get("member_ids"), []) if row else []) if str(m) != str(staff_id)]
    return _set_members(conn, group_id, members)
