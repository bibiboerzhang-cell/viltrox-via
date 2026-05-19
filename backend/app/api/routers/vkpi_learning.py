"""V-KPI learning loop routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import learning_loop, memory_feedback_backlog, recommendation_feedback_backlog


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


@router.get("/learning/memory-feedback-backlog")
def learning_memory_feedback_backlog(
    entity_type: str = "kol",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    return memory_feedback_backlog.build_memory_feedback_backlog(
        entity_type=entity_type,
        limit=limit,
    )
