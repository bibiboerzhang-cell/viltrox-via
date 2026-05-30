"""Junk row/token detection for generated placeholder content."""
from __future__ import annotations

from typing import Any


JUNK_MARKERS = (
    "<|",
    "|>",
    "无有效信息",
    "无法按照要求",
    "无法完成",
    "仅根据现有信息无法",
)


def is_junk_value(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return any(marker in text for marker in JUNK_MARKERS)


def is_junk_row(row: dict[str, Any]) -> bool:
    values = [value for key, value in row.items() if not key.startswith("__") and str(value or "").strip()]
    if not values:
        return True
    return all(is_junk_value(value) for value in values)
