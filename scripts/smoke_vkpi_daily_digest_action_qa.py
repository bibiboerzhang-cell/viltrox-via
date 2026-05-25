#!/usr/bin/env python3
"""Smoke test for Daily Top100 action QA.

This covers the user-facing P3.7H flow:
- generated digest rows carry profile/source links for the UI buttons
- assignment metadata is visible to the frontend
- dismissing a suggestion removes it from the next generated digest
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

from app.db.connection import get_conn  # noqa: E402
from app.domains import analytics  # noqa: E402
from app.domains.analytics.schema import ensure_vkpi_analytics_schema  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_staff(marker: str) -> tuple[int, int, dict[str, Any]]:
    conn = get_conn()
    now = _now()
    email = f"p37h.{marker}@example.com"
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", f"P37H Staff {marker}", "approved", "operator", 1, f"https://avatar.example/{marker}.png"),
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
    return user_id, staff_id, {"id": staff_id, "role": "operator", "email": email}


def _seed_suggestion(marker: str, product_sku: str, index: int, staff_id: int) -> int:
    ensure_vkpi_analytics_schema()
    conn = get_conn()
    handle = f"p37h_creator_{marker}_{index}"
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
            f"sug-p37h-{marker}-{index}",
            product_sku,
            _now(),
            "youtube",
            handle,
            f"P37H Creator {index}",
            50000 + index,
            0.04 + (index / 100),
            f"https://avatar.example/{handle}.png",
            f"https://www.youtube.com/@{handle}",
            f"https://www.youtube.com/watch?v=p37h{marker}{index}",
            f"P3.7H action QA video {index}",
            90000 - index,
            5000 - index,
            5 - index,
            91.0 - index,
            True,
            "new",
            _json({"marker": marker, "responsible_staff_id": staff_id, "smoke": "p37h-action-qa"}),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM vkpi_outreach_suggestions WHERE suggestion_uid=?", (f"sug-p37h-{marker}-{index}",)).fetchone()
    return int(row["id"])


def _digest_suggestion_ids(staff_id: int, target_date: str) -> list[int]:
    conn = get_conn()
    digest = conn.execute(
        "SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=? AND digest_date=?",
        (staff_id, target_date),
    ).fetchone()
    if not digest:
        return []
    rows = conn.execute(
        "SELECT suggestion_id FROM vkpi_staff_outreach_digest_items WHERE digest_id=? ORDER BY rank",
        (int(digest["id"]),),
    ).fetchall()
    return [int(row["suggestion_id"]) for row in rows]


def _cleanup(marker: str, user_id: int | None, staff_id: int | None, product_sku: str) -> dict[str, int]:
    conn = get_conn()
    like = f"%{marker}%"
    if staff_id:
        digest_rows = conn.execute("SELECT id FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,)).fetchall()
        for row in digest_rows:
            conn.execute("DELETE FROM vkpi_staff_outreach_digest_items WHERE digest_id=?", (int(row["id"]),))
        conn.execute("DELETE FROM vkpi_staff_outreach_digests WHERE staff_id=?", (staff_id,))
    conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE source_product_sku=? OR metadata_json LIKE ?", (product_sku, like))
    if staff_id:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    if user_id:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    return {
        "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ?", (like,)).fetchone()["n"]),
        "staff": int(conn.execute("SELECT COUNT(*) AS n FROM staff WHERE id=?", (staff_id or 0,)).fetchone()["n"]),
        "suggestions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_outreach_suggestions WHERE source_product_sku=? OR metadata_json LIKE ?", (product_sku, like)).fetchone()["n"]),
    }


def main() -> None:
    ensure_vkpi_analytics_schema()
    marker = f"p37h_{secrets.token_hex(5)}"
    product_sku = f"SMOKE-DAILY-ACTION-QA-{marker}"
    target_date = "2099-01-23"
    user_id: int | None = None
    staff_id: int | None = None
    original_list_members = analytics.staff_service.list_members
    try:
        user_id, staff_id, staff = _create_staff(marker)
        first_id = _seed_suggestion(marker, product_sku, 0, staff_id)
        second_id = _seed_suggestion(marker, product_sku, 1, staff_id)

        def fake_list_members() -> dict[str, list[dict[str, Any]]]:
            return {"members": [{"id": staff_id, "name": "P37H Staff", "email": staff["email"], "role": "operator", "active": 1}]}

        analytics.staff_service.list_members = fake_list_members
        manager = {"id": 1, "role": "admin", "is_owner": 1, "email": "admin@example.com"}

        generated = analytics.generate_daily_staff_outreach_digest(
            target_date=target_date,
            limit=100,
            staff=manager,
            product_sku=product_sku,
        )
        assert generated["items_total"] == 2, generated
        assert generated["owned_assignment_count"] == 2, generated
        assert generated["fallback_assignment_count"] == 0, generated
        assert generated["duplicate_suggestion_count"] == 0, generated

        digest = analytics.list_daily_staff_outreach_digest(staff_id, target_date=target_date, limit=100)
        items = digest.get("items") or []
        assert len(items) == 2, digest
        first_item = next(item for item in items if int(item["suggestion_id"]) == first_id)
        metadata = json.loads(str(first_item.get("metadata_json") or "{}"))
        assert first_item["profile_url"].startswith("https://www.youtube.com/@"), first_item
        assert first_item["source_video_url"].startswith("https://www.youtube.com/watch"), first_item
        assert metadata["assignment_reason"] == "metadata.responsible_staff_id", metadata
        assert int(metadata["assignment_staff_id"]) == staff_id, metadata

        dismissed = analytics.dismiss_suggestion(first_id, reason="P3.7H action QA", staff=staff)
        assert dismissed["suggestion"]["status"] == "dismissed", dismissed
        assert int(dismissed["suggestion"]["dismissed_by_staff_id"]) == staff_id, dismissed

        regenerated = analytics.generate_daily_staff_outreach_digest(
            target_date=target_date,
            limit=100,
            staff=manager,
            product_sku=product_sku,
        )
        assert regenerated["items_total"] == 1, regenerated
        current_ids = _digest_suggestion_ids(staff_id, target_date)
        assert first_id not in current_ids, current_ids
        assert second_id in current_ids, current_ids

        status = analytics.daily_staff_outreach_digest_status(target_date=target_date, limit=100, staff=manager, product_sku=product_sku)
        assert status["items_total"] == 1, status
        assert status["owned_assignment_count"] == 1, status
        assert status["fallback_assignment_count"] == 0, status
        cleanup = _cleanup(marker, user_id, staff_id, product_sku)
        assert sum(cleanup.values()) == 0, cleanup
        print("VKPI_DAILY_DIGEST_ACTION_QA_SMOKE_OK")
    finally:
        analytics.staff_service.list_members = original_list_members
        _cleanup(marker, user_id, staff_id, product_sku)


if __name__ == "__main__":
    main()
