#!/usr/bin/env python3
"""Audit current real VKPI media assets without triggering crawlers.

This is a live-data diagnostic: it reads existing DB rows and makes tiny HTTP
checks against already-stored image/video URLs. It does not call Apify, YouTube,
or LLM providers.
"""
from __future__ import annotations

from stdout_utils import out

import asyncio
import json
import urllib.error
import urllib.request
from collections import Counter
from typing import Any

from app.api.routers import media
from app.db.connection import close_db_runtime, get_conn

ACCOUNT_AVATAR_KEYS = [
    "avatar_url", "avatarUrl", "profile_pic_url", "profilePicUrl",
    "profile_pic_url_hd", "profilePicUrlHD", "profile_image_url",
    "profileImageUrl", "image_url", "imageUrl", "picture",
]
ACCOUNT_PROFILE_KEYS = ["profile_url", "profileUrl", "platform_url", "homepage_url", "inputUrl", "url"]
POST_THUMBNAIL_KEYS = [
    "thumbnail_url", "thumbnail", "thumbnailUrl", "cover_url", "coverUrl",
    "image_url", "imageUrl", "display_url", "displayUrl", "display_url_hd",
    "displayUrlHD", "video_cover_url", "videoCoverUrl", "preview_url",
    "previewUrl", "poster_url", "posterUrl",
]
POST_VIDEO_KEYS = [
    "video_url", "videoUrl", "videoUrlNoWaterMark", "video_url_no_watermark",
    "video_download_url", "videoDownloadUrl", "downloadUrl", "downloadAddr",
    "media_url", "mediaUrl", "play_url", "playUrl", "url_to_video",
    "source_video_url",
]
POST_URL_KEYS = ["post_url", "postUrl", "webVideoUrl", "permalink_url", "permalinkUrl", "permalink", "shortCodeUrl", "external_url", "link", "url"]


def pick(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    raw = row.get("raw_platform_data")
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            data = {}
        for key in keys:
            value = data.get(key) if isinstance(data, dict) else None
            if value not in (None, ""):
                return str(value).strip()
    return ""


def check_image(url: str) -> tuple[bool, str]:
    try:
        normalized, host = media._allowed_external_image_url(url)
        data, content_type = media._fetch_external_image(normalized, host)
        return bool(data) and content_type.startswith("image/"), content_type
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_video_range(url: str) -> tuple[bool, str]:
    try:
        normalized, host = media._allowed_external_video_url(url)
        req = urllib.request.Request(
            normalized,
            headers=media._upstream_video_headers(host, "bytes=0-0"),
        )
        with urllib.request.urlopen(req, timeout=12) as resp:  # nosec B310 - allowlisted by router helper.
            status = int(getattr(resp, "status", 200) or 200)
            content_type = str(resp.headers.get("content-type") or "")
            resp.read(1)
            return status in {200, 206}, f"{status} {content_type}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTPError: {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    conn = get_conn()
    accounts = [dict(r) for r in conn.execute(
        """
        SELECT id, platform, handle, display_name, avatar_url, profile_url, sync_status,
               crawl_enabled, last_successful_at, raw_platform_data
        FROM vkpi_industry_accounts
        ORDER BY COALESCE(last_successful_at, last_crawled_at, discovered_at) DESC NULLS LAST, id DESC
        LIMIT 50
        """
    ).fetchall()]
    posts = [dict(r) for r in conn.execute(
        """
        SELECT id, account_id, platform, post_url, thumbnail_url, video_url, media_type,
               title, caption, views, likes, comments, published_at, raw_platform_data
        FROM vkpi_industry_posts
        ORDER BY COALESCE(published_at, created_at) DESC NULLS LAST, id DESC
        LIMIT 80
        """
    ).fetchall()]

    account_rows = []
    for row in accounts:
        avatar = pick(row, ACCOUNT_AVATAR_KEYS)
        profile = pick(row, ACCOUNT_PROFILE_KEYS)
        account_rows.append({**row, "_avatar": avatar, "_profile": profile})

    post_rows = []
    for row in posts:
        thumb = pick(row, POST_THUMBNAIL_KEYS)
        video = pick(row, POST_VIDEO_KEYS)
        post_url = pick(row, POST_URL_KEYS)
        post_rows.append({**row, "_thumbnail": thumb, "_video": video, "_post_url": post_url})

    c = Counter()
    c["accounts_total"] = len(account_rows)
    c["accounts_avatar_present"] = sum(1 for r in account_rows if r["_avatar"])
    c["accounts_profile_url_present"] = sum(1 for r in account_rows if r["_profile"])
    c["posts_total"] = len(post_rows)
    c["posts_thumbnail_present"] = sum(1 for r in post_rows if r["_thumbnail"])
    c["posts_video_present"] = sum(1 for r in post_rows if r["_video"])
    c["posts_platform_url_present"] = sum(1 for r in post_rows if r["_post_url"])

    image_samples = []
    seen = set()
    for url in [r["_avatar"] for r in account_rows] + [r["_thumbnail"] for r in post_rows]:
        if url and url not in seen:
            seen.add(url)
            image_samples.append(url)
        if len(image_samples) >= 4:
            break

    video_samples = []
    seen = set()
    for url in [r["_video"] for r in post_rows]:
        if url and url not in seen:
            seen.add(url)
            video_samples.append(url)
        if len(video_samples) >= 3:
            break

    image_checks = [check_image(url) for url in image_samples]
    video_checks = [check_video_range(url) for url in video_samples]

    out("VKPI_MEDIA_LIVE_ASSET_AUDIT")
    for key in sorted(c):
        out(f"{key}={c[key]}")
    out(f"image_samples={len(image_samples)} ok={sum(1 for ok, _ in image_checks if ok)}")
    for i, (ok, detail) in enumerate(image_checks, 1):
        out(f"image_sample_{i}={'ok' if ok else 'fail'} {detail}")
    out(f"video_samples={len(video_samples)} ok={sum(1 for ok, _ in video_checks if ok)}")
    for i, (ok, detail) in enumerate(video_checks, 1):
        out(f"video_sample_{i}={'ok' if ok else 'fail'} {detail}")

    missing_avatar = [r for r in account_rows if not r["_avatar"]]
    if missing_avatar:
        out("missing_avatar_accounts=" + ", ".join(f"{r['platform']}:{r.get('handle') or r.get('display_name')}#{r['id']}[{r.get('sync_status')}]" for r in missing_avatar[:10]))
    missing_media = [r for r in post_rows if not r["_thumbnail"] and not r["_video"]]
    if missing_media:
        out("missing_media_posts=" + ", ".join(f"{r['platform']}:{r['id']}" for r in missing_media[:10]))

    if image_samples and not any(ok for ok, _ in image_checks):
        out("AUDIT_STATUS=blocked:image_urls_present_but_unreachable")
        return 2
    if video_samples and not any(ok for ok, _ in video_checks):
        out("AUDIT_STATUS=blocked:video_urls_present_but_unreachable_or_expired")
        return 2
    if any(not ok for ok, _ in image_checks + video_checks) or missing_avatar or missing_media:
        out("AUDIT_STATUS=degraded:some_media_missing_or_expired")
        return 0
    out("AUDIT_STATUS=ok")
    return 0


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    finally:
        try:
            asyncio.run(close_db_runtime())
        except Exception:
            pass
    raise SystemExit(exit_code)
