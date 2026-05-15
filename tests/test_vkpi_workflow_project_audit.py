"""Tests for project lifecycle audit parity.

The workflow layer already writes vkpi_project_stage_events. These tests assert
that the same lifecycle actions also produce centralized business audit rows so
Audit page and project detail timelines can show a single audit source.
"""
from __future__ import annotations

import pytest

from app.db.connection import get_conn
from app.services.vkpi import workflow
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema


MARKER = "VKPI-WORKFLOW-AUDIT-TEST"
EMAIL = "vkpi-workflow-audit-test@example.com"
PRODUCT_SKU = "VKPI-WORKFLOW-AUDIT-SKU"


@pytest.fixture(autouse=True)
def _ensure_schemas():
    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    yield


def _staff_context(staff_id: int) -> dict[str, object]:
    return {
        "id": staff_id,
        "staff_id": staff_id,
        "role": "admin",
        "is_owner": True,
    }


@pytest.fixture
def seeded_staff_and_kol():
    conn = get_conn()
    now = "2026-05-01T10:00:00Z"

    def cleanup() -> None:
        project_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ?",
                (f"{MARKER}%", f"%{MARKER}%"),
            ).fetchall()
        ]
        kol_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM kols WHERE channel_name=?", (MARKER,)).fetchall()
        ]
        staff_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT s.id FROM staff s JOIN users u ON u.id=s.user_id WHERE u.email=?",
                (EMAIL,),
            ).fetchall()
        ]
        user_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchall()
        ]

        for project_id in project_ids:
            conn.execute("DELETE FROM vkpi_business_audit_logs WHERE target_type='project' AND target_id=?", (str(project_id),))
            conn.execute("DELETE FROM vkpi_project_stage_events WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM vkpi_kol_claims WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM vkpi_links WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM vkpi_projects WHERE id=?", (project_id,))
        for kol_id in kol_ids:
            conn.execute("DELETE FROM vkpi_kol_claims WHERE kol_id=?", (kol_id,))
            conn.execute("DELETE FROM kols WHERE id=?", (kol_id,))
        for staff_id in staff_ids:
            conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        for user_id in user_ids:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()

    try:
        cleanup()
        conn.execute(
            """INSERT INTO users
               (created_at, email, password_hash, name, status, role, email_verified)
               VALUES (?,?,?,?,?,?,?)""",
            (now, EMAIL, "v2:00:00", "Workflow Audit Test", "approved", "admin", 1),
        )
        user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchone()["id"])

        conn.execute(
            """INSERT INTO staff
               (user_id, role, permissions_json, mfa_enabled, active, invited_at, is_owner, email_domain_verified)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, "admin", '{"vkpi":"admin"}', 0, 1, now, 1, 1),
        )
        staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])

        conn.execute(
            """INSERT INTO kols
               (channel_name, channel_url, platform, assigned_staff_id, created_by_staff_id, follower_count, avg_views)
               VALUES (?,?,?,?,?,?,?)""",
            (MARKER, "https://example.com/workflow-audit", "youtube", staff_id, staff_id, 1000, 100),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (MARKER,)).fetchone()["id"])
        conn.commit()
        yield {"staff_id": staff_id, "kol_id": kol_id}
    finally:
        cleanup()


def _audit_actions(project_id: int) -> set[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT action_type FROM vkpi_business_audit_logs WHERE target_type='project' AND target_id=?",
        (str(project_id),),
    ).fetchall()
    return {str(row["action_type"]) for row in rows}


def test_project_create_transition_delete_write_business_audit(seeded_staff_and_kol):
    staff = _staff_context(seeded_staff_and_kol["staff_id"])
    project_uid = f"{MARKER}-001"

    created = workflow.create_project(
        {
            "project_uid": project_uid,
            "project_name": f"{MARKER} Project",
            "kol_id": seeded_staff_and_kol["kol_id"],
            "assigned_staff_id": seeded_staff_and_kol["staff_id"],
            "product_sku": PRODUCT_SKU,
            "product_name": "Workflow Audit Product",
            "platform": "youtube",
            "stage": "discovery",
            "source_type": "test",
            "note": MARKER,
            "metadata": {"marker": MARKER},
        },
        staff=staff,
    )
    project_id = int(created["id"])

    assert "project_create" in _audit_actions(project_id)

    transitioned = workflow.transition_project(
        project_id,
        {"to_stage": "contacted", "note": MARKER, "metadata": {"marker": MARKER}},
        staff=staff,
    )
    assert transitioned["from_stage"] == "discovery"
    assert transitioned["to_stage"] == "contacted"
    assert "project_stage_transition" in _audit_actions(project_id)

    deleted = workflow.delete_project(project_id, {"reason": MARKER}, staff=staff)
    assert deleted["status"] == "deleted"
    assert _audit_actions(project_id) >= {"project_create", "project_stage_transition", "project_delete"}

    stage_events = get_conn().execute(
        "SELECT event_type FROM vkpi_project_stage_events WHERE project_id=? ORDER BY id",
        (project_id,),
    ).fetchall()
    assert [str(row["event_type"]) for row in stage_events] == ["created", "stage_change", "deleted"]
