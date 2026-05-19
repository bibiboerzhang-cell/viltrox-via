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
