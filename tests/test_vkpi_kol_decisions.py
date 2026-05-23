from __future__ import annotations

import pytest

from app.db.connection import get_conn
from app.services.vkpi import kol_decisions
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema


MARKER = "vkpi-kol-decision-unit"
EMAIL = "vkpi-kol-decision-unit@example.com"


def _cleanup() -> None:
    conn = get_conn()
    kol_decisions.ensure_kol_decision_schema()
    ensure_vkpi_audit_schema()
    rows = conn.execute(
        "SELECT decision_uid FROM vkpi_kol_decision_audit WHERE metadata_json LIKE ?",
        (f"%{MARKER}%",),
    ).fetchall()
    for row in rows:
        uid = row["decision_uid"]
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE target_type='kol_decision' AND target_id=?", (uid,))
    conn.execute("DELETE FROM vkpi_kol_decision_audit WHERE metadata_json LIKE ?", (f"%{MARKER}%",))
    staff_row = conn.execute("SELECT id, user_id FROM staff WHERE user_id IN (SELECT id FROM users WHERE email=?)", (EMAIL,)).fetchone()
    if staff_row:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_row["id"],))
    conn.execute("DELETE FROM users WHERE email=?", (EMAIL,))
    conn.commit()


def _staff_id() -> int:
    conn = get_conn()
    now = "2026-05-23T09:00:00Z"
    conn.execute(
        """INSERT INTO users
           (created_at, email, password_hash, name, status, role, email_verified)
           VALUES (?,?,?,?,?,?,?)""",
        (now, EMAIL, "v2:00:00", "KOL Decision Unit", "approved", "admin", 1),
    )
    user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchone()["id"])
    conn.execute(
        """INSERT INTO staff
           (user_id, role, permissions_json, mfa_enabled, active, invited_at)
           VALUES (?,?,?,?,?,?)""",
        (user_id, "admin", '{"vkpi":"admin"}', 0, 1, now),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])


def test_kol_decision_writes_append_only_audit_and_business_log() -> None:
    _cleanup()
    try:
        staff_id = _staff_id()
        result = kol_decisions.create_decision(
            {
                "kolPoolId": 12345,
                "decisionKey": "contact",
                "decisionLabel": "可联系",
                "severity": "medium",
                "rationale": "unit test decision",
                "sourceTable": "vkpi_kol_pool",
                "sourceId": "12345",
                "query": "unit search",
                "evidenceSections": ["freshness", "memory_card"],
                "evidenceSnapshot": {"provider_calls": False, "write_db": False},
                "metadata": {"marker": MARKER, "scope": "kol_decision_label_v0"},
            },
            staff={"id": staff_id},
        )

        decision = result["decision"]
        assert result["ok"] is True
        assert decision["decision_uid"].startswith("kold_")
        assert decision["kol_pool_id"] == 12345
        assert decision["staff_id"] == staff_id
        assert decision["decision_key"] == "contact"
        assert decision["evidence_sections"] == ["freshness", "memory_card"]
        assert decision["evidence_snapshot"]["provider_calls"] is False

        listed = kol_decisions.list_decisions(kol_pool_id=12345)
        assert listed["count"] >= 1
        assert listed["decisions"][0]["decision_uid"] == decision["decision_uid"]

        audit_row = get_conn().execute(
            """
            SELECT action_type, target_type, target_id, metadata_json
            FROM vkpi_business_audit_logs
            WHERE target_type='kol_decision' AND target_id=?
            """,
            (decision["decision_uid"],),
        ).fetchone()
        assert audit_row
        assert audit_row["action_type"] == "kol_decision_label"
        assert "contact" in audit_row["metadata_json"]
    finally:
        _cleanup()


def test_kol_decision_rejects_invalid_decision_key() -> None:
    _cleanup()
    with pytest.raises(ValueError, match="invalid decision_key"):
        kol_decisions.create_decision(
            {
                "kolPoolId": 12345,
                "decisionKey": "maybe",
                "metadata": {"marker": MARKER},
            },
            staff={"id": 77},
        )
