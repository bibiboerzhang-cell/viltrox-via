"""V-KPI settings, provider, crawl, budget, and user preference routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import notification_settings, platform_crawl_settings, scope, settings as vkpi_settings, user_preferences

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-settings"])


def _is_manager_staff(staff: dict) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _require_manager_staff(staff: dict) -> None:
    if not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


@router.get("/settings/providers")
def provider_statuses(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return vkpi_settings.provider_statuses()


@router.post("/settings/providers/{provider}/probe")
async def provider_probe(provider: str, staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return await vkpi_settings.probe(provider)


@router.get("/settings/feature-flags")
def feature_flags(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return platform_crawl_settings.feature_flags()


@router.patch("/settings/feature-flags")
def update_feature_flags(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    return platform_crawl_settings.update_feature_flags(body, staff=staff)


@router.get("/settings/platform-crawl")
def platform_crawl(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return platform_crawl_settings.platform_settings()


@router.patch("/settings/platform-crawl")
def update_platform_crawl(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    return platform_crawl_settings.update_platform_settings(body, staff=staff)


@router.get("/settings/budgets")
def budget_settings(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return platform_crawl_settings.budget_settings()


@router.patch("/settings/budgets")
def update_budget_settings(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    return platform_crawl_settings.update_budget_settings(body, staff=staff)


@router.get("/settings/control-status")
def control_status(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return platform_crawl_settings.control_status()


@router.get("/settings/comment-alerts")
def comment_alert_settings(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return platform_crawl_settings.comment_alert_settings()


@router.patch("/settings/comment-alerts")
def update_comment_alert_settings(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    _require_manager_staff(staff)
    return platform_crawl_settings.update_comment_alert_settings(body, staff=staff)


@router.get("/settings/preferences")
def preference_settings(staff_id: int | None = Query(default=None), staff=Depends(require_tab("vkpi", "read"))):
    try:
        return user_preferences.get_preferences(staff=staff, staff_id=staff_id)
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "preference scope denied") from exc


@router.patch("/settings/preferences")
def update_preference_settings(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return user_preferences.update_preferences(body, staff=staff)
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "preference scope denied") from exc


@router.get("/settings/preferences/list")
def preference_settings_list(limit: int = Query(default=200, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    if not scope.can_view_all(staff):
        raise HTTPException(status_code=403, detail="management permission required")
    return user_preferences.list_preferences(staff=staff, limit=limit)


@router.get("/settings/notifications")
def notifications(staff_id: int | None = Query(default=None), staff=Depends(require_tab("vkpi", "read"))):
    try:
        return notification_settings.get_notification_settings(staff=staff, staff_id=staff_id)
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "notification settings scope denied") from exc


@router.patch("/settings/notifications")
def update_notifications(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return notification_settings.update_notification_settings(body, staff=staff)
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "notification settings scope denied") from exc


@router.get("/settings/notifications/list")
def notifications_list(limit: int = Query(default=200, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    if not scope.can_view_all(staff):
        raise HTTPException(status_code=403, detail="management permission required")
    return notification_settings.list_notification_settings(staff=staff, limit=limit)
