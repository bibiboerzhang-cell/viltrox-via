"""
services/via/product_brain.py — deterministic Viltrox-first product guidance for Via
"""
from __future__ import annotations

import re
from typing import Any

from app.core.constants import PRODUCT_RULES
from app.core.logging import get_logger
from app.services.via.external_viltrox_assets import handle_external_competitor_query
from app.services.via.product_brain_catalog import (
    CATALOG,
    FAMILY_GUIDES,
    FAMILY_TO_SERIES,
    GENERIC_CATALOG,
    MOUNT_LIBRARY,
    SERIES_HIGHLIGHTS,
    SERIES_OFFICIAL_URLS,
    SHORT_MOUNT_TOKENS,
    STORE_URL,
    ViaProduct,
    _official_search,
)
from app.services.via.product_brain_matching import (
    _apsc_query,
    _bh_market_rows,
    _budget_query,
    _comparison_query,
    _compare_axis,
    _contains_cjk,
    _detect_reply_language,
    _family_guide_query,
    _has_any,
    _link_query,
    _lower,
    _market_line,
    _scenario_label,
    _spec_query,
    detect_reply_language,
)

logger = get_logger(__name__)

def _profile_blob(profile_context: str | None, session_state: dict[str, Any] | None) -> str:
    bits = [str(profile_context or "")]
    if session_state:
        bits.extend(
            [
                " ".join(session_state.get("last_product_labels") or []),
                str(session_state.get("last_mount_hint") or ""),
                str(session_state.get("last_budget_hint") or ""),
                str(session_state.get("last_product_series") or ""),
                str(session_state.get("last_product_summary") or ""),
            ]
        )
    return _lower(" ".join(bit for bit in bits if bit))


def _extract_budget(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> int | None:
    lowered = _lower(text)
    patterns = (
        r"(?:预算|budget|under|below|around|about|就|only|学生)\s*\$?(\d{2,4})",
        r"\$?(\d{2,4})\s*(?:usd|刀|美金|dollars?)",
    )
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            try:
                value = int(match.group(1))
                if 50 <= value <= 5000:
                    return value
            except Exception:
                logger.warning(
                    "via.product_brain.budget_match_parse_failed",
                    extra={"text": text[:120]},
                    exc_info=True,
                )
    session_value = None
    if session_state:
        try:
            session_value = int(session_state.get("last_budget_hint") or 0)
        except Exception:
            session_value = None
    if session_value:
        return session_value
    blob = _profile_blob(profile_context, session_state)
    if _budget_query(blob):
        return 300
    return None


def _family_key(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> str | None:
    lowered = _lower(text)
    blob = _profile_blob(profile_context, session_state)
    if any(token in lowered for token in ("饼干头", "pancake", "chip lens", "chip", "薄饼")):
        return "pancake"
    if any(token in lowered for token in ("air", "airy", "轻便", "air系", "air 系", "air ff", "air aps-c", "travel prime")) or ("air" in blob and _has_any(lowered or blob, ("50", "40", "20", "air"))):
        return "air"
    if any(token in lowered for token in ("evo", "evo apo", "evo 系", "evo 系列")):
        return "evo"
    if any(token in lowered for token in ("lab", "旗舰", "lab line")):
        return "lab"
    if any(token in lowered for token in ("pro", "pro ff", "pro aps-c", "pro 系", "pro 系列")):
        return "pro"
    if any(token in lowered for token in ("epic", "anamorphic", "1.33x", "变形", "电影镜头", "blue streak", "silver flare", "宽银幕", "pl mount", "pl卡口", "maestro", "memento", "squeeze")):
        return "epic"
    if any(token in lowered for token in ("luna", "30-300", "42-420", "cine zoom", "broadcast", "lpl", "10x zoom", "large format zoom", "体育转播")):
        return "luna"
    if any(token in lowered for token in ("z1", "z2", "flash", "strobe", "speedlite", "闪光灯", "灯", "vintage z", "ttl", "hotshoe flash")):
        return "lighting"
    return None


def _series_rule_matches(family: str | None, text: str, limit: int = 4) -> list[dict[str, str]]:
    series = FAMILY_TO_SERIES.get(str(family or ""))
    if not series:
        return []
    lowered = _lower(text)
    matches: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in PRODUCT_RULES:
        item_series = str(item.get("series") or "").strip().upper()
        label = str(item.get("label") or "").strip()
        if item_series != series or not label:
            continue
        keywords = [str(keyword).strip().lower() for keyword in item.get("keywords") or [] if str(keyword).strip()]
        hit = bool(lowered) and (label.lower() in lowered or any(keyword in lowered for keyword in keywords[:16]))
        if hit and label not in seen:
            matches.append({"label": label, "series": series, "official_url": _official_search(label)})
            seen.add(label)
        if len(matches) >= limit:
            return matches
    for label in SERIES_HIGHLIGHTS.get(series, ()):
        if label in seen:
            continue
        matches.append({"label": label, "series": series, "official_url": _official_search(label)})
        if len(matches) >= limit:
            break
    return matches


def _series_selection_line(family: str | None, user_text: str, lang: str, *, limit: int = 4) -> str:
    matches = _series_rule_matches(family, user_text, limit=limit)
    series = FAMILY_TO_SERIES.get(str(family or ""))
    if not matches or not series:
        return ""
    labels = "、".join(item["label"] for item in matches) if lang == "zh" else ", ".join(item["label"] for item in matches)
    url = SERIES_OFFICIAL_URLS.get(series, STORE_URL)
    if lang == "zh":
        return f"这条线我会先看 {labels}。系列官方入口：{url}"
    return f"I would start with {labels}. Official series entry: {url}"


def _series_rule_guide_reply(family: str, lang: str, user_text: str, mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    guide = FAMILY_GUIDES[family]
    detail_line = _series_selection_line(family, user_text, lang, limit=6)
    text = (guide["zh_text"] if lang == "zh" else guide["en_text"]).strip()
    if detail_line:
        text = f"{text} {detail_line}".strip()
    return {
        "title": guide["zh_title"] if lang == "zh" else guide["en_title"],
        "text": text,
        "quick_actions": guide["quick_actions_zh"] if lang == "zh" else guide["quick_actions_en"],
        "lock_ai_override": False,
        "product_subintent": "family_guide",
        "behavior_mode": _product_behavior_mode("family_guide"),
        "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
    }


def _series_rule_links_reply(family: str, lang: str, user_text: str, mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    matches = _series_rule_matches(family, user_text, limit=3)
    series = FAMILY_TO_SERIES.get(family, "")
    series_url = SERIES_OFFICIAL_URLS.get(series, STORE_URL)
    listing = " / ".join(f"{item['label']}：{item['official_url']}" for item in matches) if lang == "zh" else " / ".join(f"{item['label']}: {item['official_url']}" for item in matches)
    text = (
        f"我先只给你唯卓仕官方入口。你可以先看：{listing}。系列总入口：{series_url}。"
        if lang == "zh"
        else f"I will keep this Viltrox-first. Start here: {listing}. Series entry: {series_url}."
    )
    return {
        "title": "官方链接" if lang == "zh" else "Official links",
        "text": text,
        "quick_actions": ["讲参数", "对比一下", "给我官网"] if lang == "zh" else ["Show specs", "Compare them", "Series page"],
        "lock_ai_override": True,
        "product_subintent": "links",
        "behavior_mode": _product_behavior_mode("links"),
        "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
    }


def _series_rule_specs_reply(family: str, lang: str, user_text: str, mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    detail_line = _series_selection_line(family, user_text, lang, limit=4)
    series = FAMILY_TO_SERIES.get(family, "")
    if lang == "zh":
        note = (
            "EPIC 这条线主打 1.33X 变形、PL 电影工作流，65mm 是 Macro 例外。"
            if series == "EPIC"
            else "LUNA 这条线是超长焦 10x 电影变焦，偏体育、纪录和广电工作流。"
        )
        text = f"我先按唯卓仕官方系列讲：{note} {detail_line}".strip()
    else:
        note = (
            "EPIC is the 1.33X anamorphic PL-mount cinema lane, with the 65mm as the Macro outlier."
            if series == "EPIC"
            else "LUNA is the long-reach 10x cinema zoom lane for sports, documentary, and broadcast-scale workflows."
        )
        text = f"I will frame it from the official Viltrox family first: {note} {detail_line}".strip()
    return {
        "title": "唯卓仕参数" if lang == "zh" else "Viltrox specs",
        "text": text,
        "quick_actions": ["给我链接", "对比一下", "给我官网"] if lang == "zh" else ["Give links", "Compare them", "Series page"],
        "lock_ai_override": True,
        "product_subintent": "specs",
        "behavior_mode": _product_behavior_mode("specs"),
        "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
    }


def _series_rule_comparison_reply(family: str, lang: str, user_text: str, mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    matches = _series_rule_matches(family, user_text, limit=2)
    lead = matches[0]
    alt = matches[1] if len(matches) > 1 else matches[0]
    lead_focal = int(re.search(r"(\d{2,3})mm", lead["label"]).group(1)) if re.search(r"(\d{2,3})mm", lead["label"]) else 0
    alt_focal = int(re.search(r"(\d{2,3})mm", alt["label"]).group(1)) if re.search(r"(\d{2,3})mm", alt["label"]) else 0
    series_url = SERIES_OFFICIAL_URLS.get(FAMILY_TO_SERIES.get(family, ""), STORE_URL)
    if lang == "zh":
        lead_desc = "更偏广角建立和环境叙事" if lead_focal and alt_focal and lead_focal < alt_focal else "更偏长焦压缩和主体特写"
        alt_desc = "更偏广角建立和环境叙事" if lead_focal and alt_focal and alt_focal < lead_focal else "更偏长焦压缩和主体特写"
        text = (
            f"我会这样看：{lead['label']} {lead_desc}，{alt['label']} {alt_desc}。"
            f" 它们都属于唯卓仕 {FAMILY_TO_SERIES.get(family, family).upper()} 系列，系列入口：{series_url}。"
        )
    else:
        lead_desc = "leans wider for establishing and environmental storytelling" if lead_focal and alt_focal and lead_focal < alt_focal else "leans longer for compression and tighter hero shots"
        alt_desc = "leans wider for establishing and environmental storytelling" if lead_focal and alt_focal and alt_focal < lead_focal else "leans longer for compression and tighter hero shots"
        text = (
            f"I would split it like this: {lead['label']} {lead_desc}, while {alt['label']} {alt_desc}. "
            f"Both sit inside the Viltrox {FAMILY_TO_SERIES.get(family, family).upper()} family. Series entry: {series_url}."
        )
    return {
        "title": "怎么选" if lang == "zh" else "How I would choose",
        "text": text,
        "quick_actions": ["给我链接", "讲参数", "给我官网"] if lang == "zh" else ["Give links", "Show specs", "Series page"],
        "lock_ai_override": False,
        "product_subintent": "comparison",
        "behavior_mode": _product_behavior_mode("comparison"),
        "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
    }


def _detect_mount(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> str | None:
    lowered = _lower(text)
    blob = _profile_blob(profile_context, session_state)
    short_mount = SHORT_MOUNT_TOKENS.get(lowered)
    if short_mount:
        return short_mount
    for key, info in MOUNT_LIBRARY.items():
        if any(token in lowered for token in info["tokens"]):
            return key
    if session_state and session_state.get("last_mount_hint"):
        return str(session_state["last_mount_hint"])
    if short_mount:
        return short_mount
    for key, info in MOUNT_LIBRARY.items():
        if any(token in blob for token in info["tokens"]):
            return key
    return None


def _product_topic(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> bool:
    lowered = _lower(text)
    blob = _profile_blob(profile_context, session_state)
    if lowered in {"e", "z", "x", "rf"} and session_state and session_state.get("last_product_labels"):
        return True
    if _budget_query(lowered) and any(token in blob for token in ("sony", "索尼", "viltrox", "镜头", "air", "pancake", "chip", "50mm", "85mm", "56mm")):
        return True
    return _has_any(
        lowered,
        (
            "lens",
            "lenses",
            "product",
            "gear",
            "镜头",
            "产品",
            "镜头参数",
            "买啥",
            "推荐",
            "sony",
            "索尼",
            "50mm",
            "85mm",
            "56mm",
            "焦段",
            "air",
            "evo",
            "lab",
            "pro",
            "epic",
            "luna",
            "z1",
            "z2",
            "flash",
            "闪光灯",
            "电影机",
            "cinema",
            "饼干头",
            "pancake",
            "chip lens",
            "chip",
            "mount",
            "卡口",
        ),
    ) or bool(re.search(r"(?<!\d)(20|27|28|40|50|56|85)(mm)?(?!\d)", lowered))


def _matches_product(product: ViaProduct, text: str) -> bool:
    lowered = _lower(text)
    return any(alias in lowered for alias in product.aliases)


def _generic_product_matches(text: str, limit: int = 4) -> list[dict[str, str]]:
    lowered = _lower(text)
    matches: list[dict[str, str]] = []
    for item in PRODUCT_RULES:
        label = str(item.get("label") or "").strip()
        series = str(item.get("series") or "").strip()
        if not label:
            continue
        keywords = [str(k).strip().lower() for k in item.get("keywords") or [] if str(k).strip()]
        if label.lower() in lowered or any(keyword in lowered for keyword in keywords[:10]):
            matches.append({"label": label, "series": series, "official_url": _official_search(label)})
        if len(matches) >= limit:
            break
    return matches


def _filter_by_mount(products: list[ViaProduct], mount_key: str | None) -> list[ViaProduct]:
    if not mount_key:
        return products
    label = MOUNT_LIBRARY.get(mount_key, {}).get("label")
    if not label:
        return products
    filtered = [item for item in products if label in item.mounts]
    return filtered or products


def _filter_by_budget(products: list[ViaProduct], budget_cap: int | None) -> list[ViaProduct]:
    if not budget_cap:
        return products
    filtered = [item for item in products if item.est_price_usd <= budget_cap + 25]
    return filtered or products


def _recommended_products(user_text: str, *, profile_context: str = "", session_state: dict[str, Any] | None = None) -> list[ViaProduct]:
    lowered = _lower(user_text)
    mount_key = _detect_mount(user_text, profile_context, session_state)
    budget_cap = _extract_budget(user_text, profile_context, session_state)
    explicit = _filter_by_mount([item for item in CATALOG if _matches_product(item, lowered)], mount_key)
    explicit = _filter_by_budget(explicit, budget_cap)
    if explicit:
        return explicit[:3]

    family = _family_key(user_text, profile_context, session_state)
    if family == "pancake":
        return _filter_by_mount(_filter_by_budget([CATALOG[0], CATALOG[3], CATALOG[2], CATALOG[1]], budget_cap), mount_key)[:3]
    if family == "air":
        return _filter_by_mount(_filter_by_budget([CATALOG[4], CATALOG[3], CATALOG[2], CATALOG[1], CATALOG[5], CATALOG[6]], budget_cap), mount_key)[:3]
    if family == "evo":
        return _filter_by_mount(_filter_by_budget([CATALOG[7], CATALOG[8], CATALOG[9]], budget_cap), mount_key)[:3]
    if family == "pro":
        return _filter_by_mount(_filter_by_budget([CATALOG[11], CATALOG[12], CATALOG[14], CATALOG[10]], budget_cap), mount_key)[:3]
    if family == "lab":
        return _filter_by_mount(_filter_by_budget([CATALOG[15], CATALOG[16]], budget_cap), mount_key)[:3]
    if family == "epic":
        return [CATALOG[17]]
    if family == "luna":
        return [CATALOG[18], CATALOG[19]][:3]
    if family == "lighting":
        return [CATALOG[20], CATALOG[21]][:3]

    if _apsc_query(lowered) or mount_key == "fuji_x":
        return _filter_by_mount(_filter_by_budget([CATALOG[6], CATALOG[10], CATALOG[5]], budget_cap), mount_key)[:3]

    if "85" in lowered:
        ordered = [CATALOG[9], CATALOG[13], CATALOG[14], CATALOG[5]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if "50" in lowered:
        ordered = [CATALOG[4], CATALOG[12], CATALOG[3]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if "35" in lowered:
        ordered = [CATALOG[7], CATALOG[11], CATALOG[6], CATALOG[15]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if budget_cap and budget_cap <= 320:
        ordered = [CATALOG[4], CATALOG[3], CATALOG[2], CATALOG[1], CATALOG[0], CATALOG[5]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if mount_key == "sony_e":
        ordered = [CATALOG[4], CATALOG[7], CATALOG[8], CATALOG[11], CATALOG[9]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if _budget_query(lowered) or _budget_query(_profile_blob(profile_context, session_state)):
        ordered = [CATALOG[4], CATALOG[3], CATALOG[2], CATALOG[1], CATALOG[5]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    ordered = [CATALOG[4], CATALOG[7], CATALOG[12], CATALOG[15], CATALOG[18], CATALOG[20]]
    return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]


def build_product_context(
    user_text: str,
    limit: int = 5,
    *,
    profile_context: str = "",
    session_state: dict[str, Any] | None = None,
) -> list[str]:
    if not _product_topic(user_text, profile_context, session_state):
        return []
    mount_key = _detect_mount(user_text, profile_context, session_state)
    budget_cap = _extract_budget(user_text, profile_context, session_state)
    family = _family_key(user_text, profile_context, session_state)
    matched_products = _recommended_products(user_text, profile_context=profile_context, session_state=session_state)[:limit]
    market_rows = _bh_market_rows(matched_products)
    lines: list[str] = []
    for item in matched_products:
        lines.append(
            f"{item.label} | {item.series} | {item.format_tag} | mounts: {item.mount_label} | "
            f"best for: {item.use_case} | est_price_usd: {item.est_price_usd} | budget_tier: {item.budget_tier} | "
            f"requested_mount: {MOUNT_LIBRARY.get(mount_key, {}).get('label', '')} | budget_cap: {budget_cap or ''} | url: {item.official_url}"
        )
        market_row = market_rows.get(item.label)
        if market_row:
            lines.append(
                f"market signal | {item.label} | B&H price: {market_row.get('price') or 0} | rating: {market_row.get('rating') or 0} | "
                f"reviews: {market_row.get('review_count') or 0} | in_stock: {bool(market_row.get('in_stock'))} | url: {market_row.get('url') or ''}"
            )
    if family in {"epic", "luna"}:
        for item in _series_rule_matches(family, user_text, limit=max(1, limit - len(lines))):
            lines.append(
                f"{item['label']} | {item['series']} | series_url: {SERIES_OFFICIAL_URLS.get(item['series'], STORE_URL)} | url: {item['official_url']}"
            )
            if len(lines) >= limit:
                break
    if len(lines) < limit:
        for item in _generic_product_matches(user_text, limit - len(lines)):
            lines.append(f"{item['label']} | {item['series']} | store: {STORE_URL} | url: {item['official_url']}")
    return lines[:limit]


def _mount_reply(products: list[ViaProduct], mount_key: str | None, lang: str) -> dict[str, Any]:
    first = products[0]
    if lang == "zh":
        mount_line = "、".join(first.mounts)
        requested = MOUNT_LIBRARY.get(mount_key or "", {}).get("label_zh") or "这条路线"
        text = (
            f"如果你是在问刚才这支 {first.label}，它现在主看 {mount_line}。"
            f" 你刚刚偏向的是 {requested}，所以我会优先继续按这条卡口帮你挑。官网入口：{first.official_url}"
        )
        return {"title": "卡口说明", "text": text, "quick_actions": ["给我链接", "再便宜一点", "换 40mm"], "lock_ai_override": True}
    mount_line = ", ".join(first.mounts)
    requested = MOUNT_LIBRARY.get(mount_key or "", {}).get("label") or "that mount lane"
    text = (
        f"If you mean {first.label}, it currently sits on {mount_line}. "
        f"You are leaning toward {requested}, so I will keep filtering for that mount. Official link: {first.official_url}"
    )
    return {"title": "Mount match", "text": text, "quick_actions": ["Give link", "Lower budget", "Show 40mm"], "lock_ai_override": True}


def _spec_reply(products: list[ViaProduct], lang: str) -> dict[str, Any]:
    first = products[0]
    second = products[1] if len(products) > 1 else None
    if lang == "zh":
        specs = [f"{first.label}：约 ${first.est_price_usd}｜{first.format_tag}｜{first.mount_label}｜适合 {first.use_case_zh}"]
        if second:
            specs.append(f"{second.label}：约 ${second.est_price_usd}｜{second.format_tag}｜{second.mount_label}｜适合 {second.use_case_zh}")
        return {
            "title": "唯卓仕参数",
            "text": f"我先按唯卓仕自家产品给你讲具体一点：{'；'.join(specs)}。正式商城入口在 {STORE_URL}",
            "quick_actions": ["给我链接", "按预算选", "按卡口选"],
            "lock_ai_override": True,
        }
    specs = [f"{first.label}: about ${first.est_price_usd}, {first.format_tag}, {first.mount_label}, best for {first.use_case}"]
    if second:
        specs.append(f"{second.label}: about ${second.est_price_usd}, {second.format_tag}, {second.mount_label}, best for {second.use_case}")
    return {
        "title": "Viltrox specs",
        "text": f"I will keep this inside Viltrox. Start with {'; '.join(specs)}. Store: {STORE_URL}",
        "quick_actions": ["Give links", "Budget picks", "Mount picks"],
        "lock_ai_override": True,
    }


def _link_reply(products: list[ViaProduct], lang: str) -> dict[str, Any]:
    market_rows = _bh_market_rows(products[:2])
    listing = " / ".join(f"{item.label}：{item.official_url}" for item in products[:2]) if lang == "zh" else " / ".join(f"{item.label}: {item.official_url}" for item in products[:2])
    market_hint = ""
    lead_market = market_rows.get(products[0].label) if products else None
    if lead_market:
        market_hint = " " + _market_line(products[0], lead_market, lang)
    return {
        "title": "官方链接" if lang == "zh" else "Official links",
        "text": (
            f"我先只给你推唯卓仕自家产品。你可以先看：{listing}。总店入口：{STORE_URL}。{market_hint}".strip()
            if lang == "zh"
            else f"I will keep it Viltrox-only. Start here: {listing}. Store: {STORE_URL}.{market_hint}"
        ),
        "quick_actions": ["讲参数", "按预算选", "按机身选"] if lang == "zh" else ["Show specs", "Budget pick", "Mount pick"],
        "lock_ai_override": True,
    }


def _recommendation_reply(products: list[ViaProduct], lang: str, budget_cap: int | None) -> dict[str, Any]:
    lead = products[0]
    extra = products[1] if len(products) > 1 else None
    market_rows = _bh_market_rows(products[:2])
    scenario = _scenario_label(" ".join([lead.use_case, extra.use_case if extra else ""]), lang)
    if lang == "zh":
        budget_line = f"如果你想把预算压在 ${budget_cap} 左右，" if budget_cap else ""
        text = (
            f"{budget_line}我会先推唯卓仕自己的 {lead.label}。"
            f" 它更适合 {lead.use_case_zh}，大致在 ${lead.est_price_usd} 这一档，而且 {lead.hero_reason_zh}。"
        )
        if extra:
            text += f" 如果你想要另一个同样更稳的方向，我会再让你看 {extra.label}，它更像 {extra.use_case_zh} 这条线。"
        text += f" 如果你现在主要是 {scenario}，我会先从这条路线下手。"
        text += " " + _market_line(lead, market_rows.get(lead.label), lang)
        return {"title": "唯卓仕推荐", "text": text, "quick_actions": ["给我参数", "给我链接", "按卡口选"], "lock_ai_override": False}
    budget_line = f"If you want to stay around ${budget_cap}, " if budget_cap else ""
    text = (
        f"{budget_line}I would keep it inside Viltrox and start with {lead.label}. "
        f"It fits {lead.use_case}, sits roughly around ${lead.est_price_usd}, and {lead.hero_reason}."
    )
    if extra:
        text += f" My alternate lane would be {extra.label} for {extra.use_case}."
    text += f" If your real use case is {scenario}, this is where I would start. "
    text += _market_line(lead, market_rows.get(lead.label), lang)
    return {"title": "Viltrox picks", "text": text, "quick_actions": ["Show specs", "Give links", "Mount match"], "lock_ai_override": False}


def _comparison_reply(products: list[ViaProduct], lang: str, budget_cap: int | None, user_text: str) -> dict[str, Any]:
    lead = products[0]
    alt = products[1] if len(products) > 1 else products[0]
    market_rows = _bh_market_rows([lead, alt])
    if lang == "zh":
        budget_line = f"你现在如果想压在 ${budget_cap} 左右，" if budget_cap else ""
        text = (
            f"{budget_line}我会这样分：{_compare_axis(lead, alt, lang)} "
            f"{lead.label} 大约在 ${lead.est_price_usd} 这一档，{alt.label} 大约在 ${alt.est_price_usd} 这一档。 "
            f"如果你更看重 {_scenario_label(user_text, lang)}，我会先开 {lead.label} 的官网页给你。 "
            f"{_market_line(lead, market_rows.get(lead.label), lang)}"
        )
        if alt.label != lead.label:
            text += f" 备选方向是 {alt.label}，入口在 {alt.official_url}。"
        return {"title": "怎么选", "text": text, "quick_actions": ["给我参数", "给我链接", "按预算重排"], "lock_ai_override": False}
    budget_line = f"If you need to stay around ${budget_cap}, " if budget_cap else ""
    text = (
        f"{budget_line}here is the cleaner split: {_compare_axis(lead, alt, lang)} "
        f"{lead.label} lands around ${lead.est_price_usd}, while {alt.label} lands around ${alt.est_price_usd}. "
        f"For {_scenario_label(user_text, lang)}, I would open {lead.label} first. "
        f"{_market_line(lead, market_rows.get(lead.label), lang)}"
    )
    if alt.label != lead.label:
        text += f" Alternate route: {alt.label} at {alt.official_url}."
    return {"title": "How I would choose", "text": text, "quick_actions": ["Show specs", "Give links", "Re-rank by budget"], "lock_ai_override": False}


def _state_patch(products: list[ViaProduct], mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    return {
        "last_product_labels": [item.label for item in products[:3]],
        "last_product_series": products[0].series if products else "",
        "last_product_summary": products[0].label if products else "",
        "last_mount_hint": mount_key or "",
        "last_budget_hint": budget_cap or 0,
    }


def _product_behavior_mode(subintent: str) -> str:
    if subintent in {"budget", "recommendation", "family_guide", "comparison"}:
        return "photography"
    if subintent in {"specs", "links", "mount"}:
        return "gear"
    return "pet"


def _classify_product_subintent(
    *,
    lowered: str,
    user_text: str,
    family: str | None,
    mount_only: bool,
    has_products: bool,
) -> str:
    if family and _family_guide_query(user_text, family):
        return "family_guide"
    if mount_only:
        return "mount"
    if _comparison_query(user_text):
        return "comparison"
    if _spec_query(user_text):
        return "specs"
    if _link_query(user_text):
        return "links"
    if has_products:
        budget_cap = _extract_budget(user_text)
        if budget_cap:
            return "budget"
        return "recommendation"
    return "catalog"


def get_via_product_reply(
    user_text: str,
    *,
    profile_context: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    competitor_reply = handle_external_competitor_query(user_text)
    if competitor_reply:
        return competitor_reply
    if not _product_topic(user_text, profile_context, session_state):
        return None
    lang = _detect_reply_language(user_text, profile_context, session_state)
    family = _family_key(user_text, profile_context, session_state)
    mount_key = _detect_mount(user_text, profile_context, session_state)
    budget_cap = _extract_budget(user_text, profile_context, session_state)
    lowered = _lower(user_text)
    mount_only = lowered in {"e", "z", "x", "rf"} or _has_any(lowered, ("卡口", "mount"))
    subintent = _classify_product_subintent(
        lowered=lowered,
        user_text=user_text,
        family=family,
        mount_only=mount_only,
        has_products=True,
    )
    if family in {"epic", "luna"} and _series_rule_matches(family, user_text, limit=2):
        if subintent == "family_guide":
            return _series_rule_guide_reply(family, lang, user_text, mount_key, budget_cap)
        if subintent == "links":
            return _series_rule_links_reply(family, lang, user_text, mount_key, budget_cap)
        if subintent == "specs":
            return _series_rule_specs_reply(family, lang, user_text, mount_key, budget_cap)
        if subintent == "comparison":
            return _series_rule_comparison_reply(family, lang, user_text, mount_key, budget_cap)
    if subintent == "family_guide" and family and family in FAMILY_GUIDES:
        guide = FAMILY_GUIDES[family]
        return {
            "title": guide["zh_title"] if lang == "zh" else guide["en_title"],
            "text": guide["zh_text"] if lang == "zh" else guide["en_text"],
            "quick_actions": guide["quick_actions_zh"] if lang == "zh" else guide["quick_actions_en"],
            "lock_ai_override": False,
            "product_subintent": "family_guide",
            "behavior_mode": _product_behavior_mode("family_guide"),
            "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
        }

    products = _recommended_products(user_text, profile_context=profile_context, session_state=session_state)
    if not products:
        generic = _generic_product_matches(user_text, 2)
        if not generic:
            return None
        listing = " / ".join(f"{item['label']}：{item['official_url']}" for item in generic) if lang == "zh" else " / ".join(f"{item['label']}: {item['official_url']}" for item in generic)
        return {
            "title": "唯卓仕产品" if lang == "zh" else "Viltrox products",
            "text": (f"我会优先推荐唯卓仕自家产品。你可以先从这几条官方入口开始看：{listing}。总店入口也在 {STORE_URL}" if lang == "zh" else f"I will keep recommendations inside Viltrox. Start here: {listing}. Store: {STORE_URL}"),
            "quick_actions": ["讲参数", "按预算选", "按机身选"] if lang == "zh" else ["Show specs", "Budget pick", "Mount pick"],
            "lock_ai_override": True,
            "product_subintent": "catalog",
            "behavior_mode": _product_behavior_mode("catalog"),
            "session_state_patch": {"last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
        }

    subintent = _classify_product_subintent(
        lowered=lowered,
        user_text=user_text,
        family=family,
        mount_only=mount_only,
        has_products=bool(products),
    )

    if subintent == "mount":
        reply = _mount_reply(products, mount_key, lang)
    elif subintent == "comparison":
        reply = _comparison_reply(products, lang, budget_cap, user_text)
    elif subintent == "specs":
        reply = _spec_reply(products, lang)
    elif subintent == "links":
        reply = _link_reply(products, lang)
    else:
        reply = _recommendation_reply(products, lang, budget_cap)
    reply["product_subintent"] = subintent
    reply["behavior_mode"] = _product_behavior_mode(subintent)
    reply["session_state_patch"] = _state_patch(products, mount_key, budget_cap)
    return reply
