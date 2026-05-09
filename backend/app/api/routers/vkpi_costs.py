"""V-KPI cost ledger and product cost catalog routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import costs, scope

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-costs"])


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
@router.get("/costs")
def list_costs(
    project_id: int | None = None,
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return costs.list_costs(project_id=project_id, staff_id=staff_id, limit=limit, staff=staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/costs/{cost_id}")
def get_cost(cost_id: int, staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    try:
        return costs.get_cost(cost_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/costs/{cost_id}")
def update_cost(cost_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return costs.update_cost(cost_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/costs/{cost_id}/approve")
def approve_cost(cost_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return costs.approve_cost(cost_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/costs/{cost_id}/void")
def void_cost(cost_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return costs.void_cost(cost_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/product-costs")
def list_product_costs(
    limit: int = Query(default=200, ge=1, le=500),
    include_inactive: bool = False,
    staff=Depends(require_tab("vkpi", "read")),
):
    _require_manager_staff(staff)
    return costs.list_product_costs(limit=limit, include_inactive=include_inactive)


@router.post("/product-costs")
def upsert_product_cost(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return costs.upsert_product_cost(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
