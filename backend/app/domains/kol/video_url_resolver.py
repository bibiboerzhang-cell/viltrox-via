"""Durable resolver for a previously unseen public video URL.

The HTTP path may classify a URL and read owned evidence, but it must never
call a provider.  Unknown videos are therefore queued here and resolved only
inside the fenced Apify worker context.  The resolver owns *base evidence*
only; ``final_v1`` remains the existing readiness/budget-gated downstream job.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from app.db.connection import get_conn
from app.domains.tasks.apify_idempotency import (
    active_job_idempotency_key,
    enqueue_active_apify_job,
)


VIDEO_URL_RESOLVE_JOB_TYPE = "video_url_resolve"
_STEP_DEFS = (
    ("resolve_video", "解析视频"),
    ("identify_creator", "识别作者"),
    ("cache_media", "缓存媒体"),
    ("ai_analysis", "AI分析"),
)
_BASE_COMPLETE_STATUSES = frozenset(
    {
        "queued",
        "already_queued",
        "already_analyzed",
        "already_evaluated",
        "ai_disabled",
        "not_requested",
        "skipped_non_video",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def initial_video_url_resolution_progress() -> dict[str, Any]:
    """Return the public, deterministic four-stage resolver state."""

    return {
        "version": "video_url_resolve_v1",
        "status": "queued",
        "base_status": "pending",
        "current_step": "resolve_video",
        "steps": [
            {
                "key": key,
                "label": label,
                "status": "queued" if index == 0 else "pending",
                "reason": "",
            }
            for index, (key, label) in enumerate(_STEP_DEFS)
        ],
        "updated_at": _now(),
    }


def _progress(
    current: dict[str, Any] | None,
    step: str,
    status: str,
    *,
    overall: str = "running",
    base_status: str = "pending",
    reason: str = "",
) -> dict[str, Any]:
    value = deepcopy(current or initial_video_url_resolution_progress())
    steps = value.get("steps") if isinstance(value.get("steps"), list) else []
    for item in steps:
        if not isinstance(item, dict) or _text(item.get("key")) != step:
            continue
        item["status"] = status
        item["reason"] = _text(reason)[:240]
    value.update(
        {
            "status": overall,
            "base_status": base_status,
            "current_step": step,
            "updated_at": _now(),
        }
    )
    return value


def failed_video_url_resolution_progress(
    current: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    step = _text((current or {}).get("current_step")) or "resolve_video"
    return _progress(
        current,
        step,
        "failed",
        overall="failed",
        base_status="failed",
        reason=reason,
    )


def enqueue_video_url_resolve_job(
    url: str,
    *,
    staff: dict[str, Any] | None = None,
    search_session_id: int | None = None,
    source: str = "kol_video_url_resolve",
    queue_lane: str = "interactive",
    max_posts: int = 3,
    local_evaluation: bool = False,
) -> dict[str, Any]:
    """Queue one native video identity; never enqueue a profile-crawl job."""

    from app.domains.kol.url_deep_crawl import SUPPORTED_PLATFORMS, classify_url

    classified = classify_url(_text(url))
    if classified.url_type != "video" or classified.platform not in SUPPORTED_PLATFORMS:
        raise ValueError("video_url_resolve requires a supported video URL")
    lane = _text(queue_lane).lower() or "interactive"
    if lane not in {"interactive", "batch"}:
        raise ValueError("queue_lane must be interactive or batch")
    native_identity = f"{classified.platform}:{classified.video_id}"
    progress = initial_video_url_resolution_progress()
    payload = {
        "queue_lane": lane,
        "url": classified.normalized_url,
        "source_url": classified.normalized_url,
        "platform": classified.platform,
        "video_id": classified.video_id,
        "query_text": classified.normalized_url[:96],
        "target_type": "video_url",
        "target_id": native_identity,
        "derive_method": "video_url_resolve_v1",
        "max_posts": max(1, min(12, int(max_posts or 3))),
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
        "search_session_id": _int_or_none(search_session_id),
        "source": _text(source)[:80] or "kol_video_url_resolve",
        "local_evaluation": bool(local_evaluation),
        "video_url_resolution": progress,
    }
    conn = get_conn()
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type=VIDEO_URL_RESOLVE_JOB_TYPE,
        payload=payload,
        idempotency_key=active_job_idempotency_key(
            VIDEO_URL_RESOLVE_JOB_TYPE,
            native_identity,
        ),
    )
    conn.commit()
    return {
        "status": "queued" if inserted else "already_queued",
        "job_id": int(job["id"]),
        "job_type": VIDEO_URL_RESOLVE_JOB_TYPE,
        "write_db": True,
        "provider_calls_performed": False,
        "resolution_progress": progress,
        "ai_analysis": {
            "state": "waiting_for_evidence",
            "reason": "video_url_resolution_queued",
            "provider_calls_allowed": False,
        },
    }


def _emit(
    callback: Callable[[dict[str, Any]], None] | None,
    value: dict[str, Any],
) -> dict[str, Any]:
    if callback:
        callback(value)
    return value


def _ai_terminal_state(flow: dict[str, Any]) -> tuple[str, str, str]:
    ai = flow.get("ai_analysis") if isinstance(flow.get("ai_analysis"), dict) else {}
    state = _text(ai.get("state") or flow.get("status")).lower()
    reason = _text(ai.get("reason") or ai.get("gate_reason"))
    if state in {"queued", "running", "already_queued"}:
        return "queued", "running", reason
    if state in {"ready", "already_analyzed", "already_evaluated"}:
        return "ready", "ready", reason
    if state in {"not_requested", "ai_disabled", "disabled", "blocked"}:
        return "skipped", "ready", reason or "ai_not_enabled"
    return "skipped", "ready", reason or "analysis_not_requested"


def run_video_url_resolve_for_job(
    payload: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Resolve metadata/creator/evidence, then use the gated final_v1 enqueue."""

    del staff  # final_v1 uses durable actor/session lineage, not an HTTP credential
    from app.domains.kol.url_deep_crawl import (
        SUPPORTED_PLATFORMS,
        _execute_existing_creator_video_flow,
        _execute_new_creator_video_flow,
        _match_pool,
        _video_creator_resolved,
        _video_flow_plan,
        classify_url,
    )

    current = payload.get("video_url_resolution")
    current = current if isinstance(current, dict) else initial_video_url_resolution_progress()
    current = _emit(progress_callback, _progress(current, "resolve_video", "running"))
    classified = classify_url(_text(payload.get("url") or payload.get("source_url")))
    if classified.url_type != "video" or classified.platform not in SUPPORTED_PLATFORMS:
        raise ValueError("unsupported_video_url")

    video_flow, matches = _video_flow_plan(classified, _match_pool(classified))
    scrape_status = _text((video_flow.get("video_metadata") or {}).get("scrape_status")).lower()
    if scrape_status == "pending" or _text(video_flow.get("status")) == "provider_refresh_pending":
        raise RuntimeError("media_resolve_failed:provider_refresh_deferred_in_worker")
    if _text(video_flow.get("status")) in {"metadata_failed"}:
        raise RuntimeError("media_resolve_failed:video_metadata_unavailable")
    current = _emit(progress_callback, _progress(current, "resolve_video", "ready"))

    current = _emit(progress_callback, _progress(current, "identify_creator", "running"))
    if len(matches) > 1:
        current = _progress(
            current,
            "identify_creator",
            "failed",
            overall="partial",
            reason="multiple_creator_matches",
        )
        _emit(progress_callback, current)
        return {
            "status": "needs_human_choice",
            "resolution_progress": current,
            "candidate_count": len(matches),
            "creator_identity": video_flow.get("creator_identity"),
            "video_metadata": video_flow.get("video_metadata"),
            "provider_calls_performed": bool(video_flow.get("provider_calls_performed")),
        }
    if not _video_creator_resolved(video_flow):
        current = _progress(
            current,
            "identify_creator",
            "failed",
            overall="partial",
            reason="creator_unresolved",
        )
        _emit(progress_callback, current)
        return {
            "status": "creator_unresolved",
            "resolution_progress": current,
            "creator_identity": video_flow.get("creator_identity"),
            "video_metadata": video_flow.get("video_metadata"),
            "provider_calls_performed": bool(video_flow.get("provider_calls_performed")),
        }
    current = _emit(progress_callback, _progress(current, "identify_creator", "ready"))

    # The existing execute flows own evidence writes, optional R2 warming and
    # the single reviewed final_v1 preflight/enqueue.  Do not duplicate those
    # gates here.
    body = {
        "url": classified.normalized_url,
        "execute": True,
        "mode": "video_deep",
        "max_posts": payload.get("max_posts") or 3,
        "search_session_id": _int_or_none(payload.get("search_session_id")),
        "search_session_item_id": _int_or_none(payload.get("search_session_item_id")),
        "parent_job_id": _int_or_none(payload.get("job_id") or payload.get("parent_job_id")),
        "local_evaluation": payload.get("local_evaluation") is True,
        # One pasted video should resolve that video only.  New-creator profile
        # basics are required, but representative/history/dossier fan-out is a
        # separate account workflow and must not multiply jobs or spend here.
        "skip_profile_video_followups": True,
    }
    current = _emit(progress_callback, _progress(current, "cache_media", "running"))
    flow = (
        _execute_existing_creator_video_flow(classified, matches, video_flow, body)
        if len(matches) == 1
        else _execute_new_creator_video_flow(classified, video_flow, body)
    )
    flow_status = _text(flow.get("status")).lower()
    if flow_status not in _BASE_COMPLETE_STATUSES:
        if flow_status in {"profile_crawl_failed", "crawl_failed", "provider_error", "timeout"}:
            raise RuntimeError(f"media_resolve_failed:{flow_status}")
        raise RuntimeError(f"video_url_resolve_failed:{flow_status or 'unknown'}")

    cache_status = "ready" if _text(flow.get("cached_video_url")) else "skipped"
    cache_reason = "" if cache_status == "ready" else (
        "youtube_embed_no_r2_required" if classified.platform == "youtube" else "media_cache_not_ready"
    )
    current = _emit(
        progress_callback,
        _progress(current, "cache_media", cache_status, reason=cache_reason),
    )
    ai_step_status, overall, ai_reason = _ai_terminal_state(flow)
    current = _emit(
        progress_callback,
        _progress(
            current,
            "ai_analysis",
            ai_step_status,
            overall=overall,
            base_status="ready",
            reason=ai_reason,
        ),
    )
    return {
        "status": flow_status,
        "operation": _text(flow.get("operation")) or "video_url_resolve",
        "kol_pool_id": _int_or_none(flow.get("kol_pool_id")),
        "evidence_id": _int_or_none(flow.get("evidence_id")),
        "creator_identity": flow.get("creator_identity"),
        "video_metadata": flow.get("video_metadata"),
        "cached_video_url": flow.get("cached_video_url"),
        "ai_analysis": flow.get("ai_analysis"),
        "video_flow": flow,
        "resolution_progress": current,
        "provider_calls_performed": bool(
            video_flow.get("provider_calls_performed") or flow.get("provider_calls_performed")
        ),
        "llm_calls_performed": False,
        "viltrox_fit_score_untouched": bool(flow.get("viltrox_fit_score_untouched", True)),
    }


def video_url_session_sync_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the safe item patch replayed by the worker session reducer."""

    progress = payload.get("video_url_resolution")
    progress = progress if isinstance(progress, dict) else {}
    result = payload.get("video_url_resolve_result")
    result = result if isinstance(result, dict) else {}
    flow = result.get("video_flow") if isinstance(result.get("video_flow"), dict) else {}
    patch: dict[str, Any] = {}
    if progress:
        patch["video_url_resolution"] = progress
    for key in ("creator_identity", "video_metadata", "ai_analysis"):
        value = result.get(key)
        if isinstance(value, dict):
            patch[key] = value
    if flow:
        from app.domains.kol.search_sessions_serde import _compact_flow

        patch["video_flow"] = _compact_flow(flow)
    cached = _text(result.get("cached_video_url"))
    if cached:
        patch["cached_video_url"] = cached
    step = _text(progress.get("current_step"))
    stage = {
        "resolve_video": "identified",
        "identify_creator": "profile",
        "cache_media": "evidence",
        "ai_analysis": "analysis",
    }.get(step, "identified")
    return {
        "payload_patch": patch,
        "stage": stage,
        "base_status": _text(progress.get("base_status")).lower(),
        "resolution_status": _text(progress.get("status")).lower(),
        "kol_pool_id": _int_or_none(result.get("kol_pool_id") or flow.get("kol_pool_id")),
        "evidence_id": _int_or_none(result.get("evidence_id") or flow.get("evidence_id")),
    }


def reconcile_video_url_ai_progress(
    item_payload: dict[str, Any],
    downstream: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project the downstream final_v1 state into the fourth UI stage."""

    flow = item_payload.get("video_flow") if isinstance(item_payload.get("video_flow"), dict) else {}
    current = item_payload.get("video_url_resolution")
    if not isinstance(current, dict):
        current = flow.get("resolution_progress")
    if not isinstance(current, dict) or not current:
        return {}
    video = (downstream or {}).get("video")
    video = video if isinstance(video, dict) else {}
    state = _text(video.get("state")).lower()
    if state not in {"active", "ready", "failed"}:
        return current
    step_status = {"active": "running", "ready": "ready", "failed": "failed"}[state]
    overall = {"active": "running", "ready": "ready", "failed": "partial"}[state]
    reason = _text(video.get("last_error")) if state == "failed" else ""
    return _progress(
        current,
        "ai_analysis",
        step_status,
        overall=overall,
        base_status="ready",
        reason=reason,
    )
