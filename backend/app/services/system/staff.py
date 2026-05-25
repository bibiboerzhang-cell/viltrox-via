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
from typing import Any

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
from app.services.auth.email import email_service_available
from app.services.auth.tokens import create_email_token
from app.services.system.staff_helpers import (
    _allow_any_external,
    _augment_member_invite_status,
    _compute_delivery_method,
    _compute_verification_status,
    _get_active_invite_token,
    _inviter_is_owner,
    _load_allowed_domains,
    _password_reset_url,
    _permissions_from_invite_body,
    _send_staff_invite_email,
    _send_staff_password_reset_email,
    _staff_activation_url,
    _staff_columns,
    _staff_id_for_user,
    _strip_admin_levels,
    _token_hint,
    _truthy,
    _utcnow,
    _validate_email_domain,
    _validate_staff_email,
)
from app.services.system.staff_tokens import (
    create_api_token,
    get_audit_log,
    list_api_tokens,
    revoke_api_token,
    verify_token,
)

logger = get_logger(__name__)


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
