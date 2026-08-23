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
from app.domains.recommendations import pool_action_bridge


_DEFAULT_PERMISSIONS: dict[str, Any] = {
    "shared_projects": [],
    "shared_kol_pool": "",
    "shared_kol_pool_ids": [],
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


# ── 分组共享真生效(P-GROUP-34)─────────────────────────────────────────────
# 把「组级 shared_projects × 组成员」展开成真 vkpi_project_members 行(标 shared_via_group_id),
# scope.project_filter / assert_project_access 据此放行(零改 scope.py,纯复用现成 enforce)。
# 红线:只写 vkpi_project_members,绝不碰 vkpi_projects/staff/viltrox_fit_score/rule_v0。
#   只「加宽」分组成员对被共享项目的可见性,绝不收窄既有 own-only;restricted 项目仍只认
#   assigned/creator/can_view_all(放行 gate 仍在 scope.py 的非 restricted 子句上)。
def _group_origin_id(group_id: str) -> int | None:
    """把 VARCHAR 组 id(grp_<ms>)映射成 shared_via_group_id 的 BIGINT 后缀。

    取末段非数字之后的数字串(grp_1718500000000 → 1718500000000)。取不到 → None
    (不展开,诚实降级,绝不用 0 之类伪 id 误删/误插)。
    """
    s = str(group_id or "")
    digits = ""
    for ch in reversed(s):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    if not digits:
        return None
    try:
        return int(digits)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return None
    return iv or None


def _existing_ids(conn: Any, table: str, ids: list[int]) -> set[int]:
    """回读 table 中真实存在的 id 子集(用于展开前过滤陈旧 id)。

    分组的 member_ids / shared_projects / shared_kol_pool_ids 可能含已删的 staff/project/kol id;
    直接展开 INSERT 会撞 FK 违约 → 500 且泄露 PG 原文。展开前用本函数只留存在的 id。
    table 为域内硬编码常量('vkpi_projects'/'vkpi_kol_pool'/'staff'),非用户输入,拼接安全。
    """
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return set()
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id FROM {table} WHERE id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    return {int(dict(r)["id"]) for r in rows}


def _shared_project_ids(permissions: dict[str, Any] | None) -> list[int]:
    """从 permissions.shared_projects 取「真项目 id」列表(去重、丢非数字)。

    P-GROUP-34 后 EditGroupModal 存的是项目 id 数组;历史文本(项目名)无法映射成 id,
    会被 _int_or_none 丢弃(诚实——旧文本不再生效,需在 UI 重选项目)。
    """
    perms = permissions if isinstance(permissions, dict) else {}
    out: list[int] = []
    for v in (perms.get("shared_projects") or []):
        pid = _int_or_none(v)
        if pid and pid not in out:
            out.append(pid)
    return out


def _recompute_group_shared_projects(
    conn: Any,
    group_id: str,
    member_ids: list[Any],
    permissions: dict[str, Any] | None,
) -> None:
    """幂等重算:把本组的「组级 shared_projects × 组成员」展开成 vkpi_project_members 行。

    做法(对该 group 的 origin_id):
      1) DELETE FROM vkpi_project_members WHERE shared_via_group_id = <origin_id>
         —— 只删本组产生的行,绝不碰 P-COLLAB-1 手动共享行(shared_via_group_id IS NULL)。
      2) 对 (每个组成员 staff_id × 每个 shared_project_id) INSERT(shared_via_group_id=<origin_id>)。
         vkpi_project_members 有 UNIQUE(project_id, staff_id):若该(项目,人)已存在手动共享行,
         用 ON CONFLICT DO NOTHING 跳过(不抢手动行的 role/来源,手动优先,组共享不覆盖)。
    改组成员/改 shared_projects/删组 都重算 —— 每次都是「先清本组再重铺」,天然幂等。
    """
    origin_id = _group_origin_id(group_id)
    if origin_id is None:
        # 取不到稳定 origin id → 不展开(避免用伪 id 误删/误插);不阻断分组本身保存。
        return
    conn.execute(
        "DELETE FROM vkpi_project_members WHERE shared_via_group_id = ?",
        (origin_id,),
    )
    project_ids = _shared_project_ids(permissions)
    staff_ids = [sid for sid in (_int_or_none(m) for m in (member_ids or [])) if sid]
    # 展开前过滤陈旧 id:分组存的 shared_projects/member_ids 可能含已删 project/staff,
    # 直接 INSERT 会撞 vkpi_project_members 的 FK(project_id/staff_id)→ 500 且泄露 PG 原文。
    # 只留真实存在的 id;被过滤掉的陈旧 id 诚实降级(不展开,不阻断分组本身保存)。
    if project_ids:
        live_projects = _existing_ids(conn, "vkpi_projects", project_ids)
        project_ids = [pid for pid in project_ids if pid in live_projects]
    if staff_ids:
        live_staff = _existing_ids(conn, "staff", staff_ids)
        staff_ids = [sid for sid in staff_ids if sid in live_staff]
    if not project_ids or not staff_ids:
        # 无(存在的)共享项目或成员 → 上面 DELETE 已清空本组行,直接收尾(空集即「无共享」)。
        return
    for pid in project_ids:
        for sid in staff_ids:
            conn.execute(
                """
                INSERT INTO vkpi_project_members
                    (project_id, staff_id, role, added_by_staff_id, shared_via_group_id)
                VALUES (?, ?, 'viewer', NULL, ?)
                ON CONFLICT (project_id, staff_id) DO NOTHING
                """,
                (pid, sid, origin_id),
            )


# ── 分组共享 KOL 真生效(P-GROUP-7 → 分组扩展,镜像上方 projects)─────────────
# 把「组级 shared_kol_pool_ids × 组成员」展开成真 vkpi_kol_pool_members 行(标 shared_via_group_id,
# 迁移 161),scope.member_shared_kol_ids / my_kol_aggregate 的 LEFT JOIN 据此放行(零改 scope.py)。
# 红线:只写 vkpi_kol_pool_members(159),绝不碰 vkpi_kol_pool/favorites/claims/viltrox_fit_score/rule_v0。
#   只「加宽」分组成员对被共享 KOL 的只读可见,绝不改归属/收藏/认领。
def _shared_kol_ids(permissions: dict[str, Any] | None) -> list[int]:
    """从 permissions.shared_kol_pool_ids 取「真 kol_pool_id」列表(去重、丢非数字)。

    EditGroupModal 存的是 kol_pool_id 数组;历史文本字段 shared_kol_pool(池名)无法映射成 id,
    不在此读取(诚实——旧文本不再生效,需在 UI 重选 KOL)。
    """
    perms = permissions if isinstance(permissions, dict) else {}
    out: list[int] = []
    for v in (perms.get("shared_kol_pool_ids") or []):
        kid = _int_or_none(v)
        if kid and kid not in out:
            out.append(kid)
    return out


def _recompute_group_shared_kols(
    conn: Any,
    group_id: str,
    member_ids: list[Any],
    permissions: dict[str, Any] | None,
) -> list[int]:
    """幂等重算:把本组的「组级 shared_kol_pool_ids × 组成员」展开成 vkpi_kol_pool_members 行。
    返回本次真正展开(有成员承接)的 kol_pool_id 列表,供 commit 后做训练信号插桩;未展开返回 []。

    做法(对该 group 的 origin_id,镜像 _recompute_group_shared_projects):
      1) DELETE FROM vkpi_kol_pool_members WHERE shared_via_group_id = <origin_id>
         —— 只删本组产生的行,绝不碰 P-GROUP-7 手动共享行(shared_via_group_id IS NULL)。
      2) 对 (每个组成员 staff_id × 每个 shared_kol_pool_id) INSERT(shared_via_group_id=<origin_id>)。
         vkpi_kol_pool_members 有 UNIQUE(kol_pool_id, staff_id):若该(KOL,人)已存在手动共享行,
         用 ON CONFLICT DO NOTHING 跳过(不抢手动行,手动优先,组共享不覆盖)。
    改组成员/改 shared_kol_pool_ids/删组 都重算 —— 每次「先清本组再重铺」,天然幂等。
    """
    origin_id = _group_origin_id(group_id)
    if origin_id is None:
        # 取不到稳定 origin id → 不展开(避免用伪 id 误删/误插);不阻断分组本身保存。
        return []
    conn.execute(
        "DELETE FROM vkpi_kol_pool_members WHERE shared_via_group_id = ?",
        (origin_id,),
    )
    kol_ids = _shared_kol_ids(permissions)
    staff_ids = [sid for sid in (_int_or_none(m) for m in (member_ids or [])) if sid]
    # 展开前过滤陈旧 id:分组存的 shared_kol_pool_ids/member_ids 可能含已删 kol/staff,
    # 直接 INSERT 会撞 vkpi_kol_pool_members 的 FK(kol_pool_id/staff_id)→ 500 且泄露 PG 原文。
    # 只留真实存在的 id;被过滤掉的陈旧 id 诚实降级(不展开,不阻断分组本身保存)。
    if kol_ids:
        live_kols = _existing_ids(conn, "vkpi_kol_pool", kol_ids)
        kol_ids = [kid for kid in kol_ids if kid in live_kols]
    if staff_ids:
        live_staff = _existing_ids(conn, "staff", staff_ids)
        staff_ids = [sid for sid in staff_ids if sid in live_staff]
    if not kol_ids or not staff_ids:
        # 无(存在的)共享 KOL 或成员 → 上面 DELETE 已清空本组行,直接收尾(空集即「无共享」)。
        return []
    for kid in kol_ids:
        for sid in staff_ids:
            conn.execute(
                """
                INSERT INTO vkpi_kol_pool_members
                    (kol_pool_id, staff_id, shared_by, shared_via_group_id)
                VALUES (?, ?, NULL, ?)
                ON CONFLICT (kol_pool_id, staff_id) DO NOTHING
                """,
                (kid, sid, origin_id),
            )
    return list(kol_ids)


def _bridge_member_feedback(group_id: str, kol_ids: list[int], staff: dict[str, Any] | None) -> None:
    """C4 写口插桩(2026-08-23):组共享成员 = 收藏级正信号("favorite",payload.pool_action="member"),
    即时进推荐反馈(训练信号)。必须在 commit 之后调用;桥 best-effort,失败只告警不阻断分组保存。
    幂等由 actions 按 (recommendation × feedback_type) 去重——每次重算不会堆重复行。"""
    for kid in kol_ids:
        pool_action_bridge.bridge_pool_action(
            kid, "favorite", staff=staff,
            payload={"pool_action": "member", "group_id": str(group_id)},
            source="staff_group_shared_kol",
        )


# ── Staff Groups ────────────────────────────────────────────────────────────
def list_groups(staff: dict[str, Any] | None, *, limit: int = 200) -> dict[str, Any]:
    """列分组:管理层全量;X4-LOW(2026-07-03)非管理层只见自己所在的分组
    (组织架构/团队构成不向普通成员全量外泄)。"""
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 200), 500))
    rows = conn.execute(
        "SELECT * FROM vkpi_staff_groups ORDER BY updated_at DESC, created_at DESC LIMIT ?",
        (safe_limit,),
    ).fetchall()
    items = [_group_row(r) for r in rows]
    role = str((staff or {}).get("role") or "").strip().lower()
    is_manager = int((staff or {}).get("is_owner") or 0) == 1 or role in {
        "admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"
    }
    if not is_manager:
        me = str((staff or {}).get("staff_id") or "")
        items = [
            g for g in items
            if me and any(str(x) == me for x in (g.get("member_ids") or []))
        ]
    return {"items": items}


def get_group(group_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    return {"item": _group_row(row) if row else None}


def create_group(payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    gid = str(payload.get("id") or _gen_id("grp"))
    permissions = {**_DEFAULT_PERMISSIONS, **(payload.get("permissions") or {})}
    members = _normalize_members(payload.get("member_ids"))
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
            _dumps(members),
            _dumps(permissions),
            _staff_id(staff),
        ),
    )
    # 新组保存即展开「组级 shared_projects × 成员」成真访问(P-GROUP-34,幂等)。
    _recompute_group_shared_projects(conn, gid, members, permissions)
    # 同上,展开「组级 shared_kol_pool_ids × 成员」成真 KOL 只读共享(幂等)。
    bridged_kols = _recompute_group_shared_kols(conn, gid, members, permissions)
    conn.commit()
    _bridge_member_feedback(gid, bridged_kols, staff)
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
    row = conn.execute("SELECT * FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    # 改组(成员/shared_projects 任一变)即按「持久化后的真状态」重算共享访问(P-GROUP-34,幂等)。
    # 从回读行取最终 member_ids/permissions,无需分支判断 payload 改了哪个字段。
    bridged_kols: list[int] = []
    if row is not None:
        parsed = _group_row(row)
        _recompute_group_shared_projects(
            conn, str(group_id), parsed.get("member_ids") or [], parsed.get("permissions") or {}
        )
        bridged_kols = _recompute_group_shared_kols(
            conn, str(group_id), parsed.get("member_ids") or [], parsed.get("permissions") or {}
        )
    conn.commit()
    _bridge_member_feedback(str(group_id), bridged_kols, staff)
    return {"item": _group_row(row) if row else None}


def delete_group(group_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    # 删组先撤销该组展开的共享访问(只删本组 origin 的行,不碰手动共享行);幂等。
    _recompute_group_shared_projects(conn, str(group_id), [], {})
    _recompute_group_shared_kols(conn, str(group_id), [], {})
    conn.execute("DELETE FROM vkpi_staff_groups WHERE id = ?", (str(group_id),))
    conn.commit()
    return {"ok": True, "id": str(group_id)}


# ── Members(成员增删,镜像 events team_ids)────────────────────────────────
def _set_members(conn: Any, group_id: str, members: list[Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    conn.execute(
        "UPDATE vkpi_staff_groups SET member_ids = ?::jsonb, updated_at = NOW() WHERE id = ?",
        (_dumps(members), str(group_id)),
    )
    row = conn.execute("SELECT * FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    # 增/删成员即按真状态重算共享访问(新成员获得组共享项目可见,被移除成员失去)(P-GROUP-34,幂等)。
    bridged_kols: list[int] = []
    if row is not None:
        parsed = _group_row(row)
        _recompute_group_shared_projects(
            conn, str(group_id), parsed.get("member_ids") or [], parsed.get("permissions") or {}
        )
        bridged_kols = _recompute_group_shared_kols(
            conn, str(group_id), parsed.get("member_ids") or [], parsed.get("permissions") or {}
        )
    conn.commit()
    _bridge_member_feedback(str(group_id), bridged_kols, staff)
    return {"item": _group_row(row) if row else None}


def add_member(group_id: str, staff_id: Any, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT member_ids FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    members = _loads(dict(row).get("member_ids"), []) if row else []
    if staff_id not in members:
        members.append(staff_id)
    return _set_members(conn, group_id, members, staff=staff)


def remove_member(group_id: str, staff_id: Any, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT member_ids FROM vkpi_staff_groups WHERE id = ?", (str(group_id),)).fetchone()
    members = [m for m in (_loads(dict(row).get("member_ids"), []) if row else []) if str(m) != str(staff_id)]
    return _set_members(conn, group_id, members, staff=staff)
