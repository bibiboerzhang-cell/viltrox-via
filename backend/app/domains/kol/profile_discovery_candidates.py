"""Candidate normalization and platform filtering for profile discovery."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

from app.domains.kol.profile_recall_match_evidence import candidate_set_distribution_from_items

from app.domains.kol.discovery_filters import (
    SUPPORTED_DISCOVERY_PLATFORMS,
    _platforms,
    _text,
)


# Viltrox 品牌自营账号:handle/URL 归一后以 viltrox 打头(viltrox.official/viltrox_id/
# viltrox.global/viltrox.usa/viltrox.cee 等)。官号走专线,不当外部 KOL 发现。env 可扩关键词。
import os as _os  # noqa: E402 — 模块级 os 未导入(散点函数内 import);此处只读 env

_OWN_BRAND_PREFIXES = tuple(
    p.strip().lower()
    for p in str(_os.environ.get("VKPI_OWN_BRAND_HANDLE_PREFIXES", "viltrox")).split(",")
    if p.strip()
)

_OWN_BRAND_CONFIRMED_SUFFIXES = frozenset({
    "",
    "official",
    "global",
    "photography",
    "photo",
    "video",
    "camera",
    "lens",
    "lenses",
    "store",
    "shop",
    "hq",
    "usa",
    "us",
    "uk",
    "eu",
    "europe",
    "asia",
    "japan",
    "india",
    "indonesia",
    "id",
    "philippines",
    "ph",
    "cee",
    "de",
    "fr",
    "es",
    "it",
})
_OWN_BRAND_PROFILE_HOSTS = frozenset({
    "youtube.com",
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "fb.com",
})


def _own_brand_identity_suffix(value: Any) -> str | None:
    norm = re.sub(r"[^a-z0-9]", "", str(value or "").lower().lstrip("@"))
    if not norm:
        return None
    for prefix in _OWN_BRAND_PREFIXES:
        base = re.sub(r"[^a-z0-9]", "", prefix)
        if base and norm.startswith(base):
            suffix = norm[len(base):]
            if suffix in _OWN_BRAND_CONFIRMED_SUFFIXES:
                return suffix
    return None


def _profile_identity_locator(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return ""
    host = parsed.netloc.lower().split(":", 1)[0]
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    if host not in _OWN_BRAND_PROFILE_HOSTS:
        return ""
    parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    if host == "youtube.com":
        if len(parts) == 1 and parts[0].startswith("@"):
            return parts[0]
        if len(parts) == 2 and parts[0].lower() in {"channel", "c", "user"}:
            return parts[1]
        return ""
    if host == "tiktok.com":
        return parts[0] if len(parts) == 1 and parts[0].startswith("@") else ""
    return parts[0] if len(parts) == 1 else ""


def _is_own_brand_account(item: dict[str, Any]) -> bool:
    """Conservatively recognize confirmed Viltrox-owned account identities.

    Strong identity comes only from a handle or normalized platform profile
    URL. Display/channel names are weak evidence: a Viltrox mention there must
    also carry an explicit official form or a corporate-voice bio.
    """
    if any(
        _own_brand_identity_suffix(item.get(field)) is not None
        for field in ("handle", "channel_handle", "username")
    ):
        return True
    if any(
        _own_brand_identity_suffix(_profile_identity_locator(item.get(field))) is not None
        for field in ("profile_url", "channel_url", "url")
    ):
        return True
    names = " ".join(
        str(item.get(field) or "")
        for field in ("display_name", "channel_name", "author_name")
    ).strip()
    if not names or not any(
        _own_brand_identity_suffix(item.get(field)) is not None
        for field in ("display_name", "channel_name", "author_name")
    ):
        return False
    if "official" in names.lower():
        return True
    from app.domains.kol.discovery_filters import _corporate_voice_bio

    return _corporate_voice_bio(item.get("bio") or item.get("description"))


_PLATFORM_HOSTS = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "fb.com": "facebook",
}
_PLATFORM_ALIASES = {
    "yt": "youtube",
    "youtube_shorts": "youtube",
    "ig": "instagram",
    "ins": "instagram",
    "tt": "tiktok",
    "fb": "facebook",
}


def _normalize_discovery_platform(value: Any) -> str:
    text = _text(value).lower().replace(" ", "_")
    return _PLATFORM_ALIASES.get(text, text)


def _strict_discovery_platforms(value: Any, *, fallback: str = "") -> list[str]:
    """Resolve explicit platform choices without widening invalid input to all defaults."""
    raw_values = value if isinstance(value, list) else [value]
    explicit = [
        _normalize_discovery_platform(raw)
        for raw in raw_values
        if _text(raw) and _text(raw).lower() not in {"all", "*"}
    ]
    if explicit:
        return list(dict.fromkeys(item for item in explicit if item in SUPPORTED_DISCOVERY_PLATFORMS))
    fallback_text = _normalize_discovery_platform(fallback)
    if fallback_text and fallback_text not in {"all", "*"}:
        return [fallback_text] if fallback_text in SUPPORTED_DISCOVERY_PLATFORMS else []
    return _platforms(None)


def explicit_platforms_from_query(query: Any) -> list[str]:
    """Extract only platform names the operator actually typed."""
    text = str(query or "").strip().lower()
    patterns = (
        ("youtube", r"(?<![a-z])(?:youtube|yt)(?![a-z])|油管"),
        # ``ins`` is a common unit abbreviation ("5.5 ins monitor"), not a
        # safe Instagram alias.  Keep the unambiguous product/UI spellings.
        ("instagram", r"(?<![a-z])(?:instagram|insta|ig)(?![a-z])"),
        ("tiktok", r"(?<![a-z])(?:tiktok|tt)(?![a-z])|抖音"),
        ("facebook", r"(?<![a-z])(?:facebook|fb)(?![a-z])"),
    )
    return [platform for platform, pattern in patterns if re.search(pattern, text)]


def _candidate_platform_signals(item: dict[str, Any]) -> set[str]:
    signals: set[str] = set()
    explicit = _text(
        item.get("platform")
        or item.get("platform_name")
        or item.get("source_platform")
    )
    if explicit:
        signals.add(_normalize_discovery_platform(explicit))
    for key in ("channel_url", "profile_url", "source_url", "url", "post_url", "webVideoUrl"):
        raw_url = _text(item.get(key))
        if not raw_url:
            continue
        try:
            host = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}").netloc.lower().removeprefix("www.")
        except ValueError:
            continue
        for suffix, platform in _PLATFORM_HOSTS.items():
            if host == suffix or host.endswith(f".{suffix}"):
                signals.add(platform)
                break
    return signals


def filter_recall_result_platforms(result: dict[str, Any], value: Any) -> dict[str, Any]:
    """Apply the explicit UI platform allowlist to local recall rows and buckets."""
    raw_values = value if isinstance(value, list) else [value]
    requested = {
        _normalize_discovery_platform(raw)
        for raw in raw_values
        if _text(raw) and _text(raw).lower() not in {"all", "*"}
    }
    if not requested:
        return result

    def _keep(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        platform = _normalize_discovery_platform(item.get("platform") or payload.get("platform"))
        return bool(platform and platform in requested)

    filtered = dict(result)
    original_items = result.get("items") if isinstance(result.get("items"), list) else None
    if original_items is not None:
        filtered["items"] = [item for item in original_items if _keep(item)]
    original_buckets = result.get("buckets") if isinstance(result.get("buckets"), dict) else None
    if original_buckets is not None:
        filtered["buckets"] = {
            key: [item for item in items if _keep(item)]
            for key, items in original_buckets.items()
            if isinstance(items, list)
        }
    original_business_buckets = (
        result.get("business_buckets") if isinstance(result.get("business_buckets"), dict) else None
    )
    if original_business_buckets is not None:
        filtered["business_buckets"] = {
            key: [item for item in items if _keep(item)]
            for key, items in original_business_buckets.items()
            if isinstance(items, list)
        }
    before_item_count = len(original_items or [])
    before_bucket_count = sum(
        len(items) for items in (original_buckets or {}).values() if isinstance(items, list)
    )
    after_item_count = len(filtered.get("items") or [])
    after_bucket_count = sum(
        len(items) for items in (filtered.get("buckets") or {}).values() if isinstance(items, list)
    )
    filtered_buckets = filtered.get("buckets") if isinstance(filtered.get("buckets"), dict) else {}
    filtered_business_buckets = (
        filtered.get("business_buckets")
        if isinstance(filtered.get("business_buckets"), dict)
        else {}
    )
    before_count = before_item_count or before_bucket_count
    after_count = after_item_count or after_bucket_count
    diagnostics = dict(result.get("diagnostics")) if isinstance(result.get("diagnostics"), dict) else {}
    requested_count = int(diagnostics.get("requested_count") or after_count)
    final_count = after_count
    shortfall = max(0, requested_count - final_count)
    filtered_items = filtered.get("items") if isinstance(filtered.get("items"), list) else []
    lane_order = ("core_vertical", "expansion", "exploration")
    lane_selected = {
        lane: len(filtered_business_buckets.get(lane) or [])
        for lane in lane_order
    }
    # Older callers may not return business_buckets. In that case the final
    # item list remains authoritative for selected-lane counts.
    if not any(lane_selected.values()) and filtered_items:
        lane_selected = {
            lane: sum(1 for item in filtered_items if item.get("candidate_bucket") == lane)
            for lane in lane_order
        }
    original_lane_selection = (
        diagnostics.get("lane_selection")
        if isinstance(diagnostics.get("lane_selection"), dict)
        else {}
    )
    original_targets = (
        original_lane_selection.get("lane_targets")
        if isinstance(original_lane_selection.get("lane_targets"), dict)
        else {}
    )
    lane_targets = {lane: max(0, int(original_targets.get(lane) or 0)) for lane in lane_order}
    lane_shortfalls = {
        lane: max(0, lane_targets[lane] - min(lane_selected[lane], lane_targets[lane]))
        for lane in lane_order
    }
    lane_refills = {
        lane: max(0, lane_selected[lane] - lane_targets[lane])
        for lane in lane_order
    }
    profile_counts = {
        "creator": len(filtered_buckets.get("creator") or []),
        "reviewer": len(filtered_buckets.get("reviewer") or []),
        "unknown": len(filtered_buckets.get("unknown") or []),
    }
    filter_changed_result = before_count != after_count
    if not filter_changed_result and original_lane_selection:
        # Normal route: platform was already a hard retrieval filter. Keep the
        # richer pre-selection availability diagnostics when this guard is a
        # no-op instead of downgrading them to returned-set counts.
        reconciled_lane_selection = original_lane_selection
    else:
        reconciled_lane_selection = {
            **original_lane_selection,
            "lane_targets": lane_targets,
            # The compatibility post-filter can only observe the returned set,
            # so do not retain a stale pre-filter availability claim.
            "lane_available": dict(lane_selected),
            "lane_available_scope": "post_filter_returned_set",
            "lane_selected": lane_selected,
            "lane_shortfalls": lane_shortfalls,
            "lane_refills": lane_refills,
            "lane_contract_satisfied": all(value == 0 for value in lane_shortfalls.values()),
            "profile_counts": profile_counts,
        }
    original_business_counts = diagnostics.get("business_bucket_counts")
    reconciled_business_counts = (
        original_business_counts
        if not filter_changed_result and isinstance(original_business_counts, dict)
        else lane_selected
    )
    diagnostics.update(
        {
            "returned_count": after_count,
            "final_count": final_count,
            "shortfall": shortfall,
            "result_contract_satisfied": shortfall == 0,
            "creator_returned": len(filtered_buckets.get("creator") or []),
            "reviewer_returned": len(filtered_buckets.get("reviewer") or []),
            "unknown_type_returned": len(filtered_buckets.get("unknown") or []),
            "strict_count": sum(1 for item in filtered_items if item.get("match_tier") == "strict"),
            "relaxed_count": sum(1 for item in filtered_items if item.get("match_tier") == "relaxed"),
            "backfill_count": sum(1 for item in filtered_items if item.get("match_tier") == "backfill"),
            "business_bucket_counts": reconciled_business_counts,
            "lane_selection": reconciled_lane_selection,
            "platform_filtered_out": max(0, before_count - after_count),
            "platform_filter": sorted(requested),
            "post_filter_counts_reconciled": True,
        }
    )
    filtered["diagnostics"] = diagnostics
    if diagnostics.get("evidence_gate_enabled"):
        filtered["match_status"] = "matched" if after_count else "empty"
        if after_count:
            diagnostics["empty_reason"] = ""
        elif before_count > 0:
            diagnostics["empty_reason"] = "no_platform_evidence_match"
        filtered["candidate_set_distribution"] = candidate_set_distribution_from_items(
            list(filtered.get("items") or [])
        )
    filtered["platform_filter"] = {
        "applied": True,
        "requested": sorted(requested),
        "filtered_out": max(0, before_count - after_count),
    }
    return filtered


_MARKET_ALIASES = {
    "us": "us", "usa": "us", "united states": "us", "united states of america": "us", "美国": "us",
    "uk": "gb", "gb": "gb", "united kingdom": "gb", "great britain": "gb", "英国": "gb",
    "ca": "ca", "canada": "ca", "加拿大": "ca",
    "de": "de", "germany": "de", "德国": "de",
    "fr": "fr", "france": "fr", "法国": "fr",
    "jp": "jp", "japan": "jp", "日本": "jp",
    "kr": "kr", "korea": "kr", "south korea": "kr", "韩国": "kr",
    "au": "au", "australia": "au", "澳大利亚": "au",
    "es": "es", "spain": "es", "españa": "es", "西班牙": "es",
    "mx": "mx", "mexico": "mx", "méxico": "mx", "墨西哥": "mx",
    "it": "it", "italy": "it", "italia": "it", "意大利": "it",
    "br": "br", "brazil": "br", "brasil": "br", "巴西": "br",
    "pt": "pt", "portugal": "pt", "葡萄牙": "pt",
    "ru": "ru", "russia": "ru", "russian federation": "ru", "俄罗斯": "ru",
    "th": "th", "thailand": "th", "泰国": "th",
    "vn": "vn", "vietnam": "vn", "viet nam": "vn", "越南": "vn",
    "id": "id", "indonesia": "id", "印尼": "id", "印度尼西亚": "id",
    "tr": "tr", "turkey": "tr", "türkiye": "tr", "土耳其": "tr",
    "pl": "pl", "poland": "pl", "波兰": "pl",
    "nl": "nl", "netherlands": "nl", "holland": "nl", "荷兰": "nl",
    "sa": "sa", "saudi arabia": "sa", "沙特": "sa",
    "ae": "ae", "united arab emirates": "ae", "uae": "ae", "阿联酋": "ae",
    "in": "in", "india": "in", "印度": "in",
    "sg": "sg", "singapore": "sg", "新加坡": "sg",
    "nz": "nz", "new zealand": "nz", "新西兰": "nz",
}
AMBIGUOUS_MARKET_CONSTRAINT = "__ambiguous_market__"
_CONTEXT_REQUIRED_MARKET_CODES = frozenset({"ae", "au", "ca", "de", "id", "in", "it", "pl", "pt", "sa"})
_LOWERCASE_SAFE_MARKET_CODES = frozenset({
    "br", "fr", "gb", "jp", "kr", "mx", "nl", "nz", "ru", "sg", "th", "tr", "uk", "vn",
})


def explicit_market_constraint(query: Any, planned_market: Any) -> str:
    """Return the one market the operator actually stated.

    ``planned_market`` is retained for API compatibility but is never trusted
    as a hard constraint; provider/fallback defaults are not operator choices.
    """
    raw_text = str(query or "").strip()
    text = f" {raw_text.lower()} "
    del planned_market
    matches: set[str] = set()
    for alias, code in _MARKET_ALIASES.items():
        if any("\u4e00" <= char <= "\u9fff" for char in alias):
            if alias in text:
                matches.add(code)
        elif len(alias) == 2:
            upper = re.escape(alias.upper())
            if alias == "pl" and re.search(r"(?i)(?<![A-Za-z])PL\s*(?:-\s*)?(?:mount|卡口)", raw_text):
                # PL is also a cinema-lens mount.  Even phrases such as
                # "in PL mount" describe product compatibility, not Poland.
                continue
            # Ambiguous codes collide with ordinary language, US states, or
            # product syntax (notably ``PL mount``). Other uppercase country
            # codes remain useful shorthand in KOL operator queries.
            if alias in _CONTEXT_REQUIRED_MARKET_CODES:
                pattern = rf"(?i)(?:\b(?:in|from|country|market)\s*[:=]?\s*){upper}(?![A-Za-z])"
            elif alias in _LOWERCASE_SAFE_MARKET_CODES:
                pattern = rf"(?i)(?<![A-Za-z]){upper}(?![A-Za-z])"
            else:
                # Keep ambiguous lowercase words such as the English pronoun
                # ``us`` and Spanish ``es`` from silently becoming countries.
                pattern = rf"(?<![A-Za-z]){upper}(?![A-Za-z])"
            if re.search(pattern, raw_text):
                matches.add(code)
        elif re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text):
            matches.add(code)
    if len(matches) > 1:
        return AMBIGUOUS_MARKET_CONSTRAINT
    return next(iter(matches)) if matches else ""


def normalize_market_constraint(value: Any) -> str:
    """Normalize an explicit structured market; unknown values fail closed."""
    return _MARKET_ALIASES.get(str(value or "").strip().lower(), "")


def resolve_market_constraint(query: Any, structured: Any = None) -> str:
    """Resolve one operator market, rejecting unsupported, ambiguous or conflicting input."""
    query_market = explicit_market_constraint(query, None)
    if query_market == AMBIGUOUS_MARKET_CONSTRAINT:
        raise ValueError("multiple market constraints are not supported")
    raw_structured = str(structured or "").strip()
    structured_market = normalize_market_constraint(raw_structured) if raw_structured else ""
    if raw_structured and not structured_market:
        raise ValueError("unsupported market constraint")
    if query_market and structured_market and query_market != structured_market:
        raise ValueError("conflicting market constraints")
    return query_market or structured_market


def filter_recall_result_market(result: dict[str, Any], value: Any) -> dict[str, Any]:
    """Apply an explicit country market as a hard filter to evidence results."""
    raw_value = str(value or "").strip()
    requested = _MARKET_ALIASES.get(raw_value.lower(), "")
    if not raw_value:
        return result
    if not requested:
        filtered = dict(result)
        filtered["items"] = []
        original_buckets = result.get("buckets") if isinstance(result.get("buckets"), dict) else {}
        filtered["buckets"] = {
            key: [] for key, values in original_buckets.items() if isinstance(values, list)
        }
        diagnostics = dict(result.get("diagnostics")) if isinstance(result.get("diagnostics"), dict) else {}
        diagnostics.update({
            "returned_count": 0,
            "creator_returned": 0,
            "reviewer_returned": 0,
            "market_filtered_out": len(result.get("items") or []),
            "market_filter": "invalid",
            "empty_reason": "invalid_market_constraint",
        })
        filtered["diagnostics"] = diagnostics
        filtered["match_status"] = "empty"
        filtered["candidate_set_distribution"] = candidate_set_distribution_from_items([])
        filtered["market_filter"] = {"applied": True, "requested": "invalid", "invalid": True}
        return filtered
    items = result.get("items") if isinstance(result.get("items"), list) else []

    def keep(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        qualification = (
            item.get("qualification_evidence")
            if isinstance(item.get("qualification_evidence"), dict)
            else {}
        )
        qualified_market = (
            qualification.get("market")
            if isinstance(qualification.get("market"), dict)
            else {}
        )
        if qualified_market.get("passed") and _text(qualified_market.get("value")).lower() == requested:
            return True
        facets = item.get("candidate_facets") if isinstance(item.get("candidate_facets"), dict) else {}
        raw_country = str(facets.get("country") or "").strip().lower()
        country = _MARKET_ALIASES.get(raw_country, raw_country if re.fullmatch(r"[a-z]{2}", raw_country) else "")
        return country == requested

    filtered = dict(result)
    filtered["items"] = [item for item in items if keep(item)]
    original_buckets = result.get("buckets") if isinstance(result.get("buckets"), dict) else {}
    filtered["buckets"] = {
        key: [item for item in values if keep(item)]
        for key, values in original_buckets.items()
        if isinstance(values, list)
    }
    diagnostics = dict(result.get("diagnostics")) if isinstance(result.get("diagnostics"), dict) else {}
    after_count = len(filtered["items"])
    diagnostics.update(
        {
            "returned_count": after_count,
            "creator_returned": len(filtered["buckets"].get("creator") or []),
            "reviewer_returned": len(filtered["buckets"].get("reviewer") or []),
            "market_filtered_out": max(0, len(items) - after_count),
            "market_filter": requested,
        }
    )
    before_count = len(items)
    if diagnostics.get("evidence_gate_enabled"):
        filtered["match_status"] = "matched" if after_count else "empty"
        if after_count:
            diagnostics["empty_reason"] = ""
        elif before_count > 0:
            diagnostics["empty_reason"] = "no_market_evidence_match"
        filtered["candidate_set_distribution"] = candidate_set_distribution_from_items(filtered["items"])
    filtered["diagnostics"] = diagnostics
    filtered["market_filter"] = {"applied": True, "requested": requested}
    return filtered
