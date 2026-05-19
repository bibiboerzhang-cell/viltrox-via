"""V-KPI learning loop routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import learning_loop, recommendation_feedback_backlog


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-learning"])


@router.get("/learning/snapshot")
def learning_snapshot(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    del staff
    return learning_loop.build_learning_snapshot()


@router.get("/learning/recommendation-feedback-backlog")
def learning_recommendation_feedback_backlog(
    run_uid: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    return recommendation_feedback_backlog.build_recommendation_feedback_backlog(
        run_uid=run_uid,
        limit=limit,
    )
