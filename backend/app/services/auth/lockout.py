from __future__ import annotations

from datetime import datetime, timedelta

from app.db.connection import get_conn

MAX_FAILED = 5
LOCKOUT_MINUTES = 15


def _utcnow_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate_ip(ip: str) -> str:
    value = str(ip or "").strip()
    if not value:
        return ""
    if ":" in value:
        parts = value.split(":")
        return ":".join(parts[:4])
    parts = value.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".0"
    return value[:64]


def record_failed_login(user_id: int, ip: str = "") -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO failed_logins (user_id, attempted_at, ip_truncated)
        VALUES (?, ?, ?)
        """,
        (int(user_id), _utcnow_text(), _truncate_ip(ip)),
    )
    conn.commit()


def is_locked_out(user_id: int) -> bool:
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(minutes=LOCKOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM failed_logins
        WHERE user_id=? AND attempted_at>=?
        """,
        (int(user_id), cutoff),
    ).fetchone()
    return int(row["count"] if row and "count" in row.keys() else row[0] if row else 0) >= MAX_FAILED


def clear_failed(user_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM failed_logins WHERE user_id=?", (int(user_id),))
    conn.commit()
