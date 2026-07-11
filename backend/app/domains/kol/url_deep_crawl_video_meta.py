"""账号代表/历史视频 metadata 提取(从 url_deep_crawl.py 抽出,行为不变)。

纯解析:provider videos_items → 规范化 metadata + 增量过滤。依赖全来自中性模块(url_deep_crawl_helpers / pool_common),
ClassifiedUrl 仅作类型注解(from __future__ annotations 字符串化,运行时不需导入)。被 url_deep_crawl re-export。
红线:纯 metadata 解析,零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.domains.kol.pool_common import _first_present, _int_or_none
# 线上修(2026-07-10):本文件 :131 一直在用 profile_crawl_source 却从未 import——
# 走到该分支即 NameError,深爬任务进 triage(线上 6 条实证)。补上缺失的 import。
from app.domains.kol.video_evidence_sources import profile_crawl_source
from app.domains.kol.url_deep_crawl_helpers import (
    _duration_seconds,
    _metadata_text,
    _parse_date,
    _profile_video_dedupe_key,
    _profile_video_is_newer_than_cutoff,
    _video_id,
)


def _profile_representative_video_metadata(
    classified: ClassifiedUrl,
    crawl: dict[str, Any],
    *,
    limit: int,
    exclude_video_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    items = crawl.get("videos_items") if isinstance(crawl.get("videos_items"), list) else []
    provider_source = str(crawl.get("provider_source") or "").strip()
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    excluded_keys = {
        _profile_video_dedupe_key(classified.platform, url)
        for url in (exclude_video_urls or [])
        if str(url or "").strip()
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = _metadata_from_profile_video_item(classified, item, provider_source=provider_source)
        content_url = str(metadata.get("content_url") or "").strip()
        if not content_url:
            continue
        dedupe_key = _profile_video_dedupe_key(classified.platform, content_url)
        if dedupe_key in excluded_keys:
            continue
        if dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        results.append(metadata)
        if len(results) >= limit:
            break
    return results


def _filter_incremental_profile_videos(
    videos: list[dict[str, Any]],
    incremental_state: dict[str, Any],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    cutoff = _metadata_text((incremental_state or {}).get("last_video_at"))
    if not cutoff:
        return videos[:limit], 0
    selected: list[dict[str, Any]] = []
    skipped = 0
    for metadata in videos:
        if _profile_video_is_newer_than_cutoff(metadata, cutoff):
            selected.append(metadata)
            if len(selected) >= limit:
                break
        else:
            skipped += 1
    return selected, skipped


def _metadata_from_profile_video_item(
    classified: ClassifiedUrl,
    item: dict[str, Any],
    *,
    provider_source: str,
) -> dict[str, Any]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    content_details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    platform = classified.platform
    video_id = _profile_video_id(platform, item)
    content_url = _profile_video_url(platform, item, video_id=video_id, classified=classified)
    published = _first_present(
        snippet.get("publishedAt"),
        item.get("publish_date"),
        item.get("published_at"),
        item.get("publishedAt"),
        item.get("uploadDate"),
        item.get("date"),
        item.get("timestamp"),
        item.get("createTimeISO"),
        item.get("createTime"),
        item.get("takenAtIso"),
    )
    channel_id = _metadata_text(_first_present(snippet.get("channelId"), item.get("channel_id"), item.get("channelId"), classified.channel_id))
    channel_name = _metadata_text(
        _first_present(
            snippet.get("channelTitle"),
            item.get("channel_name"),
            item.get("channelName"),
            item.get("authorName"),
            item.get("ownerUsername"),
            classified.handle,
        )
    )
    return {
        "platform": platform,
        "content_url": content_url,
        "title": _metadata_text(_first_present(item.get("title"), snippet.get("title"), item.get("caption"), item.get("text"))) or content_url,
        "description": _metadata_text(_first_present(item.get("description"), snippet.get("description"), item.get("caption"), item.get("text"))),
        "view_count": _int_or_none(_first_present(stats.get("viewCount"), item.get("view_count"), item.get("viewCount"), item.get("views"), item.get("playCount"), item.get("videoPlayCount"), item.get("videoViewCount"))),
        "like_count": _int_or_none(_first_present(stats.get("likeCount"), item.get("like_count"), item.get("likeCount"), item.get("likes"), item.get("likesCount"), item.get("diggCount"))),
        "comment_count": _int_or_none(_first_present(stats.get("commentCount"), item.get("comment_count"), item.get("commentCount"), item.get("comments"), item.get("commentsCount"), item.get("commentCount"))),
        "share_count": _int_or_none(_first_present(item.get("share_count"), item.get("shareCount"), item.get("shares"), item.get("sharesCount"))),
        "publish_date": published,
        "posted_at": _parse_date(published),
        "duration_seconds": _duration_seconds(_first_present(content_details.get("duration"), item.get("duration_seconds"), item.get("durationSeconds"), item.get("duration"))),
        "thumbnail_url": _profile_video_thumbnail(item, snippet),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "scrape_source": _metadata_text(item.get("provider_source")) or provider_source or profile_crawl_source(platform),
        "scrape_status": "success",
    }


def _profile_video_id(platform: str, item: dict[str, Any]) -> str:
    raw_id = _metadata_text(_first_present(item.get("id"), item.get("video_id"), item.get("videoId"), item.get("shortCode"), item.get("shortcode"), item.get("code")))
    if platform == "youtube":
        if isinstance(item.get("id"), dict):
            raw_id = _metadata_text((item.get("id") or {}).get("videoId"))
        if raw_id:
            return raw_id
        video_url = _profile_video_url("youtube", item, video_id="", classified=None)
        parsed = urlparse(video_url)
        return _video_id("youtube", parsed.netloc, parsed.path, parsed.query)
    return raw_id


def _profile_video_url(
    platform: str,
    item: dict[str, Any],
    *,
    video_id: str,
    classified: ClassifiedUrl | None,
) -> str:
    for key in ("content_url", "url", "videoUrl", "video_url", "web_url", "webUrl", "permalink", "link"):
        value = _metadata_text(item.get(key))
        if value.startswith("http"):
            return value
    if platform == "youtube" and video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    if platform == "instagram" and video_id:
        return f"https://www.instagram.com/p/{video_id.strip('/')}/"
    if platform == "tiktok" and video_id and classified and classified.handle:
        return f"https://www.tiktok.com/@{classified.handle.lstrip('@')}/video/{video_id}"
    return ""


def _profile_video_thumbnail(item: dict[str, Any], snippet: dict[str, Any]) -> str:
    for key in (
        "thumbnail_url",
        "thumbnailUrl",
        "thumbnail",
        "displayUrl",
        "imageUrl",
        "coverUrl",
        "cover",
        # TikTok clockworks/tiktok-scraper top-level cover variants (IG uses
        # displayUrl above; TikTok usually nests covers under videoMeta below,
        # but some payloads also surface these at the top level).
        "originalCoverUrl",
        "dynamicCover",
    ):
        value = _metadata_text(item.get(key))
        if value.startswith("http"):
            return value
    # TikTok clockworks actor puts the real cover under videoMeta.* (mirrors
    # channels/official.py and account_scan_service). Top-level coverUrl is
    # rarely populated for TikTok, which is why TikTok thumbnails were empty.
    video_meta = item.get("videoMeta") if isinstance(item.get("videoMeta"), dict) else {}
    for key in ("coverUrl", "originalCoverUrl", "dynamicCoverUrl", "cover"):
        value = _metadata_text(video_meta.get(key))
        if value.startswith("http"):
            return value
    # `covers` array fallback (apify.py reads covers[0]); honest empty if absent.
    covers = item.get("covers")
    if isinstance(covers, list):
        for entry in covers:
            url = _metadata_text(entry.get("url") if isinstance(entry, dict) else entry)
            if url.startswith("http"):
                return url
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        value = thumbnails.get(key)
        if isinstance(value, dict):
            url = _metadata_text(value.get("url"))
            if url.startswith("http"):
                return url
    return ""
