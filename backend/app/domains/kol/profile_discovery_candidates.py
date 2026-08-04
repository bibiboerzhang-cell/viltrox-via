"""Candidate normalization and platform filtering for profile discovery."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

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


def _is_own_brand_account(item: dict[str, Any]) -> bool:
    for field in (
        "handle", "channel_handle", "username", "display_name", "channel_name", "author_name",
        "profile_url", "channel_url", "url",
    ):
        raw = str(item.get(field) or "")
        # 从 URL 尾段/@handle 里提取账号名,再剥非字母数字。
        tail = re.split(r"[/@]", raw)[-1] if raw else ""
        norm = re.sub(r"[^a-z0-9]", "", tail.lower())
        if norm and any(norm.startswith(p) for p in _OWN_BRAND_PREFIXES):
            return True
    return False


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
    filtered["platform_filter"] = {
        "applied": True,
        "requested": sorted(requested),
        "filtered_out": max(0, before_count - after_count),
    }
    return filtered
