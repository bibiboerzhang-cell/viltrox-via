"""KOL claim query helpers."""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn


logger = get_logger(__name__)


def safe_json_loads(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception as exc:
        logger.warning("kol claims json parse failed: %s", exc)
        return fallback


def rows_or_empty(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        logger.warning("kol claims rows query failed: %s", exc)
        return []


def row_or_empty(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    conn = get_conn()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception as exc:
        logger.warning("kol claims row query failed: %s", exc)
        return {}
