"""KOL decision audit routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import kol_decisions

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-decisions"])


@router.post("/kol-decisions")
def create_kol_decision(body: dict[str, Any], staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_decisions.create_decision(body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/kol-decisions")
def list_kol_decisions(
    kol_pool_id: int = Query(default=0, ge=0),
    decision_key: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_decisions.list_decisions(kol_pool_id=kol_pool_id, decision_key=decision_key, limit=limit)
