"""V-KPI Memory v0 routes."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import memory


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-memory"])


@router.get("/memory/summary")
def memory_summary(
    source_ref: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    return memory.summary(source_ref=source_ref)


@router.get("/memory/entities")
def memory_entities(
    entity_type: str = Query(default=""),
    query: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    return memory.list_entities(entity_type=entity_type, query=query, limit=limit)


@router.get("/memory/entities/{entity_uid}/facts")
def memory_entity_facts(
    entity_uid: str,
    limit: int = Query(default=200, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        return memory.entity_facts(entity_uid, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/memory/product-kol-candidates")
def memory_product_kol_candidates(
    product_query: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        return memory.product_kol_candidates(product_query=product_query, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/memory/entities/{entity_uid}/product-memory")
def memory_entity_product_memory(
    entity_uid: str,
    limit: int = Query(default=50, ge=1, le=300),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        return memory.kol_product_memory(entity_uid, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/memory/entities/{entity_uid}/fit-features")
def memory_entity_fit_features(
    entity_uid: str,
    product_query: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        return memory.fit_features(entity_uid, product_query=product_query)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/memory/build-from-legacy/{batch_uid}")
def build_from_legacy(
    batch_uid: str,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    del staff
    try:
        return memory.build_memory_from_legacy_batch(batch_uid)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/memory/feedback")
def memory_feedback(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    return memory.record_feedback(body, staff=staff)
