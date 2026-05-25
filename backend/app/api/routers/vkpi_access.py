"""V-KPI access-control readiness routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.domains.staff import rbac_status


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-access"])


@router.get("/access/rbac-status")
def get_rbac_status(
    include_staff: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "admin")),
) -> dict:
    del staff
    return rbac_status.build_rbac_status(include_staff=include_staff)
