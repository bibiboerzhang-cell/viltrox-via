"""V-KPI read-only operating review routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.domains.operations import operating_review


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-operating-review"])


@router.get("/operating-review/status")
def operating_review_status(
    limit: int = Query(default=25, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    return operating_review.build_operating_review(limit=limit)
