"""V-KPI command center, staff, and dashboard routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import audit, decision_engine, metric_lineage, scope, workflow
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
        effective_staff_id = scope.effective_staff_id(staff, staff_id)
        result = (
            decision_engine.dashboard_view("staff", window_days=window_days, staff_id=effective_staff_id)
            if effective_staff_id
            else decision_engine.dashboard(window_days=window_days)
        )
        lineage = metric_lineage.dashboard_metrics(
            period_days=window_days,
            staff=staff,
            staff_id=effective_staff_id,
            generated_by_staff_id=resolve_staff_id(staff) or None,
        )
        result["metric_run"] = lineage.get("run") or {}
        result["metrics"] = lineage.get("metrics") or []
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
        return decision_engine.revenue_trend(
            window_days=window_days,
            staff_id=scope.effective_staff_id(staff, staff_id),
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
        return decision_engine.product_performance(
            window_days=window_days,
            staff_id=scope.effective_staff_id(staff, staff_id),
            limit=limit,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc

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
        requested_staff_id = staff_id if staff_id is not None else staff_id_from_context(view, staff)
        effective_staff_id = scope.effective_staff_id(staff, requested_staff_id)
        result = decision_engine.dashboard_view(view, window_days=window_days, staff_id=effective_staff_id)
        try:
            lineage = metric_lineage.dashboard_metrics(
                period_days=window_days,
                staff=staff,
                staff_id=effective_staff_id,
                generated_by_staff_id=resolve_staff_id(staff) or None,
            )
            result["metric_run"] = lineage.get("run") or {}
            result["metrics"] = lineage.get("metrics") or []
        except Exception:
            result["metric_run"] = {}
            result["metrics"] = []
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


def staff_id_from_context(view: str, staff: dict) -> int | None:
    if str(view or "").strip().lower() in {"staff", "employee"}:
        return resolve_staff_id(staff) or None
    return None


@router.get("/workflow/stages")
def stages(staff=Depends(require_tab("vkpi", "read"))):
    return workflow.stage_config()

