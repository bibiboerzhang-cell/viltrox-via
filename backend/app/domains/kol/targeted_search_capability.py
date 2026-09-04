"""Lens capability classification for prospective KOL discovery."""
from __future__ import annotations

import re
from typing import Any, Iterable


FOCAL_LENGTH_RE = re.compile(
    r"(?<![a-z0-9])(?P<low>\d{1,3})(?:\s*-\s*(?P<high>\d{1,3}))?\s*mm(?![a-z0-9])",
    flags=re.IGNORECASE,
)
APERTURE_RE = re.compile(
    r"(?<![a-z0-9])(?:f|t)\s*/?\s*\d+(?:\.\d+)?(?![a-z0-9])",
    flags=re.IGNORECASE,
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def lens_focal_span(value: str) -> tuple[int | None, int | None]:
    matches = list(FOCAL_LENGTH_RE.finditer(value))
    if not matches:
        return None, None
    values: list[int] = []
    for match in matches:
        values.append(int(match.group("low")))
        if match.group("high"):
            values.append(int(match.group("high")))
    return min(values), max(values)


def lens_focals(value: str) -> list[int]:
    values: set[int] = set()
    for match in FOCAL_LENGTH_RE.finditer(value):
        values.add(int(match.group("low")))
        if match.group("high"):
            values.add(int(match.group("high")))
    return sorted(values)


def fast_lens_aperture(value: str) -> bool:
    for match in APERTURE_RE.finditer(value):
        token = match.group(0).lower().replace(" ", "")
        if not token.startswith("f"):
            continue
        number = re.sub(r"^[ft]/?", "", token)
        try:
            if float(number) <= 1.4:
                return True
        except ValueError:
            continue
    return False


def _is_multi_focal_set(normalized: str, focals: list[int]) -> bool:
    return len(focals) >= 3 and any(
        term in normalized for term in (" set", " kit", "套装", "整套", "全套")
    )


def prospective_lens_capability(
    value: str,
    *,
    operator_segments: Iterable[Any] = (),
) -> str:
    """Map product facts to deterministic prospective-use terms."""

    normalized = _text(value).lower()
    has_macro = any(term in normalized for term in ("macro", "micro lens", "微距"))
    has_cine = any(term in normalized for term in ("anamorphic", "cine", "cinema", "电影", "影视"))
    focals = lens_focals(normalized)
    if has_cine and _is_multi_focal_set(normalized, focals):
        return "cinema lens"
    if has_macro and has_cine:
        return "macro cinema lens"
    if has_macro:
        return "macro lens"
    if has_cine:
        return "cinema lens"
    if any(term in normalized for term in ("ultra-wide", "ultrawide", "ultra wide", "super wide", "超广")):
        return "ultra-wide lens"
    if any(term in normalized for term in ("telephoto", "长焦")):
        return "telephoto portrait lens"

    focal_min, focal_max = lens_focal_span(normalized)
    intent_blob = " ".join(
        _text(segment.get("key") or segment.get("query_term") or segment.get("label"))
        if isinstance(segment, dict)
        else _text(segment)
        for segment in operator_segments
    ).lower()
    portrait_intent = any(
        term in intent_blob for term in ("portrait", "wedding", "人像", "婚礼")
    )
    if portrait_intent and focal_min is not None and focal_max is not None:
        if focal_min >= 85:
            return "telephoto portrait lens"
        if 28 <= focal_min and focal_max <= 100:
            return "portrait lens"
    if any(term in normalized for term in ("portrait", "人像")):
        return "portrait lens"
    if (
        focal_min is not None
        and focal_max is not None
        and 28 <= focal_min <= 84
        and focal_max <= 100
        and fast_lens_aperture(normalized)
    ):
        return "portrait lens"
    if focal_min is not None and focal_max is not None:
        if focal_min <= 20:
            return "ultra-wide lens"
        if focal_max < 35:
            return "wide-angle lens"
        if focal_min >= 200:
            return "super-telephoto lens"
        if focal_min >= 85:
            return "telephoto portrait lens"
        if 50 <= focal_min <= 84 and focal_max <= 100:
            return "portrait lens"
    return "camera lens"
