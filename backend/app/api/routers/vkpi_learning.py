"""V-KPI learning loop routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies.perms import require_tab
from app.domains.recommendations import feedback_backlog as recommendation_feedback_backlog
from app.services.vkpi import learning_loop, memory_feedback_backlog


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


@router.get("/learning/recommendation-feedback-backlog.csv")
def learning_recommendation_feedback_backlog_csv(
    run_uid: str = "",
    limit: int = Query(default=120, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> Response:
    del staff
    content = recommendation_feedback_backlog.build_recommendation_feedback_backlog_csv(
        run_uid=run_uid,
        limit=limit,
    )
    headers = {"Content-Disposition": 'attachment; filename="vkpi-p13-review-backlog.csv"'}
    return Response(content=content, media_type="text/csv; charset=utf-8", headers=headers)


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
