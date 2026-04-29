"""
services/system/staff.py — Staff management (batch 5)

Domains:
  - Staff members (invite / update / suspend / reactivate)
  - Roles + permission matrix
  - Audit log queries
  - API tokens (argon2 hashed)
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


# Canonical role → permissions matrix (displayed on UI)
ROLES = {
    "admin": {
        "label": "Admin",
        "description": "Full access including staff + billing",
        "permissions": {
            "content":     ["view", "approve", "reject", "bulk"],
            "creators":    ["view", "edit", "block", "flag"],
            "commerce":    ["view", "approve_payouts", "process_payouts", "override_attribution"],
            "intelligence":["view", "generate_insights"],
            "via":         ["view", "approve_proposals", "edit_personas"],
            "system":      ["view", "edit_integrations", "manage_staff"],
            "trust":       ["view", "edit_rules", "block_user"],
            "staff":       ["view", "invite", "update", "suspend"],
        },
    },
    "operations": {
        "label": "Operations",
        "description": "Day-to-day content + creator ops",
        "permissions": {
            "content":     ["view", "approve", "reject", "bulk"],
            "creators":    ["view", "edit", "flag"],
            "commerce":    ["view"],
            "intelligence":["view"],
            "via":         ["view"],
            "system":      [],
            "trust":       ["view"],
            "staff":       [],
        },
    },
    "analyst": {
        "label": "Analyst",
        "description": "Read-only across all modules + can generate insights",
        "permissions": {
            "content":     ["view"],
            "creators":    ["view"],
            "commerce":    ["view"],
            "intelligence":["view", "generate_insights"],
            "via":         ["view"],
            "system":      ["view"],
            "trust":       ["view"],
            "staff":       [],
        },
    },
    "readonly": {
        "label": "Read-only",
        "description": "View only — no writes",
        "permissions": {
            "content":     ["view"],
            "creators":    ["view"],
            "commerce":    ["view"],
            "intelligence":["view"],
            "via":         ["view"],
            "system":      [],
            "trust":       [],
            "staff":       [],
        },
    },
}


# =========================================================================
# Members
# =========================================================================

def list_members() -> dict:
    conn = get_conn()
    rows = conn.execute(
        """SELECT s.*, u.creator_code AS user_handle, u.email AS user_email, u.name AS user_name
           FROM staff s LEFT JOIN users u ON s.user_id = u.id
           ORDER BY s.active DESC, s.id"""
    ).fetchall()
    members = []
    for r in rows:
        m = dict(r)
        if m.get("permissions_json"):
            try:
                m["permissions"] = json.loads(m["permissions_json"])
            except Exception:
                logger.debug(
                    "staff.permissions_json_parse_failed",
                    extra={"staff_id": m.get("id")},
                    exc_info=True,
                )
        members.append(m)
    if members:
        return {"members": members}

    # Compatibility fallback: surface existing admin-role accounts even if the
    # richer `staff` table has not been populated yet.
    bootstrap_rows = conn.execute(
        """
        SELECT
            u.id AS id,
            u.id AS user_id,
            CASE
                WHEN LOWER(COALESCE(u.role, '')) = 'admin' THEN 'admin'
                ELSE 'readonly'
            END AS role,
            NULL AS permissions_json,
            0 AS mfa_enabled,
            1 AS active,
            NULL AS invited_by,
            u.created_at AS invited_at,
            NULL AS accepted_at,
            u.last_login AS last_active_at,
            NULL AS suspended_at,
            NULL AS suspended_reason,
            u.creator_code AS user_handle,
            u.email AS user_email,
            u.name AS user_name
        FROM users u
        WHERE LOWER(COALESCE(u.role, '')) IN ('admin', 'ops', 'operations', 'analyst', 'readonly')
        ORDER BY
            CASE LOWER(COALESCE(u.role, ''))
                WHEN 'admin' THEN 0
                WHEN 'ops' THEN 1
                WHEN 'operations' THEN 1
                WHEN 'analyst' THEN 2
                ELSE 3
            END,
            u.created_at ASC
        """
    ).fetchall()
    return {"members": [dict(row) for row in bootstrap_rows]}


def invite(body: dict, *, inviter_id: int) -> dict:
    """
    Invite flow:
      1. If email matches existing user, link staff row to it.
      2. Else create a placeholder user (not implemented here) and link.
      3. Send email with magic link (out of scope — hook in your mailer).
    """
    conn = get_conn()
    email = body.get("email")
    role = body.get("role", "readonly")
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    if not email:
        raise ValueError("email required")

    user = conn.execute(
        "SELECT id FROM users WHERE email = ?", (email,)
    ).fetchone()
    user_id = user["id"] if user else None
    if not user_id:
        # Current auth gate is binary (`role == admin`), so invited staff
        # users need a real admin-role account alongside the richer `staff` row.
        base = "".join(ch for ch in email.split("@")[0].lower() if ch.isalnum()) or "staff"
        creator_code = _unique_placeholder_creator_code(conn, base)
        conn.execute(
            """
            INSERT INTO users
                (created_at, email, name, creator_code, status, role, email_verified)
            VALUES
                (datetime('now'), ?, ?, ?, 'active', 'admin', 0)
            """,
            (email, body.get("name") or email.split("@")[0], creator_code),
        )
        user_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO staff (user_id, role, permissions_json,
             mfa_enabled, active, invited_by, invited_at)
           VALUES (?, ?, ?, 0, 1, ?, datetime('now'))""",
        (
            user_id, role,
            json.dumps(body.get("permissions_override") or {}),
            inviter_id,
        ),
    )
    conn.commit()

    # TODO: send invite email with magic link
    return {"id": cur.lastrowid, "user_id": user_id, "role": role, "email": email}


def _unique_placeholder_creator_code(conn, base: str) -> str:
    seed = (base or "staff")[:12]
    candidate = f"staff_{seed}"
    suffix = 1
    while conn.execute(
        "SELECT 1 FROM users WHERE creator_code = ? LIMIT 1",
        (candidate,),
    ).fetchone():
        candidate = f"staff_{seed}_{suffix}"
        suffix += 1
    return candidate


def update(staff_id: int, body: dict) -> None:
    conn = get_conn()
    fields, params = [], []
    if "role" in body and body["role"] in ROLES:
        fields.append("role = ?"); params.append(body["role"])
    if "permissions_override" in body:
        fields.append("permissions_json = ?")
        params.append(json.dumps(body["permissions_override"]))
    if "mfa_enabled" in body:
        fields.append("mfa_enabled = ?")
        params.append(1 if body["mfa_enabled"] else 0)
    if not fields:
        return
    params.append(staff_id)
    conn.execute(f"UPDATE staff SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()


def suspend(staff_id: int, reason: str) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE staff SET active = 0,
            suspended_at = datetime('now'),
            suspended_reason = ?
           WHERE id = ?""",
        (reason, staff_id),
    )
    conn.commit()


def reactivate(staff_id: int) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE staff SET active = 1,
            suspended_at = NULL,
            suspended_reason = NULL
           WHERE id = ?""",
        (staff_id,),
    )
    conn.commit()


# =========================================================================
# Roles + matrix
# =========================================================================

def list_roles() -> dict:
    return {"roles": [{"key": k, **v} for k, v in ROLES.items()]}


def permission_matrix() -> dict:
    """Flat matrix display: {role: {module: [permissions]}}."""
    return {
        "modules": ["content", "creators", "commerce", "intelligence",
                    "via", "system", "trust", "staff"],
        "roles": {k: v["permissions"] for k, v in ROLES.items()},
    }


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
