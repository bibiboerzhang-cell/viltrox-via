"""
services/audit/similarity.py — Viltrox 检测 + 产品分类 + 相似度检查
"""
from __future__ import annotations

import re
import json
import math
from typing import Any, Dict, List, Optional

from app.core.constants import VILTROX_BRAND_KEYWORDS, PRODUCT_RULES, CAMERA_KEYWORDS, LENS_KEYWORDS, SPAM_COMMENT_KEYWORDS
from app.utils.text import detect_content_types

def classify_product(full_text: str) -> Dict[str, Any]:
    t = full_text.lower()
    best = None
    best_score = 0
    evidence: List[str] = []

    # Load learned keywords (from admin corrections)
    try:
        from app.services.audit.learning import get_all_learned_keywords
        learned = get_all_learned_keywords()
    except Exception:
        learned = {}

    try:
        from app.db.repositories.knowledge import list_product_knowledge_rules
        dynamic_rules = list_product_knowledge_rules(limit=300)
    except Exception:
        dynamic_rules = []

    # Check built-in PRODUCT_RULES + merge learned keywords
    for item in [*PRODUCT_RULES, *dynamic_rules]:
        rule_keywords = [str(keyword).lower() for keyword in item["keywords"]]
        # Merge learned keywords for this label
        learned_extras = learned.get(item["label"], [])
        if learned_extras:
            rule_keywords.extend(kw.lower() for kw in learned_extras)

        matched = [kw for kw in rule_keywords if kw in t]
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


def detect_viltrox(full_text: str, hints: Dict[str, bool]) -> Dict[str, Any]:
    t = full_text.lower()

    brand_hits = sum(1 for kw in VILTROX_BRAND_KEYWORDS if kw in t)
    product_hits = sum(1 for item in PRODUCT_RULES for k in item["keywords"] if k in t)
    hint_score = (
        int(hints.get("logo", False))
        + int(hints.get("product", False))
        + int(hints.get("voice", False))
    )
    tags = detect_content_types(t)

    evidence: List[str] = []
    if brand_hits > 0:
        evidence.append("Text includes Viltrox brand")
    if product_hits > 0:
        evidence.append("Text includes Viltrox product / focal / model keywords")
    if hints.get("logo"):
        evidence.append("Manual mark: Logo")
    if hints.get("product"):
        evidence.append("Manual mark: Product visible")
    if hints.get("voice"):
        evidence.append("Manual mark: Voice mention")
    if tags:
        evidence.append("Content context: " + " / ".join(tags))

    # Official Viltrox accounts or strong brand signals -> auto-confirm
    VILTROX_OFFICIAL_HANDLES = {
        "viltrox.official", "viltrox.usa", "viltrox_official",
        "唯卓仕官方", "viltroxofficial",
    }

    if (brand_hits > 0 and product_hits > 0) \
       or (brand_hits > 0 and hint_score > 0) \
       or (brand_hits >= 2 and len(tags) > 0) \
       or (brand_hits > 0 and product_hits == 0 and len(evidence) >= 2):
        status = "confirmed"
    elif brand_hits > 0 or product_hits > 0 or hint_score > 0:
        status = "suspected"
    else:
        status = "not_detected"

    if not evidence:
        evidence.append("No strong Viltrox evidence found")

    auto_flags = {
        "logo": brand_hits > 0,
        "product": product_hits > 0,
        "voice": False,
        "review": ("Review" in tags or "Tutorial" in tags),
    }

    return {
        "status": status,
        "confirmed": status == "confirmed",
        "evidence": evidence,
        "content_types": tags,
        "auto_flags": auto_flags,
    }


def analyze_comments_for_spam(comments: List[str]) -> Dict[str, Any]:
    if not comments:
        return {"spam_ratio": 0.0, "spam_hits": [], "spam_count": 0}

    hits = []
    spam_count = 0
    for c in comments:
        lc = c.lower()
        matched = [kw for kw in SPAM_COMMENT_KEYWORDS if kw in lc]
        if matched:
            spam_count += 1
            hits.append({"comment": c, "matched": matched})

    ratio = spam_count / max(len(comments), 1)
    return {"spam_ratio": round(ratio, 3), "spam_hits": hits[:5], "spam_count": spam_count}


def compute_risk(
    metrics: Dict[str, int],
    metrics_available: Dict[str, bool],
    comment_spam: Dict[str, Any],
) -> Dict[str, Any]:
    v = metrics["views"]
    l = metrics["likes"]
    c = metrics["comments"]
    s = metrics["shares"]
    f = metrics["favorites"]

    reasons: List[str] = []
    risk = 0

    if metrics_available.get("views") and v > 0:
        if metrics_available.get("likes"):
            like_rate = l / v
            if v >= 5000 and like_rate < 0.01:
                risk += 20
                reasons.append("Low like rate")
        if metrics_available.get("comments"):
            comment_rate = c / v
            if v >= 5000 and comment_rate < 0.001:
                risk += 20
                reasons.append("Low comment rate")
        if metrics_available.get("shares"):
            share_rate = s / v
            if v >= 5000 and share_rate < 0.0005:
                risk += 20
                reasons.append("Low share rate")
        if metrics_available.get("favorites"):
            fav_rate = f / v
            if v >= 5000 and fav_rate > 0.3:
                risk += 10
                reasons.append("Unusually high save rate")

    if comment_spam["spam_ratio"] >= 0.3 and comment_spam["spam_count"] >= 3:
        risk += 20
        reasons.append("Comment section may contain spam / fake engagement")

    risk = min(100, risk)
    penalty = round(risk * 0.8)
    if not reasons:
        reasons = ["No obvious anomaly"]
    return {"risk_score": risk, "penalty": penalty, "reasons": reasons}


def compute_creator_score(
    metrics: Dict[str, int],
    metrics_available: Dict[str, bool],
    risk_score: int,
) -> int:
    weights = {"views": 8, "likes": 18, "comments": 22, "shares": 26, "favorites": 18}
    raw = 0.0
    for key, coef in weights.items():
        if metrics_available.get(key):
            raw += math.log10(metrics[key] + 1) * coef
    penalty = risk_score * 0.35
    return max(0, min(100, round(raw - penalty)))


def compute_campaign_score(
    metrics: Dict[str, int],
    metrics_available: Dict[str, bool],
    detected: bool,
    content_types: List[str],
    platform: str = "",
    video_analysis: Dict[str, Any] = None,
) -> Dict[str, int]:
    if not detected:
        return {"content_score": 0, "campaign_interaction_score": 0, "raw_score": 0, "final_score": 0}

    va = video_analysis or {}

    # ── Facebook image-only post: max 20 pts to prevent gaming ──
    # If it's Facebook and no video detected (just product images), cap heavily
    is_fb_image_only = (
        platform == "Facebook" and
        not va.get("uploaded") and
        va.get("method", "") not in ("ytdlp_Facebook", "claude_vision") and
        not any(ct in (content_types or []) for ct in ["Video", "Vlog", "Tutorial", "Review"])
    )
    if is_fb_image_only:
        # Pure product image post — base score only, max 20
        likes = metrics.get("likes", 0)
        comments = metrics.get("comments", 0)
        mini_interaction = min(10, likes // 10 + comments // 2)
        raw = min(20, 10 + mini_interaction)
        return {
            "content_score": 10,
            "campaign_interaction_score": mini_interaction,
            "raw_score": raw,
            "final_score": raw,
            "capped_reason": "Facebook image-only post — max 20pts",
        }

    # Content score: base 20 + content richness (max 50) + quality boost
    type_bonus = min(50, len(content_types) * 12)
    content_score = min(100, 20 + type_bonus + 30)

    # Interaction weight
    interaction_weight = 0.0
    if metrics_available.get("views"):
        interaction_weight += (metrics["views"] / 1000) * 1
    if metrics_available.get("likes"):
        interaction_weight += metrics["likes"] * 1
    if metrics_available.get("comments"):
        interaction_weight += metrics["comments"] * 6
    if metrics_available.get("shares"):
        interaction_weight += metrics["shares"] * 10
    if metrics_available.get("favorites"):
        interaction_weight += metrics["favorites"] * 8

    interaction_score = min(250, math.floor(interaction_weight / 5))
    raw_score = min(400, 20 + 30 + content_score + interaction_score)

    return {
        "content_score": content_score,
        "campaign_interaction_score": interaction_score,
        "raw_score": raw_score,
        "final_score": raw_score,
    }
