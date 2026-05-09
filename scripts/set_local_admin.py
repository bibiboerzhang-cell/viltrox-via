#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.core.security import hash_password  # noqa: E402
from runtime_env import apply_runtime_env  # noqa: E402
from stdout_utils import out  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update a local admin user.")
    parser.add_argument("email", help="Admin email to create or update.")
    parser.add_argument("--name", default="", help="Display name for the admin user.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    apply_runtime_env()

    email = args.email.strip().lower()
    password = sys.stdin.readline().rstrip("\n")
    if not email or "@" not in email:
        raise SystemExit("valid email required")
    if not password:
        raise SystemExit("password required on stdin")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not configured")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = args.name.strip() or email.split("@", 1)[0]
    password_hash = hash_password(password)

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users
                    (created_at, email, password_hash, name, status, role, email_verified)
                VALUES
                    (%s, %s, %s, %s, 'approved', 'admin', 1)
                ON CONFLICT (email) DO UPDATE SET
                    password_hash = EXCLUDED.password_hash,
                    name = EXCLUDED.name,
                    status = 'approved',
                    role = 'admin',
                    email_verified = 1
                RETURNING id, email, status, role, email_verified
                """,
                (now, email, password_hash, name),
            )
            row = cur.fetchone()
            if row is not None:
                cur.execute("SELECT id FROM staff WHERE user_id = %s ORDER BY id DESC LIMIT 1", (row[0],))
                staff_row = cur.fetchone()
                if staff_row:
                    cur.execute(
                        """
                        UPDATE staff
                        SET role = 'admin',
                            active = 1,
                            is_owner = 1,
                            email_domain_verified = 1
                        WHERE id = %s
                        """,
                        (staff_row[0],),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO staff
                            (user_id, role, permissions_json, mfa_enabled, active, invited_by, invited_at,
                             is_owner, email_domain_verified, accepted_at)
                        VALUES
                            (%s, 'admin', '{}', 0, 1, %s, %s, 1, 1, %s)
                        """,
                        (row[0], row[0], now, now),
                    )
        conn.commit()

    if row is None:
        raise SystemExit("admin upsert failed")
    out(f"{row[0]}|{row[1]}|{row[2]}|{row[3]}|{row[4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
