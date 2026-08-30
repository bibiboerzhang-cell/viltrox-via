"""Durable URL-crawl orchestration behind the KOL search HTTP facade."""
from __future__ import annotations

from typing import Any


def _should_defer_provider(classified: Any, url_deep_crawl: Any) -> bool:
    if not classified:
        return False
    if (
        classified.url_type in {"profile", "video"}
        and classified.platform in url_deep_crawl.SUPPORTED_PLATFORMS
    ):
        return True
    return bool(
        classified.url_type == "video"
        and classified.platform in url_deep_crawl.CN_VIDEO_ANALYSIS_PLATFORMS
    )


def _enqueue_deferred_work(
    *,
    body: dict,
    result: dict,
    session: dict | None,
    classified: Any,
    staff: dict,
    default_source: str,
    url_deep_crawl: Any,
    int_or_none,
    reused_video_session_lineage,
    prepare_video_resolver_session_item,
) -> dict[str, Any]:
    matched_kol_pool_id = int_or_none(result.get("matched_kol_pool_id"))
    is_profile = bool(classified and classified.url_type == "profile")
    video_flow = result.get("video_flow") if isinstance(result.get("video_flow"), dict) else {}
    stored_evidence_id = int_or_none(video_flow.get("evidence_id"))
    reused_stored_video = bool(not is_profile and matched_kol_pool_id and stored_evidence_id)

    if reused_stored_video:
        session, search_session_item_id = reused_video_session_lineage(
            session,
            result,
            body=body,
            staff=staff,
            default_source=default_source,
            kol_pool_id=int(matched_kol_pool_id),
            evidence_id=int(stored_evidence_id),
        )
        queued = url_deep_crawl.enqueue_stored_video_analysis_job(
            kol_pool_id=int(matched_kol_pool_id),
            evidence_id=int(stored_evidence_id),
            staff=staff,
            search_session_id=int_or_none(session.get("id")),
            search_session_item_id=search_session_item_id,
            source=str(body.get("source") or f"{default_source}_existing_video"),
            local_evaluation=False,
        )
    elif is_profile and url_deep_crawl.profile_deep_crawl_is_fresh(matched_kol_pool_id):
        queued = {"status": "already_fresh", "job_id": None}
    elif not is_profile:
        resolver_item_id = prepare_video_resolver_session_item(session, result)
        queued = url_deep_crawl.enqueue_video_url_resolve_job(
            str(body.get("url") or ""),
            staff=staff,
            search_session_id=int_or_none((session or {}).get("id")),
            search_session_item_id=resolver_item_id,
            source=str(body.get("source") or f"{default_source}_video_resolve"),
            max_posts=int(body.get("max_posts") or 3),
            local_evaluation=False,
        )
    else:
        queued = url_deep_crawl.enqueue_profile_deep_crawl_job(
            str(body.get("url") or ""),
            kol_pool_id=matched_kol_pool_id,
            max_posts=int(body.get("max_posts") or 3),
            mode=str(body.get("mode") or "account_deep"),
            representative_video_limit=int(body.get("representative_video_limit") or 1),
            staff=staff,
            search_session_id=int_or_none((session or {}).get("id")),
            source=str(body.get("source") or f"{default_source}_deferred"),
        )
    return {
        "session": session,
        "queued": queued,
        "is_profile": is_profile,
        "stored_evidence_id": stored_evidence_id,
        "reused_stored_video": reused_stored_video,
    }


def _flow_message(
    *,
    already_fresh: bool,
    reused_stored_video: bool,
    queue_active: bool,
    video_resolver_queued: bool,
    direct_video_status: str,
) -> str:
    if already_fresh:
        return "账号资料在 24 小时内已更新，直接复用现有档案。"
    if reused_stored_video and queue_active:
        return "已复用本地视频证据并排入 final_v1 深析。"
    if reused_stored_video and direct_video_status in {"ai_disabled", "not_requested"}:
        return "已复用本地视频证据；AI 深析当前未启用，本轮没有创建模型任务。"
    if reused_stored_video:
        return "已复用本地视频证据与现有分析。"
    if video_resolver_queued:
        return "已进入视频 URL 专用队列；将按解析视频、识别作者、缓存媒体、AI 分析分阶段回填。"
    return "已进入后台队列；抓取、联系方式、受众和视频分析结果会分阶段回填。"


def _project_deferred_result(result: dict, state: dict[str, Any], pending_enrichment_state) -> None:
    queued = state["queued"]
    is_profile = state["is_profile"]
    reused_stored_video = state["reused_stored_video"]
    flow_key = "profile_flow" if is_profile else "video_flow"
    flow = result.get(flow_key) if isinstance(result.get(flow_key), dict) else {}
    already_fresh = queued.get("status") == "already_fresh"
    queue_active = queued.get("status") in {"queued", "already_queued"}
    video_resolver_queued = bool(not is_profile and not reused_stored_video)
    enrichment = (
        None
        if already_fresh or reused_stored_video or video_resolver_queued
        else pending_enrichment_state()
    )
    direct_video_ai = queued.get("ai_analysis") if isinstance(queued.get("ai_analysis"), dict) else None
    direct_video_status = str(queued.get("status") or "") if not is_profile else ""
    result.update(
        {
            "dry_run": False,
            "execute": True,
            "deferred_to_queue": queue_active,
            "writes_performed": bool(queued.get("write_db")) if reused_stored_video else queue_active,
            "provider_calls_performed": False,
            "worker_touched": queue_active,
            "enrichment": enrichment,
            flow_key: {
                **flow,
                "status": "ready" if already_fresh else direct_video_status or str(queued.get("status") or "queued"),
                "operation": (
                    "reuse_recent_profile"
                    if already_fresh
                    else "existing_creator_video_analysis"
                    if reused_stored_video
                    else "profile_deep_crawl_queue"
                    if is_profile
                    else "video_url_resolve_queue"
                ),
                "job_id": queued.get("job_id"),
                "evidence_id": state["stored_evidence_id"] or flow.get("evidence_id"),
                "enqueue_result": queued if not is_profile else flow.get("enqueue_result"),
                "resolution_progress": queued.get("resolution_progress") if video_resolver_queued else flow.get("resolution_progress"),
                "ai_analysis": direct_video_ai or flow.get("ai_analysis"),
                "enrichment": enrichment,
                "message": _flow_message(
                    already_fresh=already_fresh,
                    reused_stored_video=reused_stored_video,
                    queue_active=queue_active,
                    video_resolver_queued=video_resolver_queued,
                    direct_video_status=direct_video_status,
                ),
                "crawl_performed": False,
                "business_tables_written": bool(queued.get("write_db")) if reused_stored_video else False,
                "worker_touched": queue_active,
            },
        }
    )


def run_url_deep_crawl(
    body: dict,
    *,
    staff: dict,
    default_create_session: bool,
    default_source: str,
    url_deep_crawl: Any,
    search_sessions: Any,
    body_bool,
    int_or_none,
    reused_video_session_lineage,
    prepare_video_resolver_session_item,
    pending_enrichment_state,
    url_response_status,
) -> dict:
    """Execute the durable URL path with all route-owned effects injected."""
    if body.get("local_evaluation") is True:
        raise ValueError("local_evaluation_http_forbidden")
    session_id = body.get("session_id")
    try:
        session_id_int = int(session_id) if session_id else None
    except (TypeError, ValueError):
        raise ValueError("session_id must be an integer") from None

    execute = body_bool(body, "execute")
    classified = url_deep_crawl.classify_url(str(body.get("url") or "")) if execute else None
    defer_provider = _should_defer_provider(classified, url_deep_crawl)
    crawl_body = {**body, "execute": False} if defer_provider else body
    result = url_deep_crawl.dry_run_url_deep_crawl(crawl_body)
    session = search_sessions.ensure_session_for_result(
        session_id=session_id_int,
        create=body_bool(body, "create_session", default=default_create_session),
        query_text=str(body.get("url") or ""),
        query_type=(
            "url_video"
            if result.get("url_type") == "video"
            else "url_profile"
            if result.get("url_type") == "profile"
            else "unknown"
        ),
        source=str(body.get("source") or default_source),
        input_payload={key: value for key, value in body.items() if key != "api_token"},
        staff=staff,
    )
    if defer_provider:
        state = _enqueue_deferred_work(
            body=body,
            result=result,
            session=session,
            classified=classified,
            staff=staff,
            default_source=default_source,
            url_deep_crawl=url_deep_crawl,
            int_or_none=int_or_none,
            reused_video_session_lineage=reused_video_session_lineage,
            prepare_video_resolver_session_item=prepare_video_resolver_session_item,
        )
        session = state["session"]
        _project_deferred_result(result, state, pending_enrichment_state)
    result["status"] = url_response_status(result)
    if session:
        result["search_session"] = search_sessions.attach_url_result(int(session["id"]), result)
    return result
