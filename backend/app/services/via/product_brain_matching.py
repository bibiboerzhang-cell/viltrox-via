"""Text matching and market-signal helpers for Via product guidance."""
from __future__ import annotations

import re
from typing import Any

from app.services.intelligence.bh_repository import get_latest_bh_products
from app.services.via.product_brain_catalog import ViaProduct

def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def detect_reply_language(text: str) -> str:
    return "zh" if _contains_cjk(text) else "en"


def _detect_reply_language(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> str:
    if _contains_cjk(text):
        return "zh"
    blob = " ".join(
        [
            str(profile_context or ""),
            str((session_state or {}).get("last_user_language") or ""),
            str((session_state or {}).get("preferred_language") or ""),
        ]
    )
    if _contains_cjk(blob) or "zh" in _lower(blob):
        return "zh"
    return "en"


def _lower(text: str) -> str:
    return str(text or "").strip().lower()


def _has_any(text: str, tokens: tuple[str, ...] | list[str]) -> bool:
    lowered = _lower(text)
    return any(token in lowered for token in tokens)


def _budget_query(text: str) -> bool:
    return _has_any(text, ("student", "budget", "cheap", "affordable", "低预算", "预算不高", "学生", "便宜", "性价比", "刀", "美金"))


def _apsc_query(text: str) -> bool:
    return _has_any(text, ("aps-c", "apsc", "crop", "半画幅", "富士", "fujifilm", "x mount", "x-mount"))


def _spec_query(text: str) -> bool:
    return _has_any(text, ("spec", "specs", "parameter", "parameters", "参数", "规格", "配置", "多少光圈", "卡口", "mount"))


def _link_query(text: str) -> bool:
    return _has_any(text, ("link", "url", "链接", "官网", "购买", "site", "store"))


def _comparison_query(text: str) -> bool:
    return _has_any(text, ("compare", "vs", "versus", "区别", "对比", "哪个好", "怎么选", "差别", "difference"))


def _family_guide_query(text: str, family: str | None) -> bool:
    if not family:
        return False
    lowered = _lower(text)
    if _comparison_query(lowered) or _spec_query(lowered) or _link_query(lowered) or _specific_product_prompt(lowered):
        return False
    guide_terms = (
        "系列",
        "产品线",
        "系列里",
        "series",
        "line",
        "lineup",
        "catalog",
        "路线",
        "讲讲",
        "介绍",
        "什么意思",
        "是什么",
        "有哪些",
        "都有谁",
        "怎么理解",
        "梳理",
    )
    return any(term in lowered for term in guide_terms)


def _specific_product_prompt(text: str) -> bool:
    lowered = _lower(text)
    if re.search(r"(30-300|42-420|14mm|20mm|27mm|28mm|35mm|40mm|50mm|55mm|56mm|85mm|135mm)", lowered):
        return True
    if re.search(r"(?<!\\d)(14|20|27|28|35|40|42|50|55|56|85|135)(?!\\d)", lowered):
        return True
    return _has_any(
        lowered,
        (
            "chip lens",
            "fe ii",
            "z1",
            "z2",
            "30-300",
            "42-420",
            "85 evo",
            "35 evo",
            "55 evo",
            "50 air",
            "40 air",
            "20 air",
            "35 lab",
            "135 lab",
            "35 pro",
            "50 pro",
            "85 pro",
        ),
    )


def _scenario_label(user_text: str, lang: str) -> str:
    lowered = _lower(user_text)
    if _has_any(lowered, ("portrait", "人像", "beauty", "妆造")):
        return "人像和访谈" if lang == "zh" else "portraits and talking-head work"
    if _has_any(lowered, ("street", "街拍", "travel", "旅行", "daily", "日常", "campus", "学生")):
        return "轻便随拍和校园日常" if lang == "zh" else "light everyday carry and campus creator work"
    if _has_any(lowered, ("cinema", "电影", "filmmaking", "narrative", "commercial", "剧情", "广告")):
        return "更偏电影和商业拍摄" if lang == "zh" else "cinema and commercial production"
    if _has_any(lowered, ("vlog", "travel", "旅行", "gimbal", "稳定器")):
        return "vlog、旅行和稳定器镜头" if lang == "zh" else "vlog, travel, and gimbal work"
    if _has_any(lowered, ("product", "开箱", "detail", "细节")):
        return "产品和细节拍摄" if lang == "zh" else "product and detail work"
    return "你现在这个拍摄场景" if lang == "zh" else "your current shooting scenario"


def _normalize_market_text(text: str) -> str:
    lowered = _lower(text)
    lowered = lowered.replace("full frame", "ff").replace("full-frame", "ff")
    lowered = lowered.replace("sony e", "sony").replace("nikon z", "nikon").replace("fujifilm x", "fuji")
    return re.sub(r"[^a-z0-9\+\./-]+", " ", lowered).strip()


def _product_match_tokens(product: ViaProduct) -> set[str]:
    ignored = {
        "af", "lens", "series", "full", "frame", "ff", "apo", "mini", "cinema", "workflow",
        "viltrox", "vintage", "lighting", "accessory", "for", "the",
    }
    tokens: set[str] = set()
    for source in (product.label, product.series, *product.aliases):
        normalized = _normalize_market_text(source)
        if not normalized:
            continue
        if " " in normalized and len(normalized) >= 5:
            tokens.add(normalized)
        for part in normalized.split():
            if part in ignored:
                continue
            if len(part) >= 3 or part.endswith("mm") or part.startswith("f"):
                tokens.add(part)
    return tokens


def _bh_score(product: ViaProduct, row: dict[str, Any]) -> int:
    title = _normalize_market_text(str(row.get("title") or ""))
    if not title:
        return 0
    score = 0
    full_label = _normalize_market_text(product.label)
    if full_label and full_label in title:
        score += 10
    for token in _product_match_tokens(product):
        if token and token in title:
            score += 2
    if product.series and product.series.lower() in title:
        score += 2
    if any(mount.lower().split()[0] in title for mount in product.mounts if mount and mount[0].isalpha()):
        score += 1
    return score


def _bh_market_rows(products: list[ViaProduct]) -> dict[str, dict[str, Any]]:
    rows = get_latest_bh_products(limit=120)
    matched: dict[str, dict[str, Any]] = {}
    for product in products:
        best_row: dict[str, Any] | None = None
        best_score = 0
        for row in rows:
            score = _bh_score(product, row)
            if score > best_score:
                best_score = score
                best_row = row
        if best_row and best_score >= 4:
            matched[product.label] = best_row
    return matched


def _market_line(product: ViaProduct, market_row: dict[str, Any] | None, lang: str) -> str:
    if not market_row:
        return (
            f"官方商城入口是 {product.official_url}，如果你想要我继续比一比，我会沿着 {product.series} 这条线帮你挑。"
            if lang == "zh"
            else f"The official store entry is {product.official_url}. If you want, I can keep comparing inside the {product.series} lane."
        )
    price = float(market_row.get("price") or 0)
    rating = float(market_row.get("rating") or 0)
    review_count = int(market_row.get("review_count") or 0)
    in_stock = bool(market_row.get("in_stock"))
    stock_word = "有现货" if in_stock else "暂时缺货"
    stock_word_en = "in stock" if in_stock else "currently out of stock"
    if lang == "zh":
        bits = [f"B&H 观察里这支目前 {stock_word}"]
        if price > 0:
            bits.append(f"价格约 ${price:.0f}")
        if rating > 0:
            bits.append(f"评分 {rating:.1f}")
        if review_count > 0:
            bits.append(f"{review_count} 条评论")
        bits.append(f"官方商城：{product.official_url}")
        return "，".join(bits) + "。"
    bits = [f"B&H currently shows it {stock_word_en}"]
    if price > 0:
        bits.append(f"around ${price:.0f}")
    if rating > 0:
        bits.append(f"rated {rating:.1f}")
    if review_count > 0:
        bits.append(f"with {review_count} reviews")
    bits.append(f"official store: {product.official_url}")
    return ", ".join(bits) + "."


def _compare_axis(lead: ViaProduct, alt: ViaProduct, lang: str) -> str:
    if lang == "zh":
        return (
            f"{lead.label} 更偏 {lead.use_case_zh}，{alt.label} 更偏 {alt.use_case_zh}。"
            if lead.label != alt.label
            else f"{lead.label} 更像 {lead.use_case_zh} 这条路线。"
        )
    return (
        f"{lead.label} leans toward {lead.use_case}, while {alt.label} leans toward {alt.use_case}."
        if lead.label != alt.label
        else f"{lead.label} is the stronger fit for {lead.use_case}."
    )


