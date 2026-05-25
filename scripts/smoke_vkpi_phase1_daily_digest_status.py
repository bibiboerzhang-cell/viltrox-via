#!/usr/bin/env python3
"""Smoke test for V-KPI daily Top-100 digest status.

This validates the 08:00 China scheduling contract, per-staff digest coverage,
and the "uncontacted only" candidate rule without calling external APIs.
"""
from __future__ import annotations

import asyncio
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
from app.domains.sync import cron  # noqa: E402
from app.services.vkpi import analytics  # noqa: E402
from app.services.vkpi.schema_analytics import ensure_vkpi_analytics_schema  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_staff(marker: str) -> tuple[int, int]:
    conn = get_conn()
    now = _now()
    email = f"{marker}@viltrox-smoke.local"
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", marker, "approved", "operator", 1, f"https://avatar.example/{marker}.png"),
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


def _seed_suggestion(marker: str, product_sku: str) -> int:
    ensure_vkpi_analytics_schema()
    conn = get_conn()
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
            f"sug-{marker}",
            product_sku,
            _now(),
            "youtube",
            f"{marker}_creator",
            f"{marker}_creator",
            42000,
            0.051,
            f"https://avatar.example/{marker}_creator.png",
            f"https://youtube.com/@{marker}_creator",
            f"https://youtube.com/watch?v={marker}",
            "Smoke Viltrox lens review with buyer intent",
            210000,
            11200,
            5,
            91.0,
            True,
            "new",
            _json({"marker": marker, "smoke": True}),
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM vkpi_outreach_suggestions WHERE suggestion_uid=?", (f"sug-{marker}",)).fetchone()["id"])


def _cleanup(marker: str, user_id: int | None, staff_id: int | None, suggestion_id: int | None) -> None:
    conn = get_conn()
    if staff_id:
        digest_rows = conn.execute("SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,)).fetchall()
        for row in digest_rows:
            conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (int(row["id"]),))
        conn.execute("DELETE FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,))
    if suggestion_id:
        conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE id=?", (suggestion_id,))
    conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE suggestion_uid=?", (f"sug-{marker}",))
    if staff_id:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    if user_id:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


def main() -> None:
    marker = f"smoke_digest_status_{secrets.token_hex(5)}"
    product_sku = f"SMOKE-DIGEST-STATUS-{marker}"
    user_id: int | None = None
    staff_id: int | None = None
    suggestion_id: int | None = None
    try:
        user_id, staff_id = _create_staff(marker)
        suggestion_id = _seed_suggestion(marker, product_sku)
        staff = {"id": staff_id, "role": "operator", "email": f"{marker}@viltrox-smoke.local"}

        before = analytics.daily_staff_outreach_digest_status(limit=100, staff=staff, product_sku=product_sku)
        assert before["scheduled_time"] == "08:00", before
        assert before["timezone"] == "Asia/Shanghai", before
        assert before["limit_per_staff"] == 100, before
        assert before["staff_count"] == 1, before
        assert before["digest_count"] == 0, before
        assert before["uncontacted_count"] == 1, before

        result = asyncio.run(
            cron.run_job(
                "daily_outreach_digest_only",
                {"limit": 100, "product_sku": product_sku, "staff": staff},
            )
        )
        assert result["status"] == "ok", result

        after = analytics.daily_staff_outreach_digest_status(limit=100, staff=staff, product_sku=product_sku)
        assert after["digest_count"] == 1, after
        assert after["ready_staff_count"] == 1, after
        assert after["items_total"] == 1, after
        assert "未联系" in after["rule"], after
        print("VKPI_PHASE1_DAILY_DIGEST_STATUS_SMOKE_OK")
    finally:
        _cleanup(marker, user_id, staff_id, suggestion_id)


if __name__ == "__main__":
    main()
