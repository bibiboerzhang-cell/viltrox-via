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
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from app.core.gemini_models import DEFAULT_VIDEO_GEMINI_MODEL
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
_YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_PATH_ROUTES = frozenset({"shorts", "embed", "live", "v"})


def _ytdlp_max_height() -> int:
    """慢路下载分辨率上限(默认 720 行为不变)。Gemini 视频帧按固定 token/帧降采样,480p 对分析输入
    几乎无差而下载+File API 上传字节量 ~2-3x 更小;是否降档由运维用 env 决定,代码默认不动。"""

    try:
        value = int(os.environ.get("GEMINI_VIDEO_YTDLP_MAX_HEIGHT", "720") or "720")
    except ValueError:
        value = 720
    return max(240, min(1080, value))


def _ytdlp_download_format(max_height: int) -> str:
    return f"best[ext=mp4][height<={max_height}]/18/best[height<={max_height}]/best"


def _ytdlp_cookies_args() -> list[str]:
    """YTDLP_COOKIES_FILE 指向存在的 cookies.txt 时透传 --cookies(治「Sign in to confirm you're
    not a bot」类下载失败);未设/文件不存在 → 空,行为不变。"""

    path = str(os.environ.get("YTDLP_COOKIES_FILE", "") or "").strip()
    if path and os.path.isfile(path):
        return ["--cookies", path]
    return []


def canonical_youtube_url(url: str) -> tuple[str, str]:
    """把任意形态的 YouTube 链接规范成 ``https://www.youtube.com/watch?v=<id>``。

    剖面坐实(2026-08,隔离库 vkpi_llm_calls):直链 33 次失败里 18 次是带 ``&t=314s`` /
    ``&pp=ygU...`` 等附加参数的 watch 链接,Gemini file_uri 约 2.5s 秒拒;成功的 47 次零带参。
    秒拒后整条任务掉进 yt-dlp 下载 + File API 慢路(p50 98s→269s,或干脆下载失败)。
    返回 ``(canonical_url, video_id)``;解析不出 11 位 id 时原样返回 ``(url, "")``,不改行为。
    """

    raw = str(url or "").strip()
    if not raw:
        return raw, ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw, ""
    host = (parsed.hostname or "").lower().rstrip(".")
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    parts = [part for part in parsed.path.split("/") if part]
    lowered = [part.lower() for part in parts]
    video_id = ""
    if host == "youtu.be" and parts:
        video_id = parts[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if lowered[:1] == ["watch"]:
            video_id = str((parse_qs(parsed.query).get("v") or [""])[0] or "")
        elif len(parts) >= 2 and lowered[0] in _YOUTUBE_PATH_ROUTES:
            video_id = parts[1]
    video_id = video_id.strip()
    if not _YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
        return raw, ""
    return f"https://www.youtube.com/watch?v={video_id}", video_id


async def analyze_youtube_with_gemini(
    url: str,
    title: str,
    creator_handle: str = "",
    *,
    schema_version: str = "legacy",
    performance_context: dict[str, Any] | None = None,
    final_v1_models: list[str] | str | None = None,
    models: list[str] | str | None = None,
    llm_context: dict[str, Any] | None = None,
    authorization_checkpoint: Callable[[str], None] | None = None,
) -> dict:
    """
    Gemini YouTube analysis via File API:
    1. Download first 2min with yt-dlp
    2. Upload to Gemini File API
    3. Analyze with DEFAULT_VIDEO_GEMINI_MODEL (core/gemini_models; frame by frame)
    4. Delete file from Gemini

    ``authorization_checkpoint(stage)`` runs before subtitles, before every
    direct-URL attempt, before the slow download, before the File API upload
    and before every File API attempt.  ``AnalysisScopeRevoked`` stops the
    chain: the result carries ``scope_revoked`` and no later stage runs.
    """
    # lazy import 避免循环依赖（gemini_video.py 加载时 re-export 本函数）
    from app.services.ai.analyzers.gemini_video import (
        ProviderPressureExhausted,
        _final_v1_cache_config,
        _is_provider_pressure_error,
        _retry_after_context_cache_error,
        _scope_guard,
        _stage_add,
        _strict_generate_content,
        _video_generate_config,
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
        "llm_attempts": [],
        "cost_authority": "llm_production_google_generate_content_v1",
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

    scope_passes = _scope_guard(result, authorization_checkpoint)
    # ── Fetch subtitles for precise timestamp anchoring ──
    subtitle_ctx = ""
    if not scope_passes("youtube_subtitles"):
        return result
    subtitles_started = time.monotonic()
    subtitle_raw = fetch_youtube_subtitles(url)
    _stage_add(result, "subtitles", subtitles_started)
    result["subtitle_chars"] = len(subtitle_raw or "")
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

    # ── Model list (2026-08 模型升级刀) ─────────────────────────────────────
    # 默认只有一个精确模型 DEFAULT_VIDEO_GEMINI_MODEL(core/gemini_models,
    # 随 APIFY_WORKER_GEMINI_MODEL):预检与执行必须是同一 binding,多模型
    # 兜底链会让一次预检放行另一个提供方请求。需要链时由 models /
    # final_v1_models 参数或 GEMINI_FINAL_V1_MODELS env 显式给出。
    # 注释必须与代码同步:过期模型注释曾在 recycle wave N2 误导过压力恢复排查。
    GEMINI_MODELS = [DEFAULT_VIDEO_GEMINI_MODEL]
    if models is not None:
        GEMINI_MODELS = final_v1_gemini_models(models)
    elif is_final_v1:
        GEMINI_MODELS = final_v1_gemini_models(final_v1_models)
    attempt_total = len(GEMINI_MODELS) * (
        2 if "youtu.be" in url or "youtube.com" in url else 1
    )

    # ===== FAST PATH: YouTube direct URL (no download, no upload) =====
    # Gemini 3 supports passing YouTube URLs directly via file_uri.
    # This bypasses yt-dlp download (saves 2-15 min per video).
    # Falls back to slow path on any exception.
    _active_file_name = None  # tracks File API resource for cleanup in finally

    if "youtu.be" in url or "youtube.com" in url:
        # 直链诊断(零成本):每次模型尝试的错误原文落 result,剖面脚本据此统计直链命中率与降级真因。
        # 旧码只 logger.warning,线上 570 条 final_v1 里没有一条能回答「直链为什么失败」。
        # 刀①:直链只喂规范化 watch?v= 链接(带 &t=/&pp=/?si= 的原链是直链秒拒的头号真因)。
        direct_url, direct_video_id = canonical_youtube_url(url)
        direct_diag: dict[str, Any] = {
            "attempted": True,
            "success": False,
            "attempts": [],
            "fallback_reason": "",
            "url": direct_url,
            "url_canonicalized": bool(direct_video_id) and direct_url != url,
        }
        result["youtube_direct"] = direct_diag
        try:
            logger.info(
                "gemini_fast_path_start",
                extra={"url": direct_url, "canonicalized": direct_diag["url_canonicalized"]},
            )

            class _YouTubeDirectFile:
                """Pseudo file object passing YouTube URL directly to Gemini"""
                def __init__(self, youtube_url):
                    self.uri = youtube_url
                    self.name = None

            gemini_file = _YouTubeDirectFile(direct_url)
            _fast_path_success = False
            _fast_path_err = None

            # Try analyzing with Gemini 3 models directly (no upload, no polling)
            direct_plan = list(enumerate(GEMINI_MODELS, start=1))
            direct_cache_retried: set[str] = set()
            direct_pos = 0
            while direct_pos < len(direct_plan):
                attempt_index, model_name = direct_plan[direct_pos]
                direct_pos += 1
                if not scope_passes("youtube_direct_attempt"):
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
                    def _analyze_direct(m=model_name, u=direct_url):
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
                        if request_config:
                            kwargs["config"] = request_config
                        return _strict_generate_content(
                            model_name=m,
                            contents=kwargs["contents"],
                            config=kwargs.get("config"),
                            prompt=request_prompt,
                            performance_context=performance_context,
                            llm_context=llm_context,
                            subphase="youtube_uri_fast_generation",
                            attempt_index=attempt_index,
                            attempt_total=attempt_total,
                            attempt_log=result["llm_attempts"],
                        )
                    resp = await asyncio.to_thread(_analyze_direct)
                    direct_diag["attempts"].append(
                        {
                            "model": model_name,
                            "ok": True,
                            "error": "",
                            "elapsed_ms": _stage_add(result, "youtube_direct", attempt_started),
                        }
                    )
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
                    last_attempt = direct_diag["attempts"][-1] if direct_diag["attempts"] else None
                    if last_attempt and last_attempt.get("model") == model_name and last_attempt.get("ok"):
                        # 响应已回但 JSON/结构校验抛错:沿用已计的耗时,改标失败(不重复累加阶段时间)
                        last_attempt["ok"] = False
                        last_attempt["error"] = _fast_path_err
                    else:
                        direct_diag["attempts"].append(
                            {
                                "model": model_name,
                                "ok": False,
                                "error": _fast_path_err,
                                "elapsed_ms": _stage_add(result, "youtube_direct", attempt_started),
                            }
                        )
                    logger.warning(
                        "gemini_fast_path_model_failed",
                        extra={"model": model_name, "error": _fast_path_err[:80]},
                    )
                    if _retry_after_context_cache_error(e, cache_info, model_name, direct_cache_retried):
                        direct_plan.insert(direct_pos, (attempt_index, model_name))
                    continue

            if _fast_path_success:
                direct_diag["success"] = True
                return result  # Done! Skip slow path entirely.
            elif _fast_path_err and _is_provider_pressure_error(_fast_path_err):
                direct_diag["fallback_reason"] = f"provider_pressure: {_fast_path_err}"
                logger.warning(
                    "gemini_fast_path_provider_pressure_abort",
                    extra={"error": _fast_path_err[:120]},
                )
                raise ProviderPressureExhausted(
                    f"provider_pressure(all models tried): {_fast_path_err}"
                )
            else:
                direct_diag["fallback_reason"] = _fast_path_err or "no_model_succeeded"
                logger.warning("gemini_fast_path_fallback_to_download")
                # Fall through to slow path below
        except ProviderPressureExhausted:
            raise
        except Exception as fast_err:
            direct_diag["fallback_reason"] = f"fast_path_exception: {str(fast_err)[:200]}"
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
            if not scope_passes("youtube_download"):
                return result
            logger.info("gemini_fileapi_download_start", extra={"url": url})

            download_max_height = _ytdlp_max_height()
            dl_cmd = [
                YTDLP_BIN,  # 解析好的全路径(.venv/bin/yt-dlp);裸 'yt-dlp' 在 worker PATH 上找不到 → media_resolve_failed
                "-f", _ytdlp_download_format(download_max_height),
                "--merge-output-format", "mp4",
                "-o", tmp_path,
                "--no-playlist",
                "--quiet",
            ]
            if YTDLP_PROXY:
                dl_cmd += ["--proxy", YTDLP_PROXY]
            dl_cmd += _ytdlp_cookies_args()
            dl_cmd.append(url)
            download_started = time.monotonic()
            dl_proc = await asyncio.to_thread(
                lambda: subprocess.run(
                    dl_cmd,
                    capture_output=True,
                    timeout=GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS,
                )
            )
            download_ms = _stage_add(result, "download", download_started)
            downloaded_bytes = int(os.path.getsize(tmp_path)) if os.path.exists(tmp_path) else 0
            # 下载诊断(零成本):旧码把 yt-dlp stderr 整个丢掉,线上 167 条「yt-dlp video download failed」
            # 无一能区分 bot 验证/地区限制/代理断流;现把 returncode + stderr 尾巴落 result。
            _stderr_raw = getattr(dl_proc, "stderr", b"") if dl_proc is not None else b""
            if isinstance(_stderr_raw, bytes):
                _stderr_text = _stderr_raw.decode("utf-8", errors="ignore")
            else:
                _stderr_text = str(_stderr_raw or "")
            result["download_diagnostics"] = {
                "tool": "yt-dlp",
                "returncode": getattr(dl_proc, "returncode", None) if dl_proc is not None else None,
                "elapsed_ms": download_ms,
                "bytes": downloaded_bytes,
                "proxy": bool(YTDLP_PROXY),
                "max_height": download_max_height,
                "cookies": "--cookies" in dl_cmd,
                "stderr_tail": _stderr_text[-600:],
            }
            if downloaded_bytes < 1000:
                result["error"] = "yt-dlp video download failed for Gemini analysis"
                logger.warning(
                    "gemini_fileapi_download_failed",
                    extra={"url": url, "stderr_tail": _stderr_text[-300:]},
                )
                return result

            file_size_mb = downloaded_bytes / 1024 / 1024
            logger.info("gemini_fileapi_upload_start", extra={"size_mb": round(file_size_mb, 1)})

            # Step 2: Upload to Gemini File API
            # BUG FIX: capture upload errors explicitly — a failed upload returns
            # an object whose .name may be None, causing files.get() to 404.
            if not scope_passes("file_api_upload"):
                return result
            upload_started = time.monotonic()
            try:
                def _upload():
                    return gemini_client.files.upload(
                        file=tmp_path,
                        config={"mime_type": "video/mp4"}
                    )
                gemini_file = await asyncio.to_thread(_upload)
            except Exception as upload_err:
                _stage_add(result, "upload", upload_started)
                result["error"] = f"Gemini File API upload failed: {upload_err}"
                logger.warning("gemini_fileapi_upload_failed", extra={"error": str(upload_err)})
                return result
            _stage_add(result, "upload", upload_started)

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
            active_wait_started = time.monotonic()
            for poll_attempt in range(30):   # max 90s (30 × 3s)
                try:
                    def _check(name=gemini_file.name):
                        return gemini_client.files.get(name=name)
                    polled = await asyncio.to_thread(_check)
                    gemini_file = polled
                except Exception as poll_err:
                    _stage_add(result, "file_active_wait", active_wait_started)
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
                    _stage_add(result, "file_active_wait", active_wait_started)
                    # File processing failed on Google's side — no point waiting
                    result["error"] = f"Gemini file processing FAILED (state={state}). Try re-uploading."
                    logger.warning(
                        "gemini_fileapi_processing_failed",
                        extra={"attempt": poll_attempt + 1, "state": state},
                    )
                    return result
                await asyncio.sleep(3)
            else:
                _stage_add(result, "file_active_wait", active_wait_started)
                result["error"] = f"Gemini file ACTIVE timeout after 90s (final state={state})"
                logger.warning("gemini_fileapi_poll_timeout", extra={"state": state})
                return result
            _stage_add(result, "file_active_wait", active_wait_started)

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
            file_plan = list(enumerate(MODELS, start=1))
            file_cache_retried: set[str] = set()
            file_pos = 0
            while file_pos < len(file_plan):
                model_offset, model_name = file_plan[file_pos]
                file_pos += 1
                if not scope_passes("file_api_attempt"):
                    return result
                attempt_started = time.monotonic()
                cache_info = {}
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
                                genai_types.Part.from_uri(
                                    file_uri=f.uri,
                                    mime_type="video/mp4"
                                ),
                                request_prompt
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
                            subphase="youtube_file_fallback_generation",
                            attempt_index=len(GEMINI_MODELS) + model_offset,
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
                    _stage_add(result, "generation", attempt_started)
                    last_err = str(e)
                    logger.warning(
                        "gemini_fileapi_model_failed",
                        extra={
                            "model": model_name,
                            "error": str(e)[:80],
                            "traceback_tail": traceback.format_exc()[-500:],
                        },
                    )
                    if _retry_after_context_cache_error(e, cache_info, model_name, file_cache_retried):
                        file_plan.insert(file_pos, (model_offset, model_name))
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
            cleanup_started = time.monotonic()
            try:
                def _delete(name=_file_to_delete):
                    gemini_client.files.delete(name=name)
                await asyncio.to_thread(_delete)
                logger.info("gemini_fileapi_deleted", extra={"file_name": _file_to_delete})
            except Exception as del_err:
                # 404 here is harmless — file was already gone or never fully created
                logger.warning("gemini_fileapi_delete_skipped", extra={"error": str(del_err)})
            _stage_add(result, "cleanup", cleanup_started)

    return result
