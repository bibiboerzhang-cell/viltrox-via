"""Small shared helpers for V-KPI service modules.

Keep this module intentionally boring: only behavior-preserving conversions
belong here. Business scoring, schema checks, and write policy stay in their
own services.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def json_loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def to_text(value: Any) -> str:
    return str(value or "").strip()


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def row_to_dict(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return dict(row.items()) if hasattr(row, "items") else dict(row)
