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
from psycopg.rows import dict_row

from app.core.gemini_models import VISUAL_PASS_MODEL
from app.core.logging import get_logger
from app.core.model_registry import CLAUDE_OPUS_EXACT_MODEL
from app.domains.kol.video_keyframe_qa_enqueue import (
    final_v1_payload_from_cache_result,
    final_v1_payload_sha256,
)
from app.workers.apify_jobs_worker_helpers import _platform_from_content_url
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer
from app.workers.apify_jobs_cost import (
    _anthropic_cost,
    _authoritative_gemini_cost,
    _gemini_cost,
    _openai_cost,
)
from app.workers.apify_jobs_video_context import (
    _video_final_context,
    _video_performance_context,
)


logger = get_logger(__name__)


class KeyframeQaSourceError(RuntimeError):
    """The queued review no longer points at the exact ready final_v1 source."""


def _load_ready_final_v1_source_for_qa(
    conn: psycopg.Connection[Any],
    *,
    payload: dict[str, Any],
    evidence_id: int,
) -> dict[str, Any]:
    cache_id = int(payload.get("source_final_v1_cache_id") or 0)
    expected_sha = str(payload.get("source_final_v1_sha256") or "").strip().lower()
    if cache_id <= 0 or len(expected_sha) != 64:
        raise KeyframeQaSourceError("keyframe_qa_source_fence_required")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, target_id, model, result, prompt_version, updated_at
            FROM vkpi_analysis_cache
            WHERE id=%s AND target_type='video' AND target_id=%s
              AND derive_method='video_analysis_final_v1' AND status='ready'
              AND id=(
                  SELECT latest.id FROM vkpi_analysis_cache latest
                  WHERE latest.target_type='video' AND latest.target_id=%s
                    AND latest.derive_method='video_analysis_final_v1' AND latest.status='ready'
                  ORDER BY latest.updated_at DESC, latest.id DESC LIMIT 1
              )
            LIMIT 1
            """,
            (cache_id, str(int(evidence_id)), str(int(evidence_id))),
        )
        row = cur.fetchone()
    if not row:
        raise KeyframeQaSourceError("keyframe_qa_source_not_ready")
    item = dict(row)
    final_v1 = final_v1_payload_from_cache_result(item.get("result"))
    if not final_v1:
        raise KeyframeQaSourceError("keyframe_qa_source_invalid")
    actual_sha = final_v1_payload_sha256(final_v1)
    if actual_sha != expected_sha:
        raise KeyframeQaSourceError("keyframe_qa_source_drifted")
    return {
        "cache_id": int(item["id"]),
        "model": str(item.get("model") or ""),
        "prompt_version": str(item.get("prompt_version") or "") or None,
        "updated_at": item.get("updated_at"),
        "payload_sha256": actual_sha,
        "final_v1": final_v1,
    }


def _keyframe_qa_scope_checkpoint(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    *,
    provider_calls_performed: bool,
) -> bool:
    from app.db.connection import db_connection_sync_scope
    from app.workers.apify_jobs_worker_paid_scope import final_v1_scope_checkpoint

    return final_v1_scope_checkpoint(
        conn,
        job,
        payload,
        FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
        provider_calls_performed=provider_calls_performed,
        block_job=_block_job,
        connection_scope=db_connection_sync_scope,
    )


def _keyframe_qa_provider_calls_performed(raw: dict[str, Any]) -> bool:
    """Use analyzer truth; fall back to the strict-attempt ledger for old rows/tests."""
    if raw.get("provider_calls_performed") is True:
        return True
    attempts = raw.get("llm_attempts") if isinstance(raw.get("llm_attempts"), list) else []
    provider_states = {"settled", "model_mismatch", "usage_missing", "unknown"}
    return any(
        str(item.get("state") or "").strip().lower() in provider_states
        for item in attempts
        if isinstance(item, dict)
    )


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

    if not _keyframe_qa_scope_checkpoint(
        conn,
        job,
        payload,
        provider_calls_performed=False,
    ):
        return
    try:
        source = _load_ready_final_v1_source_for_qa(
            conn,
            payload=payload,
            evidence_id=int(evidence.get("id") or 0),
        )
    except KeyframeQaSourceError as exc:
        _block_job(
            conn,
            int(job["id"]),
            str(exc),
            {
                "stage": "keyframe_qa_source",
                "provider_calls_performed": False,
                "source_final_v1_cache_id": payload.get("source_final_v1_cache_id"),
            },
        )
        return

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
    qa_preflight_cost = qa_estimated_cost if qa_estimated_cost > 0 else max(0.0, float(preflight_cost or 0.0))

    started = time.monotonic()
    analysis_context = _video_final_context(evidence)
    final_v1 = source["final_v1"]
    layer1 = final_v1.get("layer1_visual_content") if isinstance(final_v1.get("layer1_visual_content"), dict) else {}
    with _extract_keyframes_for_qa(evidence, layer1, limit=6, temp_prefix="vkpi-final-v1-qa-video-") as qa_frames:
        keyframe_requests = qa_frames["keyframe_requests"]
        frame_meta = qa_frames["frame_meta"]
        download = qa_frames["download"]
        if not _keyframe_qa_scope_checkpoint(
            conn,
            job,
            payload,
            provider_calls_performed=False,
        ):
            return
        qa_raw = asyncio.run(
            gemini_video_analyzer.analyze_final_v1_keyframe_qa(
                final_v1_result=final_v1,
                keyframes=qa_frames["frames"],
                title=str(evidence.get("title") or ""),
                performance_context=analysis_context,
                model_name=qa_model,
                llm_context=_judge_llm_context(job, payload, evidence, stage="keyframe_qa"),
            )
        )

    qa_provider_calls_performed = _keyframe_qa_provider_calls_performed(qa_raw)
    if not _keyframe_qa_scope_checkpoint(
        conn,
        job,
        payload,
        provider_calls_performed=qa_provider_calls_performed,
    ):
        return
    if not qa_provider_calls_performed:
        # Local capability/keyframe early returns never reached Google.  Do not
        # recheck as a post-provider write and do not manufacture preflight cost.
        raise RuntimeError(
            "Gemini final_v1 keyframe QA failed before provider call: "
            f"{qa_raw.get('error') or 'provider_call_not_performed'}"
        )
    # The main analysis may have been replaced while the provider call was in
    # flight.  Recheck its exact cache id + payload hash before publishing QA.
    try:
        source = _load_ready_final_v1_source_for_qa(
            conn, payload=payload, evidence_id=int(evidence.get("id") or 0)
        )
    except KeyframeQaSourceError as exc:
        _block_job(
            conn,
            int(job["id"]),
            str(exc),
            {
                "stage": "keyframe_qa_source_recheck",
                "provider_calls_performed": qa_provider_calls_performed,
            },
        )
        return
    qa_cost, qa_basis, qa_tokens_in, qa_tokens_out = _authoritative_gemini_cost(
        qa_raw, qa_preflight_cost
    )
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=qa_raw,
        cost=qa_cost,
        cost_basis=qa_basis,
        tokens_in=qa_tokens_in,
        tokens_out=qa_tokens_out,
        latency_ms=0,
        preflight_cost=qa_preflight_cost,
    )
    if not qa_raw.get("analyzed"):
        raise RuntimeError(f"Gemini final_v1 keyframe QA failed: {qa_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(qa_cost, 6)
    execution = payload.get("_llm_execution") if isinstance(payload.get("_llm_execution"), dict) else {}
    combined_raw = {
        "analyzed": True,
        "status": "completed",
        "provider_calls_performed": qa_provider_calls_performed,
        "method": "final_v1_ready_cache_keyframe_qa",
        "model": str(qa_raw.get("model") or qa_model),
        "video_analysis_final_v1": final_v1,
        "final_v1_pass": {
            "reused_ready_cache": True,
            "provider_calls_performed": False,
            "source_target_id": str(int(evidence.get("id") or 0)),
            "source_cache_id": source["cache_id"],
            "source_model": source.get("model"),
            "source_prompt_version": source.get("prompt_version"),
            "source_payload_sha256": source["payload_sha256"],
        },
        "final_v1_keyframe_qa": qa_raw.get("final_v1_keyframe_qa") if isinstance(qa_raw.get("final_v1_keyframe_qa"), dict) else {},
        "qa_pass": qa_raw.get("qa_pass"),
        "qa_method": qa_raw.get("method"),
        "qa_model": qa_raw.get("model") or qa_model,
        "qa_usage_metadata": qa_raw.get("usage_metadata") if isinstance(qa_raw.get("usage_metadata"), dict) else {},
        "usage_metadata": qa_raw.get("usage_metadata") if isinstance(qa_raw.get("usage_metadata"), dict) else {},
        "llm_attempts": qa_raw.get("llm_attempts") if isinstance(qa_raw.get("llm_attempts"), list) else [],
        "cost_authority": qa_raw.get("cost_authority"),
        "llm_execution": {
            **execution,
            "binding": f"google/{qa_model}",
            "model": qa_model,
            "reported_model": str(qa_raw.get("model") or qa_model),
            "base_derive_method": FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
            "cache_derive_method": FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
        },
        "cost_segments": [
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
        cost_basis=qa_basis,
        preflight_cost=qa_preflight_cost,
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
    FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
    FINAL_V1_KEYFRAME_QA_MODEL,
    LLM_BUDGET_SCOPE,
    _block_job,
    _extract_keyframes_for_qa,
    _gemini_worker_overrides,
    _log_budget_preflight_record_only,
    _provider_allowed,
    _provider_budget_preflight,
)
