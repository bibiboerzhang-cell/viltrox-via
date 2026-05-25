"""Industry account matrix and cross-platform snapshot helpers."""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.utcnow().date().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _bool(value: Any) -> Any:
    postgres_selected = os.environ.get("DB_RUNTIME_BACKEND") == "postgres" or is_postgres_runtime()
    return bool(value) if postgres_selected else (1 if value else 0)


def _active_sql(alias: str | None = None) -> str:
    column = f"{alias}.is_active" if alias else "is_active"
    postgres_selected = os.environ.get("DB_RUNTIME_BACKEND") == "postgres" or is_postgres_runtime()
    return f"{column} IS TRUE" if postgres_selected else f"{column}=1"


def _platform(value: Any) -> str:
    return str(value or "other").strip().lower() or "other"


def create_project(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("industry project name required")
    uid = f"industry-{secrets.token_hex(8)}"
    now = _utcnow()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_industry_projects
            (project_uid, name, description, project_type, linked_launch_id, linked_campaign_id,
             monitoring_frequency, auto_archive_days, report_subscriptions_json, owner_staff_id,
             is_active, metadata_json, created_at, updated_at, idempotency_key)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            name,
            str(payload.get("description") or ""),
            str(payload.get("project_type") or "brand_monitor"),
            payload.get("linked_launch_id") or None,
            payload.get("linked_campaign_id") or None,
            str(payload.get("monitoring_frequency") or "daily"),
            payload.get("auto_archive_days") or None,
            _json(payload.get("report_subscriptions") or {}),
            int(payload.get("owner_staff_id") or resolve_staff_id(staff) or 0) or None,
            _bool(True),
            _json(payload.get("metadata") or {}),
            now,
            now,
            payload.get("idempotency_key") or uid,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_industry_projects WHERE project_uid=?", (uid,)).fetchone()
    audit.log_business_event(staff_id=resolve_staff_id(staff), action_type="industry_project_create", target_type="industry_project", target_id=dict(row).get("id") if row else uid, detail=name)
    return {"project": dict(row) if row else {"project_uid": uid}}


def list_projects(limit: int = 100, active_only: bool = True) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    where = f"WHERE {_active_sql()}" if active_only else ""
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_industry_projects {where} ORDER BY updated_at DESC, id DESC LIMIT ?",
        (max(1, min(300, int(limit or 100))),),
    ).fetchall()
    return {"projects": [dict(row) for row in rows]}


def get_project(project_id: int) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    row = get_conn().execute("SELECT * FROM vkpi_industry_projects WHERE id=?", (int(project_id),)).fetchone()
    if not row:
        raise LookupError("industry project not found")
    return {"project": dict(row), "accounts": list_accounts(int(project_id)).get("accounts")}


def delete_project(project_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    get_conn().execute("UPDATE vkpi_industry_projects SET is_active=?, archived_at=?, updated_at=? WHERE id=?", (_bool(False), _utcnow(), _utcnow(), int(project_id)))
    get_conn().commit()
    audit.log_business_event(staff_id=resolve_staff_id(staff), action_type="industry_project_archive", target_type="industry_project", target_id=project_id)
    return {"archived": True, "project_id": int(project_id)}


def add_account(project_id: int, payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    raw_handle = str(payload.get("handle") or payload.get("username") or "").strip()
    raw_profile_url = str(payload.get("profile_url") or payload.get("url") or "").strip()
    if not raw_profile_url and raw_handle.lower().startswith(("http://", "https://")):
        raw_profile_url = raw_handle
    handle = _normalize_handle({**payload, "handle": raw_handle, "profile_url": raw_profile_url}).strip().lstrip("@")
    if not handle:
        raise ValueError("account handle required")
    uid = f"account-{secrets.token_hex(8)}"
    now = _utcnow()
    platform = _platform(payload.get("platform"))
    profile_url = raw_profile_url
    if not profile_url:
        profile_url = _default_profile_url(platform, handle)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_industry_accounts
            (account_uid, project_id, platform, platform_user_id, handle, display_name, avatar_url,
             profile_url, bio, is_verified, brand_group, account_role, region, category,
             linked_kol_pool_id, auto_kol_link, crawl_enabled, crawl_frequency, sync_status,
             notes, added_by_staff_id, is_active, raw_platform_data, discovered_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id, platform, handle) DO UPDATE SET
            display_name=excluded.display_name,
            avatar_url=excluded.avatar_url,
            profile_url=excluded.profile_url,
            bio=excluded.bio,
            is_verified=excluded.is_verified,
            brand_group=excluded.brand_group,
            account_role=excluded.account_role,
            region=excluded.region,
            category=excluded.category,
            raw_platform_data=excluded.raw_platform_data
        """,
        (
            uid,
            int(project_id),
            platform,
            str(payload.get("platform_user_id") or ""),
            handle,
            str(payload.get("display_name") or handle),
            str(payload.get("avatar_url") or ""),
            profile_url,
            str(payload.get("bio") or ""),
            _bool(payload.get("is_verified")),
            str(payload.get("brand_group") or ""),
            str(payload.get("account_role") or "reference"),
            str(payload.get("region") or ""),
            str(payload.get("category") or ""),
            payload.get("linked_kol_pool_id") or None,
            _bool(payload.get("auto_kol_link")),
            _bool(payload.get("crawl_enabled")),
            str(payload.get("crawl_frequency") or "daily"),
            str(payload.get("sync_status") or "not_configured"),
            str(payload.get("notes") or ""),
            resolve_staff_id(staff) or None,
            _bool(True),
            _json(payload.get("raw_platform_data") or payload),
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_industry_accounts WHERE project_id=? AND platform=? AND handle=?", (int(project_id), platform, handle)).fetchone()
    return {"account": dict(row) if row else {}}


def update_account(account_id: int, payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    row = get_conn().execute("SELECT * FROM vkpi_industry_accounts WHERE id=?", (int(account_id),)).fetchone()
    if not row:
        raise LookupError("industry account not found")
    updates: list[str] = []
    params: list[Any] = []
    if "crawl_enabled" in payload:
        updates.append("crawl_enabled=?")
        params.append(_bool(payload.get("crawl_enabled")))
        updates.append("sync_status=?")
        params.append("not_configured" if not payload.get("crawl_enabled") else "queued")
    if "crawl_frequency" in payload:
        updates.append("crawl_frequency=?")
        params.append(str(payload.get("crawl_frequency") or "daily"))
    if "notes" in payload:
        updates.append("notes=?")
        params.append(str(payload.get("notes") or ""))
    if not updates:
        return get_account(account_id)
    params.append(int(account_id))
    get_conn().execute(f"UPDATE vkpi_industry_accounts SET {', '.join(updates)} WHERE id=?", tuple(params))
    get_conn().commit()
    audit.log_business_event(
        staff_id=resolve_staff_id(staff),
        action_type="industry_account_update",
        target_type="industry_account",
        target_id=account_id,
        metadata={"fields": sorted([str(key) for key in payload.keys()])},
    )
    return get_account(account_id)


def import_accounts(project_id: int, items: list[dict[str, Any]], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    imported = []
    skipped = 0
    for item in items:
        try:
            imported.append(add_account(project_id, item, staff=staff).get("account") or {})
        except ValueError:
            skipped += 1
    return {"imported": len(imported), "skipped": skipped, "accounts": imported}


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return None


def _nested_first(data: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value: Any = data
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value.get(part)
        if value not in (None, ""):
            return value
    return None


def _list_value(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        nested = value.get("items") or value.get("data") or value.get("edges") or []
        if isinstance(nested, list):
            return [item.get("node") if isinstance(item, dict) and isinstance(item.get("node"), dict) else item for item in nested if isinstance(item, dict)]
    return []


def _infer_platform(item: dict[str, Any]) -> str:
    raw = str(_first(item, "platform", "sourcePlatform", "socialNetwork", "type") or "").strip().lower()
    aliases = {
        "ig": "instagram",
        "instagram": "instagram",
        "yt": "youtube",
        "youtube": "youtube",
        "tiktok": "tiktok",
        "douyin": "tiktok",
        "xiaohongshu": "xiaohongshu",
        "xhs": "xiaohongshu",
        "bilibili": "bilibili",
        "facebook": "facebook",
        "reddit": "reddit",
        "twitter": "x",
        "x": "x",
    }
    if raw in aliases:
        return aliases[raw]
    url = str(_first(item, "url", "profileUrl", "profile_url", "channelUrl", "channel_url", "inputUrl") or "")
    host = urlparse(url).netloc.lower()
    if "instagram" in host:
        return "instagram"
    if "youtube" in host or "youtu.be" in host:
        return "youtube"
    if "tiktok" in host:
        return "tiktok"
    if "xiaohongshu" in host or "xhslink" in host:
        return "xiaohongshu"
    if "bilibili" in host:
        return "bilibili"
    if "facebook" in host:
        return "facebook"
    if "reddit" in host:
        return "reddit"
    if "twitter" in host or host == "x.com":
        return "x"
    return "other"


def _handle_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    if parts[0] in {"channel", "c", "user"} and len(parts) > 1:
        return parts[1].lstrip("@")
    return parts[0].lstrip("@")


def _default_profile_url(platform: str, handle: str) -> str:
    normalized = str(handle or "").strip().lstrip("@")
    if not normalized:
        return ""
    platform_key = _platform(platform)
    if platform_key == "instagram":
        return f"https://www.instagram.com/{normalized}"
    if platform_key == "tiktok":
        return f"https://www.tiktok.com/@{normalized}"
    if platform_key == "youtube":
        return f"https://www.youtube.com/@{normalized}"
    if platform_key == "facebook":
        return f"https://www.facebook.com/{normalized}"
    if platform_key == "reddit":
        return f"https://www.reddit.com/user/{normalized}"
    if platform_key == "x":
        return f"https://x.com/{normalized}"
    if platform_key == "bilibili":
        return f"https://space.bilibili.com/{normalized}"
    return ""


def _normalize_handle(item: dict[str, Any]) -> str:
    handle = str(_first(item, "handle", "username", "userName", "screenName", "channelName", "author", "ownerUsername") or "").strip()
    if handle.lower().startswith(("http://", "https://")):
        handle = _handle_from_url(handle)
    if not handle:
        handle = str(_nested_first(item, "authorMeta.name", "authorMeta.username", "owner.username", "user.username") or "").strip()
    if not handle:
        handle = _handle_from_url(str(_first(item, "url", "profileUrl", "profile_url", "channelUrl", "channel_url", "inputUrl") or ""))
    return handle.lstrip("@").strip()


def _normalize_apify_post(post: dict[str, Any]) -> dict[str, Any]:
    stats = post.get("statistics") if isinstance(post.get("statistics"), dict) else {}
    snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
    post_id = _first(post, "id", "videoId", "postId", "shortCode", "code") or _nested_first(post, "id.videoId")
    title = _first(post, "title", "text", "caption", "description") or _first(snippet, "title", "description") or ""
    caption = _first(post, "caption", "description", "text") or _first(snippet, "description") or ""
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    thumbnail_url = (
        _first(post, "thumbnailUrl", "thumbnail_url", "displayUrl", "imageUrl")
        or (((thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}) or {}).get("url") if thumbnails else "")
    )
    video_url = (
        _first(
            post,
            "video_url",
            "videoUrl",
            "videoDownloadUrl",
            "downloadUrl",
            "downloadAddr",
            "playUrl",
            "play_url",
            "mediaUrl",
            "media_url",
            "url_to_video",
            "video_url_no_watermark",
        )
        or _nested_first(post, "video.url", "video.playAddr", "video.downloadAddr", "media.videoUrl")
        or ""
    )
    duration_seconds = (
        _first(post, "duration_seconds", "durationSeconds", "duration", "videoDuration")
        or _nested_first(post, "videoMeta.duration", "video.duration", "contentDetails.duration")
    )
    media_type = str(_first(post, "media_type", "mediaType", "type") or "").strip().lower()
    if media_type not in {"video", "image", "carousel", "reel", "short", "photo"}:
        media_type = "video" if video_url else ("image" if thumbnail_url else "")
    return {
        "id": str(post_id or secrets.token_hex(8)),
        "post_url": str(_first(post, "url", "postUrl", "webVideoUrl", "permalink", "permalinkUrl") or ""),
        "title": str(title or ""),
        "caption": str(caption or ""),
        "publishedAt": str(_first(post, "publishedAt", "published_at", "timestamp", "takenAt", "createdAt") or ""),
        "thumbnail_url": str(thumbnail_url or ""),
        "video_url": str(video_url or ""),
        "media_type": media_type,
        "duration_seconds": duration_seconds,
        "video_source": "apify_cdn" if video_url else "",
        "views": _first(post, "views", "viewCount", "videoViewCount", "playCount") or _first(stats, "views", "viewCount"),
        "likes": _first(post, "likes", "likeCount", "likesCount") or _first(stats, "likes", "likeCount"),
        "comments": _first(post, "comments", "commentCount", "commentsCount") or _first(stats, "comments", "commentCount"),
        "shares": _first(post, "shares", "shareCount") or _first(stats, "shares", "shareCount"),
        "saves": _first(post, "saves", "saveCount"),
        "raw": post,
    }


def _extract_posts(item: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("videos", "posts", "latestPosts", "latest_posts", "latestVideos", "items", "data"):
        posts = _list_value(item.get(key))
        if posts:
            return [_normalize_apify_post(post) for post in posts]
    if _first(item, "postUrl", "videoUrl", "webVideoUrl") or (_first(item, "title", "caption") and _first(item, "views", "viewCount", "playCount")):
        return [_normalize_apify_post(item)]
    return []


def _apify_raw_to_collector_payload(item: dict[str, Any], *, source_type: str, source_ref: str) -> dict[str, Any]:
    handle = _normalize_handle(item)
    followers = _first(item, "followers", "followersCount", "followerCount", "subscribers", "subscriberCount") or _nested_first(item, "authorMeta.fans")
    posts_count = _first(item, "posts", "postsCount", "mediaCount", "videoCount", "videosCount")
    views = _first(item, "views", "totalViews", "viewCount")
    posts = _extract_posts(item)
    return {
        "source": source_type or "apify_import",
        "source_ref": source_ref,
        "snapshot_date": str(_first(item, "snapshot_date", "snapshotDate", "date") or _today()),
        "youtube_kpi_status": "historical_import",
        "youtube_kpi_source_ref": source_ref,
        "profile": {
            "items": [
                {
                    "id": str(_first(item, "platformUserId", "platform_user_id", "channelId", "id") or ""),
                    "snippet": {
                        "title": str(_first(item, "displayName", "display_name", "fullName", "name", "title") or handle),
                        "description": str(_first(item, "bio", "biography", "description") or ""),
                    },
                    "statistics": {
                        "subscriberCount": followers,
                        "followers": followers,
                        "videoCount": posts_count,
                        "posts": posts_count,
                        "viewCount": views,
                    },
                }
            ]
        },
        "videos": posts,
        "raw_import_item": item,
    }


def _has_real_metrics(raw_data: dict[str, Any]) -> bool:
    profile = ((raw_data.get("profile") or {}).get("items") or [{}])[0]
    stats = profile.get("statistics") if isinstance(profile, dict) else {}
    return any(stats.get(key) not in (None, "") for key in ("subscriberCount", "followers", "videoCount", "posts", "viewCount")) or bool(raw_data.get("videos"))


def import_historical_dataset(
    project_id: int,
    items: list[dict[str, Any]],
    *,
    source_type: str = "apify_json",
    source_ref: str = "",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import exported Apify/dataset rows without making paid API calls.

    Rows with real metrics write a snapshot/posts through the collector. Rows
    containing only an account identity create the account with
    ``imported_no_metrics`` so the UI never shows fake zero followers.
    """

    ensure_vkpi_product_industry_schema()
    imported_accounts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    snapshots_written = 0
    posts_written = 0
    for index, item in enumerate(items or []):
        if not isinstance(item, dict):
            skipped.append({"index": index, "reason": "row_not_object"})
            continue
        platform = _infer_platform(item)
        handle = _normalize_handle(item)
        if not handle:
            skipped.append({"index": index, "reason": "missing_handle"})
            continue
        raw_data = _apify_raw_to_collector_payload(item, source_type=source_type, source_ref=source_ref)
        profile_url = str(_first(item, "profileUrl", "profile_url", "channelUrl", "channel_url", "url", "inputUrl") or "")
        try:
            account = add_account(
                project_id,
                {
                    "platform": platform,
                    "platform_user_id": _first(item, "platformUserId", "platform_user_id", "channelId", "id") or "",
                    "handle": handle,
                    "display_name": _first(item, "displayName", "display_name", "fullName", "name", "title") or handle,
                    "avatar_url": _first(item, "avatarUrl", "avatar_url", "profilePicUrl", "profile_pic_url", "profilePictureUrl") or "",
                    "profile_url": profile_url,
                    "bio": _first(item, "bio", "biography", "description") or "",
                    "is_verified": bool(_first(item, "isVerified", "verified", "is_verified")),
                    "brand_group": _first(item, "brand_group", "brandGroup") or "",
                    "account_role": _first(item, "account_role", "role") or "historical_import",
                    "region": _first(item, "country", "region", "location") or "",
                    "category": _first(item, "category", "niche") or "",
                    "crawl_enabled": False,
                    "sync_status": "historical_import" if _has_real_metrics(raw_data) else "imported_no_metrics",
                    "raw_platform_data": {"source_type": source_type, "source_ref": source_ref, "raw": item},
                },
                staff=staff,
            ).get("account") or {}
        except Exception as exc:
            skipped.append({"index": index, "reason": str(exc)[:200]})
            continue
        imported_accounts.append(account)
        if _has_real_metrics(raw_data):
            collected = importlib_collect_account_snapshot(int(account["id"]), raw_data=raw_data, staff=staff)
            if collected.get("snapshot"):
                snapshots_written += 1
            posts_written += int(collected.get("posts_written") or 0)
    audit.log_business_event(
        staff_id=resolve_staff_id(staff),
        action_type="industry_apify_import",
        target_type="industry_project",
        target_id=int(project_id),
        metadata={
            "source_type": source_type,
            "source_ref": source_ref,
            "input_count": len(items or []),
            "imported": len(imported_accounts),
            "skipped": skipped,
            "snapshots_written": snapshots_written,
            "posts_written": posts_written,
        },
    )
    return {
        "source_type": source_type,
        "source_ref": source_ref,
        "input_count": len(items or []),
        "imported": len(imported_accounts),
        "skipped_count": len(skipped),
        "skipped": skipped[:50],
        "snapshots_written": snapshots_written,
        "posts_written": posts_written,
        "accounts": imported_accounts,
        "provider_status": "historical_import_only",
    }


def importlib_collect_account_snapshot(account_id: int, *, raw_data: dict[str, Any], staff: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.services.vkpi import industry_snapshot_collector

    return industry_snapshot_collector.collect_account_snapshot(account_id, raw_data=raw_data, force_local=True, staff=staff)


def list_accounts(project_id: int | None = None, limit: int = 300) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    where = f"WHERE {_active_sql('a')}"
    params: list[Any] = []
    if project_id:
        where += " AND a.project_id=?"
        params.append(int(project_id))
    rows = get_conn().execute(
        f"""
        SELECT
            a.*,
            s.snapshot_date AS latest_snapshot_date,
            s.followers AS followers,
            s.followers_growth_24h AS followers_growth_24h,
            s.followers_growth_30d AS followers_growth_30d,
            s.followers_growth_pct_30d AS followers_growth_pct_30d,
            s.posts AS posts,
            s.posts_30d AS posts_30d,
            s.avg_posts_per_day AS avg_posts_per_day,
            s.views AS views,
            s.views_30d AS views_30d,
            s.likes AS likes,
            s.comments AS comments,
            s.shares AS shares,
            s.saves AS saves,
            s.engagement_total_30d AS engagement_total_30d,
            s.engagement_rate AS engagement_rate,
            s.avg_engagement_rate_by_followers AS avg_engagement_rate_by_followers,
            s.avg_engagement_per_day AS avg_engagement_per_day,
            s.avg_eng_rate_by_views AS avg_eng_rate_by_views,
            s.reach_total_30d AS reach_total_30d,
            s.impressions_total_30d AS impressions_total_30d,
            s.reels_views_30d AS reels_views_30d,
            s.estimated_organic_value_cents AS estimated_organic_value_cents
        FROM vkpi_industry_accounts a
        LEFT JOIN vkpi_industry_account_snapshots s
          ON s.id = (
            SELECT s2.id
            FROM vkpi_industry_account_snapshots s2
            WHERE s2.account_id = a.id
            ORDER BY s2.snapshot_date DESC, s2.id DESC
            LIMIT 1
          )
        {where}
        ORDER BY a.discovered_at DESC, a.id DESC
        LIMIT ?
        """,
        (*params, max(1, min(1000, int(limit or 300)))),
    ).fetchall()
    return {"accounts": [dict(row) for row in rows]}


def get_account(account_id: int, *, post_limit: int = 500) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    row = get_conn().execute("SELECT * FROM vkpi_industry_accounts WHERE id=?", (int(account_id),)).fetchone()
    if not row:
        raise LookupError("industry account not found")
    snapshots = get_conn().execute("SELECT * FROM vkpi_industry_account_snapshots WHERE account_id=? ORDER BY snapshot_date DESC LIMIT 30", (int(account_id),)).fetchall()
    safe_post_limit = max(1, min(500, int(post_limit or 500)))
    posts = get_conn().execute(
        "SELECT * FROM vkpi_industry_posts WHERE account_id=? ORDER BY published_at DESC, id DESC LIMIT ?",
        (int(account_id), safe_post_limit),
    ).fetchall()
    return {"account": dict(row), "snapshots": [dict(item) for item in snapshots], "posts": [dict(item) for item in posts]}


def refresh_account(account_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    # Imported lazily to keep industry_data.py as the public compatibility module
    # and avoid a package/module name conflict with future crawler adapters.
    from app.services.vkpi import industry_snapshot_collector

    return industry_snapshot_collector.collect_account_snapshot(account_id, staff=staff)


def add_snapshot(account_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    date = str(payload.get("snapshot_date") or _today())
    conn = get_conn()
    fields = [
        "followers", "followers_growth_24h", "followers_growth_30d", "followers_growth_pct_30d", "posts", "posts_30d",
        "avg_posts_per_day", "views", "views_30d", "likes", "comments", "shares", "saves", "engagement_total_30d",
        "engagement_rate", "avg_engagement_rate_by_followers", "avg_engagement_per_day", "avg_eng_rate_by_views",
        "avg_eng_rate_by_impressions", "avg_eng_rate_by_reach", "avg_views", "reach_total_30d", "impressions_total_30d",
        "reels_views_30d", "top_post_views", "day_with_most_posts", "hour_with_most_posts", "day_with_highest_engagement",
        "hour_with_highest_engagement", "avg_hashtags_per_post", "avg_video_duration_seconds", "estimated_organic_value_cents",
        "vkpi_attributed_gmv_cents", "vkpi_attributed_orders", "vkpi_linked_kol_count", "vkpi_project_count",
        "youtube_kpi_status", "youtube_kpi_source_ref", "youtube_kpi_updated_at", "youtube_kpi_json", "raw_platform_data",
    ]
    values = []
    for field in fields:
        value = payload.get(field)
        if field.endswith("_json") or field == "raw_platform_data":
            value = _json(value)
        values.append(value)
    placeholders = ",".join("?" for _ in fields)
    updates = ",".join(f"{field}=excluded.{field}" for field in fields)
    conn.execute(
        f"""
        INSERT INTO vkpi_industry_account_snapshots
            (account_id, snapshot_date, {', '.join(fields)}, created_at)
        VALUES (?, ?, {placeholders}, ?)
        ON CONFLICT(account_id, snapshot_date) DO UPDATE SET {updates}
        """,
        (int(account_id), date, *values, _utcnow()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_industry_account_snapshots WHERE account_id=? AND snapshot_date=?", (int(account_id), date)).fetchone()
    return {"snapshot": dict(row) if row else {}}


def cross_platform(project_id: int) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    rows = get_conn().execute(
        f"""
        SELECT a.platform, COUNT(*) AS account_count,
               COALESCE(SUM(s.followers),0) AS followers,
               COALESCE(SUM(s.views_30d),0) AS views_30d,
               COALESCE(SUM(s.engagement_total_30d),0) AS engagement_total_30d,
               COALESCE(SUM(s.vkpi_attributed_gmv_cents),0) AS vkpi_attributed_gmv_cents
        FROM vkpi_industry_accounts a
        LEFT JOIN vkpi_industry_account_snapshots s ON s.account_id = a.id
        WHERE a.project_id=? AND {_active_sql("a")}
        GROUP BY a.platform
        ORDER BY views_30d DESC
        """,
        (int(project_id),),
    ).fetchall()
    return {"platforms": [dict(row) for row in rows], "provider_status": "local_snapshots_only"}


def posts(project_id: int, limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    rows = get_conn().execute(
        """
        SELECT p.* FROM vkpi_industry_posts p
        JOIN vkpi_industry_accounts a ON a.id=p.account_id
        WHERE a.project_id=?
        ORDER BY COALESCE(p.published_at, p.created_at) DESC, p.id DESC
        LIMIT ?
        """,
        (int(project_id), max(1, min(500, int(limit or 100)))),
    ).fetchall()
    return {"posts": [dict(row) for row in rows]}
