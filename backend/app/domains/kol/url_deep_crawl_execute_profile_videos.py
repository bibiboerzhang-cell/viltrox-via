"""Profile representative / history 视频证据落库簇(从 url_deep_crawl_execute.py 抽出,行为不变).

两个函数整体搬运,函数体逐字不变:
- _execute_profile_representative_video_analysis:profile 代表作 → evidence + 入队 final_v1。
- _execute_profile_history_video_evidence:profile 历史视频 → evidence materialize(不入队分析)。

调用点由 url_deep_crawl_execute re-export 兜住(behavior-preserving move)。

循环导入纪律:本模块绝不在顶层 import url_deep_crawl / url_deep_crawl_execute;
ClassifiedUrl 仅作类型注解,from __future__ annotations 已字符串化,运行时无需导入。

红线:LLM 绝不写 viltrox_fit_score;本模块零 fit 写,只透传上游 *_changed_ids。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.domains.kol.url_deep_crawl_helpers import (
    _compact_enqueue_result,
    _compact_video_evidence_result,
    _fit_changed_ids,
    _max_posts,
    _profile_exclude_video_urls,
    _profile_history_video_limit,
    _profile_representative_video_limit,
    _profile_should_enqueue_representative_videos,
    _profile_should_materialize_history_videos,
    _public_video_metadata,
)
from app.domains.kol.url_deep_crawl_video_meta import (
    _filter_incremental_profile_videos,
    _profile_representative_video_metadata,
)
from app.domains.kol.video_analysis_enqueue import _enqueue_final_v1_video_analysis
from app.domains.kol.video_evidence import ensure_video_evidence_from_url

from app.core.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from app.domains.kol.url_deep_crawl import ClassifiedUrl


def _representative_ai_analysis(items: list[dict[str, Any]]) -> dict[str, Any]:
    analyses = [
        (item.get("enqueue_result") or {}).get("ai_analysis")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("enqueue_result"), dict)
    ]
    analyses = [value for value in analyses if isinstance(value, dict)]
    if not analyses:
        return {
            "state": "not_requested",
            "reason": "no_eligible_representative_video",
            "gate_reason": "",
            "model_readiness_status": "not_ready",
            "provider_calls_allowed": False,
        }
    states = {str(value.get("state") or "not_requested") for value in analyses}
    disabled = [value for value in analyses if value.get("reason") == "ai_disabled"]
    if "queued" in states:
        state, reason = "queued", "analysis_queued"
    elif "ready" in states:
        state, reason = "ready", "cached_analysis"
    elif disabled:
        state, reason = "not_requested", "ai_disabled"
    else:
        state, reason = "not_requested", str(analyses[0].get("reason") or "not_requested")
    source = disabled[0] if disabled else analyses[0]
    return {
        "state": state,
        "reason": reason,
        "gate_reason": str(source.get("gate_reason") or ""),
        "model_readiness_status": str(source.get("model_readiness_status") or "not_ready"),
        "provider_calls_allowed": any(bool(value.get("provider_calls_allowed")) for value in analyses),
        "item_count": len(analyses),
        "not_requested_count": sum(1 for value in analyses if value.get("state") == "not_requested"),
    }


def _execute_profile_representative_video_analysis(
    conn: Any,
    *,
    classified: ClassifiedUrl,
    kol_pool_id: int | None,
    crawl: dict[str, Any],
    body: dict[str, Any],
    incremental_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    incremental_state = incremental_state if isinstance(incremental_state, dict) else {}
    if not _profile_should_enqueue_representative_videos(body):
        return {
            "enabled": False,
            "status": "disabled_profile_only",
            "items": [],
            "queued": 0,
            "skipped": 0,
            "errors": 0,
            "worker_touched": False,
            "ai_analysis": {
                "state": "not_requested",
                "reason": "profile_video_analysis_not_requested",
                "gate_reason": "",
                "model_readiness_status": "not_ready",
                "provider_calls_allowed": False,
            },
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }
    if not kol_pool_id:
        return {
            "enabled": True,
            "status": "missing_kol_pool_id",
            "items": [],
            "queued": 0,
            "skipped": 0,
            "errors": 1,
            "worker_touched": False,
            "ai_analysis": {
                "state": "not_requested",
                "reason": "missing_kol_pool_id",
                "gate_reason": "",
                "model_readiness_status": "not_ready",
                "provider_calls_allowed": False,
            },
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }
    limit = _profile_representative_video_limit(body)
    all_videos = _profile_representative_video_metadata(
        classified,
        crawl,
        limit=max(limit, _max_posts(body)),
        exclude_video_urls=_profile_exclude_video_urls(body),
    )
    videos, skipped_by_incremental = _filter_incremental_profile_videos(all_videos, incremental_state, limit=limit)
    if not videos:
        return {
            "enabled": True,
            "status": "no_new_representative_video_after_cutoff" if skipped_by_incremental else "no_representative_video_url",
            "items": [],
            "queued": 0,
            "skipped": skipped_by_incremental,
            "errors": 0,
            "candidate_count": len(all_videos),
            "incremental": incremental_state,
            "worker_touched": False,
            "ai_analysis": {
                "state": "not_requested",
                "reason": "no_eligible_representative_video",
                "gate_reason": "",
                "model_readiness_status": "not_ready",
                "provider_calls_allowed": False,
            },
            "viltrox_fit_score_changed_ids": [],
        }

    items: list[dict[str, Any]] = []
    queued = 0
    skipped = 0
    errors = 0
    changed_ids: list[int] = []
    for metadata in videos:
        evidence_result: dict[str, Any] = {}
        enqueue_result: dict[str, Any] = {}
        item_status = "failed"
        error = ""
        try:
            evidence_result = ensure_video_evidence_from_url(
                int(kol_pool_id),
                str(metadata.get("content_url") or ""),
                metadata,
                dry_run=False,
                conn=conn,
            )
            changed_ids.extend(_fit_changed_ids(evidence_result))
            if not evidence_result.get("ok"):
                item_status = str(evidence_result.get("status") or "evidence_failed")
                skipped += 1
            else:
                evidence_id = int(evidence_result.get("evidence_id") or 0)
                enqueue_result = _enqueue_final_v1_video_analysis(
                    conn,
                    kol_pool_id=int(kol_pool_id),
                    evidence_id=evidence_id,
                    source="kol_url_deep_crawl",
                    batch="url_profile_representative",
                    commit=True,
                    search_session_id=body.get("search_session_id"),
                    search_session_item_id=body.get("search_session_item_id"),
                    parent_job_id=body.get("parent_job_id"),
                )
                changed_ids.extend(_fit_changed_ids(enqueue_result))
                item_status = str(enqueue_result.get("status") or "enqueue_unknown")
                if item_status == "queued":
                    queued += 1
                else:
                    skipped += 1
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
                pass
            errors += 1
            error = "representative_video_enqueue_failed"
            item_status = "error"
        items.append(
            {
                "status": item_status,
                "metadata": _public_video_metadata(metadata),
                "evidence_result": _compact_video_evidence_result(evidence_result),
                "enqueue_result": _compact_enqueue_result(enqueue_result),
                "ai_analysis": enqueue_result.get("ai_analysis") if isinstance(enqueue_result, dict) else None,
                "error": error or None,
            }
        )

    return {
        "enabled": True,
        "status": "completed" if not errors else "completed_with_errors",
        "limit": limit,
        "requested": len(videos),
        "candidate_count": len(all_videos),
        "skipped_by_incremental": skipped_by_incremental,
        "queued": queued,
        "skipped": skipped,
        "errors": errors,
        "items": items,
        "incremental": incremental_state,
        "worker_touched": queued > 0,
        "ai_analysis": _representative_ai_analysis(items),
        "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
        "viltrox_fit_score_untouched": not changed_ids,
    }


def _execute_profile_history_video_evidence(
    conn: Any,
    *,
    classified: ClassifiedUrl,
    kol_pool_id: int | None,
    crawl: dict[str, Any],
    body: dict[str, Any],
    incremental_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    incremental_state = incremental_state if isinstance(incremental_state, dict) else {}
    if not _profile_should_materialize_history_videos(body):
        return {
            "enabled": False,
            "status": "disabled",
            "items": [],
            "materialized": 0,
            "reused": 0,
            "skipped": 0,
            "errors": 0,
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }
    if not kol_pool_id:
        return {
            "enabled": True,
            "status": "missing_kol_pool_id",
            "items": [],
            "materialized": 0,
            "reused": 0,
            "skipped": 0,
            "errors": 1,
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }

    limit = _profile_history_video_limit(body)
    all_videos = _profile_representative_video_metadata(
        classified,
        crawl,
        limit=max(limit, _max_posts(body)),
        exclude_video_urls=_profile_exclude_video_urls(body),
    )
    videos, skipped_by_incremental = _filter_incremental_profile_videos(all_videos, incremental_state, limit=limit)
    if not videos:
        return {
            "enabled": True,
            "status": "no_new_history_video_after_cutoff" if skipped_by_incremental else "no_history_video_url",
            "items": [],
            "materialized": 0,
            "reused": 0,
            "skipped": skipped_by_incremental,
            "errors": 0,
            "candidate_count": len(all_videos),
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }

    items: list[dict[str, Any]] = []
    materialized = 0
    reused = 0
    skipped = 0
    errors = 0
    changed_ids: list[int] = []
    for metadata in videos:
        evidence_result: dict[str, Any] = {}
        item_status = "failed"
        error = ""
        try:
            evidence_result = ensure_video_evidence_from_url(
                int(kol_pool_id),
                str(metadata.get("content_url") or ""),
                metadata,
                dry_run=False,
                conn=conn,
                method="url_profile_history_video_evidence_v1",
            )
            changed_ids.extend(_fit_changed_ids(evidence_result))
            if not evidence_result.get("ok"):
                item_status = str(evidence_result.get("status") or "evidence_failed")
                skipped += 1
            else:
                item_status = str(evidence_result.get("status") or "evidence_unknown")
                if item_status == "created":
                    materialized += 1
                else:
                    reused += 1
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
                pass
            errors += 1
            error = "history_video_evidence_failed"
            item_status = "error"
        items.append(
            {
                "status": item_status,
                "metadata": _public_video_metadata(metadata),
                "evidence_result": _compact_video_evidence_result(evidence_result),
                "error": error or None,
            }
        )

    return {
        "enabled": True,
        "status": "completed" if not errors else "completed_with_errors",
        "limit": limit,
        "requested": len(videos),
        "candidate_count": len(all_videos),
        "skipped_by_incremental": skipped_by_incremental,
        "materialized": materialized,
        "reused": reused,
        "skipped": skipped,
        "errors": errors,
        "items": items,
        "incremental": incremental_state,
        "worker_touched": False,
        "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
        "viltrox_fit_score_untouched": not changed_ids,
    }
