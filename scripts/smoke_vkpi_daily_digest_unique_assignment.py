#!/usr/bin/env python3
"""Smoke test for unique Daily Top100 assignment across staff.

The digest is a work queue. One suggestion must not be assigned to multiple
staff members on the same day, otherwise outreach can duplicate.
"""
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
from app.domains import analytics  # noqa: E402
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_staff(marker: str, index: int) -> tuple[int, int]:
    conn = get_conn()
    now = _now()
    email = f"unique.assignment.{index}.{marker}@example.com"
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", f"Assignment Staff {index}", "approved", "operator", 1, f"https://avatar.example/{marker}-{index}.png"),
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


def _seed_pool(marker: str, count: int) -> list[int]:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    ids: list[int] = []
    now = _now()
    for idx in range(count):
        uid = f"pool-{marker}-{idx}"
        handle = f"unique_creator_{marker}_{idx}"
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
                (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio,
                 followers, posts_count, avg_views, avg_likes, avg_comments, engagement_rate,
                 viltrox_fit_score, viltrox_fit_reason, sync_status, source_type, source_ref,
                 raw_platform_data, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                uid,
                "youtube",
                handle,
                f"https://youtube.com/@{handle}",
                f"Unique Creator {idx}",
                f"https://avatar.example/{marker}-{idx}.png",
                "Camera lens review creator",
                100000 + idx,
                30 + idx,
                50000 + idx,
                2000 + idx,
                100 + idx,
                2.5,
                80.0 - idx,
                "strong camera lens fit",
                "synced",
                "smoke_pool",
                marker,
                _json({"marker": marker, "idx": idx}),
                now,
                now,
            ),
        )
    conn.commit()
    rows = conn.execute("SELECT id FROM vkpi_kol_pool WHERE source_ref=?", (marker,)).fetchall()
    for row in rows:
        ids.append(int(row["id"]))
    return ids


def _cleanup(user_ids: list[int], staff_ids: list[int], pool_ids: list[int], product_sku: str) -> None:
    conn = get_conn()
    for staff_id in staff_ids:
        digest_rows = conn.execute("SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,)).fetchall()
        for row in digest_rows:
            conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (int(row["id"]),))
        conn.execute("DELETE FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,))
    conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE source_product_sku=?", (product_sku,))
    for pool_id in pool_ids:
        conn.execute("DELETE FROM vkpi_kol_pool WHERE id=?", (pool_id,))
    for staff_id in staff_ids:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    for user_id in user_ids:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


def main() -> None:
    marker = f"daily_unique_{secrets.token_hex(5)}"
    product_sku = f"SMOKE-DAILY-UNIQUE-{marker}"
    target_date = "2099-01-17"
    user_ids: list[int] = []
    staff_ids: list[int] = []
    pool_ids: list[int] = []
    original_list_members = analytics.staff_service.list_members
    try:
        for index in range(2):
            user_id, staff_id = _create_staff(marker, index)
            user_ids.append(user_id)
            staff_ids.append(staff_id)
        pool_ids = _seed_pool(marker, 5)

        def fake_list_members() -> dict[str, list[dict[str, Any]]]:
            return {
                "members": [
                    {"id": staff_ids[0], "name": "Assignment Staff A", "email": f"a.{marker}@example.com", "role": "operator", "active": 1},
                    {"id": staff_ids[1], "name": "Assignment Staff B", "email": f"b.{marker}@example.com", "role": "operator", "active": 1},
                ]
            }

        analytics.staff_service.list_members = fake_list_members
        manager = {"id": 1, "role": "admin", "is_owner": 1, "email": "admin@example.com"}
        status = analytics.daily_staff_outreach_digest_status(target_date=target_date, limit=100, staff=manager, product_sku=product_sku)
        assert status["candidate_source"] == "kol_pool_bridge", status
        assert status["uncontacted_count"] >= 5, status

        result = analytics.generate_daily_staff_outreach_digest(target_date=target_date, limit=100, staff=manager, product_sku=product_sku)
        assert result["staff_count"] == 2, result
        assert result["items_total"] >= 5, result
        assert result["items_total"] == result["assigned_unique_count"], result
        assert result["duplicate_suggestion_count"] == 0, result
        counts = sorted(int(row.get("item_count") or 0) for row in result["digests"])
        assert all(count > 0 for count in counts), result

        conn = get_conn()
        suggestion_ids: list[int] = []
        for staff_id in staff_ids:
            digest = conn.execute(
                "SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?",
                (staff_id, target_date),
            ).fetchone()
            assert digest, staff_id
            rows = conn.execute("SELECT suggestion_id FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (int(digest["id"]),)).fetchall()
            suggestion_ids.extend(int(row["suggestion_id"]) for row in rows)
        assert len(suggestion_ids) == int(result["items_total"]), (suggestion_ids, result)
        assert len(set(suggestion_ids)) == len(suggestion_ids), suggestion_ids

        after = analytics.daily_staff_outreach_digest_status(target_date=target_date, limit=100, staff=manager, product_sku=product_sku)
        assert after["items_total"] == int(result["items_total"]), after
        assert after["duplicate_suggestion_count"] == 0, after
        stdout_out("VKPI_DAILY_DIGEST_UNIQUE_ASSIGNMENT_SMOKE_OK")
    finally:
        analytics.staff_service.list_members = original_list_members
        _cleanup(user_ids, staff_ids, pool_ids, product_sku)


if __name__ == "__main__":
    main()
