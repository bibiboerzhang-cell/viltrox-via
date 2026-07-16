#!/usr/bin/env python3
"""Smoke test for Daily Top100 staff scope and generation response shape.

The test keeps this offline: it patches the staff listing so the digest service
can prove eligible-vs-excluded staff counts without depending on the real team
roster or external provider APIs.
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
from app.domains.analytics.schema import ensure_vkpi_analytics_schema  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_staff(marker: str, *, name: str, email: str, active: int = 1) -> tuple[int, int]:
    conn = get_conn()
    now = _now()
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", name, "approved", "operator", 1, f"https://avatar.example/{marker}.png"),
    )
    user_row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    user_id = int(user_row["id"])
    staff_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
    insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
    values: list[Any] = [user_id, "operator", _json({"vkpi": "write"}), 0, active, None, now]
    if "is_owner" in staff_cols:
        insert_cols.append("is_owner")
        values.append(0)
    if "email_domain_verified" in staff_cols:
        insert_cols.append("email_domain_verified")
        values.append(1)
    placeholders = ",".join("?" for _ in insert_cols)
    conn.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({placeholders})", values)
    staff_row = conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()
    conn.commit()
    return user_id, int(staff_row["id"])


def _seed_suggestion(marker: str, product_sku: str) -> int:
    ensure_vkpi_analytics_schema()
    conn = get_conn()
    uid = f"sug-{marker}"
    conn.execute(
        """
        INSERT INTO vkpi_outreach_suggestions
            (suggestion_uid, source_product_sku, detected_at, platform, handle, channel_name,
             follower_count, engagement_rate, avatar_url, profile_url, source_video_url,
             source_video_title, source_view_count, source_like_count, priority, score, is_viral,
             status, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            product_sku,
            _now(),
            "youtube",
            f"{marker}_creator",
            f"{marker}_creator",
            88000,
            0.043,
            f"https://avatar.example/{marker}.png",
            f"https://youtube.com/@{marker}_creator",
            f"https://youtube.com/watch?v={marker}",
            "Smoke Viltrox buyer intent review",
            190000,
            7200,
            4,
            88.0,
            True,
            "new",
            _json({"marker": marker, "smoke": True}),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM vkpi_outreach_suggestions WHERE suggestion_uid=?", (uid,)).fetchone()
    return int(row["id"])


def _cleanup(marker: str, suggestion_id: int | None, staff_ids: list[int], user_ids: list[int]) -> None:
    conn = get_conn()
    for staff_id in staff_ids:
        digest_rows = conn.execute("SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,)).fetchall()
        for row in digest_rows:
            conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (int(row["id"]),))
        conn.execute("DELETE FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,))
    for staff_id in staff_ids:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    for user_id in user_ids:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    if suggestion_id:
        conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE id=?", (suggestion_id,))
    conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE suggestion_uid=?", (f"sug-{marker}",))
    conn.commit()


def main() -> None:
    marker = f"daily_scope_{secrets.token_hex(5)}"
    product_sku = f"SMOKE-DAILY-SCOPE-{marker}"
    eligible_staff_id = 0
    smoke_staff_id = 0
    inactive_staff_id = 0
    user_ids: list[int] = []
    suggestion_id: int | None = None
    original_list_members = analytics.staff_service.list_members
    try:
        eligible_user_id, eligible_staff_id = _create_staff(marker, name="Real Operator", email=f"real.operator.{marker}@example.com", active=1)
        smoke_user_id, smoke_staff_id = _create_staff(marker, name=f"{marker}-smoke-user", email=f"{marker}@viltrox-smoke.local", active=1)
        inactive_user_id, inactive_staff_id = _create_staff(marker, name="Inactive User", email=f"inactive.{marker}@example.com", active=0)
        user_ids = [eligible_user_id, smoke_user_id, inactive_user_id]
        suggestion_id = _seed_suggestion(marker, product_sku)

        def fake_list_members() -> dict[str, list[dict[str, Any]]]:
            return {
                "members": [
                    {"id": eligible_staff_id, "name": "Real Operator", "email": f"real.operator.{marker}@example.com", "role": "operator", "active": 1},
                    {"id": smoke_staff_id, "name": f"{marker}-smoke-user", "email": f"{marker}@viltrox-smoke.local", "role": "operator", "active": 1},
                    {"id": inactive_staff_id, "name": "Inactive User", "email": f"inactive.{marker}@example.com", "role": "operator", "active": 0},
                ]
            }

        analytics.staff_service.list_members = fake_list_members
        manager = {"id": 1, "role": "admin", "is_owner": 1, "email": "admin@example.com"}

        before = analytics.daily_staff_outreach_digest_status(limit=100, staff=manager, product_sku=product_sku)
        assert before["staff_count"] == 1, before
        assert before["eligible_staff_count"] == 1, before
        assert before["active_staff_count"] == 2, before
        assert before["excluded_staff_count"] == 1, before
        assert before["digest_count"] == 0, before
        assert before["uncontacted_count"] == 1, before
        assert before["staff_filter"] == "active_non_test_staff", before

        result = analytics.generate_daily_staff_outreach_digest(limit=100, staff=manager, product_sku=product_sku)
        assert result["staff_count"] == 1, result
        assert result["eligible_staff_count"] == 1, result
        assert result["active_staff_count"] == 2, result
        assert result["excluded_staff_count"] == 1, result
        assert result["items_per_staff"] == 1, result
        assert result["digests"][0]["staff_id"] == eligible_staff_id, result

        after = analytics.daily_staff_outreach_digest_status(limit=100, staff=manager, product_sku=product_sku)
        assert after["generated_staff_count"] == 1, after
        assert after["ready_staff_count"] == 1, after
        assert after["empty_staff_count"] == 0, after
        assert after["items_total"] == 1, after
        stdout_out("VKPI_DAILY_DIGEST_STAFF_SCOPE_SMOKE_OK")
    finally:
        analytics.staff_service.list_members = original_list_members
        _cleanup(marker, suggestion_id, [eligible_staff_id, smoke_staff_id, inactive_staff_id], user_ids)


if __name__ == "__main__":
    main()
