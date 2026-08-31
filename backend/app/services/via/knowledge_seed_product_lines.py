"""DOCX product-line parsing helpers for Via knowledge seeding."""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable


def looks_like_product_model(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    if text.startswith("（") and text.endswith("）"):
        return False
    return bool(
        re.search(r"\b(AF|EPIC|LUNA)\b", text, flags=re.IGNORECASE)
        or re.search(r"\b\d{1,3}mm\b", text, flags=re.IGNORECASE)
        or re.search(r"\bT\d", text, flags=re.IGNORECASE)
        or "Chip" in text
    )


def _matching_alias(clean: str, aliases: dict[str, set[str]]) -> str:
    return next(
        (
            key
            for key, names in aliases.items()
            if any(clean.lower() == str(name).lower() for name in names)
        ),
        "",
    )


def parse_product_line_lines(
    lines: Iterable[str],
    aliases: dict[str, set[str]],
    *,
    is_guide_heading: Callable[[str], bool],
    looks_like_model: Callable[[str], bool],
) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    current_key = ""
    current_model_index = -1
    for line in lines:
        clean = str(line or "").strip()
        if not clean:
            continue
        matched_key = _matching_alias(clean, aliases)
        if matched_key:
            current_key = matched_key
            current_model_index = -1
            found.setdefault(
                current_key,
                {"name": clean, "summary": "", "models": [], "notes": []},
            )
            continue
        if is_guide_heading(clean):
            current_key = ""
            current_model_index = -1
            continue
        if not current_key:
            continue
        entry = found[current_key]
        if not entry.get("summary") and ("系列" in clean or "产品线" in clean or "镜头" in clean):
            entry["summary"] = clean
            continue
        if looks_like_model(clean):
            if clean not in entry["models"] and len(entry["models"]) < 18:
                entry["models"].append(clean)
                current_model_index = len(entry["models"]) - 1
            continue
        if clean.startswith("（") and clean.endswith("）") and 0 <= current_model_index < len(entry["models"]):
            model = entry["models"][current_model_index]
            entry["models"][current_model_index] = f"{model} {clean}"
            continue
        if len(entry["notes"]) < 8:
            entry["notes"].append(clean)
    return found
