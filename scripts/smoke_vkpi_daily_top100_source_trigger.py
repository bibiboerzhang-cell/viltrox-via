#!/usr/bin/env python3
"""Smoke test for Daily Top100 monitored-product source repair.

The smoke proves the missing upstream source problem can be diagnosed and fixed
without live crawlers: a real product_sku is registered as monitored, a scoped
suggestion is assigned once, and all marker data is cleaned up.
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
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.db.connection import get_conn  # noqa: E402
from app.services.vkpi import analytics  # noqa: E402
from app.services.vkpi.schema_analytics import ensure_vkpi_analytics_schema  # noqa: E402
from audit_vkpi_daily_top100_source import ProductCandidate, audit_state, upsert_candidates  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_staff(marker: str) -> tuple[int, int]:
    conn = get_conn()
    now = _now()
    email = f"top100.source.{marker}@example.com"
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", f"Top100 Source {marker}", "approved", "operator", 1, f"https://avatar.example/{marker}.png"),
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


def _seed_suggestion(marker: str, product_sku: str, staff_id: int) -> int:
    ensure_vkpi_analytics_schema()
    conn = get_conn()
    now = _now()
    handle = f"top100_source_creator_{marker}"
    metadata = {
        "marker": marker,
        "source": "smoke_product_monitor",
        "responsible_staff_id": staff_id,
    }
    conn.execute(
        """
        INSERT INTO vkpi_outreach_suggestions
            (suggestion_uid, source_product_sku, detected_at, platform, handle, channel_name,
             follower_count, engagement_rate, country_code, avatar_url, profile_url,
             source_video_url, source_video_title, source_view_count, source_like_count,
             source_published_at, mention_count, is_viral, priority, score, status,
             metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"sug-top100-source-{marker}",
            product_sku,
            now,
            "youtube",
            handle,
            f"Top100 Source Creator {marker}",
            88000,
            4.2,
            "US",
            f"https://avatar.example/{marker}.png",
            f"https://youtube.com/@{handle}",
            f"https://youtube.com/watch?v={marker[:8]}",
            "Viltrox lens review source trigger smoke",
            145000,
            4200,
            now,
            1,
            True,
            5,
            88.5,
            "new",
            _json(metadata),
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM vkpi_outreach_suggestions WHERE suggestion_uid=?", (f"sug-top100-source-{marker}",)).fetchone()["id"])


def _cleanup(marker: str, product_sku: str, user_id: int | None, staff_id: int | None) -> None:
    conn = get_conn()
    if staff_id:
        digest_rows = conn.execute("SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,)).fetchall()
        for row in digest_rows:
            conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (int(row["id"]),))
        conn.execute("DELETE FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,))
    conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE source_product_sku=? OR metadata_json LIKE ?", (product_sku, f"%{marker}%"))
    conn.execute("DELETE FROM vkpi_monitored_products WHERE product_sku=?", (product_sku,))
    if staff_id:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    if user_id:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


def main() -> None:
    marker = f"daily_top100_source_{secrets.token_hex(5)}"
    product_sku = f"P311-TOP100-SOURCE-{marker}"
    product_name = f"Smoke Lens Source {marker}"
    user_id: int | None = None
    staff_id: int | None = None
    original_list_members = analytics.staff_service.list_members
    try:
        initial = audit_state(limit=10)
        assert "product_candidates" in initial, initial

        dry = upsert_candidates([ProductCandidate(product_sku, product_name, "explicit", 1)], platforms=["youtube", "instagram"], apply=False)
        assert len(dry["planned"]) == 1, dry
        assert not dry["applied"], dry
        assert not get_conn().execute("SELECT * FROM vkpi_monitored_products WHERE product_sku=?", (product_sku,)).fetchone()

        applied = upsert_candidates([ProductCandidate(product_sku, product_name, "explicit", 1)], platforms=["youtube", "instagram"], apply=True)
        assert len(applied["applied"]) == 1, applied
        product = get_conn().execute("SELECT * FROM vkpi_monitored_products WHERE product_sku=?", (product_sku,)).fetchone()
        assert product, applied
        assert str(product["product_name"] or "") == product_name, dict(product)

        user_id, staff_id = _create_staff(marker)
        _seed_suggestion(marker, product_sku, staff_id)

        def fake_list_members() -> dict[str, list[dict[str, Any]]]:
            return {"members": [{"id": staff_id, "name": "Top100 Source Staff", "email": f"top100.source.{marker}@example.com", "role": "operator", "active": 1}]}

        analytics.staff_service.list_members = fake_list_members
        manager = {"id": 1, "role": "admin", "is_owner": 1, "email": "admin@example.com"}
        status = analytics.daily_staff_outreach_digest_status(target_date="2099-02-11", limit=100, staff=manager, product_sku=product_sku)
        assert status["candidate_source"] == "outreach_suggestions", status
        assert status["uncontacted_count"] >= 1, status
        result = analytics.generate_daily_staff_outreach_digest(target_date="2099-02-11", limit=100, staff=manager, product_sku=product_sku)
        assert result["items_total"] == 1, result
        assert result["duplicate_suggestion_count"] == 0, result
        print("VKPI_DAILY_TOP100_SOURCE_TRIGGER_SMOKE_OK")
    finally:
        analytics.staff_service.list_members = original_list_members
        _cleanup(marker, product_sku, user_id, staff_id)


if __name__ == "__main__":
    main()
