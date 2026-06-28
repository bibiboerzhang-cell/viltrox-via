"""Gemini 视频分析 legacy(非 v2/final_v1)inline prompt 构造器(从 gemini_video.py 抽出,行为不变)。

纯函数:只用入参拼 f-string,零 I/O/零外部依赖。被 gemini_video re-export 回灌,调用点不变。
红线:纯 prompt 文本构造,零触 viltrox_fit_score。
"""
from __future__ import annotations


def _video_legacy_prompt(
    *,
    title: str,
    profile_ctx: str,
    subtitle_ctx: str,
    subtitle_raw: str,
) -> str:
    return f"""你是 Viltrox (唯卓仕) 品牌资深内容分析师。请仔细观看这个完整视频的每一帧画面和音频。{profile_ctx}{subtitle_ctx}
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
