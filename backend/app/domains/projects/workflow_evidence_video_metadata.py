"""Video metadata scraping helpers for V-KPI workflow evidence.

行为不变搬迁(move + re-export):从 workflow_evidence.py 整体搬出
「视频元数据抓取」内聚簇 —— 纯文本/数值规整辅助 + 平台识别 + YouTube API /
Apify 抓取链路。函数体逐字不变,只依赖标准库与本模块内的同簇函数,
不依赖原文件留下的任何名字,故零循环导入风险。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compact_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = int(value)
        return parsed if parsed >= 0 else None
    raw = str(value).replace(",", "").strip()
    if not raw or raw.lower() in {"none", "null", "nan", "-"}:
        return None
    match = re.match(r"^([0-9]*\.?[0-9]+)\s*([kKmMbB])?$", raw)
    if match:
        number = float(match.group(1))
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get((match.group(2) or "").lower(), 1)
        parsed = int(number * multiplier)
        return parsed if parsed >= 0 else None
    try:
        parsed = int(float(raw))
        return parsed if parsed >= 0 else None
    except ValueError:
        return None


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        current: Any = data
        found = True
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, ""):
            return current
    return None


def _detect_video_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "tiktok.com" in host:
        return "tiktok"
    if "instagram.com" in host:
        return "instagram"
    if "facebook.com" in host or "fb.watch" in host:
        return "facebook"
    if host in {"x.com", "twitter.com"} or host.endswith(".x.com") or host.endswith(".twitter.com"):
        return "x"
    if "bilibili.com" in host:
        return "bilibili"
    return "unknown"


def _youtube_video_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        return parsed.path.strip("/").split("/")[0]
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("v"):
        return query["v"][0]
    for pattern in (r"/shorts/([^/?#]+)", r"/embed/([^/?#]+)"):
        match = re.search(pattern, parsed.path)
        if match:
            return match.group(1)
    return ""


def _duration_seconds(value: Any) -> int | None:
    raw = _text(value)
    if not raw:
        return None
    if raw.startswith("PT"):
        match = re.fullmatch(
            r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
            raw,
        )
        if not match:
            return None
        parts = {key: int(val or 0) for key, val in match.groupdict().items()}
        return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]
    pieces = raw.split(":")
    if not all(piece.isdigit() for piece in pieces):
        return _compact_int(raw)
    values = [int(piece) for piece in pieces]
    if len(values) == 2:
        return values[0] * 60 + values[1]
    if len(values) == 3:
        return values[0] * 3600 + values[1] * 60 + values[2]
    return values[0] if len(values) == 1 else None


def _published_pair(value: Any) -> tuple[str | None, str | None]:
    raw = _text(value)
    if not raw:
        return None, None
    if re.fullmatch(r"\d{8}", raw):
        raw = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}T00:00:00Z"
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw, raw[:10]
    return raw, None


def _youtube_api_metadata(video_url: str) -> dict[str, Any]:
    video_id = _youtube_video_id(video_url)
    if not video_id:
        raise ValueError("YouTube URL missing video id")
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY is not configured")
    params = urllib.parse.urlencode({
        "part": "snippet,statistics,contentDetails",
        "id": video_id,
        "key": api_key,
    })
    request_url = f"https://www.googleapis.com/youtube/v3/videos?{params}"
    try:
        with urllib.request.urlopen(request_url, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"youtube_api_http_{exc.code}:{body}") from exc
    items = payload.get("items") or []
    if not items:
        raise LookupError("youtube video not found")
    item = items[0]
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    details = item.get("contentDetails") or {}
    published_at, posted_at = _published_pair(snippet.get("publishedAt"))
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    high_thumb = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
    return {
        "platform": "youtube",
        "content_url": f"https://www.youtube.com/watch?v={video_id}",
        "title": _text(snippet.get("title")),
        "description": _text(snippet.get("description")),
        "view_count": _compact_int(stats.get("viewCount")),
        "like_count": _compact_int(stats.get("likeCount")),
        "comment_count": _compact_int(stats.get("commentCount")),
        "share_count": None,
        "publish_date": published_at,
        "posted_at": posted_at,
        "duration_seconds": _duration_seconds(details.get("duration")),
        "thumbnail_url": _text(high_thumb.get("url")),
        "channel_id": _text(snippet.get("channelId")),
        "channel_name": _text(snippet.get("channelTitle")),
        "scrape_source": "youtube_api",
        "scrape_status": "success",
        "scrape_error": "",
    }


def _apify_actor_for(platform: str) -> str:
    defaults = {
        "youtube": "streamers/youtube-scraper",
        "instagram": "apify/instagram-scraper",
        "tiktok": "clockworks/tiktok-scraper",
        "facebook": "apify/facebook-posts-scraper",
    }
    env_key = f"APIFY_{platform.upper()}_ACTOR_ID"
    actor_id = os.getenv(env_key, "").strip() or defaults.get(platform, "")
    if not actor_id:
        raise ValueError(f"Apify actor not configured for platform: {platform}")
    return actor_id


def _apify_proxy_config(platform: str) -> dict[str, Any]:
    """按平台返回 Apify 代理配置。IG/TikTok 反爬重,默认走住宅代理(命中率高,能打穿
    'Request blocked' 拦截)。env ``APIFY_<PLATFORM>_PROXY_GROUPS`` 可调以平衡成本:
      ``RESIDENTIAL``(默认 IG/TikTok,贵但稳)/ ``DATACENTER``(便宜易被拦)/
      逗号分隔多组 / ``off`` 或空串(不挂代理,回退 actor 默认)。YT/FB 默认不挂。
    """
    default_groups = {"instagram": "RESIDENTIAL", "tiktok": "RESIDENTIAL"}
    raw = os.getenv(f"APIFY_{platform.upper()}_PROXY_GROUPS", default_groups.get(platform, "")).strip()
    if raw.lower() in {"off", "none", ""}:
        return {}
    if raw.lower() in {"datacenter", "dc"}:
        return {"useApifyProxy": True}
    groups = [g.strip().upper() for g in raw.split(",") if g.strip()]
    if not groups:
        return {}
    return {"useApifyProxy": True, "apifyProxyGroups": groups}


def _apify_input(platform: str, video_url: str) -> dict[str, Any]:
    if platform == "youtube":
        payload: dict[str, Any] = {"startUrls": [{"url": video_url}], "maxResults": 1}
    elif platform == "instagram":
        payload = {"directUrls": [video_url], "resultsType": "posts", "resultsLimit": 1}
    elif platform == "tiktok":
        payload = {
            "postURLs": [video_url],
            "resultsPerPage": 1,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }
    elif platform == "facebook":
        payload = {"startUrls": [{"url": video_url}], "resultsLimit": 1}
    else:
        raise ValueError(f"unsupported Apify platform: {platform}")
    proxy = _apify_proxy_config(platform)
    if proxy:
        payload["proxyConfiguration"] = proxy
    return payload


def _apify_item_metadata(platform: str, video_url: str, item: dict[str, Any], run_id: str) -> dict[str, Any]:
    if platform == "youtube":
        published_at, posted_at = _published_pair(_first(item, "date", "publishedAt", "publishDate"))
        return {
            "platform": "youtube",
            "content_url": _text(_first(item, "url", "input")) or video_url,
            "title": _text(_first(item, "title")),
            "description": _text(_first(item, "description", "text")),
            "view_count": _compact_int(_first(item, "viewCount", "views", "view_count")),
            "like_count": _compact_int(_first(item, "likes", "likeCount", "like_count")),
            "comment_count": _compact_int(_first(item, "commentsCount", "commentCount", "comments")),
            "share_count": _compact_int(_first(item, "shareCount", "shares")),
            "publish_date": published_at,
            "posted_at": posted_at,
            "duration_seconds": _duration_seconds(_first(item, "duration")),
            "thumbnail_url": _text(_first(item, "thumbnailUrl", "thumbnail", "thumbnails.0.url")),
            "channel_id": _text(_first(item, "channelId")),
            "channel_name": _text(_first(item, "channelName")),
            "scrape_source": "apify",
            "scrape_status": "success",
            "scrape_error": "",
            "apify_run_id": run_id,
        }
    if platform == "instagram":
        caption = _text(_first(item, "caption", "text"))
        published_at, posted_at = _published_pair(_first(item, "timestamp", "takenAtTimestamp"))
        images = item.get("images") if isinstance(item.get("images"), list) else []
        return {
            "platform": "instagram",
            "content_url": _text(_first(item, "url")) or video_url,
            "title": caption[:500],
            "description": caption,
            "view_count": _compact_int(_first(item, "videoViewCount", "videoPlayCount", "viewCount", "viewsCount")),
            "like_count": _compact_int(_first(item, "likesCount", "likeCount", "likes")),
            "comment_count": _compact_int(_first(item, "commentsCount", "commentCount", "comments")),
            "share_count": _compact_int(_first(item, "shareCount", "shares")),
            "publish_date": published_at,
            "posted_at": posted_at,
            "duration_seconds": _duration_seconds(_first(item, "videoDuration", "duration")),
            "thumbnail_url": _text(_first(item, "displayUrl", "thumbnailUrl")) or (str(images[0]) if images else ""),
            "channel_id": _text(_first(item, "ownerId")),
            "channel_name": _text(_first(item, "ownerUsername", "ownerFullName")),
            "scrape_source": "apify",
            "scrape_status": "success",
            "scrape_error": "",
            "apify_run_id": run_id,
        }
    if platform == "tiktok":
        title = _text(_first(item, "text", "title"))
        published_at, posted_at = _published_pair(_first(item, "createTimeISO", "createTime", "timestamp"))
        return {
            "platform": "tiktok",
            "content_url": _text(_first(item, "webVideoUrl", "submittedVideoUrl")) or video_url,
            "title": title[:500],
            "description": title,
            "view_count": _compact_int(_first(item, "playCount", "viewCount", "views")),
            "like_count": _compact_int(_first(item, "diggCount", "likeCount", "likes")),
            "comment_count": _compact_int(_first(item, "commentCount", "commentsCount", "comments")),
            "share_count": _compact_int(_first(item, "shareCount", "shares")),
            "publish_date": published_at,
            "posted_at": posted_at,
            "duration_seconds": _duration_seconds(_first(item, "videoMeta.duration", "duration")),
            "thumbnail_url": _text(_first(item, "videoMeta.coverUrl", "thumbnailUrl")),
            "channel_id": _text(_first(item, "authorMeta.id")),
            "channel_name": _text(_first(item, "authorMeta.name", "authorMeta.nickName")),
            "scrape_source": "apify",
            "scrape_status": "success",
            "scrape_error": "",
            "apify_run_id": run_id,
        }
    text = _text(_first(item, "text", "title"))
    published_at, posted_at = _published_pair(_first(item, "time", "timestamp", "date"))
    return {
        "platform": "facebook",
        "content_url": _text(_first(item, "facebookUrl", "url", "postUrl")) or video_url,
        "title": text[:500],
        "description": text,
        "view_count": _compact_int(_first(item, "viewsCount", "viewCount", "views")),
        "like_count": _compact_int(_first(item, "likes", "likesCount", "likeCount")),
        "comment_count": _compact_int(_first(item, "comments", "commentsCount", "commentCount")),
        "share_count": _compact_int(_first(item, "shares", "shareCount")),
        "publish_date": published_at,
        "posted_at": posted_at,
        "duration_seconds": _duration_seconds(_first(item, "duration")),
        "thumbnail_url": _text(_first(item, "thumbnailUrl", "imageUrl")),
        "channel_id": _text(_first(item, "pageId", "user.id")),
        "channel_name": _text(_first(item, "pageName", "user.name")),
        "scrape_source": "apify",
        "scrape_status": "success",
        "scrape_error": "",
        "apify_run_id": run_id,
    }


def _apify_scrape_attempts() -> int:
    """整次抓取的最大尝试数(env ``APIFY_SCRAPE_MAX_ATTEMPTS``,默认 2,夹在 1..4)。
    平台反爬(IG/TikTok)常间歇拦截,换 session/代理重跑一次往往就过。"""
    try:
        return max(1, min(4, int(os.getenv("APIFY_SCRAPE_MAX_ATTEMPTS", "2"))))
    except (TypeError, ValueError):
        return 2


def _apify_item_is_real(item: dict[str, Any]) -> bool:
    """dataset item 是否为可用的真 post(有 owner 或正文),而非错误哨兵。"""
    return bool(
        _text(_first(item, "ownerUsername", "ownerFullName", "channelName", "authorMeta.name", "authorMeta.nickName", "pageName"))
        or _text(_first(item, "title", "caption", "text", "shortCode"))
    )


def _apify_metadata(platform: str, video_url: str) -> dict[str, Any]:
    token = os.getenv("APIFY_API_TOKEN", "").strip() or os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise RuntimeError("APIFY_API_TOKEN is not configured")
    try:
        from apify_client import ApifyClient
    except ImportError as exc:  # pragma: no cover - environment dependency
        raise RuntimeError("apify-client is not installed") from exc
    actor_id = _apify_actor_for(platform)
    client = ApifyClient(token)
    run_input = _apify_input(platform, video_url)
    attempts = _apify_scrape_attempts()
    last_reason = "Apify returned no items"
    # 反爬重试:每次 actor.call 都是全新 session,_apify_input 已注入(默认住宅)代理组。
    # 错误哨兵(run SUCCEEDED 但 item 只有 error/errorDescription,平台拦截/私密/已删)或空结果 → 换次重跑;
    # 全部尝试仍失败 → 抬成显式失败带真因(绝不把失败谎报成 scrape_status=success,误导排查)。
    for _ in range(attempts):
        run = client.actor(actor_id).call(run_input=run_input, timeout_secs=300)
        dataset_id = run.get("defaultDatasetId")
        items = client.dataset(dataset_id).list_items(limit=5).items if dataset_id else []
        if not items:
            last_reason = "Apify returned no items"
            continue
        first = dict(items[0])
        if not _apify_item_is_real(first):
            last_reason = _text(first.get("errorDescription")) or _text(first.get("error")) or "Apify returned no usable item"
            continue
        metadata = _apify_item_metadata(platform, video_url, first, _text(run.get("id")))
        metadata["scrape_error"] = ""
        return metadata
    raise LookupError(f"{platform}_scrape_unavailable: {last_reason}"[:240])


def _fetch_video_metadata(video_url: str) -> dict[str, Any]:
    platform = _detect_video_platform(video_url)
    if platform == "unknown":
        raise ValueError("unsupported or unknown video platform")
    if platform == "youtube":
        try:
            return _youtube_api_metadata(video_url)
        except Exception as exc:
            fallback = _apify_metadata(platform, video_url)
            fallback["scrape_error"] = f"youtube_api_fallback:{str(exc)[:240]}"
            return fallback
    return _apify_metadata(platform, video_url)
