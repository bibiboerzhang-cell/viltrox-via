"""Employee platform bindings and sync state for Viltrox Marketing."""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import secrets
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from app.db.connection import get_conn
from app.services.cache import cache_clear, cache_get, cache_set
from app.services.vkpi import scope
from app.services.vkpi.media_cache import cached_image_url, cached_video_url
from app.services.vkpi.schema_channels import ensure_vkpi_channels_schema
from app.services.vkpi.workflow import staff_id as resolve_staff_id

SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok", "xhs", "xiaohongshu", "bilibili", "facebook", "reddit", "x", "threads", "twitch", "pinterest", "vimeo", "website", "other"}
CHANNEL_READ_CACHE_TTL_SEC = 300


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _channel_cache_scope(staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None) -> str:
    if view_as_staff_id:
        return f"staff:{int(view_as_staff_id)}"
    if scope.can_view_all(staff):
        return "all"
    return f"staff:{resolve_staff_id(staff) or 0}"


def _channel_cache_key(name: str, *, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None, limit: int = 0) -> str:
    return f"vkpi:channels:{name}:{_channel_cache_scope(staff, view_as_staff_id)}:limit:{int(limit or 0)}"


def _clear_channel_read_cache() -> None:
    try:
        cache_clear(prefix="vkpi:channels:")
    except Exception:
        pass


def _channel_cache_hit(payload: Any) -> Any:
    if isinstance(payload, dict):
        result = dict(payload)
        result["cache"] = {"hit": True, "ttl_sec": CHANNEL_READ_CACHE_TTL_SEC}
        return result
    return payload


def _channel_cache_store(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = {**payload, "cache": {"hit": False, "ttl_sec": CHANNEL_READ_CACHE_TTL_SEC}}
    cache_set(key, result, ttl=CHANNEL_READ_CACHE_TTL_SEC)
    return result


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _count(value: Any) -> int:
    return max(0, _int(value))


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _cumulative_floor_status(raw_payload: dict[str, Any], *, sample_count: int = 0) -> dict[str, Any]:
    floor = raw_payload.get("cumulative_floor") if isinstance(raw_payload, dict) else {}
    if not isinstance(floor, dict):
        floor = {}
    fields = floor.get("fields") if isinstance(floor.get("fields"), dict) else {}
    protected_fields = sorted(str(key) for key, value in fields.items() if value is not None)
    if not protected_fields:
        return {
            "baseline_protected": False,
            "baseline_protected_fields": [],
            "baseline_protected_detail": {},
        }
    details: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, dict):
            details[str(key)] = {
                "provider_value": _int(value.get("provider_value")),
                "kept_value": _int(value.get("kept_value")),
            }
    if sample_count:
        label = f"本轮样本 {sample_count} 条 < 历史累计，沿用历史值"
    else:
        label = "本轮样本小于历史累计，沿用历史值"
    return {
        "baseline_protected": True,
        "baseline_protected_label": label,
        "baseline_protected_reason": _text(
            floor.get("reason"),
            "provider returned a narrower sample or missing cumulative value than the previous official-account baseline",
        ),
        "baseline_protected_fields": protected_fields,
        "baseline_protected_detail": details,
    }


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _actor(staff: dict[str, Any] | None) -> int:
    return resolve_staff_id(staff) or 0


def _fernet() -> Fernet:
    key = os.environ.get("VKPI_CHANNELS_ENCRYPTION_KEY") or os.environ.get("JWT_SECRET") or os.environ.get("APP_SECRET") or "vkpi-local-dev-key"
    raw = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return _fernet().encrypt(text.encode("utf-8")).decode("utf-8")


def _mask(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return f"{text[:4]}...{text[-4:]}" if len(text) > 8 else "****"


def _platform(value: Any) -> str:
    raw = str(value or "other").strip().lower()
    if raw == "twitter":
        raw = "x"
    if raw == "red":
        raw = "reddit"
    if raw in {"xiaohongshu", "小红书"}:
        raw = "xhs"
    return raw if raw in SUPPORTED_PLATFORMS else "other"


def bind_channel(body: dict[str, Any], *, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    actor = _actor(staff)
    requested_staff_id = view_as_staff_id or body.get("staff_id")
    target_staff_id = int(scope.effective_staff_id(staff, requested_staff_id) or actor or 0)
    if not target_staff_id:
        raise ValueError("staff_id required")
    platform = _platform(body.get("platform"))
    handle = str(body.get("account_handle") or body.get("handle") or "").strip().lstrip("@")
    if not handle:
        raise ValueError("account_handle required")
    api_key = str(body.get("api_key") or "")
    api_secret = str(body.get("api_secret") or "")
    uid = f"chan-{secrets.token_hex(8)}"
    now = _utcnow()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_employee_channels
            (channel_uid, staff_id, platform, account_handle, account_display_name, account_url, avatar_url,
             api_key_encrypted, api_secret_encrypted, refresh_token_encrypted, access_token_encrypted,
             auth_method, self_reported_followers, self_reported_posts, status, last_sync_status,
             metadata_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(staff_id, platform, account_handle) DO UPDATE SET
            account_display_name=excluded.account_display_name,
            account_url=excluded.account_url,
            avatar_url=excluded.avatar_url,
            api_key_encrypted=CASE WHEN excluded.api_key_encrypted!='' THEN excluded.api_key_encrypted ELSE vkpi_employee_channels.api_key_encrypted END,
            api_secret_encrypted=CASE WHEN excluded.api_secret_encrypted!='' THEN excluded.api_secret_encrypted ELSE vkpi_employee_channels.api_secret_encrypted END,
            self_reported_followers=excluded.self_reported_followers,
            self_reported_posts=excluded.self_reported_posts,
            status='active',
            updated_at=excluded.updated_at
        """,
        (
            uid,
            target_staff_id,
            platform,
            handle,
            str(body.get("account_display_name") or body.get("display_name") or handle),
            str(body.get("account_url") or body.get("url") or ""),
            str(body.get("avatar_url") or ""),
            _encrypt(api_key),
            _encrypt(api_secret),
            _encrypt(str(body.get("refresh_token") or "")),
            _encrypt(str(body.get("access_token") or "")),
            str(body.get("auth_method") or "manual_api_key"),
            _int(body.get("self_reported_followers")),
            _int(body.get("self_reported_posts")),
            "active",
            "not_configured" if not api_key else "configured_pending_provider",
            _json({"api_key_mask": _mask(api_key), "api_secret_mask": _mask(api_secret)}),
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM vkpi_employee_channels WHERE staff_id=? AND platform=? AND account_handle=?", (target_staff_id, platform, handle)).fetchone()
    channel_id = int(row["id"]) if row else 0
    conn.execute("INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at) VALUES (?,?,?,?,?)", (channel_id or None, actor or target_staff_id, "bind", f"{platform}:{handle}", now))
    conn.commit()
    _clear_channel_read_cache()
    return {"channel": get_channel(channel_id, staff=staff).get("channel", {})}


def _visible_staff_id(staff: dict[str, Any] | None, view_as_staff_id: int | None = None) -> int | None:
    actor = _actor(staff)
    return scope.effective_staff_id(staff, view_as_staff_id) or actor or None


def _assert_channel_access(channel_id: int, staff: dict[str, Any] | None) -> None:
    if scope.can_view_all(staff):
        return
    actor = _actor(staff)
    if not actor:
        raise scope.ScopeDenied("channel scope denied")
    row = get_conn().execute(
        "SELECT staff_id FROM vkpi_employee_channels WHERE id=? AND deleted_at IS NULL",
        (int(channel_id),),
    ).fetchone()
    if not row:
        return
    if int(dict(row).get("staff_id") or 0) != actor:
        raise scope.ScopeDenied("channel scope denied")


def _channel_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ["api_key_encrypted", "api_secret_encrypted", "refresh_token_encrypted", "access_token_encrypted"]:
        data.pop(key, None)
    meta = {}
    try:
        meta = json.loads(data.get("metadata_json") or "{}")
    except Exception:
        meta = {}
    data["api_key_mask"] = meta.get("api_key_mask", "")
    data["sync_status"] = data.get("last_sync_status") or "待同步"
    return data


def get_channel(channel_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff)
    row = get_conn().execute("SELECT * FROM vkpi_employee_channels WHERE id=? AND deleted_at IS NULL", (int(channel_id),)).fetchone()
    if not row:
        raise LookupError("channel not found")
    return {"channel": _channel_row_to_dict(row)}


def list_channels(*, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None, limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    safe_limit = max(1, min(300, int(limit or 100)))
    cache_key = _channel_cache_key("list", staff=staff, view_as_staff_id=view_as_staff_id, limit=safe_limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return _channel_cache_hit(cached)
    target = _visible_staff_id(staff, view_as_staff_id)
    where = "WHERE deleted_at IS NULL"
    params: list[Any] = []
    if target:
        where += " AND staff_id=?"
        params.append(target)
    rows = get_conn().execute(
        f"""
        SELECT c.*,
               (SELECT followers FROM vkpi_channel_metrics m WHERE m.channel_id=c.id ORDER BY snapshot_date DESC LIMIT 1) AS latest_followers,
               (SELECT posts_count FROM vkpi_channel_metrics m WHERE m.channel_id=c.id ORDER BY snapshot_date DESC LIMIT 1) AS latest_posts,
               (SELECT total_views FROM vkpi_channel_metrics m WHERE m.channel_id=c.id ORDER BY snapshot_date DESC LIMIT 1) AS latest_views
        FROM vkpi_employee_channels c
        {where}
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    return _channel_cache_store(cache_key, {"channels": [_channel_row_to_dict(row) for row in rows]})


def unbind_channel(channel_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff)
    actor = _actor(staff)
    now = _utcnow()
    get_conn().execute("UPDATE vkpi_employee_channels SET status='revoked', deleted_at=?, updated_at=? WHERE id=?", (now, now, int(channel_id)))
    get_conn().execute("INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at) VALUES (?,?,?,?,?)", (int(channel_id), actor or None, "unbind", "manual unbind", now))
    get_conn().commit()
    _clear_channel_read_cache()
    return {"status": "revoked", "channel_id": int(channel_id)}


def sync_now(channel_id: int, *, staff: dict[str, Any] | None = None, max_posts: int = 12) -> dict[str, Any]:
    """Sync a bound official channel through configured providers."""
    ensure_vkpi_channels_schema()
    channel = get_channel(channel_id, staff=staff)["channel"]
    from app.services.vkpi import channel_refill

    result = channel_refill.sync_channel_snapshot(channel, staff=staff, max_posts=max(1, min(1000, int(max_posts or 12))))
    _clear_channel_read_cache()
    return result


def metrics(channel_id: int, limit: int = 30, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff)
    rows = get_conn().execute("SELECT * FROM vkpi_channel_metrics WHERE channel_id=? ORDER BY snapshot_date DESC LIMIT ?", (int(channel_id), max(1, min(365, int(limit or 30))))).fetchall()
    return {"metrics": [dict(row) for row in rows], "sync_status": "待同步" if not rows else "synced"}


def team_overview() -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    cache_key = _channel_cache_key("team_overview", limit=200)
    cached = cache_get(cache_key)
    if cached is not None:
        return _channel_cache_hit(cached)
    try:
        rows = get_conn().execute("SELECT * FROM vkpi_team_channels_overview LIMIT 200").fetchall()
        return _channel_cache_store(cache_key, {"rows": [dict(row) for row in rows]})
    except Exception:
        rows = get_conn().execute(
            """
            SELECT staff_id, COUNT(*) AS active_channels, 0 AS total_followers, 0 AS total_views, MAX(last_sync_at) AS most_recent_sync_at
            FROM vkpi_employee_channels
            WHERE deleted_at IS NULL AND status='active'
            GROUP BY staff_id
            ORDER BY active_channels DESC
            """
        ).fetchall()
        return _channel_cache_store(cache_key, {"rows": [dict(row) for row in rows]})


def team_detail(staff_id: int) -> dict[str, Any]:
    return list_channels(staff={"id": int(staff_id)}, limit=200)


def _latest_official_channel_rows(staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None) -> list[dict[str, Any]]:
    ensure_vkpi_channels_schema()
    target = _visible_staff_id(staff, view_as_staff_id) if view_as_staff_id or not scope.can_view_all(staff) else None
    where = "WHERE c.deleted_at IS NULL AND c.status='active'"
    params: list[Any] = []
    if target:
        where += " AND c.staff_id=?"
        params.append(target)
    rows = get_conn().execute(
        f"""
        SELECT c.*,
               COALESCE(u.name, u.email, 'Staff ' || c.staff_id) AS staff_name,
               COALESCE(u.email, '') AS staff_email,
               COALESCE(u.avatar_url, '') AS staff_avatar_url,
               COALESCE(st.role, '') AS staff_role,
               COALESCE(st.active, 1) AS staff_active,
               m.snapshot_date,
               m.followers AS metric_followers,
               m.posts_count AS metric_posts,
               m.total_views AS metric_views,
               m.total_likes AS metric_likes,
               m.total_comments AS metric_comments,
               m.total_shares AS metric_shares,
               m.followers_delta AS metric_followers_delta,
               m.posts_delta AS metric_posts_delta,
               m.views_delta_24h AS metric_views_delta,
               m.engagement_rate AS metric_engagement_rate,
               m.raw_payload_json AS metric_raw_payload_json,
               m.captured_at AS metric_captured_at
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
            SELECT id FROM vkpi_channel_metrics mm
            WHERE mm.channel_id = c.id
            ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
            LIMIT 1
        )
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        {where}
        ORDER BY c.platform ASC, c.account_handle ASC, c.id ASC
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _platform_label(platform: str) -> str:
    labels = {
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "facebook": "Facebook",
        "x": "X",
        "reddit": "Reddit",
    }
    return labels.get(str(platform or "").lower(), str(platform or "Other").title())


def _raw_sample(row: dict[str, Any]) -> dict[str, Any]:
    raw = _parse_json(row.get("metric_raw_payload_json"))
    sample = raw.get("raw_sample") if isinstance(raw.get("raw_sample"), dict) else raw
    return sample if isinstance(sample, dict) else {}


def _account_name(row: dict[str, Any]) -> str:
    return _text(row.get("account_display_name"), row.get("account_handle"), "官方账号")


def _account_url(row: dict[str, Any]) -> str:
    return _text(row.get("account_url"))


def _cached_media_url(raw_url: Any) -> str:
    text = _text(raw_url)
    return cached_image_url(text) or text


def _cached_video_media_url(raw_url: Any) -> str:
    text = _text(raw_url)
    return cached_video_url(text) or text


def _looks_like_image_media_url(url: str, *, key_hint: str = "") -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if not host or host.startswith("v.redd.it") or host in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return False
    image_hosts = (
        "cdninstagram.com",
        "fbcdn.net",
        "xx.fbcdn.net",
        "ytimg.com",
        "googleusercontent.com",
        "tiktokcdn.com",
        "byteoversea.com",
        "apifyusercontent.com",
        "redd.it",
        "redditmedia.com",
        "twimg.com",
    )
    if any(part in host for part in image_hosts):
        return True
    if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")):
        return True
    return key_hint not in {"url", "permalink", "postUrl", "topLevelUrl", "facebookUrl"}


def _image_url(value: Any, *, key_hint: str = "", depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, str):
        text = _text(value)
        if not text.startswith(("http://", "https://")):
            return ""
        host = urllib.parse.urlparse(text).hostname or ""
        if key_hint == "url" and not any(part in host for part in ["fbcdn", "cdninstagram", "ytimg", "redd.it", "redditmedia"]):
            return ""
        return text
    if isinstance(value, list):
        for item in value:
            found = _image_url(item, depth=depth + 1)
            if found:
                return found
        return ""
    if isinstance(value, dict):
        for key in ["thumbnailUrl", "thumbnail", "displayUrl", "imageUrl", "picture", "uri", "photo_image", "thumbnailImage", "image", "media"]:
            found = _image_url(value.get(key), key_hint=key, depth=depth + 1)
            if found:
                return found
        return _image_url(value.get("url"), key_hint="url", depth=depth + 1)
    return ""


def _post_from_instagram(item: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    direct_posts = _items(item.get("posts")) or ([item] if _text(item.get("dedupe_key"), item.get("shortCode"), item.get("url")) else [])
    for post in direct_posts:
        child_posts = _items(post.get("childPosts"))
        video_url = _text(post.get("videoUrl"), _video_url(post.get("video")), _video_url(post.get("media")), _video_url(post))
        image_urls = _media_urls(
            post.get("images"),
            post.get("media"),
            post.get("displayResources"),
            post.get("sidecarChildren"),
            post.get("edge_sidecar_to_children"),
            [child.get("displayUrl") or child.get("imageUrl") for child in child_posts],
            post.get("displayUrl"),
            post.get("imageUrl"),
            post.get("thumbnailUrl"),
            post.get("media_url"),
        )
        media_kind = _media_type_kind(post.get("type") or post.get("productType") or post.get("mediaType"))
        posts.append(
            {
                "id": _text(post.get("id"), post.get("source_id"), post.get("shortCode"), post.get("short_code"), post.get("url")),
                "source_id": _text(post.get("shortCode"), post.get("id"), post.get("source_id")),
                "title": _text(post.get("caption"), post.get("title"), post.get("alt"), "Instagram 内容"),
                "url": _text(post.get("url"), post.get("shortCodeUrl"), post.get("displayUrl")),
                "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", post.get("displayUrl"), post.get("imageUrl"), post.get("media_url"), post.get("videoUrl"))),
                "video_url": _cached_video_media_url(video_url),
                "image_urls": image_urls[:12],
                "media_kind": "video" if video_url or media_kind == "video" else ("carousel" if len(image_urls) > 1 or media_kind == "carousel" else media_kind or "image"),
                "posted_at": _text(post.get("timestamp"), post.get("createdAt"), post.get("posted_at")),
                "views": _int(post.get("views"), _int(post.get("videoViewCount"), _int(post.get("videoPlayCount")))),
                "likes": _int(post.get("likes"), _int(post.get("likesCount"))),
                "comments": _int(post.get("comments"), _int(post.get("commentsCount"))),
                "shares": _int(post.get("shares"), _int(post.get("shareCount"))),
            }
        )
    for post in _items(item.get("latestPosts")):
        child_posts = _items(post.get("childPosts"))
        video_url = _text(post.get("videoUrl"), _video_url(post.get("video")), _video_url(post.get("media")), _video_url(post))
        image_urls = _media_urls(
            post.get("images"),
            post.get("media"),
            post.get("displayResources"),
            post.get("sidecarChildren"),
            post.get("edge_sidecar_to_children"),
            [child.get("displayUrl") or child.get("imageUrl") for child in child_posts],
            post.get("displayUrl"),
            post.get("imageUrl"),
            post.get("thumbnailUrl"),
        )
        media_kind = _media_type_kind(post.get("type") or post.get("productType") or post.get("mediaType"))
        posts.append(
            {
                "id": _text(post.get("id"), post.get("shortCode"), post.get("url")),
                "source_id": _text(post.get("shortCode"), post.get("id")),
                "title": _text(post.get("caption"), post.get("alt"), "Instagram 内容"),
                "url": _text(post.get("url"), post.get("displayUrl")),
                "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", post.get("displayUrl"), post.get("imageUrl"), post.get("videoUrl"))),
                "video_url": _cached_video_media_url(video_url),
                "image_urls": image_urls[:12],
                "media_kind": "video" if video_url or media_kind == "video" else ("carousel" if len(image_urls) > 1 or media_kind == "carousel" else media_kind or "image"),
                "posted_at": _text(post.get("timestamp"), post.get("createdAt")),
                "views": _int(post.get("videoViewCount"), _int(post.get("videoPlayCount"))),
                "likes": _int(post.get("likesCount"), _int(post.get("likes"))),
                "comments": _int(post.get("commentsCount"), _int(post.get("comments"))),
                "shares": _int(post.get("shareCount")),
            }
        )
    if not posts and _int(row.get("metric_views")):
        posts.append(_account_level_post(row))
    return posts


def _post_from_tiktok(item: dict[str, Any]) -> dict[str, Any]:
    media = _items(item.get("mediaUrls"))
    video_meta = item.get("videoMeta") if isinstance(item.get("videoMeta"), dict) else {}
    video_url = _text(
        _video_url(media),
        _video_url(video_meta),
        _video_url(item.get("video")),
        _video_url(item),
    )
    image_urls = _media_urls(
        video_meta.get("coverUrl"),
        video_meta.get("originalCoverUrl"),
        video_meta.get("coverUrl"),
        video_meta.get("originalCoverUrl"),
        item.get("coverUrl"),
        item.get("thumbnailUrl"),
        item.get("dynamicCover"),
    )
    return {
        "id": _text(item.get("id"), item.get("webVideoUrl")),
        "source_id": _text(item.get("id")),
        "title": _text(item.get("text"), "TikTok 内容"),
        "url": _text(item.get("webVideoUrl"), item.get("url")),
        "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", media[0].get("url") if media else "", item.get("coverUrl"), item.get("thumbnailUrl"), item.get("webVideoUrl"))),
        "video_url": _cached_video_media_url(video_url),
        "image_urls": image_urls[:12],
        "media_kind": "video" if video_url else "image",
        "posted_at": _text(item.get("createTimeISO"), item.get("createTime")),
        "views": _int(item.get("playCount")),
        "likes": _int(item.get("diggCount")),
        "comments": _int(item.get("commentCount")),
        "shares": _int(item.get("shareCount")),
    }


def _post_from_youtube(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    thumb = {}
    for key in ["maxres", "standard", "high", "medium", "default"]:
        if isinstance(thumbnails.get(key), dict):
            thumb = thumbnails[key]
            break
    video_id = _text(item.get("id"))
    if isinstance(item.get("id"), dict):
        video_id = _text(item["id"].get("videoId"), item["id"].get("channelId"))
    return {
        "id": video_id,
        "title": _text(snippet.get("title"), "YouTube 内容"),
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        "media_url": _cached_media_url(thumb.get("url")),
        "posted_at": _text(snippet.get("publishedAt")),
        "views": _int(stats.get("viewCount")),
        "likes": _int(stats.get("likeCount")),
        "comments": _int(stats.get("commentCount")),
        "shares": 0,
    }


def _post_from_facebook(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("error"):
        return None
    url = _text(item.get("url"), item.get("postUrl"), item.get("topLevelUrl"))
    if not url or not _text(item.get("postId"), item.get("text"), item.get("media"), item.get("isVideo")):
        return None
    return {
        "id": _text(item.get("postId"), item.get("id"), url),
        "title": _text(item.get("text"), item.get("message"), "Facebook 内容"),
        "url": url,
        "media_url": _cached_media_url(_text(_image_url(item.get("media")), _image_url(item.get("image")), _image_url(item.get("picture")))),
        "posted_at": _text(item.get("time"), item.get("timestamp"), item.get("createdAt")),
        "views": _int(item.get("views"), _int(item.get("videoViews"), _int(item.get("videoViewCount"), _int(item.get("viewsCount"))))),
        "likes": _int(item.get("likes"), _int(item.get("reactions"), _int(item.get("reactionLikeCount"), _int(item.get("topReactionsCount"))))),
        "comments": _int(item.get("comments"), _int(item.get("commentsCount"))),
        "shares": _int(item.get("shares"), _int(item.get("sharesCount"))),
    }


def _post_from_x(item: dict[str, Any]) -> dict[str, Any]:
    image_urls = _media_urls(item.get("extendedEntities"), item.get("entities"), item.get("media"), item.get("photos"))
    video_url = _video_url(item.get("extendedEntities")) or _video_url(item.get("entities")) or _video_url(item.get("media")) or _video_url(item.get("video"))
    return {
        "id": _text(item.get("id"), item.get("url"), item.get("twitterUrl")),
        "title": _text(item.get("fullText"), item.get("text"), "X 内容"),
        "url": _text(item.get("twitterUrl"), item.get("url")),
        "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", video_url)),
        "video_url": _cached_video_media_url(video_url),
        "image_urls": image_urls[:12],
        "media_kind": "video" if video_url else ("image" if image_urls else "post"),
        "posted_at": _text(item.get("createdAt")),
        "views": _int(item.get("viewCount")),
        "likes": _int(item.get("likeCount")),
        "comments": _int(item.get("replyCount")),
        "shares": _int(item.get("retweetCount")),
    }


def _post_from_reddit(item: dict[str, Any]) -> dict[str, Any] | None:
    data_type = str(item.get("dataType") or "").lower()
    if data_type in {"community", "subreddit", "comment"} or item.get("numberOfMembers"):
        return None
    url = _text(item.get("url"), item.get("permalink"))
    if not url:
        return None
    image_urls = _media_urls(
        item.get("imageUrls"),
        item.get("link"),
        item.get("preview"),
        item.get("media"),
        item.get("secureMedia"),
        item.get("image"),
        item.get("thumbnailUrl"),
        item.get("url"),
    )
    video_url = _text(_video_url(item.get("media")), _video_url(item.get("secureMedia")), _video_url(item))
    return {
        "id": _text(item.get("id"), item.get("name"), url),
        "source_id": _text(item.get("parsedId"), item.get("id"), item.get("name")),
        "title": _text(item.get("title"), item.get("body"), "Reddit 内容"),
        "url": url,
        "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", item.get("thumbnailUrl"))),
        "video_url": _cached_video_media_url(video_url),
        "image_urls": image_urls[:12],
        "media_kind": "video" if video_url or _bool(item.get("isVideo")) else ("image" if image_urls else "post"),
        "posted_at": _text(item.get("createdAt")),
        "views": _int(item.get("views")),
        "likes": _int(item.get("upVotes"), _int(item.get("score"))),
        "comments": _int(item.get("numberOfComments"), _int(item.get("comments"))),
        "shares": 0,
        "views_unavailable": True,
        "views_metric_label": "公开播放",
        "views_unavailable_reason": "Reddit 不公开帖子播放量；今年分析使用点赞、评论和站内评分。",
    }


def _account_level_post(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"channel-{row.get('id')}",
        "title": f"{_account_name(row)} 账号快照",
        "url": _account_url(row),
        "media_url": _cached_media_url(row.get("avatar_url")),
        "posted_at": _text(row.get("metric_captured_at"), row.get("last_sync_at")),
        "views": _int(row.get("metric_views")),
        "likes": _int(row.get("metric_likes")),
        "comments": _int(row.get("metric_comments")),
        "shares": _int(row.get("metric_shares")),
        "account_level": True,
    }


def _extract_posts(row: dict[str, Any], *, per_account_limit: int) -> list[dict[str, Any]]:
    platform = str(row.get("platform") or "").lower()
    sample = _raw_sample(row)
    posts: list[dict[str, Any]] = []
    if platform == "instagram":
        sample_posts = _items(sample.get("posts"))
        if sample_posts:
            posts.extend(_post_from_instagram({"posts": sample_posts}, row))
        else:
            for item in _items(sample.get("items")):
                posts.extend(_post_from_instagram(item, row))
    elif platform == "tiktok":
        posts.extend(_post_from_tiktok(item) for item in _items(sample.get("items")))
    elif platform == "youtube":
        posts.extend(_post_from_youtube(item) for item in _items(sample.get("videos")))
    elif platform == "facebook":
        for item in _items(sample.get("items")):
            post = _post_from_facebook(item)
            if post:
                posts.append(post)
    elif platform == "x":
        posts.extend(_post_from_x(item) for item in _items(sample.get("items")))
    elif platform == "reddit":
        reddit_items = _items(sample.get("items")) or _items(sample.get("posts"))
        for item in reddit_items:
            post = _post_from_reddit(item)
            if post:
                posts.append(post)
    posts = [post for post in posts if post.get("id") or post.get("url")]
    seen: set[str] = set()
    unique_posts: list[dict[str, Any]] = []
    for post in posts:
        key = _text(post.get("url"), post.get("id"))
        if key in seen:
            continue
        seen.add(key)
        unique_posts.append(post)
    posts = unique_posts
    if not posts and _int(row.get("metric_views")):
        posts = [_account_level_post(row)]
    return posts[: max(1, min(50, int(per_account_limit or 10)))]


def official_account_matrix(*, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None, limit: int = 50) -> dict[str, Any]:
    """Return platform -> official account -> post hierarchy for data-analysis UI."""
    safe_limit = max(1, min(50, int(limit or 50)))
    cache_key = _channel_cache_key("official_matrix", staff=staff, view_as_staff_id=view_as_staff_id, limit=safe_limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return _channel_cache_hit(cached)
    rows = _latest_official_channel_rows(staff=staff, view_as_staff_id=view_as_staff_id)
    platforms: dict[str, dict[str, Any]] = {}
    total_views = 0
    total_posts = 0
    for row in rows:
        platform = str(row.get("platform") or "other").lower()
        platform_entry = platforms.setdefault(
            platform,
            {
                "platform": platform,
                "label": _platform_label(platform),
                "total_views": 0,
                "total_posts": 0,
                "total_followers": 0,
                "followers_delta": 0,
                "posts_delta": 0,
                "views_delta": 0,
                "baseline_protected": False,
                "baseline_protected_accounts": 0,
                "baseline_protected_fields": [],
                "baseline_protected_detail": {},
                "accounts": [],
            },
        )
        sample_limit = safe_limit
        raw_payload = _parse_json(row.get("metric_raw_payload_json"))
        package_posts = _posts_from_package(_text(raw_payload.get("package_dir")))
        posts = package_posts[:sample_limit] if package_posts else _extract_posts(row, per_account_limit=limit)
        floor_status = _cumulative_floor_status(raw_payload, sample_count=len(posts))
        account_views = _int(row.get("metric_views"))
        account_posts = _int(row.get("metric_posts"), len(posts))
        account_followers = _int(row.get("metric_followers"))
        account_followers_delta = _int(row.get("metric_followers_delta"))
        account_posts_delta = _int(row.get("metric_posts_delta"))
        account_views_delta = _int(row.get("metric_views_delta"))
        platform_entry["total_views"] += account_views
        platform_entry["total_posts"] += account_posts
        platform_entry["total_followers"] += account_followers
        platform_entry["followers_delta"] += account_followers_delta
        platform_entry["posts_delta"] += account_posts_delta
        platform_entry["views_delta"] += account_views_delta
        if floor_status.get("baseline_protected"):
            platform_entry["baseline_protected"] = True
            platform_entry["baseline_protected_accounts"] += 1
            field_set = set(platform_entry.get("baseline_protected_fields") or [])
            field_set.update(floor_status.get("baseline_protected_fields") or [])
            platform_entry["baseline_protected_fields"] = sorted(field_set)
            detail = platform_entry.get("baseline_protected_detail") if isinstance(platform_entry.get("baseline_protected_detail"), dict) else {}
            detail[str(row.get("id") or "")] = floor_status.get("baseline_protected_detail") or {}
            platform_entry["baseline_protected_detail"] = detail
            platform_entry["baseline_protected_label"] = f"{platform_entry['baseline_protected_accounts']} 个账号基线保护"
            platform_entry["baseline_protected_reason"] = "部分账号本轮样本小于历史累计，沿用历史值。"
        total_views += account_views
        total_posts += account_posts
        platform_entry["accounts"].append(
            {
                "id": int(row.get("id") or 0),
                "staff_id": _int(row.get("staff_id")),
                "staff_name": _text(row.get("staff_name"), row.get("staff_email"), f"Staff {_int(row.get('staff_id'))}"),
                "staff_email": _text(row.get("staff_email")),
                "staff_avatar_url": _text(row.get("staff_avatar_url")),
                "staff_role": _text(row.get("staff_role")),
                "staff_active": bool(_int(row.get("staff_active"), 1)),
                "platform": platform,
                "platform_label": _platform_label(platform),
                "handle": str(row.get("account_handle") or ""),
                "display_name": _account_name(row),
                "account_url": _account_url(row),
                "avatar_url": _cached_media_url(row.get("avatar_url")),
                "sync_status": row.get("last_sync_status") or "not_configured",
                "last_sync_at": row.get("last_sync_at"),
                "last_sync_error": _text(row.get("last_sync_error")),
                "followers": account_followers,
                "followers_delta": account_followers_delta,
                "posts_count": account_posts,
                "posts_delta": account_posts_delta,
                "total_views": account_views,
                "views_delta": account_views_delta,
                "total_likes": _int(row.get("metric_likes")),
                "total_comments": _int(row.get("metric_comments")),
                "total_shares": _int(row.get("metric_shares")),
                "engagement_rate": _float(row.get("metric_engagement_rate")),
                **floor_status,
                "posts": posts,
            }
        )
    return _channel_cache_store(cache_key, {
        "platforms": sorted(platforms.values(), key=lambda item: item["label"]),
        "account_count": len(rows),
        "post_count": total_posts,
        "total_views": total_views,
    })


def _latest_channel_row(channel_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff)
    row = get_conn().execute(
        """
        SELECT c.*,
               COALESCE(u.name, u.email, 'Staff ' || c.staff_id) AS staff_name,
               COALESCE(u.email, '') AS staff_email,
               COALESCE(u.avatar_url, '') AS staff_avatar_url,
               COALESCE(st.role, '') AS staff_role,
               COALESCE(st.active, 1) AS staff_active,
               m.snapshot_date,
               m.followers AS metric_followers,
               m.posts_count AS metric_posts,
               m.total_views AS metric_views,
               m.total_likes AS metric_likes,
               m.total_comments AS metric_comments,
               m.total_shares AS metric_shares,
               m.engagement_rate AS metric_engagement_rate,
               m.raw_payload_json AS metric_raw_payload_json,
               m.captured_at AS metric_captured_at
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
            SELECT id FROM vkpi_channel_metrics mm
            WHERE mm.channel_id = c.id
            ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
            LIMIT 1
        )
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE c.id=? AND c.deleted_at IS NULL
        LIMIT 1
        """,
        (int(channel_id),),
    ).fetchone()
    if not row:
        raise LookupError("channel not found")
    return dict(row)


def _media_type_kind(value: Any) -> str:
    text = _text(value).lower()
    if text in {"video", "reel", "reels", "clips"}:
        return "video"
    if text in {"sidecar", "carousel", "album"}:
        return "carousel"
    if text in {"image", "photo"}:
        return "image"
    return text


def _media_urls(*values: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def push(value: Any, *, key_hint: str = "") -> None:
        if isinstance(value, dict):
            for key in (
                "displayUrl",
                "imageUrl",
                "thumbnailUrl",
                "thumbnail",
                "uri",
                "picture",
                "photo_image",
        "thumbnailImage",
        "profilePictureUrl",
        "profilePicUrlHD",
        "profilePicUrl",
        "displayResources",
        "sidecarChildren",
        "edge_sidecar_to_children",
        "coverPhotoUrl",
        "coverUrl",
        "media_url",
                "media_url_https",
                "preview_image_url",
                "url",
            ):
                push(value.get(key), key_hint=key)
            return
        if isinstance(value, list):
            for item in value:
                push(item, key_hint=key_hint)
            return
        url = _text(value)
        if url.startswith("["):
            try:
                push(json.loads(url), key_hint=key_hint)
            except Exception:
                pass
            return
        if not url or not url.startswith(("http://", "https://")) or url in seen:
            return
        if not _looks_like_image_media_url(url, key_hint=key_hint):
            return
        seen.add(url)
        urls.append(_cached_media_url(url))

    for value in values:
        push(value)
    return urls


def _video_url(value: Any, *, depth: int = 0) -> str:
    if depth > 7:
        return ""
    if isinstance(value, str):
        url = _text(value)
        if not url.startswith(("http://", "https://")):
            return ""
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        if ".mp4" in parsed.path.lower() or "googlevideo.com" in host or host.endswith("v.redd.it") or host.endswith("video.twimg.com") or "video-" in host:
            return url
        return ""
    if isinstance(value, list):
        for item in value:
            found = _video_url(item, depth=depth + 1)
            if found:
                return found
        return ""
    if isinstance(value, dict):
        for key in ("videoUrl", "browser_native_hd_url", "browser_native_sd_url", "playable_url", "fallback_url", "url"):
            found = _video_url(value.get(key), depth=depth + 1)
            if found:
                return found
        for item in value.values():
            found = _video_url(item, depth=depth + 1)
            if found:
                return found
    return ""


def _raw_package_posts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    posts = raw.get("posts") if isinstance(raw.get("posts"), dict) else {}
    profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    profile_items = _items(profile.get("items"))
    latest = _items(profile_items[0].get("latestPosts")) if profile_items else []
    profile_posts = [
        item for item in profile_items
        if _text(item.get("dataType"), item.get("type")).lower() not in {"community", "subreddit_profile", "comment"}
        and (_text(item.get("title")) or _text(item.get("url"), item.get("permalink")))
    ]
    return [*_items(posts.get("items")), *latest, *profile_posts]


def _raw_post_index(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in _raw_package_posts(raw):
        for key in (
            _text(item.get("id")),
            _text(item.get("name")),
            _text(item.get("postId")),
            _text(item.get("parsedId")),
            _text(item.get("shortCode"), item.get("code")),
            _text(item.get("url")),
            _text(item.get("postUrl")),
            _text(item.get("topLevelUrl")),
            _text(item.get("facebookUrl")),
            _text(item.get("permalink")),
        ):
            if key and key not in index:
                index[key] = item
    return index


def _enrich_package_post(post: dict[str, Any], raw_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw = raw_index.get(_text(post.get("source_id"))) or raw_index.get(_text(post.get("short_code"))) or raw_index.get(_text(post.get("url"))) or {}
    child_posts = _items(raw.get("childPosts"))
    video_url = _text(raw.get("videoUrl"), _video_url(raw.get("media")), _video_url(raw))
    image_urls = _media_urls(
        raw.get("images"),
        raw.get("media"),
        raw.get("image"),
        raw.get("picture"),
        raw.get("thumbnailUrl"),
        raw.get("thumbnail"),
        raw.get("coverUrl"),
        raw.get("coverPhotoUrl"),
        raw.get("displayResources"),
        raw.get("sidecarChildren"),
        raw.get("edge_sidecar_to_children"),
        [child.get("displayUrl") or child.get("imageUrl") for child in child_posts],
        raw.get("displayUrl"),
        raw.get("imageUrl"),
        raw.get("media_url"),
        raw.get("media_url_https"),
        raw.get("preview_image_url"),
    )
    media_kind = _media_type_kind(post.get("media_type") or raw.get("type") or raw.get("productType"))
    if video_url:
        post["video_url"] = _cached_video_media_url(video_url)
    if image_urls:
        post["image_urls"] = image_urls[:12]
    post["media_kind"] = "video" if video_url or media_kind == "video" else ("carousel" if len(image_urls) > 1 or media_kind == "carousel" else media_kind or "image")
    platform = _text(post.get("platform")).lower()
    instagram_image_views_unavailable = platform == "instagram" and post["media_kind"] in {"image", "carousel"} and _int(post.get("views")) <= 0
    post["views_unavailable"] = instagram_image_views_unavailable or platform == "reddit"
    if post["views_unavailable"]:
        post["views_metric_label"] = "公开播放"
        post["views_unavailable_reason"] = (
            "Reddit 不公开帖子播放量；今年分析使用点赞、评论和站内评分。"
            if platform == "reddit"
            else "IG 图文/轮播没有公开视频播放量，需要后台 Insights 才能补齐。"
        )
    if platform == "reddit":
        post["score"] = _int(raw.get("score"), _int(post.get("likes")))
        post["upvote_ratio"] = _float(raw.get("upvoteRatio"), _float(raw.get("upvote_ratio")))
        post["author"] = _text(raw.get("author"), raw.get("authorName"), raw.get("username"))
        post["subreddit"] = _text(raw.get("subreddit"), raw.get("subredditName"), raw.get("communityName"))
        post["flair"] = _text(raw.get("flair"), raw.get("linkFlairText"), raw.get("postFlair"))
        post["is_locked"] = _bool(raw.get("locked"), _bool(raw.get("isLocked")))
        post["is_pinned"] = _bool(raw.get("stickied"), _bool(raw.get("pinned"), _bool(raw.get("isPinned"))))
        post["is_removed"] = _bool(raw.get("removed"), _bool(raw.get("isRemoved"))) or bool(_text(raw.get("removedByCategory")))
    return post


def _post_from_package_row(row: dict[str, Any]) -> dict[str, Any]:
    post = {
        "id": _text(row.get("source_id"), row.get("short_code"), row.get("url"), row.get("dedupe_key")),
        "source_id": _text(row.get("source_id")),
        "short_code": _text(row.get("short_code")),
        "platform": _text(row.get("platform")),
        "title": _text(row.get("title"), "内容"),
        "url": _text(row.get("url")),
        "media_url": _cached_media_url(row.get("media_url")),
        "video_url": _cached_video_media_url(row.get("video_url")),
        "media_type": _text(row.get("media_type")),
        "posted_at": _text(row.get("posted_at")),
        "views": _count(row.get("views")),
        "likes": _count(row.get("likes")),
        "comments": _count(row.get("comments")),
        "shares": _count(row.get("shares")),
        "reaction_total": _count(row.get("reaction_total")),
        "reaction_like": _count(row.get("reaction_like")),
        "reaction_love": _count(row.get("reaction_love")),
        "reaction_care": _count(row.get("reaction_care")),
        "reaction_haha": _count(row.get("reaction_haha")),
        "reaction_wow": _count(row.get("reaction_wow")),
        "reaction_sad": _count(row.get("reaction_sad")),
        "reaction_angry": _count(row.get("reaction_angry")),
        "dedupe_key": _text(row.get("dedupe_key")),
    }
    image_urls = _media_urls(_parse_json(row.get("image_urls")), row.get("image_urls"))
    if image_urls:
        post["image_urls"] = image_urls[:12]
    return post


def _resolve_package_dir(package_dir: str) -> Path | None:
    if not package_dir:
        return None
    path = Path(package_dir).expanduser()
    if (path / "posts.csv").exists():
        return path
    marker = "tmp/vkpi_channel_packages/"
    raw = str(package_dir)
    if marker in raw:
        suffix = raw.split(marker, 1)[1]
        for base in (Path.cwd(), Path("/opt/viltrox-2.0")):
            candidate = base / "tmp" / "vkpi_channel_packages" / suffix
            if (candidate / "posts.csv").exists():
                return candidate
    return None


def _posts_from_package(package_dir: str) -> list[dict[str, Any]]:
    resolved = _resolve_package_dir(package_dir)
    if not resolved:
        return []
    path = resolved / "posts.csv"
    if not path.exists() or not path.is_file():
        return []
    raw_index: dict[str, dict[str, Any]] = {}
    raw_path = resolved / "raw.json"
    if raw_path.exists() and raw_path.is_file():
        raw_index = _raw_post_index(_parse_json(raw_path.read_text(encoding="utf-8")))
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [_enrich_package_post(_post_from_package_row(dict(row)), raw_index) for row in csv.DictReader(handle)]


def _raw_package(package_dir: str) -> dict[str, Any]:
    resolved = _resolve_package_dir(package_dir)
    if not resolved:
        return {}
    raw_path = resolved / "raw.json"
    if not raw_path.exists() or not raw_path.is_file():
        return {}
    return _parse_json(raw_path.read_text(encoding="utf-8"))


def _raw_package_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    return _items(profile.get("items")) or _items(raw.get("items"))


def _post_datetime(post: dict[str, Any]) -> datetime | None:
    raw = _text(post.get("posted_at"))
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _filter_posts_by_window(posts: list[dict[str, Any]], window: str) -> list[dict[str, Any]]:
    key = str(window or "all").lower()
    days_by_key = {"7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}
    if key == "all":
        return posts
    now = datetime.now(timezone.utc)
    if key == "year":
        return [post for post in posts if (posted_at := _post_datetime(post)) and posted_at.year == now.year]
    days = days_by_key.get(key)
    if not days:
        return posts
    cutoff = now - timedelta(days=days)
    return [post for post in posts if (posted_at := _post_datetime(post)) and posted_at >= cutoff]


def _sort_posts(posts: list[dict[str, Any]], sort: str, direction: str) -> list[dict[str, Any]]:
    sort_key = str(sort or "latest").lower()
    reverse = str(direction or "desc").lower() != "asc"
    metric_keys = {"views", "likes", "comments", "shares"}
    if sort_key in metric_keys:
        if sort_key == "views":
            available = [post for post in posts if not post.get("views_unavailable")]
            unavailable = [post for post in posts if post.get("views_unavailable")]
            unavailable.sort(key=lambda post: _post_datetime(post) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            return [
                *sorted(available, key=lambda post: (_int(post.get(sort_key)), _post_datetime(post) or datetime.min.replace(tzinfo=timezone.utc)), reverse=reverse),
                *unavailable,
            ]
        return sorted(posts, key=lambda post: (_int(post.get(sort_key)), _post_datetime(post) or datetime.min.replace(tzinfo=timezone.utc)), reverse=reverse)
    return sorted(posts, key=lambda post: _post_datetime(post) or datetime.min.replace(tzinfo=timezone.utc), reverse=reverse)


def channel_posts(
    channel_id: int,
    *,
    page: int = 1,
    limit: int = 10,
    sort: str = "latest",
    direction: str = "desc",
    window: str = "all",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = _latest_channel_row(channel_id, staff=staff)
    raw = _parse_json(row.get("metric_raw_payload_json"))
    package_dir = _text(raw.get("package_dir"))
    posts = _posts_from_package(package_dir)
    source = "package_csv" if posts else "snapshot_sample"
    if not posts:
        posts = _extract_posts(row, per_account_limit=50)
    posts = [post for post in posts if _text(post.get("id"), post.get("url"))]
    posts = _filter_posts_by_window(posts, window)
    posts = _sort_posts(posts, sort, direction)
    page_i = max(1, int(page or 1))
    limit_i = max(1, min(50, int(limit or 10)))
    total = len(posts)
    offset = (page_i - 1) * limit_i
    items = posts[offset : offset + limit_i]
    return {
        "channel_id": int(channel_id),
        "account": {
            "id": int(row.get("id") or 0),
            "platform": str(row.get("platform") or ""),
            "handle": str(row.get("account_handle") or ""),
            "display_name": _account_name(row),
            "account_url": _account_url(row),
            "followers": _int(row.get("metric_followers")),
            "posts_count": _int(row.get("metric_posts")),
            "total_views": _int(row.get("metric_views")),
            "total_likes": _int(row.get("metric_likes")),
            "total_comments": _int(row.get("metric_comments")),
            "total_shares": _int(row.get("metric_shares")),
            "captured_at": _text(row.get("metric_captured_at")),
        },
        "posts": items,
        "pagination": {
            "page": page_i,
            "limit": limit_i,
            "total": total,
            "pages": (total + limit_i - 1) // limit_i if total else 0,
            "has_next": offset + limit_i < total,
            "has_prev": page_i > 1,
        },
        "sort": sort if sort in {"latest", "views", "likes", "comments", "shares"} else "latest",
        "direction": direction if direction in {"asc", "desc"} else "desc",
        "window": window if window in {"all", "7d", "30d", "90d", "180d", "365d", "year"} else "all",
        "source": source,
        "package_dir": package_dir,
    }


def _all_posts_for_channel(row: dict[str, Any]) -> tuple[list[dict[str, Any]], str, str]:
    raw = _parse_json(row.get("metric_raw_payload_json"))
    package_dir = _text(raw.get("package_dir"))
    posts = _posts_from_package(package_dir)
    source = "package_csv" if posts else "snapshot_sample"
    if not posts:
        posts = _extract_posts(row, per_account_limit=50)
    posts = [post for post in posts if _text(post.get("id"), post.get("url"))]
    return posts, source, package_dir


def _reddit_external_id(value: str) -> str:
    text = _text(value)
    if "/comments/" in text:
        return text.split("/comments/", 1)[1].split("/", 1)[0]
    return text.replace("t3_", "")


def _match_post(row: dict[str, Any], post_id: str, url: str = "") -> dict[str, Any] | None:
    posts, _, _ = _all_posts_for_channel(row)
    candidates = {_text(post_id), _text(url), _reddit_external_id(post_id), _reddit_external_id(url)}
    candidates = {candidate for candidate in candidates if candidate}
    for post in posts:
        keys = {
            _text(post.get("id")),
            _text(post.get("source_id")),
            _text(post.get("short_code")),
            _text(post.get("url")),
            _reddit_external_id(_text(post.get("id"))),
            _reddit_external_id(_text(post.get("url"))),
        }
        if candidates & {key for key in keys if key}:
            return post
    return None


def official_views_evidence(*, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None, limit: int = 120) -> dict[str, Any]:
    """Map official-account channel data into metric evidence rows for views."""
    safe_limit = max(1, min(300, int(limit or 120)))
    cache_key = _channel_cache_key("official_views_evidence", staff=staff, view_as_staff_id=view_as_staff_id, limit=safe_limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return _channel_cache_hit(cached)
    matrix = official_account_matrix(staff=staff, view_as_staff_id=view_as_staff_id, limit=safe_limit)
    rows: list[dict[str, Any]] = []
    max_rows = safe_limit
    for platform in matrix["platforms"]:
        platform_label = platform.get("label") or _platform_label(platform.get("platform"))
        for account in platform.get("accounts", []):
            account_name = account.get("display_name") or account.get("handle") or "官方账号"
            posts = account.get("posts") or []
            if not posts and _int(account.get("total_views")):
                posts = [
                    {
                        "id": f"account-{account.get('id')}",
                        "title": f"{account_name} 账号播放量快照",
                        "url": account.get("account_url"),
                        "posted_at": account.get("last_sync_at"),
                        "views": account.get("total_views"),
                    }
                ]
            for post in posts:
                amount = _int(post.get("views"))
                if amount <= 0:
                    continue
                rows.append(
                    {
                        "id": f"official-{account.get('id')}-{post.get('id') or len(rows)}",
                        "metric": "views",
                        "label": _text(post.get("title"), f"{account_name} 内容"),
                        "source": f"Viltrox 自营账号 · {platform_label}",
                        "amount": amount,
                        "amountUnit": "number",
                        "ownerName": account_name,
                        "kolName": "",
                        "confidence": _text(account.get("sync_status"), "synced"),
                        "occurredAt": _text(post.get("posted_at"), account.get("last_sync_at")),
                        "rawRef": _text(post.get("url"), account.get("account_url")),
                        "platform": account.get("platform"),
                        "platformLabel": platform_label,
                        "attributionType": "owned_official",
                        "accountId": account.get("id"),
                        "accountName": account_name,
                        "accountHandle": account.get("handle"),
                        "accountUrl": account.get("account_url"),
                        "staffId": account.get("staff_id"),
                        "staffName": account.get("staff_name"),
                        "staffEmail": account.get("staff_email"),
                        "staffRole": account.get("staff_role"),
                        "postId": post.get("id"),
                        "mediaUrl": post.get("media_url"),
                    }
                )
    rows.sort(key=lambda item: (item.get("occurredAt") or ""), reverse=True)
    return _channel_cache_store(cache_key, {
        "rows": rows[:max_rows],
        "account_count": matrix["account_count"],
        "post_count": matrix["post_count"],
        "total_views": matrix["total_views"],
        "evidence_views": sum(_int(row.get("amount")) for row in rows),
        "returned_rows": min(len(rows), max_rows),
        "platforms": matrix["platforms"],
    })
