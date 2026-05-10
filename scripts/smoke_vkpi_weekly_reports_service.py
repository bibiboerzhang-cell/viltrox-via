#!/usr/bin/env python3
"""Smoke P1.6 weekly report generator persistence path.

Forces LLM gateway offline and verifies generate_for_staff() creates draft
weekly reports from grounded data context without consuming provider quota.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"
os.environ["LLM_MONTHLY_BUDGET_USD"] = "0"


def main() -> None:
    from app.db.connection import get_conn
    from app.services.vkpi import weekly_report_generator

    marker = f"weekly_smoke_{uuid.uuid4().hex[:10]}"
    conn = get_conn()
    user_id = staff_id = None
    try:
        weekly_report_generator.ensure_vkpi_weekly_reports_schema()
        user_id = conn.execute(
            """
            INSERT INTO users (email, password_hash, name, status, role, email_verified)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                marker + "@viltrox.test",
                "v2:00:00",
                "Weekly Smoke Staff",
                "active",
                "admin",
                1,
            ),
        ).fetchone()["id"]
        staff_id = conn.execute(
            """
            INSERT INTO staff (user_id, role, permissions_json, active, is_owner)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """,
            (user_id, "employee", '{"vkpi":"write"}', 1, 0),
        ).fetchone()["id"]
        conn.commit()

        period_end = date.today()
        result = weekly_report_generator.generate_for_staff(
            staff_id,
            period_start=period_end - timedelta(days=7),
            period_end=period_end,
        )
        if result.get("total_reports", 0) < 1:
            raise AssertionError(f"expected reports, got {result}")
        statuses = {r.get("status") for r in result.get("reports", [])}
        if "ok" not in statuses:
            raise AssertionError(f"expected ok report status, got {result}")

        stored = conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_weekly_reports WHERE staff_id = ?",
            (staff_id,),
        ).fetchone()
        if not stored or int(stored["n"] or 0) < 1:
            raise AssertionError("weekly reports were not persisted")

        listed = weekly_report_generator.list_reports(staff_id=staff_id)
        if listed.get("count", 0) < 1:
            raise AssertionError(f"list_reports did not return generated reports: {listed}")

        print("VKPI_WEEKLY_REPORTS_SERVICE_SMOKE_OK")
    finally:
        if staff_id is not None:
            conn.execute("DELETE FROM vkpi_weekly_reports WHERE staff_id = ?", (staff_id,))
            conn.execute("DELETE FROM staff WHERE id = ?", (staff_id,))
        if user_id is not None:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


if __name__ == "__main__":
    main()
