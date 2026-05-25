"""Industry account snapshot collector and KPI calculator.

This module is the Phase 1 collection seam. It can be called by cron, API
refresh, or future Apify import jobs. It never fabricates account metrics:
disabled providers return not_configured/disabled and real snapshots are only
written from supplied raw data or an enabled live adapter.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
import importlib

platform_crawl_settings = importlib.import_module("app.domains.settings.platform_crawl")
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id


from app.services.vkpi.industry_snapshot_kpis import SNAPSHOT_FIELDS, _int, _json, _parse_duration_seconds, _snippet, _stats, _today, _utcnow, _video_items, calculate_kpis

_POST_MEDIA_COLUMNS_READY = False


def _platform_config(platform: str) -> dict[str, Any]:
    settings = platform_crawl_settings.platform_settings().get("platforms") or []
    for item in settings:
        if str(item.get("platform") or "").lower() == str(platform or "").lower():
            return dict(item)
    return {}


def _record_platform_test_status(platform: str, status: str, metadata: dict[str, Any] | None = None) -> None:
    """Persist the provider test status used by Settings and account diagnostics."""
    clean_platform = str(platform or "").strip().lower()
    clean_status = str(status or "").strip().lower()
    if not clean_platform or not clean_status:
        return
    now = _utcnow()
    conn = get_conn()
    row = conn.execute(
        "SELECT metadata_json FROM vkpi_platform_crawl_settings WHERE platform=?",
        (clean_platform,),
    ).fetchone()
    current_metadata: dict[str, Any] = {}
    if row:
        try:
            current_metadata = json.loads(str(dict(row).get("metadata_json") or "{}"))
        except Exception:
            current_metadata = {}
    if metadata:
        current_metadata.update(metadata)
    current_metadata["last_live_status_update"] = now
    conn.execute(
        """
        UPDATE vkpi_platform_crawl_settings
        SET last_test_status=?, last_test_at=?, metadata_json=?
        WHERE platform=?
        """,
        (clean_status, now, _json(current_metadata), clean_platform),
    )
    conn.commit()


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _ensure_post_media_columns(conn: Any) -> None:
    """Keep older local databases compatible with the new media columns."""
    global _POST_MEDIA_COLUMNS_READY
    if _POST_MEDIA_COLUMNS_READY:
        return
    columns = {
        "video_url": "TEXT DEFAULT ''",
        "media_type": "TEXT DEFAULT ''",
        "duration_seconds": "INTEGER",
        "video_source": "TEXT DEFAULT ''",
    }
    if is_postgres_runtime():
        for column, definition in columns.items():
            conn.execute(f"ALTER TABLE vkpi_industry_posts ADD COLUMN IF NOT EXISTS {column} {definition}")
    else:
        existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(vkpi_industry_posts)").fetchall()}
        for column, definition in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE vkpi_industry_posts ADD COLUMN {column} {definition}")
    conn.commit()
    _POST_MEDIA_COLUMNS_READY = True


def _first_media_url(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    for path in ("video.url", "video.playAddr", "video.downloadAddr", "media.videoUrl", "media.url"):
        value: Any = row
        for part in path.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value is not None and str(value).strip():
            return str(value).strip()
    for list_key in ("medias", "media", "attachments"):
        value = row.get(list_key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            media_type = str(item.get("type") or item.get("media_type") or "").lower()
            url = item.get("videoUrl") or item.get("video_url") or item.get("url") or item.get("downloadUrl")
            if url and (media_type in {"video", "reel", "short"} or ".mp4" in str(url).lower()):
                return str(url).strip()
    return ""


def _duration_seconds(value: Any) -> int | None:
    parsed_int = _int(value)
    if parsed_int is not None:
        return parsed_int
    parsed_iso = _parse_duration_seconds(value)
    return parsed_iso


def _post_duration_value(video: dict[str, Any]) -> Any:
    for key in ("duration_seconds", "durationSeconds", "duration", "videoDuration"):
        value = video.get(key)
        if value not in (None, ""):
            return value
    for path in ("videoMeta.duration", "video.duration", "contentDetails.duration"):
        value: Any = video
        for part in path.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value not in (None, ""):
            return value
    return None


def _media_type(video: dict[str, Any], *, video_url: str, thumbnail_url: str) -> str:
    raw = str(video.get("media_type") or video.get("mediaType") or video.get("type") or "").strip().lower()
    if raw in {"video", "image", "carousel", "reel", "short", "photo"}:
        return "video" if raw in {"reel", "short"} else ("image" if raw == "photo" else raw)
    if video_url:
        return "video"
    if thumbnail_url:
        return "image"
    return ""


def _first_profile(raw_data: dict[str, Any]) -> dict[str, Any]:
    profile = raw_data.get("profile") if isinstance(raw_data, dict) else {}
    items = profile.get("items") if isinstance(profile, dict) else []
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return dict(items[0])
    if isinstance(profile, dict) and any(key in profile for key in ("username", "profilePicUrl", "profilePicUrlHD")):
        return profile
    return {}


def _sync_account_profile_fields(account: dict[str, Any], raw_data: dict[str, Any]) -> dict[str, Any]:
    profile = _first_profile(raw_data)
    if not profile:
        return account
    platform = str(account.get("platform") or "").lower()
    username = _first_text(profile, "username", "handle")
    avatar_url = _first_text(profile, "profilePicUrlHD", "profilePicUrl", "profilePictureUrl", "profile_pic_url", "avatar_url")
    profile_url = _first_text(profile, "url", "inputUrl", "profile_url")
    if not profile_url and platform == "instagram" and username:
        profile_url = f"https://www.instagram.com/{username}/"
    display_name = _first_text(profile, "fullName", "displayName", "name", "username")
    updates: list[str] = []
    params: list[Any] = []
    if avatar_url:
        updates.append("avatar_url=?")
        params.append(avatar_url)
    if profile_url:
        updates.append("profile_url=?")
        params.append(profile_url)
    if display_name:
        updates.append("display_name=?")
        params.append(display_name)
    if not updates:
        return account
    params.append(int(account.get("id") or 0))
    conn = get_conn()
    conn.execute(f"UPDATE vkpi_industry_accounts SET {', '.join(updates)} WHERE id=?", tuple(params))
    conn.commit()
    updated = dict(account)
    if avatar_url:
        updated["avatar_url"] = avatar_url
    if profile_url:
        updated["profile_url"] = profile_url
    if display_name:
        updated["display_name"] = display_name
    return updated


def provider_gate(account: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    platform = str(account.get("platform") or "other").strip().lower()
    config = _platform_config(platform)
    if not force and not int(account.get("crawl_enabled") or 0):
        return {"allowed": False, "provider_status": "not_configured", "sync_status": "not_configured", "message": "该行业账号未开启抓取。"}
    if not force and not int(config.get("crawl_enabled") or 0):
        return {"allowed": False, "provider_status": "not_configured", "sync_status": "not_configured", "message": "平台抓取开关未开启。"}
    if not force and float(config.get("monthly_budget_usd") or 0) <= 0:
        return {"allowed": False, "provider_status": "budget_disabled", "sync_status": "not_configured", "message": "平台抓取预算为 0，未执行外部抓取。"}
    if not force:
        budget_gate = platform_crawl_settings.crawl_budget_gate(platform)
        if not budget_gate.get("allowed"):
            return {
                "allowed": False,
                "provider_status": "budget_disabled",
                "sync_status": "not_configured",
                "reason": budget_gate.get("reason") or "budget_disabled",
                "message": budget_gate.get("message") or "预算闸门未通过。",
                "budget_key": budget_gate.get("budget_key"),
            }
    # R-Phase2-A: 多平台 crawler 注册检查
    from app.services.vkpi.industry_crawlers import get_crawler, is_supported
    if not is_supported(platform):
        return {"allowed": False, "provider_status": "not_configured", "sync_status": "not_configured", "message": f"{platform} 抓取适配器尚未注册。"}
    crawler = get_crawler(platform)
    if crawler is None or not crawler.configured:
        provider_msg = {
            "youtube": "YouTube API key 未配置。",
            "instagram": "APIFY_TOKEN 未配置 (Instagram)。",
            "tiktok": "APIFY_TOKEN 未配置 (TikTok)。",
        }.get(platform, f"{platform} crawler 未配置。")
        return {"allowed": False, "provider_status": "not_configured", "sync_status": "not_configured", "message": provider_msg}
    return {"allowed": True, "provider_status": "configured", "sync_status": "queued", "message": ""}


def _insert_snapshot(account_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    date = str(payload.get("snapshot_date") or _today())
    values = []
    for field in SNAPSHOT_FIELDS:
        value = payload.get(field)
        if field.endswith("_json") or field == "raw_platform_data":
            value = _json(value)
        values.append(value)
    placeholders = ",".join("?" for _ in SNAPSHOT_FIELDS)
    updates = ",".join(f"{field}=excluded.{field}" for field in SNAPSHOT_FIELDS)
    conn = get_conn()
    conn.execute(
        f"""
        INSERT INTO vkpi_industry_account_snapshots
            (account_id, snapshot_date, {', '.join(SNAPSHOT_FIELDS)}, created_at)
        VALUES (?, ?, {placeholders}, ?)
        ON CONFLICT(account_id, snapshot_date) DO UPDATE SET {updates}
        """,
        (int(account_id), date, *values, _utcnow()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_industry_account_snapshots WHERE account_id=? AND snapshot_date=?", (int(account_id), date)).fetchone()
    return dict(row) if row else {}


def _insert_posts(account: dict[str, Any], raw_data: dict[str, Any], *, limit: int = 100) -> int:
    videos = _video_items(raw_data)[: max(0, min(200, int(limit or 100)))]
    if not videos:
        return 0
    platform = str(account.get("platform") or "youtube")
    account_id = int(account.get("id") or 0)
    conn = get_conn()
    _ensure_post_media_columns(conn)
    count = 0
    for video in videos:
        stats = _stats(video)
        snippet = _snippet(video)
        platform_post_id = str(((video.get("id") if not isinstance(video.get("id"), dict) else video.get("id", {}).get("videoId")) or video.get("videoId") or "")).strip()
        if not platform_post_id:
            platform_post_id = secrets.token_hex(8)
        post_url = str(video.get("post_url") or video.get("url") or (f"https://www.youtube.com/watch?v={platform_post_id}" if platform == "youtube" else ""))
        title = str(snippet.get("title") or video.get("title") or "")
        caption = str(snippet.get("description") or video.get("caption") or "")
        thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        thumbnail_url = str(video.get("thumbnail_url") or video.get("displayUrl") or (((thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}) or {}).get("url") if thumbnails else ""))
        video_url = _first_media_url(
            video,
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
        media_type = _media_type(video, video_url=video_url, thumbnail_url=thumbnail_url)
        duration_seconds = _duration_seconds(_post_duration_value(video))
        video_source = str(video.get("video_source") or ("apify_cdn" if video_url else "")).strip()
        uid = f"post-{platform}-{account_id}-{platform_post_id}"
        conn.execute(
            """
            INSERT INTO vkpi_industry_posts
                (post_uid, account_id, platform, platform_post_id, post_url, thumbnail_url,
                 video_url, media_type, duration_seconds, video_source,
                 title, caption, published_at, views, likes, comments, shares, saves,
                 hashtags_json, mentions_json, detected_products_json, content_pillar,
                 sentiment, raw_platform_data, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(post_uid) DO UPDATE SET
                post_url=excluded.post_url,
                thumbnail_url=excluded.thumbnail_url,
                video_url=excluded.video_url,
                media_type=excluded.media_type,
                duration_seconds=excluded.duration_seconds,
                video_source=excluded.video_source,
                title=excluded.title,
                caption=excluded.caption,
                published_at=excluded.published_at,
                views=excluded.views,
                likes=excluded.likes,
                comments=excluded.comments,
                shares=excluded.shares,
                saves=excluded.saves,
                raw_platform_data=excluded.raw_platform_data
            """,
            (
                uid,
                account_id,
                platform,
                platform_post_id,
                post_url,
                thumbnail_url,
                video_url,
                media_type,
                duration_seconds,
                video_source,
                title,
                caption,
                snippet.get("publishedAt") or video.get("published_at") or video.get("timestamp") or "",
                _int(stats.get("viewCount") or stats.get("views") or stats.get("view") or stats.get("view_count") or stats.get("play") or stats.get("playCount") or stats.get("videoViewCount") or stats.get("videoPlayCount") or stats.get("impression_count")),
                _int(stats.get("likeCount") or stats.get("likes") or stats.get("like_count") or stats.get("likesCount") or stats.get("likedCount") or stats.get("diggCount")),
                _int(stats.get("commentCount") or stats.get("comments") or stats.get("commentsCount") or stats.get("reply_count") or stats.get("comment_count")),
                _int(stats.get("shareCount") or stats.get("shares") or stats.get("sharesCount") or stats.get("share_count") or stats.get("retweet_count") or stats.get("repostCount")),
                _int(stats.get("saveCount") or stats.get("saves") or stats.get("savedCount") or stats.get("collectCount") or stats.get("bookmark_count")),
                _json(re.findall(r"#[\\w\\-\\u4e00-\\u9fff]+", f"{title} {caption}")),
                _json([]),
                _json([]),
                "",
                "",
                _json(video),
                _utcnow(),
            ),
        )
        count += 1
    conn.commit()
    return count


def collect_account_snapshot(
    account_id: int,
    *,
    raw_data: dict[str, Any] | None = None,
    force_local: bool = False,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_industry_accounts WHERE id=?", (int(account_id),)).fetchone()
    if not row:
        raise LookupError("industry account not found")
    account = dict(row)
    platform = str(account.get("platform") or "other").strip().lower()

    if raw_data is None:
        gate = provider_gate(account, force=force_local)
        if not gate.get("allowed"):
            provider_status = str(gate.get("provider_status") or "").strip().lower()
            message = str(gate.get("message") or "")
            if provider_status == "not_configured" and any(
                token in message
                for token in ("API", "TOKEN", "crawler", "适配器", "未配置")
            ):
                _record_platform_test_status(
                    platform,
                    "not_configured",
                    {"last_gate_message": message, "account_id": int(account_id)},
                )
            conn.execute(
                "UPDATE vkpi_industry_accounts SET sync_status=?, last_crawled_at=?, crawl_error_count=crawl_error_count+1 WHERE id=?",
                (gate.get("sync_status") or "not_configured", _utcnow(), int(account_id)),
            )
            conn.commit()
            return {"account": account, **gate}
        # R-Phase2-A: 多平台 dispatch
        from app.services.vkpi.industry_crawlers import get_crawler
        crawler = get_crawler(platform)
        if crawler is None:
            raw_data = {"source": "unsupported", "platform": platform, "message": f"{platform} 适配器未注册"}
        else:
            handle_or_url = str(account.get("profile_url") or account.get("handle") or "")
            platform_user_id = str(account.get("platform_user_id") or "")
            max_posts = max(1, int(_platform_config(platform).get("posts_per_account") or 25))
            if platform == "youtube":
                profile_payload = crawler.crawl_channel_profile(handle_or_url, channel_id=platform_user_id) if hasattr(crawler, "crawl_channel_profile") else {}
            else:
                profile_payload = crawler.crawl_channel_profile(handle_or_url, channel_id=platform_user_id, max_posts=max_posts) if hasattr(crawler, "crawl_channel_profile") else {}
            profile_items = profile_payload.get("items") or []
            channel_id = ""
            if platform == "youtube":
                channel_id = str((profile_items[0] or {}).get("id") or "") if profile_items else ""
            else:
                channel_id = platform_user_id or (str(profile_items[0].get("username", "")) if profile_items else "")
            videos_items = []
            if platform == "youtube" and channel_id:
                videos_payload = crawler.crawl_channel_videos(channel_id, max_results=max_posts)
                videos_items = videos_payload.get("items") or []
                if not videos_items and isinstance(profile_payload.get("videos"), list):
                    videos_items = profile_payload.get("videos") or []
            else:
                if profile_items:
                    first_profile = profile_items[0] or {}
                    videos_items = first_profile.get("latestPosts") or first_profile.get("posts") or first_profile.get("videos") or []
                if not videos_items and isinstance(profile_payload.get("videos"), list):
                    videos_items = profile_payload.get("videos") or []
            raw_data = {
                "source": f"{platform}_crawler",
                "profile": profile_payload,
                "videos": videos_items,
                "kpi_status": profile_payload.get("sync_status") or profile_payload.get("provider_status"),
            }
            if platform == "youtube":
                raw_data["youtube_kpi_status"] = raw_data["kpi_status"]
                youtube_source = str(profile_payload.get("provider_source") or (videos_payload.get("provider_source") if "videos_payload" in locals() and isinstance(videos_payload, dict) else "") or "").strip()
                raw_data["source"] = "youtube_apify" if youtube_source == "apify" else "youtube_api"
                raw_data["youtube_provider_source"] = youtube_source or "youtube_api"
                youtube_fallback_from = profile_payload.get("fallback_from") or (videos_payload.get("fallback_from") if "videos_payload" in locals() and isinstance(videos_payload, dict) else "")
                if youtube_fallback_from:
                    raw_data["youtube_fallback_from"] = youtube_fallback_from

    raw_status = str(
        (raw_data or {}).get("kpi_status")
        or (raw_data or {}).get("provider_status")
        or (raw_data or {}).get("youtube_kpi_status")
        or ""
    ).strip().lower()
    if raw_status in {"ok", "configured", "synced", "success"}:
        _record_platform_test_status(
            platform,
            "synced",
            {"last_live_account_id": int(account_id), "last_live_source": (raw_data or {}).get("source") or ""},
        )

    kpis = calculate_kpis(raw_data or {})
    kpis["snapshot_date"] = str((raw_data or {}).get("snapshot_date") or _today())
    snapshot = _insert_snapshot(int(account_id), kpis)
    posts_written = _insert_posts(account, raw_data or {}, limit=int(_platform_config(platform).get("posts_per_account") or 100))
    account = _sync_account_profile_fields(account, raw_data or {})
    conn.execute(
        "UPDATE vkpi_industry_accounts SET sync_status=?, last_crawled_at=?, last_successful_at=?, crawl_error_count=0, raw_platform_data=? WHERE id=?",
        ("synced", _utcnow(), _utcnow(), _json(raw_data), int(account_id)),
    )
    conn.commit()
    return {
        "account": {**account, "sync_status": "synced"},
        "provider_status": str(kpis.get("youtube_kpi_status") or "synced"),
        "sync_status": "synced",
        "snapshot": snapshot,
        "posts_written": posts_written,
        "updated_by_staff_id": resolve_staff_id(staff),
    }


def sync_enabled_accounts(*, limit: int = 100, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh enabled industry accounts within configured provider gates.

    This is safe for the 08:00 daily job: accounts with disabled crawl settings,
    zero budget, missing keys, or unsupported adapters are counted as skipped and
    no fake snapshots are written.
    """

    ensure_vkpi_product_industry_schema()
    max_accounts = max(1, min(500, int(limit or 100)))
    rows = get_conn().execute(
        """
        SELECT *
        FROM vkpi_industry_accounts
        WHERE is_active=? AND crawl_enabled=?
        ORDER BY COALESCE(last_crawled_at, discovered_at) ASC, id ASC
        LIMIT ?
        """,
        (True, True, max_accounts),
    ).fetchall()
    synced = 0
    skipped = 0
    failed = 0
    statuses: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    for row in rows:
        account = dict(row)
        try:
            result = collect_account_snapshot(int(account["id"]), staff=staff)
            status = str(result.get("sync_status") or result.get("provider_status") or "unknown")
            if status == "synced":
                synced += 1
            else:
                skipped += 1
            statuses[status] = statuses.get(status, 0) + 1
            if len(samples) < 20:
                samples.append(
                    {
                        "account_id": account.get("id"),
                        "platform": account.get("platform"),
                        "handle": account.get("handle"),
                        "sync_status": status,
                        "provider_status": result.get("provider_status"),
                        "message": result.get("message") or "",
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive for live providers.
            failed += 1
            statuses["error"] = statuses.get("error", 0) + 1
            get_conn().execute(
                "UPDATE vkpi_industry_accounts SET sync_status='error', last_crawled_at=?, crawl_error_count=crawl_error_count+1 WHERE id=?",
                (_utcnow(), int(account["id"])),
            )
            get_conn().commit()
            if len(samples) < 20:
                samples.append(
                    {
                        "account_id": account.get("id"),
                        "platform": account.get("platform"),
                        "handle": account.get("handle"),
                        "sync_status": "error",
                        "provider_status": "error",
                        "message": str(exc)[:200],
                    }
                )
    return {
        "status": "ok",
        "candidate_accounts": len(rows),
        "synced": synced,
        "skipped": skipped,
        "failed": failed,
        "statuses": statuses,
        "samples": samples,
    }
