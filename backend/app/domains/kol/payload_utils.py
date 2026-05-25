"""Shared KOL payload normalization helpers."""
from __future__ import annotations

import json
from typing import Any


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _json_loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _clamp_score(value: Any) -> int:
    return max(0, min(100, _int(value)))


def _grade(score: int) -> str:
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"
