"""Shared KOL claim helpers for V-KPI."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import claim_access
from app.domains.kol.claim_payloads import (
    claim_payload,
    json_array,
    json_object,
)
from app.domains.kol.identity import (
    HANDLE_RE,
    PLATFORM_ALIASES,
    SUPPORTED_PLATFORMS,
    dedup_key,
    normalize_handle,
    normalize_platform,
)

logger = get_logger(__name__)

def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _json(value: Any) -> str:
    return json_object(value)

def _json_array(value: Any) -> str:
    return json_array(value)

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default

def _claim_payload(row: Any) -> dict[str, Any]:
    return claim_payload(row)

def _find_kol(platform: str, handle: str) -> dict[str, Any] | None:
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

def _safe_json_loads(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception as exc:
        logger.warning("kol claims json parse failed: %s", exc)
        return fallback

def _rows_or_empty(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        logger.warning("kol claims rows query failed: %s", exc)
        return []

def _row_or_empty(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    conn = get_conn()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception as exc:
        logger.warning("kol claims row query failed: %s", exc)
        return {}

def assert_kol_access(kol_id: int, staff: dict[str, Any] | None, *, allow_unclaimed: bool = False) -> None:
    claim_access.assert_kol_access(kol_id, staff, allow_unclaimed=allow_unclaimed)

def _assert_kol_access(kol_id: int, staff: dict[str, Any] | None) -> None:
    assert_kol_access(kol_id, staff, allow_unclaimed=False)

def _create_kol(platform: str, handle: str, body: dict[str, Any], actor_staff_id: int) -> dict[str, Any]:
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
            _json_array(body.get("contact_links")),
            _json(body.get("contact_raw")),
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
