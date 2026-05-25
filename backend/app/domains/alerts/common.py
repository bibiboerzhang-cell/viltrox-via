"""Shared alert persistence helpers."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.shared.vkpi_utils import utcnow_iso


def utcnow() -> str:
    return utcnow_iso()


def table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)


def resolve_open_alert(conn: Any, alert_key: str) -> bool:
    now = utcnow()
    existing_open = conn.execute(
        "SELECT id FROM vkpi_alerts WHERE alert_key=? AND status='open'",
        (alert_key,),
    ).fetchone()
    conn.execute(
        """
        UPDATE vkpi_alerts
        SET status='resolved', resolved_at=?, updated_at=?
        WHERE alert_key=? AND status='open'
        """,
        (now, now, alert_key),
    )
    return bool(existing_open)
