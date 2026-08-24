"""V-KPI AI/provider budget guard routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.manager_guard import require_manager_tab
from app.api.dependencies.perms import require_tab
from app.domains.costs import budget_guard, budget_readonly


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-budgets"])


# AI/provider 真实花费属公司级敏感财务读:仅管理层可见(读裁决 2026-07-07,
# 员工不可看 AI 花费;联盟 GMV 另有裁决=员工可看,不在本文件)。
@router.get("/budgets")
def budgets(staff=Depends(require_manager_tab("vkpi", "read"))):
    del staff
    return budget_readonly.list_budget_status_readonly()


@router.post("/budgets/{scope}/update")
def update_budget(scope: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return budget_guard.update_budget(scope, body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/budgets/usage-by-provider")
def usage_by_provider(
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_manager_tab("vkpi", "read")),
):
    del staff
    return budget_guard.usage_by_provider(limit=limit)


@router.get("/budgets/usage-by-cron")
def usage_by_cron(
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_manager_tab("vkpi", "read")),
):
    del staff
    return budget_guard.usage_by_cron(limit=limit)
