#!/usr/bin/env python3
"""Smoke test for owner-first Daily Top100 assignment.

Imported candidate lists can carry a responsible staff id. Daily Top100 should
respect that ownership before falling back to round-robin distribution, while
still keeping each suggestion assigned once per digest date.
"""
from __future__ import annotations

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

from app.db.connection import get_conn, is_postgres_runtime  # noqa: E402
from app.domains import analytics  # noqa: E402
from app.domains.analytics.schema import ensure_vkpi_analytics_schema  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _db_bool(value: bool) -> bool | int:
    return bool(value) if is_postgres_runtime() else (1 if value else 0)


def _create_staff(marker: str, index: int) -> tuple[int, int]:
    conn = get_conn()
    now = _now()
    email = f"owner.assignment.{index}.{marker}@example.com"
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", f"Owner Assignment Staff {index}", "approved", "operator", 1, f"https://avatar.example/{marker}-{index}.png"),
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


def _insert_suggestion(marker: str, product_sku: str, index: int, responsible_staff_id: int | None = None) -> int:
    ensure_vkpi_analytics_schema()
    conn = get_conn()
    handle = f"owner_creator_{marker}_{index}"
    metadata = {"marker": marker, "source": "owner_assignment_smoke"}
    if responsible_staff_id:
        metadata["responsible_staff_id"] = responsible_staff_id
    conn.execute(
        """
        INSERT INTO vkpi_outreach_suggestions
            (suggestion_uid, source_run_id, source_product_sku, detected_at, platform, handle,
             channel_name, follower_count, engagement_rate, country_code, avatar_url, profile_url,
             source_video_url, source_video_title, source_view_count, source_like_count,
             existing_kol_id, worked_before, mention_count, is_viral, priority, score, status,
             metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"sug-owner-{marker}-{index}",
            None,
            product_sku,
            _now(),
            "youtube",
            handle,
            f"Owner Creator {index}",
            100000 + index,
            3.2,
            "",
            f"https://avatar.example/{marker}-{index}.png",
            f"https://youtube.com/@{handle}",
            f"https://youtube.com/watch?v={marker}{index}",
            f"Camera lens review {index}",
            80000 + index,
            2500 + index,
            None,
            _db_bool(False),
            1,
            _db_bool(False),
            5,
            80.0 - index,
            "new",
            _json(metadata),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM vkpi_outreach_suggestions WHERE suggestion_uid=?", (f"sug-owner-{marker}-{index}",)).fetchone()
    return int(row["id"])


def _cleanup(user_ids: list[int], staff_ids: list[int], product_sku: str) -> None:
    conn = get_conn()
    for staff_id in staff_ids:
        digest_rows = conn.execute("SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,)).fetchall()
        for row in digest_rows:
            conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (int(row["id"]),))
        conn.execute("DELETE FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,))
    conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE source_product_sku=?", (product_sku,))
    for staff_id in staff_ids:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    for user_id in user_ids:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


def main() -> None:
    marker = f"daily_owner_{secrets.token_hex(5)}"
    product_sku = f"SMOKE-DAILY-OWNER-{marker}"
    target_date = "2099-01-18"
    user_ids: list[int] = []
    staff_ids: list[int] = []
    original_list_members = analytics.staff_service.list_members
    try:
        for index in range(2):
            user_id, staff_id = _create_staff(marker, index)
            user_ids.append(user_id)
            staff_ids.append(staff_id)

        owned_suggestion_id = _insert_suggestion(marker, product_sku, 0, responsible_staff_id=staff_ids[1])
        fallback_suggestion_ids = [_insert_suggestion(marker, product_sku, index) for index in range(1, 4)]

        def fake_list_members() -> dict[str, list[dict[str, Any]]]:
            return {
                "members": [
                    {"id": staff_ids[0], "name": "Owner Staff A", "email": f"a.{marker}@example.com", "role": "operator", "active": 1},
                    {"id": staff_ids[1], "name": "Owner Staff B", "email": f"b.{marker}@example.com", "role": "operator", "active": 1},
                ]
            }

        analytics.staff_service.list_members = fake_list_members
        manager = {"id": 1, "role": "admin", "is_owner": 1, "email": "admin@example.com"}

        result = analytics.generate_daily_staff_outreach_digest(target_date=target_date, limit=100, staff=manager, product_sku=product_sku)
        assert result["staff_count"] == 2, result
        assert result["items_total"] == 1 + len(fallback_suggestion_ids), result
        assert result["assigned_unique_count"] == result["items_total"], result
        assert result["duplicate_suggestion_count"] == 0, result
        assert result["owned_assignment_count"] >= 1, result
        assert result["fallback_assignment_count"] >= 1, result

        conn = get_conn()
        by_staff: dict[int, list[int]] = {}
        metadata_by_suggestion: dict[int, dict[str, Any]] = {}
        for staff_id in staff_ids:
            digest = conn.execute(
                "SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?",
                (staff_id, target_date),
            ).fetchone()
            assert digest, staff_id
            rows = conn.execute(
                "SELECT suggestion_id, metadata_json FROM vkpi_staff_outreach_digest_items WHERE digest_id=? ORDER BY rank",
                (int(digest["id"]),),
            ).fetchall()
            by_staff[staff_id] = [int(row["suggestion_id"]) for row in rows]
            for row in rows:
                metadata_by_suggestion[int(row["suggestion_id"])] = json.loads(str(row["metadata_json"] or "{}"))

        all_ids = [suggestion_id for ids in by_staff.values() for suggestion_id in ids]
        assert len(all_ids) == len(set(all_ids)), by_staff
        assert owned_suggestion_id in by_staff[staff_ids[1]], by_staff
        assert owned_suggestion_id not in by_staff[staff_ids[0]], by_staff
        owned_meta = metadata_by_suggestion[owned_suggestion_id]
        assert int(owned_meta.get("assignment_staff_id") or 0) == staff_ids[1], owned_meta
        assert owned_meta.get("assignment_reason") == "metadata.responsible_staff_id", owned_meta

        status = analytics.daily_staff_outreach_digest_status(target_date=target_date, limit=100, staff=manager, product_sku=product_sku)
        assert status["duplicate_suggestion_count"] == 0, status
        assert status["assignment_strategy"] == "owner_first_then_round_robin", status
        print("VKPI_DAILY_DIGEST_OWNER_ASSIGNMENT_SMOKE_OK")
    finally:
        analytics.staff_service.list_members = original_list_members
        _cleanup(user_ids, staff_ids, product_sku)


if __name__ == "__main__":
    main()
