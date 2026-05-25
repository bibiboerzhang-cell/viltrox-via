"""
backend/app/api/routers/vkpi_sentiment.py

P1.4 API endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

import app.domains.comments.sentiment as sentiment
from app.api.dependencies.perms import require_permission
from app.services.vkpi.p1_compat import admin_router_prefix


router = APIRouter(prefix=admin_router_prefix("sentiment"), tags=["vkpi-sentiment"])


@router.post("/analyze/{comment_id}")
def api_analyze_one(
    comment_id: int,
    force_reanalyze: bool = Query(False),
    staff: dict = Depends(require_permission("vkpi.sentiment.analyze")),
) -> dict[str, Any]:
    """Analyze single comment."""
    return sentiment.analyze_comment(
        comment_id, force_reanalyze=force_reanalyze, staff=staff
    )


@router.post("/analyze-batch")
def api_analyze_batch(
    comment_ids: list[int],
    staff: dict = Depends(require_permission("vkpi.sentiment.analyze")),
) -> dict[str, Any]:
    """Batch analyze comments."""
    return sentiment.analyze_batch(comment_ids, staff=staff)


@router.post("/backfill")
def api_backfill(
    platform: str = Query(""),
    days: int = Query(30, ge=1, le=180),
    limit: int = Query(1000, ge=1, le=5000),
    staff: dict = Depends(require_permission("vkpi.sentiment.backfill")),
) -> dict[str, Any]:
    """Backfill historical comments."""
    return sentiment.backfill_historical(
        platform=platform, days=days, limit=limit, staff=staff
    )


@router.get("/stats")
def api_stats(
    days: int = Query(30, ge=1, le=180),
    staff: dict = Depends(require_permission("vkpi.sentiment.read")),
) -> dict[str, Any]:
    """Sentiment statistics."""
    return sentiment.stats(days=days)
