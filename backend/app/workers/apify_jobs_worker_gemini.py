"""Gemini 视频分析处理簇 + 成本入账 + 结果塑形/落库,从 apify_jobs_worker.py 整簇 move 出来。

函数体逐字不变 → 行为必然不变。原文件用
`from app.workers.apify_jobs_worker_gemini import (...)` re-export 兜住所有调用点。
依赖原文件的常量/小工具(_block_job/_load_video_evidence/...)在本模块**底部**
lazy 形式 import(放底部避免循环导入:此时本模块所有函数已定义,原文件所需名也已先于
其 re-export 行绑定)。红线:本簇零 fit 写;LLM 绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

import psycopg

from app.core.logging import get_logger
from app.db.connection import db_connection_sync_scope
from app.domains.costs import budget_guard
from app.services.media.video_download import download_direct_video_url
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer
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
    _low_scores,
    _video_final_context,
    _video_performance_context,
)
from app.workers.apify_jobs_worker_gemini_cost import (  # noqa: F401
    _record_anthropic_cost,
    _record_openai_cost,
)
from app.workers.apify_jobs_worker_gemini_stages import (
    StageClock,
    merged_stage_timings,
    record_final_v1_outcome_diagnostics,
)
from app.domains.analysis.cache_repo import upsert_video_analysis_cache
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
    value = raw.get("llm_execution") if isinstance(raw.get("llm_execution"), dict) else {}
    execution_class = str(
        value.get("execution_class") or WORKER_LLM_EXECUTION_CLASS
    )
    evaluation_only = execution_class == "local_evaluation"
    production_authorized = bool(value.get("production_authorized")) and not evaluation_only
    requested_model = str(value.get("model") or WORKER_GEMINI_MODEL)
    reported_model = str(value.get("reported_model") or raw.get("model") or "")
    return {
        "binding": str(value.get("binding") or f"google/{WORKER_GEMINI_MODEL}"),
        "model": requested_model,
        "reported_model": reported_model or None,
        "model_match": bool(reported_model and reported_model == requested_model),
        "execution_class": execution_class,
        "authorization_scope": (
            "evaluation_only"
            if evaluation_only
            else "production"
            if production_authorized
            else str(value.get("authorization_scope") or "blocked")
        ),
        "evaluation_only": evaluation_only,
        "production_authorized": production_authorized,
        # Model readiness and content claims are orthogonal.  No model result
        # promotes a descriptive content claim to a validated business claim.
        "claim_status": "descriptive_only",
        "model_readiness_status": str(
            value.get("model_readiness_status")
            or (
                "evaluation_only_not_production_ready"
                if evaluation_only
                else "production_ready"
                if production_authorized
                else "not_ready"
            )
        ),
        "base_derive_method": str(
            value.get("base_derive_method") or "video_analysis_final_v1"
        ),
        "cache_derive_method": str(
            value.get("cache_derive_method")
            or (
                "video_analysis_final_v1__local_eval"
                if evaluation_only
                else value.get("base_derive_method")
                or "video_analysis_final_v1"
            )
        ),
    }


def _gemini_analyzer_payload(
    payload: dict[str, Any], derive_method: str
) -> dict[str, Any]:
    exact = {
        **payload,
        "gemini_model": WORKER_GEMINI_MODEL,
        "gemini_models": [WORKER_GEMINI_MODEL],
    }
    if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS:
        exact["gemini_final_v1_models"] = (
            gemini_video_analyzer.final_v1_gemini_models(
                [WORKER_GEMINI_MODEL]
            )
        )
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
    execution = _llm_execution_metadata(raw)
    if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS:
        final = raw.get("video_analysis_final_v1") if isinstance(raw.get("video_analysis_final_v1"), dict) else {}
        model_name = str(raw.get("model") or raw.get("method") or "gemini_video")
        segments = raw.get("cost_segments") if isinstance(raw.get("cost_segments"), list) else None
        shaped: dict[str, Any] = {
            "schema_version": "video_analysis_final_v1",
            "status": "completed",
            "mock": False,
            "analysis_method": derive_method,
            "model": model_name,
            "provenance": {
                **(
                    raw.get("provenance")
                    if isinstance(raw.get("provenance"), dict)
                    else {
                        "provider": "gemini",
                        "model": model_name,
                        "method": str(raw.get("method") or "gemini_video"),
                    }
                ),
                "binding": execution["binding"],
                "execution_class": execution["execution_class"],
                "evaluation_only": execution["evaluation_only"],
                "production_authorized": execution["production_authorized"],
                "claim_status": execution["claim_status"],
                "model_readiness_status": execution["model_readiness_status"],
                "base_derive_method": execution["base_derive_method"],
                "cache_derive_method": execution["cache_derive_method"],
                "prompt_contract": FINAL_V1_PROMPT_CONTRACT,
            },
            "llm_execution": execution,
            "evaluation_only": execution["evaluation_only"],
            "production_authorized": execution["production_authorized"],
            "claim_status": execution["claim_status"],
            "model_readiness_status": execution["model_readiness_status"],
            "job_id": job.get("id"),
            "target_type": "video",
            "target_id": str(evidence.get("id")),
            "source": {
                "url": evidence.get("content_url"),
                "title": evidence.get("title"),
                "platform": evidence.get("platform"),
                "creator_handle": evidence.get("creator_handle"),
                "creator_name": evidence.get("creator_name"),
                "project_id": evidence.get("project_id"),
                "project_name": evidence.get("project_name"),
                "product_name": evidence.get("product_name"),
                "kol_pool_id": evidence.get("kol_pool_id"),
            },
            "performance_metrics": _video_performance_context(evidence),
            "layer1_visual_content": final.get("layer1_visual_content") or {},
            "layer2_viewer_emotion": final.get("layer2_viewer_emotion") or {},
            "layer3_three_values": final.get("layer3_three_values") or {},
            "layer4_attribution": final.get("layer4_attribution") or {},
            "layer5_recommendations": final.get("layer5_recommendations") or {},
            "layer6_flags_and_scores": final.get("layer6_flags_and_scores") or {},
            "cost": {
                "recorded_cost_usd": cost,
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "segments": segments
                or [
                    {
                        "stage": "single_pass",
                        "provider": "gemini",
                        "model": model_name,
                        "cost_usd": cost,
                    }
                ],
                "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
                "latency_ms": latency_ms,
                # 剖面用阶段耗时(分析器子进程 + worker 合并;零 LLM 成本,只追加不改六层)
                "stage_timings_ms": merged_stage_timings(raw),
            },
            "raw_gemini_video": raw,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if isinstance(raw.get("final_v1_keyframe_qa"), dict):
            shaped["keyframe_qa"] = raw.get("final_v1_keyframe_qa") or {}
            shaped["qa_pass"] = raw.get("qa_pass")
            shaped["frame_extraction"] = raw.get("frame_extraction") if isinstance(raw.get("frame_extraction"), dict) else {}
            shaped["final_v1_pass"] = raw.get("final_v1_pass") if isinstance(raw.get("final_v1_pass"), dict) else {}
        return shaped
    if derive_method in GEMINI_VIDEO_V2_DERIVE_METHODS:
        v2 = raw.get("video_analysis_v2") if isinstance(raw.get("video_analysis_v2"), dict) else {}
        layer3 = dict(v2.get("layer3_integrated_judgment") or {})
        layer3["performance_metrics"] = _video_performance_context(evidence)
        model_name = str(raw.get("model") or raw.get("method") or "gemini_video")
        segments = raw.get("cost_segments") if isinstance(raw.get("cost_segments"), list) else None
        return {
            "schema_version": "video_analysis_v2",
            "mock": False,
            "analysis_method": derive_method,
            "llm_execution": execution,
            "evaluation_only": execution["evaluation_only"],
            "production_authorized": execution["production_authorized"],
            "claim_status": execution["claim_status"],
            "job_id": job.get("id"),
            "target_type": "video",
            "target_id": str(evidence.get("id")),
            "source": {
                "url": evidence.get("content_url"),
                "title": evidence.get("title"),
                "platform": evidence.get("platform"),
                "creator_handle": evidence.get("creator_handle"),
                "creator_name": evidence.get("creator_name"),
                "project_id": evidence.get("project_id"),
                "project_name": evidence.get("project_name"),
                "product_name": evidence.get("product_name"),
                "kol_pool_id": evidence.get("kol_pool_id"),
            },
            "layer1_visual_content": v2.get("layer1_visual_content") or {},
            "layer2_video_scores": v2.get("layer2_video_scores") or {},
            "layer3_integrated_judgment": layer3,
            "cost": {
                "recorded_cost_usd": cost,
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "segments": segments
                or [
                    {
                        "stage": "single_pass",
                        "provider": "gemini",
                        "model": model_name,
                        "cost_usd": cost,
                    }
                ],
                "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
                "latency_ms": latency_ms,
            },
            "raw_gemini_video": raw,
            "frame_extraction": raw.get("frame_extraction") if isinstance(raw.get("frame_extraction"), dict) else {},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    quality_scores = raw.get("quality_scores") if isinstance(raw.get("quality_scores"), dict) else {}
    return {
        "mock": False,
        "analysis_method": "gemini",
        "llm_execution": execution,
        "evaluation_only": execution["evaluation_only"],
        "production_authorized": execution["production_authorized"],
        "claim_status": execution["claim_status"],
        "job_id": job.get("id"),
        "target_type": "video",
        "target_id": str(evidence.get("id")),
        "source": {
            "url": evidence.get("content_url"),
            "title": evidence.get("title"),
            "platform": evidence.get("platform"),
            "creator_handle": evidence.get("creator_handle"),
            "project_id": evidence.get("project_id"),
            "kol_pool_id": evidence.get("kol_pool_id"),
        },
        "platform_algorithm_rules": {
            "content_genre": raw.get("content_genre"),
            "target_audience": raw.get("target_audience"),
            "hook_analysis": raw.get("hook_analysis"),
            "marketing_potential": raw.get("marketing_potential"),
            "brand_integration_depth": raw.get("brand_integration_depth"),
            "community_value": raw.get("community_value"),
            "quality_scores": quality_scores,
        },
        "weak_performance_reasons": {
            "quality_summary": raw.get("quality_summary"),
            "vertical_quality_notes": raw.get("vertical_quality_notes"),
            "marketing_notes": raw.get("marketing_notes"),
            "tech_floor": raw.get("tech_floor"),
            "low_scores": _low_scores(quality_scores),
        },
        "improvement_suggestions": raw.get("improvements") if isinstance(raw.get("improvements"), list) else [],
        "raw_gemini_video": raw,
        "cost": {
            "recorded_cost_usd": cost,
            "cost_basis": cost_basis,
            "preflight_estimated_cost_usd": preflight_cost,
            "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
            "latency_ms": latency_ms,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


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
                "claim_status": execution["claim_status"],
                "model_readiness_status": execution["model_readiness_status"],
                "base_derive_method": execution["base_derive_method"],
                "cache_derive_method": execution["cache_derive_method"],
            },
            extra_scopes=["monthly_total", "single_call", "provider:gemini"],
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
    if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS:
        ensure_final_v1_result_cacheable(raw)
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
    with conn.transaction():
        with conn.cursor() as cur:
            cache_id = upsert_video_analysis_cache(
                cur,
                target_type=target_type,
                target_id=target_id,
                model=str(raw.get("model") or raw.get("method") or "gemini_video"),
                derive_method=cache_derive_method,
                result_json=_json(shaped),
                cost=cost,
                triggered_by_user_id=_int_or_none(triggered_by),
                prompt_version=_cache_prompt_version(derive_method),
            )
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
                (job["id"],),
            )
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
        # QA P0 修:主成功路径补内容契合链式入队(此前只接在 cache-skip 路径,主路径漏接 → 发现的新人
        # 首次 final_v1 完成拿不到内容契合)。QA P1 修:连同 account_dossier 一并兜进 try/except,
        # 入队异常仅 warning、绝不冒泡把 final_v1 标 failed(『失败不阻断 final_v1』)。
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


def _process_gemini_video(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
) -> None:
    target_type, target_id = _target(payload)
    derive_method = _derive_method(payload)
    if target_type != "video":
        _block_job(conn, int(job["id"]), "unsupported_gemini_target_type", {"target_type": target_type})
        return
    evidence = _load_video_evidence(conn, target_id)
    platform = _platform_from_content_url(str(evidence.get("content_url") or ""))
    if platform == "unsupported":
        _block_job(conn, int(job["id"]), "unsupported_platform", {"source_url_host": _url_host(str(evidence.get("content_url") or ""))})
        return
    if (
        platform in {"instagram", "tiktok"}
        and derive_method not in GEMINI_VIDEO_V2_DERIVE_METHODS
        and derive_method not in GEMINI_VIDEO_FINAL_DERIVE_METHODS
    ):
        _block_job(conn, int(job["id"]), "unsupported_media_derive_method", {"platform": platform, "derive_method": derive_method})
        return
    if derive_method == FINAL_V1_KEYFRAME_QA_DERIVE_METHOD:
        _process_gemini_video_final_v1_keyframe_qa(conn, job, payload, evidence, preflight_cost)
        return
    if derive_method == "gemini_video_v2_flash_pro_judge":
        _process_gemini_video_flash_pro_judge(conn, job, payload, evidence, preflight_cost)
        return
    if derive_method == "gemini_video_v2_flash_gpt55_judge":
        _process_gemini_video_flash_gpt55_judge(conn, job, payload, evidence, preflight_cost)
        return
    if derive_method == "gemini_video_v2_flash_claude_judge":
        _process_gemini_video_flash_claude_judge(conn, job, payload, evidence, preflight_cost)
        return
    logger.info(
        "apify_jobs gemini video start | job_id=%s target_id=%s url=%s",
        job.get("id"),
        target_id,
        str(evidence.get("content_url") or "")[:120],
    )
    started = time.monotonic()
    clock = StageClock()
    # The child process forces every generateContent call to this exact model;
    # final_v1 receives the same one-model chain, so cache setup cannot drift.
    analyzer_payload = _gemini_analyzer_payload(payload, derive_method)
    authorization = (
        payload.get("_llm_execution")
        if isinstance(payload.get("_llm_execution"), dict)
        else {}
    )
    analyzer_payload["llm_context"] = {
        "purpose": "audit_video_analysis",
        "cost_tag": LLM_BUDGET_SCOPE,
        # 台账 staff 外键要的是 staff id;triggered_by_user_id 是 user id(owner staff 40 ↔ user 1),
        # 混传曾让 owner 从 UI 点的深析全部记账外键炸。优先 payload.staff_id,没有才退 user id。
        "triggered_by": payload.get("staff_id")
        or payload.get("triggered_by_user_id", payload.get("user_id")),
        "execution_class": str(
            authorization.get("execution_class") or WORKER_LLM_EXECUTION_CLASS
        ),
        "metadata": {
            "surface": "apify_jobs_worker",
            "task_binding": "audit_video_analysis",
            "parent_job_id": job.get("id"),
            "target_type": target_type,
            "target_id": target_id,
            "platform": platform,
            "phase": "video_analysis",
            "target_label": f"video:{target_id}",
        },
    }
    if platform in {"instagram", "tiktok"}:
        with tempfile.TemporaryDirectory(prefix="vkpi-analysis-video-") as tmpdir:
            with clock.stage("media_resolve"):
                resolved = _resolve_cached_or_provider_video(conn, evidence, tmpdir)
            if str(resolved.get("status") or "") == "blocked":
                _block_job(conn, int(job["id"]), str(resolved.get("reason") or "media_resolve_blocked"), resolved)
                return
            if not resolved.get("ok"):
                resolve_reason = str(resolved.get("reason") or "")
                # X6(2026-07-02 对齐 prod 热修):IG 图文帖 —— 抓取本身成功(scraped_ok)但没有可下载
                # 视频 URL(scraped_no_downloadable_url)= 这条内容就是图文没有视频,重试多少次也长不出
                # 视频。裸 raise 会进 retry/triage 白耗预算 → 直接转 blocked(image_post_no_video)。
                if platform == "instagram" and resolved.get("scraped_ok") and resolve_reason.endswith("scraped_no_downloadable_url"):
                    _block_job(conn, int(job["id"]), "image_post_no_video", resolved)
                    return
                # reason 已含 media_resolve_failed:<platform>:<真因>(见 _resolve_video_media 诚实化),
                # 不再二次包装成 "media_resolve_failed: media_resolve_failed",保留可诊断真因。
                raise RuntimeError(resolve_reason or f"media_resolve_failed:{platform}")
            if resolved.get("cache_hit"):
                download = {
                    "success": True,
                    "path": str(resolved.get("path") or ""),
                    "bytes": int(resolved.get("bytes") or 0),
                    "error": None,
                    "cache_hit": True,
                }
            else:
                with clock.stage("worker_download"):
                    download = download_direct_video_url(
                        str(resolved.get("direct_video_url") or ""),
                        tmpdir,
                        referer=str(evidence.get("content_url") or ""),
                    )
            if not download.get("success") or not download.get("path"):
                # Point 8: a terminal precheck verdict (404/403/410/451) means the
                # direct URL is confidently unavailable. Re-raise its bare reason —
                # which carries a content_* marker — instead of the
                # "direct_video_download_failed:" prefix, so _error_category routes
                # it to content_unavailable/_restricted/_blocked (a terminal,
                # non-retried bucket) rather than the retryable "download" bucket.
                if download.get("precheck_terminal"):
                    raise RuntimeError(str(download.get("error") or f"content unavailable: {platform}"))
                raise RuntimeError(f"direct_video_download_failed: {download.get('error') or platform}")
            media_provider_called = bool(resolved.get("provider_calls_performed"))
            if not _final_v1_scope_checkpoint(
                conn, job, payload, derive_method, provider_calls_performed=media_provider_called
            ):
                return
            local_schema = "final_v1" if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS else "v2"
            analysis_context = _video_final_context(evidence) if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS else _video_performance_context(evidence)
            analyzer_started = time.monotonic()
            raw = _run_gemini_analyzer_with_timeout(
                {
                    **analyzer_payload,
                    "mode": "local",
                    "video_path": str(download["path"]),
                    "title": str(evidence.get("title") or ""),
                    "creator_handle": str(evidence.get("creator_handle") or ""),
                    "schema_version": local_schema,
                    "performance_context": analysis_context,
                },
                job_id=job.get("id"),
                target_id=target_id,
                platform=platform,
            )
            clock.add("analyzer_subprocess", analyzer_started)
            # File API upload / per-model retries inside the child carry their own
            # stage gates; a child-reported revocation terminalizes here too.
            if not _final_v1_scope_checkpoint(
                conn, job, payload, derive_method, provider_calls_performed=True, raw=raw
            ):
                return
            raw["media_resolution"] = {
                "platform": platform,
                "source_url_host": _url_host(str(evidence.get("content_url") or "")),
                "direct_video_url_host": resolved.get("direct_video_url_host"),
                "status": resolved.get("status"),
                "cache_hit": bool(resolved.get("cache_hit")),
                "cache_source": resolved.get("cache_source"),
                "cache_asset_id": resolved.get("cache_asset_id"),
                "cache_lookup_reason": resolved.get("cache_lookup_reason"),
            }
            raw["local_video_input"] = {
                "download_bytes": int(download.get("bytes") or 0),
                "temporary_files_cleaned": True,
                "download_error": download.get("error"),
            }
            if not resolved.get("cache_hit"):
                # C3:临时目录删除前,把这份已下载、已被 Gemini 分析的字节登记进视频缓存(传 R2),
                # 令 KOL 详情页 cached_video_url 有值、视频可播。已命中缓存时不重复上传。
                with clock.stage("r2_warm"):
                    _warm_video_to_r2_from_local(
                        job_id=job.get("id"),
                        platform=platform,
                        content_url=str(evidence.get("content_url") or ""),
                        direct_video_url=str(resolved.get("direct_video_url") or ""),
                        local_path=str(download.get("path") or ""),
                    )
    else:
        analysis_context = _video_final_context(evidence) if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS else _video_performance_context(evidence)
        analyzer_started = time.monotonic()
        raw = _run_gemini_analyzer_with_timeout(
            {
                **analyzer_payload,
                "mode": "youtube",
                "url": str(evidence.get("content_url") or ""),
                "title": str(evidence.get("title") or ""),
                "creator_handle": str(evidence.get("creator_handle") or ""),
                "schema_version": "final_v1"
                if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS
                else ("v2" if derive_method in GEMINI_VIDEO_V2_DERIVE_METHODS else "legacy"),
                "performance_context": analysis_context
                if derive_method in GEMINI_VIDEO_V2_DERIVE_METHODS or derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS
                else None,
            },
            job_id=job.get("id"),
            target_id=target_id,
            platform=platform,
        )
        clock.add("analyzer_subprocess", analyzer_started)
    latency_ms = int((time.monotonic() - started) * 1000)
    reported_model = str(raw.get("model") or "")
    if raw.get("analyzed") and reported_model != WORKER_GEMINI_MODEL:
        raise RuntimeError(
            "model_binding_mismatch:"
            f"expected=google/{WORKER_GEMINI_MODEL}:reported={reported_model or 'missing'}"
        )
    # YouTube children gate subtitles / direct attempts / download / File API
    # themselves and report ``scope_revoked``; the parent re-checks before writes.
    if not _final_v1_scope_checkpoint(
        conn, job, payload, derive_method, provider_calls_performed=True, raw=raw
    ):
        return
    raw["llm_execution"] = {
        **authorization,
        "binding": f"google/{WORKER_GEMINI_MODEL}",
        "model": WORKER_GEMINI_MODEL,
        "reported_model": reported_model or None,
        "model_match": bool(reported_model == WORKER_GEMINI_MODEL),
    }
    logger.info(
        "apify_jobs gemini video returned | job_id=%s analyzed=%s method=%s latency_ms=%s",
        job.get("id"),
        bool(raw.get("analyzed")),
        raw.get("method"),
        latency_ms,
    )
    validation_error: InvalidFinalV1ResultError | None = None
    if derive_method in GEMINI_VIDEO_FINAL_DERIVE_METHODS and raw.get("analyzed"):
        try:
            ensure_final_v1_result_cacheable(raw)
        except InvalidFinalV1ResultError as exc:
            validation_error = exc
    cost, cost_basis, tokens_in, tokens_out = _authoritative_gemini_cost(
        raw,
        preflight_cost,
    )
    with clock.stage("cost_record"):
        ledger = _record_gemini_cost(
            job=job,
            payload=payload,
            raw=raw,
            cost=cost,
            cost_basis=cost_basis,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            preflight_cost=preflight_cost,
        )
    # worker 侧阶段(媒体解析/下载/子进程/R2/记账)并入 raw → 随 raw_gemini_video 与 cost.stage_timings_ms 落库
    raw["worker_stage_timings_ms"] = dict(clock.timings)
    if validation_error is not None:
        # 契约校验失败(含截断续写后仍不完整)也要把 truncation / retries / 直链诊断落库,否则查不到真因
        record_final_v1_outcome_diagnostics(
            conn, job_id=int(job["id"]), raw=raw, clock=clock, platform=platform, error=str(validation_error)
        )
        raise validation_error
    if not raw.get("analyzed"):
        raw_error = str(raw.get("error") or "not_analyzed")
        # 失败也落阶段耗时 + 直链/下载诊断(payload.diagnostics),否则 raw 随异常丢失、失败原因永远查不到
        record_final_v1_outcome_diagnostics(conn, job_id=int(job["id"]), raw=raw, clock=clock, platform=platform, error=raw_error)
        if raw_error == "gemini_call_timeout":
            raise RuntimeError("gemini_call_timeout")
        raise RuntimeError(f"Gemini video analysis failed: {raw_error}")
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    triggered_by_user_id = _int_or_none(triggered_by)
    shaped = _shape_gemini_result(
        job=job,
        evidence=evidence,
        raw={**raw, "ledger": ledger},
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
    persist_started = time.monotonic()
    with conn.transaction():
        with conn.cursor() as cur:
            cache_id = upsert_video_analysis_cache(
                cur,
                target_type=target_type,
                target_id=target_id,
                model=str(raw.get("model") or raw.get("method") or "gemini_video"),
                derive_method=cache_derive_method,
                result_json=_json(shaped),
                cost=cost,
                triggered_by_user_id=triggered_by_user_id,
                prompt_version=_cache_prompt_version(derive_method),
            )
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
                (job["id"],),
            )
    clock.add("persist", persist_started)
    followups_started = time.monotonic()
    if execution["evaluation_only"]:
        # Local evidence is physically/logically isolated from production
        # final_v1 and never seeds deep-result, dossier, fit, QA or judge
        # follow-ups.  The URL session receives only a labelled pointer so the
        # UI can show the result without promoting it into production facts.
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
        # QA P0 修:主成功路径补内容契合链式入队(此前只接在 cache-skip 路径,主路径漏接 → 发现的新人
        # 首次 final_v1 完成拿不到内容契合)。QA P1 修:连同 account_dossier 一并兜进 try/except,
        # 入队异常仅 warning、绝不冒泡把 final_v1 标 failed(『失败不阻断 final_v1』)。
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
    clock.add("followups", followups_started)
    record_final_v1_outcome_diagnostics(conn, job_id=int(job["id"]), raw=raw, clock=clock, platform=platform)


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
