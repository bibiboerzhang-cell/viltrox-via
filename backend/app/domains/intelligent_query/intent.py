"""Conservative bilingual intent and topic extraction for Ask & Find v2."""

from __future__ import annotations

import re
from typing import Any

from app.domains.intelligent_query.contracts import SUPPORTED_INTENTS


_VIDEO_TOPIC_PATTERNS = (
    re.compile(r"(?:关于|有关|针对|做过|发布过)\s*[“\"']?(.{2,80}?)[”\"']?\s*(?:相关)?(?:的)?视频", re.I),
    re.compile(r"视频\s*[：:]?\s*[“\"']?([a-z0-9][a-z0-9+._\-/ ]{1,70})[”\"']?", re.I),
    re.compile(r"videos?\s+(?:about|for|mentioning)\s+[“\"']?(.{2,80}?)[”\"']?(?:\?|$)", re.I),
    re.compile(r"(?:how many|which)\s+kols?.*?(?:covered|reviewed|mentioned|posted about)\s+[“\"']?(.{2,80}?)[”\"']?(?:\?|$)", re.I),
)

_MODEL_TOKEN_RE = re.compile(
    r"\b\d{1,3}(?:\.\d+)?\s*mm(?:\s+(?:evo|pro|air|lab|f\s*/?\s*\d(?:\.\d)?|[a-z0-9-]{2,12})){0,3}\b",
    re.I,
)


def normalize_query_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


def resolve_intent(query: str, filters: dict[str, Any]) -> str:
    explicit = str(filters.get("intent") or "").strip()
    if explicit in SUPPORTED_INTENTS:
        return explicit
    q = normalize_query_text(query).lower()

    weekly_tokens = (
        "本周市场",
        "本周 viltrox",
        "这周市场",
        "市场对 viltrox",
        "viltrox 的评价",
        "viltrox评价",
        "weekly market",
        "market feedback",
        "market sentiment",
        "weekly voice",
    )
    if any(token in q for token in weekly_tokens) and ("viltrox" in q or "市场" in q or "market" in q):
        return "market.viltrox.weekly_voice"

    has_kol = "kol" in q or "达人" in q or "创作者" in q
    has_video = "视频" in q or "video" in q or "reviewed" in q or "mentioned" in q
    has_count = any(token in q for token in ("多少", "几个", "数量", "how many", "count"))
    if has_kol and has_video and (has_count or filters.get("topic")):
        return "kol.video_topic.count"

    if "项目" in q or re.search(r"\bprojects?\b", q):
        return "project.search"

    overview_tokens = (
        "目前kol数量",
        "当前kol数量",
        "kol数量",
        "多少kol",
        "多少个kol",
        "多少达人",
        "创作者数量",
        "how many kols",
        "kol count",
        "creator count",
        "pool overview",
    )
    compact = re.sub(r"\s+", "", q)
    if any(token.replace(" ", "") in compact for token in overview_tokens):
        return "kol.pool.overview"
    return "unknown"


def extract_video_topic(query: str, filters: dict[str, Any]) -> str:
    explicit = normalize_query_text(filters.get("topic"))[:120]
    if explicit:
        return explicit
    text = normalize_query_text(query)
    model = _MODEL_TOKEN_RE.search(text)
    if model:
        return model.group(0).strip(" ，。?？:：\"'“”")[:120]
    quoted = re.search(r"[“\"']([^”\"']{2,80})[”\"']", text)
    if quoted:
        return quoted.group(1).strip()[:120]
    for pattern in _VIDEO_TOPIC_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = match.group(1)
        candidate = re.sub(
            r"(?:相关|有关|内容|等|的|please|currently|right now|in total)+\s*$",
            "",
            candidate,
            flags=re.I,
        )
        candidate = candidate.strip(" ，。?？:：\"'“”")
        if len(candidate) >= 2:
            return candidate[:120]
    return ""


def extract_project_keyword(query: str, filters: dict[str, Any]) -> str:
    explicit = normalize_query_text(filters.get("keyword"))[:120]
    if explicit:
        return explicit
    text = normalize_query_text(query)
    text = re.sub(
        r"(?:请|帮我|查找|查询|搜索|找一下|看看|显示|当前|目前|所有|我的|团队|项目|projects?|find|search|show|list|current|my|team)",
        " ",
        text,
        flags=re.I,
    )
    return " ".join(text.split()).strip(" ，。?？:：")[:120]
