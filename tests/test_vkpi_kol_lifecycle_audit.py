"""Tests for KOL lifecycle centralized audit parity."""
from __future__ import annotations

import pytest

from app.db.connection import get_conn
from app.domains.kol import claims as kol_claims
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema


MARKER = "vkpi-step14-kol-audit"
ADMIN_EMAIL = "vkpi-step14-admin@example.com"
ASSIGNEE_EMAIL = "vkpi-step14-assignee@example.com"


@pytest.fixture(autouse=True)
def _ensure_schemas():
    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    yield


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
