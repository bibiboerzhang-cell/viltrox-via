"""V-KPI team feedback endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import team_feedback

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-feedback"])


@router.post("/feedback")
def create_feedback(body: dict[str, Any], staff=Depends(require_tab("vkpi", "write"))):
    try:
        return team_feedback.create_feedback(body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/feedback")
def list_feedback(
    status: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "admin")),
):
    return team_feedback.list_feedback(status=status, limit=limit)


@router.patch("/feedback/{uid}")
def update_feedback(uid: str, body: dict[str, Any], staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return team_feedback.update_feedback_status(uid, body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
