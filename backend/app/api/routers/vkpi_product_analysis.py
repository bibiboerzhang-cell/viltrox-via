"""V-KPI product analysis and recommendation routes.

This module keeps the public API paths identical to the old monolithic vkpi
router while moving one bounded domain out of the 2k-line router file.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.legacy_scope import legacy_system_admin_scope_guard
from app.api.dependencies.perms import require_tab
from app.domains.kol import pool as kol_pool
from app.domains.recommendations import product_analysis

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-product-analysis"])


def _require_legacy_recommendation_scope(staff: dict) -> None:
    unavailable = legacy_system_admin_scope_guard(
        staff,
        surface="Product recommendation action",
    )
    if unavailable is not None:
        raise HTTPException(status_code=403, detail=unavailable)


@router.get("/product-analysis/launches")
def product_analysis_launches(
    limit: int = Query(default=100, ge=1, le=300),
    status: str = "",
    staff=Depends(require_tab("vkpi", "read")),
):
    return product_analysis.list_launches(limit=limit, status=status)


@router.post("/product-analysis/launches")
def product_analysis_create_launch(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return product_analysis.create_launch(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/product-analysis/launches/{launch_id}")
def product_analysis_get_launch(launch_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return product_analysis.get_launch(launch_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/product-analysis/launches/{launch_id}")
def product_analysis_update_launch(launch_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return product_analysis.update_launch(launch_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/product-analysis/launches/{launch_id}")
def product_analysis_delete_launch(launch_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return product_analysis.delete_launch(launch_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/product-analysis/kol-pool/import")
def product_analysis_import_kol_pool(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    items = body.get("items") if isinstance(body.get("items"), list) else []
    return kol_pool.import_items(
        items,
        source_type=str(body.get("source_type") or "manual"),
        source_ref=str(body.get("source_ref") or ""),
        platform=str(body.get("platform") or ""),
        staff=staff,
    )


@router.get("/product-analysis/kol-pool")
def product_analysis_kol_pool(
    limit: int = Query(default=100, ge=1, le=500),
    platform: str = "",
    query: str = "",
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_pool.list_pool(limit=limit, platform=platform, query=query)


@router.post("/product-analysis/recommendations/run")
def product_analysis_run_recommendations(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return product_analysis.run_recommendations(body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/product-analysis/recommendations")
def product_analysis_recommendations(
    launch_id: int | None = None,
    run_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return product_analysis.list_recommendations(launch_id=launch_id, run_id=run_id, limit=limit)


@router.get("/product-analysis/recommendation-runs")
def product_analysis_recommendation_runs(
    strategy_version: str = "",
    status: str = "",
    limit: int = Query(default=100, ge=1, le=300),
    staff=Depends(require_tab("vkpi", "read")),
):
    return product_analysis.list_recommendation_runs(strategy_version=strategy_version, status=status, limit=limit)


@router.get("/product-analysis/recommendations/{recommendation_id}/evidence")
def product_analysis_recommendation_evidence(
    recommendation_id: int,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return product_analysis.get_recommendation_evidence(recommendation_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/product-analysis/outcomes/summary")
def product_analysis_outcome_summary(
    launch_id: int | None = None,
    run_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    return product_analysis.recommendation_outcome_summary(launch_id=launch_id, run_id=run_id, limit=limit)


@router.post("/product-analysis/recommendations/{recommendation_id}/{action}")
def product_analysis_recommendation_action(
    recommendation_id: int,
    action: str,
    body: dict | None = None,
    staff=Depends(require_tab("vkpi", "write")),
):
    _require_legacy_recommendation_scope(staff)
    try:
        return product_analysis.action_recommendation(recommendation_id, action, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
