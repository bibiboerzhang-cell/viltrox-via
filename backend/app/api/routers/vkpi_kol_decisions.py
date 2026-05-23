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


@router.get("/kol-decisions/followups")
def list_kol_decision_followups(
    status: str = Query(default="due"),
    days_after: int = Query(default=30, ge=1, le=365),
    decision_key: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_decisions.list_followup_queue(
        status=status,
        days_after=days_after,
        decision_key=decision_key,
        limit=limit,
    )


@router.post("/kol-decisions/followups")
def create_kol_decision_followup(body: dict[str, Any], staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_decisions.create_followup(body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
