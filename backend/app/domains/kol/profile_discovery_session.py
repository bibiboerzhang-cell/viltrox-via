"""Profile crawl planning, execution, and search-session advancement."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import search_sessions, url_deep_crawl
from app.domains.kol.discovery_filters import _int, _text
from app.domains.kol.search_progress_contract import completion_contract


logger = get_logger(__name__)


def _profile_url_from_kol_pool_id(kol_pool_id: Any) -> str:
    parsed = _int(kol_pool_id)
    if parsed <= 0:
        return ""
    try:
        row = get_conn().execute(
            """
            SELECT profile_url, platform, handle
            FROM vkpi_kol_pool
            WHERE id=?
            """,
            (parsed,),
        ).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    data = dict(row)
    profile_url = _text(data.get("profile_url"))
    if profile_url:
        return profile_url
    platform = _text(data.get("platform")).lower()
    handle = _text(data.get("handle")).lstrip("@")
    if not platform or not handle:
        return ""
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}"
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "facebook":
        return f"https://www.facebook.com/{handle}"
    return ""


def _profile_url_from_item(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    for key in ("profile_url", "channel_url", "source_url"):
        value = _text(payload.get(key) or item.get(key))
        if value:
            return value
    platform = _text(payload.get("platform") or item.get("platform")).lower()
    handle = _text(payload.get("handle") or payload.get("channel_name") or item.get("handle"))
    if not platform or not handle:
        return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))
    handle = handle.lstrip("@")
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}"
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "facebook":
        return f"https://www.facebook.com/{handle}"
    if platform == "douyin":
        return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))
    return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))


def profile_crawl_plan_for_session_item(
    *,
    session_id: int,
    item_id: int,
    max_posts: int = 12,
    mode: str = "profile_only",
) -> dict[str, Any]:
    item = search_sessions.get_session_item(int(session_id), int(item_id))
    item_type = _text(item.get("item_type"))
    if item_type not in {"new_creator", "existing_kol", "recall_candidate", "online_qualified_candidate"}:
        raise ValueError("profile crawl requires a discovery, recall, or strict-online candidate item")
    if item_type == "online_qualified_candidate":
        session = search_sessions.get_session(int(session_id))
        approved_ids = {
            _int(value)
            for value in (session.get("approved_kol_ids") or [])
            if _int(value) > 0
        }
        if _int(item.get("kol_pool_id")) not in approved_ids:
            raise ValueError("strict-online profile crawl requires an approved pool candidate")
    profile_url = _profile_url_from_item(item)
    if not profile_url:
        raise ValueError("discovery item does not contain a usable profile URL")
    return {
        "status": "planned",
        "session_id": int(session_id),
        "item_id": int(item_id),
        "item_type": item_type,
        "profile_url": profile_url,
        "mode": mode if mode in {"profile_only", "auto", "profile_with_video", "account_deep"} else "profile_only",
        "max_posts": max(1, min(_int(max_posts, 12), 12)),
        "message": "set execute=true to crawl profile basics through the safe writer",
        "viltrox_fit_score_untouched": True,
    }


def _enqueue_audience_enrichment(
    kol_pool_id: int,
    *,
    search_session_id: int | None = None,
    search_session_item_id: int | None = None,
    parent_job_id: int | None = None,
) -> dict[str, Any]:
    """Queue comment collection; its worker refreshes audience stats after collection."""
    try:
        row = get_conn().execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_kol_video_evidence
            WHERE kol_pool_id=? AND is_active IS NOT FALSE
              AND COALESCE(evidence_type, 'video')='video'
            """,
            (int(kol_pool_id),),
        ).fetchone()
        evidence_count = int(dict(row).get("n") or 0) if row else 0
    except Exception:
        return {
            "status": "error",
            "async": True,
            "kol_pool_id": int(kol_pool_id),
            "reason": "audience_evidence_check_failed",
        }
    if evidence_count <= 0:
        return {
            "status": "waiting_for_evidence",
            "async": True,
            "kol_pool_id": int(kol_pool_id),
            "reason": "audience enrichment starts after video evidence is available",
        }
    try:
        from app.domains.comments.collector import enqueue_kol_pool_comments_job

        queued = enqueue_kol_pool_comments_job(
            int(kol_pool_id),
            queue_lane="batch",
            search_session_id=search_session_id,
            search_session_item_id=search_session_item_id,
            parent_job_id=parent_job_id,
        )
    except Exception:
        return {
            "status": "error",
            "async": True,
            "kol_pool_id": int(kol_pool_id),
            "reason": "audience_enqueue_failed",
        }
    queue_status = _text(queued.get("status")).lower()
    return {
        "status": "pending" if queue_status in {"queued", "already_queued"} else "partial",
        "async": True,
        "kol_pool_id": int(kol_pool_id),
        "queue_status": queue_status or "unknown",
        "job_id": queued.get("job_id"),
    }


def execute_profile_crawl_for_session_item(
    *,
    session_id: int,
    item_id: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = body or {}
    execute = bool(body.get("execute"))
    pipeline_running = bool(body.get("_pipeline_running"))
    mode = _text(body.get("mode") or "profile_only")
    if mode not in {"profile_only", "auto", "profile_with_video", "account_deep"}:
        mode = "profile_only"
    max_posts = max(1, min(_int(body.get("max_posts"), 12), 12))
    plan = profile_crawl_plan_for_session_item(
        session_id=int(session_id),
        item_id=int(item_id),
        max_posts=max_posts,
        mode=mode,
    )
    if not execute:
        return {
            **plan,
            "execute": False,
            "profile_result": url_deep_crawl.dry_run_url_deep_crawl(
                {
                    "url": plan["profile_url"],
                    "execute": False,
                    "mode": mode,
                    "max_posts": max_posts,
                    "representative_video_limit": body.get("representative_video_limit") or 1,
                }
            ),
        }

    profile_result = url_deep_crawl.dry_run_url_deep_crawl(
        {
            "url": plan["profile_url"],
            "execute": True,
            "mode": mode,
            "max_posts": max_posts,
            "representative_video_limit": body.get("representative_video_limit") or 1,
            "search_session_id": int(session_id),
            "search_session_item_id": int(item_id),
            "parent_job_id": _int(body.get("job_id")) or None,
        }
    )
    profile_flow = profile_result.get("profile_flow") if isinstance(profile_result.get("profile_flow"), dict) else {}
    materialized_kol_pool_id = _int(
        profile_flow.get("kol_pool_id")
        or profile_result.get("matched_kol_pool_id")
        or body.get("kol_pool_id")
    )
    if materialized_kol_pool_id > 0:
        try:
            from app.domains.kol.contact_acquisition_queue import enqueue_contact_acquisition

            queued = enqueue_contact_acquisition(
                materialized_kol_pool_id,
                trigger_source="deep_crawl",
            )
            profile_result["contact_enrichment"] = {
                "status": str(queued.get("status") or "pending_l0"),
                "async": True,
                "kol_pool_id": materialized_kol_pool_id,
                "reason": "provider_free_l0_queued",
                "provider_calls": False,
                "website_crawls": False,
                "messages_sent": False,
            }
        except Exception:
            profile_result["contact_enrichment"] = {
                "status": "error",
                "async": bool(body.get("_async_enrichment")),
                "kol_pool_id": materialized_kol_pool_id,
                "reason": "contact_enrichment_failed",
            }
        profile_result["audience_enrichment"] = _enqueue_audience_enrichment(
            materialized_kol_pool_id,
            search_session_id=int(session_id),
            search_session_item_id=int(item_id),
            parent_job_id=_int(body.get("job_id")) or None,
        )
    else:
        waiting = {
            "status": "waiting_for_profile",
            "async": bool(body.get("_async_enrichment")),
            "reason": "profile materialization did not return a KOL Pool id",
        }
        profile_result["contact_enrichment"] = dict(waiting)
        profile_result["audience_enrichment"] = dict(waiting)
    updated_item = search_sessions.update_item_profile_execution(
        int(session_id),
        int(item_id),
        profile_result=profile_result,
    )
    profile_status = _text(profile_flow.get("status") or profile_result.get("status") or "unknown").lower()
    return {
        **plan,
        "execute": True,
        # Optional audience work has its own explicit stage/status below.  It
        # must not downgrade a successfully materialized profile to partial.
        "status": profile_status,
        "profile_status": profile_status,
        "kol_pool_id": profile_flow.get("kol_pool_id") or profile_result.get("matched_kol_pool_id"),
        "contact_enrichment": profile_result.get("contact_enrichment"),
        "audience_enrichment": profile_result.get("audience_enrichment"),
        "profile_result": profile_result,
        "updated_item": updated_item,
        "viltrox_fit_score_changed_ids": profile_flow.get("viltrox_fit_score_changed_ids") or profile_result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": profile_flow.get("viltrox_fit_score_untouched") if "viltrox_fit_score_untouched" in profile_flow else profile_result.get("viltrox_fit_score_untouched"),
    }


def _utc_progress_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def _profile_progress_item(
    item: dict[str, Any],
    *,
    item_id: int,
    status: str,
    profile_status: str,
) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return {
        "item_id": int(item_id),
        "item_type": _text(item.get("item_type")) or None,
        "rank": item.get("rank"),
        "kol_pool_id": item.get("kol_pool_id"),
        "handle": _text(payload.get("handle") or payload.get("channel_name") or item.get("handle")) or None,
        "profile_url": _profile_url_from_item(item) or None,
        "status": status or "unknown",
        "profile_status": profile_status or "unknown",
    }


def _profile_stage_timing(
    *,
    stage_started_at: str,
    stage_started_monotonic: float,
    item_started_at: str,
    item_started_monotonic: float,
    item_finished_at: str,
    stage_finished_at: str | None = None,
) -> dict[str, Any]:
    return {
        "stage_started_at": stage_started_at,
        "stage_updated_at": item_finished_at,
        "stage_finished_at": stage_finished_at,
        "stage_elapsed_ms": max(0, int((time.monotonic() - stage_started_monotonic) * 1000)),
        "current_item_started_at": item_started_at,
        "current_item_finished_at": item_finished_at,
        "current_item_elapsed_ms": max(0, int((time.monotonic() - item_started_monotonic) * 1000)),
    }


def _persist_incremental_profile_progress(
    *,
    session_id: int,
    mode: str,
    limit: int,
    base_count: int,
    selected_count: int,
    overflow: int,
    counts: dict[str, int],
    completed_count: int,
    profile_ready: int,
    profile_failed: int,
    current_item: dict[str, Any],
    timing: dict[str, Any],
    pipeline_running: bool,
) -> None:
    """Persist one provider-free progress checkpoint without failing the crawl.

    A telemetry write must not make an already completed external crawl run
    again.  The final summary update remains authoritative if this best-effort
    checkpoint encounters a transient database error.
    """

    contract = completion_contract(
        base_count=base_count,
        total=selected_count,
        terminal_count=completed_count,
        ready_count=profile_ready,
        profile_failed=profile_failed,
        active_tasks=max(0, selected_count - completed_count),
        stage_progress=None,
        # The smart-search pipeline registers downstream work after/during this
        # loop.  Do not close the whole session at 15/15 profiles.
        requested_tasks_terminal=False if pipeline_running else None,
    )
    progress = {
        "base": base_count,
        "total": selected_count,
        "profile_ready": profile_ready,
        "profile_failed": profile_failed,
        "profile_completed": completed_count,
        "profile_succeeded": max(0, completed_count - profile_failed),
        "profile_remaining": max(0, selected_count - completed_count),
        "complete_ready": int(counts.get("ready") or 0),
        "complete_partial": int(counts.get("partial") or 0),
        "current_item": dict(current_item),
        "stage_timing": dict(timing),
        **contract,
    }
    try:
        search_sessions.update_session_result_summary(
            int(session_id),
            status="running",
            summary_patch={
                "phase": "profile",
                "progress": progress,
                **contract,
                "profile_batch_advance": {
                    "status": "running",
                    "mode": mode,
                    "limit": limit,
                    "selected": selected_count,
                    "overflow": overflow,
                    "completed": completed_count,
                    "succeeded": max(0, completed_count - profile_failed),
                    "failed": profile_failed,
                    "counts": dict(counts),
                    "current_item": dict(current_item),
                    "timing": dict(timing),
                    "viltrox_fit_score_untouched": True,
                },
            },
        )
    except Exception as exc:
        logger.warning(
            "profile batch progress checkpoint failed | session_id=%s item_id=%s error_type=%s",
            session_id,
            current_item.get("item_id"),
            type(exc).__name__,
        )


def advance_search_session_items(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
    smart_local_contract: bool = False,
) -> dict[str, Any]:
    """Plan or execute ordered profile crawl for discovery items in a session.

    This is an orchestration helper for the unified KOL input. It advances
    session items one by one through the already-safe profile flow and never
    writes V6 Fit fields directly.
    """

    body = body or {}
    execute = bool(body.get("execute"))
    pipeline_running = bool(body.get("_pipeline_running"))
    limit_cap = 30 if smart_local_contract else 15
    limit = max(1, min(_int(body.get("limit"), 5), limit_cap))
    max_posts = max(1, min(_int(body.get("max_posts"), 12), 12))
    mode = _text(body.get("mode") or "profile_only")
    if mode not in {"profile_only", "auto", "profile_with_video", "account_deep"}:
        mode = "profile_only"
    include_completed = bool(body.get("include_completed"))
    item_ids_raw = body.get("item_ids")
    item_ids = {
        _int(value)
        for value in (item_ids_raw if isinstance(item_ids_raw, list) else [])
        if _int(value) > 0
    }
    allowed_types_raw = body.get("item_types")
    allowed_types = {
        _text(value)
        for value in (allowed_types_raw if isinstance(allowed_types_raw, list) else [])
        if _text(value)
    } or {"new_creator", "existing_kol", "recall_candidate", "online_qualified_candidate"}

    session = search_sessions.get_session(int(session_id))
    approved_pool_ids = {
        _int(value)
        for value in (session.get("approved_kol_ids") or [])
        if _int(value) > 0
    }
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    terminal_statuses = {"ready", "queued", "running", "already_queued", "already_analyzed"}
    for item in session.get("items") or []:
        item_id = _int(item.get("id"))
        item_type = _text(item.get("item_type"))
        item_status = _text(item.get("status"))
        if item_ids and item_id not in item_ids:
            continue
        if item_type not in allowed_types:
            continue
        if item_type not in {"new_creator", "existing_kol", "recall_candidate", "online_qualified_candidate"}:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "unsupported_item_type", "item_type": item_type})
            continue
        if item_type == "online_qualified_candidate" and _int(item.get("kol_pool_id")) not in approved_pool_ids:
            skipped.append({
                "item_id": item_id,
                "status": "skipped",
                "reason": "approval_required",
                "item_type": item_type,
            })
            continue
        if smart_local_contract and item_type != "recall_candidate":
            skipped.append({
                "item_id": item_id,
                "status": "skipped",
                "reason": "reserved_for_online_lane",
                "item_type": item_type,
            })
            continue
        if not include_completed and item_status in terminal_statuses:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "already_terminal", "item_status": item_status})
            continue
        profile_url = _profile_url_from_item(item)
        if not profile_url:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "missing_profile_url", "item_status": item_status})
            continue
        candidates.append(item)

    selected = candidates[:limit]
    overflow = max(0, len(candidates) - len(selected))
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {"planned": 0, "executed": 0, "ready": 0, "partial": 0, "failed": 0, "skipped": len(skipped), "errors": 0}
    profile_ready = 0
    profile_failed = 0
    changed_ids: list[int] = []
    stage_started_at = _utc_progress_time()
    stage_started_monotonic = time.monotonic()
    last_current_item: dict[str, Any] = {}
    last_timing: dict[str, Any] = {
        "stage_started_at": stage_started_at,
        "stage_updated_at": stage_started_at,
        "stage_finished_at": None,
        "stage_elapsed_ms": 0,
        "current_item_started_at": None,
        "current_item_finished_at": None,
        "current_item_elapsed_ms": 0,
    }

    for item in selected:
        item_id = _int(item.get("id"))
        item_started_at = _utc_progress_time()
        item_started_monotonic = time.monotonic()
        status = "unknown"
        profile_status = "unknown"
        try:
            if not execute:
                plan = profile_crawl_plan_for_session_item(
                    session_id=int(session_id),
                    item_id=item_id,
                    max_posts=max_posts,
                    mode=mode,
                )
                counts["planned"] += 1
                items.append({"item_id": item_id, "status": "planned", "plan": plan})
                continue

            result = execute_profile_crawl_for_session_item(
                session_id=int(session_id),
                item_id=item_id,
                body={**body, "execute": True, "max_posts": max_posts, "mode": mode},
            )
            counts["executed"] += 1
            status = _text(result.get("status")).lower() or "unknown"
            profile_status = _text(result.get("profile_status") or status).lower()
            if profile_status in {"ready", "already_analyzed"}:
                profile_ready += 1
            elif "failed" in profile_status or profile_status == "error":
                profile_failed += 1
            if status == "ready":
                counts["ready"] += 1
            elif status in {"failed", "crawl_failed", "profile_crawl_failed"} or "failed" in status:
                counts["failed"] += 1
            else:
                counts["partial"] += 1
            for changed_id in result.get("viltrox_fit_score_changed_ids") or []:
                parsed = _int(changed_id)
                if parsed > 0 and parsed not in changed_ids:
                    changed_ids.append(parsed)
            items.append({"item_id": item_id, "status": status, "result": result})
        except Exception:
            counts["errors"] += 1
            profile_failed += 1
            status = "error"
            profile_status = "failed"
            items.append({"item_id": item_id, "status": "error", "reason": "profile_crawl_failed"})
        if execute:
            item_finished_at = _utc_progress_time()
            last_current_item = _profile_progress_item(
                item,
                item_id=item_id,
                status=status,
                profile_status=profile_status,
            )
            last_timing = _profile_stage_timing(
                stage_started_at=stage_started_at,
                stage_started_monotonic=stage_started_monotonic,
                item_started_at=item_started_at,
                item_started_monotonic=item_started_monotonic,
                item_finished_at=item_finished_at,
            )
            _persist_incremental_profile_progress(
                session_id=int(session_id),
                mode=mode,
                limit=limit,
                base_count=len(candidates),
                selected_count=len(selected),
                overflow=overflow,
                counts=counts,
                completed_count=len(items),
                profile_ready=profile_ready,
                profile_failed=profile_failed,
                current_item=last_current_item,
                timing=last_timing,
                pipeline_running=pipeline_running,
            )

    skipped.extend(
        {
            "item_id": _int(item.get("id")),
            "status": "skipped",
            "reason": "over_limit",
            "item_status": _text(item.get("status")),
        }
        for item in candidates[limit:]
    )
    counts["skipped"] = len(skipped)

    batch_status = "planned"
    if execute:
        if not selected:
            batch_status = "partial"
        elif counts["failed"] or counts["errors"]:
            batch_status = "partial" if counts["ready"] or counts["partial"] else "failed"
        elif counts["partial"]:
            batch_status = "partial"
        elif counts["ready"] != len(selected):
            batch_status = "partial"
        else:
            batch_status = "ready"
        finished_at = _utc_progress_time()
        last_timing = {
            **last_timing,
            "stage_updated_at": finished_at,
            "stage_finished_at": finished_at,
            "stage_elapsed_ms": max(0, int((time.monotonic() - stage_started_monotonic) * 1000)),
        }
        contract = completion_contract(
            base_count=len(candidates),
            total=len(selected),
            terminal_count=len(items),
            ready_count=profile_ready,
            profile_failed=profile_failed,
            active_tasks=0,
            stage_progress=None,
            requested_tasks_terminal=False if pipeline_running else None,
        )
        search_sessions.update_session_result_summary(
            int(session_id),
            status="running" if pipeline_running else batch_status,
            summary_patch={
                "phase": "profile" if pipeline_running else ("complete" if batch_status == "ready" else "partial"),
                "progress": {
                    "base": len(candidates),
                    "total": len(selected),
                    "profile_ready": profile_ready,
                    "profile_failed": profile_failed,
                    "profile_completed": len(items),
                    "profile_succeeded": max(0, len(items) - profile_failed),
                    "profile_remaining": max(0, len(selected) - len(items)),
                    "complete_ready": int(counts.get("ready") or 0),
                    "complete_partial": int(counts.get("partial") or 0),
                    "current_item": dict(last_current_item),
                    "stage_timing": dict(last_timing),
                    **contract,
                },
                **contract,
                "profile_batch_advance": {
                    "status": batch_status,
                    "mode": mode,
                    "limit": limit,
                    "selected": len(selected),
                    "overflow": overflow,
                    "completed": len(items),
                    "succeeded": max(0, len(items) - profile_failed),
                    "failed": profile_failed,
                    "counts": dict(counts),
                    "current_item": dict(last_current_item),
                    "timing": dict(last_timing),
                    "viltrox_fit_score_changed_ids": changed_ids,
                    "viltrox_fit_score_untouched": not changed_ids,
                }
            },
        )

    return {
        "status": batch_status,
        "execute": execute,
        "session_id": int(session_id),
        "mode": mode,
        "limit": limit,
        "selected": len(selected),
        "eligible": len(candidates),
        "overflow": overflow,
        "counts": counts,
        "items": items,
        "skipped": skipped[: max(0, 50 - len(items))],
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
        "provider_calls_performed": execute and bool(selected),
        "write_db": execute and bool(selected),
        "writes": ["vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs", "vkpi_kol_search_sessions", "vkpi_kol_search_session_items"] if execute and selected else [],
    }


def _profile_advance_pipeline_status(
    recall_result: dict[str, Any],
    new_discovery: dict[str, Any] | None,
    advance_result: dict[str, Any],
) -> str:
    recall_items = recall_result.get("items") if isinstance(recall_result.get("items"), list) else []
    recall_buckets = recall_result.get("buckets") if isinstance(recall_result.get("buckets"), dict) else {}
    recall_count = len(recall_items) or sum(len(items) for items in recall_buckets.values() if isinstance(items, list))
    discovery_items = (new_discovery or {}).get("items") if isinstance((new_discovery or {}).get("items"), list) else []
    candidate_count = recall_count + len(discovery_items)
    recall_status = _text(recall_result.get("status")).lower()
    discovery_status = _text((new_discovery or {}).get("status")).lower()
    advance_status = _text(advance_result.get("status")).lower()
    if candidate_count <= 0:
        return "failed" if recall_status == "failed" or discovery_status == "failed" else "partial"
    if recall_status in {"failed", "partial"} or discovery_status in {"failed", "partial"}:
        return "partial"
    if advance_status != "ready":
        return "failed" if advance_status == "failed" else "partial"
    return "ready"
