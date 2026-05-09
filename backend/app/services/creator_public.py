"""Creator public page service: Via videos, Shop hero config, and click tracking."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.services.student_identity import build_public_vid_profile


DEFAULT_SHOP_HERO = {
    "id": "default-viltrox-shop",
    "title": "Shop by Viltrox",
    "subtitle": "Support the gear I use",
    "imageUrl": "",
    "targetUrl": "https://viltrox.com/collections/all",
    "badge": "Official Store",
    "source": "manual",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_creator_public_tables() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_shop_heroes (
                id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                subtitle TEXT DEFAULT '',
                image_url TEXT NOT NULL,
                target_url TEXT NOT NULL,
                badge TEXT DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_creator_shop_heroes_user ON creator_shop_heroes(user_id, is_active, sort_order)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_public_clicks (
                id BIGSERIAL PRIMARY KEY,
                creator_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                creator_code TEXT NOT NULL,
                click_type TEXT NOT NULL,
                target_url TEXT NOT NULL,
                shop_hero_id TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                ip_hash TEXT DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_creator_public_clicks_creator ON creator_public_clicks(creator_code, created_at DESC)")
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_shop_heroes (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT DEFAULT '',
                image_url TEXT NOT NULL,
                target_url TEXT NOT NULL,
                badge TEXT DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_creator_shop_heroes_user ON creator_shop_heroes(user_id, is_active, sort_order)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_public_clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER,
                creator_code TEXT NOT NULL,
                click_type TEXT NOT NULL,
                target_url TEXT NOT NULL,
                shop_hero_id TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                ip_hash TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_creator_public_clicks_creator ON creator_public_clicks(creator_code, created_at DESC)")
    conn.commit()


def _shop_hero_payload(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"] or ""),
        "title": row["title"] or "",
        "subtitle": row["subtitle"] or "",
        "imageUrl": row["image_url"] or "",
        "targetUrl": row["target_url"] or "",
        "badge": row["badge"] or "",
        "source": row["source"] or "manual",
        "isActive": bool(int(row["is_active"] or 0)),
        "sortOrder": int(row["sort_order"] or 0),
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
    }


def list_creator_shop_heroes(user_id: int, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    _ensure_creator_public_tables()
    if not user_id:
        return []
    conn = get_conn()
    if include_inactive:
        rows = conn.execute(
            """
            SELECT *
            FROM creator_shop_heroes
            WHERE user_id=?
            ORDER BY sort_order ASC, updated_at DESC, id ASC
            """,
            (int(user_id),),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM creator_shop_heroes
            WHERE user_id=? AND is_active=1
            ORDER BY sort_order ASC, updated_at DESC, id ASC
            """,
            (int(user_id),),
        ).fetchall()
    return [_shop_hero_payload(row) for row in rows]


def upsert_creator_shop_hero(payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_creator_public_tables()
    user_id = int(payload.get("user_id") or payload.get("userId") or 0)
    if not user_id:
        raise ValueError("user_id is required")
    title = str(payload.get("title") or "Shop by Viltrox").strip() or "Shop by Viltrox"
    subtitle = str(payload.get("subtitle") or "Support the gear I use").strip()
    image_url = str(payload.get("image_url") or payload.get("imageUrl") or DEFAULT_SHOP_HERO["imageUrl"]).strip()
    target_url = str(payload.get("target_url") or payload.get("targetUrl") or DEFAULT_SHOP_HERO["targetUrl"]).strip()
    badge = str(payload.get("badge") or "").strip()
    source = str(payload.get("source") or "manual").strip() or "manual"
    is_active = 1 if bool(payload.get("is_active", payload.get("isActive", True))) else 0
    sort_order = int(payload.get("sort_order") or payload.get("sortOrder") or 0)
    hero_id = str(payload.get("id") or f"shophero_{user_id}_{secrets.token_hex(6)}").strip()
    if not image_url:
        raise ValueError("imageUrl is required")
    if not target_url:
        raise ValueError("targetUrl is required")
    now = _utcnow()
    conn = get_conn()
    existing = conn.execute("SELECT id FROM creator_shop_heroes WHERE id=?", (hero_id,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE creator_shop_heroes
            SET user_id=?, title=?, subtitle=?, image_url=?, target_url=?, badge=?,
                source=?, is_active=?, sort_order=?, updated_at=?
            WHERE id=?
            """,
            (user_id, title, subtitle, image_url, target_url, badge, source, is_active, sort_order, now, hero_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO creator_shop_heroes
            (id, user_id, title, subtitle, image_url, target_url, badge, source, is_active, sort_order, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (hero_id, user_id, title, subtitle, image_url, target_url, badge, source, is_active, sort_order, now, now),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM creator_shop_heroes WHERE id=?", (hero_id,)).fetchone()
    if not row:
        raise RuntimeError("Shop hero save failed")
    return _shop_hero_payload(row)


def delete_creator_shop_hero(hero_id: str) -> dict[str, Any]:
    _ensure_creator_public_tables()
    clean_id = str(hero_id or "").strip()
    if not clean_id:
        raise ValueError("hero_id is required")
    conn = get_conn()
    conn.execute("DELETE FROM creator_shop_heroes WHERE id=?", (clean_id,))
    conn.commit()
    return {"status": "success", "id": clean_id}


def _video_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "title": row.get("title") or f"Via video #{row.get('id') or ''}",
        "platform": row.get("platform") or "",
        "url": row.get("url") or "",
        "mediaUrl": row.get("media_url") or "",
        "posterUrl": row.get("poster_url") or "",
        "handle": row.get("handle") or "",
        "status": row.get("status") or "",
        "productSeries": row.get("product_series") or "",
        "productLabel": row.get("product_label") or "",
        "score": int(row.get("score") or 0),
        "views": int(row.get("views") or 0),
        "likes": int(row.get("likes") or 0),
        "comments": int(row.get("comments") or 0),
        "shares": int(row.get("shares") or 0),
        "points": int(row.get("points") or 0),
        "createdAt": row.get("created_at") or "",
    }


def _user_avatar_url(user_id: int) -> str:
    if not user_id:
        return ""
    conn = get_conn()
    row = conn.execute("SELECT avatar_url FROM users WHERE id=?", (int(user_id),)).fetchone()
    return str(row["avatar_url"] or "") if row else ""


def build_creator_public_page_data(vid: str) -> dict[str, Any]:
    profile = build_public_vid_profile(vid)
    creator = profile.get("creator") or {}
    creator_id = int(creator.get("id") or 0)
    creator_code = str(creator.get("creator_code") or profile.get("vid") or vid or "").strip()
    shop_heroes = list_creator_shop_heroes(creator_id) if creator_id else []
    featured_videos = [_video_payload(row) for row in profile.get("submissions") or []]
    return {
        "status": "success",
        "creator": {
            "id": str(creator_id or ""),
            "name": creator.get("name") or creator_code or "Viltrox Creator",
            "code": creator_code,
            "avatarUrl": _user_avatar_url(creator_id),
        },
        "featuredVideos": featured_videos,
        "shopHeroes": shop_heroes or [dict(DEFAULT_SHOP_HERO)],
        "accounts": profile.get("accounts") or [],
        "links": profile.get("links") or {},
        "isBound": bool(profile.get("is_bound")),
        "vid": profile.get("vid") or creator_code or vid,
    }


def _hash_ip(ip_value: str) -> str:
    clean = str(ip_value or "").strip()
    if not clean:
        return ""
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:32]


def record_creator_public_click(payload: dict[str, Any], *, user_agent: str = "", ip_address: str = "") -> dict[str, Any]:
    _ensure_creator_public_tables()
    creator_id = int(payload.get("creator_id") or payload.get("creatorId") or 0)
    creator_code = str(payload.get("creator_code") or payload.get("creatorCode") or "").strip()
    click_type = str(payload.get("type") or payload.get("click_type") or "shop_click").strip()
    target_url = str(payload.get("target_url") or payload.get("targetUrl") or "").strip()
    shop_hero_id = str(payload.get("shop_hero_id") or payload.get("shopHeroId") or "").strip()
    if not creator_code:
        raise ValueError("creator_code is required")
    if not target_url:
        raise ValueError("target_url is required")
    now = _utcnow()
    conn = get_conn()
    params = (
        creator_id or None,
        creator_code,
        click_type,
        target_url,
        shop_hero_id,
        str(user_agent or "")[:500],
        _hash_ip(ip_address),
        now,
    )
    if is_postgres_runtime():
        cur = conn.execute(
            """
            INSERT INTO creator_public_clicks
            (creator_id, creator_code, click_type, target_url, shop_hero_id, user_agent, ip_hash, created_at)
            VALUES (?,?,?,?,?,?,?,?) RETURNING id
            """,
            params,
        )
        click_id = int(cur.lastrowid or 0)
    else:
        cur = conn.execute(
            """
            INSERT INTO creator_public_clicks
            (creator_id, creator_code, click_type, target_url, shop_hero_id, user_agent, ip_hash, created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            params,
        )
        click_id = int(cur.lastrowid or 0)
    if table_exists("attribution_clicks"):
        conn.execute(
            """
            INSERT INTO attribution_clicks
            (ref_code, ref_type, utm_source, utm_medium, utm_campaign, session_id, user_agent, ip_hash, landing_path, clicked_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                creator_code,
                "creator",
                "creator_public",
                "shop_card",
                shop_hero_id,
                "",
                str(user_agent or "")[:500],
                _hash_ip(ip_address),
                target_url,
                now,
            ),
        )
    conn.commit()
    return {"status": "success", "id": click_id}
