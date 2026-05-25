"""V-KPI audit read routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains import audit

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-audit"])


def _is_manager_staff(staff: dict) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _require_manager_staff(staff: dict) -> None:
    if not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


@router.get("/audit/sensitive-access")
def audit_sensitive_access(
    limit: int = Query(default=100, ge=1, le=500),
    staff_id: int | None = None,
    action_type: str = "",
    staff=Depends(require_tab("vkpi", "admin")),
):
    _require_manager_staff(staff)
    return audit.list_sensitive_access(limit=limit, staff_id=staff_id, action_type=action_type)


@router.get("/audit/exports")
def audit_exports(limit: int = Query(default=100, ge=1, le=500), staff_id: int | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    _require_manager_staff(staff)
    return audit.list_export_logs(limit=limit, staff_id=staff_id)


@router.get("/audit/settings-changes")
def audit_settings_changes(limit: int = Query(default=100, ge=1, le=500), staff_id: int | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    _require_manager_staff(staff)
    return audit.list_settings_changes(limit=limit, staff_id=staff_id)


@router.get("/audit/unified")
def audit_unified(limit: int = Query(default=100, ge=1, le=500), event_category: str = "", staff_id: int | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    _require_manager_staff(staff)
    return audit.unified(limit=limit, event_category=event_category, staff_id=staff_id)


@router.get("/audit/overview")
def audit_overview(
    limit: int = Query(default=100, ge=1, le=500),
    event_category: str = "",
    staff_id: int | None = None,
    days: int = Query(default=7, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "admin")),
):
    _require_manager_staff(staff)
    return audit.overview(limit=limit, event_category=event_category, staff_id=staff_id, days=days)


@router.get("/audit/summary")
def audit_summary(days: int = Query(default=7, ge=1, le=365), staff=Depends(require_tab("vkpi", "admin"))):
    _require_manager_staff(staff)
    return audit.summary(days=days)
