"""V-KPI reconciliation and attribution adjustment routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import reconciliation

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-reconciliation"])


def _is_manager_staff(staff: dict) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _require_manager_staff(staff: dict) -> None:
    if not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


@router.get("/reconciliation/stats")
def reconciliation_stats(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return reconciliation.stats()


@router.get("/reconciliation/queue")
def reconciliation_queue(
    status: str = "pending",
    assigned_to_staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    _require_manager_staff(staff)
    return reconciliation.list_queue(status=status, assigned_to_staff_id=assigned_to_staff_id, limit=limit)


@router.get("/reconciliation/queue/{queue_id}")
def reconciliation_item(queue_id: int, staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    try:
        return reconciliation.get_item(queue_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/reconciliation/queue/{queue_id}/resolve")
def reconciliation_resolve(queue_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return reconciliation.resolve(queue_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reconciliation/queue/{queue_id}/reject")
def reconciliation_reject(queue_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return reconciliation.reject(queue_id, str((body or {}).get("reason") or (body or {}).get("rejection_reason") or ""), staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/reconciliation/queue/{queue_id}/assign")
def reconciliation_assign(queue_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return reconciliation.assign(queue_id, int(body.get("assigned_to_staff_id") or body.get("staff_id") or 0), staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reconciliation/sync-logs")
def reconciliation_sync_logs(limit: int = Query(default=100, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return reconciliation.sync_logs(limit=limit)


@router.post("/attribution/{attribution_id}/adjust")
def attribution_adjust(attribution_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return reconciliation.adjust_attribution(attribution_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/attribution/{attribution_id}/void")
def attribution_void(attribution_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return reconciliation.void_attribution(attribution_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/attribution/{attribution_id}/history")
def attribution_history(attribution_id: int, staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    try:
        return reconciliation.attribution_history(attribution_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
