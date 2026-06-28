"""Region/language localization helpers for KOL Pool smart-search discovery.

Behaviour-preserving extraction from profile_discovery.py (function bodies moved
verbatim; profile_discovery re-exports for unchanged call points). Never touches
V6 Fit scoring fields.
"""
from __future__ import annotations

import logging

from app.domains.kol.discovery_filters import _text  # noqa: F401

logger = logging.getLogger(__name__)


# ── 区域语言本地化(用户令):选了目标市场就按该区语言搜平台,捞本地达人(本地达人频道/标题是本地语言)。──
# value=(relevanceLanguage ISO-639-1, regionCode);英语区/未列/空 → ("en", market)。CN/HK/TW 为排除域不作目标。
MARKET_LANGUAGE: dict[str, tuple[str, str]] = {
    "JP": ("ja", "JP"), "KR": ("ko", "KR"), "DE": ("de", "DE"), "FR": ("fr", "FR"),
    "ES": ("es", "ES"), "MX": ("es", "MX"), "IT": ("it", "IT"), "BR": ("pt", "BR"),
    "PT": ("pt", "PT"), "RU": ("ru", "RU"), "TH": ("th", "TH"), "VN": ("vi", "VN"),
    "ID": ("id", "ID"), "TR": ("tr", "TR"), "PL": ("pl", "PL"), "NL": ("nl", "NL"),
    "SA": ("ar", "SA"), "AE": ("ar", "AE"),
}
_LANG_DISPLAY = {
    "en": "English",
    "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "tr": "Turkish", "pl": "Polish", "nl": "Dutch", "ar": "Arabic",
}
_LOCALIZE_CACHE: dict[tuple[str, str], str] = {}


def _has_cjk(text: str) -> bool:
    """含中日韩统一表意文字(中文为主)→ 需翻译成目标语言再搜平台。"""
    return any("一" <= ch <= "鿿" for ch in text)


def _market_to_language(market: str) -> tuple[str, str]:
    """市场码 → (relevanceLanguage, regionCode);英语区/未列 → ('en', market)。"""
    m = _text(market).upper()
    return MARKET_LANGUAGE.get(m, ("en", m))


def _localize_search_terms(en_query: str, language: str) -> str:
    """英文 creator 检索词 → 目标语言(保持可搜关键词)。走 llm_gateway 预算闸;同 (query,lang) 进程内
    缓存不重复烧;翻译失败/空 → 回退英文(不阻断)。language 为空/en → 原样返回。"""
    q = _text(en_query)
    lang = _text(language).lower()
    if not q:
        return q
    # 英文/全球市场:query 已是拉丁/英文 → 原样;但若是中文(persona-KB 计划的中文 search_query)→
    # 翻成英文,贯彻「中文输入→英文搜索」(否则中文词又被原样拿去搜平台、捞中文圈)。
    if lang in ("", "en"):
        if not _has_cjk(q):
            return q
        lang = "en"
    key = (q, lang)
    if key in _LOCALIZE_CACHE:
        return _LOCALIZE_CACHE[key]
    try:
        from app.platform import llm_gateway

        lang_name = _LANG_DISPLAY.get(lang, lang)
        prompt = (
            f"Translate these influencer/creator search keywords into {lang_name} for searching "
            f"YouTube/Instagram/TikTok. Return ONLY the translated keywords, space-separated, "
            f"no explanation, no quotes:\n\n{q}"
        )
        resp = llm_gateway.invoke(
            prompt=prompt,
            purpose="vkpi_discovery_localize",
            preferred_provider="openai",
            max_output_tokens=120,
        )
        if str(resp.get("status") or "") == "success":
            # QA P3:去掉 LLM 可能误带的首尾引号(prompt 已要求 no quotes,这里稳健兜底)。
            text = _text(resp.get("text")).strip().strip('"').strip("'").strip()
            if text:
                _LOCALIZE_CACHE[key] = text  # QA P2:仅成功才缓存;失败不缓存,允许 LLM 恢复后重试。
                return text
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    return q  # 翻译失败/空 → 回退英文(不缓存)
