#!/usr/bin/env python3
"""Smoke test for Daily Top100 KOL Pool bridge source.

Daily Top100 should not stay empty when the live outreach suggestion table has
no rows but KOL Pool has a real imported/enriched candidate. The candidate is
seeded into vkpi_outreach_suggestions with candidate_source=kol_pool_bridge so
it is traceable and not confused with full-market crawling.
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
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_staff(marker: str) -> tuple[int, int]:
    conn = get_conn()
    now = _now()
    email = f"bridge.staff.{marker}@example.com"
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", f"Bridge Staff {marker}", "approved", "operator", 1, f"https://avatar.example/{marker}.png"),
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


def _seed_pool(marker: str) -> int:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    uid = f"pool-{marker}"
    handle = f"bridge_creator_{marker}"
    now = _now()
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
            f"Bridge Creator {marker}",
            f"https://avatar.example/{marker}.png",
            "Camera lens review creator",
            123000,
            42,
            88000,
            2400,
            180,
            3.1,
            82.0,
            "strong camera lens fit",
            "synced",
            "smoke_pool",
            marker,
            _json({"marker": marker, "smoke": True}),
            now,
            now,
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM vkpi_kol_pool WHERE pool_uid=?", (uid,)).fetchone()["id"])


def _cleanup(marker: str, user_id: int | None, staff_id: int | None, pool_id: int | None, product_sku: str) -> None:
    conn = get_conn()
    if staff_id:
        digest_rows = conn.execute("SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,)).fetchall()
        for row in digest_rows:
            conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (int(row["id"]),))
        conn.execute("DELETE FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,))
    conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE source_product_sku=?", (product_sku,))
    if pool_id:
        conn.execute("DELETE FROM vkpi_kol_pool WHERE id=?", (pool_id,))
    if staff_id:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    if user_id:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


def main() -> None:
    marker = f"daily_bridge_{secrets.token_hex(5)}"
    product_sku = f"SMOKE-DAILY-BRIDGE-{marker}"
    user_id: int | None = None
    staff_id: int | None = None
    pool_id: int | None = None
    original_list_members = analytics.staff_service.list_members
    try:
        user_id, staff_id = _create_staff(marker)
        pool_id = _seed_pool(marker)

        def fake_list_members() -> dict[str, list[dict[str, Any]]]:
            return {"members": [{"id": staff_id, "name": "Bridge Staff", "email": f"bridge.staff.{marker}@example.com", "role": "operator", "active": 1}]}

        analytics.staff_service.list_members = fake_list_members
        manager = {"id": 1, "role": "admin", "is_owner": 1, "email": "admin@example.com"}

        before_rows = get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_outreach_suggestions WHERE source_product_sku=?", (product_sku,)).fetchone()
        assert int(before_rows["n"] or 0) == 0, before_rows

        status = analytics.daily_staff_outreach_digest_status(limit=100, staff=manager, product_sku=product_sku)
        assert status["candidate_source"] == "kol_pool_bridge", status
        assert status["bridge_seeded_count"] >= 1, status
        assert status["uncontacted_count"] >= 1, status
        assert status["staff_count"] == 1, status

        result = analytics.generate_daily_staff_outreach_digest(limit=100, staff=manager, product_sku=product_sku)
        assert result["items_per_staff"] >= 1, result
        assert result["candidate_source"] == "outreach_suggestions", result

        after = analytics.daily_staff_outreach_digest_status(limit=100, staff=manager, product_sku=product_sku)
        assert after["ready_staff_count"] == 1, after
        assert after["items_total"] >= 1, after
        stdout_out("VKPI_DAILY_DIGEST_KOL_POOL_BRIDGE_SMOKE_OK")
    finally:
        analytics.staff_service.list_members = original_list_members
        _cleanup(marker, user_id, staff_id, pool_id, product_sku)


if __name__ == "__main__":
    main()
