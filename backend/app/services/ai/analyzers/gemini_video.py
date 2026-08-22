"""
services/ai/analyzers/gemini_video.py — Gemini 全视频分析（YouTube File API）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict

from app.core.logging import get_logger
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE, gemini_client
try:
    from google.genai import types as genai_types
except ImportError:
    genai_types = None

from app.core.constants import VILTROX_CATALOG_PROMPT
from app.services.scoring.core import compute_weighted_scores, get_vertical
from app.services.scraping.ytdlp import YTDLP_AVAILABLE, YTDLP_BIN, YTDLP_PROXY, fetch_youtube_subtitles
from app.services.scoring.creator import get_creator_profile
from app.services.scoring.verticals import apply_learned_weights
from app.platform import llm_gateway
from app.platform import llm_production
from app.services.media.video_keyframes import build_anthropic_multimodal_content, build_openai_multimodal_content

logger = get_logger(__name__)
# 值=(cache_name, created_monotonic);TTL 3600s,55 分钟后主动弃用重建——过期的 cache_name 会让主模型整链报错降级。
_FINAL_V1_CONTEXT_CACHES: dict[str, tuple[str, float]] = {}
_FINAL_V1_CACHE_REUSE_SECONDS = 3300.0
DEFAULT_GEMINI_FINAL_V1_MODELS = ["gemini-3-flash-preview", "gemini-2.5-flash"]
GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS = max(
    60,
    int(os.environ.get("GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SEC", "900")),
)
GEMINI_VIDEO_MAX_OUTPUT_TOKENS = min(
    llm_production.GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP,
    max(
        256,
        int(
            os.environ.get(
                "GEMINI_VIDEO_MAX_OUTPUT_TOKENS",
                os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "4096"),
            )
        ),
    ),
)
GEMINI_VIDEO_RESERVE_TOKENS_PER_SECOND = min(
    512,
    max(300, int(os.environ.get("GEMINI_VIDEO_RESERVE_TOKENS_PER_SECOND", "300"))),
)


class ProviderPressureExhausted(RuntimeError):
    """All models in the chain failed with provider-pressure class errors.

    Raised by the fast path instead of falling through to the slow path:
    switching transport (download + File API) hits the same overloaded models,
    so the download would be pure waste. Time-based retry is owned by the
    worker's backoff machinery.
    """


class AnalysisScopeRevoked(RuntimeError):
    """The caller's authorization disappeared between two paid/external stages.

    Raised by an ``authorization_checkpoint`` callable handed to the analyzers.
    The analyzers never swallow it inside their per-model retry loops: the
    result is marked ``scope_revoked`` and returned immediately so the worker
    can terminalize the job before any result, cache, or follow-up write.
    """

    def __init__(self, reason: str, *, stage: str = "") -> None:
        super().__init__(str(reason or "scope_revoked"))
        self.reason = str(reason or "scope_revoked")
        self.stage = str(stage or "")


def _scope_guard(
    result: dict[str, Any],
    authorization_checkpoint: Callable[[str], None] | None,
) -> Callable[[str], bool]:
    """Build a stage gate that marks ``result`` and reports False on revocation."""

    def _passes(stage: str) -> bool:
        if authorization_checkpoint is None:
            return True
        try:
            authorization_checkpoint(stage)
        except AnalysisScopeRevoked as exc:
            result["analyzed"] = False
            result["error"] = f"scope_revoked:{exc.reason}"
            result["scope_revoked"] = exc.reason
            result["scope_revoked_stage"] = stage
            logger.warning(
                "gemini_analysis_scope_revoked",
                extra={"stage": stage, "reason": exc.reason},
            )
            return False
        return True

    return _passes


def _stage_add(result: dict[str, Any], stage: str, started_monotonic: float) -> int:
    """零成本阶段计时:把 ``stage`` 自 ``started_monotonic`` 起的毫秒累加进
    ``result["stage_timings_ms"]``(同名阶段多次调用累加,如多模型重试)。返回本次毫秒。
    剖面脚本 scripts/ops/profile_video_analysis.py 以此分解 download/upload/gemini_call。"""
    elapsed_ms = max(0, int((time.monotonic() - started_monotonic) * 1000))
    timings = result.get("stage_timings_ms")
    if not isinstance(timings, dict):
        timings = {}
        result["stage_timings_ms"] = timings
    timings[stage] = int(timings.get(stage) or 0) + elapsed_ms
    return elapsed_ms


_PROVIDER_PRESSURE_MARKERS = (
    "429",
    "502",
    "503",
    "504",
    "resource_exhausted",
    "resource exhausted",
    "high demand",
    "overloaded",
    "rate limit",
    "service unavailable",
    "internal error",
)


def _is_provider_pressure_error(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _PROVIDER_PRESSURE_MARKERS)


def final_v1_gemini_models(value: Any = None) -> list[str]:
    raw = value
    if raw is None:
        raw = os.environ.get("GEMINI_FINAL_V1_MODELS", "")
    if isinstance(raw, (list, tuple)):
        models = [str(item or "").strip() for item in raw]
    else:
        models = [item.strip() for item in str(raw or "").split(",")]
    return [model for model in models if model] or list(DEFAULT_GEMINI_FINAL_V1_MODELS)


# 结果整形/归一化纯函数簇已抽到 gemini_video_results.py(行为不变,re-export 兜调用点)。
from app.services.ai.analyzers.gemini_video_results import (  # noqa: E402,F401
    VIDEO_FINAL_LAYERS,
    VIDEO_V2_SCORE_KEYS,
    _apply_final_v1_result,
    _apply_v2_result,
    _bool_value,
    _clamped_confidence,
    _normalise_final_v1_keyframe_qa,
    _normalise_final_v1_result,
    _normalise_v2_result,
    _parse_json_response_text,
    _response_usage_metadata,
    _score_value,
)

# 关键帧判定业务函数簇已抽到 gemini_video_keyframes.py(行为不变,re-export 兜调用点)。
from app.services.ai.analyzers.gemini_video_keyframes import (  # noqa: E402,F401
    analyze_final_v1_keyframe_qa,
    analyze_v2_judgment_with_anthropic_keyframes,
    analyze_v2_judgment_with_keyframes,
    analyze_v2_judgment_with_openai_keyframes,
)


# prompt 构造器已抽到 gemini_video_prompts.py(行为不变,re-export 兜调用点)。
from app.services.ai.analyzers.gemini_video_prompts import (  # noqa: E402
    _video_final_v1_dynamic_prompt,
    _video_final_v1_keyframe_qa_prompt,
    _video_final_v1_prompt,
    _video_final_v1_static_prompt,
    _video_v2_judgment_prompt,
    _video_v2_prompt,
)

# legacy inline prompt 已抽到 gemini_video_legacy_prompt.py(行为不变,re-export 兜调用点)。
from app.services.ai.analyzers.gemini_video_legacy_prompt import (  # noqa: E402,F401
    _video_legacy_prompt,
)


# ── 刀②:final_v1 静态提示 context cache 跨进程共享 ───────────────────────────────
# 剖面坐实(2026-08 隔离库):99 条 final_v1 → 98 个不同 cache_name。worker 每个任务都起一个
# 全新 python 子进程跑分析器,_FINAL_V1_CONTEXT_CACHES 进程内 memo 永远是空的 → 每条视频都
# caches.create 一次(多一次 API 往返,且单次使用的 cache 按全价计费 + 按时计存储费,
# 从未享受 75% 的 cached token 折扣)。现把 cache_name 登记到 persistent_cache(003 baseline 表,
# _respect_gemini_qps 同款模式,零新表零迁移),全 fleet 55 分钟内复用同一份。
# 失效防毒:generateContent 报 cache 类错误时驱逐共享条目并对同模型免 cache 重试一次。
_SHARED_CONTEXT_CACHE_ENABLED = str(
    os.environ.get("GEMINI_FINAL_V1_SHARED_CONTEXT_CACHE", "1")
).strip().lower() not in {"0", "false", "off", "no"}
_SHARED_CONTEXT_CACHE_KEY_PREFIX = "vkpi:gemini-ctx-cache:final_v1:"
_CONTEXT_CACHE_ERROR_MARKERS = ("cachedcontent", "cached content", "cached_content", "cachedcontents/")
_shared_cache_store_warned = False


def _is_context_cache_error(text: str) -> bool:
    low = str(text or "").lower()
    return any(marker in low for marker in _CONTEXT_CACHE_ERROR_MARKERS)


def _final_v1_shared_cache_key(model_name: str, static_prompt: str) -> str:
    digest = hashlib.sha256(str(static_prompt or "").encode("utf-8")).hexdigest()[:16]
    return f"{_SHARED_CONTEXT_CACHE_KEY_PREFIX}{model_name}:{digest}"


def _shared_cache_store_failed(action: str, exc: Exception) -> None:
    global _shared_cache_store_warned
    if not _shared_cache_store_warned:
        _shared_cache_store_warned = True
        logger.warning(
            "gemini_final_v1_shared_context_cache_store_unavailable",
            extra={"action": action, "error": f"{type(exc).__name__}: {str(exc)[:160]}"},
        )


def _shared_context_cache_get(key: str) -> tuple[str, float]:
    """返回 (cache_name, age_seconds);没有/过期/库不可用 → ("", 0.0),绝不抛。"""

    if not _SHARED_CONTEXT_CACHE_ENABLED:
        return "", 0.0
    try:
        from app.db.connection import db_connection_sync_scope, get_conn

        with db_connection_sync_scope():
            row = get_conn().execute(
                "SELECT value_json, EXTRACT(EPOCH FROM (NOW() - created_at)) AS age_seconds "
                "FROM persistent_cache WHERE cache_key=? AND expires_at > NOW()",
                (key,),
            ).fetchone()
        if not row:
            return "", 0.0
        data = dict(row)
        value = json.loads(str(data.get("value_json") or "{}"))
        cache_name = str((value or {}).get("cache_name") or "")
        age = max(0.0, float(data.get("age_seconds") or 0.0))
        return cache_name, age
    except Exception as exc:
        _shared_cache_store_failed("get", exc)
        return "", 0.0


def _shared_context_cache_put(key: str, cache_name: str, *, model_name: str) -> None:
    if not _SHARED_CONTEXT_CACHE_ENABLED or not cache_name:
        return
    try:
        from app.db.connection import db_connection_sync_scope, get_conn

        value = json.dumps({"cache_name": cache_name, "model": model_name}, ensure_ascii=False)
        with db_connection_sync_scope():
            conn = get_conn()
            conn.execute(
                "INSERT INTO persistent_cache (cache_key, value_json, expires_at, created_at) "
                "VALUES (?, ?, NOW() + make_interval(secs => ?), NOW()) "
                "ON CONFLICT (cache_key) DO UPDATE SET value_json=EXCLUDED.value_json, "
                "expires_at=EXCLUDED.expires_at, created_at=EXCLUDED.created_at",
                (key, value, float(_FINAL_V1_CACHE_REUSE_SECONDS)),
            )
            conn.commit()
    except Exception as exc:
        _shared_cache_store_failed("put", exc)


def _shared_context_cache_evict(key: str) -> None:
    if not _SHARED_CONTEXT_CACHE_ENABLED:
        return
    try:
        from app.db.connection import db_connection_sync_scope, get_conn

        with db_connection_sync_scope():
            conn = get_conn()
            conn.execute("DELETE FROM persistent_cache WHERE cache_key=?", (key,))
            conn.commit()
    except Exception as exc:
        _shared_cache_store_failed("evict", exc)


def _final_v1_cache_evict(model_name: str, *, reason: str = "") -> None:
    """generateContent 报 cache 类错误(cachedContent 不存在/过期/无权)→ 进程内 + 共享条目一起驱逐。"""

    static_prompt = _video_final_v1_static_prompt()
    _FINAL_V1_CONTEXT_CACHES.pop(f"{model_name}:video_analysis_final_v1:{hash(static_prompt)}", None)
    _shared_context_cache_evict(_final_v1_shared_cache_key(model_name, static_prompt))
    logger.warning(
        "gemini_final_v1_context_cache_evicted",
        extra={"model": model_name, "reason": str(reason or "")[:160]},
    )


def _retry_after_context_cache_error(
    exc: BaseException,
    cache_info: dict[str, Any] | None,
    model_name: str,
    retried: set[str],
) -> bool:
    """共享 cache 被毒(条目已失效)时:驱逐 + 同模型免 cache 重试一次。只在 cache 启用且错误
    文本指向 cachedContent 时触发;每模型最多一次,绝不放大提供方压力类错误。"""

    if model_name in retried or not isinstance(cache_info, dict) or not cache_info.get("enabled"):
        return False
    text = str(exc)
    if not _is_context_cache_error(text):
        return False
    retried.add(model_name)
    _final_v1_cache_evict(model_name, reason=text[:200])
    return True


def _final_v1_cache_config(model_name: str) -> tuple[Any | None, dict[str, Any]]:
    info: dict[str, Any] = {"enabled": False, "cache_name": "", "static_only": True, "error": "", "source": ""}
    if not GEMINI_AVAILABLE or not gemini_client or not genai_types:
        info["error"] = "gemini cache unavailable"
        return None, info
    static_prompt = _video_final_v1_static_prompt()
    cache_key = f"{model_name}:video_analysis_final_v1:{hash(static_prompt)}"
    shared_key = _final_v1_shared_cache_key(model_name, static_prompt)
    cache_name = ""
    cached_entry = _FINAL_V1_CONTEXT_CACHES.get(cache_key)
    if cached_entry:
        entry_name, created_at = cached_entry
        if (time.monotonic() - created_at) < _FINAL_V1_CACHE_REUSE_SECONDS:
            cache_name = entry_name
            info["source"] = "process"
        else:
            _FINAL_V1_CONTEXT_CACHES.pop(cache_key, None)
    if not cache_name:
        shared_name, shared_age = _shared_context_cache_get(shared_key)
        if shared_name and shared_age < _FINAL_V1_CACHE_REUSE_SECONDS:
            cache_name = shared_name
            info["source"] = "shared"
            # 进程内 memo 以共享条目的真实创建时刻为基准,不把剩余寿命算长
            _FINAL_V1_CONTEXT_CACHES[cache_key] = (cache_name, time.monotonic() - shared_age)
    try:
        if not cache_name:
            def _create_cache():
                return gemini_client.caches.create(
                    model=model_name,
                    config=genai_types.CreateCachedContentConfig(
                        contents=[static_prompt],
                        ttl="3600s",
                        displayName="vkpi_video_analysis_final_v1_static",
                    ),
                )

            cache = _create_cache()
            cache_name = str(getattr(cache, "name", "") or "")
            if cache_name:
                _FINAL_V1_CONTEXT_CACHES[cache_key] = (cache_name, time.monotonic())
                _shared_context_cache_put(shared_key, cache_name, model_name=model_name)
                info["source"] = "created"
        if not cache_name:
            info["error"] = "empty cache name"
            return None, info
        info.update({"enabled": True, "cache_name": cache_name})
        return genai_types.GenerateContentConfig(cachedContent=cache_name), info
    except Exception as exc:
        info["error"] = str(exc)[:300]
        logger.warning("gemini_final_v1_context_cache_failed", extra={"error": info["error"]})
        return None, info


def _video_generate_config(model_name: str, cache_config: Any = None) -> Any:
    """Gemini 2.5 系视频帧 token 固定 258/帧;显式压 LOW(66/帧)对齐 Gemini 3 默认口径(~70/帧)。
    Gemini 3 系保持默认不动;SDK 不支持该字段时原样回退,绝不阻断调用。"""
    if not str(model_name or "").startswith("gemini-2.5"):
        return cache_config
    try:
        resolution = getattr(getattr(genai_types, "MediaResolution", None), "MEDIA_RESOLUTION_LOW", "MEDIA_RESOLUTION_LOW")
        if cache_config is not None:
            return cache_config.model_copy(update={"media_resolution": resolution})
        return genai_types.GenerateContentConfig(media_resolution=resolution)
    except Exception:
        return cache_config


def _video_input_token_estimate(
    prompt: str,
    performance_context: dict[str, Any] | None,
) -> int:
    """Reserve video input conservatively before Gemini network I/O.

    Known durations use a deliberately high 300 tokens/second floor.  Unknown
    durations reserve the exact model's full one-million-token context instead
    of silently under-reserving a long video.
    """

    duration_raw = (
        performance_context.get("duration_seconds")
        if isinstance(performance_context, dict)
        else None
    )
    try:
        duration_seconds = int(duration_raw) if duration_raw not in (None, "") else 0
    except (TypeError, ValueError):
        duration_seconds = 0
    text_tokens = max(1, len(str(prompt or "")) // 3) + 2048
    if duration_seconds <= 0:
        return llm_production.GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP
    media_tokens = max(60, duration_seconds) * GEMINI_VIDEO_RESERVE_TOKENS_PER_SECOND
    return min(
        llm_production.GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP,
        max(1, text_tokens + media_tokens),
    )


def _strict_generate_content(
    *,
    model_name: str,
    contents: list[Any],
    config: Any,
    prompt: str,
    performance_context: dict[str, Any] | None,
    llm_context: dict[str, Any] | None,
    subphase: str,
    attempt_index: int,
    attempt_total: int,
    attempt_log: list[dict[str, Any]],
) -> Any:
    context = llm_context if isinstance(llm_context, dict) else {}
    base_metadata = (
        context.get("metadata")
        if isinstance(context.get("metadata"), dict)
        else {}
    )
    purpose = str(context.get("purpose") or "gemini_video_legacy")
    return llm_production.generate_google_content(
        client=gemini_client,
        contents=contents,
        config=config,
        model=model_name,
        purpose=purpose,
        max_output_tokens=GEMINI_VIDEO_MAX_OUTPUT_TOKENS,
        estimated_input_tokens=_video_input_token_estimate(
            prompt,
            performance_context,
        ),
        cost_tag=str(context.get("cost_tag") or "") or None,
        triggered_by=context.get("triggered_by"),
        metadata={
            **base_metadata,
            "phase": str(base_metadata.get("phase") or "video_analysis"),
            "subphase": subphase,
            "attempt_index": attempt_index,
            "attempt_total": attempt_total,
        },
        execution_class=str(
            context.get("execution_class")
            or llm_gateway.PRODUCTION_EXECUTION_CLASS
        ),
        attempt_log=attempt_log,
    )


async def analyze_local_video_with_gemini(
    video_path: str,
    title: str,
    creator_handle: str = "",
    *,
    schema_version: str = "v2",
    performance_context: dict[str, Any] | None = None,
    subtitle_text: str = "",
    final_v1_models: list[str] | str | None = None,
    models: list[str] | str | None = None,
    llm_context: dict[str, Any] | None = None,
    authorization_checkpoint: Callable[[str], None] | None = None,
) -> dict:
    """Analyze an already-downloaded local MP4 with Gemini File API.

    ``authorization_checkpoint(stage)`` is consulted before the File API upload
    and before every model attempt; raising ``AnalysisScopeRevoked`` stops the
    chain with ``result["scope_revoked"]`` set and no further provider calls.
    """
    result = {
        "analyzed": False,
        "method": "gemini_local_fileapi",
        "content_summary": "",
        "content_genre": "",
        "content_topic": "",
        "timestamps": [],
        "competitor_mentions": [],
        "why_compelling": "",
        "hook_analysis": "",
        "target_audience": "",
        "production_quality": "",
        "camera_body": None,
        "viltrox_lens": None,
        "other_lens": None,
        "viltrox_detected": False,
        "viltrox_products_all": [],
        "marketing_potential": "",
        "marketing_notes": "",
        "usage_metadata": {},
        "model": "",
        "fileapi_cleanup": {"delete_attempted": False, "deleted": False},
        "error": None,
        "llm_attempts": [],
        "cost_authority": "llm_production_google_generate_content_v1",
    }
    if not GEMINI_AVAILABLE or not gemini_client or not genai_types:
        result["error"] = "Gemini not available"
        return result
    local_path = Path(str(video_path or ""))
    if not local_path.exists() or local_path.stat().st_size < 1000:
        result["error"] = "local video file missing or empty"
        return result

    profile_ctx = ""
    if creator_handle:
        profile = get_creator_profile(creator_handle)
        if profile.get("viltrox_lenses"):
            profile_ctx = f"\n创作者历史使用过: {', '.join(profile['viltrox_lenses'][:3])}"
    subtitle_ctx = ""
    if subtitle_text:
        subtitle_ctx = (
            "\n\n=== 字幕时间轴（真实时间戳，优先用这个定位事件）===\n"
            + subtitle_text
            + "\n=== 字幕结束 ===\n"
            "时间戳规则：timestamps 里的 time 字段必须来自上面字幕里的真实时间点，不允许猜测或等间隔填写。"
        )
    schema_key = str(schema_version or "").strip().lower()
    is_v2 = schema_key == "v2"
    is_final_v1 = schema_key == "final_v1"
    final_full_prompt = ""
    if is_final_v1:
        prompt = _video_final_v1_dynamic_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_used=bool(subtitle_text),
            performance_context=performance_context,
        )
        final_full_prompt = _video_final_v1_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_used=bool(subtitle_text),
            performance_context=performance_context,
        )
    elif is_v2:
        prompt = _video_v2_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_used=bool(subtitle_text),
            performance_context=performance_context,
        )
    else:
        result["error"] = "analyze_local_video_with_gemini supports schema_version='v2' or 'final_v1' only"
        return result
    gemini_file = None
    uploaded_file_name = ""
    scope_passes = _scope_guard(result, authorization_checkpoint)
    if not scope_passes("file_api_upload"):
        return result
    try:
        file_size_mb = local_path.stat().st_size / 1024 / 1024
        logger.info("gemini_local_fileapi_upload_start", extra={"size_mb": round(file_size_mb, 1)})
        result["local_video_bytes"] = int(local_path.stat().st_size)

        upload_started = time.monotonic()
        try:
            def _upload():
                return gemini_client.files.upload(file=str(local_path), config={"mime_type": "video/mp4"})

            gemini_file = await asyncio.to_thread(_upload)
        except Exception as upload_err:
            _stage_add(result, "upload", upload_started)
            result["error"] = f"Gemini File API upload failed: {upload_err}"
            logger.warning("gemini_local_fileapi_upload_failed", extra={"error": str(upload_err)})
            return result
        _stage_add(result, "upload", upload_started)
        if not gemini_file or not getattr(gemini_file, "name", None):
            result["error"] = "Gemini upload returned empty file object"
            logger.warning("gemini_local_fileapi_upload_invalid_file", extra={"file": str(gemini_file)})
            return result
        uploaded_file_name = str(gemini_file.name)
        logger.info("gemini_local_fileapi_upload_complete", extra={"file_name": uploaded_file_name})

        state = ""
        active_wait_started = time.monotonic()
        for poll_attempt in range(30):
            try:
                def _check(name=uploaded_file_name):
                    return gemini_client.files.get(name=name)

                gemini_file = await asyncio.to_thread(_check)
            except Exception as poll_err:
                _stage_add(result, "file_active_wait", active_wait_started)
                result["error"] = f"files.get() error during polling: {poll_err}"
                logger.warning("gemini_local_fileapi_poll_error", extra={"attempt": poll_attempt, "error": str(poll_err)})
                return result
            state = getattr(gemini_file.state, "name", str(gemini_file.state))
            logger.info("gemini_local_fileapi_poll", extra={"attempt": poll_attempt + 1, "state": state})
            if state == "ACTIVE":
                break
            if state == "FAILED":
                _stage_add(result, "file_active_wait", active_wait_started)
                result["error"] = f"Gemini file processing FAILED (state={state})"
                return result
            await asyncio.sleep(3)
        else:
            _stage_add(result, "file_active_wait", active_wait_started)
            result["error"] = f"Gemini file ACTIVE timeout after 90s (final state={state})"
            return result
        _stage_add(result, "file_active_wait", active_wait_started)
        if not getattr(gemini_file, "uri", None):
            result["error"] = "Gemini file ACTIVE but uri is empty"
            return result

        last_err = ""
        model_names = (
            final_v1_gemini_models(models)
            if models is not None
            else final_v1_gemini_models(final_v1_models)
            if is_final_v1
            else ["gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-2.5-flash"]
        )
        attempt_total = len(model_names)
        attempt_plan = list(enumerate(model_names, start=1))
        cache_retried: set[str] = set()
        plan_pos = 0
        while plan_pos < len(attempt_plan):
            attempt_index, model_name = attempt_plan[plan_pos]
            plan_pos += 1
            if not scope_passes("file_api_attempt"):
                return result
            attempt_started = time.monotonic()
            cache_info: dict[str, Any] = {}
            try:
                cache_config = None
                request_prompt = prompt
                if is_final_v1:
                    cache_setup_started = time.monotonic()
                    cache_config, cache_info = _final_v1_cache_config(model_name)
                    _stage_add(result, "cache_setup", cache_setup_started)
                    if not cache_config:
                        request_prompt = final_full_prompt
                request_config = _video_generate_config(model_name, cache_config)
                def _analyze(m=model_name, f=gemini_file):
                    kwargs: dict[str, Any] = {
                        "model": m,
                        "contents": [
                            genai_types.Part.from_uri(file_uri=f.uri, mime_type="video/mp4"),
                            request_prompt,
                        ],
                    }
                    if request_config:
                        kwargs["config"] = request_config
                    return _strict_generate_content(
                        model_name=m,
                        contents=kwargs["contents"],
                        config=kwargs.get("config"),
                        prompt=request_prompt,
                        performance_context=performance_context,
                        llm_context=llm_context,
                        subphase="local_file_generation",
                        attempt_index=attempt_index,
                        attempt_total=attempt_total,
                        attempt_log=result["llm_attempts"],
                    )

                resp = await asyncio.to_thread(_analyze)
                _stage_add(result, "generation", attempt_started)
                usage_metadata = _response_usage_metadata(resp)
                raw = resp.text.strip()
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                parsed = _parse_json_response_text(raw)
                if is_final_v1:
                    _apply_final_v1_result(
                        result,
                        parsed,
                        method=f"gemini_local_fileapi_{model_name}",
                        model=model_name,
                        usage_metadata=usage_metadata,
                        subtitle_used=bool(subtitle_text),
                    )
                    result["context_cache"] = cache_info
                    logger.info(
                        "gemini_local_fileapi_final_v1_success",
                        extra={"model": model_name, "timestamps": len(result.get("timestamps") or [])},
                    )
                    break
                _apply_v2_result(
                    result,
                    parsed,
                    method=f"gemini_local_fileapi_{model_name}",
                    model=model_name,
                    usage_metadata=usage_metadata,
                    subtitle_used=bool(subtitle_text),
                )
                logger.info(
                    "gemini_local_fileapi_v2_success",
                    extra={"model": model_name, "timestamps": len(result.get("timestamps") or [])},
                )
                break
            except Exception as err:
                _stage_add(result, "generation", attempt_started)
                last_err = str(err)
                logger.warning("gemini_local_fileapi_model_failed", extra={"model": model_name, "error": last_err[:100]})
                if _retry_after_context_cache_error(err, cache_info, model_name, cache_retried):
                    attempt_plan.insert(plan_pos, (attempt_index, model_name))
                continue
        if not result["analyzed"]:
            result["error"] = last_err or "Gemini local video analysis failed"
    except Exception as exc:
        result["error"] = str(exc)
        logger.exception("gemini_local_fileapi_analysis_failed")
    finally:
        if uploaded_file_name:
            result["fileapi_cleanup"]["delete_attempted"] = True
            cleanup_started = time.monotonic()
            try:
                def _delete(name=uploaded_file_name):
                    gemini_client.files.delete(name=name)

                await asyncio.to_thread(_delete)
                result["fileapi_cleanup"]["deleted"] = True
                logger.info("gemini_local_fileapi_deleted", extra={"file_name": uploaded_file_name})
            except Exception as del_err:
                logger.warning("gemini_local_fileapi_delete_skipped", extra={"error": str(del_err)})
            _stage_add(result, "cleanup", cleanup_started)
    return result


# analyze_youtube_with_gemini 整簇已抽到 gemini_video_youtube.py(行为不变,re-export 兜调用点)。
from app.services.ai.analyzers.gemini_video_youtube import (  # noqa: E402,F401
    analyze_youtube_with_gemini,
)
