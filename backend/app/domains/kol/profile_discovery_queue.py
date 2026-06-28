"""Apify-job queue orchestration for KOL Pool smart-search profile advance.

Behaviour-preserving extraction from profile_discovery.py (move + re-export).
Functions enqueue / cancel ordered profile-crawl advancement on apify_jobs for
provider-safe pacing. Never writes V6 Fit fields directly.
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol import search_sessions
from app.domains.kol.discovery_filters import _int, _staff_user_id, _text


def enqueue_search_session_advance(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue ordered session advancement on apify_jobs for provider-safe pacing."""

    from app.domains.kol.profile_discovery import advance_search_session_items

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
        WHERE job_type IN ('session_advance', 'smart_search_profile_advance')
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
        WHERE job_type IN ('session_advance', 'smart_search_profile_advance')
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
                },
                "smart_search_profile_advance_job": {
                    "status": "running_not_cancelled" if any(_text(job.get("job_type")) == "smart_search_profile_advance" for job in running_jobs) else "no_queued_job",
                    "running_job_ids": [job.get("id") for job in running_jobs if _text(job.get("job_type")) == "smart_search_profile_advance"],
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
                "cancelled_job_ids": [job.get("id") for job in queued_jobs if _text(job.get("job_type")) == "session_advance"],
                "running_job_ids": [job.get("id") for job in running_jobs if _text(job.get("job_type")) == "session_advance"],
                "cancelled_items": cancelled_items.get("updated_count"),
                "reason": reason,
                "viltrox_fit_score_untouched": True,
            },
            "smart_search_profile_advance_job": {
                "status": "cancelled" if any(_text(job.get("job_type")) == "smart_search_profile_advance" for job in queued_jobs) else "not_cancelled",
                "cancelled_job_ids": [job.get("id") for job in queued_jobs if _text(job.get("job_type")) == "smart_search_profile_advance"],
                "running_job_ids": [job.get("id") for job in running_jobs if _text(job.get("job_type")) == "smart_search_profile_advance"],
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
        # why-fit 人群侧上下文(纯展示透传):planner 计划里的产品人群,worker 跑 recall 时拼"适合理由"。
        "product_focus": (body.get("llm_query_plan") or {}).get("product_focus") if isinstance(body.get("llm_query_plan"), dict) else None,
        "target_persona": (body.get("llm_query_plan") or {}).get("target_persona") if isinstance(body.get("llm_query_plan"), dict) else "",
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
        # 收口路①-2:内容契合入队控量旋钮(默认开,top N=6);worker→pipeline 透传。
        "include_content_fit": bool(body.get("include_content_fit", True)),
        "content_fit_top_n": max(1, min(_int(body.get("content_fit_top_n"), 6), 12)),
        "new_discovery_limit": max(1, min(_int(body.get("new_discovery_limit") or body.get("discovery_limit"), 15), 50)),
        "new_discovery_per_platform_limit": max(1, min(_int(body.get("new_discovery_per_platform_limit") or body.get("new_discovery_limit") or body.get("discovery_limit"), 15), 50)),
        "new_discovery_platforms": body.get("new_discovery_platforms") or body.get("discovery_platforms"),
        "platform": _text(body.get("platform")),
        "market": _text(body.get("market") or body.get("country")),
        "advance_limit": max(1, min(_int(body.get("advance_limit") or body.get("profile_advance_limit"), 15), 15)),
        "max_posts": max(1, min(_int(body.get("max_posts"), 12), 12)),
        "advance_mode": _text(body.get("advance_mode") or body.get("mode") or "account_deep"),
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
                "advance_mode": payload["advance_mode"],
                "representative_video_limit": payload["representative_video_limit"] or 1,
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
