"""URL deep-crawl execute + profile-build cluster (从 url_deep_crawl.py 抽出,行为不变).

包含 execute 路径(profile / 新老创作者 video / 代表作 / 历史视频)与其依赖的
profile 抓取 / 组装 / 落库 / run 记录 helper。函数体逐字搬运,调用点由 url_deep_crawl
re-export 兜住(behavior-preserving move)。

循环导入纪律:本模块绝不在顶层 import url_deep_crawl;唯一回指原文件的运行时引用
(_classified_from_creator_identity)在函数体内 lazy import。ClassifiedUrl 仅作类型注解,
from __future__ annotations 已字符串化,运行时无需导入(TYPE_CHECKING 下声明)。

红线:LLM 绝不写 viltrox_fit_score;本模块零 fit 写,只透传上游 *_changed_ids。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.industry.snapshot_kpis import calculate_kpis
from app.domains.kol.pool_common import (
    _bio,
    _content_items_from_payload,
    _first_present,
    _int_or_none,
    _json,
    _looks_like_content_item,
    _profile_item,
    _profile_stats,
    _profile_url,
    _table_columns,
    _thumb_url,
)
from app.domains.kol.profile_basics import write_kol_profile_basics
from app.domains.kol.url_deep_crawl_helpers import (
    _compact_enqueue_result,
    _compact_profile_write_result,
    _compact_video_evidence_result,
    _fit_changed_ids,
    _has_matchable_creator_identity,
    _latest_video_date,
    _load_json,
    _max_posts,
    _parse_date,
    _profile_exclude_video_urls,
    _profile_history_video_limit,
    _profile_representative_video_limit,
    _profile_should_enqueue_representative_videos,
    _profile_should_materialize_history_videos,
    _public_profile_data,
    _public_video_metadata,
    _raw_profile_backfilled_at,
    _video_execute_mode,
    _video_metadata_date,
)
from app.domains.kol.url_deep_crawl_execute_profile_videos import (  # noqa: F401  re-export 兜调用点(含私有名)
    _execute_profile_history_video_evidence,
    _execute_profile_representative_video_analysis,
)
from app.domains.kol.url_deep_crawl_video_meta import (
    _filter_incremental_profile_videos,
    _profile_representative_video_metadata,
)
from app.domains.kol.video_analysis_enqueue import _enqueue_final_v1_video_analysis
from app.domains.kol.video_evidence import ensure_video_evidence_from_url
from app.platform.industry_crawlers.instagram_crawler import InstagramCrawler
from app.platform.industry_crawlers.tiktok_crawler import TikTokCrawler
from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

if TYPE_CHECKING:
    from app.domains.kol.url_deep_crawl import ClassifiedUrl

logger = get_logger("viltrox.domains.kol.url_deep_crawl_execute")


def _execute_profile_flow(
    classified: ClassifiedUrl,
    matches: list[dict[str, Any]],
    body: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    target = _profile_target(classified)
    max_posts = _max_posts(body)
    kol_pool_id = int(matches[0]["kol_pool_id"]) if len(matches) == 1 else None
    operation = "update" if kol_pool_id else "insert"
    incremental_state = _profile_incremental_state(kol_pool_id, force_full=_profile_force_full_history(body))
    conn = get_conn()

    crawl = _crawl_profile_basics(classified, target=target, max_posts=max_posts)
    if str(crawl.get("status") or "").lower() not in {"ok", "synced"}:
        run_id = _record_deep_crawl_run(
            conn,
            kol_pool_id=kol_pool_id,
            source_url=classified.normalized_url,
            url_type="profile",
            mode=str(body.get("mode") or "profile_only"),
            status="failed",
            dry_run=False,
            summary={
                "reason": "profile_crawl_not_ready",
                "crawl_status": crawl.get("status"),
                "elapsed_ms": crawl.get("elapsed_ms"),
                "provider_source": crawl.get("provider_source"),
            },
        )
        return {
            "status": "crawl_failed",
            "operation": operation,
            "kol_pool_id": kol_pool_id,
            "target": target,
            "crawl_status": crawl.get("status"),
            "provider_source": crawl.get("provider_source"),
            "run_id": run_id,
            "crawl_performed": True,
            "business_tables_written": bool(run_id),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    profile_data = _profile_data_from_crawl(
        classified,
        crawl,
        existing_match=matches[0] if matches else {},
        max_posts=max_posts,
    )
    write_result = write_kol_profile_basics(kol_pool_id, profile_data, dry_run=False, conn=conn)
    written_kol_pool_id = int(write_result.get("kol_pool_id") or kol_pool_id or 0) or None
    representative_video_analysis = _execute_profile_representative_video_analysis(
        conn,
        classified=classified,
        kol_pool_id=written_kol_pool_id,
        crawl=crawl,
        body=body,
        incremental_state=incremental_state,
    )
    history_video_evidence = _execute_profile_history_video_evidence(
        conn,
        classified=classified,
        kol_pool_id=written_kol_pool_id,
        crawl=crawl,
        body=body,
        incremental_state=incremental_state,
    )
    worker_touched = bool(representative_video_analysis.get("worker_touched"))
    account_dossier_extract_job = None
    if written_kol_pool_id and not worker_touched:
        account_dossier_extract_job = _enqueue_account_dossier_extract_followup(
            conn,
            kol_pool_id=written_kol_pool_id,
            source="kol_url_profile_flow",
            trigger=str(representative_video_analysis.get("status") or "profile_ready_no_video_job"),
            source_url=classified.normalized_url,
            query_text=f"profile account dossier - {target}",
        )
    changed_ids = sorted(
        set(
            _fit_changed_ids(write_result)
            + _fit_changed_ids(representative_video_analysis)
            + _fit_changed_ids(history_video_evidence)
            + _fit_changed_ids(account_dossier_extract_job or {})
        )
    )
    run_id = _record_deep_crawl_run(
        conn,
        kol_pool_id=written_kol_pool_id,
        source_url=classified.normalized_url,
        url_type="profile",
        mode=str(body.get("mode") or "profile_only"),
        status="ready",
        dry_run=False,
        summary={
            "operation": operation,
            "target": target,
            "crawl_status": crawl.get("status"),
            "provider_source": crawl.get("provider_source"),
            "elapsed_ms": crawl.get("elapsed_ms"),
            "fields_written": write_result.get("fields_written"),
            "viltrox_fit_score_changed_ids": write_result.get("viltrox_fit_score_changed_ids"),
            "representative_video_analysis": representative_video_analysis,
            "history_video_evidence": history_video_evidence,
            "account_dossier_extract_job": account_dossier_extract_job,
            "incremental_state": incremental_state,
        },
    )
    return {
        "status": "ready",
        "operation": operation,
        "kol_pool_id": written_kol_pool_id,
        "target": target,
        "max_posts": max_posts,
        "profile_data": _public_profile_data(profile_data),
        "write_result": {
            "fields_written": write_result.get("fields_written"),
            "ignored_fields": write_result.get("ignored_fields"),
            "missing_columns": write_result.get("missing_columns"),
            "viltrox_fit_score_changed_ids": write_result.get("viltrox_fit_score_changed_ids"),
            "viltrox_fit_score_untouched": write_result.get("viltrox_fit_score_untouched"),
        },
        "representative_video_analysis": representative_video_analysis,
        "history_video_evidence": history_video_evidence,
        "account_dossier_extract_job": account_dossier_extract_job,
        "run_id": run_id,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "crawl_performed": True,
        "business_tables_written": True,
        "worker_touched": worker_touched or bool(account_dossier_extract_job and account_dossier_extract_job.get("status") == "queued"),
        "provider_source": crawl.get("provider_source"),
        "crawl_status": crawl.get("status"),
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
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

        vid = cache_video_for_item(platform_key, str(evidence_id), content_url)
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
                )
                changed_ids.extend(_fit_changed_ids(enqueue_result))
                status = str(enqueue_result.get("status") or "enqueue_unknown")
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        error = str(exc)[:500]
        status = "failed"

    cached_video_url, video_provider_called = _cache_video_flow_url(classified, metadata, evidence_id)

    account_dossier_extract_job = None
    if status == "already_analyzed":
        account_dossier_extract_job = _enqueue_account_dossier_extract_followup(
            conn,
            kol_pool_id=kol_pool_id,
            source="kol_url_video_flow",
            trigger="video_already_analyzed",
            source_url=classified.normalized_url,
            query_text=f"video account dossier - kol_pool #{kol_pool_id}",
        )
        changed_ids.extend(_fit_changed_ids(account_dossier_extract_job or {}))

    run_status = "ready" if status in {"queued", "already_queued", "already_analyzed"} else "failed"
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
                        )
                        changed_ids.extend(_fit_changed_ids(enqueue_result))
                        status = str(enqueue_result.get("status") or "enqueue_unknown")
                if kol_pool_id:
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
            pass
        error = str(exc)[:500]
        status = "failed"

    cached_video_url, video_provider_called = _cache_video_flow_url(
        classified, video_flow.get("video_metadata") if isinstance(video_flow.get("video_metadata"), dict) else None, evidence_id
    )

    account_dossier_extract_job = None
    if status == "already_analyzed" and kol_pool_id:
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
    run_status = "ready" if status in {"queued", "already_queued", "already_analyzed"} or representative_worker_touched else "failed"
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


def _profile_force_full_history(body: dict[str, Any]) -> bool:
    """强制全量历史:绕过 last_video_at 增量截断,把已爬过 KOL 的全部视频重新 materialize。
    用于「该用户全部视频都分析」(account_deep 重跑),不改任何评分字段。"""
    return bool((body or {}).get("force_full_history") or (body or {}).get("ignore_incremental"))


def _profile_incremental_state(kol_pool_id: int | None, *, force_full: bool = False) -> dict[str, Any]:
    if force_full:
        return {
            "enabled": True,
            "kol_pool_id": int(kol_pool_id) if kol_pool_id else None,
            "last_video_at": "",
            "profile_backfilled_at": "",
            "mode": "force_full_history",
        }
    if not kol_pool_id:
        return {
            "enabled": True,
            "kol_pool_id": None,
            "last_video_at": "",
            "profile_backfilled_at": "",
            "mode": "full_first_profile_crawl",
        }
    conn = get_conn()
    columns = _table_columns(conn, "vkpi_kol_pool")
    selected = ["id"]
    for column in ("last_video_at", "raw_platform_data"):
        if column in columns:
            selected.append(column)
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        return {
            "enabled": True,
            "kol_pool_id": int(kol_pool_id),
            "last_video_at": "",
            "profile_backfilled_at": "",
            "mode": "missing_existing_profile",
        }
    row_dict = dict(row)
    raw_payload = _load_json(row_dict.get("raw_platform_data"))
    profile_backfilled_at = _raw_profile_backfilled_at(raw_payload)
    last_video_at = _parse_date(row_dict.get("last_video_at")) or ""
    return {
        "enabled": True,
        "kol_pool_id": int(kol_pool_id),
        "last_video_at": last_video_at,
        "profile_backfilled_at": profile_backfilled_at,
        "mode": "incremental_after_last_video" if last_video_at else "full_no_last_video_at",
    }


def _video_creator_resolved(video_flow: dict[str, Any]) -> bool:
    identity = video_flow.get("creator_identity") if isinstance(video_flow, dict) else {}
    return isinstance(identity, dict) and _has_matchable_creator_identity(identity)


def _profile_classified_from_video_flow(
    classified: ClassifiedUrl,
    video_flow: dict[str, Any],
) -> ClassifiedUrl | None:
    # lazy import 避免与 url_deep_crawl 循环导入(_classified_from_creator_identity 留在原文件)。
    from app.domains.kol.url_deep_crawl import _classified_from_creator_identity

    identity = video_flow.get("creator_identity") if isinstance(video_flow, dict) else {}
    if not isinstance(identity, dict):
        return None
    return _classified_from_creator_identity(classified, identity)


def _profile_data_for_new_video_creator(
    profile_classified: ClassifiedUrl,
    crawl: dict[str, Any],
    video_flow: dict[str, Any],
    *,
    max_posts: int,
) -> dict[str, Any]:
    identity = video_flow.get("creator_identity") if isinstance(video_flow.get("creator_identity"), dict) else {}
    metadata = video_flow.get("video_metadata") if isinstance(video_flow.get("video_metadata"), dict) else {}
    profile_data = _profile_data_from_crawl(profile_classified, crawl, existing_match={}, max_posts=max_posts)
    now = datetime.now(timezone.utc).isoformat()

    profile_data["platform"] = profile_classified.platform
    profile_data["handle"] = profile_classified.handle or profile_classified.channel_id
    profile_data["profile_url"] = profile_data.get("profile_url") or profile_classified.normalized_url
    if not profile_data.get("avatar_url") and identity.get("avatar_url"):
        profile_data["avatar_url"] = identity.get("avatar_url")
    if not profile_data.get("followers") and identity.get("followers") is not None:
        profile_data["followers"] = identity.get("followers")
    if not profile_data.get("bio") and identity.get("bio"):
        profile_data["bio"] = identity.get("bio")
    profile_data["profile_backfilled_at"] = now
    if not profile_data.get("last_video_at"):
        profile_data["last_video_at"] = _video_metadata_date(metadata)

    raw_payload = _load_json(profile_data.get("raw_platform_data"))
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    raw_payload["video_url_creator_bootstrap"] = {
        "method": "url_deep_crawl_video_new_creator_v1",
        "source_url": metadata.get("content_url") or "",
        "video_title": metadata.get("title") or "",
        "video_id": metadata.get("video_id") or metadata.get("id") or "",
        "creator_identity": identity,
        "profile_backfilled_at": now,
    }
    profile_data["raw_platform_data"] = _json(raw_payload)
    return profile_data


def _enqueue_account_dossier_extract_followup(
    conn: Any,
    *,
    kol_pool_id: int | None,
    source: str,
    trigger: str,
    source_url: str,
    query_text: str = "",
) -> dict[str, Any] | None:
    if not kol_pool_id:
        return None
    existing = conn.execute(
        """
        SELECT id, job_type, status, created_at, updated_at
        FROM apify_jobs
        WHERE job_type='account_dossier_extract'
          AND status IN ('queued', 'running')
          AND payload->>'target_type'='kol_pool'
          AND payload->>'target_id'=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (str(int(kol_pool_id)),),
    ).fetchone()
    if existing:
        return {
            "status": "already_queued" if existing["status"] == "queued" else "already_running",
            "job": {
                "id": existing["id"],
                "job_type": existing["job_type"],
                "status": existing["status"],
                "created_at": existing["created_at"],
                "updated_at": existing["updated_at"],
            },
            "kol_pool_id": int(kol_pool_id),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }
    payload = {
        "target_type": "kol_pool",
        "target_id": str(int(kol_pool_id)),
        "derive_method": "kol_account_dossier_extract_v1",
        "analysis_kind": "profile_llm",
        "source": source,
        "trigger": trigger,
        "source_url": source_url,
        "query_text": query_text or f"account dossier - kol_pool #{int(kol_pool_id)}",
    }
    row = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES ('account_dossier_extract', ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, created_at, updated_at
        """,
        (json.dumps(payload, ensure_ascii=False, default=str),),
    ).fetchone()
    try:
        conn.commit()
    except Exception:
        pass
    return {
        "status": "queued",
        "job": dict(row) if row else None,
        "kol_pool_id": int(kol_pool_id),
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


def _crawl_profile_basics(classified: ClassifiedUrl, *, target: str, max_posts: int) -> dict[str, Any]:
    crawler = _crawler_for(classified.platform)
    started = time.monotonic()
    profile_payload: dict[str, Any] = {}
    videos_payload: dict[str, Any] = {}
    videos_items: list[dict[str, Any]] = []

    if classified.platform == "youtube":
        profile_payload = crawler.crawl_channel_profile(target, channel_id="", max_posts=max_posts)
        profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else []
        profile = profile_items[0] if isinstance(profile_items, list) and profile_items and isinstance(profile_items[0], dict) else {}
        channel_id = str(profile.get("id") or classified.channel_id or "")
        if channel_id and hasattr(crawler, "crawl_channel_videos"):
            videos_payload = crawler.crawl_channel_videos(channel_id, max_results=max_posts)
            videos = videos_payload.get("items") if isinstance(videos_payload, dict) else []
            videos_items = [video for video in videos if isinstance(video, dict)] if isinstance(videos, list) else []
        fallback_videos = profile_payload.get("videos") if isinstance(profile_payload, dict) else None
        if not videos_items and isinstance(fallback_videos, list):
            videos_items = [video for video in fallback_videos if isinstance(video, dict)]
    else:
        profile_payload = crawler.crawl_channel_profile(target, channel_id="", max_posts=max_posts)
        payload_items = _content_items_from_payload(profile_payload) if isinstance(profile_payload, dict) else []
        profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else []
        if payload_items and _looks_like_content_item(payload_items[0]):
            videos_items = payload_items
        elif isinstance(profile_items, list):
            videos_items = [item for item in profile_items if isinstance(item, dict) and _looks_like_content_item(item)]
        if not videos_items and isinstance(profile_items, list) and profile_items and isinstance(profile_items[0], dict):
            # IG 断点(2026-06-12 审计):instagram-profile-scraper 的 dataset item 是 profile 对象,
            # 帖子嵌在 profile.latestPosts 里——此前没人下钻这层,IG 账号分析永远 no_history_video_url。
            # 下钻口径对齐 industry/snapshot_collector.py。
            profile_obj = profile_items[0]
            for nested_key in ("latestPosts", "posts", "videos"):
                nested = profile_obj.get(nested_key)
                if isinstance(nested, list) and nested:
                    videos_items = [item for item in nested if isinstance(item, dict) and _looks_like_content_item(item)]
                    if videos_items:
                        break

    provider_source = str((profile_payload or {}).get("provider_source") or (videos_payload or {}).get("provider_source") or "")
    status = str(
        (profile_payload or {}).get("sync_status")
        or (profile_payload or {}).get("provider_status")
        or (videos_payload or {}).get("sync_status")
        or (videos_payload or {}).get("provider_status")
        or "unknown"
    )
    return {
        "profile_payload": profile_payload if isinstance(profile_payload, dict) else {},
        "videos_payload": videos_payload if isinstance(videos_payload, dict) else {},
        "videos_items": videos_items,
        "status": status,
        "provider_source": provider_source,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _profile_data_from_crawl(
    classified: ClassifiedUrl,
    crawl: dict[str, Any],
    *,
    existing_match: dict[str, Any],
    max_posts: int,
) -> dict[str, Any]:
    profile_payload = crawl.get("profile_payload") if isinstance(crawl.get("profile_payload"), dict) else {}
    videos_payload = crawl.get("videos_payload") if isinstance(crawl.get("videos_payload"), dict) else {}
    videos_items = crawl.get("videos_items") if isinstance(crawl.get("videos_items"), list) else []
    handle = classified.handle or classified.channel_id or str(existing_match.get("handle") or "")
    raw_data = {
        "source": f"{classified.platform}_url_deep_crawl_profile",
        "profile": profile_payload,
        "videos": videos_items,
        "kpi_status": crawl.get("status") or "unknown",
        "source_ref": classified.normalized_url,
        "profile_backfill": {
            "method": "url_deep_crawl_profile_v1",
            "max_posts": int(max_posts),
            "target": _profile_target(classified),
            "provider_source": crawl.get("provider_source") or "",
            "elapsed_ms": crawl.get("elapsed_ms"),
        },
    }
    if classified.platform == "youtube":
        source = str(profile_payload.get("provider_source") or videos_payload.get("provider_source") or "").strip()
        raw_data["source"] = "youtube_url_deep_crawl_profile_apify" if source == "apify" else "youtube_url_deep_crawl_profile_api"
        raw_data["youtube_provider_source"] = source or "youtube_api"

    kpis = calculate_kpis(raw_data)
    profile = _profile_item(raw_data)
    stats = _profile_stats(profile)
    return {
        "platform": classified.platform,
        "handle": handle,
        "profile_url": _profile_url(classified.platform, profile, handle, classified.normalized_url),
        "avatar_url": _thumb_url(profile),
        "bio": _bio(profile),
        "followers": _int_or_none(_first_present(kpis.get("followers"), stats.get("followers"), stats.get("followersCount"))),
        "posts_count": _int_or_none(_first_present(kpis.get("posts"), stats.get("posts"), stats.get("postsCount"))),
        "last_video_at": _latest_video_date([item for item in videos_items if isinstance(item, dict)]),
        "raw_platform_data": _json(raw_data),
    }


def _record_deep_crawl_run(
    conn: Any,
    *,
    kol_pool_id: int | None,
    source_url: str,
    url_type: str,
    mode: str,
    status: str,
    dry_run: bool,
    summary: dict[str, Any],
) -> int | None:
    columns = _table_columns(conn, "vkpi_kol_url_deep_crawl_runs")
    if "id" not in columns:
        raise RuntimeError("vkpi_kol_url_deep_crawl_runs table is missing; apply migration 102")
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_url_deep_crawl_runs
          (kol_pool_id, source_url, url_type, mode, status, dry_run, result_summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?::jsonb)
        RETURNING id
        """,
        (
            int(kol_pool_id) if kol_pool_id else None,
            source_url,
            url_type,
            mode if mode in {"auto", "profile_only", "video_deep", "dry_run"} else "profile_only",
            status,
            bool(dry_run),
            json.dumps(summary or {}, ensure_ascii=False, default=str),
        ),
    ).fetchone()
    try:
        conn.commit()
    except Exception:
        pass
    return int(row["id"]) if row and row["id"] is not None else None


def _profile_target(classified: ClassifiedUrl) -> str:
    if classified.url_type == "profile" and classified.normalized_url:
        return classified.normalized_url
    return classified.channel_id or classified.handle


def _identity_profile_data(classified: ClassifiedUrl) -> dict[str, Any]:
    return {
        "platform": classified.platform,
        "handle": classified.handle or classified.channel_id,
        "profile_url": classified.normalized_url if classified.url_type == "profile" else "",
        "raw_platform_data": {
            "source": "url_deep_crawl_profile_identity_dry_run",
            "profile_backfill": {
                "method": "url_deep_crawl_profile_v1",
                "source_url": classified.normalized_url,
            },
        },
    }


def _crawler_for(platform: str) -> Any:
    if platform == "youtube":
        return YouTubeCrawler(run_timeout_seconds=240)
    if platform == "instagram":
        return InstagramCrawler(run_timeout_seconds=180)
    if platform == "tiktok":
        return TikTokCrawler(run_timeout_seconds=240)
    raise ValueError(f"unsupported platform: {platform}")
