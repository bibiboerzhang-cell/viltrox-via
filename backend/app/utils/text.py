"""
utils/text.py — 文本分析工具（内容类型检测、器材提取）
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from app.core.constants import CAMERA_KEYWORDS, LENS_KEYWORDS

def detect_content_types(text: str) -> List[str]:
    t = text.lower()
    tags: List[str] = []
    if re.search(r"review|hands-on|first look|comparison|vs\b|versus", t):
        tags.append("Review")
    if re.search(r"tutorial|how to|guide|tips|tricks", t):
        tags.append("Tutorial")
    if re.search(r"sample|footage|shot on|test shot|b-roll", t):
        tags.append("Footage")
    if re.search(r"bts|behind the scenes", t):
        tags.append("BTS")
    if re.search(r"vlog|travel|daily|day in", t):
        tags.append("Vlog")
    if re.search(r"unbox|unboxing", t):
        tags.append("Unboxing")
    if re.search(r"wedding|portrait|street|landscape|astro", t):
        tags.append("Photography")
    return tags


def classify_product(full_text: str) -> Dict[str, Any]:
    t = full_text.lower()
    best = None
    best_score = 0
    evidence: List[str] = []

    for item in PRODUCT_RULES:
        matched = [kw for kw in item["keywords"] if kw in t]
        score = len(matched)
        if score > best_score:
            best_score = score
            best = item
            evidence = matched

    if best:
        confidence = "high" if best_score >= 2 else "medium"
        return {
            "series": best["series"],
            "label": best["label"],
            "confidence": confidence,
            "evidence": evidence,
        }

    if "viltrox" in t or "唯卓仕" in t:
        return {
            "series": "VILTROX",
            "label": "Brand detected but no exact product",
            "confidence": "low",
            "evidence": [],
        }

    return {"series": "", "label": "", "confidence": "none", "evidence": []}


def parse_gear_from_caption(text: str) -> dict:
    """
    Parse camera gear from Instagram/social media captions.
    Handles formats like:
      Camera: @sonyalpha 6700
      Lens: @viltrox.usa 27mm f/1.2
      📷 Sony A7III | 🔭 Viltrox 85mm F1.4
      Shot on: FX3 + Viltrox 35mm
    """
    if not text:
        return {}

    result = {
        "camera_body": None,
        "camera_brand": None,
        "viltrox_lens": None,
        "other_lens": None,
        "gear_combo": "",
        "parsed_from_caption": True,
    }

    # Normalize text
    t = text.replace("\n", " ").replace("  ", " ")

    # ── Pattern 1: "Camera: xxx" / "Cam: xxx" / "Shot on: xxx" ──
    cam_patterns = [
        r'[Cc]amera\s*[:：]\s*([^\n,|•]+)',
        r'[Cc]am\s*[:：]\s*([^\n,|•]+)',
        r'[Ss]hot\s+on\s*[:：]?\s*([^\n,|•+]+)',
        r'[Bb]ody\s*[:：]\s*([^\n,|•]+)',
        r'📷\s*[:：]?\s*([^\n,|•🔭]+)',
    ]
    for pat in cam_patterns:
        m = re.search(pat, t)
        if m:
            cam_raw = m.group(1).strip()
            # Clean @mentions like @sonyalpha -> Sony Alpha
            cam_raw = re.sub(r'@\w+\s*', '', cam_raw).strip()
            # Detect brand
            cam_lower = cam_raw.lower()
            brand = None
            for b, keywords in {
                "Sony": ["sony", "a7", "fx3", "fx6", "fx30", "zv", "a6"],
                "Canon": ["canon", "eos", "r5", "r6", "c70"],
                "Nikon": ["nikon", "z6", "z8", "z9"],
                "Fujifilm": ["fuji", "x-t", "x-h", "gfx"],
                "ARRI": ["arri", "alexa"],
                "Blackmagic": ["blackmagic", "bmpcc"],
                "RED": ["red komodo", "raptor"],
            }.items():
                if any(k in cam_lower for k in keywords):
                    brand = b
                    break
            if cam_raw and len(cam_raw) < 50:
                result["camera_body"] = cam_raw
                result["camera_brand"] = brand
            break

    # ── Pattern 2: "Lens: xxx" / "Glass: xxx" ──
    lens_patterns = [
        r'[Ll]ens\s*[:：]\s*([^\n,|•]+)',
        r'[Gg]lass\s*[:：]\s*([^\n,|•]+)',
        r'[Oo]ptics?\s*[:：]\s*([^\n,|•]+)',
        r'🔭\s*[:：]?\s*([^\n,|•📷]+)',
        r'🔎\s*[:：]?\s*([^\n,|•]+)',
    ]
    for pat in lens_patterns:
        m = re.search(pat, t)
        if m:
            lens_raw = m.group(1).strip()
            # Clean @mentions — @viltrox.usa -> viltrox
            lens_raw = re.sub(r'@\w+[\.\w]*\s*', '', lens_raw).strip()
            lens_lower = lens_raw.lower()

            # Check if it's Viltrox
            is_viltrox = any(kw in lens_lower for kw in [
                "viltrox", "唯卓仕", "27mm", "35mm f1.2", "85mm f1.4",
                "56mm f1.4", "23mm f1.4", "13mm", "75mm", "135mm",
                "50mm f1.4", "40mm f2.5"
            ])
            # Also check if @viltrox.usa was mentioned in the original text near "Lens:"
            if "@viltrox" in text.lower():
                is_viltrox = True

            if lens_raw and len(lens_raw) < 80:
                if is_viltrox:
                    # Prepend Viltrox if not already there
                    if not lens_raw.lower().startswith("viltrox"):
                        lens_raw = "Viltrox " + lens_raw
                    result["viltrox_lens"] = lens_raw
                else:
                    result["other_lens"] = lens_raw
            break

    # ── Pattern 3: Inline mentions like "Sony A6700 + Viltrox 27mm f/1.2" ──
    if not result["camera_body"] and not result["viltrox_lens"]:
        combo_match = re.search(
            r'(Sony|Canon|Nikon|Fuji(?:film)?|ARRI|Blackmagic)\s+([A-Za-z0-9\s\-]+?)'
            r'\s*[+&|,]\s*(Viltrox|唯卓仕)?\s*([0-9]+mm[^\n,|]{0,30})',
            t, re.IGNORECASE
        )
        if combo_match:
            result["camera_body"] = combo_match.group(1) + " " + combo_match.group(2).strip()
            result["camera_brand"] = combo_match.group(1)
            lens_part = (combo_match.group(3) or "Viltrox") + " " + combo_match.group(4).strip()
            result["viltrox_lens"] = lens_part.strip()

    # Build gear combo string
    parts = []
    if result["camera_body"]: parts.append(result["camera_body"])
    if result["viltrox_lens"]: parts.append(result["viltrox_lens"])
    elif result["other_lens"]: parts.append(result["other_lens"])
    if parts:
        result["gear_combo"] = " + ".join(parts)

    return result


def detect_gear_mentions(full_text: str) -> Dict[str, List[str]]:
    t = full_text.lower()
    cameras = sorted({kw for kw in CAMERA_KEYWORDS if kw in t})
    lenses = sorted({kw for kw in LENS_KEYWORDS if kw in t})
    return {"camera_mentions": cameras, "lens_mentions": lenses}

