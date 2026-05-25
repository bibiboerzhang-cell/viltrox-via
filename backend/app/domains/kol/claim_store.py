"""KOL claim persistence helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.connection import get_conn
from app.domains.kol.claim_payloads import json_array, json_object


def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def find_kol(platform: str, handle: str) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT *
        FROM kols
        WHERE lower(platform)=lower(?)
          AND (lower(channel_name)=lower(?) OR lower(channel_url)=lower(?))
        ORDER BY id DESC
        LIMIT 1
        """,
        (platform, handle, handle),
    ).fetchone()
    return dict(row) if row else None


def create_kol(platform: str, handle: str, body: dict[str, Any], actor_staff_id: int) -> dict[str, Any]:
    now = utcnow()
    conn = get_conn()
    channel_url = str(body.get("url") or body.get("channel_url") or "").strip()
    conn.execute(
        """
        INSERT INTO kols (
            channel_name, channel_url, platform, country, niche, project_name,
            owner_name, media_name, duplicate_flag, scale_tier, content_type,
            approval_note, channel_tags, affiliate_id, affiliate_link, discount_code,
            amazon_link, short_link, primary_category, promoted_product, follower_count,
            avg_views, contact_email, contact_phone, contact_status, notes,
            avatar_url, profile_url, contact_links_json, contact_raw_json,
            assigned_staff_id, created_by_staff_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            handle,
            channel_url,
            platform,
            str(body.get("country") or ""),
            str(body.get("niche") or body.get("category") or ""),
            str(body.get("project_name") or ""),
            str(body.get("owner_name") or ""),
            str(body.get("media_name") or ""),
            "",
            str(body.get("scale_tier") or ""),
            str(body.get("content_type") or ""),
            "",
            str(body.get("channel_tags") or ""),
            "",
            "",
            str(body.get("discount_code") or ""),
            str(body.get("amazon_link") or ""),
            str(body.get("short_link") or ""),
            str(body.get("primary_category") or ""),
            str(body.get("promoted_product") or ""),
            _int(body.get("follower_count")),
            _int(body.get("avg_views")),
            str(body.get("contact_email") or body.get("email") or ""),
            str(body.get("contact_phone") or ""),
            str(body.get("contact_status") or "cold"),
            str(body.get("notes") or ""),
            str(body.get("avatar_url") or ""),
            str(body.get("profile_url") or channel_url),
            json_array(body.get("contact_links")),
            json_object(body.get("contact_raw")),
            actor_staff_id or None,
            actor_staff_id or None,
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT * FROM kols WHERE lower(platform)=lower(?) AND lower(channel_name)=lower(?) ORDER BY id DESC LIMIT 1",
        (platform, handle),
    ).fetchone()
    return dict(row) if row else {}
