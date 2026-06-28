"""
services/ai/analyzers/gemini_video_youtube.py — analyze_youtube_with_gemini 整簇

行为不变从 gemini_video.py 搬出（函数体逐字不变）。模块级常量/类/纯函数
（ProviderPressureExhausted / _is_provider_pressure_error / final_v1_gemini_models /
_final_v1_cache_config）仍住 gemini_video.py，本模块用函数内 lazy import 取用以避免
循环依赖（gemini_video.py 在加载时 re-export 本函数）。
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
from typing import Any

from app.core.logging import get_logger
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE, gemini_client
try:
    from google.genai import types as genai_types
except ImportError:
    genai_types = None

from app.services.scoring.core import compute_weighted_scores, get_vertical
from app.services.scraping.ytdlp import YTDLP_AVAILABLE, YTDLP_BIN, YTDLP_PROXY, fetch_youtube_subtitles
from app.services.scoring.creator import get_creator_profile
from app.services.scoring.verticals import apply_learned_weights

from app.services.ai.analyzers.gemini_video_results import (
    _apply_final_v1_result,
    _apply_v2_result,
    _parse_json_response_text,
    _response_usage_metadata,
)
from app.services.ai.analyzers.gemini_video_prompts import (
    _video_final_v1_dynamic_prompt,
    _video_final_v1_prompt,
    _video_v2_prompt,
)
from app.services.ai.analyzers.gemini_video_legacy_prompt import _video_legacy_prompt

logger = get_logger(__name__)

GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS = max(
    60,
    int(os.environ.get("GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SEC", "900")),
)


async def analyze_youtube_with_gemini(
    url: str,
    title: str,
    creator_handle: str = "",
    *,
    schema_version: str = "legacy",
    performance_context: dict[str, Any] | None = None,
    final_v1_models: list[str] | str | None = None,
) -> dict:
    """
    Gemini YouTube analysis via File API:
    1. Download first 2min with yt-dlp
    2. Upload to Gemini File API
    3. Analyze with gemini-2.5-flash / gemini-2.5-pro (frame by frame)
    4. Delete file from Gemini
    """
    # lazy import 避免循环依赖（gemini_video.py 加载时 re-export 本函数）
    from app.services.ai.analyzers.gemini_video import (
        ProviderPressureExhausted,
        _final_v1_cache_config,
        _is_provider_pressure_error,
        final_v1_gemini_models,
    )

    result = {
        "analyzed": False, "method": "gemini_youtube",
        "content_summary": "", "content_genre": "", "content_topic": "",
        "timestamps": [], "competitor_mentions": [],
        "why_compelling": "", "hook_analysis": "",
        "target_audience": "", "production_quality": "",
        "camera_body": None, "viltrox_lens": None, "other_lens": None,
        "viltrox_detected": False, "viltrox_products_all": [],
        "marketing_potential": "", "marketing_notes": "",
        "error": None,
    }
    if not GEMINI_AVAILABLE or not gemini_client:
        result["error"] = "Gemini not available"
        return result
    if not YTDLP_AVAILABLE:
        result["error"] = "yt-dlp not available for download"
        return result

    profile_ctx = ""
    if creator_handle:
        profile = get_creator_profile(creator_handle)
        if profile.get("viltrox_lenses"):
            profile_ctx = f"\n创作者历史使用过: {', '.join(profile['viltrox_lenses'][:3])}"

    # ── Fetch subtitles for precise timestamp anchoring ──
    subtitle_ctx = ""
    subtitle_raw = fetch_youtube_subtitles(url)
    if subtitle_raw:
        subtitle_ctx = (
            "\n\n=== 字幕时间轴（真实时间戳，优先用这个定位事件）===\n"
            + subtitle_raw
            + "\n=== 字幕结束 ===\n"
            "时间戳规则：timestamps 里的 time 字段必须来自上面字幕里的真实时间点，"
            "不允许猜测或等间隔填写。"
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
            subtitle_used=bool(subtitle_raw),
            performance_context=performance_context,
        )
        final_full_prompt = _video_final_v1_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_used=bool(subtitle_raw),
            performance_context=performance_context,
        )
    elif is_v2:
        prompt = _video_v2_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_used=bool(subtitle_raw),
            performance_context=performance_context,
        )
    else:
        prompt = _video_legacy_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_raw=subtitle_raw,
        )

    gemini_file = None
    tmp_path = None

    # ── Model priority list (June 2026) ─────────────────────────────────────
    # Reality check 2026-06: 3-flash-preview LIVE (primary, cache 271/273 evidence),
    # 3.1-pro-preview LIVE (accuracy backup), 2.5-flash = stable GA fallback.
    # Keep comments synchronized with the table; stale model docs caused
    # provider-pressure recovery confusion during recycle wave N2.
    GEMINI_MODELS = [
        "gemini-3-flash-preview",    # PRIMARY — best price/perf, multimodal
        "gemini-3.1-pro-preview",    # BACKUP — highest accuracy, long videos
        "gemini-2.5-flash",          # FALLBACK — stable GA tier
    ]
    if is_final_v1:
        GEMINI_MODELS = final_v1_gemini_models(final_v1_models)

    # ===== FAST PATH: YouTube direct URL (no download, no upload) =====
    # Gemini 3 supports passing YouTube URLs directly via file_uri.
    # This bypasses yt-dlp download (saves 2-15 min per video).
    # Falls back to slow path on any exception.
    _active_file_name = None  # tracks File API resource for cleanup in finally

    if "youtu.be" in url or "youtube.com" in url:
        try:
            logger.info("gemini_fast_path_start", extra={"url": url})

            class _YouTubeDirectFile:
                """Pseudo file object passing YouTube URL directly to Gemini"""
                def __init__(self, youtube_url):
                    self.uri = youtube_url
                    self.name = None

            gemini_file = _YouTubeDirectFile(url)
            _fast_path_success = False
            _fast_path_err = None

            # Try analyzing with Gemini 3 models directly (no upload, no polling)
            for model_name in GEMINI_MODELS:
                try:
                    cache_config = None
                    cache_info: dict[str, Any] = {}
                    request_prompt = prompt
                    if is_final_v1:
                        cache_config, cache_info = _final_v1_cache_config(model_name)
                        if not cache_config:
                            request_prompt = final_full_prompt
                    def _analyze_direct(m=model_name, u=url):
                        kwargs: dict[str, Any] = {
                            "model": m,
                            "contents": [
                                genai_types.Part(
                                    file_data=genai_types.FileData(
                                        file_uri=u
                                    )
                                ),
                                request_prompt
                            ],
                        }
                        if cache_config:
                            kwargs["config"] = cache_config
                        return gemini_client.models.generate_content(**kwargs)
                    resp = await asyncio.to_thread(_analyze_direct)
                    usage_metadata = _response_usage_metadata(resp)
                    raw = resp.text.strip()
                    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                    parsed = _parse_json_response_text(raw)
                    if is_final_v1:
                        _apply_final_v1_result(
                            result,
                            parsed,
                            method=f"gemini_direct_{model_name}",
                            model=model_name,
                            usage_metadata=usage_metadata,
                            subtitle_used=bool(subtitle_raw),
                        )
                        result["context_cache"] = cache_info
                        logger.info(
                            "gemini_fast_path_final_v1_success",
                            extra={"model": model_name, "timestamps": len(result.get("timestamps") or [])},
                        )
                        _fast_path_success = True
                        break
                    if is_v2:
                        _apply_v2_result(
                            result,
                            parsed,
                            method=f"gemini_direct_{model_name}",
                            model=model_name,
                            usage_metadata=usage_metadata,
                            subtitle_used=bool(subtitle_raw),
                        )
                        logger.info(
                            "gemini_fast_path_v2_success",
                            extra={"model": model_name, "timestamps": len(result.get("timestamps") or [])},
                        )
                        _fast_path_success = True
                        break

                    # Mirror the parsing logic from slow path (Step 4)
                    result["analyzed"]             = True
                    result["method"]               = f"gemini_direct_{model_name}"
                    result["model"]                = model_name
                    result["usage_metadata"]       = usage_metadata
                    result["content_summary"]      = parsed.get("content_summary", "")
                    result["content_genre"]        = parsed.get("content_genre", "")
                    result["content_topic"]        = parsed.get("content_topic", "")
                    result["production_quality"]   = parsed.get("production_quality", "")
                    result["why_compelling"]       = parsed.get("why_compelling", "")
                    result["hook_analysis"]        = parsed.get("hook_analysis", "")
                    result["target_audience"]      = parsed.get("target_audience", "")
                    result["timestamps"]           = parsed.get("timestamps", [])
                    result["competitor_mentions"]  = parsed.get("competitor_mentions", [])
                    result["viltrox_detected"]     = parsed.get("viltrox_detected", False)
                    result["viltrox_products_all"] = parsed.get("viltrox_products_mentioned", [])
                    result["camera_body"]          = parsed.get("camera_body")
                    result["viltrox_lens"]         = parsed.get("viltrox_lens")
                    result["other_lens"]           = parsed.get("other_lens")
                    result["marketing_potential"]  = parsed.get("marketing_potential", "")
                    result["marketing_notes"]      = parsed.get("marketing_notes", "")
                    result["brand_integration_depth"] = parsed.get("brand_integration_depth", "")
                    result["type_specific_notes"]  = parsed.get("type_specific_notes", "")
                    result["vertical_category"]      = parsed.get("vertical_category", "")
                    result["vertical_quality_notes"] = parsed.get("vertical_quality_notes", "")
                    result["community_value"]         = parsed.get("community_value", 0)
                    bed = parsed.get("brand_exposure_detail", {})
                    result["logo_detected"]         = int(bool(
                        bed.get("logo_on_lens_barrel") or bed.get("logo_on_screen_overlay")
                    ))
                    result["product_closeup_count"] = bed.get("product_closeup_count", 0)
                    result["brand_mention_count"]   = bed.get("brand_mention_count", 0)
                    result["brand_exposure_detail"] = bed
                    qs = parsed.get("quality_scores", {})
                    qs = {k: v for k, v in qs.items() if isinstance(v, (int, float)) and v > 0}
                    if qs:
                        result["quality_scores"]    = qs
                        result["quality_overall"]   = parsed.get("quality_overall", 0)
                        result["quality_summary"]   = parsed.get("quality_summary", "")
                        result["reference_value"]   = parsed.get("reference_value", "")
                        result["reference_reasons"] = parsed.get("reference_reasons", [])
                        result["improvements"]      = parsed.get("improvements", [])
                    genre    = result.get("content_genre", "")
                    vertical = result.get("vertical_category", "")
                    v_key = get_vertical(genre)
                    apply_learned_weights(v_key)
                    ws = compute_weighted_scores(result.get("quality_scores", {}), genre, vertical)
                    result["brand_exposure_score"] = ws["brand_exposure_score"]
                    result["storytelling_score"]   = ws["storytelling_score"]
                    result["tech_status"]          = ws["tech_floor"]["status"]
                    result["tech_floor"]           = ws["tech_floor"]
                    result["tech_score"]           = ws["tech_score"]
                    result["marketing_score"]      = ws["marketing_score"]
                    result["vertical_tech_score"]  = ws["tech_score"]
                    result["vertical_mkt_score"]   = ws["marketing_score"]
                    result["quality_overall"]      = ws["quality_overall"] or result.get("quality_overall", 0)
                    logger.info(
                        "gemini_fast_path_success",
                        extra={
                            "model": model_name,
                            "genre": genre,
                            "vertical": v_key,
                            "timestamps": len(result["timestamps"]),
                            "brand_score": ws["brand_exposure_score"],
                            "story_score": ws["storytelling_score"],
                        },
                    )
                    _fast_path_success = True
                    break
                except Exception as e:
                    _fast_path_err = str(e)[:200]
                    logger.warning(
                        "gemini_fast_path_model_failed",
                        extra={"model": model_name, "error": _fast_path_err[:80]},
                    )
                    continue

            if _fast_path_success:
                return result  # Done! Skip slow path entirely.
            elif _fast_path_err and _is_provider_pressure_error(_fast_path_err):
                logger.warning(
                    "gemini_fast_path_provider_pressure_abort",
                    extra={"error": _fast_path_err[:120]},
                )
                raise ProviderPressureExhausted(
                    f"provider_pressure(all models tried): {_fast_path_err}"
                )
            else:
                logger.warning("gemini_fast_path_fallback_to_download")
                # Fall through to slow path below
        except ProviderPressureExhausted:
            raise
        except Exception as fast_err:
            logger.warning("gemini_fast_path_exception", extra={"error": str(fast_err)})
            # Fall through to slow path

    # ===== SLOW PATH: yt-dlp download + File API upload =====
    try:
        # Step 1: Download FULL video at 720p. Keep this below the worker
        # subprocess timeout so failures report as a download timeout, not a
        # generic Gemini child-process kill.
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = os.path.join(tmpdir, "gemini_video.mp4")
            logger.info("gemini_fileapi_download_start", extra={"url": url})

            dl_cmd = [
                YTDLP_BIN,  # 解析好的全路径(.venv/bin/yt-dlp);裸 'yt-dlp' 在 worker PATH 上找不到 → media_resolve_failed
                "-f", "best[ext=mp4][height<=720]/18/best[height<=720]/best",
                "--merge-output-format", "mp4",
                "-o", tmp_path,
                "--no-playlist",
                "--quiet",
            ]
            if YTDLP_PROXY:
                dl_cmd += ["--proxy", YTDLP_PROXY]
            dl_cmd.append(url)
            dl_proc = await asyncio.to_thread(
                lambda: subprocess.run(
                    dl_cmd,
                    capture_output=True,
                    timeout=GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS,
                )
            )
            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 1000:
                result["error"] = "yt-dlp video download failed for Gemini analysis"
                logger.warning("gemini_fileapi_download_failed", extra={"url": url})
                return result

            file_size_mb = os.path.getsize(tmp_path) / 1024 / 1024
            logger.info("gemini_fileapi_upload_start", extra={"size_mb": round(file_size_mb, 1)})

            # Step 2: Upload to Gemini File API
            # BUG FIX: capture upload errors explicitly — a failed upload returns
            # an object whose .name may be None, causing files.get() to 404.
            try:
                def _upload():
                    return gemini_client.files.upload(
                        file=tmp_path,
                        config={"mime_type": "video/mp4"}
                    )
                gemini_file = await asyncio.to_thread(_upload)
            except Exception as upload_err:
                result["error"] = f"Gemini File API upload failed: {upload_err}"
                logger.warning("gemini_fileapi_upload_failed", extra={"error": str(upload_err)})
                return result

            # Validate upload returned a usable file object
            if not gemini_file or not getattr(gemini_file, "name", None):
                result["error"] = "Gemini upload returned empty file object"
                logger.warning("gemini_fileapi_upload_invalid_file", extra={"file": str(gemini_file)})
                return result

            logger.info(
                "gemini_fileapi_upload_complete",
                extra={"file_name": gemini_file.name, "uri": gemini_file.uri},
            )

            # Step 3: Wait for file to be ACTIVE (usually 5-60 seconds for video)
            # BUG FIX 1: files.get() itself can throw 404 — wrap in try-except.
            # BUG FIX 2: Exit immediately on FAILED state instead of burning 60s.
            for poll_attempt in range(30):   # max 90s (30 × 3s)
                try:
                    def _check(name=gemini_file.name):
                        return gemini_client.files.get(name=name)
                    polled = await asyncio.to_thread(_check)
                    gemini_file = polled
                except Exception as poll_err:
                    # 404 here means the file disappeared (upload may have silently failed)
                    result["error"] = f"files.get() 404 during polling — upload may have failed: {poll_err}"
                    logger.warning(
                        "gemini_fileapi_poll_error",
                        extra={"attempt": poll_attempt, "error": str(poll_err)},
                    )
                    return result

                state = getattr(gemini_file.state, "name", str(gemini_file.state))
                logger.info("gemini_fileapi_poll", extra={"attempt": poll_attempt + 1, "state": state})

                if state == "ACTIVE":
                    break
                if state == "FAILED":
                    # File processing failed on Google's side — no point waiting
                    result["error"] = f"Gemini file processing FAILED (state={state}). Try re-uploading."
                    logger.warning(
                        "gemini_fileapi_processing_failed",
                        extra={"attempt": poll_attempt + 1, "state": state},
                    )
                    return result
                await asyncio.sleep(3)
            else:
                result["error"] = f"Gemini file ACTIVE timeout after 90s (final state={state})"
                logger.warning("gemini_fileapi_poll_timeout", extra={"state": state})
                return result

            logger.info("gemini_fileapi_active", extra={"uri": gemini_file.uri})

            # BUG FIX 3: Validate uri before calling generate_content.
            # A file can be ACTIVE but have a malformed uri (edge case seen in SDK v0.8+).
            if not getattr(gemini_file, "uri", None):
                result["error"] = "Gemini file ACTIVE but uri is empty — cannot call generate_content"
                logger.warning("gemini_fileapi_empty_uri", extra={"file_name": gemini_file.name})
                return result

            # Step 4: Analyze with Gemini — try stable models in priority order
            # Capture the file name string for the finally-block delete guard
            _active_file_name = gemini_file.name
            MODELS = GEMINI_MODELS
            last_err = ""
            for model_name in MODELS:
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
                                genai_types.Part.from_uri(
                                    file_uri=f.uri,
                                    mime_type="video/mp4"
                                ),
                                request_prompt
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
                            method=f"gemini_fileapi_{model_name}",
                            model=model_name,
                            usage_metadata=usage_metadata,
                            subtitle_used=bool(subtitle_raw),
                        )
                        result["context_cache"] = cache_info
                        logger.info(
                            "gemini_fileapi_final_v1_success",
                            extra={"model": model_name, "timestamps": len(result.get("timestamps") or [])},
                        )
                        break
                    if is_v2:
                        _apply_v2_result(
                            result,
                            parsed,
                            method=f"gemini_fileapi_{model_name}",
                            model=model_name,
                            usage_metadata=usage_metadata,
                            subtitle_used=bool(subtitle_raw),
                        )
                        logger.info(
                            "gemini_fileapi_v2_success",
                            extra={"model": model_name, "timestamps": len(result.get("timestamps") or [])},
                        )
                        break

                    result["analyzed"]             = True
                    result["method"]               = f"gemini_fileapi_{model_name}"
                    result["model"]                = model_name
                    result["usage_metadata"]       = usage_metadata
                    result["content_summary"]      = parsed.get("content_summary", "")
                    result["content_genre"]        = parsed.get("content_genre", "")
                    result["content_topic"]        = parsed.get("content_topic", "")
                    result["production_quality"]   = parsed.get("production_quality", "")
                    result["why_compelling"]       = parsed.get("why_compelling", "")
                    result["hook_analysis"]        = parsed.get("hook_analysis", "")
                    result["target_audience"]      = parsed.get("target_audience", "")
                    result["timestamps"]           = parsed.get("timestamps", [])
                    result["competitor_mentions"]  = parsed.get("competitor_mentions", [])
                    result["viltrox_detected"]     = parsed.get("viltrox_detected", False)
                    result["viltrox_products_all"] = parsed.get("viltrox_products_mentioned", [])
                    result["camera_body"]          = parsed.get("camera_body")
                    result["viltrox_lens"]         = parsed.get("viltrox_lens")
                    result["other_lens"]           = parsed.get("other_lens")
                    result["marketing_potential"]  = parsed.get("marketing_potential", "")
                    result["marketing_notes"]      = parsed.get("marketing_notes", "")
                    result["brand_integration_depth"] = parsed.get("brand_integration_depth", "")
                    result["type_specific_notes"]  = parsed.get("type_specific_notes", "")
                    # ── Vertical community fields ──
                    result["vertical_category"]      = parsed.get("vertical_category", "")
                    result["vertical_quality_notes"] = parsed.get("vertical_quality_notes", "")
                    result["community_value"]         = parsed.get("community_value", 0)
                    # ── Brand exposure detail ──
                    bed = parsed.get("brand_exposure_detail", {})
                    result["logo_detected"]         = int(bool(
                        bed.get("logo_on_lens_barrel") or bed.get("logo_on_screen_overlay")
                    ))
                    result["product_closeup_count"] = bed.get("product_closeup_count", 0)
                    result["brand_mention_count"]   = bed.get("brand_mention_count", 0)
                    result["brand_exposure_detail"] = bed
                    # ── Quality scores — strip instruction key ──
                    qs = parsed.get("quality_scores", {})
                    qs = {k: v for k, v in qs.items() if isinstance(v, (int, float)) and v > 0}
                    if qs:
                        result["quality_scores"]    = qs
                        result["quality_overall"]   = parsed.get("quality_overall", 0)
                        result["quality_summary"]   = parsed.get("quality_summary", "")
                        result["reference_value"]   = parsed.get("reference_value", "")
                        result["reference_reasons"] = parsed.get("reference_reasons", [])
                        result["improvements"]      = parsed.get("improvements", [])
                    # ── Compute three-axis scores ──
                    genre    = result.get("content_genre", "")
                    vertical = result.get("vertical_category", "")
                    v_key = get_vertical(genre)
                    apply_learned_weights(v_key)
                    ws = compute_weighted_scores(result.get("quality_scores", {}), genre, vertical)
                    result["brand_exposure_score"] = ws["brand_exposure_score"]
                    result["storytelling_score"]   = ws["storytelling_score"]
                    result["tech_status"]          = ws["tech_floor"]["status"]
                    result["tech_floor"]           = ws["tech_floor"]
                    result["tech_score"]           = ws["tech_score"]
                    result["marketing_score"]      = ws["marketing_score"]
                    result["vertical_tech_score"]  = ws["tech_score"]
                    result["vertical_mkt_score"]   = ws["marketing_score"]
                    result["quality_overall"]      = ws["quality_overall"] or result.get("quality_overall", 0)
                    logger.info(
                        "gemini_fileapi_success",
                        extra={
                            "model": model_name,
                            "genre": genre,
                            "vertical": v_key,
                            "timestamps": len(result["timestamps"]),
                            "brand_score": ws["brand_exposure_score"],
                            "story_score": ws["storytelling_score"],
                            "tech_floor": ws["tech_floor"]["status"],
                            "logo_detected": bool(result.get("logo_detected")),
                            "quality_dims": len(result.get("quality_scores", {})),
                        },
                    )
                    break
                except Exception as e:
                    import traceback
                    last_err = str(e)
                    logger.warning(
                        "gemini_fileapi_model_failed",
                        extra={
                            "model": model_name,
                            "error": str(e)[:80],
                            "traceback_tail": traceback.format_exc()[-500:],
                        },
                    )
                    continue

            if not result["analyzed"]:
                result["error"] = last_err

    except Exception as e:
        result["error"] = str(e)
        logger.exception("gemini_analysis_failed")
    finally:
        # Step 5: Always delete file from Gemini File API to avoid storage charges.
        # BUG FIX: Only delete if the file was successfully registered (has a name).
        # Using the captured _active_file_name string avoids holding a reference to
        # the mutable gemini_file object that polling may have partially updated.
        _file_to_delete = getattr(gemini_file, "name", None) if gemini_file else None
        if _file_to_delete:
            try:
                def _delete(name=_file_to_delete):
                    gemini_client.files.delete(name=name)
                await asyncio.to_thread(_delete)
                logger.info("gemini_fileapi_deleted", extra={"file_name": _file_to_delete})
            except Exception as del_err:
                # 404 here is harmless — file was already gone or never fully created
                logger.warning("gemini_fileapi_delete_skipped", extra={"error": str(del_err)})

    return result
