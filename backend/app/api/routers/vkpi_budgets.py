"""V-KPI AI/provider budget guard routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import budget_guard


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-budgets"])


@router.get("/budgets")
def budgets(staff=Depends(require_tab("vkpi", "read"))):
    del staff
    return budget_guard.get_budget_status()


@router.post("/budgets/{scope}/update")
def update_budget(scope: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return budget_guard.update_budget(scope, body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/budgets/usage-by-provider")
def usage_by_provider(
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return budget_guard.usage_by_provider(limit=limit)


@router.get("/budgets/usage-by-cron")
def usage_by_cron(
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return budget_guard.usage_by_cron(limit=limit)
