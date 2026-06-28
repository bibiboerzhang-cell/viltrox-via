"""
services/ai/analyzers/gemini_video.py — Gemini 全视频分析（YouTube File API）
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import subprocess
from pathlib import Path
from typing import Any, Dict

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
from app.services.media.video_keyframes import build_anthropic_multimodal_content, build_openai_multimodal_content

logger = get_logger(__name__)
_FINAL_V1_CONTEXT_CACHES: dict[str, str] = {}
DEFAULT_GEMINI_FINAL_V1_MODELS = ["gemini-3-flash-preview", "gemini-2.5-flash"]
GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS = max(
    60,
    int(os.environ.get("GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SEC", "900")),
)


class ProviderPressureExhausted(RuntimeError):
    """All models in the chain failed with provider-pressure class errors.

    Raised by the fast path instead of falling through to the slow path:
    switching transport (download + File API) hits the same overloaded models,
    so the download would be pure waste. Time-based retry is owned by the
    worker's backoff machinery.
    """


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


def _final_v1_cache_config(model_name: str) -> tuple[Any | None, dict[str, Any]]:
    info: dict[str, Any] = {"enabled": False, "cache_name": "", "static_only": True, "error": ""}
    if not GEMINI_AVAILABLE or not gemini_client or not genai_types:
        info["error"] = "gemini cache unavailable"
        return None, info
    static_prompt = _video_final_v1_static_prompt()
    cache_key = f"{model_name}:video_analysis_final_v1:{hash(static_prompt)}"
    cache_name = _FINAL_V1_CONTEXT_CACHES.get(cache_key)
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
                _FINAL_V1_CONTEXT_CACHES[cache_key] = cache_name
        if not cache_name:
            info["error"] = "empty cache name"
            return None, info
        info.update({"enabled": True, "cache_name": cache_name})
        return genai_types.GenerateContentConfig(cachedContent=cache_name), info
    except Exception as exc:
        info["error"] = str(exc)[:300]
        logger.warning("gemini_final_v1_context_cache_failed", extra={"error": info["error"]})
        return None, info


async def analyze_local_video_with_gemini(
    video_path: str,
    title: str,
    creator_handle: str = "",
    *,
    schema_version: str = "v2",
    performance_context: dict[str, Any] | None = None,
    subtitle_text: str = "",
    final_v1_models: list[str] | str | None = None,
) -> dict:
    """Analyze an already-downloaded local MP4 with Gemini File API."""
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
    try:
        file_size_mb = local_path.stat().st_size / 1024 / 1024
        logger.info("gemini_local_fileapi_upload_start", extra={"size_mb": round(file_size_mb, 1)})

        try:
            def _upload():
                return gemini_client.files.upload(file=str(local_path), config={"mime_type": "video/mp4"})

            gemini_file = await asyncio.to_thread(_upload)
        except Exception as upload_err:
            result["error"] = f"Gemini File API upload failed: {upload_err}"
            logger.warning("gemini_local_fileapi_upload_failed", extra={"error": str(upload_err)})
            return result
        if not gemini_file or not getattr(gemini_file, "name", None):
            result["error"] = "Gemini upload returned empty file object"
            logger.warning("gemini_local_fileapi_upload_invalid_file", extra={"file": str(gemini_file)})
            return result
        uploaded_file_name = str(gemini_file.name)
        logger.info("gemini_local_fileapi_upload_complete", extra={"file_name": uploaded_file_name})

        state = ""
        for poll_attempt in range(30):
            try:
                def _check(name=uploaded_file_name):
                    return gemini_client.files.get(name=name)

                gemini_file = await asyncio.to_thread(_check)
            except Exception as poll_err:
                result["error"] = f"files.get() error during polling: {poll_err}"
                logger.warning("gemini_local_fileapi_poll_error", extra={"attempt": poll_attempt, "error": str(poll_err)})
                return result
            state = getattr(gemini_file.state, "name", str(gemini_file.state))
            logger.info("gemini_local_fileapi_poll", extra={"attempt": poll_attempt + 1, "state": state})
            if state == "ACTIVE":
                break
            if state == "FAILED":
                result["error"] = f"Gemini file processing FAILED (state={state})"
                return result
            await asyncio.sleep(3)
        else:
            result["error"] = f"Gemini file ACTIVE timeout after 90s (final state={state})"
            return result
        if not getattr(gemini_file, "uri", None):
            result["error"] = "Gemini file ACTIVE but uri is empty"
            return result

        last_err = ""
        model_names = final_v1_gemini_models(final_v1_models) if is_final_v1 else ["gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-2.5-flash"]
        for model_name in model_names:
            try:
                cache_config = None
                cache_info: dict[str, Any] = {}
                request_prompt = prompt
                if is_final_v1:
                    cache_config, cache_info = _final_v1_cache_config(model_name)
                    if not cache_config:
                        request_prompt = final_full_prompt
                def _analyze(m=model_name, f=gemini_file):
                    kwargs: dict[str, Any] = {
                        "model": m,
                        "contents": [
                            genai_types.Part.from_uri(file_uri=f.uri, mime_type="video/mp4"),
                            request_prompt,
                        ],
                    }
                    if cache_config:
                        kwargs["config"] = cache_config
                    return gemini_client.models.generate_content(**kwargs)

                resp = await asyncio.to_thread(_analyze)
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
                last_err = str(err)
                logger.warning("gemini_local_fileapi_model_failed", extra={"model": model_name, "error": last_err[:100]})
                continue
        if not result["analyzed"]:
            result["error"] = last_err or "Gemini local video analysis failed"
    except Exception as exc:
        result["error"] = str(exc)
        logger.exception("gemini_local_fileapi_analysis_failed")
    finally:
        if uploaded_file_name:
            result["fileapi_cleanup"]["delete_attempted"] = True
            try:
                def _delete(name=uploaded_file_name):
                    gemini_client.files.delete(name=name)

                await asyncio.to_thread(_delete)
                result["fileapi_cleanup"]["deleted"] = True
                logger.info("gemini_local_fileapi_deleted", extra={"file_name": uploaded_file_name})
            except Exception as del_err:
                logger.warning("gemini_local_fileapi_delete_skipped", extra={"error": str(del_err)})
    return result


# analyze_youtube_with_gemini 整簇已抽到 gemini_video_youtube.py(行为不变,re-export 兜调用点)。
from app.services.ai.analyzers.gemini_video_youtube import (  # noqa: E402,F401
    analyze_youtube_with_gemini,
)
