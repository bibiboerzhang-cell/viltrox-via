"""Operator-owned objective, platform, and follower filters for KOL search."""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


DEFAULT_OBJECTIVE = "prospective_growth"
PROSPECTIVE_GROWTH = DEFAULT_OBJECTIVE
EXISTING_EVIDENCE = "existing_evidence"
SUPPORTED_OBJECTIVES = frozenset({PROSPECTIVE_GROWTH, EXISTING_EVIDENCE})
SUPPORTED_PLATFORMS = frozenset({"youtube", "instagram", "tiktok"})

_COUNT_TOKEN = r"(?P<{name}>\d+(?:\.\d+)?)\s*(?P<{name}_suffix>百万|万|千|[kwm])?"
_FOLLOWER_CONTEXT_RE = re.compile(r"粉丝|粉|关注者|followers?|audience", re.IGNORECASE)
_RANGE_SEP = r"(?:-|–|—|~|～|至|到|to)"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            output.append(item)
    return output


def normalize_objective(*values: Any) -> str:
    """Return a supported objective; unspecified values use prospective growth."""

    aliases = {
        "prospective": PROSPECTIVE_GROWTH,
        "growth": PROSPECTIVE_GROWTH,
        "market_growth": PROSPECTIVE_GROWTH,
        "potential_users": PROSPECTIVE_GROWTH,
        "existing": EXISTING_EVIDENCE,
        "existing_user": EXISTING_EVIDENCE,
        "brand_evidence": EXISTING_EVIDENCE,
    }
    for value in values:
        if isinstance(value, dict):
            value = value.get("objective") or value.get("search_objective") or value.get("searchObjective")
        candidate = _text(value).lower()
        if candidate in SUPPORTED_OBJECTIVES:
            return candidate
        if candidate in aliases:
            return aliases[candidate]
    return DEFAULT_OBJECTIVE


def _count_value(number: str, suffix: str = "", *, inherited_suffix: str = "") -> int | None:
    try:
        value = float(number)
    except (TypeError, ValueError):
        return None
    multiplier = {
        "": 1,
        "k": 1_000,
        "千": 1_000,
        "w": 10_000,
        "万": 10_000,
        "m": 1_000_000,
        "百万": 1_000_000,
    }.get((suffix or inherited_suffix or "").lower())
    if multiplier is None:
        return None
    parsed = int(value * multiplier)
    return parsed if 0 <= parsed <= 100_000_000 else None


def _parse_count_token(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = int(value)
        return parsed if 0 <= parsed <= 100_000_000 else None
    match = re.fullmatch(
        _COUNT_TOKEN.format(name="value"),
        _text(value).lower().replace(",", ""),
        flags=re.IGNORECASE,
    )
    return (
        _count_value(match.group("value"), match.group("value_suffix") or "")
        if match else None
    )


def _filter_dict(body_or_filters: Any) -> dict[str, Any]:
    payload = body_or_filters if isinstance(body_or_filters, dict) else {}
    nested = payload.get("filters")
    return {**payload, **nested} if isinstance(nested, dict) else payload


def _filter_value(filters: dict[str, Any], keys: tuple[str, ...]) -> tuple[Any, bool]:
    for key in keys:
        if key in filters and filters.get(key) not in (None, ""):
            return filters.get(key), True
    return None, False


def operator_platforms(body: Any, fallback: Iterable[Any]) -> list[str]:
    """Keep an explicit operator platform facet authoritative in the plan."""

    raw, explicit = _filter_value(
        _filter_dict(body),
        ("platforms", "new_discovery_platforms", "discovery_platforms", "platform"),
    )
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    selected = _dedupe(
        _text(value).lower()
        for value in values
        if _text(value).lower() in SUPPORTED_PLATFORMS
    )
    return selected if explicit and selected else _dedupe(
        _text(value).lower() for value in fallback
    )


def _range_result(low: int | None, high: int | None, *, source: str, matched: str = "") -> dict[str, Any]:
    valid = low is not None and high is not None and low <= high
    return {
        "followers_min": low,
        "followers_max": high,
        "source": source,
        "locked": True,
        "valid": valid,
        "error": (
            "followers_min_exceeds_max"
            if low is not None and high is not None and low > high
            else ("invalid_follower_value" if not valid else "")
        ),
        "matched_text": matched,
    }


def _unspecified_range() -> dict[str, Any]:
    return {
        "followers_min": None,
        "followers_max": None,
        "source": "unspecified",
        "locked": False,
        "valid": True,
        "error": "",
        "matched_text": "",
    }


def parse_follower_range(query: Any = "", body_or_filters: Any = None) -> dict[str, Any]:
    """Parse an explicit follower interval without silently relaxing it."""

    filters = _filter_dict(body_or_filters)
    raw_min, has_min = _filter_value(
        filters, ("followers_min", "follower_min", "followersMin", "minFollowers")
    )
    raw_max, has_max = _filter_value(
        filters, ("followers_max", "follower_max", "followersMax", "maxFollowers")
    )
    if has_min or has_max:
        low = _parse_count_token(raw_min) if has_min else None
        high = _parse_count_token(raw_max) if has_max else None
        valid = (not has_min or low is not None) and (not has_max or high is not None)
        if valid and low is not None and high is not None and low > high:
            valid = False
        return {
            "followers_min": low,
            "followers_max": high,
            "source": "operator_filter",
            "locked": True,
            "valid": valid,
            "error": (
                "followers_min_exceeds_max"
                if low is not None and high is not None and low > high
                else ("invalid_follower_value" if not valid else "")
            ),
            "matched_text": "",
        }

    raw = _text(query).lower().replace(",", "")
    if not raw:
        return _unspecified_range()

    first = _COUNT_TOKEN.format(name="first")
    second = _COUNT_TOKEN.format(name="second")
    for pattern in (
        re.compile(rf"(?:between\s+)?{first}\s*(?:and|{_RANGE_SEP})\s*{second}", re.IGNORECASE),
        re.compile(rf"{first}\s*{_RANGE_SEP}\s*{second}", re.IGNORECASE),
    ):
        match = pattern.search(raw)
        if not match:
            continue
        first_suffix = match.group("first_suffix") or ""
        second_suffix = match.group("second_suffix") or ""
        if not _FOLLOWER_CONTEXT_RE.search(raw) and not (first_suffix or second_suffix):
            continue
        return _range_result(
            _count_value(match.group("first"), first_suffix, inherited_suffix=second_suffix),
            _count_value(match.group("second"), second_suffix, inherited_suffix=first_suffix),
            source="operator_text",
            matched=match.group(0),
        )

    token = _COUNT_TOKEN.format(name="value")
    bound_patterns = (
        (
            "followers_min",
            (
                re.compile(rf"(?:至少|不低于|大于|超过|more\s+than|over|at\s+least|min(?:imum)?\s*)\s*{token}", re.IGNORECASE),
                re.compile(rf"{token}\s*(?:以上|起|及以上|\+)", re.IGNORECASE),
            ),
        ),
        (
            "followers_max",
            (
                re.compile(rf"(?:不超过|低于|小于|少于|under|below|up\s+to|max(?:imum)?\s*)\s*{token}", re.IGNORECASE),
                re.compile(rf"{token}\s*(?:以下|以内|及以下)", re.IGNORECASE),
            ),
        ),
    )
    for bound, patterns in bound_patterns:
        for pattern in patterns:
            match = pattern.search(raw)
            if not match:
                continue
            suffix = match.group("value_suffix") or ""
            if not _FOLLOWER_CONTEXT_RE.search(raw) and not suffix:
                continue
            value = _count_value(match.group("value"), suffix)
            return {
                "followers_min": value if bound == "followers_min" else None,
                "followers_max": value if bound == "followers_max" else None,
                "source": "operator_text",
                "locked": True,
                "valid": value is not None,
                "error": "" if value is not None else "invalid_follower_value",
                "matched_text": match.group(0),
            }
    return _unspecified_range()


__all__ = [
    "DEFAULT_OBJECTIVE",
    "PROSPECTIVE_GROWTH",
    "EXISTING_EVIDENCE",
    "SUPPORTED_OBJECTIVES",
    "SUPPORTED_PLATFORMS",
    "normalize_objective",
    "operator_platforms",
    "parse_follower_range",
]
