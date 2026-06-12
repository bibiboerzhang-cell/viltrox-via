"""V-KPI data-scope enforcement.

Frontend staff_id/view_as_staff_id is only a hint. Backend read/write paths
must reduce it to the actor's allowed scope before hitting business tables.
"""
from __future__ import annotations

from typing import Any

from app.core.permissions import normalize_permissions
from app.db.connection import get_conn


MANAGER_ROLES = {"admin", "owner", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}
FINANCE_ROLES = {"finance", "accounting"}


class ScopeDenied(PermissionError):
    """Raised when a staff actor attempts to view or mutate out-of-scope data."""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def actor_staff_id(staff: dict[str, Any] | None) -> int:
    if not staff:
        return 0
    return _int(staff.get("id") or staff.get("staff_id") or staff.get("user_id"))


def role_key(staff: dict[str, Any] | None) -> str:
    return str((staff or {}).get("role") or "").strip().lower()


def is_owner(staff: dict[str, Any] | None) -> bool:
    return bool(staff) and _int((staff or {}).get("is_owner")) == 1


def can_view_all(staff: dict[str, Any] | None, *, domain: str = "general") -> bool:
    role = role_key(staff)
    if is_owner(staff):
        return True
    if role == "admin":
        permissions = normalize_permissions(
            (staff or {}).get("permissions_json") or (staff or {}).get("permissions"),
            role,
            owner=False,
        )
        return str(permissions.get("vkpi") or "none").lower() == "admin"
    if role in MANAGER_ROLES:
        return True
    if domain in {"cost", "finance", "export"} and role in FINANCE_ROLES:
        return True
    return False


def scope_context(
    staff: dict[str, Any] | None,
    requested_staff_id: int | None = None,
    *,
    domain: str = "general",
) -> dict[str, Any]:
    """Return the actual backend scope used for a list/read request.

    The frontend may request another staff_id, but non-manager actors are
    always reduced to their own staff id. Returning this context makes that
    reduction visible to the UI and prevents "view all" assumptions from
    leaking into new pages.
    """
    actor = actor_staff_id(staff)
    requested = _int(requested_staff_id)
    can_all = can_view_all(staff, domain=domain)
    effective = effective_staff_id(staff, requested_staff_id, domain=domain)
    if not staff:
        mode = "anonymous"
    elif can_all and requested:
        mode = "requested_staff"
    elif can_all:
        mode = "all"
    else:
        mode = "own"
    return {
        "actor_staff_id": actor or None,
        "requested_staff_id": requested or None,
        "effective_staff_id": effective or None,
        "can_view_all": bool(can_all),
        "scope_mode": mode,
        "role": role_key(staff),
        "is_owner": is_owner(staff),
        "domain": domain,
    }


def effective_staff_id(staff: dict[str, Any] | None, requested_staff_id: int | None = None, *, domain: str = "general") -> int | None:
    requested = _int(requested_staff_id)
    if can_view_all(staff, domain=domain):
        return requested or None
    actor = actor_staff_id(staff)
    return actor or None


def staff_filter(column_sql: str, staff: dict[str, Any] | None, requested_staff_id: int | None = None, *, domain: str = "general") -> tuple[str, list[Any]]:
    scoped_staff_id = effective_staff_id(staff, requested_staff_id, domain=domain)
    if not scoped_staff_id:
        return "", []
    return f"{column_sql} = ?", [scoped_staff_id]


def project_filter(alias: str, staff: dict[str, Any] | None, requested_staff_id: int | None = None) -> tuple[str, list[Any]]:
    """PV-3 裁决(2026-06-12):员工默认可见全部项目 + 例外遮蔽制。

    旧口径(assigned/created_by 归属过滤)在 33 个项目归属键全 NULL 的现实下
    把 14 个员工挡成空列表。新口径:admin 全可见(含 restricted);非 admin 可见
    全部非 restricted 项目(migration 110;先遮后开铁则——14 个 smoke/测试项目
    已先标 restricted=TRUE 再落本反转)。requested_staff_id 仍生效:显式按人
    筛选时叠加归属条件(查询语义,非权限)。
    """
    prefix = f"{alias}." if alias else ""
    if can_view_all(staff):
        if requested_staff_id:
            return f"({prefix}assigned_staff_id = ? OR {prefix}created_by_staff_id = ?)", [int(requested_staff_id), int(requested_staff_id)]
        return "", []
    clause = f"COALESCE({prefix}restricted, FALSE) = FALSE"
    params: list[Any] = []
    if requested_staff_id:
        clause += f" AND ({prefix}assigned_staff_id = ? OR {prefix}created_by_staff_id = ?)"
        params = [int(requested_staff_id), int(requested_staff_id)]
    return f"({clause})", params


def link_filter(alias: str, staff: dict[str, Any] | None, requested_staff_id: int | None = None) -> tuple[str, list[Any]]:
    scoped_staff_id = effective_staff_id(staff, requested_staff_id)
    if not scoped_staff_id:
        return "", []
    prefix = f"{alias}." if alias else ""
    return f"({prefix}staff_id = ? OR {prefix}created_by_staff_id = ?)", [scoped_staff_id, scoped_staff_id]


def row_staff_filter(alias: str, staff: dict[str, Any] | None, requested_staff_id: int | None = None, *, column: str = "staff_id", domain: str = "general") -> tuple[str, list[Any]]:
    scoped_staff_id = effective_staff_id(staff, requested_staff_id, domain=domain)
    if not scoped_staff_id:
        return "", []
    prefix = f"{alias}." if alias else ""
    return f"{prefix}{column} = ?", [scoped_staff_id]


def assert_project_access(project_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> None:
    if can_view_all(staff):
        return
    actor = actor_staff_id(staff)
    if not actor:
        raise ScopeDenied("project scope denied")
    row = get_conn().execute(
        """
        SELECT assigned_staff_id, created_by_staff_id, COALESCE(restricted, FALSE) AS restricted
        FROM vkpi_projects
        WHERE id=?
        """,
        (int(project_id),),
    ).fetchone()
    if not row:
        return
    item = dict(row)
    if actor in {_int(item.get("assigned_staff_id")), _int(item.get("created_by_staff_id"))}:
        return
    # PV-3 对齐(2026-06-12 添加KOL弹窗 403 案 → 全盘扫描 P0 写侧跟进):
    # 非 restricted 项目对员工读写均放行——存量项目 75% 双归属 NULL,只开读会让
    # 推进/合同/截图/留档全部 403,与旅程"员工往项目塞人"相悖(候追认)。
    # restricted 项目仍只认 assigned/creator/全可见角色(先遮后开铁则不破)。
    if not bool(item.get("restricted")):
        return
    raise ScopeDenied("project scope denied")


def is_project_member(project_id: int, staff: dict[str, Any] | None) -> bool:
    """True 当 actor 是该项目的 assigned 或 creator(批D 收款遮蔽豁免判定用)。"""
    actor = actor_staff_id(staff)
    if not actor:
        return False
    row = get_conn().execute(
        "SELECT assigned_staff_id, created_by_staff_id FROM vkpi_projects WHERE id=?",
        (int(project_id),),
    ).fetchone()
    if not row:
        return False
    item = dict(row)
    return actor in {_int(item.get("assigned_staff_id")), _int(item.get("created_by_staff_id"))}


_ANALYSIS_TARGET_PROJECT_TABLES = {
    "contract": "vkpi_project_contracts",
    "video": "vkpi_kol_video_evidence",
}


def resolve_analysis_target_project(target_type: str, target_id: str | int) -> int | None:
    """批D 权限收口(2026-06-12):把分析缓存读目标映射回所属项目。

    project → 自身;contract/video → 反查行上的 project_id;kol_pool 等无项目
    维度的目标、或反查不到(历史 NULL 归属)→ 返回 None,调用方维持 tab 级权限,
    不额外收紧(诚实降级,不假装有归属)。
    """
    kind = str(target_type or "").strip().lower()
    try:
        row_id = int(str(target_id).strip())
    except (TypeError, ValueError):
        return None
    if kind == "project":
        return row_id
    table = _ANALYSIS_TARGET_PROJECT_TABLES.get(kind)
    if not table:
        return None
    row = get_conn().execute(
        f"SELECT project_id FROM {table} WHERE id=?",  # noqa: S608 - table 来自白名单映射
        (row_id,),
    ).fetchone()
    if not row:
        return None
    project_id = _int(dict(row).get("project_id"))
    return project_id or None


def assert_link_access(link_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> None:
    if can_view_all(staff):
        return
    actor = actor_staff_id(staff)
    if not actor:
        raise ScopeDenied("link scope denied")
    row = get_conn().execute(
        """
        SELECT l.staff_id, l.created_by_staff_id, p.assigned_staff_id, p.created_by_staff_id AS project_creator_id
        FROM vkpi_links l
        LEFT JOIN vkpi_projects p ON p.id = l.project_id
        WHERE l.id=?
        """,
        (int(link_id),),
    ).fetchone()
    if not row:
        return
    item = dict(row)
    allowed = {
        _int(item.get("staff_id")),
        _int(item.get("created_by_staff_id")),
        _int(item.get("assigned_staff_id")),
        _int(item.get("project_creator_id")),
    }
    if actor in allowed:
        return
    raise ScopeDenied("link scope denied")


def assert_staff_access(target_staff_id: int | None, staff: dict[str, Any] | None, *, domain: str = "general") -> None:
    target = _int(target_staff_id)
    if not target or can_view_all(staff, domain=domain):
        return
    if target != actor_staff_id(staff):
        raise ScopeDenied("staff scope denied")
