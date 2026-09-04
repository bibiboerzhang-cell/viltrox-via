"""Token and model-code parsing primitives for the product resolver."""
from __future__ import annotations

import re
from typing import Any


STOPWORDS = frozenset(
    {
        "mm", "f", "the", "a", "for", "and", "lens", "camera", "viltrox", "af",
        "pl", "t", "x", "full", "frame", "inch", "kit", "set", "new",
    }
)
COMPACT_PRO_RE = re.compile(r"(?<![a-z0-9])([a-z]\d{1,3})\s*pro(?![a-z0-9])")
VILTROX_Z_MODEL_CONTEXT_RE = re.compile(
    r"\bviltrox\b|\bvintage\b|唯卓仕|维卓仕?",
    re.IGNORECASE,
)
NIKON_CAMERA_CONTEXT_RE = re.compile(
    r"(?<![a-z0-9])nikon(?![a-z0-9])|尼康",
    re.IGNORECASE,
)
_MODEL_CODE_RE = re.compile(
    r"(?<![a-z0-9])(?P<code>(?:dc|af|mf|ef|nf|dg|vl|epic|z)[-_ ]?(?:[a-z]*\d[a-z0-9]*))(?![a-z0-9])",
    re.IGNORECASE,
)
_MODEL_CODE_PREFIXES = ("epic", "dc", "af", "mf", "ef", "nf", "dg", "vl", "z")
_APERTURE_RE = re.compile(
    r"(?<![a-z0-9])(?P<kind>[ft])\s*/?\s*(?P<value>\d{1,2}(?:\.\d+)?)(?![a-z0-9])",
    re.IGNORECASE,
)
_CHINESE_APERTURE_RE = re.compile(
    r"(?<![\d.])(?P<value>\d{1,2}\.\d+)\s*光圈(?![\d.])",
    re.IGNORECASE,
)


def normkey(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def split_glued(low: str) -> str:
    # "65macro" → "65 macro", "550pro" → "550 pro", "z1" stays "z 1" only at boundaries.
    spaced = re.sub(r"(?<=[0-9])(?=[a-z])", " ", low)
    spaced = re.sub(r"(?<=[a-z])(?=[0-9])", " ", spaced)
    return spaced


def query_tokens(query: str) -> list[str]:
    spaced = split_glued(str(query or "").lower())
    return [tok for tok in re.split(r"[^a-z0-9.]+", spaced) if tok]


def model_code_mentions(value: Any) -> list[tuple[str, str]]:
    """Return ``(normalised, display)`` product-code mentions in input order."""

    source_text = str(value or "")
    output: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _MODEL_CODE_RE.finditer(source_text):
        raw = match.group("code")
        normalized = normkey(raw)
        if re.fullmatch(r"(?:af|mf)\d{1,3}mm", normalized):
            continue
        if (
            re.fullmatch(r"z\d+[a-z0-9]*", normalized)
            and (
                not re.fullmatch(r"z1(?:pro[a-z0-9]*)?", normalized)
                or NIKON_CAMERA_CONTEXT_RE.search(source_text)
            )
            and not VILTROX_Z_MODEL_CONTEXT_RE.search(source_text)
        ):
            continue
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        display = re.sub(r"[-_ ]+", "-", raw).upper()
        if not re.search(r"[-_ ]", raw):
            prefix = next(
                (item for item in _MODEL_CODE_PREFIXES if normalized.startswith(item) and len(normalized) > len(item)),
                "",
            )
            if prefix and prefix != "z":
                display = f"{prefix.upper()}-{normalized[len(prefix):].upper()}"
        output.append((normalized, display))
    return output


def query_model_codes(value: Any) -> list[str]:
    return [normalized for normalized, _display in model_code_mentions(value)]


def model_code_score_tokens(model_codes: list[str]) -> list[str]:
    output = list(model_codes)
    for code in model_codes:
        prefix = next(
            (item for item in _MODEL_CODE_PREFIXES if code.startswith(item) and len(code) > len(item)),
            "",
        )
        if len(prefix) >= 2:
            output.append(prefix)
    return output


def query_apertures(value: Any) -> set[tuple[str, float]]:
    apertures: set[tuple[str, float]] = set()
    for match in _APERTURE_RE.finditer(str(value or "")):
        try:
            apertures.add((match.group("kind").lower(), round(float(match.group("value")), 3)))
        except (TypeError, ValueError):
            continue
    for match in _CHINESE_APERTURE_RE.finditer(str(value or "")):
        try:
            apertures.add(("f", round(float(match.group("value")), 3)))
        except (TypeError, ValueError):
            continue
    return apertures


def looks_like_bare_sku(value: Any) -> bool:
    """Limit full-catalog exact checks to short operator-typed model codes."""

    text = str(value or "").strip()
    return bool(
        len(text.split()) <= 2
        and re.fullmatch(r"[a-z][a-z0-9._/+ -]*", text, re.IGNORECASE)
        and any(char.isdigit() for char in text)
    )
