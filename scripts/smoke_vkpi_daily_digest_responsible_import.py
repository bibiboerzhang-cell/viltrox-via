#!/usr/bin/env python3
"""Smoke test for promo/CSV owner mapping into Daily Top100 assignment."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.db.connection import get_conn  # noqa: E402
from app.domains.kol import pool as kol_pool  # noqa: E402
from app.domains import analytics  # noqa: E402
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_staff(marker: str, index: int, name: str) -> tuple[int, int]:
    conn = get_conn()
    now = _now()
    email = f"responsible.import.{index}.{marker}@example.com"
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", name, "approved", "operator", 1, f"https://avatar.example/{marker}-{index}.png"),
    )
    user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
    staff_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
    insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
    values: list[Any] = [user_id, "operator", _json({"vkpi": "write"}), 0, 1, None, now]
    if "is_owner" in staff_cols:
        insert_cols.append("is_owner")
        values.append(0)
    if "email_domain_verified" in staff_cols:
        insert_cols.append("email_domain_verified")
        values.append(1)
    placeholders = ",".join("?" for _ in insert_cols)
    conn.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({placeholders})", values)
    staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
    conn.commit()
    return user_id, staff_id


def _cleanup(marker: str, user_ids: list[int], staff_ids: list[int], product_sku: str) -> None:
    conn = get_conn()
    for staff_id in staff_ids:
        digest_rows = conn.execute("SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,)).fetchall()
        for row in digest_rows:
            conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (int(row["id"]),))
        conn.execute("DELETE FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,))
    conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE source_product_sku=?", (product_sku,))
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=?", (marker,))
    for staff_id in staff_ids:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    for user_id in user_ids:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


def main() -> None:
    marker = f"daily_resp_{secrets.token_hex(5)}"
    product_sku = f"SMOKE-DAILY-RESP-{marker}"
    target_date = "2099-01-19"
    user_ids: list[int] = []
    staff_ids: list[int] = []
    original_list_members = analytics.staff_service.list_members
    try:
        user_a, staff_a = _create_staff(marker, 0, "Responsible Staff A")
        user_b, staff_b = _create_staff(marker, 1, "Responsible Staff B")
        user_ids.extend([user_a, user_b])
        staff_ids.extend([staff_a, staff_b])

        ensure_vkpi_product_industry_schema()
        imported = kol_pool.import_items(
            [
                {
                    "platform": "youtube",
                    "handle": f"responsible_creator_{marker}",
                    "display_name": "Responsible Creator",
                    "profile_url": f"https://youtube.com/@responsible_creator_{marker}",
                    "followers": 120000,
                    "avg_views": 62000,
                    "engagement_rate": 2.8,
                    "owner_names": ["Responsible Staff B"],
                    "product": product_sku,
                },
                {
                    "platform": "youtube",
                    "handle": f"fallback_creator_{marker}",
                    "display_name": "Fallback Creator",
                    "profile_url": f"https://youtube.com/@fallback_creator_{marker}",
                    "followers": 90000,
                    "avg_views": 41000,
                    "engagement_rate": 2.1,
                    "owner_names": ["Unmatched Future Owner"],
                    "product": product_sku,
                },
            ],
            source_type="promo_plan_xlsx",
            source_ref=marker,
            staff={"id": staff_a, "role": "admin", "is_owner": 1},
        )
        assert imported["imported"] == 2, imported

        conn = get_conn()
        rows = [dict(row) for row in conn.execute("SELECT * FROM vkpi_kol_pool WHERE source_ref=? ORDER BY handle", (marker,)).fetchall()]
        assert len(rows) == 2, rows
        responsible_row = next(row for row in rows if str(row["handle"]).startswith("responsible_creator_"))
        raw = json.loads(str(responsible_row["raw_platform_data"] or "{}"))
        assert int(raw.get("responsible_staff_id") or 0) == staff_b, raw
        assert raw.get("responsible_staff_match_status") == "owner_name:Responsible Staff B", raw

        def fake_list_members() -> dict[str, list[dict[str, Any]]]:
            return {
                "members": [
                    {"id": staff_a, "name": "Responsible Staff A", "email": f"a.{marker}@example.com", "role": "operator", "active": 1},
                    {"id": staff_b, "name": "Responsible Staff B", "email": f"b.{marker}@example.com", "role": "operator", "active": 1},
                ]
            }

        analytics.staff_service.list_members = fake_list_members
        manager = {"id": 1, "role": "admin", "is_owner": 1, "email": "admin@example.com"}
        result = analytics.generate_daily_staff_outreach_digest(target_date=target_date, limit=100, staff=manager, product_sku=product_sku)
        assert result["candidate_source"] in {"kol_pool_bridge", "outreach_suggestions"}, result
        assert result["owned_assignment_count"] >= 1, result
        assert result["duplicate_suggestion_count"] == 0, result

        digest_b = conn.execute("SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?", (staff_b, target_date)).fetchone()
        assert digest_b, result
        items_b = [
            dict(row)
            for row in conn.execute(
                """
                SELECT i.suggestion_id, i.metadata_json, s.handle
                FROM vkpi_staff_outreach_digest_items i
                JOIN vkpi_outreach_suggestions s ON s.id=i.suggestion_id
                WHERE i.digest_id=?
                """,
                (int(digest_b["id"]),),
            ).fetchall()
        ]
        owned_item = next((row for row in items_b if str(row["handle"]).startswith("responsible_creator_")), None)
        assert owned_item, {"items_b": items_b, "result": result}
        owned_meta = json.loads(str(owned_item["metadata_json"] or "{}"))
        assert int(owned_meta.get("assignment_staff_id") or 0) == staff_b, owned_meta
        assert owned_meta.get("assignment_reason") == "metadata.responsible_staff_id", owned_meta
        stdout_out("VKPI_DAILY_DIGEST_RESPONSIBLE_IMPORT_SMOKE_OK")
    finally:
        analytics.staff_service.list_members = original_list_members
        _cleanup(marker, user_ids, staff_ids, product_sku)


if __name__ == "__main__":
    main()
