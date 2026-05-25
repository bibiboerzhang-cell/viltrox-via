"""Claude image batch analysis helpers."""
from __future__ import annotations

import json
import re

from app.core.config import ANTHROPIC_API_KEY
from app.core.logging import get_logger
from app.services.ai.analyzers.claude_vision_client import _build_anthropic_client
from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE
from app.services.ai.retry import call_ai_with_retry

logger = get_logger(__name__)


def _analyze_images_batch(images_b64: list, title: str, platform: str, profile_hint: str = "") -> dict:
    """Analyze a batch of images (carousel/gallery) with Claude Vision.
    Each image gets individual composition + gear analysis."""
    if not images_b64 or not ANTHROPIC_AVAILABLE or not ANTHROPIC_API_KEY:
        return {}
    try:
        client = _build_anthropic_client()
        if client is None:
            return {}
        content = []
        for i, b64 in enumerate(images_b64[:10]):
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": b64
            }})
            content.append({"type": "text", "text": f"[图片 {i+1}/{len(images_b64)}]"})

        # Build per-image analysis request
        per_image_schema = ""
        for i in range(min(len(images_b64), 10)):
            per_image_schema += (
                f'{{"image_index":{i+1},'
                f'"composition":"构图描述（中文，e.g.三分构图/中心对称/对角线）",'
                f'"lighting":"布光描述（中文，e.g.自然光/单点闪光/影棚三点光）",'
                f'"camera_body":"机身型号或null",'
                f'"lens":"镜头型号或null",'
                f'"viltrox_visible":true/false,'
                f'"viltrox_product":"Viltrox产品名或null",'
                f'"key_elements":"画面主要元素（中文）",'
                f'"quality_note":"画质和美学亮点（中文）"}},'
            )

        content.append({"type": "text", "text": (
            f"这是来自 {platform} 的 {len(images_b64)} 张图片，标题: \"{title}\"。\n"
            + profile_hint + "\n\n"
            "你是 Viltrox 品牌情报分析师。请逐张分析每张图片的构图和器材。\n\n"
            "重点识别:\n"
            "- VILTROX文字/唯卓仕/橙色圆环/VCM/APO/LAB/Pro/EVO/AIR/EPIC/LUNA/RAZE\n"
            "- 相机机身logo和外形\n"
            "- 竞品镜头品牌 (Sigma/Tamron/Zeiss/Sony GM/Canon L等)\n"
            "- 闪光灯/灯光 (Godox/Profoto/Aputure)\n"
            "- 配件 (SmallRig/Tilta/DJI云台/Atomos监视器)\n\n"
            "返回 JSON (只返回JSON，不要markdown):\n{"
            '"viltrox_detected":true/false,'
            '"confidence":"high/medium/low/none",'
            '"logo_visible":true/false,'
            '"product_visible":true/false,'
            '"camera_gear_present":true/false,'
            '"camera_body":"型号或null",'
            '"camera_brand":"Sony/Canon/Nikon/Fujifilm/ARRI/Blackmagic/RED/DJI/Other/null",'
            '"viltrox_lens":"如 AF 85mm F1.4 Pro VCM 或 null",'
            '"viltrox_products_all":["所有可见Viltrox产品"],'
            '"other_lens":"品牌+型号或null",'
            '"flash":"品牌+型号或null",'
            '"adapter":"品牌+型号或null",'
            '"accessories":["列表"],'
            '"gear_combo":"相机+镜头组合",'
            '"brand_elements":["具体Viltrox证据"],'
            '"products_detected":["Viltrox产品"],'
            '"competitor_products":[{"brand":"Sigma","model":"35mm Art","context":"对比"}],'
            '"competitor_brands":["列表"],'
            '"brand_integration_depth":"incidental/featured/central/exclusive",'
            '"content_genre":"review/tutorial/cinematic/vlog/bts/portrait/street/unboxing/comparison",'
            '"content_topic":"内容主题（英文）",'
            '"content_summary":"2-3句中文内容简介",'
            '"production_quality":"amateur/semi-pro/professional/broadcast",'
            '"audience_fit":"poor/fair/good/excellent",'
            '"content_types":["列表"],'
            '"negative_signals":[],'
            f'"per_image_analysis":[{per_image_schema[:-1]}],'
            '"quality_scores":{"exposure":7,"focus":7,"stability":8,"color_grade":7,"composition":7,"lighting":6,"editing":7,"storytelling":5,"hook":6,"viltrox_branding":8},'
            '"quality_overall":7,'
            '"quality_summary":"2句中文总结图片质量亮点和不足",'
            '"reference_value":"high/medium/low",'
            '"reference_reasons":["中文说明"],'
            '"improvements":[{"area":"构图","priority":"medium","timestamp":"图片1","problem":"具体问题","suggestion":"具体建议（中文）","expected_improvement":"预期效果"}],'
            '"marketing_potential":"high/medium/low",'
            '"marketing_notes":"中文：是否能转化观众购买Viltrox",'
            '"needs_manual_review":false,'
            '"manual_review_reason":null,'
            '"notes":"English: all gear + content description"'
            "}"
        )})

        resp = call_ai_with_retry(
            "claude_vision.image_batch",
            lambda: client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": content}],
            ),
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        if not raw.endswith('}'): raw = raw[:raw.rfind('}')+1] if '}' in raw else raw+'}'
        parsed = json.loads(raw)
        logger.info(
            "image vision | viltrox=%s | confidence=%s | lens=%s",
            parsed.get("viltrox_detected"),
            parsed.get("confidence"),
            parsed.get("viltrox_lens"),
        )
        return parsed
    except Exception as e:
        logger.warning("image vision error: %s", e)
        return {}
