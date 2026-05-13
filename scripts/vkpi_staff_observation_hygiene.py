#!/usr/bin/env python3
"""P4.1B staff observation hygiene utility.

Before real employee observation, remove stale smoke-created staff accounts
that can pollute active staff counts and Daily Top100 coverage numbers.

Default mode is dry-run. Use --apply only after reviewing the printed rows.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db.connection import get_conn


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def find_stale_smoke_staff() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            s.id AS staff_id,
            s.user_id,
            COALESCE(s.role, '') AS staff_role,
            COALESCE(s.active, 1) AS staff_active,
            COALESCE(s.is_owner, 0) AS is_owner,
            COALESCE(u.email, '') AS email,
            COALESCE(u.name, '') AS name,
            u.created_at AS created_at,
            COALESCE(u.status, '') AS user_status
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE
            LOWER(COALESCE(u.email, '')) LIKE ?
            OR LOWER(COALESCE(u.name, '')) LIKE ?
            OR LOWER(COALESCE(u.email, '')) LIKE ?
            OR LOWER(COALESCE(u.email, '')) LIKE ?
        ORDER BY s.id
        """,
        ("%smoke%", "%smoke%", "%@viltrox-smoke.local", "vkpi-%@example.com"),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def cleanup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    conn = get_conn()
    staff_ids = [int(row["staff_id"]) for row in rows if row.get("staff_id")]
    user_ids = [int(row["user_id"]) for row in rows if row.get("user_id")]
    if staff_ids:
        placeholders = ",".join("?" for _ in staff_ids)
        conn.execute(f"DELETE FROM staff WHERE id IN ({placeholders})", staff_ids)
    if user_ids:
        placeholders = ",".join("?" for _ in user_ids)
        conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
    conn.commit()
    return {"deleted_staff": len(staff_ids), "deleted_users": len(user_ids)}


def summarize_real_staff() -> dict[str, Any]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            s.id AS staff_id,
            COALESCE(s.role, '') AS role,
            COALESCE(s.active, 1) AS active,
            COALESCE(s.is_owner, 0) AS is_owner,
            COALESCE(u.email, '') AS email,
            COALESCE(u.status, '') AS user_status
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        ORDER BY s.id
        """
    ).fetchall()
    active = [dict(row) for row in rows if int(dict(row).get("active") or 0) == 1]
    real = [
        row
        for row in active
        if "smoke" not in str(row.get("email") or "").lower()
        and not str(row.get("email") or "").endswith("@example.com")
        and not str(row.get("email") or "").endswith("@viltrox-smoke.local")
    ]
    admin_roles = {"owner", "admin", "manager", "lead", "marketing_lead", "marketing-manager", "marketing_manager"}
    admins = [
        row
        for row in real
        if int(row.get("is_owner") or 0) == 1 or str(row.get("role") or "").lower() in admin_roles
    ]
    return {
        "total_active_staff": len(active),
        "real_staff_candidates": len(real),
        "real_admin_staff": len(admins),
        "real_employee_staff": max(0, len(real) - len(admins)),
        "recommended_real_staff_min": 3,
        "staff_ready_for_observation": len(real) >= 3 and len(admins) >= 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean stale smoke staff before P4.1 observation.")
    parser.add_argument("--apply", action="store_true", help="Actually delete stale smoke staff/users.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    args = parser.parse_args()

    rows = find_stale_smoke_staff()
    before = summarize_real_staff()
    result: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "stale_smoke_staff_count": len(rows),
        "stale_smoke_staff": rows,
        "staff_before": before,
    }
    if args.apply:
        result["cleanup"] = cleanup(rows)
        result["staff_after"] = summarize_real_staff()
    else:
        result["cleanup"] = {"deleted_staff": 0, "deleted_users": 0}
        result["staff_after"] = before

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    print("VKPI_P4_1B_STAFF_OBSERVATION_HYGIENE", json.dumps(result, ensure_ascii=False, sort_keys=True))
    if rows and not args.apply:
        print("Dry-run only. Review rows, then rerun with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
