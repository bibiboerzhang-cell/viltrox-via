"""Best-effort resume hints for bounded inventory selection, never spend authority.

A full page of invalid legacy URLs must not pin every scheduled run to the
same prefix. The existing persistent cache stores a continuation only after a
bounded scan found no usable candidate. Expiry or concurrent updates may cause
rescans, but cannot authorize provider work or bypass the durable daily cap.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger


logger = get_logger(__name__)
CACHE_KEY = "kol_search_inventory:scan_cursor:v1"
MAX_CURSOR_OFFSET = 10_000_000


def bounded_offset(value: Any) -> int:
    try:
        offset = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return offset if 0 <= offset <= MAX_CURSOR_OFFSET else 0


def _rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:
        logger.warning("inventory_scan_cursor.rollback_failed", exc_info=True)


def load_offset(conn: Any, *, as_of: datetime) -> tuple[int, str]:
    try:
        row = conn.execute(
            "SELECT value_json FROM persistent_cache "
            "WHERE cache_key=? AND expires_at>?",
            (CACHE_KEY, as_of),
        ).fetchone()
        raw = dict(row).get("value_json") if row else None
        if not raw:
            return 0, "missing"
        value = json.loads(raw)
        if not isinstance(value, dict):
            return 0, "invalid"
        return bounded_offset(value.get("next_offset")), "loaded"
    except (TypeError, ValueError):
        logger.warning("inventory_scan_cursor.invalid_value")
        return 0, "invalid"
    except Exception:
        _rollback(conn)
        logger.warning("inventory_scan_cursor.read_failed", exc_info=True)
        return 0, "unavailable"


def save_offset(conn: Any, offset: int, *, as_of: datetime) -> str:
    current = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    try:
        conn.execute(
            "INSERT INTO persistent_cache "
            "(cache_key, value_json, expires_at, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (cache_key) DO UPDATE SET "
            "value_json=excluded.value_json, expires_at=excluded.expires_at, "
            "created_at=excluded.created_at",
            (
                CACHE_KEY,
                json.dumps({"next_offset": bounded_offset(offset)}, separators=(",", ":")),
                current + timedelta(days=30),
                current,
            ),
        )
        conn.commit()
        return "saved"
    except Exception:
        _rollback(conn)
        logger.warning("inventory_scan_cursor.write_failed", exc_info=True)
        return "unavailable"
