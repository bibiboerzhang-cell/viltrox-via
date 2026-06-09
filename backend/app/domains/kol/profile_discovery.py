"""New-creator discovery helpers for KOL Pool smart search.

This module reuses the existing platform-search provider from old Discover.
It does not create KOL Pool rows and never touches V6 Fit scoring fields.
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol import history_match
from app.domains.kol import profile_recall
from app.domains.kol import search_sessions
from app.domains.kol import url_deep_crawl
from app.services.intelligence.account_scan_service import search_platform_content


SUPPORTED_DISCOVERY_PLATFORMS = {"youtube", "instagram", "tiktok", "douyin"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _staff_user_id(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    for key in ("user_id", "id", "staff_id"):
        parsed = _int(staff.get(key))
        if parsed > 0:
            return parsed
    return None


def _platforms(value: Any, fallback: str = "") -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    out: list[str] = []
    for raw in raw_values:
        text = _text(raw).lower()
        if text in {"all", "*"}:
            continue
        if text in SUPPORTED_DISCOVERY_PLATFORMS and text not in out:
            out.append(text)
    fallback_text = _text(fallback).lower()
    if not out and fallback_text in SUPPORTED_DISCOVERY_PLATFORMS:
        out.append(fallback_text)
    return out or ["youtube"]


def _candidate_key(item: dict[str, Any], platform: str) -> str:
    for key in ("handle", "channel_url", "source_url", "channel_name"):
        value = _text(item.get(key)).lower()
        if value:
            return f"{platform}:{value}"
    return f"{platform}:unknown:{len(str(item))}"


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
    if platform == "douyin":
        return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))
    return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))


def discovery_plan(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    safe_limit = max(1, min(_int(limit, 15), 50))
    resolved_platforms = _platforms(platforms, fallback=platform_hint)
    return {
        "status": "planned",
        "query": _text(query_text),
        "platforms": resolved_platforms,
        "limit": safe_limit,
        "provider_calls": False,
        "message": "new discovery is planned only; set execute_new_discovery=true to call platform providers",
    }


def profile_crawl_plan_for_session_item(
    *,
    session_id: int,
    item_id: int,
    max_posts: int = 12,
    mode: str = "profile_only",
) -> dict[str, Any]:
    item = search_sessions.get_session_item(int(session_id), int(item_id))
    item_type = _text(item.get("item_type"))
    if item_type not in {"new_creator", "existing_kol", "recall_candidate"}:
        raise ValueError("profile crawl can only run for new_creator, existing_kol, or recall_candidate items")
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


def execute_profile_crawl_for_session_item(
    *,
    session_id: int,
    item_id: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = body or {}
    execute = bool(body.get("execute"))
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
        }
    )
    updated_item = search_sessions.update_item_profile_execution(
        int(session_id),
        int(item_id),
        profile_result=profile_result,
    )
    profile_flow = profile_result.get("profile_flow") if isinstance(profile_result.get("profile_flow"), dict) else {}
    return {
        **plan,
        "execute": True,
        "status": profile_flow.get("status") or profile_result.get("status") or "unknown",
        "kol_pool_id": profile_flow.get("kol_pool_id") or profile_result.get("matched_kol_pool_id"),
        "profile_result": profile_result,
        "updated_item": updated_item,
        "viltrox_fit_score_changed_ids": profile_flow.get("viltrox_fit_score_changed_ids") or profile_result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": profile_flow.get("viltrox_fit_score_untouched") if "viltrox_fit_score_untouched" in profile_flow else profile_result.get("viltrox_fit_score_untouched"),
    }


def advance_search_session_items(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan or execute ordered profile crawl for discovery items in a session.

    This is an orchestration helper for the unified KOL input. It advances
    session items one by one through the already-safe profile flow and never
    writes V6 Fit fields directly.
    """

    body = body or {}
    execute = bool(body.get("execute"))
    limit = max(1, min(_int(body.get("limit"), 5), 15))
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
    } or {"new_creator", "existing_kol", "recall_candidate"}

    session = search_sessions.get_session(int(session_id))
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
        if item_type not in {"new_creator", "existing_kol", "recall_candidate"}:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "unsupported_item_type", "item_type": item_type})
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
    changed_ids: list[int] = []

    for item in selected:
        item_id = _int(item.get("id"))
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
        except Exception as exc:
            counts["errors"] += 1
            items.append({"item_id": item_id, "status": "error", "reason": str(exc)[:500]})

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
        if counts["failed"] or counts["errors"]:
            batch_status = "partial" if counts["ready"] or counts["partial"] else "failed"
        else:
            batch_status = "ready"
        search_sessions.update_session_result_summary(
            int(session_id),
            status=batch_status,
            summary_patch={
                "profile_batch_advance": {
                    "status": batch_status,
                    "mode": mode,
                    "limit": limit,
                    "selected": len(selected),
                    "overflow": overflow,
                    "counts": counts,
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


def enqueue_search_session_advance(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue ordered session advancement on apify_jobs for provider-safe pacing."""

    body = body or {}
    session_id = int(session_id)
    plan = advance_search_session_items(
        session_id=session_id,
        body={**body, "execute": False},
    )
    if plan.get("selected", 0) <= 0:
        return {
            "status": "nothing_to_queue",
            "session_id": session_id,
            "plan": plan,
            "provider_calls_performed": False,
            "write_db": False,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    conn = get_conn()
    existing = conn.execute(
        """
        SELECT id, job_type, status, created_at, updated_at
        FROM apify_jobs
        WHERE job_type='session_advance'
          AND status IN ('queued', 'running')
          AND payload->>'search_session_id'=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (str(session_id),),
    ).fetchone()
    if existing:
        existing_job = dict(existing)
        queued_items: dict[str, Any] | None = None
        if _text(existing_job.get("status")).lower() == "queued":
            queued_items = search_sessions.mark_items_profile_queued(
                session_id,
                item_ids=[_int(item.get("item_id")) for item in plan.get("items") or []],
                job_id=_int(existing_job.get("id")),
                reason="session_advance_already_queued",
                plan_items=plan.get("items") or [],
            )
        return {
            "status": "already_queued",
            "session_id": session_id,
            "job": existing_job,
            "queued_items": queued_items,
            "plan": plan,
            "provider_calls_performed": False,
            "write_db": bool(queued_items and queued_items.get("updated_count")),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    triggered_by_user_id = _staff_user_id(staff)
    payload = {
        "target_type": "search_session",
        "target_id": str(session_id),
        "derive_method": "kol_session_profile_advance",
        "search_session_id": session_id,
        "mode": plan.get("mode"),
        "limit": plan.get("limit"),
        "max_posts": max(1, min(_int(body.get("max_posts"), 12), 12)),
        "item_types": body.get("item_types"),
        "item_ids": body.get("item_ids"),
        "include_completed": bool(body.get("include_completed")),
        "representative_video_limit": body.get("representative_video_limit"),
        "prompt": f"profile crawl advance session:{session_id}",
        "summary": f"profile crawl advance · session {session_id}",
        "triggered_by_user_id": triggered_by_user_id,
        "viltrox_fit_score_untouched": True,
    }
    row = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES ('session_advance', ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, created_at, updated_at
        """,
        (search_sessions._json_dumps(payload),),
    ).fetchone()
    conn.commit()
    job = dict(row) if row else {}
    queued_items = search_sessions.mark_items_profile_queued(
        session_id,
        item_ids=[_int(item.get("item_id")) for item in plan.get("items") or []],
        job_id=_int(job.get("id")),
        reason="session_advance_queued",
        plan_items=plan.get("items") or [],
    )
    search_sessions.update_session_result_summary(
        session_id,
        status="running",
        summary_patch={
            "profile_batch_advance_job": {
                "status": "queued",
                "job_id": job.get("id"),
                "selected": plan.get("selected"),
                "eligible": plan.get("eligible"),
                "overflow": plan.get("overflow"),
                "queued_items": queued_items.get("updated_count"),
                "viltrox_fit_score_untouched": True,
            }
        },
    )
    return {
        "status": "queued",
        "session_id": session_id,
        "job": job,
        "queued_items": queued_items,
        "plan": plan,
        "provider_calls_performed": False,
        "write_db": True,
        "writes": ["apify_jobs", "vkpi_kol_search_sessions", "vkpi_kol_search_session_items"],
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


def cancel_search_session_advance(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Block queued session-advance jobs without interrupting running provider work."""

    del staff
    body = body or {}
    session_id = int(session_id)
    reason = _text(body.get("reason") or "session_advance_cancelled_by_user")
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, job_type, status, payload, created_at, updated_at
        FROM apify_jobs
        WHERE job_type='session_advance'
          AND status IN ('queued', 'running')
          AND payload->>'search_session_id'=?
        ORDER BY created_at DESC, id DESC
        """,
        (str(session_id),),
    ).fetchall()
    jobs = [dict(row) for row in rows]
    queued_jobs = [job for job in jobs if _text(job.get("status")).lower() == "queued"]
    running_jobs = [job for job in jobs if _text(job.get("status")).lower() == "running"]
    if not queued_jobs:
        search_sessions.update_session_result_summary(
            session_id,
            status="running" if running_jobs else "ready",
            summary_patch={
                "profile_batch_advance_job": {
                    "status": "running_not_cancelled" if running_jobs else "no_queued_job",
                    "running_job_ids": [job.get("id") for job in running_jobs],
                    "viltrox_fit_score_untouched": True,
                }
            },
        )
        return {
            "status": "running_not_cancelled" if running_jobs else "no_queued_job",
            "session_id": session_id,
            "cancelled_jobs": [],
            "running_jobs": running_jobs,
            "provider_calls_performed": False,
            "write_db": bool(running_jobs),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    cancelled_job_ids = [_int(job.get("id")) for job in queued_jobs if _int(job.get("id")) > 0]
    for job in queued_jobs:
        payload = job.get("payload")
        payload_dict = search_sessions._loads(payload, {}) if not isinstance(payload, dict) else payload
        if not isinstance(payload_dict, dict):
            payload_dict = {}
        payload_dict = {**payload_dict, "session_advance_cancelled": {"status": "cancelled", "reason": reason}}
        conn.execute(
            """
            UPDATE apify_jobs
            SET status='blocked',
                last_error=?,
                payload=?::jsonb,
                updated_at=NOW()
            WHERE id=? AND status='queued'
            """,
            (reason[:2000], search_sessions._json_dumps(payload_dict), int(job["id"])),
        )
    conn.commit()
    cancelled_items = search_sessions.mark_items_profile_cancelled(
        session_id,
        job_ids=cancelled_job_ids,
        reason=reason,
    )
    search_sessions.update_session_result_summary(
        session_id,
        status="running" if running_jobs else "partial",
        summary_patch={
            "profile_batch_advance_job": {
                "status": "cancelled" if not running_jobs else "partial_cancelled_running_remains",
                "cancelled_job_ids": cancelled_job_ids,
                "running_job_ids": [job.get("id") for job in running_jobs],
                "cancelled_items": cancelled_items.get("updated_count"),
                "reason": reason,
                "viltrox_fit_score_untouched": True,
            }
        },
    )
    return {
        "status": "cancelled" if not running_jobs else "partial_cancelled_running_remains",
        "session_id": session_id,
        "cancelled_jobs": queued_jobs,
        "running_jobs": running_jobs,
        "cancelled_items": cancelled_items,
        "provider_calls_performed": False,
        "write_db": True,
        "writes": ["apify_jobs", "vkpi_kol_search_sessions", "vkpi_kol_search_session_items"],
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


def enqueue_smart_search_profile_advance(
    *,
    query_text: str,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue the full text-search -> discovery -> profile-advance pipeline."""

    body = body or {}
    query = _text(query_text)
    if not query:
        raise ValueError("query_text is required")
    session = search_sessions.ensure_session_for_result(
        session_id=None,
        create=True,
        query_text=query,
        query_type="text_recall",
        source=_text(body.get("source") or "kol_smart_search_profile_pipeline"),
        input_payload={key: value for key, value in body.items() if key != "api_token"},
        staff=staff,
    )
    if not session:
        raise RuntimeError("smart search session was not created")
    session_id = int(session["id"])
    triggered_by_user_id = _staff_user_id(staff)
    payload = {
        "target_type": "search_session",
        "target_id": str(session_id),
        "derive_method": "kol_smart_search_profile_advance",
        "search_session_id": session_id,
        "query_text": query,
        "product_sku": _text(body.get("product_sku")),
        "candidate_limit": max(1, min(_int(body.get("candidate_limit"), 100), 500)),
        "limit": max(1, min(_int(body.get("limit"), 30), 50)),
        "creator_quota": max(0, min(_int(body.get("creator_quota"), 15), 50)),
        "reviewer_quota": max(0, min(_int(body.get("reviewer_quota"), 15), 50)),
        "ratio_policy": _text(body.get("ratio_policy") or "soft"),
        "mixed_policy": _text(body.get("mixed_policy") or "dominant"),
        "dedupe": bool(body.get("dedupe", True)),
        "vector_weight": body.get("vector_weight") if body.get("vector_weight") is not None else 0.7,
        "type_weight": body.get("type_weight") if body.get("type_weight") is not None else 0.3,
        "type_boost_enabled": bool(body.get("type_boost_enabled", True)),
        "include_new_discovery": bool(body.get("include_new_discovery", True)),
        "new_discovery_limit": max(1, min(_int(body.get("new_discovery_limit") or body.get("discovery_limit"), 15), 50)),
        "new_discovery_per_platform_limit": max(1, min(_int(body.get("new_discovery_per_platform_limit") or body.get("new_discovery_limit") or body.get("discovery_limit"), 15), 50)),
        "new_discovery_platforms": body.get("new_discovery_platforms") or body.get("discovery_platforms"),
        "platform": _text(body.get("platform")),
        "market": _text(body.get("market") or body.get("country")),
        "advance_limit": max(1, min(_int(body.get("advance_limit") or body.get("profile_advance_limit"), 15), 15)),
        "max_posts": max(1, min(_int(body.get("max_posts"), 12), 12)),
        "advance_mode": _text(body.get("advance_mode") or body.get("mode") or "profile_only"),
        "representative_video_limit": body.get("representative_video_limit"),
        "item_types": body.get("item_types") or ["new_creator", "existing_kol", "recall_candidate"],
        "include_completed": bool(body.get("include_completed")),
        "prompt": f"smart profile advance · {query[:120]}",
        "summary": f"smart profile advance · {query[:80]}",
        "triggered_by_user_id": triggered_by_user_id,
        "viltrox_fit_score_untouched": True,
    }
    conn = get_conn()
    row = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES ('smart_search_profile_advance', ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, created_at, updated_at
        """,
        (search_sessions._json_dumps(payload),),
    ).fetchone()
    conn.commit()
    job = dict(row) if row else {}
    search_sessions.update_session_result_summary(
        session_id,
        status="running",
        summary_patch={
            "smart_search_profile_advance_job": {
                "status": "queued",
                "job_id": job.get("id"),
                "query_text": query,
                "include_new_discovery": payload["include_new_discovery"],
                "advance_limit": payload["advance_limit"],
                "viltrox_fit_score_untouched": True,
            }
        },
    )
    return {
        "status": "queued",
        "session_id": session_id,
        "search_session": search_sessions.get_session(session_id),
        "job": job,
        "provider_calls_performed": False,
        "write_db": True,
        "writes": ["vkpi_kol_search_sessions", "apify_jobs"],
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


async def execute_smart_search_profile_advance_pipeline(
    *,
    session_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute a queued text recall/new-discovery/profile-advance pipeline."""

    query = _text(payload.get("query_text") or payload.get("input") or payload.get("query"))
    if not query:
        raise ValueError("smart profile advance payload missing query_text")
    recall_result = profile_recall.recall_kol_profiles(
        query_text=query,
        product_sku=_text(payload.get("product_sku")),
        candidate_limit=max(1, min(_int(payload.get("candidate_limit"), 100), 500)),
        limit=max(1, min(_int(payload.get("limit"), 30), 50)),
        creator_quota=max(0, min(_int(payload.get("creator_quota"), 15), 50)),
        reviewer_quota=max(0, min(_int(payload.get("reviewer_quota"), 15), 50)),
        ratio_policy=_text(payload.get("ratio_policy") or "soft"),
        mixed_policy=_text(payload.get("mixed_policy") or "dominant"),
        dedupe=bool(payload.get("dedupe", True)),
        vector_weight=float(payload.get("vector_weight") if payload.get("vector_weight") is not None else 0.7),
        type_weight=float(payload.get("type_weight") if payload.get("type_weight") is not None else 0.3),
        type_boost_enabled=bool(payload.get("type_boost_enabled", True)),
    )
    recall_session = search_sessions.attach_recall_result(int(session_id), recall_result)
    new_discovery: dict[str, Any] | None = None
    if bool(payload.get("include_new_discovery", True)):
        new_discovery = await discover_new_creators(
            query_text=query,
            platforms=payload.get("new_discovery_platforms") or payload.get("discovery_platforms"),
            platform_hint=_text(payload.get("platform")),
            market=_text(payload.get("market") or payload.get("country")),
            limit=max(1, min(_int(payload.get("new_discovery_limit"), 15), 50)),
            per_platform_limit=max(1, min(_int(payload.get("new_discovery_per_platform_limit"), 15), 50)),
        )
        search_sessions.attach_new_discovery_result(int(session_id), new_discovery)

    advance_result = advance_search_session_items(
        session_id=int(session_id),
        body={
            **payload,
            "execute": True,
            "limit": max(1, min(_int(payload.get("advance_limit") or payload.get("profile_advance_limit"), 15), 15)),
            "max_posts": max(1, min(_int(payload.get("max_posts"), 12), 12)),
            "mode": _text(payload.get("advance_mode") or payload.get("mode") or "profile_only"),
            "item_types": payload.get("item_types") or ["new_creator", "existing_kol", "recall_candidate"],
            "include_completed": bool(payload.get("include_completed")),
        },
    )
    changed_ids = [
        _int(value)
        for value in (advance_result.get("viltrox_fit_score_changed_ids") or [])
        if _int(value) > 0
    ]
    pipeline_status = "failed" if advance_result.get("status") == "failed" else "ready"
    search_sessions.update_session_result_summary(
        int(session_id),
        status="partial" if changed_ids or advance_result.get("status") == "partial" else pipeline_status,
        summary_patch={
            "smart_search_profile_advance_job": {
                "status": pipeline_status,
                "query_text": query,
                "recall_returned": len(recall_result.get("items") or []),
                "new_discovery_status": (new_discovery or {}).get("status") if new_discovery else "not_requested",
                "advance_status": advance_result.get("status"),
                "advance_counts": advance_result.get("counts"),
                "viltrox_fit_score_changed_ids": changed_ids,
                "viltrox_fit_score_untouched": not changed_ids,
            }
        },
    )
    return {
        "status": pipeline_status,
        "session_id": int(session_id),
        "query": query,
        "recall": {
            "method": recall_result.get("method"),
            "returned_count": len(recall_result.get("items") or []),
            "diagnostics": recall_result.get("diagnostics"),
            "search_session": recall_session,
        },
        "new_discovery": new_discovery,
        "advance": advance_result,
        "provider_calls_performed": True,
        "write_db": True,
        "writes": ["vkpi_kol_search_sessions", "vkpi_kol_search_session_items", "vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs"],
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
    }


async def discover_new_creators(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    market: str = "",
    limit: int = 15,
    per_platform_limit: int = 15,
) -> dict[str, Any]:
    """Search platforms for creator candidates and mark existing KOL matches."""
    query = _text(query_text)
    safe_limit = max(1, min(_int(limit, 15), 50))
    safe_per_platform = max(1, min(_int(per_platform_limit, 15), 50))
    resolved_platforms = _platforms(platforms, fallback=platform_hint)
    if not query:
        return {
            "status": "invalid_query",
            "query": query,
            "platforms": resolved_platforms,
            "items": [],
            "new_creators": [],
            "existing_matches": [],
            "provider_calls": False,
            "message": "query is required",
        }

    new_creators: list[dict[str, Any]] = []
    existing_matches: list[dict[str, Any]] = []
    platform_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[dict[str, Any]] = []

    for platform in resolved_platforms:
        result = await search_platform_content(
            platform,
            query,
            market=_text(market).upper(),
            max_results=safe_per_platform,
        )
        raw_items = [dict(item or {}) for item in (result.get("items") or [])]
        annotated = history_match.annotate_platform_items(raw_items, platform=platform)
        platform_results.append(
            {
                "platform": platform,
                "status": result.get("status"),
                "returned": len(annotated),
                "metadata": result.get("metadata") or {},
                "message": result.get("message"),
            }
        )
        if result.get("status") not in {"done", "ready"} and not annotated:
            errors.append({"platform": platform, "status": result.get("status"), "message": result.get("message")})
        for item in annotated:
            key = _candidate_key(item, platform)
            if key in seen:
                continue
            seen.add(key)
            if item.get("historical_match") or item.get("history_kol_pool_id"):
                existing_matches.append(item)
                continue
            if len(new_creators) < safe_limit:
                new_creators.append(item)

    status = "ready" if new_creators or existing_matches else "empty"
    if errors and (new_creators or existing_matches):
        status = "partial"
    elif errors:
        status = "failed"
    return {
        "status": status,
        "query": query,
        "platforms": resolved_platforms,
        "market": _text(market).upper(),
        "limit": safe_limit,
        "per_platform_limit": safe_per_platform,
        "items": [*existing_matches, *new_creators],
        "new_creators": new_creators,
        "existing_matches": existing_matches,
        "counts": {
            "new_creators": len(new_creators),
            "existing_matches": len(existing_matches),
            "platforms": len(resolved_platforms),
            "errors": len(errors),
        },
        "platform_results": platform_results,
        "errors": errors,
        "provider_calls": True,
    }
