"""本地视频文件 → Gemini File API 梯子(从 claude_vision.analyze_url_content_smart 抽出)。

Layer 2 的首选路线:yt-dlp/直链下载到本地后上传 File API,等 ACTIVE,再用与 YouTube
直读同结构的 prompt 跑一次 generate_content,把结果合进 smart result(字段同
YouTube Gemini 路径)。失败返回 False,由调用方回落到 Claude 关键帧分析。

2026-08-23 C3 收口:generate_content 走 llm_production.generate_google_content
(任务绑定 local_file_video → 精确 gemini-3.6-flash;就绪门 + 预算预留/台账/结算)。
3.x 家族只认 thinking_level="minimal"(thinking_budget=0 会 400);不传 temperature。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.core.logging import get_logger
from app.platform import llm_production
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE, gemini_client as _gemini_client
try:
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - SDK optional in some test envs
    genai_types = None
from app.services.scoring.creator import get_creator_profile
from app.services.scoring.core import compute_weighted_scores
from app.services.scraping.ytdlp import fetch_youtube_subtitles

logger = get_logger(__name__)

# Legacy local-file Gemini ladder (layer 2 File API). 3.x family needs thinking_level=minimal.
LOCAL_FILE_GEMINI_MODEL = "gemini-3.6-flash"
LOCAL_FILE_GEMINI_TASK_BINDING = "local_file_video"
LOCAL_FILE_GEMINI_MAX_OUTPUT_TOKENS = 4096
# 预留估算与 gemini_video 同口径:已知时长按 300 token/秒保守估;未知时长按满上下文预留。
LOCAL_FILE_RESERVE_TOKENS_PER_SECOND = 300
_FILE_ACTIVE_POLLS = 20
_FILE_ACTIVE_POLL_SECONDS = 3

_LOCAL_PROMPT_TEMPLATE = """你是 Viltrox 品牌内容分析师。仔细观看这个完整视频。{profile_ctx}{subtitle_ctx}
平台: {platform} | 标题: {title}

第一步识别内容类型: review/cinematic/tutorial/comparison/vlog/unboxing/showcase/bts
第二步按类型标准评估，禁止生成 00:00/00:05 等间隔时间戳。

评分标准（严格）：9-10分=TOP 5-10%；8分=良好(25%)；7分=普通(30%)；6分=有缺陷(20%)；5分以下=严重问题。

只返回JSON，不含Markdown:
{{
  "content_genre": "review/cinematic/tutorial/comparison/vlog/unboxing/showcase/bts",
  "content_type_cn": "类型中文名",
  "content_summary": "3句中文：类型+内容+亮点",
  "production_quality": "amateur/semi-pro/professional/broadcast",
  "vertical_category": "wedding/food/lifestyle/review/cinematic/sports/travel/portrait/tutorial/commercial",
  "viltrox_detected": true,
  "viltrox_products_mentioned": ["精确型号"],
  "camera_body": "型号或null",
  "viltrox_lens": "型号或null",
  "other_lens": "型号或null",
  "timestamps": [
    {{"time": "MM:SS", "event": "中文具体事件", "type": "viltrox/competitor/camera/key_moment/intro/conclusion"}}
  ],
  "brand_exposure_detail": {{
    "logo_on_lens_barrel": false,
    "logo_on_screen_overlay": false,
    "logo_in_thumbnail": false,
    "product_closeup_count": 0,
    "brand_mention_count": 0,
    "product_screen_time_pct": 0,
    "notes": "中文说明"
  }},
  "quality_scores": {{
    "exposure": 7, "stability": 7, "color_grade": 6, "composition": 7,
    "lighting": 6, "editing": 7, "storytelling": 6, "hook": 7,
    "viltrox_branding": 7, "logo_visibility": 6, "product_screen_time": 6,
    "close_up_quality": 7, "audience_fit": 7, "authenticity": 7, "conclusion_strength": 6
  }},
  "quality_overall": 7,
  "quality_summary": "2句中文：品牌曝光亮点+故事说服力不足",
  "marketing_potential": "high/medium/low",
  "marketing_notes": "转化分析（中文）",
  "reference_value": "high/medium/low",
  "improvements": [
    {{"area": "品牌曝光", "priority": "high", "timestamp": "00:05", "problem": "具体问题", "suggestion": "具体方案（中文）", "expected_improvement": "预期效果"}}
  ]
}}"""


def build_local_video_prompt(*, url: str, title: str, platform: str, creator_handle: str) -> str:
    subtitle_raw = fetch_youtube_subtitles(url) if "youtube" in str(url or "").lower() else ""
    subtitle_ctx = (
        "\n\n=== 字幕时间轴 ===\n" + subtitle_raw + "\n=== 字幕结束 ===\n"
        "timestamps 必须来自字幕真实时间，禁止等间隔填写。"
        if subtitle_raw else ""
    )
    profile_ctx = ""
    if creator_handle:
        prof = get_creator_profile(creator_handle)
        if prof.get("viltrox_lenses"):
            profile_ctx = f"\n创作者历史使用过: {', '.join(prof['viltrox_lenses'][:3])}"
    return _LOCAL_PROMPT_TEMPLATE.format(
        profile_ctx=profile_ctx,
        subtitle_ctx=subtitle_ctx,
        platform=platform,
        title=title or url,
    )


def local_video_input_token_estimate(prompt: str, duration_seconds: Any) -> int:
    try:
        duration = int(float(duration_seconds)) if duration_seconds not in (None, "") else 0
    except (TypeError, ValueError):
        duration = 0
    text_tokens = max(1, len(str(prompt or "")) // 3) + 2048
    if duration <= 0:
        return llm_production.GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP
    return min(
        llm_production.GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP,
        max(1, text_tokens + max(60, duration) * LOCAL_FILE_RESERVE_TOKENS_PER_SECOND),
    )


def local_video_generate_config() -> Any:
    """Minimal thinking for the 3.x family; no sampling params (temperature deprecated)."""
    return genai_types.GenerateContentConfig(
        thinking_config=genai_types.ThinkingConfig(thinking_level="minimal"),
    )


def merge_local_gemini_result(result: dict[str, Any], parsed: dict[str, Any], model_name: str, platform: str) -> dict[str, Any]:
    """Merge one parsed local-file Gemini JSON into the smart result (same fields as YouTube Gemini)."""
    for field in ["content_genre", "content_type_cn", "content_summary",
                  "production_quality", "vertical_category", "marketing_potential",
                  "marketing_notes", "reference_value"]:
        if parsed.get(field):
            result[field] = parsed[field]
    if parsed.get("viltrox_products_mentioned"):
        result["viltrox_products_all"] = parsed["viltrox_products_mentioned"]
    for f in ["camera_body", "viltrox_lens", "other_lens"]:
        if parsed.get(f) and not result.get(f):
            result[f] = parsed[f]
    if parsed.get("timestamps"):
        result["timestamps"] = parsed["timestamps"]
    bed = parsed.get("brand_exposure_detail", {})
    result["logo_detected"] = int(bool(bed.get("logo_on_lens_barrel") or bed.get("logo_on_screen_overlay")))
    result["product_closeup_count"] = bed.get("product_closeup_count", 0)
    result["brand_mention_count"] = bed.get("brand_mention_count", 0)
    result["brand_exposure_detail"] = bed
    qs = {k: v for k, v in parsed.get("quality_scores", {}).items()
          if isinstance(v, (int, float)) and v > 0}
    if qs:
        result["quality_scores"] = qs
        result["quality_overall"] = parsed.get("quality_overall", 0)
        result["quality_summary"] = parsed.get("quality_summary", "")
        result["improvements"] = parsed.get("improvements", [])
    ws = compute_weighted_scores(
        result.get("quality_scores", {}),
        result.get("content_genre", ""),
        result.get("vertical_category", ""),
    )
    result["brand_exposure_score"] = ws["brand_exposure_score"]
    result["storytelling_score"] = ws["storytelling_score"]
    result["tech_status"] = ws["tech_floor"]["status"]
    result["tech_floor"] = ws["tech_floor"]
    result["tech_score"] = ws["tech_score"]
    result["marketing_score"] = ws["marketing_score"]
    result["analyzed"] = True
    result["method"] = f"gemini_fileapi_{platform}_{model_name}"
    result["layers_used"].append(f"gemini_{model_name}")
    logger.info(
        "smart analysis | layer 2 Gemini ok | model=%s | brand=%s | story=%s | tech_floor=%s | qs=%s",
        model_name,
        ws["brand_exposure_score"],
        ws["storytelling_score"],
        ws["tech_floor"]["status"],
        f"yes({len(qs)}dims)" if qs else "no",
    )
    return ws


def generate_local_video_analysis(
    *,
    gfile: Any,
    prompt: str,
    model_name: str,
    duration_seconds: Any,
    title: str,
    platform: str,
) -> Any:
    """One strict generate_content attempt on an ACTIVE File API upload."""
    return llm_production.generate_google_content(
        client=_gemini_client,
        contents=[
            genai_types.Part.from_uri(file_uri=gfile.uri, mime_type="video/mp4"),
            prompt,
        ],
        config=local_video_generate_config(),
        model=model_name,
        purpose="local_file_video",
        max_output_tokens=LOCAL_FILE_GEMINI_MAX_OUTPUT_TOKENS,
        estimated_input_tokens=local_video_input_token_estimate(prompt, duration_seconds),
        metadata={
            "task_binding": LOCAL_FILE_GEMINI_TASK_BINDING,
            "surface": "audit_pipeline",
            "phase": "video_analysis",
            "subphase": "local_file_gemini",
            "platform": str(platform or "")[:80],
            "target_label": str(title or "local video")[:160],
        },
    )


async def analyze_local_video_with_gemini_file_api(
    *,
    video_path: str,
    url: str,
    title: str,
    platform: str,
    creator_handle: str,
    duration_seconds: Any,
    result: dict[str, Any],
) -> bool:
    """Upload ``video_path`` to the Gemini File API and merge one analysis into ``result``.

    Returns True when a model result was merged; False means the caller should
    fall back to Claude frame analysis.  Never raises.
    """
    if not GEMINI_AVAILABLE or _gemini_client is None or genai_types is None:
        return False
    gemini_ok = False
    try:
        logger.info("smart analysis | layer 2 Gemini File API | platform=%s", platform)

        def _upload_local():
            return _gemini_client.files.upload(file=video_path, config={"mime_type": "video/mp4"})

        gfile = await asyncio.to_thread(_upload_local)
        for _ in range(_FILE_ACTIVE_POLLS):
            def _chk(f=gfile):
                return _gemini_client.files.get(name=f.name)

            gfile = await asyncio.to_thread(_chk)
            if gfile.state.name == "ACTIVE":
                break
            await asyncio.sleep(_FILE_ACTIVE_POLL_SECONDS)

        if gfile.state.name == "ACTIVE":
            local_prompt = build_local_video_prompt(
                url=url, title=title, platform=platform, creator_handle=creator_handle
            )
            for model_name in (LOCAL_FILE_GEMINI_MODEL,):
                try:
                    resp = await asyncio.to_thread(
                        lambda m=model_name, f=gfile: generate_local_video_analysis(
                            gfile=f,
                            prompt=local_prompt,
                            model_name=m,
                            duration_seconds=duration_seconds,
                            title=title or url,
                            platform=platform,
                        )
                    )
                    raw = str(getattr(resp, "text", "") or "").strip()
                    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                    parsed = json.loads(raw)
                    merge_local_gemini_result(result, parsed, model_name, platform)
                    gemini_ok = True
                    break
                except Exception as e:
                    logger.warning("smart analysis | layer 2 Gemini failed | model=%s | error=%s", model_name, str(e)[:60])
                    continue

        try:
            await asyncio.to_thread(lambda f=gfile: _gemini_client.files.delete(name=f.name))
        except Exception as exc:
            logger.debug("gemini file cleanup failed: %s", exc)
    except Exception as e:
        logger.warning("smart analysis | layer 2 Gemini upload error: %s", e)
    return gemini_ok


__all__ = [
    "LOCAL_FILE_GEMINI_MODEL",
    "LOCAL_FILE_GEMINI_TASK_BINDING",
    "analyze_local_video_with_gemini_file_api",
    "build_local_video_prompt",
    "generate_local_video_analysis",
    "local_video_generate_config",
    "local_video_input_token_estimate",
    "merge_local_gemini_result",
]
