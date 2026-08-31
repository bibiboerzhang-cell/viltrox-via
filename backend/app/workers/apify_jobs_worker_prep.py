"""LLM 预算 preflight 簇 + keyframe 抽帧/Gemini override 簇,从 apify_jobs_worker.py 整簇 move 出来。

函数体逐字不变 → 行为必然不变;原文件 re-export 兜住所有调用点(含下划线私有名)。
共享常量(LLM_MAX_OUTPUT_TOKENS / LLM_BUDGET_SCOPE / WORKER_GEMINI_MODEL 等)从 config 叶子
顶部 import(2026-08-30 拆 import 期环:原底部回环 import 已删)。红线:本簇零 fit 写。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from app.core.logging import get_logger
from app.core.video_model_chain import (
    analyzer_model_chain,
    model_fallback_candidates,
    ready_model_subchain,
)
from app.db.connection import db_connection_sync_scope
# 预算/围栏/本地算力授权簇的三个叶子(2026-08-31 fan-out 刀从 apify_jobs_worker.py 顶层搬来)。
# 本模块是该簇的内聚归口(_llm_budget_preflight / _provider_budget_preflight /
# _google_execution_authorization / _google_allowed / _provider_allowed 都在这);
# worker 侧改为同名 re-export,apify_jobs_worker_execution 与 apify_jobs_worker_runtime
# 的 namespace["..."] / deps["..."] 取用路径与取到的对象逐字不变。
from app.domains.costs import budget_guard  # noqa: F401  仅经 worker 同名 re-export 对外
from app.platform import llm_gateway
from app.platform.apify_budget import (  # noqa: F401  同上
    ApifyBudgetBlocked,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
    acquire_provider_execution_claim,
    apify_execution_context,
    finalize_provider_execution_claim,
)
from app.platform.llm_local_evaluation import (  # noqa: F401  同上
    LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
    LOCAL_EVALUATION_DERIVE_METHOD,
    LOCAL_EVALUATION_EXECUTION_CLASS,
    verify_job_local_evaluation_capability,
)
from app.services.media.video_keyframes import temporary_keyframes
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer
from app.workers.apify_jobs_worker_helpers import (
    _int_or_none,
    _redact_sensitive_text,
    _target,
    _truthy,
)
from app.workers.apify_jobs_video_context import _select_keyframe_requests
# 共享常量来自 config 叶子(原 worker 底部回环 import 转正到顶部,2026-08-30 拆环)。
from app.workers.apify_jobs_worker_config import (
    FINAL_V1_KEYFRAME_QA_DERIVE_METHOD,
    FINAL_V1_KEYFRAME_QA_MODEL,
    LLM_BUDGET_SCOPE,
    LLM_MAX_OUTPUT_TOKENS,
    WORKER_GEMINI_MODEL,
    WORKER_LLM_EXECUTION_CLASS,
)


logger = get_logger(__name__)

_FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
_WORKER_MODEL_EXECUTION_KEY = "worker_model_execution"


def _worker_execution_model_chain(
    payload: dict[str, Any],
    derive_method: str,
) -> list[str]:
    """Return the exact execution-time chain that the analyzer may consume."""

    if derive_method == FINAL_V1_KEYFRAME_QA_DERIVE_METHOD:
        return [
            str(
                payload.get("final_v1_qa_model") or FINAL_V1_KEYFRAME_QA_MODEL
            ).strip()
        ]
    if derive_method == _FINAL_V1_DERIVE_METHOD:
        return analyzer_model_chain(payload, final_v1=True)
    return [WORKER_GEMINI_MODEL]


def _google_provider_row(
    preflight: dict[str, Any],
    *,
    model: str = "",
    binding: str = "",
) -> dict[str, Any]:
    providers = (
        preflight.get("providers")
        if isinstance(preflight.get("providers"), list)
        else []
    )
    google = [item for item in providers if item.get("provider") == "google"]
    wanted_model = str(model or "").strip()
    wanted_binding = str(binding or "").strip()
    if wanted_model:
        exact = next(
            (
                item
                for item in google
                if str(item.get("model") or "").strip() == wanted_model
            ),
            None,
        )
        if exact is not None:
            return exact
    if wanted_binding:
        exact = next(
            (
                item
                for item in google
                if str(item.get("binding") or "").strip() == wanted_binding
            ),
            None,
        )
        if exact is not None:
            return exact
    return google[0] if google else {}


def _llm_budget_preflight(
    job: dict[str, Any],
    payload: dict[str, Any],
    *,
    execution_class: str = llm_gateway.PRODUCTION_EXECUTION_CLASS,
) -> dict[str, Any]:
    target_type, target_id = _target(payload)
    prompt = str(payload.get("prompt") or f"{job.get('job_type') or 'analysis'} {target_type}:{target_id}")
    derive_method = str(payload.get("derive_method") or "").strip()
    keyframe_only = derive_method == FINAL_V1_KEYFRAME_QA_DERIVE_METHOD
    requested_models = _worker_execution_model_chain(payload, derive_method)
    exact_model = requested_models[0]
    with db_connection_sync_scope():
        preflight = llm_gateway.budget_preflight(
            prompt,
            purpose="keyframe_qa" if keyframe_only else "vkpi_analysis_worker",
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            preferred_provider="google",
            model_override=exact_model,
            model_fallbacks=model_fallback_candidates(requested_models),
            execution_class=execution_class,
            cost_tag=LLM_BUDGET_SCOPE,
            # 主线 enforce 口径:只对真有 caps 行的 scope 硬拦,未配额的 cost_scope
            # (如 video_analysis_final_v1)放行,避免视频深析被「未配额=全拦」误杀。
            require_configured=False,
        )
    ready_models, blocked_models = ready_model_subchain(
        preflight,
        requested_models,
    )
    authorizations_by_model = {
        model: _google_execution_authorization(preflight, model=model)
        for model in ready_models
    }
    authorizations_by_binding = {
        authorization["binding"]: authorization
        for authorization in authorizations_by_model.values()
        if str(authorization.get("binding") or "").strip()
    }
    head = ready_models[0] if ready_models else requested_models[0]
    head_row = _google_provider_row(preflight, model=head)
    return {
        **preflight,
        _WORKER_MODEL_EXECUTION_KEY: {
            "requested_models": list(requested_models),
            "ready_models": list(ready_models),
            "blocked_models": dict(blocked_models),
            "authorizations_by_model": authorizations_by_model,
            "authorizations_by_binding": authorizations_by_binding,
            "estimated_cost_usd": float(
                head_row.get("estimated_cost_usd") or 0.0
            ),
        },
    }


def _google_allowed(preflight: dict[str, Any]) -> tuple[bool, str, float]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    google = next((item for item in providers if item.get("provider") == "google"), {})
    reason = str(
        google.get("binding_gate_reason")
        or preflight.get("provider_gate_reason")
        or "provider_calls_blocked"
    )
    return bool(google.get("provider_calls_allowed")), reason, float(google.get("estimated_cost_usd") or 0.0)


def _google_execution_authorization(
    preflight: dict[str, Any],
    *,
    model: str = "",
    binding: str = "",
) -> dict[str, Any]:
    google = _google_provider_row(preflight, model=model, binding=binding)
    signed_ready = bool(google.get("signed_model_production_ready"))
    production_authorized = bool(google.get("production_authorized"))
    evaluation_only = bool(google.get("evaluation_only"))
    return {
        "binding": str(google.get("binding") or ""),
        "model": str(google.get("model") or ""),
        "execution_class": str(
            google.get("execution_class")
            or preflight.get("execution_class")
            or llm_gateway.PRODUCTION_EXECUTION_CLASS
        ),
        "authorization_scope": str(
            google.get("authorization_scope") or "blocked"
        ),
        "evaluation_only": evaluation_only,
        "production_authorized": production_authorized,
        "claim_status": str(
            google.get("claim_status")
            or google.get("model_claim_status")
            or preflight.get("claim_status")
            or "descriptive_only"
        ),
        "model_readiness_status": str(
            google.get("model_readiness_status")
            or preflight.get("model_readiness_status")
            or "not_ready"
        ),
        # Immutable preflight snapshots.  Historical permission to execute is
        # deliberately not represented as current signed model readiness.
        "execution_authorization_at_run": {
            "scope": "execution_time_snapshot",
            "authorized": bool(production_authorized or evaluation_only),
            "production_authorized": production_authorized,
            "evaluation_only": evaluation_only,
            "status": str(
                google.get("operational_authorization_status")
                or (
                    "operationally_authorized"
                    if production_authorized
                    else "evaluation_only_authorized"
                    if evaluation_only
                    else "blocked"
                )
            ),
            "source": str(
                google.get("operational_authorization_source")
                or (
                    "legacy_runtime_policy"
                    if production_authorized
                    else "local_evaluation"
                    if evaluation_only
                    else "blocked"
                )
            ),
            "temporary": bool(google.get("operational_authorization_temporary")),
        },
        "signed_readiness_at_run": {
            "scope": "execution_time_snapshot",
            "production_ready": signed_ready,
            "status": str(
                google.get("signed_model_readiness_status")
                or ("production_ready" if signed_ready else "not_production_ready")
            ),
            "claim_status": str(
                google.get("signed_model_readiness_claim_status")
                or "descriptive_only"
            ),
            "evidence_source": str(
                google.get("signed_model_readiness_evidence_source")
                or "not_configured"
            ),
        },
    }


def _provider_allowed(preflight: dict[str, Any], provider_name: str) -> tuple[bool, str, float]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    provider = next((item for item in providers if item.get("provider") == provider_name), {})
    reason = str(preflight.get("provider_gate_reason") or provider.get("provider_gate_reason") or "provider_calls_blocked")
    return bool(provider.get("provider_calls_allowed")), reason, float(provider.get("estimated_cost_usd") or 0.0)


def _log_budget_preflight_record_only(
    *,
    job: dict[str, Any],
    provider: str,
    allowed: bool,
    reason: str,
    estimated_cost: float,
    stage: str,
) -> None:
    if allowed:
        return
    logger.warning(
        "apify_jobs budget preflight would block, continuing record-only | job_id=%s provider=%s stage=%s reason=%s estimated_cost_usd=%s",
        job.get("id"),
        provider,
        stage,
        reason,
        estimated_cost,
    )


def _provider_budget_preflight(job: dict[str, Any], payload: dict[str, Any], provider: str) -> dict[str, Any]:
    target_type, target_id = _target(payload)
    prompt = str(payload.get("prompt") or f"{job.get('job_type') or 'analysis'} {target_type}:{target_id} {provider}")
    keyframe_only = (
        provider == "google"
        and str(payload.get("derive_method") or "").strip() == FINAL_V1_KEYFRAME_QA_DERIVE_METHOD
    )
    model_kwargs = (
        {"model_override": str(payload.get("final_v1_qa_model") or FINAL_V1_KEYFRAME_QA_MODEL).strip()}
        if keyframe_only
        else {}
    )
    with db_connection_sync_scope():
        return llm_gateway.budget_preflight(
            prompt,
            purpose="keyframe_qa" if keyframe_only else "vkpi_analysis_worker",
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            preferred_provider=provider,
            # QA and judge calls never inherit a final_v1 local-evaluation
            # capability.  They remain behind production model readiness.
            execution_class=llm_gateway.PRODUCTION_EXECUTION_CLASS,
            cost_tag=LLM_BUDGET_SCOPE,
            require_configured=False,
            **model_kwargs,
        )


def _load_video_evidence(conn: psycopg.Connection[Any], target_id: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              e.id,
              e.content_url,
              COALESCE(e.video_title, e.title, '') AS title,
              e.platform,
              e.view_count,
              e.like_count,
              e.comment_count,
              e.share_count,
              e.duration_seconds,
              e.publish_date,
              e.metrics_source,
              e.metrics_scraped_at,
              e.project_id,
              e.kol_pool_id,
              p.project_name,
              p.product_name,
              COALESCE(kp.handle, '') AS creator_handle,
              COALESCE(kp.display_name, '') AS creator_name,
              kp.followers,
              kp.avg_views,
              kp.engagement_rate
            FROM vkpi_kol_video_evidence e
            LEFT JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
            LEFT JOIN vkpi_projects p ON p.id = e.project_id
            WHERE e.id = %s
            LIMIT 1
            """,
            (_int_or_none(target_id) or 0,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"video evidence not found: {target_id}")
    if not str(row.get("content_url") or "").strip():
        raise ValueError(f"video evidence has no content_url: {target_id}")
    return dict(row)


def _download_youtube_for_keyframes(url: str, output_dir: str) -> dict[str, Any]:
    output: dict[str, Any] = {"success": False, "path": None, "error": None, "bytes": 0}
    out_tmpl = str(Path(output_dir) / "youtube_keyframes.%(ext)s")
    ytdlp_proxy = os.environ.get("YTDLP_PROXY", "").strip()
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--quiet",
        "--no-progress",
        "-f",
        "bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]/best[height<=720]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        out_tmpl,
    ]
    if ytdlp_proxy:
        cmd.extend(["--proxy", ytdlp_proxy])
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        output["error"] = "yt_dlp_entrypoint_missing"
        return output
    except OSError:
        output["error"] = "yt_dlp_execution_failed"
        return output
    except subprocess.TimeoutExpired:
        output["error"] = "youtube keyframe download timed out"
        return output
    if proc.returncode != 0:
        raw_error = (proc.stderr or proc.stdout or "youtube keyframe download failed")[-500:]
        output["error"] = _redact_sensitive_text(raw_error, limit=500)
        return output
    candidates = sorted(Path(output_dir).glob("youtube_keyframes.*"))
    if not candidates:
        output["error"] = "youtube keyframe download produced no file"
        return output
    video_path = candidates[0]
    if not video_path.exists() or video_path.stat().st_size <= 0:
        output["error"] = "youtube keyframe download produced empty file"
        return output
    output.update({"success": True, "path": str(video_path), "bytes": video_path.stat().st_size})
    return output


@contextmanager
def _extract_keyframes_for_qa(
    evidence: dict[str, Any],
    layer1: dict[str, Any],
    *,
    limit: int = 6,
    temp_prefix: str = "vkpi-keyframe-qa-video-",
) -> Iterator[dict[str, Any]]:
    keyframe_requests = _select_keyframe_requests(layer1, limit=limit)
    with tempfile.TemporaryDirectory(prefix=temp_prefix) as tmpdir:
        download = _download_youtube_for_keyframes(str(evidence.get("content_url") or ""), tmpdir)
        if not download.get("success") or not download.get("path"):
            raise RuntimeError(f"youtube_keyframe_download_failed: {download.get('error')}")
        with temporary_keyframes(str(download["path"]), keyframe_requests) as frames:
            if not frames:
                raise RuntimeError("keyframe extraction produced no frames")
            frame_meta = [
                {"timestamp": frame.get("timestamp"), "reason": frame.get("reason")}
                for frame in frames
            ]
            yield {
                "frames": frames,
                "frame_meta": frame_meta,
                "keyframe_requests": keyframe_requests,
                "download": download,
            }


@contextmanager
def _gemini_worker_overrides(payload: dict[str, Any]):
    model_override = str(payload.get("gemini_model") or WORKER_GEMINI_MODEL).strip()
    skip_subtitles = _truthy(
        payload.get("skip_subtitles", payload.get("gemini_skip_subtitles", os.environ.get("APIFY_WORKER_GEMINI_SKIP_SUBTITLES")))
    )
    with ExitStack() as stack:
        if skip_subtitles:
            stack.enter_context(patch.object(gemini_video_analyzer, "fetch_youtube_subtitles", lambda *_args, **_kwargs: ""))
        if model_override and getattr(gemini_video_analyzer, "gemini_client", None):
            original_generate = gemini_video_analyzer.gemini_client.models.generate_content

            def _forced_generate_content(*args: Any, **kwargs: Any):
                kwargs["model"] = model_override
                return original_generate(*args, **kwargs)

            stack.enter_context(patch.object(gemini_video_analyzer.gemini_client.models, "generate_content", _forced_generate_content))
        yield model_override
