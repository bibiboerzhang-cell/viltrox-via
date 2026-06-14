"""V-KPI Staff Groups 路由 — 员工分组(团队)CRUD + 成员增删。

前缀 /api/admin/vkpi/staff-groups;读 require_tab("vkpi","read")、写 require_tab("vkpi","write")。
供 TeamModal/EditGroupModal/Events 选人复用;member_ids 镜像 vkpi_events.team_ids。
与 KOL Pool / 评分域物理隔离;迁移 123 的单表 vkpi_staff_groups。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.staff_groups import service


router = APIRouter(prefix="/api/admin/vkpi/staff-groups", tags=["vkpi-staff-groups"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface DB/serialize errors as 500 with msg
        raise HTTPException(status_code=500, detail=f"staff-groups error: {exc}") from exc


# ── Groups ──────────────────────────────────────────────────────────────────
@router.get("")
def list_groups(limit: int = Query(default=200, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    return _guard(service.list_groups, staff, limit=limit)


@router.post("")
def create_group(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.create_group, body or {}, staff)


@router.patch("/{group_id}")
def update_group(group_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.update_group, group_id, body or {}, staff)


@router.delete("/{group_id}")
def delete_group(group_id: str, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.delete_group, group_id, staff)


# ── Members(成员增删)──────────────────────────────────────────────────────
@router.post("/{group_id}/members")
def add_member(group_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.add_member, group_id, (body or {}).get("staff_id"), staff)


@router.delete("/{group_id}/members/{staff_id}")
def remove_member(group_id: str, staff_id: str, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.remove_member, group_id, staff_id, staff)
