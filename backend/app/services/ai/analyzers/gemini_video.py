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
from app.services.scraping.ytdlp import YTDLP_AVAILABLE, YTDLP_PROXY, fetch_youtube_subtitles
from app.services.scoring.creator import get_creator_profile
from app.services.scoring.verticals import apply_learned_weights

logger = get_logger(__name__)


VIDEO_V2_SCORE_KEYS = (
    "video_specialty",
    "product_fit",
    "product_showcase",
    "brand_exposure",
    "production_quality",
    "storytelling",
    "authenticity",
    "competitor_context",
)


def _response_usage_metadata(resp: Any) -> dict[str, Any]:
    usage = getattr(resp, "usage_metadata", None)
    if not usage:
        return {}
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump(mode="json", exclude_none=True)
        except Exception:
            pass
    fields = (
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "cached_content_token_count",
        "thoughts_token_count",
    )
    output: dict[str, Any] = {}
    for field in fields:
        value = getattr(usage, field, None)
        if value is not None:
            output[field] = value
    return output


def _video_v2_prompt(
    *,
    title: str,
    profile_ctx: str,
    subtitle_ctx: str,
    subtitle_used: bool,
    performance_context: dict[str, Any] | None,
) -> str:
    metrics = json.dumps(performance_context or {}, ensure_ascii=False, default=str)
    return f"""你是 Viltrox (唯卓仕) 品牌资深视频内容分析师。请仔细观看完整视频画面和音频。{profile_ctx}{subtitle_ctx}
视频标题: {title}
播放表现数据（只做绝对表现判断；没有账号基准，不要判断“相对该 KOL 平时”）:
{metrics}

只返回 JSON，不要 Markdown。必须按以下三层 schema 输出；评不出的字段用 null，confidence=0，不能瞎编。
每个评分项 score 为 0-100 或 null，confidence 为 0-1，evidence 必须列出画面/字幕依据。

{{
  "layer1_visual_content": {{
    "content_summary": "这条视频拍了什么、讲了什么",
    "scene_timeline": [{{"timestamp": "MM:SS", "what": "关键画面/内容"}}],
    "product_presence": {{
      "hero_product": true,
      "closeup_time_pct": 20,
      "products": ["Viltrox 产品精确名称"],
      "selling_points_shown": ["展示到的卖点"],
      "notes": "产品出镜是否充分"
    }},
    "brand_exposure": {{
      "logo_scenes": [{{"timestamp": "MM:SS", "scene": "Logo/品牌露出场景"}}],
      "sufficient": true,
      "notes": "品牌露出是否充分"
    }},
    "competitor_presence": [{{"brand": "竞品品牌", "scene": "出现/提及场景"}}],
    "production_observations": {{
      "composition": "构图客观描述",
      "lighting": "光线客观描述",
      "color_grade": "调色客观描述",
      "professional_level": "amateur/semi-pro/professional/broadcast",
      "notes": "制作质量观察"
    }},
    "evidence": {{"timestamps": ["MM:SS 依据"], "subtitle_used": {str(bool(subtitle_used)).lower()}}}
  }},
  "layer2_video_scores": {{
    "score_scale": "0-100",
    "scores": {{
      "video_specialty": {{"score": 0, "confidence": 0.0, "evidence": ["视频题材/类型契合度依据"]}},
      "product_fit": {{"score": 0, "confidence": 0.0, "evidence": ["这视频/这KOL适合推此产品的依据"]}},
      "product_showcase": {{"score": 0, "confidence": 0.0, "evidence": ["卖点是否讲清/展示到位依据"]}},
      "brand_exposure": {{"score": 0, "confidence": 0.0, "evidence": ["品牌露出依据"]}},
      "production_quality": {{"score": 0, "confidence": 0.0, "evidence": ["制作质量依据"]}},
      "storytelling": {{"score": 0, "confidence": 0.0, "evidence": ["叙事/吸引力依据"]}},
      "authenticity": {{"score": 0, "confidence": 0.0, "evidence": ["真实推荐还是模板化恰饭依据"]}},
      "competitor_context": {{"score": 0, "confidence": 0.0, "evidence": ["竞品对比/风险依据"]}}
    }},
    "dimensions11_mapping": {{
      "content_specialty": "video_specialty",
      "product_fit": "product_fit",
      "competitor_risk_score": "competitor_context"
    }}
  }},
  "layer3_integrated_judgment": {{
    "advantages": ["这视频好在哪"],
    "weaknesses": ["这视频差在哪"],
    "homogeneity": {{"is_homogeneous": false, "unique_points": ["独特点"], "rationale": "是否同质化的判断"}},
    "better_direction": {{"better_products": ["更适合推的产品/系列"], "content_adjustments": ["内容方向建议"], "rationale": "理由"}},
    "cooperation_recommendation": {{"recommendation": "continue/cautious/not_recommended", "reason": "后续合作理由"}},
    "content_value_score": {{"score": 0, "confidence": 0.0, "rationale": "视频本身做得好不好"}},
    "placement_value_score": {{"score": 0, "confidence": 0.0, "rationale": "对 Viltrox 投放值不值；说明无账号基准，仅基于绝对播放表现"}}
  }}
}}"""


def _score_value(entry: Any) -> float | int | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("score")
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    parsed = max(0.0, min(100.0, parsed))
    return int(parsed) if parsed.is_integer() else round(parsed, 2)


def _normalise_v2_result(parsed: dict[str, Any], *, subtitle_used: bool) -> dict[str, Any]:
    layer1 = parsed.get("layer1_visual_content") if isinstance(parsed.get("layer1_visual_content"), dict) else {}
    layer2 = parsed.get("layer2_video_scores") if isinstance(parsed.get("layer2_video_scores"), dict) else {}
    layer3 = parsed.get("layer3_integrated_judgment") if isinstance(parsed.get("layer3_integrated_judgment"), dict) else {}
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    evidence["subtitle_used"] = bool(evidence.get("subtitle_used", subtitle_used))
    layer1["evidence"] = evidence
    scores = layer2.get("scores") if isinstance(layer2.get("scores"), dict) else {}
    for key in VIDEO_V2_SCORE_KEYS:
        entry = scores.get(key)
        if not isinstance(entry, dict):
            scores[key] = {"score": None, "confidence": 0, "evidence": []}
            continue
        if _score_value(entry) is None:
            entry["score"] = None
            entry["confidence"] = 0
        entry["evidence"] = entry.get("evidence") if isinstance(entry.get("evidence"), list) else []
    layer2["scores"] = scores
    layer2["dimensions11_mapping"] = {
        "content_specialty": "video_specialty",
        "product_fit": "product_fit",
        "competitor_risk_score": "competitor_context",
    }
    return {
        "schema_version": "gemini_video_v2",
        "layer1_visual_content": layer1,
        "layer2_video_scores": layer2,
        "layer3_integrated_judgment": layer3,
    }


def _apply_v2_result(
    result: dict[str, Any],
    parsed: dict[str, Any],
    *,
    method: str,
    model: str,
    usage_metadata: dict[str, Any],
    subtitle_used: bool,
) -> None:
    payload = _normalise_v2_result(parsed, subtitle_used=subtitle_used)
    layer1 = payload["layer1_visual_content"]
    layer2 = payload["layer2_video_scores"]
    layer3 = payload["layer3_integrated_judgment"]
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    quality_scores = {
        key: value
        for key, entry in (layer2.get("scores") or {}).items()
        if (value := _score_value(entry)) is not None
    }
    content_value = layer3.get("content_value_score") if isinstance(layer3.get("content_value_score"), dict) else {}
    result.update(
        {
            "analyzed": True,
            "method": method,
            "model": model,
            "usage_metadata": usage_metadata,
            "schema_version": "gemini_video_v2",
            "video_analysis_v2": payload,
            "content_summary": layer1.get("content_summary") or "",
            "content_genre": "video_analysis_v2",
            "content_topic": layer1.get("content_summary") or "",
            "production_quality": (layer1.get("production_observations") or {}).get("professional_level")
            if isinstance(layer1.get("production_observations"), dict)
            else "",
            "timestamps": evidence.get("timestamps") if isinstance(evidence.get("timestamps"), list) else timeline,
            "competitor_mentions": layer1.get("competitor_presence") if isinstance(layer1.get("competitor_presence"), list) else [],
            "quality_scores": quality_scores,
            "quality_overall": _score_value(content_value) or 0,
        }
    )


async def analyze_local_video_with_gemini(
    video_path: str,
    title: str,
    creator_handle: str = "",
    *,
    schema_version: str = "v2",
    performance_context: dict[str, Any] | None = None,
    subtitle_text: str = "",
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
    is_v2 = str(schema_version or "").strip().lower() == "v2"
    if not is_v2:
        result["error"] = "analyze_local_video_with_gemini currently supports schema_version='v2' only"
        return result
    prompt = _video_v2_prompt(
        title=title,
        profile_ctx=profile_ctx,
        subtitle_ctx=subtitle_ctx,
        subtitle_used=bool(subtitle_text),
        performance_context=performance_context,
    )
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
        for model_name in ["gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-2.5-flash"]:
            try:
                def _analyze(m=model_name, f=gemini_file):
                    return gemini_client.models.generate_content(
                        model=m,
                        contents=[
                            genai_types.Part.from_uri(file_uri=f.uri, mime_type="video/mp4"),
                            prompt,
                        ],
                    )

                resp = await asyncio.to_thread(_analyze)
                usage_metadata = _response_usage_metadata(resp)
                raw = resp.text.strip()
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                parsed = json.loads(raw)
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


async def analyze_youtube_with_gemini(
    url: str,
    title: str,
    creator_handle: str = "",
    *,
    schema_version: str = "legacy",
    performance_context: dict[str, Any] | None = None,
) -> dict:
    """
    Gemini YouTube analysis via File API:
    1. Download first 2min with yt-dlp
    2. Upload to Gemini File API
    3. Analyze with gemini-2.5-flash / gemini-2.5-pro (frame by frame)
    4. Delete file from Gemini
    """
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

    is_v2 = str(schema_version or "").strip().lower() == "v2"
    if is_v2:
        prompt = _video_v2_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_used=bool(subtitle_raw),
            performance_context=performance_context,
        )
    else:
        prompt = f"""你是 Viltrox (唯卓仕) 品牌资深内容分析师。请仔细观看这个完整视频的每一帧画面和音频。{profile_ctx}{subtitle_ctx}
视频标题: {title}

=== 第一步：识别内容类型 ===
从以下类型选最匹配的一个：
review（评测：口头介绍产品）/ cinematic（电影感纯拍摄）/ tutorial（教学操作）
comparison（多产品对比）/ vlog（生活记录）/ unboxing（开箱）/ showcase（样片展示）/ bts（幕后）

=== 第二步：按类型标准客观评估 ===
review类：口述质量、产品展示清晰度、结论可信度是核心
cinematic类：画质、色彩、构图、镜头语言是核心，不看口述
tutorial类：步骤清晰度、可重复性是核心
comparison类：对比公平性、样本质量是核心
vlog类：真实感、器材自然使用是核心

=== 第三步：竞品识别（全面扫描）===
仔细扫描整个视频，识别所有竞品品牌和产品：
- 画面中出现的品牌Logo（机身/镜头桶身/包装/贴纸）
- 口头提及的任何品牌名或型号
- 比较测试中出现的竞品
- 字幕/标题卡片中的品牌文字
重点关注：Sigma / Tamron / Sony GM / Canon L / Nikon Z / Zeiss / Tokina / Samyang
          Godox / Profoto / Nanlite / Aputure（闪光灯/灯光）
          任何其他相机镜头或摄影器材品牌
每个竞品必须记录：品牌+精确型号+出现方式+和Viltrox的对比结果

=== 第四步：强制惩罚项检测（优先于其他评分）===

【A. 分辨率检测】
- 视频分辨率低于1080p（480p/720p竖屏）：stability/exposure/color_grade/composition上限为6
- 480p及以下：所有技术维度上限为5，production_quality最高semi-pro
- 只有1080p及以上视频，技术分才能给7+

【B. 水视频检测（严重扣分）】
以下任意一条触发，storytelling和hook各-2分，marketing_potential自动降级：
- 视频超过60%时长是同一机位静止镜头循环
- 装饰性粒子/星星/闪光特效覆盖画面超过30%时长
- 镜像翻转文字等无意义视觉效果
- 同一B-roll素材出现3次以上

【C. 废话填充检测】
以下任意一条触发，hook和conclusion_strength各-1分：
- PPT风格幻灯片/图文卡片插入打断视频叙事流
- 前30秒无实质内容（纯logo动画/纯背景音乐/空镜）
- 视频超过3分钟但实质不同内容少于1分钟（大量重复素材填充）

=== 第五步：时间戳（严格要求）===
{('基于上方字幕的真实时间生成时间戳，每个事件必须能在字幕里找到对应句子。' if subtitle_raw else '基于你实际观看到的视频内容生成时间戳，根据画面和声音定位。')}
❌ 错误示例（绝对禁止）：
00:00 intro 开场
00:05 title 标题卡
00:10 presenter 主持人出现
00:15 title 标题卡2
00:20 explanation 讲解
00:25 analysis 分析
→ 这是等间隔垃圾时间戳，完全无用

✅ 正确示例（应该这样）：
00:12 Dustin手持 Viltrox 27mm F1.2 首次入画，镜头桶身 Logo 清晰可见
01:34 Sony GM 50mm F1.4 首次出现，开始双镜对比测试
03:47 Viltrox 在散景测试中明显胜出，Dustin 表示「这个价格无法被击败」
07:22 Sigma 35mm Art 加入三镜横评
12:05 室外对焦追焦测试，Viltrox 追焦速度对比 Sony GM
18:33 最终结论：Viltrox 性价比最高，适合预算有限摄影师
→ 每个时间戳都有实质内容，不可互换

规则：
- 长视频（>10分钟）生成 20-30 个时间戳
- 短视频（<3分钟）生成 8-12 个时间戳
- 每个时间戳必须描述该时刻「具体发生了什么」，不能用泛泛的词如「讲解」「分析」「介绍」
- 竞品首次出现必须记录
- Viltrox 产品每次特写必须记录
- 评测者说出关键结论的时刻必须记录

只返回 JSON，不包含 Markdown:
{{
  "content_genre": "review/cinematic/tutorial/comparison/vlog/unboxing/showcase/bts",
  "content_type_cn": "该类型的中文名称",
  "content_summary": "3-4句中文总结：第一句说明视频类型+核心内容，后面说具体亮点和不足",
  "content_topic": "English: one sentence topic",
  "production_quality": "amateur/semi-pro/professional/broadcast",
  "why_compelling": "这个视频最吸引人的地方，针对该类型具体说明（中文）",
  "hook_analysis": "前30秒的钩子设计分析（中文）",
  "target_audience": "目标受众：摄影水平、器材预算、内容偏好（中文）",
  "viltrox_detected": true,
  "viltrox_products_mentioned": ["精确型号，如 Viltrox AF 85mm F1.8 MF FE"],
  "camera_body": "精确机身型号或null",
  "viltrox_lens": "精确Viltrox镜头名称或null",
  "other_lens": "竞品镜头精确型号或null",
  "competitor_products": [
    {{
      "brand": "品牌名（Sigma/Tamron/Sony/Canon/Nikon/Zeiss/Tokina/Samyang/Rokinon/Godox/Profoto/Nanlite等）",
      "model": "精确型号（如 35mm F1.4 DG DN Art）",
      "category": "lens/flash/camera/accessory",
      "context": "head-to-head/three-way/briefly-shown/mentioned-only",
      "sentiment": "viltrox_wins/viltrox_loses/neutral/not_compared",
      "screen_time_seconds": 30,
      "first_appearance": "MM:SS"
    }}
  ],
  "competitor_brands": ["出现过的所有竞品品牌列表，只要画面或口头出现都要列出"],
  "timestamps": [
    {{"time": "MM:SS", "event": "中文：具体描述这个时间点的事件，不要泛泛", "type": "viltrox/competitor/camera/key_moment/intro/conclusion"}}
  ],
  "competitor_mentions": [
    {{"brand": "品牌", "model": "型号", "time": "MM:SS", "context": "具体提及场景", "sentiment": "positive/neutral/negative"}}
  ],
  "type_specific_notes": "在该垂类社区里，这个视频的水准如何？（中文，2句）",
  "marketing_potential": "high/medium/low",
  "marketing_notes": "为何适合或不适合推广Viltrox（中文）",
  "brand_integration_depth": "incidental/featured/central/exclusive",
  "vertical_category": "wedding/food/lifestyle/review/cinematic/sports/travel/portrait/tutorial/commercial",
  "vertical_quality_notes": "和同垂类最优秀内容相比差距在哪？（中文，2句）",
  "community_value": 7,
  "brand_exposure_detail": {{
    "logo_on_lens_barrel": true,
    "logo_on_screen_overlay": false,
    "logo_in_thumbnail": false,
    "product_closeup_count": 3,
    "brand_mention_count": 2,
    "product_screen_time_pct": 40,
    "notes": "中文：品牌曝光方式描述"
  }},
  "quality_scores": {{
    "=== 评分标准（必须严格执行）===": "10分=全球TOP5%示范案例(极稀少); 9分=专业出色(仅10%); 8分=良好(25%); 7分=普通够用(30%); 6分=明显缺陷(20%); 5分及以下=严重问题。【强制校准】480p视频技术分<=5; 720p<=6; 1080p+才能7+; 水视频/过度特效自动-2分; 素人视频应在5-7之间，专业制作才能到8+",
    "exposure": 7,
    "stability": 7,
    "color_grade": 6,
    "composition": 7,
    "lighting": 6,
    "editing": 7,
    "storytelling": 6,
    "hook": 7,
    "viltrox_branding": 7,
    "logo_visibility": 6,
    "product_screen_time": 6,
    "close_up_quality": 7,
    "audience_fit": 7,
    "authenticity": 7,
    "conclusion_strength": 6
  }},
  "quality_overall": 7,
  "quality_summary": "2句中文：第一句说品牌曝光亮点，第二句说故事说服力不足在哪",
  "reference_value": "high/medium/low",
  "reference_reasons": ["中文说明"],
  "improvements": [
    {{"area": "品牌曝光", "priority": "high", "timestamp": "02:30", "problem": "具体问题", "suggestion": "具体可执行方案（中文）", "expected_improvement": "预期效果"}}
  ]
}}"""

    gemini_file = None
    tmp_path = None

    # ── Model priority list (April 2026) ──────────────────────────────────────
    # gemini-3-flash-preview: shut down; gemini-3-pro-preview: shut down 2026-03-09
    # gemini-1.5-x: shut down 2025-04-29; gemini-2.0-flash: GA until 2026-06-01
    # Safe stable order: 2.5-flash first (best price/perf, video capable),
    # then 2.5-pro (slower/pricier but more accurate), then 2.0-flash as fallback.
    GEMINI_MODELS = [
        "gemini-3-flash-preview",    # PRIMARY — best price/perf, multimodal
        "gemini-3.1-pro-preview",    # BACKUP — highest accuracy, long videos
        "gemini-2.5-flash",          # FALLBACK — stable GA tier
    ]

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
                    def _analyze_direct(m=model_name, u=url):
                        return gemini_client.models.generate_content(
                            model=m,
                            contents=[
                                genai_types.Part(
                                    file_data=genai_types.FileData(
                                        file_uri=u
                                    )
                                ),
                                prompt
                            ]
                        )
                    resp = await asyncio.to_thread(_analyze_direct)
                    usage_metadata = _response_usage_metadata(resp)
                    raw = resp.text.strip()
                    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                    parsed = json.loads(raw)
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
            else:
                logger.warning("gemini_fast_path_fallback_to_download")
                # Fall through to slow path below
        except Exception as fast_err:
            logger.warning("gemini_fast_path_exception", extra={"error": str(fast_err)})
            # Fall through to slow path
    
    # ===== SLOW PATH: yt-dlp download + File API upload =====
    try:
        # Step 1: Download FULL video at 720p (no time limit)
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = os.path.join(tmpdir, "gemini_video.mp4")
            logger.info("gemini_fileapi_download_start", extra={"url": url})

            dl_cmd = [
                "yt-dlp",
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
                lambda: subprocess.run(dl_cmd, capture_output=True, timeout=600)
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
                    def _analyze(m=model_name, f=gemini_file):
                        return gemini_client.models.generate_content(
                            model=m,
                            contents=[
                                genai_types.Part.from_uri(
                                    file_uri=f.uri,
                                    mime_type="video/mp4"
                                ),
                                prompt
                            ]
                        )
                    resp = await asyncio.to_thread(_analyze)
                    usage_metadata = _response_usage_metadata(resp)
                    raw = resp.text.strip()
                    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                    parsed = json.loads(raw)
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
