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

VIDEO_FINAL_LAYERS = (
    "layer1_visual_content",
    "layer2_viewer_emotion",
    "layer3_three_values",
    "layer4_attribution",
    "layer5_recommendations",
    "layer6_flags_and_scores",
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


def _parse_json_response_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Gemini response JSON root must be an object")
    return parsed


def _video_v2_prompt(
    *,
    title: str,
    profile_ctx: str,
    subtitle_ctx: str,
    subtitle_used: bool,
    performance_context: dict[str, Any] | None,
) -> str:
    metrics = json.dumps(performance_context or {}, ensure_ascii=False, default=str)
    return f"""你是 Viltrox (唯卓仕) 品牌方的视频投放审查员，对投放预算负责。请以挑剔、直接、可追责的品牌方视角审查完整视频画面和音频，不要像友善评论员一样客套夸奖。{profile_ctx}{subtitle_ctx}
视频标题: {title}
播放表现数据（只做绝对表现判断；没有账号基准，不要判断“相对该 KOL 平时”）:
{metrics}

只返回 JSON，不要 Markdown。必须按以下三层 schema 输出；评不出的字段用 null，confidence=0，不能瞎编。
每个评分项 score 为 0-100 或 null，confidence 为 0-1，evidence 必须列出画面/字幕依据。
评分必须有区分度，不要都集中在 80-95。给分前先判断：这条视频放在所有 KOL 投放视频里，属于前 10%、中上、中游，还是偏弱？
- 真正出类拔萃、罕见优秀才给 90+
- 扎实合格的中上水平给 70-85
- 普通、达标但无亮点给 55-70
- 有明显短板给 40-55
大部分视频应该落在中间区段。不要因为视频“没硬伤”就给高分；“没硬伤”只是合格，不是优秀。

weaknesses 必须指出真正影响投放价值的问题，例如恰饭感重/套路化、产品展示不充分或一带而过、受众与产品不匹配、内容与其他 KOL 高度同质化、信息密度低、转化引导弱。禁止写“可以增加更多场景”“对新手可以更友好”这类放之四海皆准的客套缺点。如果确实有明显短板，直接具体地说；如果几乎无可挑剔，说明为什么它罕见地好。
authenticity 衡量这是真心推荐还是收钱办事：明显套模板、念稿感、为恰饭而恰饭、对产品无真实理解给 40 以下；有商业痕迹但基本走心给 55-75；真正出于喜爱、有个人观点和真实使用体验才给 80+。这个维度要敢于打低分，帮助品牌方识别谁是真喜欢产品、谁只是收钱。
cooperation_recommendation 必须给真实判断，不要默认 continue。continue=确实值得继续投；cautious=有价值但有顾虑（说明恰饭感、数据存疑或性价比一般等顾虑）；reconsider=不太值得（说明原因）。如果谁都是 continue，这个建议就没有价值。
key_hook 用一句话点出这条视频/这个 KOL 最值得品牌方深入了解的点，或最大的疑点，让品牌方有“想点进去深究”的理由。

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
    "weaknesses": ["真正影响投放价值的具体短板，禁止客套话"],
    "homogeneity": {{"is_homogeneous": false, "unique_points": ["独特点"], "rationale": "是否同质化的判断"}},
    "better_direction": {{"better_products": ["更适合推的产品/系列"], "content_adjustments": ["内容方向建议"], "rationale": "理由"}},
    "cooperation_recommendation": {{"recommendation": "continue/cautious/reconsider", "reason": "后续合作理由"}},
    "key_hook": "最值得品牌方深挖的一个点，或最大的疑点",
    "content_value_score": {{"score": 0, "confidence": 0.0, "rationale": "视频本身做得好不好"}},
    "placement_value_score": {{"score": 0, "confidence": 0.0, "rationale": "对 Viltrox 投放值不值；说明无账号基准，仅基于绝对播放表现"}}
  }}
}}"""


def _video_final_v1_prompt(
    *,
    title: str,
    profile_ctx: str,
    subtitle_ctx: str,
    subtitle_used: bool,
    performance_context: dict[str, Any] | None,
) -> str:
    metrics = json.dumps(performance_context or {}, ensure_ascii=False, default=str)
    return f"""你是 Viltrox (唯卓仕) 的视频投放复盘审查员。你同时站在两个视角判断完整视频：
1. 品牌方：这条投放是否真的帮 Viltrox 卖镜头、证明镜头、产出可复用素材。
2. 真实观众：一个摄影/视频创作者看完后是否心动、是否想继续了解、是否反感商业植入。

【静态规则 A-I：固定判断框架】
A. 素材源 vs 分发渠道：高质量但低播放时，要拆开判断“好素材源”和“弱分发渠道”，不要混成一个分。
B. 效果归因：必须区分画面好看来自产品本身，还是来自 KOL 的 LUT/滤镜/后期/灯光/模特/场景/剪辑。Viltrox 投放目标是卖镜头，不是帮 KOL 展示调色包。
C. 素材复用价值：判断这条是否适合买断/授权作为官方素材、官网素材、广告投流切片、产品页、展会屏幕。
D. 恰饭警惕：即使完整看视频，也要警惕模板化、念稿感、赞助水印、折扣链接、自家 LUT/滤镜顺带推广造成的商业归因污染。
E. Viltrox 投放目标：卖镜头、证明镜头能力、促成购买决策；不是帮 KOL 涨粉，也不是只追求漂亮画面。
F. 11维理念精髓：关注内容专长、产品契合、品牌露出、真实性、竞品风险、转化说服力、素材资产价值；单条视频评不出账号长期能力时必须说明。
G. 单视频边界：没有 followers/avg_views 等账号基准时，禁止判断“相对该 KOL 平时”；只能用绝对播放/点赞/评论/时长/发布时间。
H. 评分锚点：90+ 是罕见顶级；70-85 是合格中上；55-70 是普通达标；40-55 是明显短板；大部分视频应落中间，不能因为没硬伤就给高分。
I. 守不造假：所有评分必须带 confidence 和 evidence；证据不足给低 confidence 或 null，不补脑。

【Viltrox 产品背景】
Viltrox 是摄影/视频镜头品牌，核心产品线包括 Pro 系列自动对焦定焦、LAB 高端镜头、Air 轻量镜头、电影镜头等。判断 product_fit 时要考虑：产品定位、价格档、是否适合该 KOL 受众、是否和 Sony/Sigma/Tamron/Canon/Nikon 等竞品形成清晰购买理由。

{profile_ctx}{subtitle_ctx}
视频标题: {title}
播放表现数据（绝对表现；没有账号基准时不要做相对判断）:
{metrics}

只返回 JSON，不要 Markdown。必须输出以下 6 层结构。
每个 score 为 0-100 或 null；confidence 为 0-1；evidence 必须是具体画面/字幕/数据依据。
layer2_viewer_emotion 必须像真实观众反应，不要品牌官腔；并且必须和真实播放数据对照，指出“模拟很心动但播放低”或“心动弱且播放低”等矛盾/一致性。

{{
  "layer1_visual_content": {{
    "content_summary": "这条视频拍了什么、讲了什么",
    "scene_timeline": [{{"timestamp": "MM:SS", "what": "关键画面/内容"}}],
    "product_presence": {{
      "hero_product": true,
      "closeup_time_pct": 0,
      "products": ["Viltrox 产品精确名称"],
      "selling_points_shown": ["展示到的卖点"],
      "missing_proof": ["没有证明到的关键能力"],
      "notes": "产品出镜是否充分"
    }},
    "brand_exposure": {{
      "logo_scenes": [{{"timestamp": "MM:SS", "scene": "Logo/品牌露出场景"}}],
      "sufficient": true,
      "notes": "品牌露出是否充分"
    }},
    "competitor_presence": [{{"brand": "竞品品牌", "scene": "出现/提及场景", "risk": "风险"}}],
    "production_observations": {{
      "composition": "构图客观描述",
      "lighting": "光线客观描述",
      "color_grade": "调色客观描述",
      "professional_level": "amateur/semi-pro/professional/broadcast",
      "notes": "制作质量观察"
    }},
    "evidence": {{"timestamps": ["MM:SS 依据"], "subtitle_used": {str(bool(subtitle_used)).lower()}}}
  }},
  "layer2_viewer_emotion": {{
    "one_sentence_viewer_feeling": "一个真实摄影观众看完后的第一反应，口语化但专业",
    "heart_movement_score": {{"score": 0, "confidence": 0.0, "evidence": ["哪里让人心动/不心动"]}},
    "desire_to_click_or_buy": {{"score": 0, "confidence": 0.0, "evidence": ["是否想点链接/查价格/继续看评测"]}},
    "annoyance_or_ad_fatigue": {{"score": 0, "confidence": 0.0, "evidence": ["恰饭感/广告疲劳/反感来源"]}},
    "memory_points": ["观众最可能记住的点"],
    "seeding_power": {{"score": 0, "confidence": 0.0, "evidence": ["是否真的种草镜头"]}},
    "performance_alignment": {{
      "real_metrics_summary": "播放/点赞/评论的绝对表现总结",
      "emotion_vs_metrics": "心动模拟和真实数据是否矛盾，必须明确指出",
      "possible_explanation": "如果矛盾，解释可能原因；不能归因过度"
    }}
  }},
  "layer3_three_values": {{
    "channel_value": {{"score": 0, "confidence": 0.0, "rationale": "作为 KOL 发布渠道值不值", "evidence": ["绝对播放/评论/传播证据"]}},
    "asset_value": {{"score": 0, "confidence": 0.0, "rationale": "作为官方素材买断/复用值不值", "reuse_scenarios": ["官网/广告/产品页/展会/社媒切片"], "evidence": ["素材质量证据"]}},
    "product_proof_value": {{"score": 0, "confidence": 0.0, "rationale": "是否真正证明镜头能力", "proved_capabilities": ["已证明能力"], "unproved_capabilities": ["未证明能力"], "evidence": ["证明依据"]}},
    "material_source_vs_distribution_channel": "一句话拆分：它更像素材源、分发渠道，还是两者兼具"
  }},
  "layer4_attribution": {{
    "beauty_attribution": [
      {{"source": "lens_optics/lighting/LUT/color_grade/filter/post_production/model/location/editing", "share_pct_estimate": 0, "confidence": 0.0, "evidence": ["依据"]}}
    ],
    "lens_contribution": {{"score": 0, "confidence": 0.0, "rationale": "画面好看有多少能归功于镜头本身"}},
    "kol_craft_contribution": {{"score": 0, "confidence": 0.0, "rationale": "KOL 布光/构图/后期贡献"}},
    "attribution_risk": "如果好看主要来自 LUT/滤镜/后期，要直说风险",
    "what_to_request_to_verify_lens": ["下次应要求的未调色/无滤镜/竞品对比/原生样片"]
  }},
  "layer5_recommendations": {{
    "cooperation_recommendation": {{"recommendation": "continue/cautious/reconsider", "reason": "真实合作建议"}},
    "buyout_or_license_recommendation": {{"recommendation": "buyout/license_do_not_buyout/ask_for_more", "reason": "是否买断/授权素材", "suggested_usage": ["适合用途"], "avoid_usage": ["不适合用途"]}},
    "next_brief_adjustments": ["下次给这个 KOL 的 brief 怎么改"],
    "must_request_from_kol": ["必须补拍/补交的素材或数据"],
    "budget_action": "increase/keep_small/test_only/stop",
    "why": "给老板看的最终理由，直接、可执行"
  }},
  "layer6_flags_and_scores": {{
    "risk_flags": [
      {{"flag": "heavy_sponsored_feel/overprocessed_visuals/no_raw_proof/no_competitor_comparison/low_views_low_discussion/low_view_high_like_anomaly/weak_cta/self_product_distraction", "severity": "low/medium/high", "evidence": "依据"}}
    ],
    "scores": {{
      "content_quality_score": {{"score": 0, "confidence": 0.0, "rationale": "视频做得好不好"}},
      "viewer_heart_score": {{"score": 0, "confidence": 0.0, "rationale": "真实观众会不会心动"}},
      "channel_value_score": {{"score": 0, "confidence": 0.0, "rationale": "渠道投放价值"}},
      "asset_reuse_score": {{"score": 0, "confidence": 0.0, "rationale": "素材复用价值"}},
      "product_proof_score": {{"score": 0, "confidence": 0.0, "rationale": "产品证明价值"}},
      "marketing_value_score": {{"score": 0, "confidence": 0.0, "rationale": "对 Viltrox 卖镜头有没有用，不等同于内容好看"}}
    }},
    "final_verdict": "一句话最终结论",
    "key_hook": "最值得品牌方深入了解的一点或最大疑点"
  }}
}}"""


def _video_final_v1_static_prompt() -> str:
    return """你是 Viltrox (唯卓仕) 的视频投放复盘审查员。你同时站在两个视角判断完整视频：
1. 品牌方：这条投放是否真的帮 Viltrox 卖镜头、证明镜头、产出可复用素材。
2. 真实观众：一个摄影/视频创作者看完后是否心动、是否想继续了解、是否反感商业植入。

固定规则 A-I：
A. 素材源 vs 分发渠道：高质量但低播放时，拆开判断“好素材源”和“弱分发渠道”。
B. 效果归因：区分画面好看来自产品本身，还是来自 KOL 的 LUT/滤镜/后期/灯光/模特/场景/剪辑。
C. 素材复用价值：判断是否适合买断/授权作为官方素材、官网素材、广告投流切片、产品页、展会屏幕。
D. 恰饭警惕：警惕模板化、念稿感、赞助水印、折扣链接、自家 LUT/滤镜顺带推广。
E. Viltrox 投放目标：卖镜头、证明镜头能力、促成购买决策；不是帮 KOL 涨粉。
F. 11维理念精髓：关注内容专长、产品契合、品牌露出、真实性、竞品风险、转化说服力、素材资产价值。
G. 单视频边界：没有账号基准时，禁止判断“相对该 KOL 平时”；只能用绝对播放/点赞/评论/时长/发布时间。
H. 评分锚点：90+ 罕见顶级；70-85 合格中上；55-70 普通达标；40-55 明显短板；不能因为没硬伤就给高分。
I. 守不造假：所有评分必须带 confidence 和 evidence；证据不足给低 confidence 或 null，不补脑。

Viltrox 产品背景：Viltrox 是摄影/视频镜头品牌，核心产品线包括 Pro 系列自动对焦定焦、LAB 高端镜头、Air 轻量镜头、电影镜头等。判断 product_fit 时考虑产品定位、价格档、竞品购买理由。若视频是闪光灯/配件，也要按“是否帮助 Viltrox 卖该产品和形成购买信心”评估。

输出必须是 JSON，包含 6 层：layer1_visual_content / layer2_viewer_emotion / layer3_three_values / layer4_attribution / layer5_recommendations / layer6_flags_and_scores。
每个 score 为 0-100 或 null；confidence 为 0-1；evidence 必须具体。
layer2_viewer_emotion 必须像真实观众反应，并和真实播放数据对照，指出矛盾或一致性。
layer3 必须拆 channel_value / asset_value / product_proof_value。
layer4 必须拆 lens_or_product_contribution / kol_craft_contribution / lighting / LUT / filter / editing / location 等归因。
layer5 必须给合作建议、素材买断/授权建议、下一轮 brief 调整、必须向 KOL 索取什么。
layer6 必须给 risk_flags、content_quality_score、viewer_heart_score、channel_value_score、asset_reuse_score、product_proof_score、marketing_value_score、final_verdict、key_hook。

固定输出 schema 细则：
layer1_visual_content = content_summary / scene_timeline[{timestamp, what, why_it_matters}] / product_presence / brand_exposure / competitor_presence / production_observations / evidence{timestamps, subtitle_used}。
layer2_viewer_emotion = first_three_seconds_feeling / viewer_heart_score / dislike_or_resistance / memorable_points / purchase_or_interest_trigger / one_sentence_viewer_reaction / data_alignment。必须说明真实观众是否会想继续看、收藏、点赞、下单或反感。
layer3_three_values = channel_value / asset_value / product_proof_value。channel_value 只评渠道投放值；asset_value 只评素材买断/复用；product_proof_value 只评是否证明 Viltrox 产品能力。
layer4_attribution = attribution_breakdown[{source, share_pct_estimate, confidence, evidence}] / product_contribution / kol_craft_contribution / attribution_risk / what_to_request_to_verify_product。必须拆产品、灯光、LUT、后期、布景、模特、剪辑、平台压缩。
layer5_recommendations = cooperation_recommendation / buyout_or_license_recommendation / next_brief_adjustments / must_request_from_kol / budget_action / why。
layer6_flags_and_scores = risk_flags / scores / final_verdict / key_hook。scores 至少包含 content_quality_score、viewer_heart_score、channel_value_score、asset_reuse_score、product_proof_score、marketing_value_score。
分数锚点再次强调：90+ 必须罕见且证据强；70-85 是可用但要指出不足；55-70 是普通达标；40-55 是明显短板；40以下是投放风险或证据很差。不要因为画面好看就默认渠道值高，也不要因为互动高就忽略归因风险。"""


def _video_final_v1_dynamic_prompt(
    *,
    title: str,
    profile_ctx: str,
    subtitle_ctx: str,
    subtitle_used: bool,
    performance_context: dict[str, Any] | None,
) -> str:
    metrics = json.dumps(performance_context or {}, ensure_ascii=False, default=str)
    return f"""动态输入如下。请严格按缓存中的 final_v1 固定规则输出完整 6 层 JSON，不要 Markdown。
{profile_ctx}{subtitle_ctx}
视频标题: {title}
字幕是否注入: {str(bool(subtitle_used)).lower()}（最终 evidence.subtitle_used 会由代码侧校准）
播放表现数据（绝对表现；没有账号基准时不要做相对判断）:
{metrics}

必须输出的 JSON 顶层键：
layer1_visual_content, layer2_viewer_emotion, layer3_three_values, layer4_attribution, layer5_recommendations, layer6_flags_and_scores。"""


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


def _normalise_final_v1_result(parsed: dict[str, Any], *, subtitle_used: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {"schema_version": "video_analysis_final_v1"}
    for layer in VIDEO_FINAL_LAYERS:
        value = parsed.get(layer) if isinstance(parsed.get(layer), dict) else {}
        payload[layer] = value
    layer1 = payload["layer1_visual_content"]
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    evidence["subtitle_used"] = bool(subtitle_used)
    layer1["evidence"] = evidence
    return payload


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


def _clamped_confidence(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, parsed)), 3)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "pass", "passed"}


def _normalise_final_v1_keyframe_qa(parsed: dict[str, Any]) -> dict[str, Any]:
    checks = parsed.get("checks") if isinstance(parsed.get("checks"), dict) else {}
    score_correction = parsed.get("score_correction") if isinstance(parsed.get("score_correction"), dict) else {}
    issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else []
    confidence = _clamped_confidence(parsed.get("confidence"))
    return {
        "schema_version": "video_analysis_final_v1_keyframe_qa",
        "qa_pass": _bool_value(parsed.get("qa_pass")),
        "confidence": confidence,
        "summary": str(parsed.get("summary") or "").strip(),
        "checks": checks,
        "issues": [item for item in issues if isinstance(item, dict)],
        "score_correction": score_correction,
        "recommended_review_action": str(parsed.get("recommended_review_action") or "").strip(),
    }


def _normalise_v2_result(parsed: dict[str, Any], *, subtitle_used: bool) -> dict[str, Any]:
    layer1 = parsed.get("layer1_visual_content") if isinstance(parsed.get("layer1_visual_content"), dict) else {}
    layer2 = parsed.get("layer2_video_scores") if isinstance(parsed.get("layer2_video_scores"), dict) else {}
    layer3 = parsed.get("layer3_integrated_judgment") if isinstance(parsed.get("layer3_integrated_judgment"), dict) else {}
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    evidence["subtitle_used"] = bool(subtitle_used)
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


def _video_v2_judgment_prompt(
    *,
    layer1_visual_content: dict[str, Any],
    keyframes: list[dict[str, Any]],
    performance_context: dict[str, Any] | None,
) -> str:
    layer1_json = json.dumps(layer1_visual_content or {}, ensure_ascii=False, default=str)
    keyframes_json = json.dumps(
        [{"timestamp": frame.get("timestamp"), "reason": frame.get("reason")} for frame in keyframes],
        ensure_ascii=False,
        default=str,
    )
    metrics_json = json.dumps(performance_context or {}, ensure_ascii=False, default=str)
    return f"""你是 Viltrox 品牌方的视频投放审查员，对投放预算负责。
你没有看完整视频，只能基于下面的 Layer1 文字分析、6 张关键帧截图和播放表现数据判断。证据不足时必须给低 confidence，不要补脑。

Layer1 文字分析:
{layer1_json}

关键帧截图说明:
{keyframes_json}

播放表现数据（只做绝对表现判断；没有账号基准，不要判断“相对该 KOL 平时”）:
{metrics_json}

只返回 JSON，不要 Markdown。必须输出 layer2_video_scores + layer3_integrated_judgment。不要重复输出 layer1。
评分必须有区分度，不要都集中在 80-95。真正出类拔萃才给 90+；中上给 70-85；普通达标给 55-70；明显短板给 40-55。不要因为“没硬伤”就给高分。
weaknesses 必须指出真正影响投放价值的问题，禁止“可以增加更多场景”这类客套话。
authenticity 要敢打低分：模板化、念稿感、恰饭感重、缺少真实理解给 40 以下；有商业痕迹但基本走心给 55-75；真正喜爱且有个人观点才给 80+。
cooperation_recommendation 不要默认 continue：continue=值得继续投；cautious=有价值但有顾虑；reconsider=不太值得。
key_hook 用一句话点出最值得品牌方深入了解的点，或最大的疑点。

{{
  "layer2_video_scores": {{
    "score_scale": "0-100",
    "scores": {{
      "video_specialty": {{"score": 0, "confidence": 0.0, "evidence": ["基于 Layer1/截图的依据"]}},
      "product_fit": {{"score": 0, "confidence": 0.0, "evidence": ["基于 Layer1/截图的依据"]}},
      "product_showcase": {{"score": 0, "confidence": 0.0, "evidence": ["基于 Layer1/截图的依据"]}},
      "brand_exposure": {{"score": 0, "confidence": 0.0, "evidence": ["基于 Layer1/截图的依据"]}},
      "production_quality": {{"score": 0, "confidence": 0.0, "evidence": ["基于 Layer1/截图的依据"]}},
      "storytelling": {{"score": 0, "confidence": 0.0, "evidence": ["基于 Layer1/截图的依据"]}},
      "authenticity": {{"score": 0, "confidence": 0.0, "evidence": ["基于 Layer1/截图的依据"]}},
      "competitor_context": {{"score": 0, "confidence": 0.0, "evidence": ["基于 Layer1/截图的依据"]}}
    }}
  }},
  "layer3_integrated_judgment": {{
    "advantages": ["优势"],
    "weaknesses": ["真正影响投放价值的具体短板"],
    "homogeneity": {{"is_homogeneous": false, "unique_points": ["独特点"], "rationale": "判断依据"}},
    "better_direction": {{"better_products": ["更适合产品"], "content_adjustments": ["调整建议"], "rationale": "理由"}},
    "cooperation_recommendation": {{"recommendation": "continue/cautious/reconsider", "reason": "后续合作理由"}},
    "key_hook": "最值得深挖的一个点或最大疑点",
    "content_value_score": {{"score": 0, "confidence": 0.0, "rationale": "视频内容价值"}},
    "placement_value_score": {{"score": 0, "confidence": 0.0, "rationale": "投放价值；说明无账号基准，仅基于绝对播放表现"}}
  }}
}}"""


def _video_final_v1_keyframe_qa_prompt(
    *,
    final_v1_result: dict[str, Any],
    keyframes: list[dict[str, Any]],
    performance_context: dict[str, Any] | None,
) -> str:
    final_json = json.dumps(final_v1_result or {}, ensure_ascii=False, default=str)
    keyframes_json = json.dumps(
        [{"timestamp": frame.get("timestamp"), "reason": frame.get("reason")} for frame in keyframes],
        ensure_ascii=False,
        default=str,
    )
    metrics_json = json.dumps(performance_context or {}, ensure_ascii=False, default=str)
    schema = """
{
  "qa_pass": true,
  "confidence": 0.0,
  "summary": "一句话说明关键帧是否支持 final_v1 的核心判断",
  "checks": {
    "product_identity": {"status": "pass/warn/fail/unknown", "observed_products": [], "claimed_products": [], "issues": [], "evidence": []},
    "brand_exposure": {"status": "pass/warn/fail/unknown", "observed_brand_signals": [], "issues": [], "evidence": []},
    "competitor_context": {"status": "pass/warn/fail/unknown", "observed_competitors": [], "issues": [], "evidence": []},
    "inscription_or_model_text": {"status": "pass/warn/fail/unknown", "observed_text": [], "issues": [], "evidence": []},
    "title_image_consistency": {"status": "pass/warn/fail/unknown", "issues": [], "evidence": []}
  },
  "issues": [
    {"severity": "low/medium/high", "type": "product_mismatch/brand_missing/competitor_missed/title_image_mismatch/score_overconfident/other", "timestamp": "MM:SS", "evidence": "关键帧证据", "correction": "建议纠偏"}
  ],
  "score_correction": {
    "apply": false,
    "marketing_value_delta": 0,
    "corrected_marketing_value_score": null,
    "rationale": "若建议调分，说明原因；否则说明不调分"
  },
  "recommended_review_action": "accept_final_v1/review_manually/rerun_with_pro"
}
"""
    return f"""你是 Viltrox 视频投放分析的关键帧 QA 审核员。你不会重写 final_v1 六层分析，只做截图核验和纠偏。

任务：基于 final_v1 的 6 层结果、抽取的关键帧截图、关键帧时间点说明和播放表现数据，检查 final_v1 是否在以下高风险事实上犯错：
1. 产品型号/焦段/铭文是否识别错，例如标题是 27mm 但画面铭文像 75mm。
2. Viltrox 品牌/Logo/镜头桶身/包装是否真的有露出，还是 final_v1 过度推断。
3. 竞品/对比对象是否被漏掉或误标。
4. 标题声称的产品和画面实际产品是否一致。
5. final_v1 的 marketing_value_score 是否因为产品识别错误、品牌露出缺失或证据不足而明显过高/过低。

重要边界：
- 你只看关键帧截图，证据不足必须给 unknown 或 warn，不要补脑。
- 不要重写 layer1-layer6，不要重新评分整条视频。
- score_correction 只是建议，不是正式写分；只有证据强才 apply=true。
- 必须返回 JSON，不要 Markdown。

final_v1 六层结果：
{final_json}

关键帧说明：
{keyframes_json}

播放表现数据：
{metrics_json}

请严格按这个 JSON schema 返回：
{schema}"""


async def analyze_v2_judgment_with_keyframes(
    *,
    layer1_visual_content: dict[str, Any],
    keyframes: list[dict[str, Any]],
    title: str,
    performance_context: dict[str, Any] | None = None,
    model_name: str = "gemini-3.1-pro-preview",
) -> dict[str, Any]:
    """Judge Layer2+3 from Layer1 text plus keyframe JPGs."""
    result = {
        "analyzed": False,
        "method": f"gemini_keyframe_judgment_{model_name}",
        "model": model_name,
        "usage_metadata": {},
        "error": None,
    }
    if not GEMINI_AVAILABLE or not gemini_client or not genai_types:
        result["error"] = "Gemini not available"
        return result

    prompt = _video_v2_judgment_prompt(
        layer1_visual_content=layer1_visual_content,
        keyframes=keyframes,
        performance_context=performance_context,
    )
    contents: list[Any] = [f"视频标题: {title}\n\n{prompt}"]
    for frame in keyframes:
        image_path = Path(str(frame.get("image_path") or ""))
        if not image_path.exists():
            continue
        contents.append(genai_types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg"))
    try:
        def _analyze():
            return gemini_client.models.generate_content(model=model_name, contents=contents)

        resp = await asyncio.to_thread(_analyze)
        usage_metadata = _response_usage_metadata(resp)
        raw = resp.text.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        parsed = _parse_json_response_text(raw)
        subtitle_used = bool((layer1_visual_content.get("evidence") or {}).get("subtitle_used"))
        merged = {"layer1_visual_content": layer1_visual_content, **parsed}
        _apply_v2_result(
            result,
            merged,
            method=f"gemini_keyframe_judgment_{model_name}",
            model=model_name,
            usage_metadata=usage_metadata,
            subtitle_used=subtitle_used,
        )
        return result
    except Exception as exc:
        result["error"] = f"Gemini keyframe judgment failed: {str(exc)[:300]}"
        logger.warning("gemini_keyframe_judgment_failed", extra={"error": str(exc)[:120]})
        return result


async def analyze_final_v1_keyframe_qa(
    *,
    final_v1_result: dict[str, Any],
    keyframes: list[dict[str, Any]],
    title: str,
    performance_context: dict[str, Any] | None = None,
    model_name: str = "gemini-3.1-pro-preview",
) -> dict[str, Any]:
    """QA final_v1 facts from keyframe JPGs without regenerating the six-layer result."""
    result = {
        "analyzed": False,
        "method": f"gemini_final_v1_keyframe_qa_{model_name}",
        "model": model_name,
        "usage_metadata": {},
        "error": None,
    }
    if not GEMINI_AVAILABLE or not gemini_client or not genai_types:
        result["error"] = "Gemini not available"
        return result

    prompt = _video_final_v1_keyframe_qa_prompt(
        final_v1_result=final_v1_result,
        keyframes=keyframes,
        performance_context=performance_context,
    )
    contents: list[Any] = [f"视频标题: {title}\n\n{prompt}"]
    for frame in keyframes:
        image_path = Path(str(frame.get("image_path") or ""))
        if not image_path.exists():
            continue
        contents.append(genai_types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg"))
    if len(contents) <= 1:
        result["error"] = "no keyframe images available for QA"
        return result

    try:
        def _analyze():
            return gemini_client.models.generate_content(model=model_name, contents=contents)

        resp = await asyncio.to_thread(_analyze)
        usage_metadata = _response_usage_metadata(resp)
        raw = resp.text.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        parsed = _parse_json_response_text(raw)
        qa_payload = _normalise_final_v1_keyframe_qa(parsed)
        result.update(
            {
                "analyzed": True,
                "method": f"gemini_final_v1_keyframe_qa_{model_name}",
                "model": model_name,
                "usage_metadata": usage_metadata,
                "schema_version": "video_analysis_final_v1_keyframe_qa",
                "final_v1_keyframe_qa": qa_payload,
                "qa_pass": qa_payload.get("qa_pass"),
            }
        )
        return result
    except Exception as exc:
        result["error"] = f"Gemini final_v1 keyframe QA failed: {str(exc)[:500]}"
        logger.warning("gemini_final_v1_keyframe_qa_failed", extra={"error": str(exc)[:160]})
        return result


async def analyze_v2_judgment_with_openai_keyframes(
    *,
    layer1_visual_content: dict[str, Any],
    keyframes: list[dict[str, Any]],
    title: str,
    performance_context: dict[str, Any] | None = None,
    model_name: str = "gpt-5.5",
) -> dict[str, Any]:
    """Judge Layer2+3 from Layer1 text plus keyframe JPGs using OpenAI vision."""
    result = {
        "analyzed": False,
        "method": f"openai_keyframe_judgment_{model_name}",
        "model": model_name,
        "usage_metadata": {},
        "error": None,
    }
    api_key = llm_gateway._get_api_key("openai")
    if not api_key:
        result["error"] = "OpenAI not available"
        return result
    try:
        from openai import OpenAI
    except Exception as exc:
        result["error"] = f"OpenAI SDK unavailable: {exc}"
        return result

    prompt = _video_v2_judgment_prompt(
        layer1_visual_content=layer1_visual_content,
        keyframes=keyframes,
        performance_context=performance_context,
    )
    content = build_openai_multimodal_content(f"视频标题: {title}\n\n{prompt}", keyframes)
    client = OpenAI(api_key=api_key)
    try:
        def _analyze():
            return client.responses.create(
                model=model_name,
                input=[{"role": "user", "content": content}],
                max_output_tokens=4000,
            )

        resp = await asyncio.to_thread(_analyze)
        usage = getattr(resp, "usage", None)
        usage_metadata: dict[str, Any] = {}
        if usage:
            if hasattr(usage, "model_dump"):
                usage_metadata = usage.model_dump(mode="json", exclude_none=True)
            else:
                usage_metadata = {
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                }
        raw = str(getattr(resp, "output_text", "") or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        parsed = _parse_json_response_text(raw)
        subtitle_used = bool((layer1_visual_content.get("evidence") or {}).get("subtitle_used"))
        merged = {"layer1_visual_content": layer1_visual_content, **parsed}
        _apply_v2_result(
            result,
            merged,
            method=f"openai_keyframe_judgment_{model_name}",
            model=str(getattr(resp, "model", None) or model_name),
            usage_metadata=usage_metadata,
            subtitle_used=subtitle_used,
        )
        return result
    except Exception as exc:
        result["error"] = f"OpenAI keyframe judgment failed: {str(exc)[:500]}"
        logger.warning("openai_keyframe_judgment_failed", extra={"error": str(exc)[:160]})
        return result


async def analyze_v2_judgment_with_anthropic_keyframes(
    *,
    layer1_visual_content: dict[str, Any],
    keyframes: list[dict[str, Any]],
    title: str,
    performance_context: dict[str, Any] | None = None,
    model_name: str = "claude-opus-4-8",
) -> dict[str, Any]:
    """Judge Layer2+3 from Layer1 text plus keyframe JPGs using Claude vision."""
    result = {
        "analyzed": False,
        "method": f"anthropic_keyframe_judgment_{model_name}",
        "model": model_name,
        "usage_metadata": {},
        "error": None,
    }
    api_key = llm_gateway._get_api_key("anthropic")
    if not api_key:
        result["error"] = "Anthropic not available"
        return result
    try:
        import anthropic
    except Exception as exc:
        result["error"] = f"Anthropic SDK unavailable: {exc}"
        return result

    prompt = _video_v2_judgment_prompt(
        layer1_visual_content=layer1_visual_content,
        keyframes=keyframes,
        performance_context=performance_context,
    )
    content = build_anthropic_multimodal_content(f"视频标题: {title}\n\n{prompt}", keyframes)
    client = anthropic.Anthropic(api_key=api_key)
    try:
        def _analyze():
            return client.messages.create(
                model=model_name,
                max_tokens=4000,
                messages=[{"role": "user", "content": content}],
            )

        resp = await asyncio.to_thread(_analyze)
        usage = getattr(resp, "usage", None)
        usage_metadata: dict[str, Any] = {}
        if usage:
            if hasattr(usage, "model_dump"):
                usage_metadata = usage.model_dump(mode="json", exclude_none=True)
            else:
                usage_metadata = {
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                }
        raw = "\n".join(str(block.text) for block in getattr(resp, "content", []) if getattr(block, "type", "") == "text").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        parsed = _parse_json_response_text(raw)
        subtitle_used = bool((layer1_visual_content.get("evidence") or {}).get("subtitle_used"))
        merged = {"layer1_visual_content": layer1_visual_content, **parsed}
        _apply_v2_result(
            result,
            merged,
            method=f"anthropic_keyframe_judgment_{model_name}",
            model=str(getattr(resp, "model", None) or model_name),
            usage_metadata=usage_metadata,
            subtitle_used=subtitle_used,
        )
        return result
    except Exception as exc:
        result["error"] = f"Anthropic keyframe judgment failed: {str(exc)[:500]}"
        logger.warning("anthropic_keyframe_judgment_failed", extra={"error": str(exc)[:160]})
        return result


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


def _apply_final_v1_result(
    result: dict[str, Any],
    parsed: dict[str, Any],
    *,
    method: str,
    model: str,
    usage_metadata: dict[str, Any],
    subtitle_used: bool,
) -> None:
    payload = _normalise_final_v1_result(parsed, subtitle_used=subtitle_used)
    layer1 = payload["layer1_visual_content"]
    layer6 = payload["layer6_flags_and_scores"]
    scores = layer6.get("scores") if isinstance(layer6.get("scores"), dict) else {}
    content_quality = scores.get("content_quality_score") if isinstance(scores.get("content_quality_score"), dict) else {}
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    result.update(
        {
            "analyzed": True,
            "method": method,
            "model": model,
            "usage_metadata": usage_metadata,
            "schema_version": "video_analysis_final_v1",
            "video_analysis_final_v1": payload,
            "content_summary": layer1.get("content_summary") or "",
            "content_genre": "video_analysis_final_v1",
            "content_topic": layer1.get("content_summary") or "",
            "production_quality": (layer1.get("production_observations") or {}).get("professional_level")
            if isinstance(layer1.get("production_observations"), dict)
            else "",
            "timestamps": evidence.get("timestamps") if isinstance(evidence.get("timestamps"), list) else timeline,
            "competitor_mentions": layer1.get("competitor_presence") if isinstance(layer1.get("competitor_presence"), list) else [],
            "quality_scores": {
                key: _score_value(value)
                for key, value in scores.items()
                if isinstance(value, dict) and _score_value(value) is not None
            },
            "quality_overall": _score_value(content_quality) or 0,
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
