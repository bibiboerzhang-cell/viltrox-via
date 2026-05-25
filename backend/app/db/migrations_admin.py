"""Default admin bootstrap for SQLite runtime migrations."""
from __future__ import annotations

import os
import secrets as secrets_mod
from datetime import datetime, timezone

from app.core.config import IS_PRODUCTION
from app.core.logging import get_logger
from app.core.security import hash_password

logger = get_logger(__name__)


def ensure_default_admin_account(conn) -> None:
    admin_exists = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if admin_exists:
        return

    admin_pw_plain = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pw_plain:
        if IS_PRODUCTION:
            raise RuntimeError("2.0 production requires ADMIN_PASSWORD to bootstrap the first admin account")
        admin_pw_plain = secrets_mod.token_urlsafe(16)
        logger.warning(
            "Generated ephemeral local admin password for bootstrap only — change it immediately: %s",
            admin_pw_plain,
        )
    else:
        logger.info("Admin password loaded from ADMIN_PASSWORD env var")

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
