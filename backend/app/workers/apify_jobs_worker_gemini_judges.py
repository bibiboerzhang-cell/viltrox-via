"""Gemini 视频多 pass 评审处理簇(keyframe QA / flash+pro / flash+gpt55 / flash+claude),
从 apify_jobs_worker_gemini.py 整簇 move 出来。

函数体逐字不变 → 行为必然不变。原 gemini 模块用
`from app.workers.apify_jobs_worker_gemini_judges import (...)` re-export 兜住所有调用点。
成本入账/落库(_record_*_cost / _write_gemini_cache)从 apify_jobs_worker_gemini 在本模块
**底部** lazy import(放底部避免循环导入:此时本簇函数已定义,gemini 模块也已先于其 re-export
行绑定本簇函数);其余常量/小工具从各自中性模块取。红线:本簇零 fit 写;LLM 绝不写
viltrox_fit_score。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import psycopg

from app.core.gemini_models import VISUAL_PASS_MODEL
from app.core.logging import get_logger
from app.core.model_registry import CLAUDE_OPUS_EXACT_MODEL
from app.workers.apify_jobs_worker_helpers import _platform_from_content_url
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer
from app.workers.apify_jobs_cost import (
    _anthropic_cost,
    _gemini_cost,
    _openai_cost,
)
from app.workers.apify_jobs_video_context import (
    _video_final_context,
    _video_performance_context,
)


logger = get_logger(__name__)


def _judge_llm_context(
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    *,
    stage: str,
) -> dict[str, Any]:
    """台账归属上下文(C3 收口后裁判/QA 经 llm_production,需要 cost_tag/triggered_by)。

    与 apify_jobs_worker_gemini 视频主通道同口径:cost_tag=LLM_BUDGET_SCOPE,
    triggered_by 优先 payload.staff_id(台账 staff 外键),否则退 user id。
    """
    return {
        "purpose": stage,
        "cost_tag": LLM_BUDGET_SCOPE,
        "triggered_by": payload.get("staff_id")
        or payload.get("triggered_by_user_id", payload.get("user_id")),
        "metadata": {
            "surface": "apify_jobs_worker",
            "parent_job_id": job.get("id"),
            "target_type": "evidence",
            "target_id": evidence.get("id"),
            "platform": _platform_from_content_url(str(evidence.get("content_url") or "")),
            "phase": "video_analysis",
            "target_label": f"video:{evidence.get('id')}",
        },
    }


def _process_gemini_video_final_v1_keyframe_qa(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("video_analysis_final_v1_keyframe_qa currently supports YouTube only")

    qa_model = str(payload.get("final_v1_qa_model") or FINAL_V1_KEYFRAME_QA_MODEL).strip() or FINAL_V1_KEYFRAME_QA_MODEL
    qa_preflight = _provider_budget_preflight(
        job,
        {
            **payload,
            "prompt": f"final_v1 keyframe QA video:{evidence.get('id')} model:{qa_model}",
        },
        "google",
    )
    qa_allowed, qa_reason, qa_estimated_cost = _provider_allowed(qa_preflight, "google")
    _log_budget_preflight_record_only(
        job=job,
        provider="google",
        allowed=qa_allowed,
        reason=qa_reason,
        estimated_cost=qa_estimated_cost,
        stage="keyframe_qa",
    )
    if not qa_allowed:
        # 护栏② enforce:撞 cap 不再继续——_block_job 终态(对齐 cron fallback_action=block_job)
        _block_job(
            conn,
            int(job["id"]),
            "budget_guard_blocked",
            {
                "provider": "google",
                "stage": "keyframe_qa",
                "reason_detail": qa_reason,
                "estimated_cost_usd": qa_estimated_cost,
            },
        )
        return

    started = time.monotonic()
    analysis_context = _video_final_context(evidence)
    analyzer_payload = {
        **payload,
        "gemini_final_v1_models": gemini_video_analyzer.final_v1_gemini_models(
            payload.get("gemini_final_v1_models") or FINAL_V1_GEMINI_MODELS
        ),
    }
    visual_raw = _run_gemini_analyzer_with_timeout(
        {
            **analyzer_payload,
            "mode": "youtube",
            "url": str(evidence.get("content_url") or ""),
            "title": str(evidence.get("title") or ""),
            "creator_handle": str(evidence.get("creator_handle") or ""),
            "schema_version": "final_v1",
            "performance_context": analysis_context,
        },
        job_id=job.get("id"),
        target_id=str(evidence.get("id")),
        platform="youtube",
    )
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raw_error = str(visual_raw.get("error") or "not_analyzed")
        if raw_error == "gemini_call_timeout":
            raise RuntimeError("gemini_call_timeout")
        raise RuntimeError(f"Gemini final_v1 pass failed: {raw_error}")

    final_v1 = visual_raw.get("video_analysis_final_v1") if isinstance(visual_raw.get("video_analysis_final_v1"), dict) else {}
    layer1 = final_v1.get("layer1_visual_content") if isinstance(final_v1.get("layer1_visual_content"), dict) else {}
    with _extract_keyframes_for_qa(evidence, layer1, limit=6, temp_prefix="vkpi-final-v1-qa-video-") as qa_frames:
        keyframe_requests = qa_frames["keyframe_requests"]
        frame_meta = qa_frames["frame_meta"]
        download = qa_frames["download"]
        qa_raw = asyncio.run(
            gemini_video_analyzer.analyze_final_v1_keyframe_qa(
                final_v1_result=final_v1,
                keyframes=qa_frames["frames"],
                title=str(evidence.get("title") or ""),
                performance_context=analysis_context,
                model_name=qa_model,
                llm_context=_judge_llm_context(job, payload, evidence, stage="final_v1_keyframe_qa"),
            )
        )

    qa_cost, qa_basis, qa_tokens_in, qa_tokens_out = _gemini_cost(qa_raw, qa_estimated_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=qa_raw,
        cost=qa_cost,
        cost_basis=qa_basis,
        tokens_in=qa_tokens_in,
        tokens_out=qa_tokens_out,
        latency_ms=0,
        preflight_cost=qa_estimated_cost,
    )
    if not qa_raw.get("analyzed"):
        raise RuntimeError(f"Gemini final_v1 keyframe QA failed: {qa_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + qa_cost, 6)
    visual_model = str(visual_raw.get("model") or visual_raw.get("method") or "final_v1_gemini")
    combined_raw = {
        **visual_raw,
        "method": "final_v1_flash_keyframe_qa",
        "model": f"{visual_model}+{qa_model}",
        "final_v1_pass": visual_raw,
        "final_v1_keyframe_qa": qa_raw.get("final_v1_keyframe_qa") if isinstance(qa_raw.get("final_v1_keyframe_qa"), dict) else {},
        "qa_pass": qa_raw.get("qa_pass"),
        "qa_method": qa_raw.get("method"),
        "qa_model": qa_raw.get("model") or qa_model,
        "qa_usage_metadata": qa_raw.get("usage_metadata") if isinstance(qa_raw.get("usage_metadata"), dict) else {},
        "cost_segments": [
            {
                "stage": "final_v1_video_pass",
                "provider": "gemini",
                "model": visual_model,
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "keyframe_qa_pass",
                "provider": "gemini",
                "model": qa_model,
                "cost_usd": qa_cost,
                "cost_basis": qa_basis,
                "usage_metadata": qa_raw.get("usage_metadata") if isinstance(qa_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=combined_raw,
        cost=total_cost,
        cost_basis="gemini_final_v1_keyframe_qa_segmented_model_rate",
        preflight_cost=preflight_cost + qa_estimated_cost,
        latency_ms=latency_ms,
        derive_method=FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
    )


def _process_gemini_video_flash_pro_judge(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("gemini_video_v2_flash_pro_judge currently supports YouTube only")
    started = time.monotonic()
    performance = _video_performance_context(evidence)
    visual_payload = {**payload, "gemini_model": VISUAL_PASS_MODEL}
    with _gemini_worker_overrides(visual_payload):
        visual_raw = asyncio.run(
            gemini_video_analyzer.analyze_youtube_with_gemini(
                str(evidence.get("content_url") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("creator_handle") or ""),
                schema_version="v2",
                performance_context=performance,
            )
        )
    if visual_raw.get("analyzed"):
        visual_raw["model"] = VISUAL_PASS_MODEL
        visual_raw["method"] = f"gemini_direct_{VISUAL_PASS_MODEL}"
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raise RuntimeError(f"Gemini visual pass failed: {visual_raw.get('error') or 'not_analyzed'}")

    v2 = visual_raw.get("video_analysis_v2") if isinstance(visual_raw.get("video_analysis_v2"), dict) else {}
    layer1 = v2.get("layer1_visual_content") if isinstance(v2.get("layer1_visual_content"), dict) else {}
    with _extract_keyframes_for_qa(evidence, layer1, limit=6, temp_prefix="vkpi-scheme2-video-") as qa_frames:
        keyframe_requests = qa_frames["keyframe_requests"]
        frame_meta = qa_frames["frame_meta"]
        download = qa_frames["download"]
        judgment_raw = asyncio.run(
            gemini_video_analyzer.analyze_v2_judgment_with_keyframes(
                layer1_visual_content=layer1,
                keyframes=qa_frames["frames"],
                title=str(evidence.get("title") or ""),
                performance_context=performance,
                model_name=FINAL_V1_KEYFRAME_QA_MODEL,
                llm_context=_judge_llm_context(job, payload, evidence, stage="keyframe_judgment"),
            )
        )
    judgment_cost, judgment_basis, judgment_tokens_in, judgment_tokens_out = _gemini_cost(judgment_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=judgment_raw,
        cost=judgment_cost,
        cost_basis=judgment_basis,
        tokens_in=judgment_tokens_in,
        tokens_out=judgment_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not judgment_raw.get("analyzed"):
        raise RuntimeError(f"Gemini keyframe judgment failed: {judgment_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + judgment_cost, 6)
    raw = {
        **judgment_raw,
        "method": "gemini_flash_pro_judge",
        "model": f"{VISUAL_PASS_MODEL}+{FINAL_V1_KEYFRAME_QA_MODEL}",
        "visual_pass": visual_raw,
        "cost_segments": [
            {
                "stage": "visual_video_pass",
                "provider": "gemini",
                "model": VISUAL_PASS_MODEL,
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "judgment_pass",
                "provider": "gemini",
                "model": FINAL_V1_KEYFRAME_QA_MODEL,
                "cost_usd": judgment_cost,
                "cost_basis": judgment_basis,
                "usage_metadata": judgment_raw.get("usage_metadata") if isinstance(judgment_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=raw,
        cost=total_cost,
        cost_basis="gemini_usage_metadata_segmented_model_rate",
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
        derive_method="gemini_video_v2_flash_pro_judge",
    )


def _process_gemini_video_flash_gpt55_judge(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("gemini_video_v2_flash_gpt55_judge currently supports YouTube only")
    openai_preflight = _provider_budget_preflight(job, payload, "openai")
    openai_allowed, openai_reason, openai_estimated_cost = _provider_allowed(openai_preflight, "openai")
    _log_budget_preflight_record_only(
        job=job,
        provider="openai",
        allowed=openai_allowed,
        reason=openai_reason,
        estimated_cost=openai_estimated_cost,
        stage="openai_keyframe_judge",
    )
    if not openai_allowed:
        _block_job(
            conn,
            int(job["id"]),
            "budget_guard_blocked",
            {
                "provider": "openai",
                "stage": "openai_keyframe_judge",
                "reason_detail": openai_reason,
                "estimated_cost_usd": openai_estimated_cost,
            },
        )
        return

    started = time.monotonic()
    performance = _video_performance_context(evidence)
    visual_payload = {**payload, "gemini_model": VISUAL_PASS_MODEL}
    with _gemini_worker_overrides(visual_payload):
        visual_raw = asyncio.run(
            gemini_video_analyzer.analyze_youtube_with_gemini(
                str(evidence.get("content_url") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("creator_handle") or ""),
                schema_version="v2",
                performance_context=performance,
            )
        )
    if visual_raw.get("analyzed"):
        visual_raw["model"] = VISUAL_PASS_MODEL
        visual_raw["method"] = f"gemini_direct_{VISUAL_PASS_MODEL}"
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raise RuntimeError(f"Gemini visual pass failed: {visual_raw.get('error') or 'not_analyzed'}")

    v2 = visual_raw.get("video_analysis_v2") if isinstance(visual_raw.get("video_analysis_v2"), dict) else {}
    layer1 = v2.get("layer1_visual_content") if isinstance(v2.get("layer1_visual_content"), dict) else {}
    with _extract_keyframes_for_qa(evidence, layer1, limit=6, temp_prefix="vkpi-scheme3a-video-") as qa_frames:
        keyframe_requests = qa_frames["keyframe_requests"]
        frame_meta = qa_frames["frame_meta"]
        download = qa_frames["download"]
        judgment_raw = asyncio.run(
            gemini_video_analyzer.analyze_v2_judgment_with_openai_keyframes(
                layer1_visual_content=layer1,
                keyframes=qa_frames["frames"],
                title=str(evidence.get("title") or ""),
                performance_context=performance,
                model_name="gpt-5.5",
                llm_context=_judge_llm_context(job, payload, evidence, stage="openai_keyframe_judge"),
            )
        )
    judgment_cost, judgment_basis, judgment_tokens_in, judgment_tokens_out = _openai_cost(judgment_raw, openai_estimated_cost)
    _record_openai_cost(
        job=job,
        payload=payload,
        raw=judgment_raw,
        cost=judgment_cost,
        cost_basis=judgment_basis,
        tokens_in=judgment_tokens_in,
        tokens_out=judgment_tokens_out,
        latency_ms=0,
        preflight_cost=openai_estimated_cost,
    )
    if not judgment_raw.get("analyzed"):
        raise RuntimeError(f"OpenAI keyframe judgment failed: {judgment_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + judgment_cost, 6)
    raw = {
        **judgment_raw,
        "method": "gemini_flash_gpt55_judge",
        "model": f"{VISUAL_PASS_MODEL}+gpt-5.5",
        "visual_pass": visual_raw,
        "cost_segments": [
            {
                "stage": "visual_video_pass",
                "provider": "gemini",
                "model": VISUAL_PASS_MODEL,
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "judgment_pass",
                "provider": "openai",
                "model": "gpt-5.5",
                "cost_usd": judgment_cost,
                "cost_basis": judgment_basis,
                "usage_metadata": judgment_raw.get("usage_metadata") if isinstance(judgment_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=raw,
        cost=total_cost,
        cost_basis="gemini_openai_usage_metadata_segmented_model_rate",
        preflight_cost=preflight_cost + openai_estimated_cost,
        latency_ms=latency_ms,
        derive_method="gemini_video_v2_flash_gpt55_judge",
    )


def _process_gemini_video_flash_claude_judge(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("gemini_video_v2_flash_claude_judge currently supports YouTube only")
    anthropic_preflight = _provider_budget_preflight(job, payload, "anthropic")
    anthropic_allowed, anthropic_reason, anthropic_estimated_cost = _provider_allowed(anthropic_preflight, "anthropic")
    _log_budget_preflight_record_only(
        job=job,
        provider="anthropic",
        allowed=anthropic_allowed,
        reason=anthropic_reason,
        estimated_cost=anthropic_estimated_cost,
        stage="anthropic_keyframe_judge",
    )
    if not anthropic_allowed:
        _block_job(
            conn,
            int(job["id"]),
            "budget_guard_blocked",
            {
                "provider": "anthropic",
                "stage": "anthropic_keyframe_judge",
                "reason_detail": anthropic_reason,
                "estimated_cost_usd": anthropic_estimated_cost,
            },
        )
        return

    started = time.monotonic()
    performance = _video_performance_context(evidence)
    visual_payload = {**payload, "gemini_model": VISUAL_PASS_MODEL}
    with _gemini_worker_overrides(visual_payload):
        visual_raw = asyncio.run(
            gemini_video_analyzer.analyze_youtube_with_gemini(
                str(evidence.get("content_url") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("creator_handle") or ""),
                schema_version="v2",
                performance_context=performance,
            )
        )
    if visual_raw.get("analyzed"):
        visual_raw["model"] = VISUAL_PASS_MODEL
        visual_raw["method"] = f"gemini_direct_{VISUAL_PASS_MODEL}"
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raise RuntimeError(f"Gemini visual pass failed: {visual_raw.get('error') or 'not_analyzed'}")

    v2 = visual_raw.get("video_analysis_v2") if isinstance(visual_raw.get("video_analysis_v2"), dict) else {}
    layer1 = v2.get("layer1_visual_content") if isinstance(v2.get("layer1_visual_content"), dict) else {}
    with _extract_keyframes_for_qa(evidence, layer1, limit=6, temp_prefix="vkpi-scheme3b-video-") as qa_frames:
        keyframe_requests = qa_frames["keyframe_requests"]
        frame_meta = qa_frames["frame_meta"]
        download = qa_frames["download"]
        judgment_raw = asyncio.run(
            gemini_video_analyzer.analyze_v2_judgment_with_anthropic_keyframes(
                layer1_visual_content=layer1,
                keyframes=qa_frames["frames"],
                title=str(evidence.get("title") or ""),
                performance_context=performance,
                model_name=CLAUDE_OPUS_EXACT_MODEL,
                llm_context=_judge_llm_context(job, payload, evidence, stage="anthropic_keyframe_judge"),
            )
        )
    judgment_cost, judgment_basis, judgment_tokens_in, judgment_tokens_out = _anthropic_cost(judgment_raw, anthropic_estimated_cost)
    _record_anthropic_cost(
        job=job,
        payload=payload,
        raw=judgment_raw,
        cost=judgment_cost,
        cost_basis=judgment_basis,
        tokens_in=judgment_tokens_in,
        tokens_out=judgment_tokens_out,
        latency_ms=0,
        preflight_cost=anthropic_estimated_cost,
    )
    if not judgment_raw.get("analyzed"):
        raise RuntimeError(f"Anthropic keyframe judgment failed: {judgment_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + judgment_cost, 6)
    raw = {
        **judgment_raw,
        "method": "gemini_flash_claude_judge",
        "model": f"{VISUAL_PASS_MODEL}+{CLAUDE_OPUS_EXACT_MODEL}",
        "visual_pass": visual_raw,
        "cost_segments": [
            {
                "stage": "visual_video_pass",
                "provider": "gemini",
                "model": VISUAL_PASS_MODEL,
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "judgment_pass",
                "provider": "anthropic",
                "model": CLAUDE_OPUS_EXACT_MODEL,
                "cost_usd": judgment_cost,
                "cost_basis": judgment_basis,
                "usage_metadata": judgment_raw.get("usage_metadata") if isinstance(judgment_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=raw,
        cost=total_cost,
        cost_basis="gemini_anthropic_usage_metadata_segmented_model_rate",
        preflight_cost=preflight_cost + anthropic_estimated_cost,
        latency_ms=latency_ms,
        derive_method="gemini_video_v2_flash_claude_judge",
    )


# 成本入账/落库:仍归 apify_jobs_worker_gemini(本簇被它 re-export);放底部 import 避免循环导入
# (调用点均在函数体内、运行期才解析)。
from app.workers.apify_jobs_worker_gemini import (  # noqa: E402
    _record_anthropic_cost,
    _record_gemini_cost,
    _record_openai_cost,
    _write_gemini_cache,
)

# 原 worker 留下的常量/小工具:放模块底部 import(避免循环导入;调用点均在函数体内、运行期才解析)。
from app.workers.apify_jobs_worker import (  # noqa: E402
    FINAL_V1_GEMINI_MODELS,
    FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
    FINAL_V1_KEYFRAME_QA_MODEL,
    LLM_BUDGET_SCOPE,
    _block_job,
    _extract_keyframes_for_qa,
    _gemini_worker_overrides,
    _log_budget_preflight_record_only,
    _provider_allowed,
    _provider_budget_preflight,
    _run_gemini_analyzer_with_timeout,
)
