"""Tests for V-KPI cost ledger helpers.

These tests exercise real database writes with marker-scoped seed data. The
scope is intentionally narrow: costs.py only, no router or frontend behavior.
"""
from __future__ import annotations

import pytest

from app.db.connection import get_conn
from app.services.vkpi import costs
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema


MARKER = "VKPI-COSTS-TEST"
EMAIL = "vkpi-costs-test@example.com"
PROJECT_UID = "VKPI-COSTS-TEST-PROJECT"
PRODUCT_SKU = "VKPI-COSTS-SKU"


@pytest.fixture(autouse=True)
def _ensure_schemas():
    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    costs.ensure_product_cost_schema()
    yield


def _staff_context(staff_id: int) -> dict[str, object]:
    return {
        "id": staff_id,
        "staff_id": staff_id,
        "role": "admin",
        "is_owner": True,
    }


def _audit_actions_for_metadata(marker: str) -> list[str]:
    rows = get_conn().execute(
        """
        SELECT action_type
        FROM vkpi_business_audit_logs
        WHERE metadata_json LIKE ?
        ORDER BY id
        """,
        (f"%{marker}%",),
    ).fetchall()
    return [str(row["action_type"]) for row in rows]


@pytest.fixture
def seeded_project():
    conn = get_conn()
    now = "2026-05-01T10:00:00Z"

    ids: dict[str, int] = {}

    def cleanup() -> None:
        project_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (PROJECT_UID,)).fetchall()
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

        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ?", (f"%{MARKER}%",))
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ?", (f"{MARKER}%",))
        conn.execute("DELETE FROM vkpi_product_cost_catalog WHERE product_sku LIKE ?", (f"{PRODUCT_SKU}%",))
        for project_id in project_ids:
            conn.execute("DELETE FROM vkpi_projects WHERE id=?", (project_id,))
        for kol_id in kol_ids:
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
            (now, EMAIL, "v2:00:00", "Costs Test", "approved", "admin", 1),
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
            (MARKER, "https://example.com/vkpi-costs", "youtube", staff_id, staff_id, 1000, 100),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (MARKER,)).fetchone()["id"])

        conn.execute(
            """INSERT INTO vkpi_projects
               (project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status, priority,
                source_type, sample_status, metadata_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                PROJECT_UID,
                "Costs Test Project",
                kol_id,
                staff_id,
                staff_id,
                PRODUCT_SKU,
                "Costs Test Product",
                "youtube",
                "shipped",
                "active",
                "normal",
                "manual",
                "shipped",
                "{}",
                now,
                now,
            ),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (PROJECT_UID,)).fetchone()["id"])

        conn.commit()
        ids = {"project_id": project_id, "kol_id": kol_id, "staff_id": staff_id}
        yield ids
    finally:
        cleanup()


def test_amount_and_type_helpers_are_stable():
    assert costs._amount_cents({"amount_cents": "1234"}) == 1234
    assert costs._amount_cents({"amount_usd": "12.345"}) == 1234
    assert costs._amount_cents({"amount": "0"}) == 0
    assert costs._amount_cents({"amount_usd": "bad"}) == 0
    assert costs.TYPE_ALIASES["shipping_fee"] == "shipping"
    assert costs.TYPE_ALIASES["sample_product"] == "sample"


def test_cost_lifecycle_writes_audit_and_excludes_void_from_summary(seeded_project):
    staff = _staff_context(seeded_project["staff_id"])

    created = costs.add_cost(
        {
            "project_id": seeded_project["project_id"],
            "cost_type": "sample_cost",
            "amount_usd": "12.34",
            "status": "estimated",
            "source_ref": f"{MARKER}-manual",
            "note": MARKER,
            "metadata": {"marker": MARKER},
        },
        staff=staff,
    )["cost"]

    assert created["cost_type"] == "sample"
    assert int(created["amount_cents"]) == 1234
    assert created["status"] == "estimated"

    updated = costs.update_cost(
        int(created["id"]),
        {"cost_type": "shipping_fee", "amount_cents": 1500, "note": f"{MARKER}-updated"},
        staff=staff,
    )
    assert updated["updated"] is True
    assert updated["cost"]["cost_type"] == "shipping"
    assert int(updated["cost"]["amount_cents"]) == 1500

    approved = costs.approve_cost(int(created["id"]), {"note": MARKER}, staff=staff)
    assert approved["approved"] is True
    assert approved["cost"]["status"] == "actual"

    detail = costs.get_cost(int(created["id"]), staff=staff)
    assert detail["cost"]["id"] == created["id"]
    assert {event["action_type"] for event in detail["audit_events"]} >= {
        "cost_add",
        "cost_edit",
        "cost_approve",
    }

    voided = costs.void_cost(int(created["id"]), {"reason": MARKER}, staff=staff)
    assert voided["voided"] is True
    assert voided["cost"]["status"] == "void"

    summary = costs.summarize_project(seeded_project["project_id"])
    assert int(summary["total_cost_cents"]) == 0


def test_product_cost_catalog_active_filter(seeded_project):
    staff = _staff_context(seeded_project["staff_id"])

    costs.upsert_product_cost(
        {
            "product_sku": PRODUCT_SKU,
            "product_name": "Active Lens",
            "unit_cost_cents": 2500,
            "active": True,
            "note": MARKER,
        },
        staff=staff,
    )
    costs.upsert_product_cost(
        {
            "product_sku": f"{PRODUCT_SKU}-INACTIVE",
            "product_name": "Inactive Lens",
            "unit_cost_cents": 9900,
            "active": False,
            "note": MARKER,
        },
        staff=staff,
    )

    active_rows = costs.list_product_costs(include_inactive=False)["product_costs"]
    all_rows = costs.list_product_costs(include_inactive=True)["product_costs"]

    assert PRODUCT_SKU in {row["product_sku"] for row in active_rows}
    assert f"{PRODUCT_SKU}-INACTIVE" not in {row["product_sku"] for row in active_rows}
    assert f"{PRODUCT_SKU}-INACTIVE" in {row["product_sku"] for row in all_rows}
    assert _audit_actions_for_metadata(MARKER).count("product_cost_upsert") >= 2


def test_record_shipped_product_cost_is_idempotent(seeded_project):
    staff = _staff_context(seeded_project["staff_id"])
    costs.upsert_product_cost(
        {
            "product_sku": PRODUCT_SKU,
            "product_name": "Auto Cost Lens",
            "unit_cost_cents": 2500,
            "active": True,
            "note": MARKER,
        },
        staff=staff,
    )

    first = costs.record_shipped_product_cost(seeded_project["project_id"], staff=staff)
    second = costs.record_shipped_product_cost(seeded_project["project_id"], staff=staff)
    summary = costs.summarize_project(seeded_project["project_id"])

    assert first["status"] == "recorded"
    assert first["cost"]["cost_type"] == "product"
    assert int(first["cost"]["amount_cents"]) == 2500
    assert second["status"] == "skipped"
    assert second["reason"] == "already_recorded"
    assert int(summary["total_cost_cents"]) == 2500
    assert "cost_add" in _audit_actions_for_metadata(PROJECT_UID)
