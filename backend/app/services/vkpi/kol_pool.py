"""Self-owned KOL pool and Apify/import adapters."""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.workflow import staff_id as resolve_staff_id


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _platform(value: Any) -> str:
    raw = str(value or "other").strip().lower()
    return {"ig": "instagram", "tt": "tiktok", "yt": "youtube", "twitter": "x", "小红书": "xiaohongshu"}.get(raw, raw or "other")


def _normalize_item(item: dict[str, Any], *, default_platform: str = "") -> dict[str, Any]:
    platform = _platform(item.get("platform") or default_platform or item.get("type") or "other")
    handle = str(item.get("handle") or item.get("username") or item.get("userName") or item.get("channelName") or item.get("name") or "").strip().lstrip("@").lower()
    profile_url = str(item.get("profile_url") or item.get("profileUrl") or item.get("url") or item.get("channelUrl") or "").strip()
    return {
        "platform": platform,
        "handle": handle,
        "profile_url": profile_url,
        "display_name": str(item.get("display_name") or item.get("fullName") or item.get("name") or handle),
        "avatar_url": str(item.get("avatar_url") or item.get("profilePicUrl") or item.get("avatar") or ""),
        "bio": str(item.get("bio") or item.get("biography") or item.get("description") or ""),
        "email": str(item.get("email") or item.get("publicEmail") or ""),
        "followers": _int_or_none(item.get("followers") or item.get("followersCount") or item.get("subscriberCount")),
        "following": _int_or_none(item.get("following") or item.get("followsCount")),
        "posts_count": _int_or_none(item.get("posts_count") or item.get("postsCount") or item.get("videoCount")),
        "avg_views": _int_or_none(item.get("avg_views") or item.get("averageViews")),
        "avg_likes": _int_or_none(item.get("avg_likes") or item.get("averageLikes")),
        "avg_comments": _int_or_none(item.get("avg_comments") or item.get("averageComments")),
        "engagement_rate": _float_or_none(item.get("engagement_rate") or item.get("engagementRate")),
        "raw": item,
    }


def import_items(items: list[dict[str, Any]], *, source_type: str = "manual", source_ref: str = "", platform: str = "", staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    actor = resolve_staff_id(staff) or None
    conn = get_conn()
    imported = 0
    skipped = 0
    rows: list[dict[str, Any]] = []
    now = _utcnow()
    for raw in items:
        item = _normalize_item(raw, default_platform=platform)
        if not item["handle"]:
            skipped += 1
            continue
        uid = f"pool-{secrets.token_hex(8)}"
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
+                (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
+                 followers, following, posts_count, avg_views, avg_likes, avg_comments,
+                 engagement_rate, source_type, source_ref, raw_platform_data, created_by_staff_id,
+                 last_seen_at, created_at, updated_at)
+            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
+            ON CONFLICT(platform, handle) DO UPDATE SET
+                profile_url=excluded.profile_url,
+                display_name=excluded.display_name,
+                avatar_url=excluded.avatar_url,
+                bio=excluded.bio,
+                email=excluded.email,
+                followers=excluded.followers,
+                following=excluded.following,
+                posts_count=excluded.posts_count,
+                avg_views=excluded.avg_views,
+                avg_likes=excluded.avg_likes,
+                avg_comments=excluded.avg_comments,
+                engagement_rate=excluded.engagement_rate,
+                source_type=excluded.source_type,
+                source_ref=excluded.source_ref,
+                raw_platform_data=excluded.raw_platform_data,
+                last_seen_at=excluded.last_seen_at,
+                updated_at=excluded.updated_at
+            """.replace("+", ""),
            (
                uid,
                item["platform"],
                item["handle"],
                item["profile_url"],
                item["display_name"],
                item["avatar_url"],
                item["bio"],
                item["email"],
                item["followers"],
                item["following"],
                item["posts_count"],
                item["avg_views"],
                item["avg_likes"],
                item["avg_comments"],
                item["engagement_rate"],
                source_type,
                source_ref,
                _json(item["raw"]),
                actor,
                now,
                now,
                now,
            ),
        )
        imported += 1
        row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE platform=? AND handle=?", (item["platform"], item["handle"])).fetchone()
        if row:
            rows.append(dict(row))
    conn.commit()
    return {"imported": imported, "skipped": skipped, "items": rows}


def list_pool(limit: int = 100, platform: str = "", query: str = "") -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    where: list[str] = []
    params: list[Any] = []
    if platform:
        where.append("platform=?")
        params.append(_platform(platform))
    if query:
        where.append("(handle LIKE ? OR display_name LIKE ? OR bio LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_kol_pool {clause} ORDER BY COALESCE(viltrox_fit_score, 0) DESC, updated_at DESC LIMIT ?",
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


def get_item(kol_pool_id: int) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    row = get_conn().execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    return {"item": dict(row)}
