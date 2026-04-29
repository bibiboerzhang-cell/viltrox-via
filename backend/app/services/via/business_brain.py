"""
services/via/business_brain.py — deterministic official business/rental guidance for Via
"""
from __future__ import annotations

import re
from typing import Any

from app.services.via.product_brain import SERIES_OFFICIAL_URLS

SUPPORT_CENTER_URL = "https://viltrox.com/pages/support-center"
CONTACT_URL = "https://viltrox.com/pages/contact-us"
AFFILIATE_GUIDE_URL = "https://viltrox.com/pages/join-affiliate-tutorial"
OFFICIAL_CONTACT_EMAIL = "info@viltrox.com"

_RENTAL_TOKENS = (
    "rental",
    "rent",
    "rental house",
    "rental company",
    "lensrentals",
    "sharegrid",
    "kitsplit",
    "hire",
    "租镜头",
    "租赁",
    "出租",
    "器材租赁",
    "合作租赁",
)
_TRIAL_TOKENS = (
    "borrow",
    "loan",
    "loaner",
    "trial",
    "demo",
    "try before buy",
    "test drive",
    "试用",
    "体验",
    "借",
    "借镜头",
    "借机",
    "借测",
    "借用",
)
_PARTNER_TOKENS = (
    "partner",
    "partnership",
    "cooperate",
    "cooperation",
    "business cooperation",
    "合作",
    "商务合作",
    "合作方",
)
_SUPPORT_TOKENS = (
    "support",
    "support center",
    "contact us",
    "contact",
    "客服",
    "联系官方",
    "联系你们",
    "售后",
)
_AFFILIATE_TOKENS = (
    "affiliate",
    "referral",
    "commission",
    "推广",
    "分销",
    "佣金",
)
_GEAR_TOKENS = (
    "viltrox",
    "lens",
    "lenses",
    "gear",
    "镜头",
    "电影机",
    "cinema",
    "epic",
    "anamorphic",
    "luna",
    "air",
    "evo",
    "lab",
    "pro",
)
_FOLLOW_UP_TRIAL_RE = re.compile(r"^(借|借镜头|borrow|trial|试用|rent|rental)\??$", flags=re.IGNORECASE)


def _lower(text: str) -> str:
    return str(text or "").strip().lower()


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = _lower(text)
    return any(token in lowered for token in tokens)


def _detect_lang(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> str:
    blob = " ".join(
        [
            str(text or ""),
            str(profile_context or ""),
            str((session_state or {}).get("last_user_language") or ""),
        ]
    )
    return "zh" if _contains_cjk(blob) or " zh" in f" {blob.lower()}" else "en"


def _business_series(user_text: str) -> tuple[str, str] | None:
    lowered = _lower(user_text)
    if any(token in lowered for token in ("epic", "anamorphic", "变形", "电影镜头", "blue streak", "silver flare", "pl")):
        return ("EPIC", SERIES_OFFICIAL_URLS["EPIC"])
    if any(token in lowered for token in ("luna", "30-300", "42-420", "cine zoom", "体育转播", "纪录片长焦")):
        return ("LUNA", SERIES_OFFICIAL_URLS["LUNA"])
    return None


def _business_topic(
    user_text: str,
    *,
    session_state: dict[str, Any] | None = None,
) -> bool:
    lowered = _lower(user_text)
    if not lowered:
        return False
    has_rental = _has_any(lowered, _RENTAL_TOKENS)
    has_trial = _has_any(lowered, _TRIAL_TOKENS)
    has_partner = _has_any(lowered, _PARTNER_TOKENS)
    has_support = _has_any(lowered, _SUPPORT_TOKENS)
    has_affiliate = _has_any(lowered, _AFFILIATE_TOKENS)
    has_gear = _has_any(lowered, _GEAR_TOKENS) or bool(_business_series(lowered))
    previous_business = str((session_state or {}).get("last_business_intent") or "").strip()

    if has_rental or has_trial:
        return True
    if has_partner and (has_gear or has_affiliate or previous_business):
        return True
    if has_support and (has_gear or previous_business):
        return True
    if previous_business and _FOLLOW_UP_TRIAL_RE.fullmatch(lowered):
        return True
    return False


def _classify_business_subintent(user_text: str) -> str:
    lowered = _lower(user_text)
    has_rental = _has_any(lowered, _RENTAL_TOKENS)
    has_trial = _has_any(lowered, _TRIAL_TOKENS)
    has_partner = _has_any(lowered, _PARTNER_TOKENS)
    has_affiliate = _has_any(lowered, _AFFILIATE_TOKENS)
    if has_rental or (has_partner and has_trial):
        return "rental_partner"
    if has_trial:
        return "trial_request"
    if has_affiliate:
        return "affiliate_contact"
    return "business_contact"


def build_business_context(
    user_text: str,
    *,
    profile_context: str = "",
    session_state: dict[str, Any] | None = None,
) -> list[str]:
    if not _business_topic(user_text, session_state=session_state):
        return []
    lines = [
        f"Official support center: {SUPPORT_CENTER_URL}",
        f"Official contact page: {CONTACT_URL}",
        f"Official contact email: {OFFICIAL_CONTACT_EMAIL}",
        f"Official affiliate guide: {AFFILIATE_GUIDE_URL}",
        "Use official support/contact to confirm rental, trial, or cooperation availability. Do not invent public partner rosters.",
    ]
    series = _business_series(user_text)
    if series:
        lines.append(f"Relevant official series page: {series[0]} | {series[1]}")
    return lines[:6]


def _series_line(series: tuple[str, str] | None, lang: str) -> str:
    if not series:
        return ""
    label, url = series
    if lang == "zh":
        return f" 如果你主要问的是 {label} 这条线，也可以先看 {url}。"
    return f" If the question is really about the {label} line, start here too: {url}."


def _reply_quick_actions(subintent: str, lang: str, series: tuple[str, str] | None) -> list[str]:
    if lang == "zh":
        actions = ["给我询盘模板", "Support Center", "Contact Us"]
        if subintent == "affiliate_contact":
            actions = ["Affiliate Guide", "Contact Us", "给我合作模板"]
    else:
        actions = ["Draft my inquiry", "Support Center", "Contact Us"]
        if subintent == "affiliate_contact":
            actions = ["Affiliate guide", "Contact us", "Draft outreach"]
    if series:
        actions[-1] = f"{series[0]} 官网" if lang == "zh" else f"{series[0]} page"
    return actions[:3]


def get_via_business_reply(
    user_text: str,
    *,
    profile_context: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _business_topic(user_text, session_state=session_state):
        return None
    lang = _detect_lang(user_text, profile_context, session_state)
    subintent = _classify_business_subintent(user_text)
    series = _business_series(user_text)

    if lang == "zh":
        if subintent == "rental_partner":
            title = "租赁合作"
            text = (
                "我现在没看到唯卓仕公开列出的官方租赁合作名单。最稳的公开入口是 Support Center 和 Contact Us "
                f"（{OFFICIAL_CONTACT_EMAIL}）。如果你想问试租、借测或合作，请把地区、项目、机身、时间窗和目标镜头一起发给官方。"
            )
        elif subintent == "trial_request":
            title = "试用怎么问"
            text = (
                "公开资料里我还没看到直接线上借测入口。要问试用或借镜头，最稳是走 Support Center / Contact Us，"
                "并写清地区、档期、机身和想试的型号。"
            )
        elif subintent == "affiliate_contact":
            title = "合作入口"
            text = (
                "如果你问的是推广或合作入口，公开资料里最明确的是 Affiliate Guide。"
                f"更具体的商务问题仍建议走 Contact Us（{OFFICIAL_CONTACT_EMAIL}）或 Support Center。"
            )
        else:
            title = "官方入口"
            text = (
                f"如果你是在问合作或商务对接，公开入口先看 Support Center 和 Contact Us（{OFFICIAL_CONTACT_EMAIL}）。"
                "如果涉及租赁、试用或电影线项目合作，建议直接把需求和档期发给官方确认。"
            )
    else:
        if subintent == "rental_partner":
            title = "Rental path"
            text = (
                "I do not see a public official Viltrox rental-partner roster right now. "
                f"The safest public path is Support Center and Contact Us ({OFFICIAL_CONTACT_EMAIL}). "
                "For rental, trial, or cooperation, send your region, project, camera body, timing window, and target lens."
            )
        elif subintent == "trial_request":
            title = "Trial path"
            text = (
                "I do not see a direct public loaner or trial form yet. "
                "Use Support Center or Contact Us and include your region, schedule, camera body, and the lens you want to test."
            )
        elif subintent == "affiliate_contact":
            title = "Partner lane"
            text = (
                f"The clearest public partner path is the Affiliate Guide. For anything more custom, use Contact Us ({OFFICIAL_CONTACT_EMAIL}) "
                "or Support Center so the request stays official."
            )
        else:
            title = "Official path"
            text = (
                f"For cooperation or business contact, start with Support Center or Contact Us ({OFFICIAL_CONTACT_EMAIL}). "
                "If this is about rental, trial, or cinema-line projects, send the details straight to official support."
            )
    text = f"{text}{_series_line(series, lang)}".strip()
    return {
        "title": title,
        "text": text[:500],
        "quick_actions": _reply_quick_actions(subintent, lang, series),
        "lock_ai_override": True,
        "business_subintent": subintent,
        "behavior_mode": "gear",
        "session_state_patch": {
            "last_business_intent": subintent,
            "last_business_series": series[0] if series else "",
            "last_business_summary": title,
        },
    }
