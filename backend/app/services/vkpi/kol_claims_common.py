"""Shared KOL claim helpers for V-KPI."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.vkpi import scope

logger = get_logger(__name__)

SUPPORTED_PLATFORMS = {
    "ig",
    "instagram",
    "tt",
    "tiktok",
    "yt",
    "youtube",
    "xhs",
    "bili",
    "bilibili",
    "fb",
    "facebook",
    "reddit",
    "x",
    "twitter",
    "threads",
    "twitch",
    "pinterest",
    "vimeo",
    "discord",
    "website",
    "blog",
    "weibo",
    "douyin",
    "zhihu",
    "linkedin",
    "telegram",
    "newsletter",
    "forum",
    "community",
    "other",
}
PLATFORM_ALIASES = {
    "instagram": "ig",
    "insta": "ig",
    "tiktok": "tt",
    "youtube": "yt",
    "bilibili": "bili",
    "facebook": "fb",
    "twitter": "x",
    "blog": "website",
    "community": "forum",
}
HANDLE_RE = re.compile(r"[^a-z0-9._-]+")

def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)

def _json_array(value: Any) -> str:
    return json.dumps(value if isinstance(value, list) else [], ensure_ascii=False, default=str)

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default

def normalize_platform(platform: str) -> str:
    clean = str(platform or "").strip().lower().replace(" ", "_")
    return PLATFORM_ALIASES.get(clean, clean)

def normalize_handle(value: str, platform: str = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://placeholder/{raw}")
    candidate = raw
    if parsed.netloc and parsed.netloc != "placeholder":
        parts = [part for part in parsed.path.split("/") if part]
        candidate = parts[-1] if parts else parsed.netloc
    candidate = candidate.strip().lstrip("@").split("?", 1)[0].split("#", 1)[0].lower()
    if normalize_platform(platform) == "yt" and candidate.startswith("channel/"):
        candidate = candidate.rsplit("/", 1)[-1]
    return HANDLE_RE.sub("", candidate)

def dedup_key(platform: str, handle: str, email: str = "") -> str:
    parts = [f"{normalize_platform(platform)}:{normalize_handle(handle, platform)}"]
    email_clean = str(email or "").strip().lower()
    if email_clean:
        parts.append(f"email:{email_clean}")
    return hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()

def _claim_payload(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    data["is_active"] = str(data.get("status") or "") == "active"
    return data

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
    if scope.can_view_all(staff):
        return
    actor = scope.actor_staff_id(staff)
    if not actor:
        raise scope.ScopeDenied("kol scope denied")
    conn = get_conn()
    kol = conn.execute(
        "SELECT assigned_staff_id, created_by_staff_id FROM kols WHERE id=?",
        (int(kol_id),),
    ).fetchone()
    if not kol:
        raise LookupError("kol not found")
    kol_data = dict(kol)
    assigned_staff_id = _int(kol_data.get("assigned_staff_id"))
    created_by_staff_id = _int(kol_data.get("created_by_staff_id"))
    active_claim = conn.execute(
        "SELECT staff_id FROM vkpi_kol_claims WHERE kol_id=? AND status='active' ORDER BY claimed_at DESC, id DESC LIMIT 1",
        (int(kol_id),),
    ).fetchone()
    if active_claim:
        claim_staff_id = _int(dict(active_claim).get("staff_id"))
        if claim_staff_id == actor:
            return
        raise scope.ScopeDenied("kol scope denied")
    if actor in {assigned_staff_id, created_by_staff_id}:
        return
    if allow_unclaimed and not assigned_staff_id:
        return
    found = conn.execute(
        """
        SELECT 1
        FROM vkpi_kol_claims
        WHERE kol_id=? AND staff_id=?
        UNION
        SELECT 1
        FROM vkpi_projects
        WHERE kol_id=? AND (assigned_staff_id=? OR created_by_staff_id=?)
        UNION
        SELECT 1
        FROM vkpi_links
        WHERE kol_id=? AND (staff_id=? OR created_by_staff_id=?)
        UNION
        SELECT 1
        FROM vkpi_sales_attributions
        WHERE kol_id=? AND staff_id=?
        LIMIT 1
        """,
        (int(kol_id), actor, int(kol_id), actor, actor, int(kol_id), actor, actor, int(kol_id), actor),
    ).fetchone()
    if not found:
        raise scope.ScopeDenied("kol scope denied")

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
