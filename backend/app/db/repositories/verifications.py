"""
db/repositories/verifications.py — 验证记录 CRUD
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.core.config import VERIFY_CODE_TTL_HOURS
from app.db.connection import get_conn, is_postgres_runtime


def create_verification_request(
    *,
    user_id: int,
    platform: str,
    handle: str,
    code: str,
    profile_url: str,
    generated_comment: str,
    expires_at: str,
    note: str = "",
    status: str = "pending",
) -> int:
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    params = (
        int(user_id),
        platform,
        handle,
        code,
        status,
        profile_url,
        handle,
        generated_comment,
        0,
        now,
        expires_at,
        note,
    )
    sql = """
        INSERT INTO verifications (
            user_id, platform, handle, code, status,
            profile_url, baseline_username, generated_comment,
            scan_count, created_at, expires_at, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    if is_postgres_runtime():
        cursor = conn.execute(sql + " RETURNING id", params)
        row = cursor.fetchone()
        verification_id = int(row["id"]) if row else 0
    else:
        cursor = conn.execute(sql, params)
        verification_id = int(cursor.lastrowid)
    conn.commit()
    return verification_id


def build_verification_expiry() -> str:
    expires_at = datetime.utcnow() + timedelta(hours=max(1, int(VERIFY_CODE_TTL_HOURS)))
    return expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def update_verification_generated_comment(
    verification_id: int,
    generated_comment: str,
) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE verifications SET generated_comment=? WHERE id=?",
        (generated_comment, int(verification_id)),
    )
    conn.commit()
    return cur.rowcount == 1
