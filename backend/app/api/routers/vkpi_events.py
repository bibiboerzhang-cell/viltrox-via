"""V-KPI Events 路由 — 活动 CRUD + tasks/expenses/members/kols(多人协作)。

前缀 /api/admin/vkpi/events;读 require_tab("vkpi","read")、写 require_tab("vkpi","write")。
员工 scope 在 service 层(list_events 已按 owner/team_ids/共享成员 过滤);写操作再经
scope.assert_event_access(..., write=True)收口 —— 不再是任何 vkpi:write 都能改任意活动,
只有 owner / team 成员 / editor 共享成员 / admin(can_view_all)可写(2026-06-14,镜像项目共享)。
与 KOL Pool / 评分域物理隔离;迁移 122 的 4 表 + 迁移 132 的共享成员表。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.access import scope
from app.domains.events import event_members, service


router = APIRouter(prefix="/api/admin/vkpi/events", tags=["vkpi-events"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface DB/serialize errors as 500 with msg
        raise HTTPException(status_code=500, detail=f"events error: {exc}") from exc


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


def _assert_write(event_id: str, staff) -> None:
    """写收口:owner / team 成员 / editor 共享成员 / admin 才可改本活动,否则 403。"""
    try:
        scope.assert_event_access(str(event_id), staff, write=True)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


def _assert_read(event_id: str, staff) -> None:
    """读收口(P0):直连 GET /{event_id} 此前裸奔,任意 vkpi:read 员工可拉任意活动
    的预算/团队/费用/邀约。这里按活动级 scope 把关 —— owner / team / 共享成员 /
    is_public 读 / admin 才能读,否则 403。镜像 share-members 读端 + 项目详情口径。"""
    try:
        scope.assert_event_access(str(event_id), staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


# ── Events ────────────────────────────────────────────────────────────────
@router.get("")
def list_events(limit: int = Query(default=200, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    return _guard(service.list_events, staff, limit=limit)


# ── Share members(真·活动共享:必须在动态 /{event_id} 路由之前注册)─────────────
@router.get("/{event_id}/share-members")
def list_event_share_members(event_id: str, staff=Depends(require_tab("vkpi", "read"))):
    """真·活动共享成员列表。读端按活动级 scope 把关:能看见该活动者
    (owner / team / 共享成员 / admin)才能列其成员名单。"""
    try:
        scope.assert_event_access(str(event_id), staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return _guard(event_members.list_members, str(event_id))


@router.post("/{event_id}/share-members")
def add_event_share_member(event_id: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    """把活动共享给某员工(只有活动 owner 或 can_view_all 可加)。
    body: {staff_id, role}('viewer' 只读 / 'editor' 可写)。"""
    try:
        event_members.assert_can_manage_members(str(event_id), staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    target_staff_id = body.get("staff_id")
    if target_staff_id in (None, "", 0, "0"):
        raise HTTPException(status_code=400, detail="staff_id required")
    result = event_members.add_member(
        str(event_id),
        int(target_staff_id),
        role=str(body.get("role") or "viewer"),
        added_by_staff_id=scope.actor_staff_id(staff) or None,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "add member failed")
    return result


@router.delete("/{event_id}/share-members/{staff_id}")
def remove_event_share_member(event_id: str, staff_id: int, staff=Depends(require_tab("vkpi", "write"))):
    """撤销共享(只有活动 owner 或 can_view_all 可删)。"""
    try:
        event_members.assert_can_manage_members(str(event_id), staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    result = event_members.remove_member(str(event_id), int(staff_id), removed_by_staff_id=scope.actor_staff_id(staff) or None)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "remove member failed")
    return result


@router.get("/{event_id}")
def get_event(event_id: str, staff=Depends(require_tab("vkpi", "read"))):
    _assert_read(event_id, staff)
    return _guard(service.get_event_detail, event_id, staff)


@router.post("")
def create_event(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.create_event, body or {}, staff)


@router.patch("/{event_id}")
def update_event(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.update_event, event_id, body or {}, staff)


@router.delete("/{event_id}")
def delete_event(event_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.delete_event, event_id, staff)


# ── Members(多人协作:team_ids)─────────────────────────────────────────────
@router.post("/{event_id}/members")
def add_member(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.add_member, event_id, (body or {}).get("user_id"), staff)


@router.delete("/{event_id}/members/{user_id}")
def remove_member(event_id: str, user_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.remove_member, event_id, user_id, staff)


# ── Tasks ─────────────────────────────────────────────────────────────────
@router.post("/{event_id}/tasks")
def add_task(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.add_task, event_id, body or {}, staff)


@router.patch("/{event_id}/tasks/{task_id}")
def update_task(event_id: str, task_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.update_task, event_id, task_id, body or {}, staff)


@router.delete("/{event_id}/tasks/{task_id}")
def delete_task(event_id: str, task_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.delete_task, event_id, task_id, staff)


# ── Expenses ──────────────────────────────────────────────────────────────
@router.post("/{event_id}/expenses")
def add_expense(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.add_expense, event_id, body or {}, staff)


@router.delete("/{event_id}/expenses/{expense_id}")
def delete_expense(event_id: str, expense_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.delete_expense, event_id, expense_id, staff)


# ── KOL invites ───────────────────────────────────────────────────────────
@router.post("/{event_id}/kols")
def invite_kol(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.invite_kol, event_id, body or {}, staff)


@router.delete("/{event_id}/kols/{invite_id}")
def remove_kol(event_id: str, invite_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.remove_kol, event_id, invite_id, staff)
