"""YouTube Data API adapter for industry account snapshots.

The crawler is intentionally gated: without a configured API key it returns a
not_configured status and no metric values. It uses stdlib HTTP to avoid adding
a heavy dependency while keeping the future YouTube KPI insertion point stable.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
DEFAULT_MAX_CHANNEL_VIDEOS = 10000


class YouTubeCrawler:
    """Small adapter around the YouTube Data API v3."""

    def __init__(self, api_key: str | None = None, *, timeout_seconds: int = 20) -> None:
        self.api_key = (api_key or os.environ.get("YOUTUBE_API_KEY") or os.environ.get("GOOGLE_YOUTUBE_API_KEY") or "").strip()
        self.timeout_seconds = max(3, min(60, int(timeout_seconds or 20)))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def provider_status(self) -> dict[str, Any]:
        return {
            "provider": "youtube",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "key_visible": False,
        }

    @staticmethod
    def _max_channel_videos(value: int) -> int:
        try:
            upper = int(os.environ.get("VKPI_YOUTUBE_MAX_CHANNEL_VIDEOS", str(DEFAULT_MAX_CHANNEL_VIDEOS)))
        except (TypeError, ValueError):
            upper = DEFAULT_MAX_CHANNEL_VIDEOS
        upper = max(1, min(50_000, upper))
        return max(1, min(upper, int(value or 1)))

    def _not_configured(self, operation: str) -> dict[str, Any]:
        return {
            "provider": "youtube",
            "operation": operation,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "raw": {},
            "message": "YouTube API key 未配置，未执行外部抓取。",
        }

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured(endpoint)
        query = {k: v for k, v in params.items() if v is not None and v != ""}
        query["key"] = self.api_key
        url = f"{YOUTUBE_API_BASE}/{endpoint}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ViltroxMarketing/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310 - fixed Google API host.
                body = response.read().decode("utf-8")
            payload = json.loads(body or "{}")
            payload.setdefault("provider_status", "ok")
            payload.setdefault("sync_status", "synced")
            return payload
        except Exception as exc:  # pragma: no cover - exercised only with live API.
            return {
                "provider": "youtube",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": str(exc),
            }

    @staticmethod
    def normalize_channel_ref(value: str) -> dict[str, str]:
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

    def search_channel_by_name(self, query: str, *, max_results: int = 5) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("search_channel_by_name")
        return self._request(
            "search",
            {
                "part": "snippet",
                "type": "channel",
                "q": query,
                "maxResults": max(1, min(25, int(max_results or 5))),
            },
        )

    def crawl_channel_profile(self, handle_or_url: str, *, channel_id: str = "") -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_profile")
        channel_ref = {"kind": "channel_id", "value": channel_id} if channel_id else self.normalize_channel_ref(handle_or_url)
        params: dict[str, Any] = {"part": "snippet,statistics,brandingSettings,contentDetails"}
        if channel_ref["kind"] == "channel_id":
            params["id"] = channel_ref["value"]
        elif channel_ref["kind"] == "handle":
            params["forHandle"] = channel_ref["value"]
        else:
            search = self.search_channel_by_name(channel_ref["value"], max_results=1)
            items = search.get("items") or []
            found_id = (((items[0] or {}).get("id") or {}).get("channelId") if items else "") or ""
            if not found_id:
                return {"provider": "youtube", "provider_status": "no_results", "sync_status": "no_results", "items": [], "raw": search}
            params["id"] = found_id
        payload = self._request("channels", params)
        payload["query"] = channel_ref
        return payload

    def crawl_channel_videos(self, channel_id: str, *, max_results: int = 25) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_videos")
        target = self._max_channel_videos(int(max_results or 25))
        upload_playlist_id = self._upload_playlist_id(channel_id)
        if upload_playlist_id:
            return self._crawl_upload_playlist(upload_playlist_id, target)
        return self._crawl_channel_videos_by_search(channel_id, target)

    def _upload_playlist_id(self, channel_id: str) -> str:
        profile = self._request("channels", {"part": "contentDetails", "id": channel_id})
        items = profile.get("items") or []
        if not items:
            return ""
        content_details = items[0].get("contentDetails") if isinstance(items[0], dict) else {}
        playlists = content_details.get("relatedPlaylists") if isinstance(content_details, dict) else {}
        return str((playlists or {}).get("uploads") or "").strip()

    def _crawl_upload_playlist(self, playlist_id: str, target: int) -> dict[str, Any]:
        video_ids: list[str] = []
        pages: list[dict[str, Any]] = []
        page_token = ""
        while len(video_ids) < target:
            page = self._request(
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
                    "items": len(page_items),
                    "nextPageToken": bool(page.get("nextPageToken")),
                }
            )
            if str(page.get("provider_status") or "") == "error":
                break
            for item in page_items:
                video_id = str(((item.get("contentDetails") or {}).get("videoId")) or "")
                if video_id and video_id not in video_ids:
                    video_ids.append(video_id)
            page_token = str(page.get("nextPageToken") or "")
            if not page_token or not page_items:
                break
        return self._video_details(video_ids, {"mode": "uploads_playlist", "pages": pages, "video_count": len(video_ids)})

    def _crawl_channel_videos_by_search(self, channel_id: str, target: int) -> dict[str, Any]:
        video_ids: list[str] = []
        search_pages: list[dict[str, Any]] = []
        page_token = ""
        while len(video_ids) < target:
            search = self._request(
                "search",
                {
                    "part": "snippet",
                    "channelId": channel_id,
                    "type": "video",
                    "order": "date",
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
        return self._video_details(video_ids, {"mode": "search", "pages": search_pages, "video_count": len(video_ids)})

    def _video_details(self, video_ids: list[str], raw: dict[str, Any]) -> dict[str, Any]:
        if not video_ids:
            last_page = (raw.get("pages") or [{}])[-1] if isinstance(raw.get("pages"), list) else {}
            return {"provider": "youtube", "provider_status": last_page.get("provider_status") or "no_results", "sync_status": last_page.get("sync_status") or "no_results", "items": [], "raw": raw}
        video_items: list[dict[str, Any]] = []
        for index in range(0, len(video_ids), 50):
            chunk = video_ids[index : index + 50]
            videos = self._request(
                "videos",
                {
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(chunk),
                    "maxResults": len(chunk),
                },
            )
            video_items.extend(videos.get("items") or [])
        return {
            "provider": "youtube",
            "provider_status": "ok",
            "sync_status": "synced",
            "items": video_items,
            "search_raw": raw,
        }

    def crawl_video_comments(self, video_id: str, *, max_results: int = 50) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_video_comments")
        video_id = self._extract_video_id(video_id)
        if not video_id:
            return {
                "provider": "youtube",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": "could not extract video_id",
            }
        limit = max(1, min(100, int(max_results or 50)))
        threads = self._request(
            "commentThreads",
            {
                "part": "snippet,replies",
                "videoId": video_id,
                "order": "relevance",
                "textFormat": "plainText",
                "maxResults": limit,
            },
        )
        if threads.get("provider_status") != "ok":
            return threads
        return {
            **threads,
            "items": self._flatten_comment_threads(threads.get("items") or [], limit),
            "raw": {"video_id": video_id, "thread_count": len(threads.get("items") or [])},
        }

    def _flatten_comment_threads(self, threads: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
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
                replies.extend(self._fetch_comment_replies(top_comment_id, max_results=limit - len(comments)))
            for reply in replies:
                if len(comments) >= limit:
                    break
                item = dict(reply)
                item["depth"] = 1
                comments.append(item)
        return comments[:limit]

    def _fetch_comment_replies(self, parent_id: str, *, max_results: int) -> list[dict[str, Any]]:
        replies: list[dict[str, Any]] = []
        page_token = ""
        while len(replies) < max_results:
            payload = self._request(
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

    @staticmethod
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
