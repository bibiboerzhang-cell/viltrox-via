"""V-KPI KOL long-term memory routes (W3).

Endpoints expose a pure-aggregate "memory" view of a KOL — content style,
recommended product lines, risk, fulfillment, lifecycle timeline — and a
two-layer video full-scan plan. Everything here is physically isolated from
scoring: zero touches to viltrox_fit_score / rule_v0, and the snapshot never
contains v6_fit / score fields.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.kol import lifecycle as kol_lifecycle
from app.domains.kol import memory as kol_memory
from app.domains.kol import video_fullscan
from app.domains.kol import video_prescreen

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-memory"])


@router.get("/kol-memory/{kol_id}")
def get_kol_memory(
    kol_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        latest = kol_memory.get_latest_kol_memory_snapshot(int(kol_id))
        if latest is not None:
            built = latest
        else:
            built = kol_memory.build_kol_memory_snapshot(int(kol_id))
            if built.get("status") == "missing":
                raise LookupError(f"kol_pool_id not found: {kol_id}")
        timeline = kol_lifecycle.collect_lifecycle_events(int(kol_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "status": built.get("status", "ready"),
        "kol_pool_id": int(kol_id),
        "snapshot": built.get("snapshot"),
        "source_counts": built.get("source_counts"),
        "timeline": timeline,
        "lifecycle": timeline,
        "computed_at": built.get("computed_at"),
    }


@router.post("/kol-memory/{kol_id}/rebuild")
def rebuild_kol_memory(
    kol_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    del staff
    result = kol_memory.rebuild_kol_memory_snapshot(int(kol_id))
    return {
        "written": bool(result.get("written")),
        "snapshot_id": result.get("snapshot_id"),
        "kol_pool_id": int(kol_id),
        "snapshot": result.get("snapshot"),
        "source_counts": result.get("source_counts"),
        "llm_calls": False,
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids", []),
        "computed_at": result.get("computed_at"),
    }


@router.get("/kol-memory/{kol_id}/video-fullscan-plan")
def get_kol_video_fullscan_plan(
    kol_id: int,
    top_n: int = Query(default=5, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        return video_fullscan.plan_kol_video_fullscan(int(kol_id), top_n=int(top_n))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/kol-memory/{kol_id}/video-prescreen")
def get_kol_video_prescreen(
    kol_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        return video_prescreen.prescreen_kol_videos(int(kol_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/kol-memory/{kol_id}/video-fullscan-enqueue")
def enqueue_kol_video_fullscan(
    kol_id: int,
    top_n: int = Query(default=5, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    try:
        return video_fullscan.enqueue_kol_video_fullscan(
            int(kol_id), top_n=int(top_n), staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/kol-memory/{kol_id}/video-fullscan-materialize")
def materialize_kol_video_fullscan(
    kol_id: int,
    target_n: int = Query(default=120, ge=1, le=120),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Trigger full video-metadata materialization for one KOL.

    Reuses the existing kol_profile_deep_crawl channel to back-fill
    vkpi_kol_video_evidence with the KOL's video metadata. Metadata only:
    never burns LLM, never touches viltrox_fit_score / rule_v0.
    """
    from app.domains.kol import video_full_materialize

    try:
        return video_full_materialize.enqueue_full_materialize(
            int(kol_id), target_n=int(target_n), staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
