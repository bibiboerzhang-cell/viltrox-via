"""V-KPI deterministic natural search routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.search import natural_search


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-search"])


@router.get("/search")
def vkpi_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        return natural_search.search(q, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
