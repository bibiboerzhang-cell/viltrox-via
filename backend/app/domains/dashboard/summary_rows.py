"""Dashboard summary row/value helpers (moved from summary.py, behavior unchanged)."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.core.logging import get_logger

logger = get_logger(__name__)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: _row_value(row, key) for key in row.keys()}
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    try:
        return dict(row)
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return {}


def _fetch_dicts(sql: str) -> list[dict[str, Any]]:
    return [_row_dict(row) for row in get_conn().execute(sql).fetchall()]


def _metric_item(row: dict[str, Any], metric_key: str) -> dict[str, Any]:
    return {
        "kol_id": _as_int(row.get("kol_id")),
        "kol_name": row.get("kol_name"),
        "handle": row.get("handle"),
        "profile_url": row.get("profile_url"),
        "platform": row.get("platform"),
        "title": row.get("title"),
        "url": row.get("url"),
        "value": _as_int(row.get(metric_key)),
        "view_count": _as_int(row.get("view_count")),
        "like_count": _as_int(row.get("like_count")),
        "comment_count": _as_int(row.get("comment_count")),
        "publish_date": row.get("publish_date"),
    }
