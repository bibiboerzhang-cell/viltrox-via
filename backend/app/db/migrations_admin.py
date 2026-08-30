"""Default admin and tenant bootstrap for SQLite runtime migrations."""
from __future__ import annotations

import os
import secrets as secrets_mod
from datetime import datetime, timezone

from app.core.config import IS_PRODUCTION
from app.core.logging import get_logger
from app.core.passwords import hash_password

logger = get_logger(__name__)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (str(table_name),),
    ).fetchone()
    return row is not None


def _column_names(conn, table_name: str) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _ensure_staff_tenant_columns(conn) -> None:
    """Bring the hermetic SQLite staff shape up to the runtime contract."""

    columns = _column_names(conn, "staff")
    for name, declaration in (
        ("is_owner", "INTEGER NOT NULL DEFAULT 0"),
        ("email_domain_verified", "INTEGER NOT NULL DEFAULT 0"),
        ("invited_by_staff_id", "INTEGER"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE staff ADD COLUMN {name} {declaration}")


def _ensure_tenant_kernel(conn) -> None:
    """Create the SQLite equivalent of migration 195 for local/hermetic use."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            industry TEXT NOT NULL DEFAULT '',
            brand_profile_json TEXT NOT NULL DEFAULT '{}',
            scoring_template TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS organization_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (organization_id, staff_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_members_staff ON organization_members(staff_id)"
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT OR IGNORE INTO organizations (
            id, name, slug, industry, brand_profile_json, scoring_template,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            1,
            "Viltrox",
            "viltrox",
            "camera_lens",
            '{"products":"camera lenses","target_audience":"photographers, filmmakers, reviewers"}',
            "camera_gear_kol_v1",
            now,
            now,
        ),
    )
    org = conn.execute("SELECT slug FROM organizations WHERE id=1").fetchone()
    if not org or str(org["slug"] or "").strip().lower() != "viltrox":
        raise RuntimeError("SQLite tenant bootstrap requires organization 1 to be Viltrox")


def ensure_default_admin_account(conn) -> None:
    """Ensure the local default admin has a staff identity and org-1 membership.

    This function must run after the SQLite v5 schema has created ``staff``.
    Keeping all three identities together makes the local/hermetic path match
    the Postgres greenfield bootstrap semantics.
    """

    if not _table_exists(conn, "staff"):
        raise RuntimeError("default admin bootstrap requires the SQLite staff schema")

    _ensure_staff_tenant_columns(conn)
    _ensure_tenant_kernel(conn)
    admin_exists = conn.execute(
        "SELECT id FROM users WHERE email=? LIMIT 1",
        ("admin@viltrox.com",),
    ).fetchone()

    admin_pw_plain = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pw_plain and not admin_exists:
        if IS_PRODUCTION:
            raise RuntimeError("2.0 production requires ADMIN_PASSWORD to bootstrap the first admin account")
        admin_pw_plain = secrets_mod.token_urlsafe(16)
        logger.warning(
            "Generated ephemeral local admin password for bootstrap only — change it immediately: %s",
            admin_pw_plain,
        )
    elif admin_pw_plain:
        logger.info("Admin password loaded from ADMIN_PASSWORD env var")

    if not admin_exists:
        conn.execute(
            """
            INSERT OR IGNORE INTO users
            (created_at, email, password_hash, name, status, role)
            VALUES (?,?,?,?,?,?)
            """,
            (
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "admin@viltrox.com",
                hash_password(admin_pw_plain),
                "Admin",
                "approved",
                "admin",
            ),
        )
        admin_exists = conn.execute(
            "SELECT id FROM users WHERE email=? LIMIT 1",
            ("admin@viltrox.com",),
        ).fetchone()
    if not admin_exists:
        raise RuntimeError("default admin user bootstrap failed")

    admin_user_id = int(admin_exists["id"])
    conn.execute(
        "UPDATE users SET role='admin', status='approved' WHERE id=?",
        (admin_user_id,),
    )
    staff_row = conn.execute(
        "SELECT id FROM staff WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (admin_user_id,),
    ).fetchone()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if staff_row:
        admin_staff_id = int(staff_row["id"])
        conn.execute(
            """
            UPDATE staff
               SET role='admin', active=1, is_owner=1, email_domain_verified=1
             WHERE id=?
            """,
            (admin_staff_id,),
        )
    else:
        cursor = conn.execute(
            """
            INSERT INTO staff (
                user_id, role, permissions_json, mfa_enabled, active,
                invited_by, invited_at, accepted_at, is_owner,
                email_domain_verified
            ) VALUES (?, 'admin', ?, 0, 1, ?, ?, ?, 1, 1)
            """,
            (admin_user_id, '{"vkpi":"write"}', admin_user_id, now, now),
        )
        admin_staff_id = int(cursor.lastrowid or 0)
    if admin_staff_id <= 0:
        raise RuntimeError("default admin staff bootstrap failed")

    conn.execute(
        """
        INSERT INTO organization_members (
            organization_id, staff_id, role, created_at
        ) VALUES (1, ?, 'owner', ?)
        ON CONFLICT (organization_id, staff_id) DO UPDATE SET role='owner'
        """,
        (admin_staff_id, now),
    )
