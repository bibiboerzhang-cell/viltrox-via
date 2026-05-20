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
import os
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
from app.db.connection import get_conn, is_postgres_runtime
from app.services.auth.email import email_service_available, send_email
from app.services.auth.tokens import create_email_token

logger = get_logger(__name__)


def _load_allowed_domains() -> list[str]:
    """Load staff email domain allowlist from environment."""
    domains = ["viltrox.com"]
    extra = os.environ.get("ALLOWED_EXTERNAL_STAFF_DOMAINS", "").strip()
    if extra:
        for domain in extra.split(","):
            normalized = domain.strip().lower().lstrip("@")
            if normalized and normalized not in domains:
                domains.append(normalized)
    return domains


def _allow_any_external() -> bool:
    """Return whether any external staff email domain is allowed."""
    return os.environ.get("ALLOW_EXTERNAL_STAFF_EMAILS", "").strip().lower() in {"1", "true", "yes"}


ALLOWED_STAFF_EMAIL_DOMAINS = _load_allowed_domains()


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
    "employee": {
        "label": "Marketing Employee",
        "description": "Viltrox Marketing employee workspace",
        "permissions": {
            "content":     ["view"],
            "creators":    ["view"],
            "commerce":    [],
            "intelligence":["view"],
            "via":         [],
            "system":      [],
            "trust":       [],
            "staff":       [],
        },
    },
    "manager": {
        "label": "Marketing Manager",
        "description": "Viltrox Marketing team management",
        "permissions": {
            "content":     ["view", "approve", "reject"],
            "creators":    ["view", "edit", "flag"],
            "commerce":    ["view"],
            "intelligence":["view", "generate_insights"],
            "via":         ["view"],
            "system":      ["view"],
            "trust":       ["view"],
            "staff":       ["view"],
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
        """SELECT s.*, u.creator_code AS user_handle, u.email AS user_email, u.name AS user_name,
                  u.email_verified AS user_email_verified
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
        _augment_member_invite_status(conn, m)
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
            u.name AS user_name,
            u.email_verified AS user_email_verified
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
    bootstrap_members = []
    for row in bootstrap_rows:
        m = dict(row)
        _augment_member_invite_status(conn, m)
        bootstrap_members.append(m)
    return {"members": bootstrap_members}


def invite(body: dict, *, inviter_id: int) -> dict:
    """
    Invite flow:
      1. If email matches existing user, link staff row to it.
      2. Else create a placeholder user (not implemented here) and link.
      3. Send email with magic link (out of scope — hook in your mailer).
    """
    if not email_service_available():
        raise ValueError(
            "Email delivery unavailable. Use /api/admin/staff/invite/activation-link "
            "to generate a manual activation link."
        )
    created = _create_staff_with_token(body, inviter_id=inviter_id)
    sent = _send_staff_invite_email(created["email"], created["token"])
    if not sent:
        raise ValueError(
            "Email delivery unavailable. Use /api/admin/staff/invite/activation-link "
            "to generate a manual activation link."
        )
    return {
        "id": created["staff_id"],
        "user_id": created["user_id"],
        "role": created["role"],
        "email": created["email"],
        "invite_sent": True,
    }


def create_activation_link(body: dict, *, inviter_id: int) -> dict[str, Any]:
    created = _create_staff_with_token(body, inviter_id=inviter_id)
    token = str(created["token"])
    activation_url = _staff_activation_url(token)
    return {
        "staff_id": created["staff_id"],
        "user_id": created["user_id"],
        "email": created["email"],
        "full_name": created["full_name"],
        "role": created["role"],
        "activation_url": activation_url,
        "token_hint": _token_hint(token),
        "expires_at": created["expires_at"],
        "expires_in_hours": 48,
        "delivery_method": "manual_link",
    }


def create_password_reset_link(staff_id: int) -> dict[str, Any]:
    """Create a one-time password reset link for an existing staff account."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT s.id AS staff_id, s.user_id, s.active,
               u.email AS user_email, u.name AS user_name
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE s.id = ?
        """,
        (int(staff_id),),
    ).fetchone()
    if not row:
        raise ValueError("staff not found")
    user_id = int(row["user_id"] or 0)
    email = str(row["user_email"] or "").strip().lower()
    if not user_id or not email:
        raise ValueError("staff user email missing")

    token = create_email_token(user_id, "reset_password")
    reset_url = _password_reset_url(token)
    expires_row = conn.execute(
        "SELECT expires_at FROM email_tokens WHERE token = ? AND type = 'reset_password'",
        (token,),
    ).fetchone()
    email_sent = _send_staff_password_reset_email(
        email,
        str(row["user_name"] or email.split("@")[0]),
        reset_url,
    )
    return {
        "ok": True,
        "staff_id": int(row["staff_id"]),
        "user_id": user_id,
        "email": email,
        "reset_url": reset_url,
        "token_hint": _token_hint(token),
        "expires_at": str(expires_row["expires_at"] if expires_row else ""),
        "expires_in_hours": 1,
        "email_sent": bool(email_sent),
        "delivery_method": "email" if email_sent else "manual_link",
    }


def create_existing_activation_link(staff_id: int, *, inviter_id: int) -> dict[str, Any]:
    """Issue a fresh staff invite token for an existing staff row without changing permissions."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT s.id, s.user_id, s.role, s.active,
               u.email AS user_email, u.name AS user_name
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE s.id = ?
        """,
        (int(staff_id),),
    ).fetchone()
    if not row:
        raise ValueError("staff not found")
    user_id = int(row["user_id"] or 0)
    email = str(row["user_email"] or "").strip().lower()
    if not user_id or not email:
        raise ValueError("staff user email missing")
    _validate_staff_email(email)

    conn.execute(
        """
        UPDATE email_tokens
           SET used_at = COALESCE(used_at, ?)
         WHERE user_id = ? AND type = 'staff_invite' AND used_at IS NULL
        """,
        (_utcnow(), user_id),
    )
    columns = _staff_columns(conn)
    fields = ["invited_by = ?", "invited_at = ?"]
    values: list[Any] = [int(inviter_id), _utcnow()]
    if "invited_by_staff_id" in columns:
        fields.append("invited_by_staff_id = ?")
        values.append(_staff_id_for_user(conn, int(inviter_id)))
    values.append(int(staff_id))
    conn.execute(
        f"UPDATE staff SET {', '.join(fields)} WHERE id = ?",
        values,
    )
    conn.commit()

    token = create_email_token(user_id, "staff_invite")
    expires_row = conn.execute(
        "SELECT expires_at FROM email_tokens WHERE token = ? AND type = 'staff_invite'",
        (token,),
    ).fetchone()
    return {
        "staff_id": int(staff_id),
        "user_id": user_id,
        "email": email,
        "full_name": str(row["user_name"] or email.split("@")[0]),
        "role": str(row["role"] or "readonly"),
        "activation_url": _staff_activation_url(token),
        "token_hint": _token_hint(token),
        "expires_at": str(expires_row["expires_at"] if expires_row else ""),
        "expires_in_hours": 48,
        "delivery_method": "manual_link",
    }


def _create_staff_with_token(body: dict, *, inviter_id: int) -> dict[str, Any]:
    """Create or refresh pending staff, then issue a staff invite token."""
    conn = get_conn()
    email = str(body.get("email") or "").strip().lower()
    full_name = str(body.get("full_name") or body.get("name") or email.split("@")[0]).strip()
    role = body.get("role", "readonly")
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    if not email:
        raise ValueError("email required")
    if not full_name:
        raise ValueError("full_name required")
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
        placeholder_password = hash_password(f"staff-invite:{email}:{secrets.token_urlsafe(32)}")
        insert_sql = """
            INSERT INTO users
                (created_at, email, password_hash, name, creator_code, status, role, email_verified)
            VALUES
                (?, ?, ?, ?, ?, 'active', 'admin', 0)
        """
        params = (_utcnow(), email, placeholder_password, full_name, creator_code)
        if is_postgres_runtime():
            user_row = conn.execute(f"{insert_sql} RETURNING id", params).fetchone()
            user_id = int(user_row["id"])
        else:
            conn.execute(insert_sql, params)
            user_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    else:
        conn.execute("UPDATE users SET name = COALESCE(NULLIF(name, ''), ?) WHERE id = ?", (full_name, user_id))

    owner = email in OWNER_EMAILS
    permissions = normalize_permissions(
        _permissions_from_invite_body(body),
        role,
        owner=owner,
    )
    columns = _staff_columns(conn)
    existing_staff = conn.execute("SELECT id FROM staff WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
    values: list[Any] = [user_id, role, json.dumps(permissions), 0, 1, inviter_id, _utcnow()]
    if "is_owner" in columns:
        insert_cols.append("is_owner"); values.append(1 if owner else 0)
    if "email_domain_verified" in columns:
        insert_cols.append("email_domain_verified"); values.append(1)
    if "invited_by_staff_id" in columns:
        insert_cols.append("invited_by_staff_id"); values.append(_staff_id_for_user(conn, inviter_id))
    if existing_staff:
        update_cols = [col for col in insert_cols if col != "user_id"]
        update_values = [values[insert_cols.index(col)] for col in update_cols]
        update_values.append(int(existing_staff["id"]))
        conn.execute(
            f"UPDATE staff SET {', '.join([f'{col} = ?' for col in update_cols])} WHERE id = ?",
            update_values,
        )
        staff_id = int(existing_staff["id"])
    else:
        placeholders = ",".join(["?"] * len(insert_cols))
        sql = f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({placeholders})"
        if is_postgres_runtime():
            row = conn.execute(f"{sql} RETURNING id", values).fetchone()
            staff_id = int(row["id"])
        else:
            cur = conn.cursor()
            cur.execute(sql, values)
            staff_id = int(cur.lastrowid or 0)
    conn.execute(
        """
        UPDATE email_tokens
           SET used_at = COALESCE(used_at, ?)
         WHERE user_id = ? AND type = 'staff_invite' AND used_at IS NULL
        """,
        (_utcnow(), int(user_id)),
    )
    conn.commit()

    token = create_email_token(int(user_id), "staff_invite")
    expires_row = conn.execute(
        "SELECT expires_at FROM email_tokens WHERE token = ? AND type = 'staff_invite'",
        (token,),
    ).fetchone()
    return {
        "staff_id": staff_id,
        "user_id": int(user_id),
        "role": role,
        "email": email,
        "full_name": full_name,
        "token": token,
        "expires_at": str(expires_row["expires_at"] if expires_row else ""),
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


def resend_invite(staff_id: int, *, inviter_id: int) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT s.id, s.user_id, s.active, s.accepted_at,
               u.email AS user_email, u.name AS user_name
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE s.id = ?
        """,
        (int(staff_id),),
    ).fetchone()
    if not row:
        raise ValueError("staff not found")
    email = str(row["user_email"] or "").strip().lower()
    if not email:
        raise ValueError("staff user email missing")
    _validate_staff_email(email)

    user_id = int(row["user_id"] or 0)
    if not user_id:
        raise ValueError("staff user missing")

    conn.execute(
        """
        UPDATE email_tokens
           SET used_at = COALESCE(used_at, ?)
         WHERE user_id = ? AND type = 'staff_invite' AND used_at IS NULL
        """,
        (_utcnow(), user_id),
    )
    conn.commit()
    token = create_email_token(user_id, "staff_invite")
    sent = _send_staff_invite_email(email, token)

    columns = _staff_columns(conn)
    fields = ["invited_by = ?", "invited_at = ?"]
    values: list[Any] = [int(inviter_id), _utcnow()]
    if "invited_by_staff_id" in columns:
        fields.append("invited_by_staff_id = ?")
        values.append(_staff_id_for_user(conn, int(inviter_id)))
    values.append(int(staff_id))
    conn.execute(
        f"UPDATE staff SET {', '.join(fields)} WHERE id = ?",
        values,
    )
    conn.commit()
    return {"ok": True, "staff_id": staff_id, "email": email, "invite_sent": bool(sent)}


def delete_member(staff_id: int) -> None:
    conn = get_conn()
    row = conn.execute("SELECT is_owner FROM staff WHERE id = ?", (int(staff_id),)).fetchone()
    if not row:
        raise ValueError("staff not found")
    if int(row["is_owner"] or 0) == 1:
        raise PermissionError("owner staff cannot be deleted")
    conn.execute("DELETE FROM staff WHERE id = ?", (int(staff_id),))
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
    allowed = _load_allowed_domains()
    if domain in allowed or _allow_any_external():
        return
    raise ValueError(
        f"Email domain '{domain}' not in allowed list. "
        f"Allowed: {', '.join(allowed)}. "
        "Set ALLOW_EXTERNAL_STAFF_EMAILS=1 or "
        "ALLOWED_EXTERNAL_STAFF_DOMAINS=domain1,domain2 to allow others."
    )


def _validate_email_domain(email: str) -> None:
    _validate_staff_email(email)


def _permissions_from_invite_body(body: dict) -> dict[str, Any]:
    raw = (
        body.get("permissions")
        or body.get("permissions_override")
        or body.get("permissions_json")
        or {}
    )
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.debug("staff.permissions_json_payload_parse_failed", exc_info=True)
            return {}
    return raw if isinstance(raw, dict) else {}


def _augment_member_invite_status(conn, member: dict[str, Any]) -> None:
    member.setdefault("email", member.get("user_email"))
    member.setdefault("full_name", member.get("user_name"))
    active_token = _get_active_invite_token(conn, int(member.get("user_id") or 0))
    member["invite_token_active"] = bool(active_token)
    member["verification_status"] = _compute_verification_status(member, active_token)
    member["delivery_method"] = _compute_delivery_method(member, active_token)


def _get_active_invite_token(conn, user_id: int) -> dict[str, Any] | None:
    if not user_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT token, expires_at, used_at
            FROM email_tokens
            WHERE user_id = ? AND type = 'staff_invite' AND used_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (int(user_id),),
        ).fetchone()
    except Exception:
        logger.debug("staff.active_invite_token_lookup_failed", extra={"user_id": user_id}, exc_info=True)
        return None
    if not row:
        return None
    token = dict(row)
    expires_at = str(token.get("expires_at") or "")
    if expires_at and expires_at < _utcnow():
        return None
    return token


def _compute_verification_status(member: dict[str, Any], active_token: dict[str, Any] | None) -> str:
    try:
        if _truthy(member.get("user_email_verified")):
            return "verified"
        if member.get("accepted_at"):
            return "activated"
        if member.get("invited_at"):
            return "pending" if active_token else "expired"
        return "draft"
    except Exception:
        logger.debug("staff.verification_status_compute_failed", extra={"staff_id": member.get("id")}, exc_info=True)
        return "unknown"


def _compute_delivery_method(member: dict[str, Any], active_token: dict[str, Any] | None) -> str:
    if member.get("invited_at") and not member.get("accepted_at"):
        return "pending_invite" if active_token else "unknown"
    if member.get("accepted_at") and _truthy(member.get("user_email_verified")):
        return "email"
    if member.get("accepted_at"):
        return "manual_link"
    return "unknown"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _staff_activation_url(token: str) -> str:
    site_url = os.environ.get("SITE_URL", "http://localhost:5173").strip() or "http://localhost:5173"
    return f"{site_url.rstrip('/')}/activate?token={token}"


def _password_reset_url(token: str) -> str:
    site_url = os.environ.get("SITE_URL", "http://localhost:5173").strip() or "http://localhost:5173"
    return f"{site_url.rstrip('/')}?reset_token={token}"


def _token_hint(token: str) -> str:
    if len(token) <= 8:
        return "..." if token else ""
    return f"{token[:4]}...{token[-4:]}"


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
    url = f"{SITE_URL.rstrip('/')}/login?staff_invite={token}"
    html = (
        '<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">'
        '<p style="font-size:20px;font-weight:900;color:#0f172a">Viltrox Marketing</p>'
        '<h2 style="font-size:22px;font-weight:800">You have been invited to Viltrox Marketing</h2>'
        '<p style="color:#5f6673;font-size:14px">Use the secure link below to set your password and accept the invite.</p>'
        f'<a href="{url}" style="display:inline-block;padding:13px 28px;background:#1a1d23;color:#fff;'
        'font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">Accept invite</a>'
        '<p style="color:#aaa;font-size:12px;margin-top:24px">Link expires automatically.</p></div>'
    )
    return send_email(email, "Viltrox Marketing invitation", html)


def _send_staff_password_reset_email(email: str, name: str, reset_url: str) -> bool:
    html = (
        '<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">'
        '<p style="font-size:20px;font-weight:900;color:#0f172a">Viltrox Marketing</p>'
        '<h2 style="font-size:22px;font-weight:800">Reset your password</h2>'
        f'<p style="color:#5f6673;font-size:14px">Hi {name}, use the secure link below to reset your password.</p>'
        f'<a href="{reset_url}" style="display:inline-block;padding:13px 28px;background:#1a1d23;color:#fff;'
        'font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">Reset password</a>'
        '<p style="color:#aaa;font-size:12px;margin-top:24px">Link expires in 1 hour.</p></div>'
    )
    return send_email(email, "Reset your Viltrox Marketing password", html)
