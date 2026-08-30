"""Gemini 视频分析处理簇 + 成本入账 + 结果塑形/落库,从 apify_jobs_worker.py 整簇 move 出来。

公共入口保持不变；长流程由注入式 runtime 分阶段编排。原文件用
`from app.workers.apify_jobs_worker_gemini import (...)` re-export 兜住所有调用点。
依赖原文件的常量/小工具(_block_job/_load_video_evidence/...)在本模块**底部**
lazy 形式 import(放底部避免循环导入:此时本模块所有函数已定义,原文件所需名也已先于
其 re-export 行绑定)。红线:本簇零 fit 写;LLM 绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from typing import Any

import psycopg

from app.core.logging import get_logger
from app.core.video_model_chain import analyzer_model_chain
from app.db.connection import db_connection_sync_scope
from app.domains.costs import budget_guard
from app.services.media.video_download import download_direct_video_url
from app.workers.apify_jobs_worker_ytdlp_fallback import persist_image_post_verdict as _persist_image_post_verdict
from app.services.ai.analyzers.gemini_video_results import (
    InvalidFinalV1ResultError,
    ensure_final_v1_result_cacheable,
)
from app.workers.apify_jobs_worker_helpers import (
    _derive_method,
    _int_or_none,
    _json,
    _platform_from_content_url,
    _redact_sensitive_text,
    _target,
    _url_host,
)
from app.workers.apify_jobs_cost import (
    _anthropic_cost,
    _authoritative_gemini_cost,
    _gemini_cost,
    _openai_cost,
)
from app.workers.apify_jobs_video_context import (
    FINAL_V1_PROMPT_CONTRACT,
    _video_final_context,
    _video_performance_context,
)
from app.workers.apify_jobs_worker_gemini_cost import (  # noqa: F401
    _record_anthropic_cost,
    _record_openai_cost,
)
from app.workers.apify_jobs_worker_gemini_stages import (
    StageClock,
    record_final_v1_outcome_diagnostics,
)
from app.workers.apify_jobs_worker_gemini_runtime import (
    GeminiVideoRuntimeDependencies,
    process_gemini_video,
)
from app.workers.apify_jobs_worker_gemini_result import (
    bind_execution_authorization_to_selected_model,
    llm_execution_metadata as _llm_execution_metadata_impl,
    shape_gemini_result as _shape_gemini_result_impl,
)
from app.domains.analysis.cache_repo import (
    quality_triage_target_type,
    upsert_video_analysis_cache,
)
from app.workers.apify_jobs_worker_gemini_followups import extract_lens_evidence_after_final_v1
from app.workers.apify_jobs_worker_session import (
    _enqueue_account_dossier_extract_after_final_v1,
    _enqueue_content_fit_after_final_v1,
    _search_session_analysis_summary_from_result,
    _sync_deep_analysis_result_from_cache,
    _sync_search_session_job,
)


logger = get_logger(__name__)


def _cache_prompt_version(derive_method: str) -> str | None:
    """C5:final_v1 家族行打上当前提示契约;其他 derive_method 留空(非提示产物)。"""

    return FINAL_V1_PROMPT_CONTRACT if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS else None


def _llm_execution_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    return _llm_execution_metadata_impl(
        raw,
        worker_execution_class=WORKER_LLM_EXECUTION_CLASS,
        worker_gemini_model=WORKER_GEMINI_MODEL,
    )


def _gemini_analyzer_payload(
    payload: dict[str, Any], derive_method: str
) -> dict[str, Any]:
    # C1:final_v1 生产 job 发整条认可链(主力 → lite 回退;payload 只能收窄),子进程不再
    # 强制覆盖 generate_content 的 model(gemini_model 置空),分析器如实报告命中的模型。
    # 本地评测 / 非 final_v1 derive 仍钉主力单节。
    chain = analyzer_model_chain(
        payload, final_v1=derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS
    )
    exact = {**payload, "gemini_model": "", "gemini_models": list(chain)}
    if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS:
        exact["gemini_final_v1_models"] = list(chain)
    return exact


def _shape_gemini_result(
    *,
    job: dict[str, Any],
    evidence: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    preflight_cost: float,
    latency_ms: int,
    derive_method: str,
) -> dict[str, Any]:
    return _shape_gemini_result_impl(
        job=job,
        evidence=evidence,
        raw=raw,
        cost=cost,
        cost_basis=cost_basis,
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
        derive_method=derive_method,
        worker_execution_class=WORKER_LLM_EXECUTION_CLASS,
        worker_gemini_model=WORKER_GEMINI_MODEL,
        final_derive_methods=GEMINI_VIDEO_FINAL_DERIVE_METHODS,
        v2_derive_methods=GEMINI_VIDEO_V2_DERIVE_METHODS,
        final_prompt_contract=FINAL_V1_PROMPT_CONTRACT,
        execution_metadata=_llm_execution_metadata(raw),
    )


def _record_gemini_cost(
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    preflight_cost: float,
) -> dict[str, Any]:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    execution = _llm_execution_metadata(raw)
    if raw.get("cost_authority") == "llm_production_google_generate_content_v1":
        attempts = raw.get("llm_attempts") if isinstance(raw.get("llm_attempts"), list) else []
        return {
            "recorded": True,
            "authority": "llm_production_google_generate_content_v1",
            "outer_ledger_write": False,
            "scopes_updated": [],
            "attempts": len(attempts),
            "cost_usd": cost,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model_name": str(raw.get("model") or raw.get("method") or "gemini_video"),
        }
    with db_connection_sync_scope():
        return budget_guard.record_cost(
            scope=LLM_BUDGET_SCOPE,
            cron_task="vkpi_analysis_worker",
            ai_provider="gemini",
            model_name=str(raw.get("model") or raw.get("method") or "gemini_video"),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            # 台账 staff 外键吃 staff id(payload.staff_id 由入队侧落),否则非 llm_production 路径恒 NULL
            staff_id=_int_or_none(payload.get("staff_id")),
            metadata={
                "status": "success" if raw.get("analyzed") else "provider_error",
                "job_id": job.get("id"),
                "target_type": payload.get("target_type"),
                "target_id": str(payload.get("target_id") or ""),
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "latency_ms": latency_ms,
                "triggered_by_user_id": triggered_by,
                "error": _redact_sensitive_text(raw.get("error") or ""),
                "binding": execution["binding"],
                "execution_class": execution["execution_class"],
                "authorization_scope": execution["authorization_scope"],
                "evaluation_only": execution["evaluation_only"],
                "production_authorized": execution["production_authorized"],
                "execution_authorization_at_run": execution["execution_authorization_at_run"],
                "signed_readiness_at_run": execution["signed_readiness_at_run"],
                "claim_status": execution["claim_status"],
                "model_readiness_status": execution["model_readiness_status"],
                "base_derive_method": execution["base_derive_method"],
                "cache_derive_method": execution["cache_derive_method"],
            },
            extra_scopes=["monthly_total", "single_call", "provider:gemini"],
        )


def _quality_incomplete_reason(raw: dict[str, Any]) -> dict[str, Any]:
    issues = raw.get("quality_issues") if isinstance(raw.get("quality_issues"), list) else []
    authorization_missing = "authorization_snapshot_missing" in issues
    return {
        "reason": (
            "authorization_snapshot_missing"
            if authorization_missing
            else "final_v1_quality_incomplete"
        ),
        "stage": (
            "video_analysis_final_v1_authorization_gate"
            if authorization_missing
            else "video_analysis_final_v1_quality_gate"
        ),
        "quality_status": "quality_incomplete",
        "quality_issues": [str(item)[:160] for item in issues[:32]],
    }


def _mark_authorization_snapshot_missing(raw: dict[str, Any]) -> None:
    """Keep paid output for triage without admitting it to ready consumers."""

    execution = (
        raw.get("llm_execution")
        if isinstance(raw.get("llm_execution"), dict)
        else {}
    )
    if raw.get("analyzed") is not True or execution.get(
        "authorization_snapshot_match"
    ) is not False:
        return
    issues = (
        list(raw.get("quality_issues"))
        if isinstance(raw.get("quality_issues"), list)
        else []
    )
    issues.append("authorization_snapshot_missing")
    raw["quality_status"] = "quality_incomplete"
    raw["quality_issues"] = list(
        dict.fromkeys(str(item) for item in issues if str(item).strip())
    )


def _finish_cache_job(
    cur: Any,
    *,
    job_id: int,
    cache_status: str,
    raw: dict[str, Any],
) -> None:
    if cache_status == "quality_incomplete":
        cur.execute(
            """
            UPDATE apify_jobs
            SET status='triage',
                last_error=%s::jsonb,
                last_error_category='data_quality',
                next_retry_at=NULL,
                updated_at=NOW()
            WHERE id=%s
            """,
            (_json(_quality_incomplete_reason(raw)), int(job_id)),
        )
        return
    cur.execute(
        """
        UPDATE apify_jobs
        SET status='done',
            last_error=NULL,
            last_error_category=NULL,
            next_retry_at=NULL,
            updated_at=NOW()
        WHERE id=%s
        """,
        (int(job_id),),
    )


def _write_gemini_cache(
    conn: psycopg.Connection[Any],
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    preflight_cost: float,
    latency_ms: int,
    derive_method: str,
) -> None:
    cache_status = "ready"
    if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS:
        cache_status = ensure_final_v1_result_cacheable(raw) or "ready"
    target_type, target_id = _target(payload)
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    shaped = _shape_gemini_result(
        job=job,
        evidence=evidence,
        raw=raw,
        cost=cost,
        cost_basis=cost_basis,
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
        derive_method=derive_method,
    )
    execution = _llm_execution_metadata(raw)
    cache_derive_method = (
        execution["cache_derive_method"]
        if execution["evaluation_only"]
        else derive_method
    )
    cache_target_type = (
        quality_triage_target_type(target_type)
        if cache_status == "quality_incomplete"
        else target_type
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cache_id = upsert_video_analysis_cache(
                cur,
                target_type=cache_target_type,
                target_id=target_id,
                model=str(raw.get("model") or raw.get("method") or "gemini_video"),
                derive_method=cache_derive_method,
                result_json=_json(shaped),
                cost=cost,
                triggered_by_user_id=_int_or_none(triggered_by),
                prompt_version=_cache_prompt_version(derive_method),
                status=cache_status,
            )
            _finish_cache_job(
                cur,
                job_id=int(job["id"]),
                cache_status=cache_status,
                raw=raw,
            )
    if cache_status == "quality_incomplete":
        reason = _quality_incomplete_reason(raw)
        _sync_search_session_job(
            conn,
            int(job["id"]),
            raw_status="triage",
            reason=str(reason["reason"]),
            analysis_summary={
                "status": "quality_incomplete",
                "cache_id": cache_id,
                "derive_method": cache_derive_method,
                "target_type": target_type,
                "cache_target_type": cache_target_type,
                "target_id": target_id,
                "quality_issues": reason["quality_issues"],
            },
        )
        return
    if execution["evaluation_only"]:
        _sync_search_session_job(
            conn,
            int(job["id"]),
            raw_status="done",
            analysis_summary={
                "cache_id": cache_id,
                "derive_method": cache_derive_method,
                "base_derive_method": derive_method,
                "target_type": target_type,
                "target_id": target_id,
                "evaluation_only": True,
                "production_authorized": False,
                "claim_status": "descriptive_only",
                "model_readiness_status": execution["model_readiness_status"],
            },
        )
        return
    deep_result = _sync_deep_analysis_result_from_cache(
        conn,
        cache_id=cache_id,
        derive_method=derive_method,
        job_id=int(job["id"]),
    )
    account_extract_job = None
    content_fit_job = None
    try:
        account_extract_job = _enqueue_account_dossier_extract_after_final_v1(
            conn,
            job_id=int(job["id"]),
            deep_result=deep_result,
        )
        # QA P0/P1 修:主成功路径补内容契合链式入队,连同 account_dossier 兜进 try/except;入队异常仅 warning、绝不冒泡把 final_v1 标 failed。
        content_fit_job = _enqueue_content_fit_after_final_v1(
            conn,
            job_id=int(job["id"]),
            deep_result=deep_result,
            source_payload=payload,
        )
    except Exception as exc:
        logger.warning(
            "final_v1 followup enqueue failed (non-fatal) | job_id=%s exception_type=%s",
            job.get("id"),
            type(exc).__name__,
        )
    extract_lens_evidence_after_final_v1(cache_id=cache_id, derive_method=derive_method, job_id=job.get("id"))  # 波 D·D2:深析完成即提列(永不抛)
    analysis_summary = _search_session_analysis_summary_from_result(
        cache_id=cache_id,
        derive_method=derive_method,
        target_type=target_type,
        target_id=target_id,
        evidence=evidence,
        result=shaped,
        cost=cost,
    )
    if analysis_summary and deep_result:
        analysis_summary["deep_result"] = deep_result
    if analysis_summary and account_extract_job:
        analysis_summary["account_dossier_extract_job"] = account_extract_job
    _sync_search_session_job(
        conn,
        int(job["id"]),
        raw_status="done",
        analysis_summary=analysis_summary,
    )


# Gemini 视频多 pass 评审处理簇(keyframe QA / flash+pro/gpt55/claude judge)整簇已抽到
# apify_jobs_worker_gemini_judges.py(函数体逐字不变;re-export 兜住所有调用点,含下划线私有名)。
# 本 import 放在 _record_*_cost / _write_gemini_cache 定义之后:judges 模块底部回引这几个名,
# 此刻它们已绑定,故无循环导入死锁。
from app.workers.apify_jobs_worker_gemini_judges import (  # noqa: E402
    _process_gemini_video_final_v1_keyframe_qa,
    _process_gemini_video_flash_claude_judge,
    _process_gemini_video_flash_gpt55_judge,
    _process_gemini_video_flash_pro_judge,
)


def _final_v1_scope_checkpoint(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    derive_method: str,
    *,
    provider_calls_performed: bool | None,
    raw: dict[str, Any] | None = None,
) -> bool:
    """Revalidate the signed child between paid/external execution phases.

    Thin binding over ``apify_jobs_worker_paid_scope.final_v1_scope_checkpoint``
    so the worker's ``_block_job`` and connection scope stay the injectable
    seams used by the provider-chain tests.
    """
    from app.workers.apify_jobs_worker_paid_scope import final_v1_scope_checkpoint

    return final_v1_scope_checkpoint(
        conn,
        job,
        payload,
        derive_method,
        provider_calls_performed=provider_calls_performed,
        block_job=_block_job,
        connection_scope=db_connection_sync_scope,
        raw=raw,
    )


def _gemini_video_runtime_dependencies() -> GeminiVideoRuntimeDependencies:
    """Bind the worker's live seams so test monkeypatches remain authoritative."""

    return GeminiVideoRuntimeDependencies(
        target=_target,
        derive_method=_derive_method,
        block_job=_block_job,
        load_video_evidence=_load_video_evidence,
        platform_from_content_url=_platform_from_content_url,
        url_host=_url_host,
        process_keyframe_qa=_process_gemini_video_final_v1_keyframe_qa,
        process_flash_pro_judge=_process_gemini_video_flash_pro_judge,
        process_flash_gpt55_judge=_process_gemini_video_flash_gpt55_judge,
        process_flash_claude_judge=_process_gemini_video_flash_claude_judge,
        logger=logger,
        monotonic=time.monotonic,
        stage_clock_factory=StageClock,
        temporary_directory=tempfile.TemporaryDirectory,
        gemini_analyzer_payload=_gemini_analyzer_payload,
        resolve_cached_or_provider_video=_resolve_cached_or_provider_video,
        persist_image_post_verdict=_persist_image_post_verdict,
        download_direct_video_url=download_direct_video_url,
        scope_checkpoint=_final_v1_scope_checkpoint,
        video_final_context=_video_final_context,
        video_performance_context=_video_performance_context,
        run_analyzer=_run_gemini_analyzer_with_timeout,
        warm_video_to_r2=_warm_video_to_r2_from_local,
        bind_execution_authorization=bind_execution_authorization_to_selected_model,
        mark_authorization_snapshot_missing=_mark_authorization_snapshot_missing,
        ensure_final_v1_result_cacheable=ensure_final_v1_result_cacheable,
        invalid_final_v1_error=InvalidFinalV1ResultError,
        authoritative_cost=_authoritative_gemini_cost,
        record_cost=_record_gemini_cost,
        record_diagnostics=record_final_v1_outcome_diagnostics,
        int_or_none=_int_or_none,
        shape_result=_shape_gemini_result,
        execution_metadata=_llm_execution_metadata,
        quality_triage_target_type=quality_triage_target_type,
        upsert_cache=upsert_video_analysis_cache,
        json_dump=_json,
        cache_prompt_version=_cache_prompt_version,
        finish_cache_job=_finish_cache_job,
        quality_incomplete_reason=_quality_incomplete_reason,
        sync_search_session_job=_sync_search_session_job,
        sync_deep_result=_sync_deep_analysis_result_from_cache,
        enqueue_account=_enqueue_account_dossier_extract_after_final_v1,
        enqueue_content_fit=_enqueue_content_fit_after_final_v1,
        extract_lens=extract_lens_evidence_after_final_v1,
        search_session_summary=_search_session_analysis_summary_from_result,
        final_v1_keyframe_qa_derive_method=FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
        final_derive_methods=frozenset(GEMINI_VIDEO_FINAL_DERIVE_METHODS),
        v2_derive_methods=frozenset(GEMINI_VIDEO_V2_DERIVE_METHODS),
        llm_budget_scope=LLM_BUDGET_SCOPE,
        worker_model=WORKER_GEMINI_MODEL,
        worker_execution_class=WORKER_LLM_EXECUTION_CLASS,
    )


def _process_gemini_video(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
) -> None:
    process_gemini_video(
        conn,
        job,
        payload,
        preflight_cost,
        dependencies=_gemini_video_runtime_dependencies(),
    )

# 原文件留下的常量/小工具:放模块底部 import(避免循环导入;调用点均在函数体内、运行期才解析)。
from app.workers.apify_jobs_worker import (  # noqa: E402
    FINAL_V1_GEMINI_MODELS,
    FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
    FINAL_V1_KEYFRAME_QA_MODEL,
    GEMINI_VIDEO_FINAL_DERIVE_METHODS,
    GEMINI_VIDEO_V2_DERIVE_METHODS,
    LLM_BUDGET_SCOPE,
    WORKER_GEMINI_MODEL,
    WORKER_LLM_EXECUTION_CLASS,
    _block_job,
    _extract_keyframes_for_qa,
    _gemini_worker_overrides,
    _load_video_evidence,
    _log_budget_preflight_record_only,
    _provider_allowed,
    _provider_budget_preflight,
    _resolve_cached_or_provider_video,
    _resolve_video_media,
    _run_gemini_analyzer_with_timeout,
    _warm_video_to_r2_from_local,
)
