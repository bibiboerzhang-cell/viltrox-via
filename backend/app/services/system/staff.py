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

from app.core.config import SITE_URL
from app.core.logging import get_logger
from app.core.permissions import (
    OWNER_EMAILS,
    SYSTEM_PERMISSION_KEYS,
    TAB_PERMISSION_KEYS,
    default_permissions_for_role,
    normalize_permissions,
)
from app.core.security import hash_password
from app.db.connection import get_conn
from app.services.auth.email import send_email
from app.services.auth.tokens import create_email_token

logger = get_logger(__name__)

ALLOWED_STAFF_EMAIL_DOMAINS = ["viltrox.com"]


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
        m["permissions"] = normalize_permissions(
            m.get("permissions") or m.get("permissions_json"),
            str(m.get("role") or "readonly"),
            owner=bool(m.get("is_owner")),
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
    email = str(body.get("email") or "").strip().lower()
    role = body.get("role", "readonly")
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    if not email:
        raise ValueError("email required")
    _validate_staff_email(email)

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

    owner = email in OWNER_EMAILS
    permissions = normalize_permissions(
        body.get("permissions") or body.get("permissions_override") or {},
        role,
        owner=owner,
    )
    cur = conn.cursor()
    columns = _staff_columns(conn)
    insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
    values: list[Any] = [user_id, role, json.dumps(permissions), 0, 1, inviter_id, _utcnow()]
    if "is_owner" in columns:
        insert_cols.append("is_owner"); values.append(1 if owner else 0)
    if "email_domain_verified" in columns:
        insert_cols.append("email_domain_verified"); values.append(1)
    if "invited_by_staff_id" in columns:
        insert_cols.append("invited_by_staff_id"); values.append(_staff_id_for_user(conn, inviter_id))
    placeholders = ",".join(["?"] * len(insert_cols))
    cur.execute(
        f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()

    token = create_email_token(int(user_id), "staff_invite")
    _send_staff_invite_email(email, token)
    return {
        "id": cur.lastrowid,
        "user_id": user_id,
        "role": role,
        "email": email,
        "invite_sent": True,
    }


def accept_invite(invite_token: str, password: str) -> dict:
    token = str(invite_token or "").strip()
    if not token:
        raise ValueError("invite_token required")
    if len(str(password or "")) < 8:
        raise ValueError("password must be at least 8 characters")
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, user_id, expires_at, used_at
        FROM email_tokens
        WHERE token = ? AND type = 'staff_invite'
        """,
        (token,),
    ).fetchone()
    if not row:
        raise ValueError("invalid invite token")
    if row["used_at"]:
        raise ValueError("invite token already used")
    expires_at = str(row["expires_at"] or "")
    if expires_at and expires_at < _utcnow():
        raise ValueError("invite token expired")
    now = _utcnow()
    user_id = int(row["user_id"])
    conn.execute(
        """
        UPDATE users
        SET password_hash = ?, email_verified = 1, status = 'active', role = 'admin'
        WHERE id = ?
        """,
        (hash_password(password), user_id),
    )
    columns = _staff_columns(conn)
    fields = ["active = 1", "accepted_at = ?"]
    values: list[Any] = [now]
    if "last_login_at" in columns:
        fields.append("last_login_at = ?"); values.append(now)
    if "email_domain_verified" in columns:
        fields.append("email_domain_verified = 1")
    values.append(user_id)
    conn.execute(
        f"UPDATE staff SET {', '.join(fields)} WHERE user_id = ?",
        values,
    )
    conn.execute("UPDATE email_tokens SET used_at = ? WHERE id = ?", (now, int(row["id"])))
    conn.commit()
    return {"ok": True, "user_id": user_id}


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
        params.append(json.dumps(normalize_permissions(body["permissions_override"], body.get("role", "readonly"))))
    if "permissions" in body:
        fields.append("permissions_json = ?")
        params.append(json.dumps(normalize_permissions(body["permissions"], body.get("role", "readonly"))))
    if "mfa_enabled" in body:
        fields.append("mfa_enabled = ?")
        params.append(1 if body["mfa_enabled"] else 0)
    if not fields:
        return
    params.append(staff_id)
    conn.execute(f"UPDATE staff SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()


def update_permissions(staff_id: int, permissions: dict[str, str], *, actor_is_owner: bool = False) -> None:
    if not actor_is_owner:
        raise PermissionError("only owner can update staff permissions")
    conn = get_conn()
    row = conn.execute("SELECT role, is_owner FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if not row:
        raise ValueError("staff not found")
    normalized = normalize_permissions(
        permissions,
        str(row["role"] or "readonly"),
        owner=bool(row["is_owner"]),
    )
    conn.execute(
        "UPDATE staff SET permissions_json = ? WHERE id = ?",
        (json.dumps(normalized), staff_id),
    )
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
        "modules": [*TAB_PERMISSION_KEYS, *SYSTEM_PERMISSION_KEYS],
        "roles": {k: v["permissions"] for k, v in ROLES.items()},
        "defaults": {
            "admin": default_permissions_for_role("admin"),
            "readonly": default_permissions_for_role("readonly"),
        },
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


def _validate_staff_email(email: str) -> None:
    if "@" not in email:
        raise ValueError("valid email required")
    domain = email.rsplit("@", 1)[-1].lower()
    if domain not in ALLOWED_STAFF_EMAIL_DOMAINS:
        raise ValueError("Only @viltrox.com emails allowed")


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _staff_columns(conn) -> set[str]:
    try:
        rows = conn.execute("PRAGMA table_info(staff)").fetchall()
        return {str(row["name"]) for row in rows}
    except Exception:
        return set()


def _staff_id_for_user(conn, user_id: int) -> int | None:
    try:
        row = conn.execute("SELECT id FROM staff WHERE user_id = ? ORDER BY id DESC LIMIT 1", (int(user_id),)).fetchone()
        return int(row["id"]) if row else None
    except Exception:
        return None


def _send_staff_invite_email(email: str, token: str) -> bool:
    url = f"{SITE_URL.rstrip('/')}/admin/login?staff_invite={token}"
    html = (
        '<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">'
        '<p style="font-size:20px;font-weight:900;color:#ff8f2a">V-OS Admin</p>'
        '<h2 style="font-size:22px;font-weight:800">You have been invited to V-OS Admin</h2>'
        '<p style="color:#5f6673;font-size:14px">Use the secure link below to set your password and accept the invite.</p>'
        f'<a href="{url}" style="display:inline-block;padding:13px 28px;background:#1a1d23;color:#fff;'
        'font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">Accept invite</a>'
        '<p style="color:#aaa;font-size:12px;margin-top:24px">Link expires automatically.</p></div>'
    )
    return send_email(email, "V-OS Admin invitation", html)
