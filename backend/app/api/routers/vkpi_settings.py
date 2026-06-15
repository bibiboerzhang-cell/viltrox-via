"""V-KPI settings, provider, crawl, budget, and user preference routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains import settings as settings_domain
from app.domains.access import scope
from app.domains.ops import scheduler_registry

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-settings"])


def _require_manager_staff(staff: dict) -> None:
    if not settings_domain.is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


@router.get("/settings/providers")
def provider_statuses(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return settings_domain.provider_statuses()


@router.post("/settings/providers/{provider}/probe")
async def provider_probe(provider: str, staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return await settings_domain.provider_probe(provider)


@router.get("/settings/feature-flags")
def feature_flags(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return settings_domain.feature_flags()


@router.patch("/settings/feature-flags")
def update_feature_flags(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    return settings_domain.update_feature_flags(body, staff=staff)


@router.get("/settings/platform-crawl")
def platform_crawl(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return settings_domain.platform_crawl()


@router.patch("/settings/platform-crawl")
def update_platform_crawl(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    return settings_domain.update_platform_crawl(body, staff=staff)


@router.get("/settings/budgets")
def budget_settings(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return settings_domain.budget_settings()


@router.patch("/settings/budgets")
def update_budget_settings(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    return settings_domain.update_budget_settings(body, staff=staff)


@router.get("/settings/control-status")
def control_status(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return settings_domain.control_status()


@router.get("/settings/comment-alerts")
def comment_alert_settings(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return settings_domain.comment_alert_settings()


@router.patch("/settings/comment-alerts")
def update_comment_alert_settings(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    _require_manager_staff(staff)
    return settings_domain.update_comment_alert_settings(body, staff=staff)


@router.get("/settings/preferences")
def preference_settings(staff_id: int | None = Query(default=None), staff=Depends(require_tab("vkpi", "read"))):
    try:
        return settings_domain.preference_settings(staff=staff, staff_id=staff_id)
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "preference scope denied") from exc


@router.patch("/settings/preferences")
def update_preference_settings(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return settings_domain.update_preference_settings(body, staff=staff)
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "preference scope denied") from exc


@router.get("/settings/preferences/list")
def preference_settings_list(limit: int = Query(default=200, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    if not settings_domain.can_view_all(staff):
        raise HTTPException(status_code=403, detail="management permission required")
    return settings_domain.preference_settings_list(staff=staff, limit=limit)


@router.get("/settings/notifications")
def notifications(staff_id: int | None = Query(default=None), staff=Depends(require_tab("vkpi", "read"))):
    try:
        return settings_domain.notifications(staff=staff, staff_id=staff_id)
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "notification settings scope denied") from exc


@router.patch("/settings/notifications")
def update_notifications(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return settings_domain.update_notifications(body, staff=staff)
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "notification settings scope denied") from exc


@router.get("/settings/notifications/list")
def notifications_list(limit: int = Query(default=200, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    if not settings_domain.can_view_all(staff):
        raise HTTPException(status_code=403, detail="management permission required")
    return settings_domain.notifications_list(staff=staff, limit=limit)


# ---------------------------------------------------------------------------
# Scheduler-task registry (S1) — visibility + enable toggles ONLY.
# 红线:本期仅注册 + 可见 + 翻 enabled 开关;不执行、不 auto-run 任何任务。
# 执行链(调度器接线)留待后续单独接入。
# ---------------------------------------------------------------------------
@router.get("/settings/scheduler-tasks")
def scheduler_tasks(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return {
        "tasks": scheduler_registry.list_scheduler_tasks(),
        "status": scheduler_registry.scheduler_status(),
    }


@router.patch("/settings/scheduler-tasks/{task_key}")
def update_scheduler_task(task_key: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    if "enabled" not in (body or {}):
        raise HTTPException(status_code=400, detail="enabled (bool) required")
    try:
        task = scheduler_registry.set_scheduler_task_enabled(task_key, bool(body.get("enabled")), staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "scheduler task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "invalid scheduler task request") from exc
    return {"task": task, "status": scheduler_registry.scheduler_status()}
