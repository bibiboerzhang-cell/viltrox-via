"""Apify-job queue orchestration for KOL Pool smart-search profile advance.

Behaviour-preserving extraction from profile_discovery.py (move + re-export).
Functions enqueue / cancel ordered profile-crawl advancement on apify_jobs for
provider-safe pacing. Never writes V6 Fit fields directly.
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol import (
    profile_recall,
    profile_recall_qualification,
    search_sessions,
    search_sessions_online,
)
from app.domains.kol.discovery_filters import _int, _staff_user_id, _text
from app.domains.kol.provider_job_access import (
    FENCE_KEY as PROVIDER_JOB_FENCE_KEY,
    SESSION_ADVANCE,
    SMART_SEARCH_PROFILE_ADVANCE,
    ServerOwnedProviderCapability,
    build_search_session_provider_fence,
)
from app.domains.kol.search_progress_contract import completion_contract
from app.domains.tasks.apify_idempotency import active_job_idempotency_key, enqueue_active_apify_job


def _pending_enrichment() -> dict[str, Any]:
    return {
        "contacts": {"status": "pending", "async": True},
        "audience": {"status": "pending", "async": True},
    }


def _requests_smart_local_30(body: dict[str, Any]) -> bool:
    """Recognize only the named UI contract; never trust client-supplied limits as policy."""
    spec = body.get("local_qualification_spec")
    return bool(
        isinstance(spec, dict)
        and _text(spec.get("version")) == "local_30_v1"
        and _int(spec.get("target_count")) == 30
    )


def _requests_smart_online_30(body: dict[str, Any]) -> bool:
    """Recognize only the named online net-new contract."""
    spec = body.get("online_qualification_spec")
    return bool(
        isinstance(spec, dict)
        and _text(spec.get("version")) == "online_net_new_30_v1"
        and _int(spec.get("target_count")) == 30
    )


def enqueue_search_session_advance(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    server_owned_capability: ServerOwnedProviderCapability | None = None,
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
        search_sessions.update_session_result_summary(
            session_id,
            status="partial",
            summary_patch={
                "profile_batch_advance_job": {
                    "status": "nothing_to_queue",
                    "selected": 0,
                    "eligible": plan.get("eligible"),
                    "reason": "no eligible profile items",
                    "viltrox_fit_score_untouched": True,
                }
            },
        )
        return {
            "status": "nothing_to_queue",
            "session_id": session_id,
            "plan": plan,
            "provider_calls_performed": False,
            "write_db": True,
            "writes": ["vkpi_kol_search_sessions"],
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "enrichment": None,
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
            "enrichment": _pending_enrichment(),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    session = search_sessions.get_session(
        session_id,
        staff=staff,
        scope_to_staff=server_owned_capability is None,
    )
    triggered_by_user_id = _staff_user_id(staff)
    payload = {
        "queue_lane": "interactive",
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
        "_async_enrichment": True,
        "representative_video_limit": body.get("representative_video_limit"),
        "prompt": f"profile crawl advance session:{session_id}",
        "summary": f"profile crawl advance · session {session_id}",
        "triggered_by_user_id": triggered_by_user_id,
        "staff_id": (staff or {}).get("staff_id") or (staff or {}).get("id"),
        "viltrox_fit_score_untouched": True,
    }
    payload[PROVIDER_JOB_FENCE_KEY] = build_search_session_provider_fence(
        action=SESSION_ADVANCE,
        session=session,
        payload=payload,
        staff=staff,
        server_owned_capability=server_owned_capability,
    )
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type="session_advance",
        payload=payload,
        idempotency_key=active_job_idempotency_key("search_session_profile_advance", session_id),
    )
    conn.commit()
    queued_items = search_sessions.mark_items_profile_queued(
        session_id,
        item_ids=[_int(item.get("item_id")) for item in plan.get("items") or []],
        job_id=_int(job.get("id")),
        reason="session_advance_queued",
        plan_items=plan.get("items") or [],
    )
    selected_count = int(plan.get("selected") or 0)
    queued_contract = completion_contract(
        base_count=int(plan.get("eligible") or selected_count),
        total=selected_count,
        terminal_count=0,
        ready_count=0,
        active_tasks=selected_count,
        requested_tasks_terminal=False,
    )
    search_sessions.update_session_result_summary(
        session_id,
        status="running",
        summary_patch={
            "phase": "profile",
            "progress": {
                "base": int(plan.get("eligible") or plan.get("selected") or 0),
                "total": int(plan.get("selected") or 0),
                "profile_ready": 0,
                "profile_failed": 0,
                "complete_ready": 0,
                "complete_partial": 0,
                **queued_contract,
            },
            **queued_contract,
            "profile_batch_advance_job": {
                "status": "queued" if inserted else "already_queued",
                "job_id": job.get("id"),
                "selected": plan.get("selected"),
                "eligible": plan.get("eligible"),
                "overflow": plan.get("overflow"),
                "queued_items": queued_items.get("updated_count"),
                "enrichment": _pending_enrichment(),
                "viltrox_fit_score_untouched": True,
            }
        },
    )
    return {
        "status": "queued" if inserted else "already_queued",
        "session_id": session_id,
        "job": job,
        "queued_items": queued_items,
        "plan": plan,
        "provider_calls_performed": False,
        "write_db": True,
        "writes": (["apify_jobs"] if inserted else []) + ["vkpi_kol_search_sessions", "vkpi_kol_search_session_items"],
        "enrichment": _pending_enrichment(),
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
        current_status = "partial"
        if not running_jobs:
            try:
                observed = _text(search_sessions.get_session(session_id).get("status")).lower()
                if observed in {"ready", "partial", "failed", "cancelled"}:
                    current_status = observed
            except Exception:
                current_status = "partial"
        search_sessions.update_session_result_summary(
            session_id,
            status="running" if running_jobs else current_status,
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
    server_owned_capability: ServerOwnedProviderCapability | None = None,
) -> dict[str, Any]:
    """Queue the full text-search -> discovery -> profile-advance pipeline."""

    body = body or {}
    query = _text(query_text)
    if not query:
        raise ValueError("query_text is required")
    smart_local_30 = _requests_smart_local_30(body)
    smart_online_30 = _requests_smart_online_30(body)
    if smart_online_30:
        # Reject unsupported strict platforms before creating a session/job or
        # spending provider budget. Legacy discovery keeps its wider surface.
        from app.domains.kol import profile_online_qualification

        profile_online_qualification.online_policy(
            market=body.get("market") or body.get("country"),
            platforms=(
                body.get("new_discovery_platforms")
                or body.get("discovery_platforms")
                or body.get("platforms")
                or body.get("platform")
            ),
            languages=body.get("languages") or body.get("content_languages"),
            profile_types=body.get("profile_types") or body.get("kol_types"),
            exclude_chinese=bool(body.get("exclude_chinese", True)),
        )
    raw_session_id = body.get("session_id") or body.get("search_session_id")
    try:
        requested_session_id = int(raw_session_id) if raw_session_id not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("session_id must be an integer") from None
    session = search_sessions.ensure_session_for_result(
        session_id=requested_session_id,
        create=requested_session_id is None,
        query_text=query,
        query_type="text_recall",
        source=_text(body.get("source") or "kol_smart_search_profile_pipeline"),
        input_payload={key: value for key, value in body.items() if key != "api_token"},
        staff=staff,
    )
    if not session:
        raise RuntimeError("smart search session was not created")
    if requested_session_id and _text(session.get("query_text")) != query:
        raise ValueError("session query does not match profile-advance query")
    if requested_session_id and _text(session.get("query_type")) != "text_recall":
        raise ValueError("session is not a text-recall session")
    session_id = int(session["id"])
    triggered_by_user_id = _staff_user_id(staff)
    if body.get("filters") not in (None, "") and not isinstance(body.get("filters"), dict):
        raise ValueError("filters must be an object")
    recall_filters = dict(body.get("filters") or {})
    if body.get("platforms") and not recall_filters.get("platforms"):
        recall_filters["platforms"] = body.get("platforms")
    for filter_key in (
        "countries", "languages", "followers_min", "followers_max",
        "follower_min", "follower_max", "verticals", "gear_content",
    ):
        if body.get(filter_key) not in (None, "") and filter_key not in recall_filters:
            recall_filters[filter_key] = body.get(filter_key)
    result_limit = max(
        1,
        min(
            _int(
                body.get("result_limit") or body.get("candidate_count") or body.get("limit"),
                profile_recall.DEFAULT_RESULT_LIMIT,
            ),
            50,
        ),
    )
    payload = {
        "queue_lane": "interactive",
        "target_type": "search_session",
        "target_id": str(session_id),
        "derive_method": "kol_smart_search_profile_advance",
        "search_session_id": session_id,
        "query_text": query,
        "product_sku": _text(body.get("product_sku")),
        # The browser may submit a previous preview plan, but a durable worker
        # must derive product/persona identity again from the operator query
        # and explicit SKU.  Never promote client-supplied plan fields into a
        # trusted queued payload.
        "product_focus": None,
        "target_persona": "",
        "candidate_limit": max(1, min(_int(body.get("candidate_limit"), 100), 500)),
        "limit": result_limit,
        "result_limit": result_limit,
        "creator_quota": max(0, min(_int(body.get("creator_quota"), 15), 50)),
        "reviewer_quota": max(0, min(_int(body.get("reviewer_quota"), 15), 50)),
        "ratio_policy": _text(body.get("ratio_policy") or "soft"),
        "mixed_policy": _text(body.get("mixed_policy") or "dominant"),
        "dedupe": bool(body.get("dedupe", True)),
        "vector_weight": body.get("vector_weight") if body.get("vector_weight") is not None else profile_recall_qualification.SMART_LOCAL_VECTOR_WEIGHT,
        "type_weight": body.get("type_weight") if body.get("type_weight") is not None else profile_recall_qualification.SMART_LOCAL_TYPE_WEIGHT,
        "type_boost_enabled": bool(body.get("type_boost_enabled", True)),
        # 接线补漏(2026-07-02 验收):前端「排除 中国/港/台 地区」开关一直随 body 传到这里,但此前
        # payload 漏透传 → worker 的 execute_smart_search_profile_advance_pipeline 只能吃默认 True,
        # 用户取消勾选形同虚设。补上后与同步(非队列)路径的 recall exclude_chinese 语义一致。
        "exclude_chinese": bool(body.get("exclude_chinese", True)),
        "filters": recall_filters,
        "search_strategy": _text(body.get("search_strategy") or "balanced"),
        "bucket_policy": (
            body.get("bucket_policy")
            if isinstance(body.get("bucket_policy"), dict)
            else body.get("bucketPolicy")
            if isinstance(body.get("bucketPolicy"), dict)
            else None
        ),
        "allow_backfill": bool(body.get("allow_backfill", True)),
        "include_new_discovery": bool(body.get("include_new_discovery", True)),
        # 收口路①-2:内容契合入队控量旋钮(默认开,top N=6);worker→pipeline 透传。
        "include_content_fit": bool(body.get("include_content_fit", True)),
        "content_fit_top_n": max(1, min(_int(body.get("content_fit_top_n"), 6), 12)),
        "new_discovery_limit": max(1, min(_int(body.get("new_discovery_limit") or body.get("discovery_limit"), 50 if smart_online_30 else 15), 50)),
        "new_discovery_per_platform_limit": max(1, min(_int(body.get("new_discovery_per_platform_limit") or body.get("new_discovery_limit") or body.get("discovery_limit"), 50 if smart_online_30 else 15), 50)),
        "new_discovery_platforms": body.get("new_discovery_platforms") or body.get("discovery_platforms"),
        "platform": _text(body.get("platform")),
        "market": _text(body.get("market") or body.get("country")),
        # Explicit operator filters are re-normalized by the worker's
        # server-owned qualification policy. Keep raw bounded request values;
        # an invalid value must fail closed instead of silently widening.
        "languages": body.get("languages") or body.get("content_languages"),
        "profile_types": body.get("profile_types") or body.get("kol_types"),
        "advance_limit": max(
            1,
            min(
                _int(body.get("advance_limit") or body.get("profile_advance_limit"), 30 if smart_local_30 else 15),
                30 if smart_local_30 else 15,
            ),
        ),
        "_smart_local_30_contract": smart_local_30,
        "_smart_online_30_contract": smart_online_30,
        "max_posts": max(1, min(_int(body.get("max_posts"), 12), 12)),
        "advance_mode": _text(body.get("advance_mode") or body.get("mode") or "account_deep"),
        "representative_video_limit": body.get("representative_video_limit"),
        "item_types": body.get("item_types") or ["new_creator", "existing_kol", "recall_candidate"],
        "include_completed": bool(body.get("include_completed")),
        "_async_enrichment": True,
        "prompt": f"smart profile advance · {query[:120]}",
        "summary": f"smart profile advance · {query[:80]}",
        "triggered_by_user_id": triggered_by_user_id,
        "staff_id": (staff or {}).get("staff_id") or (staff or {}).get("id"),
        "viltrox_fit_score_untouched": True,
    }
    payload[PROVIDER_JOB_FENCE_KEY] = build_search_session_provider_fence(
        action=SMART_SEARCH_PROFILE_ADVANCE,
        session=session,
        payload=payload,
        staff=staff,
        fallback_query_text=query,
        fallback_query_type="text_recall",
        fallback_input_payload={
            key: value for key, value in body.items() if key != "api_token"
        },
        server_owned_capability=server_owned_capability,
    )
    conn = get_conn()
    idempotency_key = active_job_idempotency_key("search_session_profile_advance", session_id)
    row = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, idempotency_key, status, created_at, updated_at)
        VALUES ('smart_search_profile_advance', ?::jsonb, ?, 'queued', NOW(), NOW())
        ON CONFLICT (idempotency_key)
          WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
            AND status IN ('queued', 'running')
        DO NOTHING
        RETURNING id, job_type, status, created_at, updated_at
        """,
        (search_sessions._json_dumps(payload), idempotency_key),
    ).fetchone()
    inserted = bool(row)
    if not row:
        row = conn.execute(
            """SELECT id, job_type, status, created_at, updated_at FROM apify_jobs
               WHERE idempotency_key=? AND status IN ('queued', 'running')
               ORDER BY id DESC LIMIT 1""",
            (idempotency_key,),
        ).fetchone()
    job = dict(row) if row else {}
    conn.commit()
    queued_total = int(payload["advance_limit"])
    queued_contract = completion_contract(
        base_count=0,
        total=queued_total,
        terminal_count=0,
        ready_count=0,
        active_tasks=queued_total,
        requested_tasks_terminal=False,
    )
    search_sessions.update_session_result_summary(
        session_id,
        status="running",
        summary_patch={
            "phase": "base",
            "progress": {
                "base": 0,
                "total": queued_total,
                "profile_ready": 0,
                "profile_failed": 0,
                "complete_ready": 0,
                "complete_partial": 0,
                **queued_contract,
            },
            **queued_contract,
            **({
                "online_qualification": search_sessions_online.queued_online_qualification()
            } if smart_online_30 else {}),
            "smart_search_profile_advance_job": {
                "status": "queued" if inserted else "already_queued",
                "job_id": job.get("id"),
                "query_text": query,
                "include_new_discovery": payload["include_new_discovery"],
                "advance_limit": payload["advance_limit"],
                "advance_mode": payload["advance_mode"],
                "representative_video_limit": payload["representative_video_limit"] or 1,
                "enrichment": _pending_enrichment(),
                "viltrox_fit_score_untouched": True,
            }
        },
    )
    return {
        "status": "queued" if inserted else "already_queued",
        "session_id": session_id,
        "search_session": search_sessions.get_session(session_id),
        "job": job,
        "provider_calls_performed": False,
        "write_db": True,
        "writes": ["vkpi_kol_search_sessions"] + (["apify_jobs"] if inserted else []),
        "enrichment": _pending_enrichment(),
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }
