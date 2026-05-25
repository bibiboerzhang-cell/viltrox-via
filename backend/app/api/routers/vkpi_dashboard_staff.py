"""V-KPI command center, staff, and dashboard routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains import dashboard as dashboard_domain
from app.services.vkpi import audit, decision_engine, scope, workflow
from app.services.vkpi.workflow import staff_id as resolve_staff_id

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-dashboard"])


def _is_manager_staff(staff: dict) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _require_manager_staff(staff: dict) -> None:
    if not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


@router.get("/architecture")
def architecture(staff=Depends(require_tab("vkpi", "read"))):
    return workflow.architecture_summary()


@router.get("/dashboard")
def dashboard(
    window_days: int = 30,
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        result = dashboard_domain.build_dashboard_summary(
            window_days=window_days,
            staff_id=staff_id,
            staff=staff,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return result


@router.get("/dashboard/revenue-trend")
def dashboard_revenue_trend(
    window_days: int = 7,
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return dashboard_domain.build_revenue_trend(
            window_days=window_days,
            staff_id=staff_id,
            staff=staff,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/dashboard/product-performance")
def dashboard_product_performance(
    window_days: int = 30,
    staff_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return dashboard_domain.build_product_performance(
            window_days=window_days,
            staff_id=staff_id,
            limit=limit,
            staff=staff,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/dashboard/kol-distribution")
def dashboard_kol_distribution(
    limit: int = Query(default=200, ge=1, le=250),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return real KOL country distribution for the premium dashboard map."""
    del staff
    return dashboard_domain.build_dashboard_kol_distribution(limit=limit)


@router.get("/dashboard/agents-status")
def dashboard_agents_status(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return read-only dashboard status for existing V-KPI agents."""
    del staff
    return dashboard_domain.build_dashboard_agents_status()


@router.get("/dashboard/copilot-brief")
def dashboard_copilot_brief(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return the latest read-only brief-agent artifact for Dashboard Copilot."""
    del staff
    return dashboard_domain._build_dashboard_copilot_brief()


@router.get("/dashboard/tasks")
def dashboard_tasks(
    limit: int = Query(default=6, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return dashboard task candidates from the latest recommendation-agent artifact."""
    del staff
    return dashboard_domain._build_dashboard_tasks(limit=limit)


@router.get("/dashboard/agents/inbox")
def dashboard_agents_inbox(
    limit: int = Query(default=50, ge=1, le=100),
    agent_id: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return read-only inbox items from existing runtime/ops agent artifacts."""
    del staff
    return dashboard_domain._build_dashboard_agents_inbox(limit=limit, agent_id=agent_id)


@router.get("/dashboard/recent-content")
def dashboard_recent_content(
    limit: int = Query(default=12, ge=1, le=30),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return recent content rows for the glass dashboard content panel."""
    del staff
    return dashboard_domain.build_dashboard_recent_content(limit=limit)


@router.get("/staff-directory")
def staff_directory(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return decision_engine.staff_directory()


@router.get("/staff/{staff_id}/profile")
def staff_profile(
    staff_id: int,
    window: str = Query(default="month", pattern="^(today|day|daily|1d|7d|week|weekly|30d|month|monthly)$"),
    limit: int = Query(default=80, ge=1, le=300),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        result = decision_engine.staff_profile(staff_id, staff=staff, window=window, limit=limit)
        audit.log_sensitive_access(
            staff_id=resolve_staff_id(staff),
            action_type="view_staff_profile",
            resource_type="staff",
            resource_id=str(staff_id),
            page_path=f"/api/admin/vkpi/staff/{staff_id}/profile",
            metadata={"window": window, "limit": limit, "costs_visible": result.get("visibility", {}).get("costs_visible")},
        )
        audit.log_business_event(
            staff_id=resolve_staff_id(staff),
            action_type="staff_profile_view",
            target_type="staff",
            target_id=staff_id,
            detail="view employee V-KPI profile",
            metadata={"window": window, "limit": limit},
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/staff-kpi")
def staff_kpi(
    window: str = Query(default="month", pattern="^(today|day|daily|1d|7d|week|weekly|30d|month|monthly)$"),
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return decision_engine.staff_kpi(window=window, staff_id=scope.effective_staff_id(staff, staff_id))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/employee-workspace")
def employee_workspace(
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    effective_staff_id = scope.effective_staff_id(staff, staff_id) or resolve_staff_id(staff)
    return decision_engine.employee_workspace(int(effective_staff_id or 0))


@router.get("/dashboard/view/{view}")
def dashboard_view(
    view: str,
    window_days: int = 30,
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return dashboard_domain.build_dashboard_view_payload(
            view=view,
            window_days=window_days,
            staff_id=staff_id,
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/workflow/stages")
def stages(staff=Depends(require_tab("vkpi", "read"))):
    return workflow.stage_config()
