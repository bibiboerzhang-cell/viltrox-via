"""V-KPI KOL long-term memory routes (W3).

Endpoints expose a pure-aggregate "memory" view of a KOL — content style,
recommended product lines, risk, fulfillment, lifecycle timeline — and a
two-layer video full-scan plan. Everything here is physically isolated from
scoring: zero touches to viltrox_fit_score / rule_v0, and the snapshot never
contains v6_fit / score fields.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.manager_guard import require_manager_staff, require_manager_tab
from app.api.dependencies.perms import require_tab
from app.core.release_validation import release_validation_active
from app.domains.kol import lifecycle as kol_lifecycle
from app.domains.kol import memory as kol_memory
from app.domains.kol import video_fullscan
from app.domains.kol import video_prescreen

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-memory"])

_MEMORY_SOURCE_COUNT_KEYS = (
    "deep_results", "video_evidence", "content_posts", "assignments", "failed_jobs",
)
_LIFECYCLE_DETAIL_KEYS = {
    "discovered": ("source",),
    "assigned": ("stage",),
    "shipped": ("stage",),
    "published": ("platform", "status", "view_count"),
    "analyzed": ("analysis_kind", "provider"),
    "failed": ("job_type",),
    "favorited": (),
}


def _assert_memory_target_readable(kol_id: int, staff: dict[str, Any] | None) -> None:
    """Keep lifecycle and commercial-memory data inside My KOL read scope."""
    from app.db.connection import get_conn
    from app.domains.kol.my_kol_paid_action_access import (
        MyKolPaidActionError,
        assert_target_readable,
    )

    try:
        assert_target_readable(get_conn(), kol_pool_id=int(kol_id), staff=staff)
    except MyKolPaidActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


def _project_lifecycle(events: Any) -> list[dict[str, Any]]:
    """Return a bounded lifecycle DTO without staff notes or logistics truth."""
    projected: list[dict[str, Any]] = []
    for raw in events if isinstance(events, list) else []:
        if not isinstance(raw, dict):
            continue
        event_type = str(raw.get("event_type") or "").strip()
        if event_type not in _LIFECYCLE_DETAIL_KEYS:
            continue
        detail = raw.get("detail_json") if isinstance(raw.get("detail_json"), dict) else {}
        projected.append({
            "event_type": event_type,
            "ref_type": str(raw.get("ref_type") or "")[:40],
            "ref_id": "",
            "occurred_at": raw.get("occurred_at"),
            "detail_json": {
                key: detail.get(key)
                for key in _LIFECYCLE_DETAIL_KEYS[event_type]
                if detail.get(key) not in (None, "")
            },
        })
    return projected[:200]


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _project_memory_snapshot(value: Any) -> dict[str, Any]:
    """Allowlist drawer fields; drop cached prose, staff/project and row IDs."""
    source = value if isinstance(value, dict) else {}
    product_lines = source.get("recommended_product_lines")
    risk = source.get("risk") if isinstance(source.get("risk"), dict) else {}
    fulfillment = source.get("fulfillment") if isinstance(source.get("fulfillment"), dict) else {}
    return {
        "content_style": str(source.get("content_style") or "")[:2000],
        "recommended_product_lines": [
            str(item)[:200]
            for item in (product_lines if isinstance(product_lines, list) else [])[:50]
            if str(item or "").strip()
        ],
        "risk": {
            "risk_flags": [
                str(item)[:500]
                for item in (risk.get("risk_flags") if isinstance(risk.get("risk_flags"), list) else [])[:50]
                if str(item or "").strip()
            ],
            "final_verdict": str(risk.get("final_verdict") or "")[:2000] or None,
        },
        "fulfillment": {
            key: _count(fulfillment.get(key))
            for key in ("assigned_count", "shipped_count", "published_count", "failed_jobs_count")
        },
        "timeline": _project_lifecycle(source.get("timeline")),
        "note": "pure_aggregate,no_llm,no_v6_fit",
    }


def _project_source_counts(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {key: _count(source.get(key)) for key in _MEMORY_SOURCE_COUNT_KEYS}


@router.get("/kol-memory/{kol_id}")
def get_kol_memory(
    kol_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    _assert_memory_target_readable(int(kol_id), staff if isinstance(staff, dict) else None)
    try:
        latest = kol_memory.get_latest_kol_memory_snapshot(int(kol_id))
        if latest is not None:
            built = latest
        else:
            built = kol_memory.build_kol_memory_snapshot(int(kol_id))
            if built.get("status") == "missing":
                raise LookupError(f"kol_pool_id not found: {kol_id}")
        timeline = _project_lifecycle(kol_lifecycle.collect_lifecycle_events(int(kol_id)))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    snapshot = _project_memory_snapshot(built.get("snapshot"))
    snapshot["timeline"] = timeline
    return {
        "status": built.get("status", "ready"),
        "kol_pool_id": int(kol_id),
        "snapshot": snapshot,
        "source_counts": _project_source_counts(built.get("source_counts")),
        "timeline": timeline,
        "lifecycle": timeline,
        "computed_at": built.get("computed_at"),
    }


@router.post("/kol-memory/{kol_id}/rebuild")
def rebuild_kol_memory(
    kol_id: int,
    v2: bool = Query(default=False),
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    require_manager_staff(staff if isinstance(staff, dict) else {})
    if release_validation_active():
        raise HTTPException(status_code=503, detail="release_validation_fenced")
    # v2=true 才按需触发一次 LLM 长期记忆 summary;默认/false = v1 纯聚合,零 LLM(不全量 blast)。
    if v2:
        result = kol_memory.rebuild_kol_memory_snapshot_v2(int(kol_id), staff=staff)
    else:
        result = kol_memory.rebuild_kol_memory_snapshot(int(kol_id))
    return {
        "written": bool(result.get("written")),
        "snapshot_id": result.get("snapshot_id"),
        "kol_pool_id": int(kol_id),
        "snapshot": result.get("snapshot"),
        "source_counts": result.get("source_counts"),
        "llm_calls": bool(result.get("llm_calls", False)),
        "method": result.get("method"),
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
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    del kol_id, top_n
    require_manager_staff(staff if isinstance(staff, dict) else {})
    raise HTTPException(status_code=410, detail={"code": "kol_memory_fullscan_route_retired"})


@router.post("/kol-memory/{kol_id}/video-fullscan-materialize")
def materialize_kol_video_fullscan(
    kol_id: int,
    target_n: int = Query(default=120, ge=1, le=120),
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    """Trigger full video-metadata materialization for one KOL.

    Reuses the existing kol_profile_deep_crawl channel to back-fill
    vkpi_kol_video_evidence with the KOL's video metadata. Metadata only:
    never burns LLM, never touches viltrox_fit_score / rule_v0.
    """
    del kol_id, target_n
    require_manager_staff(staff if isinstance(staff, dict) else {})
    raise HTTPException(status_code=410, detail={"code": "kol_memory_fullscan_route_retired"})
