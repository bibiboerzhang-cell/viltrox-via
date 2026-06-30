"""Gemini 视频分析 prompt 构造器(从 gemini_video.py 抽出,行为不变)。

纯函数:只用 json + 入参 + 彼此,零 I/O/零外部依赖。被 gemini_video re-export 回灌,调用点不变。
红线:纯 prompt 文本构造,零触 viltrox_fit_score。
"""
from __future__ import annotations

import json
from typing import Any


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
    return f"""你是 Viltrox (唯卓仕) 的视频审查员。你同时站在三个视角判断完整视频：
1. 达人发掘(找合作候选,默认主视角)：这条多半是创作者的自有内容、不是 Viltrox 出资的投放。核心问题是「这个创作者值不值得 Viltrox 接触合作」——看他是否在我们的品类(镜头/相机/摄影/配件)、制作力、受众契合与说服力。若视频拍的是竞品或非 Viltrox 产品,这通常恰恰是强契合信号(同品类、已被验证的内容力与受众),不是失败;竞品推广要当作「现有品牌关系/排他风险/合作前提」来评估,绝不能因为不是 Viltrox 产品就把整体价值/契合度打成零。
2. 品牌方(仅限已合作/复盘)：仅当这条确实是 Viltrox 出资或赞助的投放时,才追问它有没有真的帮 Viltrox 卖镜头、证明镜头、产出可复用素材;「投放失败/价值为零」这类措辞只用于这种已付费却没卖好的情形,不要套到自有内容或竞品内容上。
3. 真实观众：一个摄影/视频创作者看完后是否心动、是否想继续了解、是否反感商业植入。

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
    "final_verdict": "一句话最终结论：必须分开说「这条视频对 Viltrox 投放的价值(拍竞品/非自家产品时可能确实低)」和「这个创作者对 Viltrox 未来合作的契合度(同品类高制作力时可能很高)」，不要把两者混成一句彻底失败/价值为零",
    "key_hook": "最值得品牌方深入了解的一点或最大疑点"
  }}
}}"""


def _video_final_v1_static_prompt() -> str:
    return """你是 Viltrox (唯卓仕) 的视频审查员。你同时站在三个视角判断完整视频：
1. 达人发掘(找合作候选,默认主视角)：这条多半是创作者的自有内容、不是 Viltrox 出资的投放。核心问题是「这个创作者值不值得 Viltrox 接触合作」——看他是否在我们的品类(镜头/相机/摄影/配件)、制作力、受众契合与说服力。若视频拍的是竞品或非 Viltrox 产品,这通常恰恰是强契合信号(同品类、已被验证的内容力与受众),不是失败;竞品推广要当作「现有品牌关系/排他风险/合作前提」来评估,绝不能因为不是 Viltrox 产品就把整体价值/契合度打成零。
2. 品牌方(仅限已合作/复盘)：仅当这条确实是 Viltrox 出资或赞助的投放时,才追问它有没有真的帮 Viltrox 卖镜头、证明镜头、产出可复用素材;「投放失败/价值为零」这类措辞只用于这种已付费却没卖好的情形,不要套到自有内容或竞品内容上。
3. 真实观众：一个摄影/视频创作者看完后是否心动、是否想继续了解、是否反感商业植入。

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
分数锚点再次强调：90+ 必须罕见且证据强；70-85 是可用但要指出不足；55-70 是普通达标；40-55 是明显短板；40以下是投放风险或证据很差。不要因为画面好看就默认渠道值高，也不要因为互动高就忽略归因风险。
final_verdict 必须分开说「这条视频对 Viltrox 投放的价值(拍竞品/非自家产品时可能确实低)」和「这个创作者对 Viltrox 未来合作的契合度(同品类高制作力时可能很高)」，不要把两者混成一句彻底失败/价值为零；自有内容或竞品内容默认按达人发掘视角(找合作候选)判断，不是已付费投放复盘。"""


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


