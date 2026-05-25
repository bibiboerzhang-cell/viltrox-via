#!/usr/bin/env python3
"""Smoke test for the V-KPI 08:00 China daily digest path.

The production scheduler still runs the full morning sync at 08:00 Asia/Shanghai.
This smoke uses the digest-only cron branch with temporary rows so it never
touches real staff digests or external platform syncs.
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
            32000,
            0.045,
            f"https://avatar.example/{marker}_creator.png",
            f"https://youtube.com/@{marker}_creator",
            f"https://youtube.com/watch?v={marker}",
            "Smoke mirrorless lens review content",
            180000,
            8200,
            5,
            92.5,
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
    marker = f"smoke_digest_{secrets.token_hex(5)}"
    product_sku = f"SMOKE-DIGEST-{marker}"
    user_id: int | None = None
    staff_id: int | None = None
    suggestion_id: int | None = None
    try:
        user_id, staff_id = _create_staff(marker)
        suggestion_id = _seed_suggestion(marker, product_sku)
        result = asyncio.run(
            cron.run_job(
                "daily_outreach_digest_only",
                {
                    "limit": 100,
                    "product_sku": product_sku,
                    "staff": {"id": staff_id, "role": "operator", "email": f"{marker}@viltrox-smoke.local"},
                },
            )
        )
        digest = result.get("digest") or {}
        assert result.get("status") == "ok", result
        assert digest.get("staff_count") == 1, digest
        assert digest.get("items_per_staff") == 1, digest
        rows = get_conn().execute("SELECT * FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,)).fetchall()
        assert len(rows) == 1, rows

        from app.services.scheduler import jobs  # noqa: WPS433

        assert str(jobs.CHINA_TZ) == "Asia/Shanghai"
        assert hasattr(jobs, "job_vkpi_morning_sync")
        print("VKPI_MORNING_DIGEST_SMOKE_OK")
    finally:
        _cleanup(marker, user_id, staff_id, suggestion_id)


if __name__ == "__main__":
    main()
