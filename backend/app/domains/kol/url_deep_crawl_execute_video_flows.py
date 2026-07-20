"""URL deep-crawl video-flow execute cluster (从 url_deep_crawl_execute.py 抽出,行为不变).

包含 video URL 的 R2 媒体回灌 + 老创作者 video 分析 + 新创作者 video(建档+证据+入队)三条
execute 路径。函数体逐字搬运,调用点由 url_deep_crawl_execute re-export 兜住(behavior-preserving move)。

循环导入纪律:本模块绝不在顶层 import url_deep_crawl_execute;留在原文件的 helper
(_record_deep_crawl_run / _crawl_profile_basics / _profile_target /
_profile_data_for_new_video_creator / _profile_incremental_state /
_profile_classified_from_video_flow / _enqueue_account_dossier_extract_followup)
在函数体内 lazy import。ClassifiedUrl 仅作类型注解,from __future__ annotations 已字符串化。

红线:LLM 绝不写 viltrox_fit_score;本模块零 fit 写,只透传上游 *_changed_ids。
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.url_deep_crawl_execute_profile_videos import (
    _execute_profile_history_video_evidence,
    _execute_profile_representative_video_analysis,
)
from app.domains.kol.url_deep_crawl_helpers import (
    _compact_enqueue_result,
    _compact_profile_write_result,
    _compact_video_evidence_result,
    _fit_changed_ids,
    _max_posts,
    _public_profile_data,
    _video_execute_mode,
)
from app.domains.kol.profile_basics import write_kol_profile_basics
from app.domains.kol.video_analysis_enqueue import _enqueue_final_v1_video_analysis
from app.domains.kol.video_evidence import ensure_video_evidence_from_url

if TYPE_CHECKING:
    from app.domains.kol.url_deep_crawl import ClassifiedUrl

logger = get_logger("viltrox.domains.kol.url_deep_crawl_execute")

_VIDEO_BASE_COMPLETE_STATUSES = {
    "queued",
    "already_queued",
    "already_analyzed",
    "already_evaluated",
    "ai_disabled",
}


def _video_ai_analysis(enqueue_result: dict[str, Any]) -> dict[str, Any]:
    value = enqueue_result.get("ai_analysis") if isinstance(enqueue_result, dict) else None
    if isinstance(value, dict):
        return value
    return {
        "state": "not_requested",
        "reason": "analysis_not_requested",
        "gate_reason": "",
        "model_readiness_status": "not_ready",
        "provider_calls_allowed": False,
    }


def _cache_video_flow_url(
    classified: ClassifiedUrl,
    metadata: dict[str, Any] | None,
    evidence_id: int | None,
) -> tuple[str | None, bool]:
    """为 video URL 结果区把 IG/TikTok 视频就地喂 R2,返回 (cached_video_url, provider_called)。

    YouTube 走前端 embed 不缓存;失败/skip 不毁主链(媒体缓存属增强)。
    模式照搬 url_deep_crawl 媒体回灌段(cache_video_for_item)。
    """
    platform_key = str(getattr(classified, "platform", "") or "").lower()
    if not evidence_id or not platform_key or platform_key == "youtube":
        return None, False
    content_url = ""
    if isinstance(metadata, dict):
        content_url = str(metadata.get("content_url") or "").strip()
    content_url = content_url or classified.normalized_url
    if not content_url:
        return None, False
    try:
        from app.domains.media.cache import cache_video_for_item

        # 键对齐 worker:视频缓存按平台原生 ID(IG 短码 / TikTok video id = classified.video_id)存。
        # 此前这里用 evidence_id 查 → 命中不到 worker 已缓存的资产 → 播放器拿不到 cached_url、视频不出。
        # 改用 classified.video_id(短码)兜底 evidence_id。
        video_key = str(getattr(classified, "video_id", "") or "").strip() or str(evidence_id)
        vid = cache_video_for_item(platform_key, video_key, content_url)
        cached_url = str(vid.get("cached_url") or "").strip() or None
        logger.info(
            "video_flow r2 warm evidence_id=%s platform=%s status=%s",
            evidence_id,
            platform_key,
            vid.get("status"),
        )
        return cached_url, True
    except Exception:
        logger.warning("video_flow r2 warm failed evidence_id=%s platform=%s", evidence_id, platform_key)
        return None, True


def _execute_existing_creator_video_flow(
    classified: ClassifiedUrl,
    matches: list[dict[str, Any]],
    video_flow: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    from app.domains.kol.url_deep_crawl_execute import (
        _enqueue_account_dossier_extract_followup,
        _record_deep_crawl_run,
    )

    started = time.monotonic()
    kol_pool_id = int(matches[0]["kol_pool_id"])
    metadata = video_flow.get("video_metadata")
    if not isinstance(metadata, dict):
        metadata = None

    conn = get_conn()
    evidence_result: dict[str, Any] = {}
    enqueue_result: dict[str, Any] = {}
    status = "failed"
    error = ""
    evidence_id: int | None = None
    changed_ids: list[int] = []
    cached_video_url: str | None = None
    video_provider_called = False

    try:
        evidence_result = ensure_video_evidence_from_url(
            kol_pool_id,
            classified.normalized_url,
            metadata,
            dry_run=False,
            conn=conn,
        )
        changed_ids.extend(_fit_changed_ids(evidence_result))
        if not evidence_result.get("ok"):
            status = str(evidence_result.get("status") or "evidence_failed")
        else:
            evidence_id = int(evidence_result.get("evidence_id") or 0) or None
            if not evidence_id:
                status = "evidence_missing_id"
            else:
                enqueue_result = _enqueue_final_v1_video_analysis(
                    conn,
                    kol_pool_id=kol_pool_id,
                    evidence_id=evidence_id,
                    source="kol_url_deep_crawl",
                    batch="url_existing_creator",
                    commit=True,
                    search_session_id=body.get("search_session_id"),
                    search_session_item_id=body.get("search_session_item_id"),
                    parent_job_id=body.get("parent_job_id"),
                    local_evaluation=body.get("local_evaluation") is True,
                )
                changed_ids.extend(_fit_changed_ids(enqueue_result))
                status = str(enqueue_result.get("status") or "enqueue_unknown")
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        error = "video_analysis_enqueue_failed"
        status = "failed"

    cached_video_url, video_provider_called = _cache_video_flow_url(classified, metadata, evidence_id)

    account_dossier_extract_job = None
    if status == "already_analyzed" and body.get("skip_profile_video_followups") is not True:
        account_dossier_extract_job = _enqueue_account_dossier_extract_followup(
            conn,
            kol_pool_id=kol_pool_id,
            source="kol_url_video_flow",
            trigger="video_already_analyzed",
            source_url=classified.normalized_url,
            query_text=f"video account dossier - kol_pool #{kol_pool_id}",
        )
        changed_ids.extend(_fit_changed_ids(account_dossier_extract_job or {}))

    run_status = "ready" if status in _VIDEO_BASE_COMPLETE_STATUSES else "failed"
    run_id = _record_deep_crawl_run(
        conn,
        kol_pool_id=kol_pool_id,
        source_url=classified.normalized_url,
        url_type="video",
        mode=_video_execute_mode(body),
        status=run_status,
        dry_run=False,
        summary={
            "operation": "existing_creator_video_analysis",
            "status": status,
            "error": error or None,
            "creator_identity": video_flow.get("creator_identity"),
            "video_metadata": video_flow.get("video_metadata"),
            "evidence_result": _compact_video_evidence_result(evidence_result),
            "enqueue_result": _compact_enqueue_result(enqueue_result),
            "account_dossier_extract_job": account_dossier_extract_job,
            "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    )

    worker_touched = status == "queued"
    business_tables_written = bool(run_id) or bool(evidence_result.get("status") in {"created", "reused"}) or worker_touched
    return {
        **video_flow,
        "status": status,
        "operation": "existing_creator_video_analysis",
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "evidence_result": _compact_video_evidence_result(evidence_result),
        "enqueue_result": _compact_enqueue_result(enqueue_result),
        "ai_analysis": _video_ai_analysis(enqueue_result),
        "account_dossier_extract_job": account_dossier_extract_job,
        "run_id": run_id,
        "run_status": run_status,
        "error": error or None,
        "business_tables_written": business_tables_written,
        "worker_touched": worker_touched or bool(account_dossier_extract_job and account_dossier_extract_job.get("status") == "queued"),
        "write_db": business_tables_written,
        "writes": ["vkpi_kol_video_evidence", "apify_jobs", "vkpi_kol_url_deep_crawl_runs"],
        "cached_video_url": cached_video_url,
        "provider_calls_performed": video_provider_called,
        "llm_calls_performed": False,
        "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
        "viltrox_fit_score_untouched": not changed_ids,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _execute_new_creator_video_flow(
    classified: ClassifiedUrl,
    video_flow: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    from app.domains.kol.url_deep_crawl_execute import (
        _crawl_profile_basics,
        _enqueue_account_dossier_extract_followup,
        _profile_classified_from_video_flow,
        _profile_data_for_new_video_creator,
        _profile_incremental_state,
        _profile_target,
        _record_deep_crawl_run,
    )

    started = time.monotonic()
    max_posts = _max_posts(body)
    profile_classified = _profile_classified_from_video_flow(classified, video_flow)
    conn = get_conn()
    crawl: dict[str, Any] = {}
    profile_data: dict[str, Any] = {}
    write_result: dict[str, Any] = {}
    evidence_result: dict[str, Any] = {}
    enqueue_result: dict[str, Any] = {}
    representative_video_analysis: dict[str, Any] = {}
    history_video_evidence: dict[str, Any] = {}
    kol_pool_id: int | None = None
    evidence_id: int | None = None
    status = "failed"
    error = ""
    changed_ids: list[int] = []
    cached_video_url: str | None = None
    video_provider_called = False

    if not profile_classified:
        run_id = _record_deep_crawl_run(
            conn,
            kol_pool_id=None,
            source_url=classified.normalized_url,
            url_type="video",
            mode=_video_execute_mode(body),
            status="failed",
            dry_run=False,
            summary={
                "operation": "new_creator_video_analysis",
                "status": "creator_unresolved",
                "reason": "resolved video creator lacks a usable profile identity",
                "creator_identity": video_flow.get("creator_identity"),
                "video_metadata": video_flow.get("video_metadata"),
                "viltrox_fit_score_changed_ids": [],
            },
        )
        return {
            **video_flow,
            "status": "creator_unresolved",
            "operation": "new_creator_video_analysis",
            "message": "video creator could not be converted into a profile identity; refused to create an anonymous KOL.",
            "run_id": run_id,
            "business_tables_written": bool(run_id),
            "worker_touched": False,
            "llm_calls_performed": False,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    # 官方自有账号兜底闸:任何抵达建档口的路径都不许把官方渠道写进 KOL 池。
    # (主判定在 video_url_resolver 的 worker 链;这里防旧调用路径/回放绕过。)
    from app.domains.kol.video_url_resolver import find_official_channel_match

    official = find_official_channel_match(
        video_flow.get("creator_identity") if isinstance(video_flow.get("creator_identity"), dict) else {}
    )
    if official:
        run_id = _record_deep_crawl_run(
            conn,
            kol_pool_id=None,
            source_url=classified.normalized_url,
            url_type="video",
            mode=_video_execute_mode(body),
            status="ready",
            dry_run=False,
            summary={
                "operation": "new_creator_video_analysis",
                "status": "official_channel_video",
                "reason": "creator is a company-owned official channel; enrollment and analysis skipped by design",
                "official_channel": official,
                "creator_identity": video_flow.get("creator_identity"),
                "video_metadata": video_flow.get("video_metadata"),
                "viltrox_fit_score_changed_ids": [],
            },
        )
        return {
            **video_flow,
            "status": "official_channel_video",
            "operation": "new_creator_video_analysis",
            "message": "官方自有账号的视频：不建人选档案，也不做深度分析，仅保留视频基础数据。",
            "official_channel": official,
            "ai_analysis": {
                "state": "skipped",
                "reason": "official_channel_video",
                "provider_calls_allowed": False,
            },
            "run_id": run_id,
            "business_tables_written": bool(run_id),
            "worker_touched": False,
            "llm_calls_performed": False,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    try:
        crawl = _crawl_profile_basics(profile_classified, target=_profile_target(profile_classified), max_posts=max_posts)
        if str(crawl.get("status") or "").lower() not in {"ok", "synced"}:
            status = "profile_crawl_failed"
            error = "profile_crawl_not_ready"
        else:
            profile_data = _profile_data_for_new_video_creator(
                profile_classified,
                crawl,
                video_flow,
                max_posts=max_posts,
            )
            write_result = write_kol_profile_basics(None, profile_data, dry_run=False, conn=conn)
            changed_ids.extend(_fit_changed_ids(write_result))
            kol_pool_id = int(write_result.get("kol_pool_id") or 0) or None
            if not kol_pool_id:
                status = "kol_create_missing_id"
            else:
                metadata = video_flow.get("video_metadata")
                if not isinstance(metadata, dict):
                    metadata = None
                evidence_result = ensure_video_evidence_from_url(
                    kol_pool_id,
                    classified.normalized_url,
                    metadata,
                    dry_run=False,
                    conn=conn,
                )
                changed_ids.extend(_fit_changed_ids(evidence_result))
                if not evidence_result.get("ok"):
                    status = str(evidence_result.get("status") or "evidence_failed")
                else:
                    evidence_id = int(evidence_result.get("evidence_id") or 0) or None
                    if not evidence_id:
                        status = "evidence_missing_id"
                    else:
                        enqueue_result = _enqueue_final_v1_video_analysis(
                            conn,
                            kol_pool_id=kol_pool_id,
                            evidence_id=evidence_id,
                            source="kol_url_deep_crawl",
                            batch="url_new_creator",
                            commit=True,
                            search_session_id=body.get("search_session_id"),
                            search_session_item_id=body.get("search_session_item_id"),
                            parent_job_id=body.get("parent_job_id"),
                            local_evaluation=body.get("local_evaluation") is True,
                        )
                        changed_ids.extend(_fit_changed_ids(enqueue_result))
                        status = str(enqueue_result.get("status") or "enqueue_unknown")
                if kol_pool_id and body.get("skip_profile_video_followups") is not True:
                    onboarding_body = {
                        **body,
                        "mode": "account_deep",
                        "representative_video_limit": body.get("representative_video_limit") or 3,
                        "history_video_limit": body.get("history_video_limit") or max_posts,
                        "materialize_history_videos": True,
                        "exclude_video_urls": [classified.normalized_url],
                    }
                    incremental_state = _profile_incremental_state(None)
                    representative_video_analysis = _execute_profile_representative_video_analysis(
                        conn,
                        classified=profile_classified,
                        kol_pool_id=kol_pool_id,
                        crawl=crawl,
                        body=onboarding_body,
                        incremental_state=incremental_state,
                    )
                    history_video_evidence = _execute_profile_history_video_evidence(
                        conn,
                        classified=profile_classified,
                        kol_pool_id=kol_pool_id,
                        crawl=crawl,
                        body=onboarding_body,
                        incremental_state=incremental_state,
                    )
                    changed_ids.extend(_fit_changed_ids(representative_video_analysis))
                    changed_ids.extend(_fit_changed_ids(history_video_evidence))
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        error = "video_creator_flow_failed"
        status = "failed"

    cached_video_url, video_provider_called = _cache_video_flow_url(
        classified, video_flow.get("video_metadata") if isinstance(video_flow.get("video_metadata"), dict) else None, evidence_id
    )

    account_dossier_extract_job = None
    if (
        status == "already_analyzed"
        and kol_pool_id
        and body.get("skip_profile_video_followups") is not True
    ):
        account_dossier_extract_job = _enqueue_account_dossier_extract_followup(
            conn,
            kol_pool_id=kol_pool_id,
            source="kol_url_video_new_creator_flow",
            trigger="video_already_analyzed",
            source_url=classified.normalized_url,
            query_text=f"new creator account dossier - kol_pool #{kol_pool_id}",
        )
        changed_ids.extend(_fit_changed_ids(account_dossier_extract_job or {}))

    representative_worker_touched = bool(representative_video_analysis.get("worker_touched"))
    run_status = "ready" if status in _VIDEO_BASE_COMPLETE_STATUSES or representative_worker_touched else "failed"
    run_id = _record_deep_crawl_run(
        conn,
        kol_pool_id=kol_pool_id,
        source_url=classified.normalized_url,
        url_type="video",
        mode=_video_execute_mode(body),
        status=run_status,
        dry_run=False,
        summary={
            "operation": "new_creator_video_analysis",
            "status": status,
            "error": error or None,
            "creator_identity": video_flow.get("creator_identity"),
            "profile_url": profile_classified.normalized_url,
            "profile_crawl_status": crawl.get("status"),
            "profile_provider_source": crawl.get("provider_source"),
            "profile_write_result": _compact_profile_write_result(write_result),
            "profile_data": _public_profile_data(profile_data),
            "video_metadata": video_flow.get("video_metadata"),
            "evidence_result": _compact_video_evidence_result(evidence_result),
            "enqueue_result": _compact_enqueue_result(enqueue_result),
            "representative_video_analysis": representative_video_analysis,
            "history_video_evidence": history_video_evidence,
            "account_dossier_extract_job": account_dossier_extract_job,
            "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    )

    worker_touched = status == "queued"
    history_written = bool((history_video_evidence.get("materialized") or 0) or (history_video_evidence.get("reused") or 0))
    business_tables_written = (
        bool(kol_pool_id)
        or bool(evidence_result.get("status") in {"created", "reused"})
        or worker_touched
        or representative_worker_touched
        or history_written
        or bool(run_id)
    )
    return {
        **video_flow,
        "status": status,
        "operation": "new_creator_video_analysis",
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "profile_flow": {
            "status": "ready" if kol_pool_id else status,
            "operation": "insert",
            "kol_pool_id": kol_pool_id,
            "target": _profile_target(profile_classified),
            "profile_data": _public_profile_data(profile_data),
            "write_result": _compact_profile_write_result(write_result),
            "crawl_status": crawl.get("status"),
            "provider_source": crawl.get("provider_source"),
            "viltrox_fit_score_changed_ids": _fit_changed_ids(write_result),
            "viltrox_fit_score_untouched": not _fit_changed_ids(write_result),
        },
        "evidence_result": _compact_video_evidence_result(evidence_result),
        "enqueue_result": _compact_enqueue_result(enqueue_result),
        "ai_analysis": _video_ai_analysis(enqueue_result),
        "representative_video_analysis": representative_video_analysis,
        "history_video_evidence": history_video_evidence,
        "account_dossier_extract_job": account_dossier_extract_job,
        "run_id": run_id,
        "run_status": run_status,
        "error": error or None,
        "crawl_performed": bool(crawl),
        "business_tables_written": business_tables_written,
        "worker_touched": worker_touched
        or representative_worker_touched
        or bool(account_dossier_extract_job and account_dossier_extract_job.get("status") == "queued"),
        "write_db": business_tables_written,
        "writes": ["vkpi_kol_pool", "vkpi_kol_video_evidence", "apify_jobs", "vkpi_kol_url_deep_crawl_runs"],
        "cached_video_url": cached_video_url,
        "provider_calls_performed": video_provider_called,
        "llm_calls_performed": False,
        "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
        "viltrox_fit_score_untouched": not changed_ids,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
