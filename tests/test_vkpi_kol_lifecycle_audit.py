"""Tests for KOL lifecycle centralized audit parity."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.domains.kol import claims as kol_claims
from app.platform.db import schema as vkpi_schema
from app.platform.db import schema_audit as vkpi_audit_schema
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema


MARKER = "vkpi-step14-kol-audit"
ADMIN_EMAIL = "vkpi-step14-admin@example.com"
ASSIGNEE_EMAIL = "vkpi-step14-assignee@example.com"


@pytest.fixture(autouse=True)
def _kol_lifecycle_audit_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exercise lifecycle SQL on a private SQLite database only.

    The V-KPI guards own the ``vkpi_*`` tables, but deliberately do not own
    the legacy users/staff/kols tables.  Define only the legacy columns used by
    this lifecycle, then run the real V-KPI and audit guards for everything
    domain-owned.
    """
    db_connection.close_db_runtime_sync()
    db_path = (tmp_path / "vkpi-kol-lifecycle-audit.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db

    monkeypatch.setattr(db_connection, "DB_PATH", db_path)
    monkeypatch.setattr(db_connection, "DB_RUNTIME_BACKEND", "sqlite")
    monkeypatch.setattr(db_connection, "DB_RUNTIME_URL", "")
    monkeypatch.setattr(vkpi_schema, "_SCHEMA_READY", False)
    monkeypatch.setattr(vkpi_audit_schema, "_SCHEMA_READY", False)

    setup = sqlite3.connect(str(db_path))
    try:
        setup.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                email TEXT UNIQUE,
                password_hash TEXT,
                name TEXT,
                status TEXT,
                role TEXT,
                email_verified INTEGER DEFAULT 0
            );
            CREATE TABLE staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                role TEXT,
                permissions_json TEXT DEFAULT '{}',
                mfa_enabled INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                invited_at TEXT,
                is_owner INTEGER DEFAULT 0,
                email_domain_verified INTEGER DEFAULT 0
            );
            CREATE TABLE kols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_name TEXT NOT NULL,
                channel_url TEXT,
                platform TEXT NOT NULL,
                country TEXT,
                niche TEXT,
                project_name TEXT,
                owner_name TEXT,
                media_name TEXT,
                duplicate_flag TEXT,
                scale_tier TEXT,
                content_type TEXT,
                approval_note TEXT,
                channel_tags TEXT,
                affiliate_id TEXT,
                affiliate_link TEXT,
                discount_code TEXT,
                amazon_link TEXT,
                short_link TEXT,
                primary_category TEXT,
                promoted_product TEXT,
                follower_count INTEGER DEFAULT 0,
                avg_views INTEGER DEFAULT 0,
                contact_email TEXT,
                contact_phone TEXT,
                contact_status TEXT DEFAULT 'cold',
                notes TEXT,
                assigned_staff_id INTEGER,
                created_by_staff_id INTEGER,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        setup.commit()
    finally:
        setup.close()

    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    actual_path = Path(str(get_conn().execute("PRAGMA database_list").fetchone()["file"])).resolve()
    assert actual_path == db_path
    try:
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()


def _staff_context(staff_id: int, *, role: str = "admin", is_owner: bool = True) -> dict[str, object]:
    return {
        "id": staff_id,
        "staff_id": staff_id,
        "role": role,
        "is_owner": is_owner,
    }


@pytest.fixture
def seeded_staff():
    conn = get_conn()
    now = "2026-05-01T12:00:00Z"

    def cleanup() -> None:
        kol_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM kols WHERE channel_name=? OR channel_url LIKE ? OR notes LIKE ?",
                (MARKER, f"%{MARKER}%", f"%{MARKER}%"),
            ).fetchall()
        ]
        for kol_id in kol_ids:
            conn.execute("DELETE FROM vkpi_business_audit_logs WHERE target_type='kol' AND target_id=?", (str(kol_id),))
            conn.execute("DELETE FROM vkpi_kol_claims WHERE kol_id=?", (kol_id,))
            conn.execute("DELETE FROM kols WHERE id=?", (kol_id,))

        staff_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT s.id FROM staff s JOIN users u ON u.id=s.user_id WHERE u.email IN (?,?)",
                (ADMIN_EMAIL, ASSIGNEE_EMAIL),
            ).fetchall()
        ]
        user_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM users WHERE email IN (?,?)", (ADMIN_EMAIL, ASSIGNEE_EMAIL)).fetchall()
        ]
        for staff_id in staff_ids:
            conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        for user_id in user_ids:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()

    def create_staff(email: str, name: str, role: str = "admin") -> int:
        conn.execute(
            """INSERT INTO users
               (created_at, email, password_hash, name, status, role, email_verified)
               VALUES (?,?,?,?,?,?,?)""",
            (now, email, "v2:00:00", name, "approved", role, 1),
        )
        user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        conn.execute(
            """INSERT INTO staff
               (user_id, role, permissions_json, mfa_enabled, active, invited_at, is_owner, email_domain_verified)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, role, '{"vkpi":"admin"}', 0, 1, now, 1 if role == "admin" else 0, 1),
        )
        return int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])

    try:
        cleanup()
        admin_id = create_staff(ADMIN_EMAIL, "Step14 Admin", "admin")
        assignee_id = create_staff(ASSIGNEE_EMAIL, "Step14 Assignee", "admin")
        conn.commit()
        yield {"admin_id": admin_id, "assignee_id": assignee_id}
    finally:
        cleanup()


def _audit_actions(kol_id: int) -> list[str]:
    rows = get_conn().execute(
        """
        SELECT action_type
        FROM vkpi_business_audit_logs
        WHERE target_type='kol' AND target_id=?
        ORDER BY id
        """,
        (str(kol_id),),
    ).fetchall()
    return [str(row["action_type"]) for row in rows]


def test_kol_lookup_claim_reassign_release_and_manual_update_write_business_audit(seeded_staff):
    admin_staff = _staff_context(seeded_staff["admin_id"])

    lookup_result = kol_claims.lookup(
        {
            "platform": "instagram",
            "handle": MARKER,
            "url": f"https://www.instagram.com/{MARKER}/",
            "create_if_missing": True,
            "notes": MARKER,
        },
        staff=admin_staff,
    )
    kol_id = int(lookup_result["kol"]["id"])

    claim_result = kol_claims.claim(kol_id, {"expires_days": 7}, staff=admin_staff)
    claim_id = int(claim_result["claim"]["id"])

    updated = kol_claims.update_kol_manual(
        kol_id,
        {"notes": f"{MARKER} updated", "avg_views": 1234, "contact_links": ["https://example.com"]},
        staff=admin_staff,
    )
    assert updated["kol"]["notes"] == f"{MARKER} updated"

    reassigned = kol_claims.reassign(
        claim_id,
        {"staff_id": seeded_staff["assignee_id"], "reason": "handoff"},
        staff=admin_staff,
    )
    new_claim_id = int(reassigned["claim"]["id"])

    released = kol_claims.release(new_claim_id, {"reason": "done"}, staff=_staff_context(seeded_staff["assignee_id"]))
    assert released["status"] == "released"

    actions = _audit_actions(kol_id)
    assert actions.count("kol_lookup_create") == 1
    assert actions.count("kol_claim_create") == 2
    assert "kol_manual_update" in actions
    assert actions.count("kol_claim_release") == 2
    assert "kol_claim_reassign" in actions
