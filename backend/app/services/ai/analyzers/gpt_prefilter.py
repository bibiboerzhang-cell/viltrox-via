"""
services/ai/analyzers/gpt_prefilter.py — GPT-4o-mini 快速预筛
"""
from __future__ import annotations

import json
import re

from app.core.logging import get_logger
from app.services.ai.clients.openai_client import OPENAI_AVAILABLE, openai_client

logger = get_logger(__name__)

def gpt_prefilter_caption(title: str, caption: str, platform: str) -> dict:
    """
    Use GPT-4o-mini to quickly pre-filter submissions from caption/title.
    Extremely cheap ($0.0003/1K tokens) — runs before any expensive analysis.
    Returns: viltrox_likely, gear_extracted, skip_vision
    """
    result = {
        "viltrox_likely": False,
        "camera_body": None,
        "viltrox_lens": None,
        "other_lens": None,
        "skip_vision": False,
        "content_genre": "",
        "confidence": "none",
        "error": None,
    }
    if not OPENAI_AVAILABLE or not openai_client:
        return result
    if not title and not caption:
        return result

    try:
        text = f"Title: {title}\nCaption: {caption[:800]}\nPlatform: {platform}"
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            temperature=0,
            messages=[{
                "role": "system",
                "content": (
                    "You are a camera gear extraction AI for Viltrox brand. "
                    "Extract gear info from social media captions. "
                    "Respond ONLY with compact JSON, no markdown."
                )
            }, {
                "role": "user",
                "content": (
                    f"{text}\n\n"
                    "Return JSON:\n"
                    '{"viltrox_likely":true/false,'
                    '"camera_body":"Sony A7RIV or null",'
                    '"viltrox_lens":"Viltrox 27mm F1.2 or null",'
                    '"other_lens":"non-Viltrox lens or null",'
                    '"content_genre":"review/tutorial/cinematic/vlog/other",'
                    '"skip_vision":true/false,'
                    '"confidence":"high/medium/low/none"}'
                    "\n\nskip_vision=true only if camera AND lens are clearly stated in text."
                )
            }]
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        result.update(parsed)
        logger.info(
            "gpt_prefilter.complete",
            extra={
                "viltrox_likely": parsed.get("viltrox_likely"),
                "skip_vision": parsed.get("skip_vision"),
                "confidence": parsed.get("confidence"),
            },
        )
    except Exception as e:
        result["error"] = str(e)
        logger.warning("gpt_prefilter.failed", extra={"error": str(e)})
    return result


def gpt_analyze_engagement_anomaly(
    metrics: dict, platform: str, handle: str,
    history: list
) -> dict:
    """
    Use GPT-4o-mini to detect fake engagement / anomalies in bulk.
    Called during daily 12:00 recalculation — zero Claude cost.
    """
    result = {"anomaly": False, "risk_delta": 0, "reasons": [], "error": None}
    if not OPENAI_AVAILABLE or not openai_client:
        return result
    try:
        hist_str = json.dumps(history[-5:], ensure_ascii=False) if history else "[]"
        prompt = (
            f"Platform: {platform}, Creator: {handle}\n"
            f"Current metrics: {json.dumps(metrics)}\n"
            f"Recent history (last 5): {hist_str}\n\n"
            "Detect fake engagement anomalies. Return JSON:\n"
            '{"anomaly":true/false,"risk_delta":0-50,'
            '"reasons":["中文原因列表"]}'
        )
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            temperature=0,
            messages=[
                {"role": "system", "content": "You are a social media fraud detection AI. Respond only JSON."},
                {"role": "user", "content": prompt}
            ]
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        result.update(parsed)
    except Exception as e:
        result["error"] = str(e)
        logger.warning("gpt_prefilter.anomaly_failed", extra={"error": str(e)})
    return result
