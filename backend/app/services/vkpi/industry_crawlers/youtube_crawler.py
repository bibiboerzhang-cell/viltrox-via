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
        search = self._request(
            "search",
            {
                "part": "snippet",
                "channelId": channel_id,
                "type": "video",
                "order": "date",
                "maxResults": max(1, min(50, int(max_results or 25))),
            },
        )
        video_ids = [
            str(((item.get("id") or {}).get("videoId")) or "")
            for item in (search.get("items") or [])
            if ((item.get("id") or {}).get("videoId"))
        ]
        if not video_ids:
            return {"provider": "youtube", "provider_status": search.get("provider_status") or "no_results", "sync_status": search.get("sync_status") or "no_results", "items": [], "raw": {"search": search}}
        videos = self._request(
            "videos",
            {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids),
                "maxResults": len(video_ids),
            },
        )
        videos["search_raw"] = search
        return videos

    def crawl_video_comments(self, video_id: str, *, max_results: int = 50) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_video_comments")
        return self._request(
            "commentThreads",
            {
                "part": "snippet",
                "videoId": video_id,
                "order": "relevance",
                "textFormat": "plainText",
                "maxResults": max(1, min(100, int(max_results or 50))),
            },
        )
