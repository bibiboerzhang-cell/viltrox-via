"""K1 · 统一智能输入分类器。

一个输入框处理四类:视频 URL / 账号 URL / 产品需求 / 自然语言问题。
纯正则与启发式判定,零 LLM、零 DB —— 只读字符串,给出 kind/route/parsed/confidence。
红线:不触 viltrox_fit_score / rule_v0;模块只做"分类与路由建议"本职。
"""
from __future__ import annotations

import re
from typing import Any


CLASSIFIER_VERSION = "unified-input-v0.1"

# 输入类别
KIND_VIDEO_URL = "video_url"
KIND_PROFILE_URL = "profile_url"
KIND_PRODUCT_QUERY = "product_query"
KIND_NL_QUESTION = "nl_question"

# 建议下一步端点(供前端按 route 跳转;此处只给字符串不调用)
_ROUTE_VIDEO = "/api/admin/vkpi/kol-smart-search?mode=account_deep"
_ROUTE_PROFILE = "/api/admin/vkpi/kol-smart-search"
_ROUTE_PRODUCT = "/api/admin/vkpi/agents/recall"
_ROUTE_NL = "/api/admin/vkpi/agents/recall"

# --- 视频 URL 指纹(命中其一即视频) ---
_VIDEO_PATTERNS = (
    re.compile(r"youtube\.com/watch", re.I),
    re.compile(r"youtu\.be/", re.I),
    re.compile(r"youtube\.com/shorts/", re.I),
    re.compile(r"tiktok\.com/[^/\s]+/video/", re.I),
    re.compile(r"instagram\.com/(?:reel|p|tv)/", re.I),
)

# --- 账号 / 频道 URL 指纹 ---
_PROFILE_PATTERNS = (
    re.compile(r"youtube\.com/@", re.I),
    re.compile(r"youtube\.com/(?:channel|c|user)/", re.I),
    re.compile(r"tiktok\.com/@", re.I),
    # instagram.com/handle 或 instagram.com/handle/ (单段,非 reel/p/tv)
    re.compile(r"instagram\.com/[^/\s?#]+/?(?:[?#].*)?$", re.I),
)

# --- URL 抽取 ---
_URL_RE = re.compile(r"https?://[^\s]+", re.I)
_BARE_DOMAIN_RE = re.compile(
    r"(?:^|\s)((?:www\.)?(?:youtube\.com|youtu\.be|tiktok\.com|instagram\.com)/[^\s]+)",
    re.I,
)

# --- 自然语言问句信号(中英) ---
_NL_KEYWORDS = (
    "怎么样",
    "多少",
    "为什么",
    "趋势",
    "对比",
    "值不值",
    "该不该",
    "如何",
    "roi",
    "how",
    "what",
    "why",
    "which",
    "trend",
    "compare",
    "vs ",
    " vs",
)


def _extract_url(text: str) -> str | None:
    """从文本里抽第一个 URL(优先 http,其次裸社媒域名)。"""
    m = _URL_RE.search(text)
    if m:
        return m.group(0).rstrip(").,;】>\"'")
    m2 = _BARE_DOMAIN_RE.search(text)
    if m2:
        return m2.group(1).rstrip(").,;】>\"'")
    return None


def _looks_like_url(text: str) -> bool:
    """是否含 http 链接或裸社媒域名。"""
    return bool(_URL_RE.search(text) or _BARE_DOMAIN_RE.search(text))


def _is_video_url(candidate: str) -> bool:
    """命中视频指纹与否。"""
    return any(p.search(candidate) for p in _VIDEO_PATTERNS)


def _is_profile_url(candidate: str) -> bool:
    """命中账号/频道指纹与否。"""
    return any(p.search(candidate) for p in _PROFILE_PATTERNS)


def _is_nl_question(text: str) -> bool:
    """短问句启发:含问号或自然语言关键词。"""
    low = text.lower()
    if "?" in text or "?" in text:  # 全角问号在中文表述里允许出现于"输入文本",非 SQL
        return True
    return any(kw in low for kw in _NL_KEYWORDS)


def _keywords(text: str) -> list[str]:
    """抽取产品/检索关键词(去标点切词,过滤超短噪声)。"""
    tokens = re.split(r"[\s,，、；;/|]+", text.strip())
    out: list[str] = []
    for tok in tokens:
        tok = tok.strip().strip(".。!！?")
        if len(tok) >= 2:
            out.append(tok)
    return out[:12]


def classify_input(text: str) -> dict[str, Any]:
    """统一分类入口:把任意输入归到 4 类之一并给出路由建议。

    返回:{kind, route, parsed, confidence, version}。
    判定纯启发式:URL 指纹 → 视频/账号;http 兜底账号;问句信号 → NL;否则产品需求。
    """
    raw = "" if text is None else str(text)
    stripped = raw.strip()

    if not stripped:
        return {
            "kind": KIND_PRODUCT_QUERY,
            "route": _ROUTE_PRODUCT,
            "parsed": {"keywords": []},
            "confidence": 0.0,
            "version": CLASSIFIER_VERSION,
        }

    url = _extract_url(stripped)

    # 1) URL 优先:视频 vs 账号
    if url is not None or _looks_like_url(stripped):
        candidate = url or stripped
        if _is_video_url(candidate):
            return {
                "kind": KIND_VIDEO_URL,
                "route": _ROUTE_VIDEO,
                "parsed": {"url": candidate},
                "confidence": 0.95,
                "version": CLASSIFIER_VERSION,
            }
        if _is_profile_url(candidate):
            return {
                "kind": KIND_PROFILE_URL,
                "route": _ROUTE_PROFILE,
                "parsed": {"url": candidate},
                "confidence": 0.9,
                "version": CLASSIFIER_VERSION,
            }
        # http 开头但非已知社媒形态 → 账号兜底(best-effort)
        if candidate.lower().startswith("http"):
            return {
                "kind": KIND_PROFILE_URL,
                "route": _ROUTE_PROFILE,
                "parsed": {"url": candidate},
                "confidence": 0.5,
                "version": CLASSIFIER_VERSION,
            }

    # 2) 自然语言问句
    if _is_nl_question(stripped):
        return {
            "kind": KIND_NL_QUESTION,
            "route": _ROUTE_NL,
            "parsed": {"question": stripped, "keywords": _keywords(stripped)},
            "confidence": 0.7,
            "version": CLASSIFIER_VERSION,
        }

    # 3) 兜底:产品需求 / 检索词
    return {
        "kind": KIND_PRODUCT_QUERY,
        "route": _ROUTE_PRODUCT,
        "parsed": {"keywords": _keywords(stripped)},
        "confidence": 0.6,
        "version": CLASSIFIER_VERSION,
    }
