"""V-KPI Events 路由 — 活动 CRUD + tasks/expenses/members/kols(多人协作)。

前缀 /api/admin/vkpi/events;读 require_tab("vkpi","read")、写 require_tab("vkpi","write")。
员工 scope 在 service 层(list_events 已按 owner/team_ids 过滤);写操作目前对 vkpi:write 放行。
与 KOL Pool / 评分域物理隔离;迁移 122 的 4 表。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.events import service


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


# ── Events ────────────────────────────────────────────────────────────────
@router.get("")
def list_events(limit: int = Query(default=200, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    return _guard(service.list_events, staff, limit=limit)


@router.get("/{event_id}")
def get_event(event_id: str, staff=Depends(require_tab("vkpi", "read"))):
    return _guard(service.get_event_detail, event_id, staff)


@router.post("")
def create_event(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.create_event, body or {}, staff)


@router.patch("/{event_id}")
def update_event(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.update_event, event_id, body or {}, staff)


@router.delete("/{event_id}")
def delete_event(event_id: str, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.delete_event, event_id, staff)


# ── Members(多人协作)──────────────────────────────────────────────────────
@router.post("/{event_id}/members")
def add_member(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.add_member, event_id, (body or {}).get("user_id"), staff)


@router.delete("/{event_id}/members/{user_id}")
def remove_member(event_id: str, user_id: str, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.remove_member, event_id, user_id, staff)


# ── Tasks ─────────────────────────────────────────────────────────────────
@router.post("/{event_id}/tasks")
def add_task(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.add_task, event_id, body or {}, staff)


@router.patch("/{event_id}/tasks/{task_id}")
def update_task(event_id: str, task_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.update_task, event_id, task_id, body or {}, staff)


@router.delete("/{event_id}/tasks/{task_id}")
def delete_task(event_id: str, task_id: str, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.delete_task, event_id, task_id, staff)


# ── Expenses ──────────────────────────────────────────────────────────────
@router.post("/{event_id}/expenses")
def add_expense(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.add_expense, event_id, body or {}, staff)


@router.delete("/{event_id}/expenses/{expense_id}")
def delete_expense(event_id: str, expense_id: str, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.delete_expense, event_id, expense_id, staff)


# ── KOL invites ───────────────────────────────────────────────────────────
@router.post("/{event_id}/kols")
def invite_kol(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.invite_kol, event_id, body or {}, staff)


@router.delete("/{event_id}/kols/{invite_id}")
def remove_kol(event_id: str, invite_id: str, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.remove_kol, event_id, invite_id, staff)
