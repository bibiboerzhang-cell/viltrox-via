"""V-KPI learning loop routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.services.vkpi import learning_loop


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-learning"])


@router.get("/learning/snapshot")
def learning_snapshot(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    del staff
    return learning_loop.build_learning_snapshot()
