"""Natural-language KOL search payload builder."""
from __future__ import annotations

import re
from typing import Any

from app.domains.kol.claim_listing import list_kols
from app.domains.kol import history_match

from app.domains.kol.payload_utils import _clamp_score, _float, _int, _json_loads


_PLATFORM_ALIASES = {
    "instagram": ("instagram", "ig", "ins"),
    "youtube": ("youtube", "yt", "youtuber"),
    "tiktok": ("tiktok", "tt", "抖音"),
    "facebook": ("facebook", "fb"),
    "x": ("twitter", "x.com", "x "),
    "reddit": ("reddit",),
}

_REGION_ALIASES = {
    "US": ("美国", "美区", "usa", "united states", " u.s.", " us "),
    "DE": ("德国", "germany", "deutschland", " de "),
    "GB": ("英国", "uk", "united kingdom", " gb "),
    "JP": ("日本", "japan", " jp "),
    "KR": ("韩国", "korea", " kr "),
    "CA": ("加拿大", "canada", " ca "),
    "AU": ("澳大利亚", "australia", " au "),
    "MY": ("马来西亚", "malaysia", " my "),
}

_SEARCH_STOP_WORDS = {
    "kol",
    "kols",
    "红人",
    "达人",
    "博主",
    "测评人",
    "找",
    "寻找",
    "搜索",
    "推荐",
    "合作",
    "可合作",
    "中腰部",
    "头部",
    "长尾",
    "有联系方式",
    "联系方式",
    "无风险",
    "低风险",
    "安全",
}

_DIRECTION_KEYWORDS = (
    "35mm",
    "56mm",
    "street",
    "portrait",
    "photography",
    "review",
    "tutorial",
    "comparison",
    "cinematic",
    "vlog",
    "sony",
    "fuji",
    "nikon",
    "sigma",
    "tamron",
    "街拍",
    "人像",
    "摄影",
    "测评",
    "教程",
    "对比",
    "中腰部",
    "竞品",
    "镜头",
    "相机",
    "视频",
)


def _tokenize(text: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9\u4e00-\u9fff]+", str(text or "").lower()) if len(part) >= 2}


def _parse_natural_query(query: str, platform_hint: str = "") -> dict[str, Any]:
    q = f" {str(query or '').lower()} "
    platform = ""
    for candidate, aliases in _PLATFORM_ALIASES.items():
        if any(alias in q for alias in aliases):
            platform = candidate
            break
    if not platform and platform_hint and platform_hint != "all":
        platform = str(platform_hint or "").lower()

    country = ""
    for code, aliases in _REGION_ALIASES.items():
        if any(alias in q for alias in aliases):
            country = code
            break

    level = ""
    if any(item in q for item in ("头部", "top", "mega")):
        level = "top"
    elif any(item in q for item in ("中腰部", "mid", "micro", "中部", "腰部")):
        level = "mid"
    elif any(item in q for item in ("长尾", "tail", "nano")):
        level = "tail"

    keyword_set: set[str] = set()
    for keyword in _DIRECTION_KEYWORDS:
        if keyword.lower() in q:
            keyword_set.add(keyword.lower())
    for token in _tokenize(q):
        if any(stop and stop in token for stop in _SEARCH_STOP_WORDS):
            continue
        if any(alias.strip() and alias.strip() in token for aliases in _REGION_ALIASES.values() for alias in aliases):
            continue
        if any(alias.strip() and alias.strip() in token for aliases in _PLATFORM_ALIASES.values() for alias in aliases):
            continue
        if token.isdigit():
            continue
        keyword_set.add(token)

    return {
        "platform": platform,
        "country": country,
        "level": level,
        "requires_contact": any(item in q for item in ("联系方式", "email", "邮箱", "contact", "可联系")),
        "requires_low_risk": any(item in q for item in ("无风险", "低风险", "安全", "clean risk", "low risk")),
        "requires_collaboration": any(item in q for item in ("合作过", "已合作", "之前合作", "历史合作", "合作历史", "roi", "复用")),
        "keywords": sorted(keyword_set),
    }


def _row_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            row.get("channel_name"),
            row.get("media_name"),
            row.get("owner_name"),
            row.get("handle"),
            row.get("channel_url"),
            row.get("niche"),
            row.get("primary_category"),
            row.get("promoted_product"),
            row.get("channel_tags"),
            row.get("country"),
            row.get("country_code"),
            row.get("platform"),
            row.get("contact_email"),
            row.get("contact_links_json"),
        )
    ).lower()


def _row_has_contact(row: dict[str, Any]) -> bool:
    if str(row.get("contact_email") or "").strip() or str(row.get("contact_phone") or "").strip():
        return True
    links = _json_loads(row.get("contact_links_json"), [])
    return isinstance(links, list) and any(isinstance(item, dict) and (item.get("value") or item.get("url")) for item in links)


def _row_level(followers: int) -> str:
    if followers >= 500000:
        return "top"
    if followers >= 50000:
        return "mid"
    return "tail"


def _natural_match_score(row: dict[str, Any], parsed: dict[str, Any]) -> tuple[int, list[str]]:
    text = _row_text(row)
    followers = _int(row.get("snapshot_follower_count"), _int(row.get("follower_count")))
    score = _clamp_score(row.get("account_score") or row.get("score") or row.get("product_fit") or row.get("audience_fit") or 42)
    reasons: list[str] = []

    keywords = [str(item).lower() for item in parsed.get("keywords") or []]
    keyword_hits = [keyword for keyword in keywords if keyword and keyword in text]
    if keyword_hits:
        score += min(24, len(keyword_hits) * 8)
        reasons.append(f"关键词命中：{', '.join(keyword_hits[:4])}")

    platform = str(parsed.get("platform") or "").lower()
    if platform and platform == str(row.get("platform") or "").lower():
        score += 10
        reasons.append(f"平台匹配 {platform}")

    country = str(parsed.get("country") or "").upper()
    row_country = str(row.get("country") or row.get("country_code") or "").upper()
    if country and (country == row_country or country.lower() in text):
        score += 8
        reasons.append(f"地区匹配 {country}")

    level = str(parsed.get("level") or "")
    if level and followers and _row_level(followers) == level:
        score += 8
        reasons.append(f"量级匹配 {level}")

    if parsed.get("requires_contact") and _row_has_contact(row):
        score += 8
        reasons.append("已有联系方式")

    if parsed.get("requires_collaboration") and (_int(row.get("project_count")) or _float(row.get("revenue_cents"))):
        score += 6
        reasons.append("已有合作或收益记录")

    risk = str(row.get("risk_level") or row.get("risk_label") or "").lower()
    if parsed.get("requires_low_risk") and risk in {"", "low", "none", "clean"}:
        score += 6
        reasons.append("暂无高风险信号")

    if row.get("snapshot_scanned_at"):
        score += 4
        reasons.append("有账号快照")

    return _clamp_score(score), reasons


def _natural_search_payload(body: dict[str, Any], staff: dict[str, Any] | None = None) -> dict[str, Any]:
    query = str(body.get("query") or body.get("q") or "").strip()
    limit = max(1, min(200, _int(body.get("limit"), 100)))
    parsed = _parse_natural_query(query, str(body.get("platform") or ""))
    platform = str(parsed.get("platform") or "")
    raw_rows = list_kols(search="", platform=platform, limit=500, staff=staff).get("kols") or []
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        row = dict(raw)
        text = _row_text(row)
        followers = _int(row.get("snapshot_follower_count"), _int(row.get("follower_count")))
        has_structured_filter = any(
            parsed.get(key)
            for key in ("platform", "country", "level", "requires_contact", "requires_low_risk", "requires_collaboration")
        )
        if parsed.get("requires_contact") and not _row_has_contact(row):
            continue
        level = str(parsed.get("level") or "")
        if level and followers and _row_level(followers) != level:
            continue
        keywords = [str(keyword).lower() for keyword in parsed.get("keywords") or []]
        if keywords and not has_structured_filter and not any(keyword in text for keyword in keywords):
            continue
        country = str(parsed.get("country") or "").upper()
        row_country = str(row.get("country") or row.get("country_code") or "").upper()
        if country and row_country and country != row_country and country.lower() not in text:
            continue
        score, reasons = _natural_match_score(row, parsed)
        row["natural_match_score"] = score
        row["natural_match_reasons"] = reasons
        row["score"] = max(_clamp_score(row.get("score") or row.get("account_score") or row.get("product_fit")), score)
        rows.append(row)
    pool_rows = history_match.search_pool_for_natural(query, parsed, limit=limit)
    seen_keys = {
        (
            str(row.get("platform") or "").lower(),
            history_match.normalize_history_handle(row.get("handle") or row.get("channel_name") or ""),
        )
        for row in rows
    }
    for row in pool_rows:
        key = (
            str(row.get("platform") or "").lower(),
            history_match.normalize_history_handle(row.get("handle") or row.get("channel_name") or ""),
        )
        if key in seen_keys:
            continue
        rows.append(row)
        seen_keys.add(key)
    rows.sort(key=lambda item: (_int(item.get("natural_match_score")), _int(item.get("snapshot_follower_count"), _int(item.get("follower_count")))), reverse=True)
    notes = ["规则解析版，复用现有 kols / snapshots / reports / vkpi_kol_pool 字段；未新增后端表。"]
    if not rows:
        notes.append("没有命中时不会伪造推荐，请先补候选池或放宽关键词。")
    return {
        "query": query,
        "parsed": parsed,
        "items": rows[:limit],
        "method": "local_natural_search_v1_existing_kols",
        "degraded": True,
        "notes": notes,
    }
