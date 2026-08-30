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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

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
from app.domains.kol.provider_job_access import ProviderJobAccessError
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
    *,
    authorization_checkpoint: Callable[[], Any] | None = None,
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
        if authorization_checkpoint:
            authorization_checkpoint()
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
                if authorization_checkpoint:
                    authorization_checkpoint()
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
                    provider_parent_payload=(
                        body.get("provider_parent_payload")
                        if isinstance(body.get("provider_parent_payload"), dict)
                        else None
                    ),
                    staff=(
                        body.get("paid_action_staff")
                        if isinstance(body.get("paid_action_staff"), dict)
                        else None
                    ),
                    enforce_target_write=body.get("enforce_target_write") is True,
                )
                changed_ids.extend(_fit_changed_ids(enqueue_result))
                status = str(enqueue_result.get("status") or "enqueue_unknown")
    except ProviderJobAccessError:
        try:
            conn.rollback()
        except Exception:
            logger.warning("authorization rollback failed", exc_info=True)
        raise
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        error = "video_analysis_enqueue_failed"
        status = "failed"

    if authorization_checkpoint:
        authorization_checkpoint()
    cached_video_url, video_provider_called = _cache_video_flow_url(classified, metadata, evidence_id)

    account_dossier_extract_job = None
    if status == "already_analyzed" and body.get("skip_profile_video_followups") is not True:
        if authorization_checkpoint:
            authorization_checkpoint()
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
    if authorization_checkpoint:
        authorization_checkpoint()
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


def _noop_checkpoint() -> None:
    return None


def _rollback_quietly(conn: Any, message: str) -> None:
    """rollback 失败只告警不再抛;message 逐字保留原两处日志文案。"""
    try:
        conn.rollback()
    except Exception:
        logger.warning(message, exc_info=True)


def _video_metadata_dict(video_flow: dict[str, Any]) -> dict[str, Any] | None:
    metadata = video_flow.get("video_metadata")
    return metadata if isinstance(metadata, dict) else None


def _creator_identity_dict(video_flow: dict[str, Any]) -> dict[str, Any]:
    identity = video_flow.get("creator_identity")
    return identity if isinstance(identity, dict) else {}


@dataclass
class _NewCreatorPipelineState:
    """新创作者 video 流水线的累计状态;异常路径下已写入的部分照旧透出。"""

    crawl: dict[str, Any] = field(default_factory=dict)
    profile_data: dict[str, Any] = field(default_factory=dict)
    write_result: dict[str, Any] = field(default_factory=dict)
    evidence_result: dict[str, Any] = field(default_factory=dict)
    enqueue_result: dict[str, Any] = field(default_factory=dict)
    representative_video_analysis: dict[str, Any] = field(default_factory=dict)
    history_video_evidence: dict[str, Any] = field(default_factory=dict)
    kol_pool_id: int | None = None
    evidence_id: int | None = None
    status: str = "failed"
    error: str = ""
    changed_ids: list[int] = field(default_factory=list)
    account_dossier_extract_job: dict[str, Any] | None = None
    cached_video_url: str | None = None
    video_provider_called: bool = False


def _creator_unresolved_result(
    conn: Any,
    *,
    classified: ClassifiedUrl,
    video_flow: dict[str, Any],
    body: dict[str, Any],
    started: float,
    checkpoint: Callable[[], Any],
) -> dict[str, Any]:
    from app.domains.kol.url_deep_crawl_execute import _record_deep_crawl_run

    checkpoint()
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


def _official_channel_result(
    conn: Any,
    *,
    official: dict[str, Any],
    classified: ClassifiedUrl,
    video_flow: dict[str, Any],
    body: dict[str, Any],
    started: float,
    checkpoint: Callable[[], Any],
) -> dict[str, Any]:
    from app.domains.kol.url_deep_crawl_execute import _record_deep_crawl_run

    checkpoint()
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


def _final_v1_enqueue_kwargs(
    body: dict[str, Any], *, kol_pool_id: int, evidence_id: int, batch: str
) -> dict[str, Any]:
    """final_v1 入队 kwargs 组装;键序/取值与原内联调用逐字节等价。"""
    return {
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "source": "kol_url_deep_crawl",
        "batch": batch,
        "commit": True,
        "search_session_id": body.get("search_session_id"),
        "search_session_item_id": body.get("search_session_item_id"),
        "parent_job_id": body.get("parent_job_id"),
        "local_evaluation": body.get("local_evaluation") is True,
        "provider_parent_payload": (
            body.get("provider_parent_payload")
            if isinstance(body.get("provider_parent_payload"), dict)
            else None
        ),
        "staff": (
            body.get("paid_action_staff")
            if isinstance(body.get("paid_action_staff"), dict)
            else None
        ),
        "enforce_target_write": body.get("enforce_target_write") is True,
    }


def _pipeline_video_evidence_and_enqueue(
    conn: Any,
    state: _NewCreatorPipelineState,
    *,
    classified: ClassifiedUrl,
    video_flow: dict[str, Any],
    body: dict[str, Any],
    checkpoint: Callable[[], Any],
) -> None:
    metadata = _video_metadata_dict(video_flow)
    checkpoint()
    state.evidence_result = ensure_video_evidence_from_url(
        state.kol_pool_id,
        classified.normalized_url,
        metadata,
        dry_run=False,
        conn=conn,
    )
    state.changed_ids.extend(_fit_changed_ids(state.evidence_result))
    if not state.evidence_result.get("ok"):
        state.status = str(state.evidence_result.get("status") or "evidence_failed")
        return
    state.evidence_id = int(state.evidence_result.get("evidence_id") or 0) or None
    if not state.evidence_id:
        state.status = "evidence_missing_id"
        return
    checkpoint()
    state.enqueue_result = _enqueue_final_v1_video_analysis(
        conn,
        **_final_v1_enqueue_kwargs(
            body,
            kol_pool_id=state.kol_pool_id,
            evidence_id=state.evidence_id,
            batch="url_new_creator",
        ),
    )
    state.changed_ids.extend(_fit_changed_ids(state.enqueue_result))
    state.status = str(state.enqueue_result.get("status") or "enqueue_unknown")


def _run_onboarding_followups(
    conn: Any,
    state: _NewCreatorPipelineState,
    *,
    classified: ClassifiedUrl,
    profile_classified: Any,
    body: dict[str, Any],
    max_posts: int,
    checkpoint: Callable[[], Any],
) -> None:
    from app.domains.kol.url_deep_crawl_execute import _profile_incremental_state

    if not state.kol_pool_id or body.get("skip_profile_video_followups") is True:
        return
    checkpoint()
    onboarding_body = {
        **body,
        "mode": "account_deep",
        "representative_video_limit": body.get("representative_video_limit") or 3,
        "history_video_limit": body.get("history_video_limit") or max_posts,
        "materialize_history_videos": True,
        "exclude_video_urls": [classified.normalized_url],
    }
    incremental_state = _profile_incremental_state(None)
    state.representative_video_analysis = _execute_profile_representative_video_analysis(
        conn,
        classified=profile_classified,
        kol_pool_id=state.kol_pool_id,
        crawl=state.crawl,
        body=onboarding_body,
        incremental_state=incremental_state,
    )
    state.history_video_evidence = _execute_profile_history_video_evidence(
        conn,
        classified=profile_classified,
        kol_pool_id=state.kol_pool_id,
        crawl=state.crawl,
        body=onboarding_body,
        incremental_state=incremental_state,
    )
    state.changed_ids.extend(_fit_changed_ids(state.representative_video_analysis))
    state.changed_ids.extend(_fit_changed_ids(state.history_video_evidence))


def _run_new_creator_pipeline(
    conn: Any,
    state: _NewCreatorPipelineState,
    *,
    classified: ClassifiedUrl,
    profile_classified: Any,
    video_flow: dict[str, Any],
    body: dict[str, Any],
    max_posts: int,
    checkpoint: Callable[[], Any],
) -> None:
    """原 try 体:抓档→建档→证据→入队→onboarding 跟进;guard 早退替换 else 金字塔。"""
    from app.domains.kol.url_deep_crawl_execute import (
        _crawl_profile_basics,
        _profile_data_for_new_video_creator,
        _profile_target,
    )

    checkpoint()
    state.crawl = _crawl_profile_basics(
        profile_classified, target=_profile_target(profile_classified), max_posts=max_posts
    )
    if str(state.crawl.get("status") or "").lower() not in {"ok", "synced"}:
        state.status = "profile_crawl_failed"
        state.error = "profile_crawl_not_ready"
        return
    state.profile_data = _profile_data_for_new_video_creator(
        profile_classified,
        state.crawl,
        video_flow,
        max_posts=max_posts,
    )
    checkpoint()
    state.write_result = write_kol_profile_basics(None, state.profile_data, dry_run=False, conn=conn)
    state.changed_ids.extend(_fit_changed_ids(state.write_result))
    state.kol_pool_id = int(state.write_result.get("kol_pool_id") or 0) or None
    if not state.kol_pool_id:
        state.status = "kol_create_missing_id"
        return
    _pipeline_video_evidence_and_enqueue(
        conn, state, classified=classified, video_flow=video_flow, body=body, checkpoint=checkpoint
    )
    _run_onboarding_followups(
        conn,
        state,
        classified=classified,
        profile_classified=profile_classified,
        body=body,
        max_posts=max_posts,
        checkpoint=checkpoint,
    )


def _maybe_enqueue_dossier_followup(
    conn: Any,
    state: _NewCreatorPipelineState,
    *,
    classified: ClassifiedUrl,
    body: dict[str, Any],
    checkpoint: Callable[[], Any],
) -> dict[str, Any] | None:
    from app.domains.kol.url_deep_crawl_execute import _enqueue_account_dossier_extract_followup

    if (
        state.status != "already_analyzed"
        or not state.kol_pool_id
        or body.get("skip_profile_video_followups") is True
    ):
        return None
    checkpoint()
    job = _enqueue_account_dossier_extract_followup(
        conn,
        kol_pool_id=state.kol_pool_id,
        source="kol_url_video_new_creator_flow",
        trigger="video_already_analyzed",
        source_url=classified.normalized_url,
        query_text=f"new creator account dossier - kol_pool #{state.kol_pool_id}",
    )
    state.changed_ids.extend(_fit_changed_ids(job or {}))
    return job


def _new_creator_write_flags(state: _NewCreatorPipelineState, run_id: Any) -> tuple[bool, bool]:
    """返回 (business_tables_written, worker_touched);布尔代数逐字保持。"""
    worker_touched = state.status == "queued"
    representative_worker_touched = bool(state.representative_video_analysis.get("worker_touched"))
    history_written = bool(
        (state.history_video_evidence.get("materialized") or 0)
        or (state.history_video_evidence.get("reused") or 0)
    )
    business_tables_written = (
        bool(state.kol_pool_id)
        or bool(state.evidence_result.get("status") in {"created", "reused"})
        or worker_touched
        or representative_worker_touched
        or history_written
        or bool(run_id)
    )
    worker_touched_out = (
        worker_touched
        or representative_worker_touched
        or bool(
            state.account_dossier_extract_job
            and state.account_dossier_extract_job.get("status") == "queued"
        )
    )
    return business_tables_written, worker_touched_out


def _new_creator_run_summary(
    state: _NewCreatorPipelineState,
    *,
    video_flow: dict[str, Any],
    profile_classified: Any,
    started: float,
) -> dict[str, Any]:
    return {
        "operation": "new_creator_video_analysis",
        "status": state.status,
        "error": state.error or None,
        "creator_identity": video_flow.get("creator_identity"),
        "profile_url": profile_classified.normalized_url,
        "profile_crawl_status": state.crawl.get("status"),
        "profile_provider_source": state.crawl.get("provider_source"),
        "profile_write_result": _compact_profile_write_result(state.write_result),
        "profile_data": _public_profile_data(state.profile_data),
        "video_metadata": video_flow.get("video_metadata"),
        "evidence_result": _compact_video_evidence_result(state.evidence_result),
        "enqueue_result": _compact_enqueue_result(state.enqueue_result),
        "representative_video_analysis": state.representative_video_analysis,
        "history_video_evidence": state.history_video_evidence,
        "account_dossier_extract_job": state.account_dossier_extract_job,
        "viltrox_fit_score_changed_ids": sorted(set(state.changed_ids)),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _new_creator_response(
    state: _NewCreatorPipelineState,
    *,
    video_flow: dict[str, Any],
    profile_classified: Any,
    run_id: Any,
    run_status: str,
    started: float,
) -> dict[str, Any]:
    from app.domains.kol.url_deep_crawl_execute import _profile_target

    business_tables_written, worker_touched = _new_creator_write_flags(state, run_id)
    return {
        **video_flow,
        "status": state.status,
        "operation": "new_creator_video_analysis",
        "kol_pool_id": state.kol_pool_id,
        "evidence_id": state.evidence_id,
        "profile_flow": {
            "status": "ready" if state.kol_pool_id else state.status,
            "operation": "insert",
            "kol_pool_id": state.kol_pool_id,
            "target": _profile_target(profile_classified),
            "profile_data": _public_profile_data(state.profile_data),
            "write_result": _compact_profile_write_result(state.write_result),
            "crawl_status": state.crawl.get("status"),
            "provider_source": state.crawl.get("provider_source"),
            "viltrox_fit_score_changed_ids": _fit_changed_ids(state.write_result),
            "viltrox_fit_score_untouched": not _fit_changed_ids(state.write_result),
        },
        "evidence_result": _compact_video_evidence_result(state.evidence_result),
        "enqueue_result": _compact_enqueue_result(state.enqueue_result),
        "ai_analysis": _video_ai_analysis(state.enqueue_result),
        "representative_video_analysis": state.representative_video_analysis,
        "history_video_evidence": state.history_video_evidence,
        "account_dossier_extract_job": state.account_dossier_extract_job,
        "run_id": run_id,
        "run_status": run_status,
        "error": state.error or None,
        "crawl_performed": bool(state.crawl),
        "business_tables_written": business_tables_written,
        "worker_touched": worker_touched,
        "write_db": business_tables_written,
        "writes": ["vkpi_kol_pool", "vkpi_kol_video_evidence", "apify_jobs", "vkpi_kol_url_deep_crawl_runs"],
        "cached_video_url": state.cached_video_url,
        "provider_calls_performed": state.video_provider_called,
        "llm_calls_performed": False,
        "viltrox_fit_score_changed_ids": sorted(set(state.changed_ids)),
        "viltrox_fit_score_untouched": not state.changed_ids,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _execute_new_creator_video_flow(
    classified: ClassifiedUrl,
    video_flow: dict[str, Any],
    body: dict[str, Any],
    *,
    authorization_checkpoint: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    from app.domains.kol.url_deep_crawl_execute import (
        _profile_classified_from_video_flow,
        _record_deep_crawl_run,
    )

    # 官方自有账号兜底闸:任何抵达建档口的路径都不许把官方渠道写进 KOL 池。
    # (主判定在 video_url_resolver 的 worker 链;这里防旧调用路径/回放绕过。)
    from app.domains.kol.video_url_resolver import find_official_channel_match

    checkpoint = authorization_checkpoint or _noop_checkpoint
    started = time.monotonic()
    max_posts = _max_posts(body)
    profile_classified = _profile_classified_from_video_flow(classified, video_flow)
    conn = get_conn()

    if not profile_classified:
        return _creator_unresolved_result(
            conn, classified=classified, video_flow=video_flow, body=body, started=started, checkpoint=checkpoint
        )

    official = find_official_channel_match(_creator_identity_dict(video_flow))
    if official:
        return _official_channel_result(
            conn,
            official=official,
            classified=classified,
            video_flow=video_flow,
            body=body,
            started=started,
            checkpoint=checkpoint,
        )

    state = _NewCreatorPipelineState()
    try:
        _run_new_creator_pipeline(
            conn,
            state,
            classified=classified,
            profile_classified=profile_classified,
            video_flow=video_flow,
            body=body,
            max_posts=max_posts,
            checkpoint=checkpoint,
        )
    except ProviderJobAccessError:
        _rollback_quietly(conn, "authorization rollback failed")
        raise
    except Exception:
        _rollback_quietly(conn, "suppressed exception (hardening: was silent)")
        state.error = "video_creator_flow_failed"
        state.status = "failed"

    checkpoint()
    state.cached_video_url, state.video_provider_called = _cache_video_flow_url(
        classified, _video_metadata_dict(video_flow), state.evidence_id
    )
    state.account_dossier_extract_job = _maybe_enqueue_dossier_followup(
        conn, state, classified=classified, body=body, checkpoint=checkpoint
    )

    representative_worker_touched = bool(state.representative_video_analysis.get("worker_touched"))
    run_status = (
        "ready" if state.status in _VIDEO_BASE_COMPLETE_STATUSES or representative_worker_touched else "failed"
    )
    checkpoint()
    run_id = _record_deep_crawl_run(
        conn,
        kol_pool_id=state.kol_pool_id,
        source_url=classified.normalized_url,
        url_type="video",
        mode=_video_execute_mode(body),
        status=run_status,
        dry_run=False,
        summary=_new_creator_run_summary(
            state, video_flow=video_flow, profile_classified=profile_classified, started=started
        ),
    )
    return _new_creator_response(
        state,
        video_flow=video_flow,
        profile_classified=profile_classified,
        run_id=run_id,
        run_status=run_status,
        started=started,
    )
