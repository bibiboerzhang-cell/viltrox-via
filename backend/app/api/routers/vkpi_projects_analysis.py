"""Bounded analysis-cache read and polling routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.api.routers.vkpi_projects_helpers import _resolve_video_cached_url
from app.api.routers.vkpi_projects_masking import _mask_payment_fields, _scope_403
from app.domains.access import policy, scope
from app.domains.analysis.cache_repo import (
    analysis_cache_read_projection,
    get_analysis_cache_entry,
    get_analysis_cache_entries_for_targets,
    get_latest_analysis_job,
    get_latest_analysis_jobs_for_targets,
)

router = APIRouter()

_AUTHORITATIVE_CACHE_STATES = {"ready", "quality_incomplete", "legacy_unverified"}
_ACTIVE_ANALYSIS_JOB_STATES = {"queued", "running", "retrying", "processing"}
_TERMINAL_ANALYSIS_JOB_STATES = {"blocked", "failed"}


def _cache_entry_state(
    entry: dict | None,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    derive_method: str | None = None,
) -> str | None:
    if not entry:
        return None
    return str(analysis_cache_read_projection(
        entry,
        target_type=target_type,
        target_id=target_id,
        derive_method=derive_method,
    ).get("state") or "unknown")


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _analysis_cache_response_state(
    entry: dict | None,
    analysis_job: dict | None,
    entry_projection: dict | None = None,
) -> str:
    """Resolve one polling state without hiding a refresh behind stale evidence.

    Ready and quality-incomplete cache rows are authoritative snapshots. Stale or
    unknown rows remain visible in ``entry``, but an active refresh owns the
    response state. A terminal job only supersedes that old cache state when its
    update timestamp is at least as new as the cache row.
    """

    entry_state = str((entry_projection or analysis_cache_read_projection(entry)).get("state") or "unknown") if entry else None
    if entry_state in _AUTHORITATIVE_CACHE_STATES:
        return str(entry_state)

    job_state = str(
        (analysis_job or {}).get("state")
        or (analysis_job or {}).get("status")
        or ""
    ).strip().lower()
    if entry is None:
        return job_state or "not_requested"
    if not analysis_job:
        return str(entry_state or "unknown")
    if job_state in _ACTIVE_ANALYSIS_JOB_STATES:
        return job_state
    if job_state in _TERMINAL_ANALYSIS_JOB_STATES:
        entry_updated_at = _timestamp(entry.get("updated_at"))
        job_updated_at = _timestamp(analysis_job.get("updated_at"))
        if (
            entry_updated_at is not None
            and job_updated_at is not None
            and job_updated_at >= entry_updated_at
        ):
            return job_state
    return str(entry_state or "unknown")


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
    entry_projection = analysis_cache_read_projection(
        entry,
        target_type=target_type,
        target_id=target_id,
        derive_method=derive_method or None,
    )
    entry_state = str(entry_projection.get("state") or "unknown") if entry else None
    analysis_job = None
    if entry_state not in _AUTHORITATIVE_CACHE_STATES:
        analysis_job = get_latest_analysis_job(
            target_type,
            target_id,
            derive_method=derive_method or None,
        )
    result = {
        "target_type": target_type,
        "target_id": target_id,
        "derive_method": derive_method or None,
        "state": _analysis_cache_response_state(entry, analysis_job, entry_projection),
        "entry": entry,
        "analysis_job": analysis_job,
        **({key: entry_projection[key] for key in (
            "terminal", "revalidation_required", "claim_status", "cache_reuse_status", "cache_id", "reasons",
        ) if key in entry_projection} if entry_state == "legacy_unverified" else {}),
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
    job_target_ids = [
        target_id
        for target_id in target_ids
        if _cache_entry_state(
            entries.get((target_id, derive_method)),
            target_type=target_type,
            target_id=target_id,
            derive_method=derive_method,
        )
        not in _AUTHORITATIVE_CACHE_STATES
    ]
    jobs = get_latest_analysis_jobs_for_targets(
        target_type,
        job_target_ids,
        derive_method=derive_method,
    )
    items = []
    for target_id in target_ids:
        entry = entries.get((target_id, derive_method))
        entry_projection = analysis_cache_read_projection(
            entry,
            target_type=target_type,
            target_id=target_id,
            derive_method=derive_method,
        )
        analysis_job = jobs.get(target_id)
        items.append(
            {
                "target_id": target_id,
                "state": _analysis_cache_response_state(entry, analysis_job, entry_projection),
                "entry": entry,
                "analysis_job": analysis_job,
                **({key: entry_projection[key] for key in (
                    "terminal", "revalidation_required", "claim_status", "cache_reuse_status", "cache_id", "reasons",
                ) if key in entry_projection} if entry_projection.get("state") == "legacy_unverified" else {}),
            }
        )
    return {
        "target_type": target_type,
        "derive_method": derive_method,
        "count": len(items),
        "items": items,
    }
