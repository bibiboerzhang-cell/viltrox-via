"""YouTubeCrawler 的协作函数(W4 class-LOC 拆刀,行为保持型搬家)。

四段职责,均为模块级纯函数或以 ``request`` 回调注入的分页函数:
  * 请求构造:Apify actor input / 频道 videos URL / 增量游标转 RFC3339;
  * 响应解析:Apify item → YouTube API 形状归一、频道档案合成、id 提取;
  * 分页:uploads playlist / search 翻页、评论回复补抓;
  * 富化:videos 端点批量拉 snippet/statistics/contentDetails。

``request`` 参数 = ``YouTubeCrawler._request`` 的 bound method,调用点在类方法里
按属性实时取(``self._request``),strict_video 路径对实例/类的 monkeypatch 仍然生效
——patch 面(``_request`` / ``_should_use_apify_fallback`` / ``_start_apify_run``)
全部留在类上,本模块不复制。

注意:本模块只依赖 stdlib,绝不 import 包内模块(import-time 环棘轮;包
``__init__`` 顶层 import youtube_crawler,兄弟文件再反向顶层 import 会成环)。
youtube_crawler 对本模块一律函数体内 lazy import(与既有
``from . import record_apify_run_cost`` 同一房规)。
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any, Callable

RequestFn = Callable[[str, dict[str, Any]], dict[str, Any]]

DEFAULT_MAX_CHANNEL_VIDEOS = 10000


# ─── 请求构造 ───────────────────────────────────────────────────────────────


def _since_to_rfc3339(since: str | None) -> str:
    """Normalize valid ISO dates/timestamps; never guess an invalid date."""
    from datetime import datetime, timezone

    text = str(since or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return ""


def _max_channel_videos(value: int) -> int:
    try:
        upper = int(os.environ.get("VKPI_YOUTUBE_MAX_CHANNEL_VIDEOS", str(DEFAULT_MAX_CHANNEL_VIDEOS)))
    except (TypeError, ValueError):
        upper = DEFAULT_MAX_CHANNEL_VIDEOS
    upper = max(1, min(50_000, upper))
    return max(1, min(upper, int(value or 1)))


def _normalize_channel_ref(value: str) -> dict[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return {"kind": "empty", "value": ""}
    parsed = urllib.parse.urlparse(raw if "://" in raw else "")
    path = parsed.path.strip("/") if parsed.netloc else raw.strip("/")
    if raw.startswith("UC") and len(raw) >= 10:
        return {"kind": "channel_id", "value": raw}
    match = re.search(r"(?:youtube\.com/)?@([^/?#]+)", raw, flags=re.I)
    if match:
        return {"kind": "handle", "value": match.group(1).strip("@")}
    if path.lower().startswith("channel/"):
        return {"kind": "channel_id", "value": path.split("/", 1)[1]}
    if path.lower().startswith("c/") or path.lower().startswith("user/"):
        return {"kind": "query", "value": path.split("/", 1)[1]}
    return {"kind": "query", "value": raw.strip("@")}


def _videos_channel_ref(channel_id_or_handle: str) -> dict[str, str]:
    """crawl_channel_videos 的 Apify 引用口径:query 形态但形如 UC… 按 channel_id 用。"""
    ref = _normalize_channel_ref(channel_id_or_handle)
    if ref["kind"] == "query" and str(channel_id_or_handle or "").startswith("UC"):
        ref = {"kind": "channel_id", "value": str(channel_id_or_handle or "").strip()}
    return ref


def _apify_channel_input(handle_or_url: str, channel_ref: dict[str, str], *, max_results: int, since: str | None = None) -> dict[str, Any]:
    limit = max(1, min(_max_channel_videos(max_results or 1), 50))
    channel_url = _channel_videos_url(handle_or_url, channel_ref)
    payload: dict[str, Any] = {
        "maxResults": limit,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
    }
    if channel_url:
        payload["startUrls"] = [{"url": channel_url}]
    else:
        payload["searchQueries"] = [str(channel_ref.get("value") or handle_or_url or "").strip()]
    # since best-effort:仅在有 since 时加 oldestPostDate(保 test 对 input dict 的精确等值断言);
    # 真增量精度靠 maxResults 窗口 + 客户端 _filter_incremental_profile_videos 兜底。
    published_after = _since_to_rfc3339(since)
    if published_after:
        payload["oldestPostDate"] = published_after[:10]
    return payload


def _channel_videos_url(handle_or_url: str, channel_ref: dict[str, str]) -> str:
    raw = str(handle_or_url or "").strip()
    if raw.startswith("http"):
        parsed = urllib.parse.urlparse(raw)
        path = parsed.path.rstrip("/")
        if path.endswith("/videos"):
            return raw
        if path:
            return f"{parsed.scheme}://{parsed.netloc}{path}/videos"
    kind = channel_ref.get("kind")
    value = str(channel_ref.get("value") or "").strip().lstrip("@")
    if not value:
        return ""
    if kind == "channel_id" or value.startswith("UC"):
        return f"https://www.youtube.com/channel/{value}/videos"
    if kind == "handle" and re.match(r"^[A-Za-z0-9_.-]+$", value):
        return f"https://www.youtube.com/@{value}/videos"
    return ""


# ─── 分页 ───────────────────────────────────────────────────────────────────


def _upload_playlist_id_from_payload(profile: dict[str, Any]) -> str:
    items = profile.get("items") or []
    if not items:
        return ""
    content_details = items[0].get("contentDetails") if isinstance(items[0], dict) else {}
    playlists = content_details.get("relatedPlaylists") if isinstance(content_details, dict) else {}
    return str((playlists or {}).get("uploads") or "").strip()


def _crawl_upload_playlist(request: RequestFn, playlist_id: str, target: int, *, published_after: str = "") -> dict[str, Any]:
    if published_after:
        from .youtube_crawler_incremental import crawl_incremental_uploads

        return crawl_incremental_uploads(request, playlist_id, target, published_after, _video_details)
    video_ids: list[str] = []
    pages: list[dict[str, Any]] = []
    page_token = ""
    while len(video_ids) < target:
        page = request(
            "playlistItems",
            {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(50, target - len(video_ids)),
                "pageToken": page_token,
            },
        )
        page_items = page.get("items") or []
        pages.append(
            {
                "provider_status": page.get("provider_status"),
                "sync_status": page.get("sync_status"),
                "error_reason": page.get("error_reason"),
                "items": len(page_items),
                "nextPageToken": bool(page.get("nextPageToken")),
            }
        )
        if str(page.get("provider_status") or "") != "ok":
            break
        for item in page_items:
            video_id = str(((item.get("contentDetails") or {}).get("videoId")) or "")
            if video_id and video_id not in video_ids:
                video_ids.append(video_id)
        page_token = str(page.get("nextPageToken") or "")
        if not page_token or not page_items:
            break
    return _video_details(request, video_ids, {"mode": "uploads_playlist", "pages": pages, "video_count": len(video_ids)})


def _crawl_channel_videos_by_search(request: RequestFn, channel_id: str, target: int, *, published_after: str = "") -> dict[str, Any]:
    video_ids: list[str] = []
    search_pages: list[dict[str, Any]] = []
    page_token = ""
    while len(video_ids) < target:
        search = request(
            "search",
            {
                "part": "snippet",
                "channelId": channel_id,
                "type": "video",
                "order": "date",
                "publishedAfter": published_after or None,
                "maxResults": min(50, target - len(video_ids)),
                "pageToken": page_token,
            },
        )
        search_pages.append(
            {
                "provider_status": search.get("provider_status"),
                "sync_status": search.get("sync_status"),
                "items": len(search.get("items") or []),
                "nextPageToken": bool(search.get("nextPageToken")),
            }
        )
        if str(search.get("provider_status") or "") == "error":
            break
        page_items = search.get("items") or []
        for item in page_items:
            video_id = str(((item.get("id") or {}).get("videoId")) or "")
            if video_id and video_id not in video_ids:
                video_ids.append(video_id)
        page_token = str(search.get("nextPageToken") or "")
        if not page_token or not page_items:
            break
    return _video_details(request, video_ids, {"mode": "search", "pages": search_pages, "video_count": len(video_ids)})


def _fetch_comment_replies(request: RequestFn, parent_id: str, *, max_results: int) -> list[dict[str, Any]]:
    replies: list[dict[str, Any]] = []
    page_token = ""
    while len(replies) < max_results:
        payload = request(
            "comments",
            {
                "part": "snippet",
                "parentId": parent_id,
                "textFormat": "plainText",
                "maxResults": max(1, min(100, max_results - len(replies))),
                "pageToken": page_token,
            },
        )
        if payload.get("provider_status") != "ok":
            break
        replies.extend([item for item in payload.get("items") or [] if isinstance(item, dict)])
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            break
    return replies[:max_results]


def _flatten_comment_threads(request: RequestFn, threads: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for thread in threads:
        if not isinstance(thread, dict) or len(comments) >= limit:
            continue
        snippet = thread.get("snippet") if isinstance(thread.get("snippet"), dict) else {}
        top_comment = snippet.get("topLevelComment") if isinstance(snippet.get("topLevelComment"), dict) else None
        top_comment_id = str((top_comment or {}).get("id") or thread.get("id") or "").strip()
        if top_comment:
            top_payload = dict(top_comment)
            top_payload["depth"] = 0
            top_payload["reply_count"] = snippet.get("totalReplyCount") or 0
            comments.append(top_payload)
        if len(comments) >= limit:
            break
        replies = []
        replies_payload = thread.get("replies") if isinstance(thread.get("replies"), dict) else {}
        if isinstance(replies_payload.get("comments"), list):
            replies.extend([item for item in replies_payload.get("comments") or [] if isinstance(item, dict)])
        total_replies = int(snippet.get("totalReplyCount") or 0)
        if top_comment_id and total_replies > len(replies) and len(comments) < limit:
            replies.extend(_fetch_comment_replies(request, top_comment_id, max_results=limit - len(comments)))
        for reply in replies:
            if len(comments) >= limit:
                break
            item = dict(reply)
            item["depth"] = 1
            comments.append(item)
    return comments[:limit]


# ─── 富化 ───────────────────────────────────────────────────────────────────


def _video_details(request: RequestFn, video_ids: list[str], raw: dict[str, Any]) -> dict[str, Any]:
    if not video_ids:
        last_page = (raw.get("pages") or [{}])[-1] if isinstance(raw.get("pages"), list) else {}
        return {"provider": "youtube", "provider_status": last_page.get("provider_status") or "no_results", "sync_status": last_page.get("sync_status") or "no_results", "items": [], "raw": raw}
    video_items: list[dict[str, Any]] = []
    for index in range(0, len(video_ids), 50):
        chunk = video_ids[index : index + 50]
        videos = request(
            "videos",
            {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(chunk),
                "maxResults": len(chunk),
            },
        )
        if videos.get("provider_status") != "ok":
            return {
                "status": "partial" if video_items else "failed",
                "provider": "youtube",
                "provider_status": videos.get("provider_status") or "error",
                "sync_status": videos.get("sync_status") or videos.get("provider_status") or "error",
                "items": video_items,
                "error": videos.get("error"),
                "error_reason": videos.get("error_reason"),
                "http_status": videos.get("http_status"),
                "raw": {"video_ids": video_ids, "partial_count": len(video_items), "error": videos.get("raw") or {}},
            }
        video_items.extend(videos.get("items") or [])
    failed_pages = [page for page in raw.get("pages", []) if page.get("provider_status") not in {"ok", None}]
    return {
        "status": "partial" if failed_pages else "done" if video_items else "empty",
        "provider": "youtube",
        "provider_status": "partial" if failed_pages else "ok",
        "sync_status": "partial" if failed_pages else "synced",
        "items": video_items,
        "search_raw": raw,
    }


# ─── 响应解析(Apify → YouTube API 形状) ──────────────────────────────────


def _fallback_error_passthrough(
    result: dict[str, Any], *, fallback_from: str, reason_payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """actor 未成功时按原样透传(补 fallback_from/youtube_api 两注记,逐字)。"""
    if result.get("provider_status") != "ok":
        result["fallback_from"] = fallback_from
        result["youtube_api"] = reason_payload or {}
        return result
    return None


def _fallback_raw(result: dict[str, Any], reason_payload: dict[str, Any] | None, actor_items: list[dict[str, Any]]) -> dict[str, Any]:
    """兜底结果的 raw 注记块(actor raw + fallback_reason + item 计数,逐字)。"""
    return {
        **(result.get("raw") or {}),
        "fallback_reason": reason_payload or {},
        "actor_item_count": len(actor_items),
    }


def _finish_profile_fallback(
    result: dict[str, Any],
    *,
    channel_ref: dict[str, str],
    fallback_from: str,
    reason_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """_start_apify_run 之后的档案兜底收尾(原 _crawl_channel_profile_apify 后半段,逐字)。"""
    failed = _fallback_error_passthrough(result, fallback_from=fallback_from, reason_payload=reason_payload)
    if failed is not None:
        return failed
    actor_items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    videos = [_normalize_apify_video_item(item) for item in actor_items]
    profile = _profile_from_apify_items(actor_items, channel_ref)
    status = "synced" if actor_items else "no_results"
    return {
        "provider": "youtube",
        "provider_source": "apify",
        "provider_status": "ok" if actor_items else "no_results",
        "sync_status": status,
        "fallback_from": fallback_from,
        "items": [profile] if actor_items else [],
        "videos": videos,
        "query": channel_ref,
        "raw": _fallback_raw(result, reason_payload, actor_items),
    }


def _finish_videos_fallback(
    result: dict[str, Any],
    *,
    fallback_from: str,
    reason_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """_start_apify_run 之后的视频兜底收尾(原 _crawl_channel_videos_apify 后半段,逐字)。"""
    failed = _fallback_error_passthrough(result, fallback_from=fallback_from, reason_payload=reason_payload)
    if failed is not None:
        return failed
    actor_items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    videos = [_normalize_apify_video_item(item) for item in actor_items]
    status = "synced" if videos else "no_results"
    return {
        "provider": "youtube",
        "provider_source": "apify",
        "provider_status": "ok" if videos else "no_results",
        "sync_status": status,
        "fallback_from": fallback_from,
        "items": videos,
        "raw": _fallback_raw(result, reason_payload, actor_items),
    }


def _profile_from_apify_items(items: list[dict[str, Any]], channel_ref: dict[str, str]) -> dict[str, Any]:
    first = items[0] if items else {}
    channel_url = str(first.get("channelUrl") or first.get("channel_url") or "").strip()
    channel_id = _extract_channel_id(channel_url)
    handle = str(channel_ref.get("value") or "").strip().lstrip("@")
    title = str(first.get("channelName") or first.get("channelTitle") or handle or "").strip()
    avatar_url = str(
        first.get("channelAvatarUrl")
        or first.get("channelAvatar")
        or first.get("channelThumbnailUrl")
        or first.get("channelThumbnail")
        or ""
    ).strip()
    subscriber_count = _int(first.get("numberOfSubscribers") or first.get("subscriberCount") or first.get("subscribers"))
    total_views = sum(_int(item.get("viewCount") or item.get("views")) or 0 for item in items) if items else None
    return {
        "kind": "youtube#channel",
        "id": channel_id,
        "profile_url": channel_url or (f"https://www.youtube.com/@{handle}" if handle else ""),
        "url": channel_url or (f"https://www.youtube.com/@{handle}" if handle else ""),
        "channelUrl": channel_url,
        "title": title,
        "name": title,
        "thumbnailUrl": avatar_url,
        "snippet": {
            "title": title,
            "description": str(first.get("channelDescription") or first.get("description") or "").strip(),
            "customUrl": f"@{handle}" if handle else "",
            "thumbnails": {"high": {"url": avatar_url}, "default": {"url": avatar_url}} if avatar_url else {},
        },
        "statistics": {
            "subscriberCount": subscriber_count,
            "videoCount": len(items) if items else None,
            "viewCount": total_views,
        },
        "provider_source": "apify",
    }


def _normalize_apify_video_item(item: dict[str, Any]) -> dict[str, Any]:
    video_url = str(item.get("url") or item.get("videoUrl") or "").strip()
    video_id = _extract_video_id(video_url) or str(item.get("id") or item.get("videoId") or "").strip()
    thumbnail_url = str(item.get("thumbnailUrl") or item.get("thumbnail") or "").strip()
    title = str(item.get("title") or "").strip()
    description = str(item.get("text") or item.get("description") or "").strip()
    published_at = item.get("date") or item.get("uploadDate") or item.get("publishedAt") or item.get("published")
    return {
        **item,
        "kind": "youtube#video",
        "id": video_id,
        "url": video_url,
        "snippet": {
            "title": title,
            "description": description,
            "publishedAt": published_at,
            "channelTitle": item.get("channelName") or item.get("channelTitle") or "",
            "channelId": _extract_channel_id(str(item.get("channelUrl") or "")),
            "thumbnails": {"high": {"url": thumbnail_url}, "default": {"url": thumbnail_url}} if thumbnail_url else {},
        },
        "statistics": {
            "viewCount": _int(item.get("viewCount") or item.get("views")),
            "likeCount": _int(item.get("likes") or item.get("likeCount")),
            "commentCount": _int(item.get("commentsCount") or item.get("comments") or item.get("commentCount")),
        },
        "contentDetails": {"duration": item.get("duration") or ""},
        "provider_source": "apify",
    }


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _extract_channel_id(url_or_id: str) -> str:
    raw = str(url_or_id or "").strip()
    if raw.startswith("UC") and "/" not in raw:
        return raw
    parsed = urllib.parse.urlparse(raw if "://" in raw else "")
    path = parsed.path.strip("/") if parsed.netloc else raw.strip("/")
    if path.lower().startswith("channel/"):
        return path.split("/", 1)[1].split("/", 1)[0]
    return ""


def _extract_video_id(url_or_id: str) -> str:
    """Extract a YouTube video id from URL/Shorts URL/plain id."""
    raw = str(url_or_id or "").strip()
    if not raw:
        return ""
    if len(raw) == 11 and "/" not in raw and "?" not in raw:
        return raw
    parsed = urllib.parse.urlparse(raw if "://" in raw else "")
    if parsed.netloc:
        if parsed.netloc.endswith("youtu.be"):
            return parsed.path.strip("/").split("/")[0]
        query_video = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        if query_video:
            return query_video
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
    return ""
