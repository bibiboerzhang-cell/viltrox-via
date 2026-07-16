"""V-KPI cost ledger and product cost catalog routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains import costs
from app.domains import business_truth
from app.domains.access import scope

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


def _authorization_or_http(body: dict, *, staff: dict, action: str) -> dict:
    try:
        return business_truth.require_authorization_evidence(body, staff=staff, action=action)
    except business_truth.BusinessTruthWriteBlocked as exc:
        status_code = 409 if exc.reason == "feature_disabled" else 400
        raise HTTPException(status_code=status_code, detail={"reason": exc.reason, "message": str(exc)}) from exc


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
def update_cost(cost_id: int, body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    _require_manager_staff(staff)
    authorization = _authorization_or_http(body, staff=staff, action="cost_update")
    try:
        return costs.update_cost(
            cost_id,
            {**body, "_authorization_evidence": authorization},
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/costs/{cost_id}/approve")
def approve_cost(cost_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    _require_manager_staff(staff)
    payload = body or {}
    authorization = _authorization_or_http(payload, staff=staff, action="actual_cost_approve")
    try:
        return costs.approve_cost(
            cost_id,
            {**payload, "_authorization_evidence": authorization},
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/costs/{cost_id}/void")
def void_cost(cost_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    _require_manager_staff(staff)
    payload = body or {}
    authorization = _authorization_or_http(payload, staff=staff, action="cost_void")
    try:
        return costs.void_cost(
            cost_id,
            {**payload, "_authorization_evidence": authorization},
            staff=staff,
        )
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


@router.get("/product-catalog")
def list_product_catalog(
    limit: int = Query(default=300, ge=1, le=500),
    categories: str = "",
    query: str = "",
    status: str = "",
    staff=Depends(require_tab("vkpi", "read")),
):
    _require_manager_staff(staff)
    category_list = [item.strip() for item in str(categories or "").split(",") if item.strip()]
    return costs.list_product_catalog(limit=limit, categories=category_list, query=query, status=status)


@router.post("/product-costs")
def upsert_product_cost(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return costs.upsert_product_cost(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/product-costs/{product_sku}/verify")
def verify_product_cost(
    product_sku: str,
    body: dict,
    staff=Depends(require_tab("vkpi", "admin")),
):
    _require_manager_staff(staff)
    authorization = _authorization_or_http(body, staff=staff, action="product_cost_verify")
    try:
        return costs.verify_product_cost(
            product_sku,
            body,
            authorization_evidence=authorization,
            staff=staff,
        )
    except costs.ProductCostVerificationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
