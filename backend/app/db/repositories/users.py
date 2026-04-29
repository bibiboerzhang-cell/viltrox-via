"""
db/repositories/users.py — 用户表 CRUD
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime


logger = get_logger(__name__)


def generate_creator_code(conn, *, offset: int = 0) -> str:
    row = conn.execute(
        """
        SELECT creator_code
        FROM users
        WHERE creator_code LIKE 'V_%%'
        ORDER BY creator_code DESC
        LIMIT 1
        """
    ).fetchone()
    next_id = 1
    if row:
        match = re.match(r"^V_(\d+)$", str(row["creator_code"] or "").strip())
        if match:
            next_id = int(match.group(1)) + 1
    next_id += max(0, int(offset))
    return f"V_{next_id:06d}"


def creator_code_exists(conn, creator_code: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM users WHERE creator_code=? LIMIT 1",
        (str(creator_code or "").strip(),),
    ).fetchone()
    return bool(row)


def lookup_user_by_social(platform: str, handle: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT user_id FROM user_social_accounts WHERE platform=? AND handle=? AND verified=1",
        (platform, handle),
    ).fetchone()
    return row["user_id"] if row else None


def get_user_by_email(email: str):
    conn = get_conn()
    return conn.execute(
        "SELECT * FROM users WHERE email=?",
        (str(email or "").lower().strip(),),
    ).fetchone()


def sanitize_social_identity(platform: str, handle: str) -> tuple[str, str, bool]:
    from app.services.verification.viltrox_official import (
        detect_platform_from_profile_url,
        extract_handle_from_profile_url,
        normalize_claimed_handle,
    )

    normalized_platform = str(platform or "").strip().lower()
    raw_handle = str(handle or "").strip()
    if not normalized_platform or not raw_handle:
        return normalized_platform, "", False

    maybe_url = raw_handle.lstrip("@")
    if maybe_url.startswith("http://") or maybe_url.startswith("https://"):
        detected_platform = detect_platform_from_profile_url(maybe_url)
        if not detected_platform:
            return normalized_platform, "", False
        extracted_handle = extract_handle_from_profile_url(maybe_url, detected_platform)
        if not extracted_handle:
            return detected_platform, "", False
        return detected_platform, normalize_claimed_handle(extracted_handle, detected_platform), True

    normalized_handle = normalize_claimed_handle(raw_handle, normalized_platform)
    return normalized_platform, normalized_handle, bool(normalized_handle)


def touch_user_last_login(user_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE users SET last_login=? WHERE id=?",
        (datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), int(user_id)),
    )
    conn.commit()
    return cur.rowcount == 1


def upsert_pending_social_account(
    *,
    user_id: int,
    platform: str,
    handle: str,
    verify_code: str,
) -> int:
    platform, handle, valid = sanitize_social_identity(platform, handle)
    if not valid:
        raise ValueError("Could not extract a valid profile handle from that input")
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    existing = conn.execute(
        "SELECT id,user_id,verified FROM user_social_accounts WHERE platform=? AND handle=?",
        (platform, handle),
    ).fetchone()
    if existing and int(existing["verified"] or 0) == 1 and int(existing["user_id"] or 0) != int(user_id):
        raise ValueError("This account is already linked to another user")
    if existing:
        conn.execute(
            """
            UPDATE user_social_accounts
            SET user_id=?, verify_code=?, verified=0, verified_at=NULL
            WHERE id=?
            """,
            (int(user_id), verify_code, int(existing["id"])),
        )
        account_id = int(existing["id"])
    else:
        sql = """
            INSERT INTO user_social_accounts
            (user_id, platform, handle, verified, verify_code, created_at)
            VALUES (?, ?, ?, 0, ?, ?)
        """
        params = (int(user_id), platform, handle, verify_code, now)
        if is_postgres_runtime():
            cursor = conn.execute(sql + " RETURNING id", params)
            row = cursor.fetchone()
            account_id = int(row["id"]) if row else 0
        else:
            cursor = conn.execute(sql, params)
            account_id = int(cursor.lastrowid or 0)
    conn.commit()
    if existing and int(existing["verified"] or 0) == 1 and int(existing["user_id"] or 0) == int(user_id):
        refresh_user_social_verified(
            int(user_id),
            reason="social_verification_reset",
            context={"platform": platform, "handle": handle},
        )
    return account_id


def refresh_user_social_verified(
    user_id: int,
    *,
    reason: str = "social_verified_sync",
    context: dict[str, Any] | None = None,
) -> bool:
    conn = get_conn()
    verified_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM user_social_accounts WHERE user_id=? AND verified=1",
            (int(user_id),),
        ).fetchone()[0]
        or 0
    )
    has_verified = verified_count > 0
    conn.execute("UPDATE users SET social_verified=? WHERE id=?", (1 if has_verified else 0, int(user_id)))
    conn.commit()
    try:
        from app.services.trust import persist_trust_score

        persist_trust_score(
            int(user_id),
            reason=reason,
            context={
                "verified_accounts": verified_count,
                **(context or {}),
            },
        )
    except Exception as exc:
        logger.warning("social verify trust sync failed | user_id=%s | error=%s", user_id, exc)
    return has_verified


def mark_social_account_verified(
    *,
    user_id: int,
    platform: str,
    handle: str,
    verified_at: str | None = None,
) -> bool:
    conn = get_conn()
    now = str(verified_at or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    cur = conn.execute(
        """
        UPDATE user_social_accounts
        SET verified=1, verified_at=?, verify_code=COALESCE(verify_code, '')
        WHERE user_id=?
          AND LOWER(platform)=LOWER(?)
          AND LOWER(REPLACE(handle, '@', ''))=LOWER(REPLACE(?, '@', ''))
        """,
        (now, int(user_id), platform, handle),
    )
    conn.commit()
    if cur.rowcount:
        refresh_user_social_verified(
            int(user_id),
            reason="social_verified",
            context={"platform": platform, "handle": handle},
        )
    return cur.rowcount > 0


def backfill_user_social_verified_flags() -> int:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT u.id
        FROM users u
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS verified_count
            FROM user_social_accounts
            WHERE verified=1
            GROUP BY user_id
        ) social ON social.user_id = u.id
        WHERE COALESCE(u.social_verified, 0) != CASE WHEN COALESCE(social.verified_count, 0) > 0 THEN 1 ELSE 0 END
        """
    ).fetchall()
    user_ids = [int(row["id"]) for row in rows]
    for user_id in user_ids:
        refresh_user_social_verified(user_id, reason="social_verified_backfill")
    return len(user_ids)
