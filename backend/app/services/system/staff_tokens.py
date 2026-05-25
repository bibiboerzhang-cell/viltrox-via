"""Audit-log and API-token helpers for staff administration."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


# =========================================================================
# Audit log
# =========================================================================

def get_audit_log(
    *,
    actor_id: int | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
) -> dict:
    conn = get_conn()
    where, params = [], []
    if actor_id:
        where.append("actor_id = ?"); params.append(actor_id)
    if action:
        where.append("action = ?"); params.append(action)
    if target_type:
        where.append("target_type = ?"); params.append(target_type)
    if target_id:
        where.append("target_id = ?"); params.append(target_id)
    if from_date:
        where.append("occurred_at >= ?"); params.append(from_date)
    if to_date:
        where.append("occurred_at <= ?"); params.append(to_date)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT * FROM admin_audit_log {where_sql}
            ORDER BY occurred_at DESC LIMIT ?""",
        [*params, limit],
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        if item.get("detail_json"):
            try:
                item["detail"] = json.loads(item["detail_json"])
            except Exception:
                logger.debug(
                    "staff.audit_detail_json_parse_failed",
                    extra={"audit_log_id": item.get("id")},
                    exc_info=True,
                )
        out.append(item)
    return {"entries": out}


# =========================================================================
# API tokens
# =========================================================================

def list_api_tokens() -> dict:
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, token_prefix, name, scope, created_by, created_at,
                  last_used_at, last_used_ip, expires_at, revoked_at, active
           FROM api_tokens ORDER BY active DESC, created_at DESC"""
    ).fetchall()
    return {"tokens": [dict(r) for r in rows]}


def create_api_token(body: dict, *, created_by: int) -> dict:
    """Generate + hash + insert. Returns full token ONLY this one time."""
    name = body.get("name")
    scope = body.get("scope", "readonly")
    ttl_days = body.get("expires_days", 90)

    if not name:
        raise ValueError("name required")
    if scope not in {"admin", "readonly", "ci"}:
        raise ValueError("scope must be admin|readonly|ci")

    raw_tail = secrets.token_urlsafe(32)
    full_token = f"sk_vos_{raw_tail}"
    prefix = full_token[:12]  # 'sk_vos_AAAA'
    token_hash = _hash_token(full_token)

    expires_at = None
    if ttl_days:
        expires_at = (datetime.utcnow() + timedelta(days=ttl_days)).isoformat()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO api_tokens
            (token_prefix, token_hash, name, scope, created_by, expires_at)
           VALUES (?,?,?,?,?,?)""",
        (prefix, token_hash, name, scope, created_by, expires_at),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "token": full_token,          # show to user ONCE
        "prefix": prefix,
        "name": name,
        "scope": scope,
        "expires_at": expires_at,
    }


def revoke_api_token(token_id: int, admin_id: int) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE api_tokens SET active = 0,
            revoked_at = datetime('now'), revoked_by = ?
           WHERE id = ?""",
        (admin_id, token_id),
    )
    conn.commit()


def verify_token(raw_token: str) -> dict | None:
    """Lookup + verify. Call from a middleware that inspects Authorization header."""
    conn = get_conn()
    prefix = raw_token[:12]
    rows = conn.execute(
        "SELECT * FROM api_tokens WHERE token_prefix = ? AND active = 1",
        (prefix,),
    ).fetchall()
    for r in rows:
        if _verify_token(raw_token, r["token_hash"]):
            # Check expiry
            if r["expires_at"]:
                try:
                    if datetime.fromisoformat(r["expires_at"]) < datetime.utcnow():
                        return None
                except Exception:
                    logger.warning(
                        "staff.api_token_expiry_parse_failed",
                        extra={"token_id": r["id"]},
                        exc_info=True,
                    )
                    return None
            # Update last_used
            conn.execute(
                "UPDATE api_tokens SET last_used_at = datetime('now') WHERE id = ?",
                (r["id"],),
            )
            conn.commit()
            return dict(r)
    return None


def _hash_token(raw: str) -> str:
    """Use argon2 if available; fallback to sha256."""
    try:
        from argon2 import PasswordHasher
        return PasswordHasher().hash(raw)
    except ImportError:
        import hashlib
        return "sha256$" + hashlib.sha256(raw.encode()).hexdigest()


def _verify_token(raw: str, stored: str) -> bool:
    if stored.startswith("sha256$"):
        import hashlib
        return "sha256$" + hashlib.sha256(raw.encode()).hexdigest() == stored
    try:
        from argon2 import PasswordHasher
        from argon2.exceptions import VerifyMismatchError
        try:
            PasswordHasher().verify(stored, raw)
            return True
        except VerifyMismatchError:
            return False
    except ImportError:
        return False
