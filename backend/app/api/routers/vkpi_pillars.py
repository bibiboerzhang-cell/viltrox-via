"""
backend/app/api/routers/vkpi_pillars_router.py

P1.5 API endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_permission
from app.services.vkpi import pillars
from app.services.vkpi.p1_compat import admin_router_prefix


router = APIRouter(prefix=admin_router_prefix("pillars"), tags=["vkpi-pillars"])


@router.get("")
def api_list_pillars(
    staff: dict = Depends(require_permission("vkpi.pillars.read")),
) -> dict[str, Any]:
    """List all configured pillars."""
    return pillars.list_pillars()


@router.post("/classify/{post_id}")
def api_classify_one(
    post_id: int,
    post_table: str = Query("industry_posts"),
    force_reclassify: bool = Query(False),
    staff: dict = Depends(require_permission("vkpi.pillars.classify")),
) -> dict[str, Any]:
    """Classify single post."""
    return pillars.classify_post(
        post_id=post_id,
        post_table=post_table,
        force_reclassify=force_reclassify,
        staff=staff,
    )


@router.post("/classify-batch")
def api_classify_batch(
    post_table: str = Query("industry_posts"),
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(100, ge=1, le=500),
    staff: dict = Depends(require_permission("vkpi.pillars.classify")),
) -> dict[str, Any]:
    """Batch classify recent posts."""
    return pillars.classify_batch(
        post_table=post_table, days=days, limit=limit, staff=staff
    )


@router.post("/backfill")
def api_backfill(
    post_table: str = Query("industry_posts"),
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(5000, ge=1, le=20000),
    staff: dict = Depends(require_permission("vkpi.pillars.backfill")),
) -> dict[str, Any]:
    """Historical backfill."""
    return pillars.backfill_historical(
        post_table=post_table, days=days, limit=limit, staff=staff
    )


@router.get("/stats")
def api_stats(
    days: int = Query(30, ge=1, le=180),
    staff: dict = Depends(require_permission("vkpi.pillars.read")),
) -> dict[str, Any]:
    """Pillar distribution stats."""
    return pillars.stats(days=days)
