"""
services/ai/analyzers/claude_text.py — Claude 文本/字幕内容分析
"""
from __future__ import annotations

import json
import re
from typing import Dict

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE, get_claude_client
from app.services.ai.retry import call_ai_with_retry
from app.services.scoring.core import compute_weighted_scores
from app.utils.text import parse_gear_from_caption

logger = get_logger(__name__)

def analyze_text_content(title: str, caption: str, url: str, platform: str, scraped_text: str, og_image: str = "") -> dict:
    """
    For URL submissions (TikTok/IG/YouTube etc) — use Claude to analyze
    the text content and extract gear info + content summary.
    No video frames needed.
    """
    result = {
        "analyzed": False,
        "method": "text_analysis",
        "camera_body": None,
        "camera_brand": None,
        "viltrox_lens": None,
        "other_lens": None,
        "flash": None,
        "adapter": None,
        "accessories": [],
        "gear_combo": "",
        "brand_elements": [],
        "products_detected": [],
        "content_genre": "",
        "content_topic": "",
        "content_summary": "",
        "production_quality": "",
        "audience_fit": "",
        "content_types": [],
        "notes": "",
        "error": None,
    }

    # ── Step 0: Fast caption parser (no API call needed) ──
    # Parse structured gear info from caption FIRST before Claude
    all_text = " ".join(filter(None, [title, caption, scraped_text]))
    caption_gear = parse_gear_from_caption(all_text)
    if caption_gear.get("camera_body"):
        result["camera_body"] = caption_gear["camera_body"]
        result["camera_brand"] = caption_gear["camera_brand"]
        logger.info("claude_text.caption_camera_detected", extra={"camera_body": result["camera_body"]})
    if caption_gear.get("viltrox_lens"):
        result["viltrox_lens"] = caption_gear["viltrox_lens"]
        result["brand_elements"].append(f"Caption mentions: {result['viltrox_lens']}")
        result["products_detected"].append(result["viltrox_lens"])
        result["analyzed"] = True
        logger.info("claude_text.caption_lens_detected", extra={"viltrox_lens": result["viltrox_lens"]})
    if caption_gear.get("other_lens"):
        result["other_lens"] = caption_gear["other_lens"]
    if caption_gear.get("gear_combo"):
        result["gear_combo"] = caption_gear["gear_combo"]

    if not ANTHROPIC_AVAILABLE:
        return result

    combined_text = " ".join(filter(None, [title, caption, scraped_text])).strip()
    if not combined_text or len(combined_text) < 10:
        return result

    try:
        client = get_claude_client()
        if not client:
            return None
        prompt = f"""You are a senior brand intelligence analyst for Viltrox camera lens company.

Platform: {platform}
URL: {url}
Title: {title}
Description/Caption: {caption}
Additional text: {scraped_text[:800]}

Analyze this content and respond ONLY with valid JSON:
{{
  "camera_body": "e.g. Sony A7III or null",
  "camera_brand": "Sony/Canon/Nikon/Fujifilm/ARRI/Blackmagic/Other/null",
  "viltrox_lens": "e.g. Viltrox AF 35mm F1.2 LAB or null",
  "other_lens": "non-Viltrox lens mentioned or null",
  "flash": "flash unit if mentioned or null",
  "adapter": "adapter if mentioned or null",
  "accessories": ["gimbal","stabilizer"],
  "gear_combo": "Camera + Lens combo string or empty",
  "brand_elements": ["specific Viltrox products/keywords mentioned"],
  "products_detected": ["e.g. AF 35mm F1.2 LAB"],
  "content_genre": "review/tutorial/cinematic/vlog/bts/unboxing/comparison/showcase/portrait/street",
  "content_topic": "one sentence: what this content is about",
  "content_summary": "2句中文，描述这个视频/内容是关于什么的，用了什么器材（如有提及），主要亮点",
  "production_quality": "amateur/semi-pro/professional/broadcast",
  "audience_fit": "poor/fair/good/excellent",
  "content_types": ["vlog","review"],
  "notes": "brief English summary",
  "quality_scores": {{"exposure":7,"focus":7,"stability":7,"color_grade":7,"composition":7,"lighting":6,"editing":7,"storytelling":6,"hook":7,"viltrox_branding":8}},
  "quality_overall": 7,
  "quality_summary": "2句中文总结内容质量",
  "reference_value": "high/medium/low",
  "reference_reasons": ["中文原因"],
  "improvements": [{{"area":"内容","priority":"high","timestamp":"—","problem":"问题","suggestion":"建议","expected_improvement":"效果"}}],
  "marketing_potential": "high/medium/low",
  "marketing_notes": "中文转化潜力评估"
}}"""

        resp = call_ai_with_retry(
            "claude_text.content",
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            ),
        )

        # ── If we have a thumbnail image, do a Vision pass too ──
        image_analysis = {}
        if og_image and og_image.startswith("http"):
            try:
                import urllib.request
                req_obj = urllib.request.Request(og_image, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req_obj, timeout=8) as resp_img:
                    img_bytes = resp_img.read()
                if img_bytes and len(img_bytes) > 1000:
                    img_b64 = base64.b64encode(img_bytes).decode()
                    # Detect image type
                    media_type = "image/jpeg"
                    if og_image.endswith(".png"): media_type = "image/png"
                    elif og_image.endswith(".webp"): media_type = "image/webp"

                    vision_resp = call_ai_with_retry(
                        "claude_text.thumbnail",
                        lambda: client.messages.create(
                            model=CLAUDE_MODEL,
                            max_tokens=600,
                            messages=[{"role": "user", "content": [
                                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}},
                                {"type": "text", "text": (
                                    f"This is the thumbnail/cover image from a {platform} post.\n"
                                    "Look for: VILTROX logo/text on lens, camera body, lens markings, "
                                    "focal length numbers, aperture markings, VCM/APO text, orange accent ring.\n"
                                    "Respond JSON only: {\"viltrox_detected\":true/false,\"camera_body\":\"or null\","
                                    "\"viltrox_lens\":\"or null\",\"other_lens\":\"or null\","
                                    "\"gear_combo\":\"or empty\",\"content_genre\":\"or empty\","
                                    "\"thumbnail_summary\":\"one sentence describing the image\"}"
                                )}
                            ]}],
                        ),
                    )
                    vraw = vision_resp.content[0].text.strip()
                    vraw = re.sub(r"^```json\s*|```$", "", vraw, flags=re.MULTILINE).strip()
                    image_analysis = json.loads(vraw)
                    logger.info(
                        "claude_text.thumbnail_vision_complete",
                        extra={"viltrox_detected": image_analysis.get("viltrox_detected"), "viltrox_lens": image_analysis.get("viltrox_lens")},
                    )
            except Exception as e:
                logger.warning("claude_text.thumbnail_vision_failed", extra={"error": str(e)})
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        if not raw.endswith('}'): raw = raw[:raw.rfind('}')+1] if '}' in raw else raw+'}'
        analysis = json.loads(raw)

        result["analyzed"]          = True
        result["camera_body"]       = image_analysis.get("camera_body") or analysis.get("camera_body")
        result["camera_brand"]      = analysis.get("camera_brand")
        result["viltrox_lens"]      = image_analysis.get("viltrox_lens") or analysis.get("viltrox_lens")
        result["other_lens"]        = image_analysis.get("other_lens") or analysis.get("other_lens")
        result["flash"]             = analysis.get("flash")
        result["adapter"]           = analysis.get("adapter")
        result["accessories"]       = analysis.get("accessories", [])
        result["gear_combo"]        = image_analysis.get("gear_combo") or analysis.get("gear_combo", "")
        result["brand_elements"]    = analysis.get("brand_elements", [])
        result["products_detected"] = analysis.get("products_detected", [])
        result["content_genre"]     = image_analysis.get("content_genre") or analysis.get("content_genre", "")
        result["content_topic"]     = analysis.get("content_topic", "")
        result["content_summary"]   = analysis.get("content_summary", "")
        result["production_quality"]= analysis.get("production_quality", "")
        result["audience_fit"]      = analysis.get("audience_fit", "")
        result["content_types"]     = analysis.get("content_types", [])
        result["notes"]             = analysis.get("notes", "") + (f" | Thumbnail: {image_analysis.get('thumbnail_summary','')}" if image_analysis.get("thumbnail_summary") else "")
        # Quality fields — now included in text analysis prompt
        result["quality_scores"]    = analysis.get("quality_scores", {})
        result["quality_overall"]   = analysis.get("quality_overall", 0)
        result["quality_summary"]   = analysis.get("quality_summary", "")
        result["reference_value"]   = analysis.get("reference_value", "")
        result["reference_reasons"] = analysis.get("reference_reasons", [])
        result["improvements"]      = analysis.get("improvements", [])
        result["marketing_potential"]= analysis.get("marketing_potential", "")
        result["marketing_notes"]   = analysis.get("marketing_notes", "")
        # ── Compute weighted scores by genre ──
        ws = compute_weighted_scores(
            result.get("quality_scores", {}),
            result.get("content_genre", "")
        )
        result["tech_score"]      = ws["tech_score"]
        result["marketing_score"] = ws["marketing_score"]
        result["quality_overall"] = ws["quality_overall"]
        if image_analysis.get("viltrox_detected"):
            result["brand_elements"].append("Viltrox detected in thumbnail image")
        logger.info(
            "claude_text.analysis_complete",
            extra={
                "genre": result["content_genre"],
                "viltrox_lens": result["viltrox_lens"],
                "quality_overall": result["quality_overall"],
                "tech_score": ws["tech_score"],
                "marketing_score": ws["marketing_score"],
            },
        )

    except Exception as e:
        result["error"] = str(e)
        logger.warning("claude_text.analysis_failed", extra={"error": str(e)})

    return result


def check_content_similarity(conn, handle: str, title: str, platform: str,
                              days: int = 14, url: str = "") -> dict:
    """
    Check if a creator has submitted very similar content recently.
    Returns similarity info and whether manual review is needed.
    hard_reject=True means instant zero score, no review needed.
    """
    result = {
        "similar_found": False, "similar_count": 0,
        "reason": "", "needs_review": False, "hard_reject": False,
    }
    if not handle:
        return result
    try:
        c = conn.cursor()

        # ── Hard reject: exact same URL already exists (any user) ──
        if url:
            url_clean = url.strip().split("?")[0].rstrip("/")
            existing_url = c.execute("""
                SELECT id, extracted_handle FROM submissions
                WHERE url LIKE ? LIMIT 1
            """, (f"{url_clean}%",)).fetchone()
            if existing_url:
                result["hard_reject"] = True
                result["needs_review"] = False
                result["similar_found"] = True
                result["reason"] = (
                    f"⛔ 该链接已被提交过 (ID #{existing_url[0]}, @{existing_url[1]})，"
                    f"每条内容只能提交一次，请勿重复提交。"
                )
                return result

        # Count recent submissions from same creator
        recent = c.execute("""
            SELECT id, title, detection_status, created_at, final_score, url
            FROM submissions
            WHERE extracted_handle = ?
            AND created_at >= datetime('now', ? || ' days')
            ORDER BY created_at DESC LIMIT 30
        """, (handle, f"-{days}")).fetchall()

        if not recent:
            return result

        result["similar_count"] = len(recent)

        # Flag 1: Same URL different query string
        if url:
            url_base = url.strip().split("?")[0].rstrip("/")
            for row in recent:
                prev_url = (row[5] or "").strip().split("?")[0].rstrip("/")
                if prev_url and prev_url == url_base:
                    result["hard_reject"] = True
                    result["similar_found"] = True
                    result["reason"] = f"⛔ 相同链接已提交 (ID #{row[0]})，拒绝重复。"
                    return result

        # Flag 2: High volume spam
        if len(recent) >= 8:
            result["hard_reject"] = True
            result["similar_found"] = True
            result["reason"] = f"⛔ 异常提交频率：{len(recent)}条/{days}天，已暂停计分。"
            return result
        elif len(recent) >= 5:
            result["similar_found"] = True
            result["needs_review"] = True
            result["reason"] = f"高频提交：{len(recent)}条/{days}天，需人工审核。"

        # Flag 3: Identical or very similar title
        if title:
            title_lower = title.lower().strip()
            for row in recent:
                prev_title = (row[1] or "").lower().strip()
                if prev_title and prev_title == title_lower:
                    result["hard_reject"] = True
                    result["similar_found"] = True
                    result["reason"] = f"⛔ 完全相同标题已提交 (ID #{row[0]})，拒绝重复。"
                    return result
                t_words = set(title_lower.split())
                p_words = set(prev_title.split())
                if t_words and p_words and len(t_words) > 3:
                    overlap = len(t_words & p_words) / max(len(t_words), len(p_words))
                    if overlap > 0.85:
                        result["hard_reject"] = True
                        result["similar_found"] = True
                        result["reason"] = f"⛔ 内容高度相似({int(overlap*100)}%)已提交 (ID #{row[0]})，拒绝。"
                        return result
                    elif overlap > 0.7:
                        result["similar_found"] = True
                        result["needs_review"] = True
                        result["reason"] = f"内容相似度{int(overlap*100)}%，需人工审核 (参考 #{row[0]})"

        # Flag 4: Spam pattern — all zero scores
        zero_scores = sum(1 for r in recent if (r[4] or 0) == 0)
        if zero_scores >= 5:
            result["hard_reject"] = True
            result["similar_found"] = True
            result["reason"] = f"⛔ 检测到刷分模式：{zero_scores}条零分记录，暂停计分。"

    except Exception as e:
        result["error"] = str(e)

    return result
