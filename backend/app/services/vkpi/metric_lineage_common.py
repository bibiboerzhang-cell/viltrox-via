"""Shared helpers for V-KPI metric lineage snapshots."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from typing import Any

def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default

def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default

def _generate_run_uid() -> str:
    return f"mr-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"

def _window_bounds(period_days: int) -> tuple[str, str]:
    end = datetime.utcnow()
    start = end - timedelta(days=max(1, int(period_days)))
    return (
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value or "{}")
        return loaded if isinstance(loaded, dict) else {}
    except (TypeError, ValueError):
        return {}
