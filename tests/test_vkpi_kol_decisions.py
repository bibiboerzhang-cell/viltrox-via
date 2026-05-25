from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db.connection import get_conn
from app.domains.kol import decision_audit as kol_decisions
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema


MARKER = "vkpi-kol-decision-unit"
EMAIL = "vkpi-kol-decision-unit@example.com"


def _cleanup() -> None:
    conn = get_conn()
    kol_decisions.ensure_kol_decision_schema()
    kol_decisions.ensure_kol_decision_followup_schema()
    ensure_vkpi_audit_schema()
    rows = conn.execute(
        "SELECT decision_uid FROM vkpi_kol_decision_audit WHERE metadata_json LIKE ?",
        (f"%{MARKER}%",),
    ).fetchall()
    for row in rows:
        uid = row["decision_uid"]
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE target_type='kol_decision' AND target_id=?", (uid,))
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE target_type='kol_decision_followup' AND metadata_json LIKE ?", (f"%{uid}%",))
        conn.execute("DELETE FROM vkpi_kol_decision_followups WHERE decision_uid=?", (uid,))
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


def test_kol_decision_followup_queue_and_outcome_are_append_only() -> None:
    _cleanup()
    try:
        staff_id = _staff_id()
        created = kol_decisions.create_decision(
            {
                "kolPoolId": 12345,
                "decisionKey": "watch",
                "decisionLabel": "可观察",
                "severity": "low",
                "rationale": "unit followup decision",
                "evidenceSections": ["freshness", "comment_intelligence"],
                "evidenceSnapshot": {"provider_calls": False, "llm_calls": False, "write_db": False},
                "metadata": {"marker": MARKER, "scope": "kol_decision_followup_v0", "source": "unit"},
            },
            staff={"id": staff_id},
        )
        decision_uid = created["decision"]["decision_uid"]
        older = (datetime.now(UTC) - timedelta(days=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = get_conn()
        conn.execute("UPDATE vkpi_kol_decision_audit SET created_at=? WHERE decision_uid=?", (older, decision_uid))
        conn.commit()

        queue = kol_decisions.list_followup_queue(status="due", days_after=30, limit=20)
        due_items = [item for item in queue["items"] if item["decision"]["decision_uid"] == decision_uid]

        assert queue["mode"] == "kol_decision_30d_followup_v0"
        assert queue["provider_calls"] is False
        assert queue["llm_calls"] is False
        assert queue["write_db"] is False
        assert due_items
        assert due_items[0]["followup_status"] == "due"
        assert due_items[0]["days_since_decision"] >= 30
        assert due_items[0]["decision_context"]["evidence_sections"] == ["freshness", "comment_intelligence"]

        outcome = kol_decisions.create_followup(
            {
                "decisionUid": decision_uid,
                "outcomeKey": "effective",
                "outcomeNote": "decision held up in 30 day review",
                "metricSnapshot": {"contacted": True, "reply": "positive"},
                "metadata": {"marker": MARKER, "scope": "kol_decision_followup_v0"},
            },
            staff={"id": staff_id},
        )
        followup = outcome["followup"]
        assert outcome["ok"] is True
        assert followup["followup_uid"].startswith("kolf_")
        assert followup["decision_uid"] == decision_uid
        assert followup["outcome_key"] == "effective"
        assert followup["metric_snapshot"]["contacted"] is True

        completed = kol_decisions.list_followup_queue(status="completed", days_after=30, limit=20)
        completed_items = [item for item in completed["items"] if item["decision"]["decision_uid"] == decision_uid]
        assert completed_items
        assert completed_items[0]["latest_followup"]["outcome_key"] == "effective"
        assert completed["summary"]["outcome_counts"]["effective"] >= 1
        assert completed["summary"]["effective_rate"] is not None
    finally:
        _cleanup()
