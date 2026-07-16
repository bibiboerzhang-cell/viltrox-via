"""Bounded analysis-cache read and polling routes."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.api.routers.vkpi_projects_helpers import _resolve_video_cached_url
from app.api.routers.vkpi_projects_masking import _mask_payment_fields, _scope_403
from app.domains.access import policy, scope
from app.domains.analysis.cache_repo import (
    get_analysis_cache_entry,
    get_analysis_cache_entries_for_targets,
    get_latest_analysis_job,
    get_latest_analysis_jobs_for_targets,
)

router = APIRouter()


@router.get("/analysis-cache")
def analysis_cache(
    target_type: str = Query(..., min_length=1),
    target_id: str = Query(..., min_length=1),
    derive_method: str = "",
    allow_local_evaluation_fallback: bool = True,
    staff=Depends(require_tab("vkpi", "read")),
):
    target_type = target_type.strip()
    target_id = target_id.strip()
    derive_method = derive_method.strip()
    if not target_type or not target_id:
        raise HTTPException(status_code=400, detail="target_type and target_id required")
    scoped_project_id = scope.resolve_analysis_target_project(target_type, target_id)
    if scoped_project_id is not None:
        try:
            policy.require_project_read(scoped_project_id, staff)
        except policy.ScopeDenied as exc:
            raise _scope_403(exc) from exc
    entry = get_analysis_cache_entry(
        target_type,
        target_id,
        derive_method=derive_method or None,
        allow_local_evaluation_fallback=(
            bool(allow_local_evaluation_fallback)
            and target_type.lower() == "video"
            and derive_method == "video_analysis_final_v1"
        ),
    )
    analysis_job = None if entry else get_latest_analysis_job(
        target_type,
        target_id,
        derive_method=derive_method or None,
    )
    result = {
        "target_type": target_type,
        "target_id": target_id,
        "derive_method": derive_method or None,
        "state": (
            "ready"
            if entry
            else str(analysis_job.get("state") or analysis_job.get("status") or "unknown")
            if analysis_job
            else "not_requested"
        ),
        "entry": entry,
        "analysis_job": analysis_job,
    }
    if target_type.lower() == "video":
        cached_video_url = _resolve_video_cached_url(target_id)
        if cached_video_url:
            result["cached_video_url"] = cached_video_url
    if target_type.lower() == "contract":
        result = _mask_payment_fields(result, staff, project_id=scoped_project_id)
    return result


@router.post("/analysis-cache/batch")
def analysis_cache_batch(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Bounded production-cache/job status matrix for browser polling."""
    target_type = str(body.get("target_type") or "").strip().lower()
    derive_method = str(body.get("derive_method") or "").strip()
    raw_ids = body.get("target_ids")
    if not target_type or not derive_method or not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="target_type, target_ids and derive_method required")
    target_ids = list(dict.fromkeys(str(value).strip() for value in raw_ids if str(value).strip()))
    if not target_ids or len(target_ids) > 200:
        raise HTTPException(status_code=400, detail="target_ids must contain 1..200 unique values")
    try:
        policy.require_projects_read(
            scope.resolve_analysis_target_projects(target_type, target_ids),
            staff,
        )
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc

    entries = get_analysis_cache_entries_for_targets(
        target_type,
        target_ids,
        derive_methods=(derive_method,),
    )
    missing_ids = [target_id for target_id in target_ids if (target_id, derive_method) not in entries]
    jobs = get_latest_analysis_jobs_for_targets(
        target_type,
        missing_ids,
        derive_method=derive_method,
    )
    items = []
    for target_id in target_ids:
        entry = entries.get((target_id, derive_method))
        analysis_job = None if entry else jobs.get(target_id)
        items.append(
            {
                "target_id": target_id,
                "state": (
                    "ready"
                    if entry
                    else str(analysis_job.get("state") or analysis_job.get("status") or "unknown")
                    if analysis_job
                    else "not_requested"
                ),
                "entry": entry,
                "analysis_job": analysis_job,
            }
        )
    return {
        "target_type": target_type,
        "derive_method": derive_method,
        "count": len(items),
        "items": items,
    }
