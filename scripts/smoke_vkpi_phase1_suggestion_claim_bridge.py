#!/usr/bin/env python3
"""Smoke test for suggestion claim -> main KOL -> active claim bridge."""
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
from app.services.vkpi.schema import ensure_vkpi_schema  # noqa: E402
from app.domains.analytics.schema import ensure_vkpi_analytics_schema  # noqa: E402
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_staff(marker: str) -> tuple[int, int, dict[str, Any]]:
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
    conn.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})", values)
    staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
    conn.commit()
    return user_id, staff_id, {"id": staff_id, "role": "operator", "email": email}


def _seed_suggestion(marker: str) -> int:
    ensure_vkpi_analytics_schema()
    conn = get_conn()
    handle = f"vkpi_claim_bridge_{marker}"
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
            f"SMOKE-CLAIM-BRIDGE-{marker}",
            _now(),
            "youtube",
            handle,
            handle,
            54000,
            0.061,
            f"https://avatar.example/{handle}.png",
            f"https://www.youtube.com/@{handle}",
            f"https://www.youtube.com/watch?v={marker}",
            "Viltrox lens review smoke buyer video",
            240000,
            12000,
            5,
            95.0,
            True,
            "new",
            _json({"marker": marker, "smoke": True}),
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM vkpi_outreach_suggestions WHERE suggestion_uid=?", (f"sug-{marker}",)).fetchone()["id"])


def _cleanup(marker: str, user_id: int | None, staff_id: int | None, kol_id: int | None, suggestion_id: int | None) -> dict[str, int]:
    conn = get_conn()
    like = f"%{marker}%"
    if suggestion_id:
        conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE id=?", (suggestion_id,))
    conn.execute("DELETE FROM vkpi_outreach_suggestions WHERE suggestion_uid LIKE ? OR metadata_json LIKE ?", (like, like))
    if kol_id:
        conn.execute("DELETE FROM vkpi_kol_claims WHERE kol_id=?", (kol_id,))
        conn.execute("DELETE FROM kols WHERE id=?", (kol_id,))
    conn.execute("DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ?", (like, like))
    if staff_id:
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    if user_id:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    return {
        "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ?", (like,)).fetchone()["n"]),
        "staff": int(conn.execute("SELECT COUNT(*) AS n FROM staff WHERE id=?", (staff_id or 0,)).fetchone()["n"]),
        "suggestions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_outreach_suggestions WHERE suggestion_uid LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
        "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ?", (like,)).fetchone()["n"]),
        "claims": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_claims WHERE metadata_json LIKE ?", (like,)).fetchone()["n"]),
        "business_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ?", (like, like)).fetchone()["n"]),
    }


def main() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_analytics_schema()
    ensure_vkpi_audit_schema()
    marker = f"smoke_claim_bridge_{secrets.token_hex(5)}"
    user_id: int | None = None
    staff_id: int | None = None
    kol_id: int | None = None
    suggestion_id: int | None = None
    try:
        user_id, staff_id, staff = _create_staff(marker)
        suggestion_id = _seed_suggestion(marker)
        result = analytics.claim_suggestion(suggestion_id, staff=staff)
        suggestion = result["suggestion"]
        kol = result["kol"]
        claim = result["claim"]
        kol_id = int(kol["id"])
        assert result["claim_status"] == "created", result
        assert suggestion["status"] == "claimed", suggestion
        assert int(suggestion["existing_kol_id"]) == kol_id, suggestion
        assert int(suggestion["claimed_by_staff_id"]) == staff_id, suggestion
        assert int(claim["kol_id"]) == kol_id and int(claim["staff_id"]) == staff_id, claim
        assert str(kol["avatar_url"]).endswith(".png"), kol
        assert "Viltrox lens review" in str(kol["notes"]), kol
        audit_count = int(
            get_conn()
            .execute(
                "SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type='outreach_suggestion_claim' AND target_id=?",
                (str(kol_id),),
            )
            .fetchone()["n"]
        )
        assert audit_count >= 1, audit_count
        cleanup = _cleanup(marker, user_id, staff_id, kol_id, suggestion_id)
        assert sum(cleanup.values()) == 0, cleanup
        stdout_out("VKPI_PHASE1_SUGGESTION_CLAIM_BRIDGE_SMOKE_OK")
    except Exception:
        _cleanup(marker, user_id, staff_id, kol_id, suggestion_id)
        raise


if __name__ == "__main__":
    main()
