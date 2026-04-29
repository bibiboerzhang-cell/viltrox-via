"""
services/via/learning_signals.py — lightweight keyword and user-trait learning for Via
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any


EN_STOPWORDS = {
    "about", "after", "also", "and", "are", "ask", "because", "best", "budget", "camera", "can",
    "could", "for", "from", "give", "good", "have", "help", "here", "into", "just", "lens",
    "like", "more", "need", "price", "really", "show", "some", "that", "them", "thing", "this",
    "want", "what", "when", "which", "with", "would", "your",
}

CAMERA_SYSTEM_PATTERNS = {
    "sony": [r"\bsony\b", r"sony", r"索尼", r"\bfe\b", r"e-mount", r"\bson y\b"],
    "canon": [r"\bcanon\b", r"canon", r"佳能", r"\brf\b", r"rf-mount"],
    "nikon": [r"\bnikon\b", r"nikon", r"尼康", r"\bz-mount\b", r"\bz口\b"],
    "fujifilm": [r"\bfuji\b", r"\bfujifilm\b", r"fuji", r"fujifilm", r"富士", r"\bxf\b"],
    "lumix": [r"\blumix\b", r"lumix", r"松下", r"\bl-mount\b", r"\bl卡口\b"],
}

INTEREST_PATTERNS = {
    "portrait": [r"人像", r"\bportrait\b"],
    "street": [r"街拍", r"\bstreet\b"],
    "video": [r"视频", r"拍摄", r"\bvideo\b", r"\bfilmmaking\b", r"\bshooting\b"],
    "cinematic": [r"电影感", r"\bcinematic\b"],
    "vlog": [r"vlog", r"日常", r"短视频"],
    "travel": [r"旅行", r"旅拍", r"\btravel\b"],
    "product": [r"产品", r"开箱", r"\bproduct\b"],
    "wedding": [r"婚礼", r"\bwedding\b"],
    "sports": [r"运动", r"\bsports?\b"],
    "wildlife": [r"野生", r"\bwildlife\b", r"\bbird\b"],
}

PRODUCT_FAMILY_PATTERNS = {
    "air": [r"\bair\b", r"air", r"air系列"],
    "pancake": [r"饼干头", r"\bpancake\b"],
    "pro": [r"\bpro\b", r"pro系列", r"旗舰"],
    "lab": [r"\blab\b", r"lab系列"],
    "vintage": [r"复古", r"\bvintage\b"],
    "anamorphic": [r"变形宽银幕", r"\banamorphic\b"],
}

INTENT_PATTERNS = {
    "buy": [r"买啥", r"推荐", r"\bbuy\b", r"\brecommend\b"],
    "specs": [r"参数", r"规格", r"\bspecs?\b"],
    "link": [r"链接", r"官网", r"\blink\b", r"\burl\b"],
    "compare": [r"对比", r"区别", r"\bcompare\b", r"\bversus\b", r"\bvs\b"],
    "improve": [r"改进", r"提升", r"\bimprove\b", r"\bbetter\b"],
    "learn": [r"学习", r"研究", r"\blearn\b", r"\bresearch\b"],
}

KEYWORD_PATTERNS = {
    "sony": CAMERA_SYSTEM_PATTERNS["sony"],
    "canon": CAMERA_SYSTEM_PATTERNS["canon"],
    "nikon": CAMERA_SYSTEM_PATTERNS["nikon"],
    "fujifilm": CAMERA_SYSTEM_PATTERNS["fujifilm"],
    "lumix": CAMERA_SYSTEM_PATTERNS["lumix"],
    "air": PRODUCT_FAMILY_PATTERNS["air"],
    "pancake": PRODUCT_FAMILY_PATTERNS["pancake"],
    "portrait": INTEREST_PATTERNS["portrait"],
    "cinematic": INTEREST_PATTERNS["cinematic"],
    "vlog": INTEREST_PATTERNS["vlog"],
    "video": INTEREST_PATTERNS["video"],
    "budget": [r"预算", r"\bbudget\b", r"便宜", r"学生", r"\bstudent\b"],
    "50mm": [r"\b50mm\b", r"\b50\b", r"50定"],
    "85mm": [r"\b85mm\b", r"\b85\b"],
    "specs": INTENT_PATTERNS["specs"],
    "link": INTENT_PATTERNS["link"],
}


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def _detect_language(text: str) -> str:
    return "zh" if _contains_cjk(text) else "en"


def _matches(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _extract_keyword_hits(text: str) -> list[str]:
    lowered = str(text or "").lower()
    hits: list[str] = []
    for keyword, patterns in KEYWORD_PATTERNS.items():
        if _matches(patterns, lowered):
            hits.append(keyword)
    english_tokens = re.findall(r"[a-z][a-z0-9\-\+]{2,}", lowered)
    for token in english_tokens:
        if token in EN_STOPWORDS:
            continue
        if token not in hits:
            hits.append(token[:24])
        if len(hits) >= 10:
            break
    return hits[:10]


def _detect_camera_system(text: str) -> str:
    lowered = str(text or "").lower()
    for label, patterns in CAMERA_SYSTEM_PATTERNS.items():
        if _matches(patterns, lowered):
            return label
    return ""


def _detect_budget_band(text: str) -> str:
    lowered = str(text or "").lower()
    if _matches([r"预算不高", r"预算有限", r"便宜", r"\bbudget\b", r"\blow budget\b", r"\bstudent\b", r"学生"], lowered):
        return "budget"
    if _matches([r"中等预算", r"\bmid\b", r"\bmid-range\b"], lowered):
        return "mid"
    if _matches([r"旗舰", r"高端", r"\bpremium\b", r"\bpro budget\b"], lowered):
        return "premium"
    return ""


def _detect_creator_stage(text: str) -> str:
    lowered = str(text or "").lower()
    if _matches([r"\bstudent\b", r"学生"], lowered):
        return "student"
    if _matches([r"\bbeginner\b", r"新手", r"刚开始"], lowered):
        return "beginner"
    if _matches([r"\bprofessional\b", r"\bpro\b", r"商业", r"职业"], lowered):
        return "professional"
    return ""


def _detect_tags(text: str, patterns_map: dict[str, list[str]]) -> list[str]:
    lowered = str(text or "").lower()
    return [label for label, patterns in patterns_map.items() if _matches(patterns, lowered)]


def _extract_focal_keywords(text: str) -> list[str]:
    values = []
    for match in re.findall(r"\b(\d{2,3})mm\b", str(text or "").lower()):
        values.append(f"{match}mm")
    return values[:4]


def _build_summary(language: str, traits: dict[str, Any], keywords: list[str]) -> str:
    keyword_slice = keywords[:5]
    if language == "zh":
        bits: list[str] = []
        if traits.get("camera_system"):
            bits.append(f"{traits['camera_system']} 用户")
        if traits.get("creator_stage"):
            bits.append(traits["creator_stage"])
        if traits.get("budget_band"):
            bits.append(f"{traits['budget_band']} 预算")
        if traits.get("interest_tags"):
            bits.append("兴趣：" + "、".join(traits["interest_tags"][:3]))
        if traits.get("product_families"):
            bits.append("关注：" + "、".join(traits["product_families"][:2]))
        if keyword_slice:
            bits.append("关键词：" + "、".join(keyword_slice))
        return "；".join(bits)[:220] or "用户正在和 Via 聊产品与创作。"
    bits = []
    if traits.get("camera_system"):
        bits.append(f"{traits['camera_system']} shooter")
    if traits.get("creator_stage"):
        bits.append(traits["creator_stage"])
    if traits.get("budget_band"):
        bits.append(f"{traits['budget_band']} budget")
    if traits.get("interest_tags"):
        bits.append("interest: " + ", ".join(traits["interest_tags"][:3]))
    if traits.get("product_families"):
        bits.append("families: " + ", ".join(traits["product_families"][:2]))
    if keyword_slice:
        bits.append("keywords: " + ", ".join(keyword_slice))
    return "; ".join(bits)[:220] or "User is asking Via about products and creator progress."


def extract_via_learning_signals(user_text: str, *, reply_text: str = "") -> dict[str, Any]:
    text = str(user_text or "").strip()
    language = _detect_language(text)
    keywords = _extract_keyword_hits(text)
    focal_keywords = _extract_focal_keywords(text)
    for item in focal_keywords:
        if item not in keywords:
            keywords.append(item)
    interest_tags = _detect_tags(text, INTEREST_PATTERNS)
    product_families = _detect_tags(text, PRODUCT_FAMILY_PATTERNS)
    intent_tags = _detect_tags(text, INTENT_PATTERNS)
    traits: dict[str, Any] = {
        "preferred_language": language,
        "camera_system": _detect_camera_system(text),
        "budget_band": _detect_budget_band(text),
        "creator_stage": _detect_creator_stage(text),
        "interest_tags": interest_tags,
        "product_families": product_families,
        "intent_tags": intent_tags,
    }
    traits = {key: value for key, value in traits.items() if value not in ("", [], None)}
    confidence = min(0.92, 0.34 + 0.08 * len(traits) + 0.03 * min(len(keywords), 6))
    return {
        "language": language,
        "keywords": keywords[:10],
        "traits": traits,
        "summary": _build_summary(language, traits, keywords),
        "reply_excerpt": str(reply_text or "").strip()[:220],
        "confidence": round(confidence, 3),
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def merge_via_persona_profile(existing_profile: dict[str, Any] | None, signals: dict[str, Any]) -> dict[str, Any]:
    profile = dict(existing_profile or {})
    keyword_counts: Counter[str] = Counter()
    for item in profile.get("core_keywords", []) or []:
        if isinstance(item, dict):
            keyword = str(item.get("keyword") or "").strip()
            count = int(item.get("count") or 0)
            if keyword:
                keyword_counts[keyword] += max(1, count)
        elif isinstance(item, str):
            keyword_counts[item.strip()] += 1
    for keyword in signals.get("keywords", []):
        if keyword:
            keyword_counts[str(keyword).strip()] += 1
    user_traits = dict(profile.get("user_traits") or {})
    for key, value in (signals.get("traits") or {}).items():
        if isinstance(value, list):
            merged = list(user_traits.get(key) or [])
            for item in value:
                if item not in merged:
                    merged.append(item)
            user_traits[key] = merged[:8]
        else:
            user_traits[key] = value
    summaries = [str(item).strip() for item in (profile.get("recent_signal_summaries") or []) if str(item).strip()]
    summary = str(signals.get("summary") or "").strip()
    if summary:
        summaries.insert(0, summary[:220])
    profile.update(
        {
            "preferred_language": signals.get("language") or profile.get("preferred_language") or "en",
            "user_traits": user_traits,
            "core_keywords": [
                {"keyword": keyword, "count": count}
                for keyword, count in keyword_counts.most_common(12)
            ],
            "recent_signal_summaries": summaries[:6],
            "conversation_signal_count": int(profile.get("conversation_signal_count") or 0) + 1,
            "last_signal_at": signals.get("captured_at") or "",
            "last_reply_excerpt": str(signals.get("reply_excerpt") or "").strip()[:220],
        }
    )
    return profile


def compact_via_profile_context(profile: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(profile or {})
    return {
        "preferred_language": data.get("preferred_language") or "",
        "user_traits": data.get("user_traits") or {},
        "core_keywords": [
            item.get("keyword") if isinstance(item, dict) else str(item)
            for item in (data.get("core_keywords") or [])[:8]
            if (item.get("keyword") if isinstance(item, dict) else str(item)).strip()
        ],
        "recent_signal_summaries": [str(item).strip()[:180] for item in (data.get("recent_signal_summaries") or [])[:4] if str(item).strip()],
        "conversation_signal_count": int(data.get("conversation_signal_count") or 0),
    }
