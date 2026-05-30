"""Dashboard KOL account picker API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies.perms import require_tab
from app.domains.dashboard.account_picker import (
    build_dashboard_account_map,
    build_dashboard_kpi,
    list_dashboard_accounts,
)

router = APIRouter(prefix="/api/admin/dashboard", tags=["dashboard-account-picker"])


class DashboardKpiRequest(BaseModel):
    account_type: str = "all"
    selected_kol_ids: list[str] = Field(default_factory=list)


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/kpi")
def dashboard_kpi(body: DashboardKpiRequest, staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    del staff
    try:
        return build_dashboard_kpi(
            account_type=body.account_type,
            selected_kol_ids=body.selected_kol_ids,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/kols")
def dashboard_kols(
    account_type: str = Query(default="all"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    del staff
    try:
        return list_dashboard_accounts(
            account_type=account_type,
            page=page,
            page_size=page_size,
            search=search,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc


@router.get("/map")
def dashboard_map(
    account_type: str = Query(default="all"),
    selected_kol_ids: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    del staff
    selected = [item.strip() for item in str(selected_kol_ids or "").split(",") if item.strip()]
    try:
        return build_dashboard_account_map(account_type=account_type, selected_kol_ids=selected)
    except ValueError as exc:
        raise _bad_request(exc) from exc
