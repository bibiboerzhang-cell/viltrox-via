"""YouTube Data API adapter for industry account snapshots.

The crawler is intentionally gated: without a configured API key it returns a
not_configured status and no metric values. It uses stdlib HTTP to avoid adding
a heavy dependency while keeping the future YouTube KPI insertion point stable.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _since_to_rfc3339(since: str | None) -> str:
    """增量游标(YYYY-MM-DD 或含时间)→ RFC3339(publishedAfter 用)。
    游标来源 url_deep_crawl._parse_date 恒 YYYY-MM-DD;无法识别返回空串(=不下推 since,退回全量+客户端裁剪)。"""
    text = str(since or "").strip()
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return f"{text}T00:00:00Z"
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text):
        return text if text.endswith("Z") or "+" in text[11:] else f"{text}Z"
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return f"{match.group(1)}T00:00:00Z" if match else ""


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
DEFAULT_MAX_CHANNEL_VIDEOS = 10000
DEFAULT_APIFY_ACTOR_ID = "streamers/youtube-scraper"
YOUTUBE_QUOTA_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "userRateLimitExceeded",
    "rateLimitExceeded",
}


class YouTubeCrawler:
    """Small adapter around the YouTube Data API v3."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        apify_token: str | None = None,
        actor_id: str | None = None,
        timeout_seconds: int = 20,
        run_timeout_seconds: int = 240,
    ) -> None:
        self.api_key = (api_key or os.environ.get("YOUTUBE_API_KEY") or os.environ.get("GOOGLE_YOUTUBE_API_KEY") or "").strip()
        self.apify_token = (apify_token or os.environ.get("APIFY_TOKEN") or "").strip()
        self.apify_actor_id = (actor_id or os.environ.get("APIFY_YOUTUBE_ACTOR_ID") or DEFAULT_APIFY_ACTOR_ID).strip()
        self.timeout_seconds = max(3, min(60, int(timeout_seconds or 20)))
        self.run_timeout_seconds = max(30, min(1800, int(run_timeout_seconds or 240)))

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self.apify_token)

    def provider_status(self) -> dict[str, Any]:
        return {
            "provider": "youtube",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "youtube_api_configured": bool(self.api_key),
            "apify_configured": bool(self.apify_token),
            "apify_actor_id": self.apify_actor_id,
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
            "message": "YouTube API key 和 APIFY_TOKEN 均未配置，未执行外部抓取。",
        }

    def _api_not_configured(self, operation: str) -> dict[str, Any]:
        return {
            "provider": "youtube",
            "operation": operation,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "raw": {},
            "message": "YouTube API key 未配置。",
        }

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            return self._api_not_configured(endpoint)
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
        except urllib.error.HTTPError as exc:  # pragma: no cover - exercised only with live API.
            return self._http_error_payload(exc)
        except Exception as exc:  # pragma: no cover - exercised only with live API.
            return {
                "provider": "youtube",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": str(exc),
            }

    def _http_error_payload(self, exc: urllib.error.HTTPError) -> dict[str, Any]:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        raw_error: dict[str, Any] = {}
        if body:
            try:
                parsed = json.loads(body)
                raw_error = parsed if isinstance(parsed, dict) else {}
            except Exception:
                raw_error = {"body": body[:1000]}
        error_info = raw_error.get("error") if isinstance(raw_error.get("error"), dict) else {}
        errors = error_info.get("errors") if isinstance(error_info.get("errors"), list) else []
        first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
        reason = str(first_error.get("reason") or error_info.get("status") or "").strip()
        status = "quota_exceeded" if reason in YOUTUBE_QUOTA_REASONS else "error"
        return {
            "provider": "youtube",
            "provider_status": status,
            "sync_status": status,
            "items": [],
            "error": str(exc),
            "error_reason": reason,
            "http_status": int(getattr(exc, "code", 0) or 0),
            "raw": {"error": raw_error},
        }

    @staticmethod
    def _should_use_apify_fallback(payload: dict[str, Any]) -> bool:
        status = str((payload or {}).get("provider_status") or (payload or {}).get("sync_status") or "").strip()
        reason = str((payload or {}).get("error_reason") or "").strip()
        return status == "quota_exceeded" or reason in YOUTUBE_QUOTA_REASONS

    def _apify_not_configured(self, operation: str, reason_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {
            "provider": "youtube",
            "operation": operation,
            "provider_status": "quota_exceeded" if reason_payload else "not_configured",
            "sync_status": "quota_exceeded" if reason_payload else "not_configured",
            "items": [],
            "raw": {"youtube_api": reason_payload or {}},
            "apify_fallback_status": "not_configured",
            "message": "YouTube API quota 已用尽，但 APIFY_TOKEN 未配置，无法 fallback。",
        }
        if reason_payload:
            result["error_reason"] = reason_payload.get("error_reason") or "quotaExceeded"
            result["http_status"] = reason_payload.get("http_status")
        return result

    def _start_apify_run(self, input_payload: dict[str, Any], *, operation: str) -> dict[str, Any]:
        if not self.apify_token:
            return self._apify_not_configured(operation)
        try:
            from apify_client import ApifyClient  # type: ignore

            client = ApifyClient(self.apify_token)
            actor_path = self.apify_actor_id.replace("/", "~")
            run = client.actor(actor_path).call(
                run_input=input_payload,
                timeout_secs=self.run_timeout_seconds,
                wait_secs=self.run_timeout_seconds,
            )
            if not run or str(run.get("status") or "").upper() != "SUCCEEDED":
                return {
                    "provider": "youtube",
                    "provider_status": "error",
                    "sync_status": "error",
                    "items": [],
                    "error": f"YouTube Apify actor did not finish: {str((run or {}).get('status') or 'unknown')}",
                    "raw": {"actor_id": self.apify_actor_id, "input": input_payload},
                }
            from . import record_apify_run_cost

            record_apify_run_cost(run, platform="youtube", actor_id=self.apify_actor_id, operation="start_apify_run")
            items = list(client.dataset(run.get("defaultDatasetId")).iterate_items())
            return {
                "provider": "youtube",
                "provider_status": "ok",
                "sync_status": "synced",
                "items": [item for item in items if isinstance(item, dict)],
                "raw": {"actor_id": self.apify_actor_id, "input": input_payload},
            }
        except ImportError:
            return {
                "provider": "youtube",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": "apify-client not installed",
                "raw": {"actor_id": self.apify_actor_id},
            }
        except Exception as exc:  # pragma: no cover - exercised only with live Apify.
            return {
                "provider": "youtube",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": str(exc),
                "raw": {"actor_id": self.apify_actor_id, "input": input_payload},
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
        if not self.api_key:
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

    def crawl_channel_profile(self, handle_or_url: str, *, channel_id: str = "", max_posts: int = 1, since: str | None = None) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_profile")
        channel_ref = {"kind": "channel_id", "value": channel_id} if channel_id else self.normalize_channel_ref(handle_or_url)
        if not self.api_key:
            return self._crawl_channel_profile_apify(
                handle_or_url,
                channel_ref=channel_ref,
                max_results=max_posts,
                fallback_from="youtube_api_not_configured",
            )
        params: dict[str, Any] = {"part": "snippet,statistics,brandingSettings,contentDetails"}
        if channel_ref["kind"] == "channel_id":
            params["id"] = channel_ref["value"]
        elif channel_ref["kind"] == "handle":
            params["forHandle"] = channel_ref["value"]
        else:
            search = self.search_channel_by_name(channel_ref["value"], max_results=1)
            if self._should_use_apify_fallback(search):
                return self._crawl_channel_profile_apify(
                    handle_or_url,
                    channel_ref=channel_ref,
                    max_results=max_posts,
                    fallback_from="youtube_api",
                    reason_payload=search,
                )
            items = search.get("items") or []
            found_id = (((items[0] or {}).get("id") or {}).get("channelId") if items else "") or ""
            if not found_id:
                return {"provider": "youtube", "provider_status": "no_results", "sync_status": "no_results", "items": [], "raw": search}
            params["id"] = found_id
        payload = self._request("channels", params)
        if self._should_use_apify_fallback(payload):
            return self._crawl_channel_profile_apify(
                handle_or_url,
                channel_ref=channel_ref,
                max_results=max_posts,
                fallback_from="youtube_api",
                reason_payload=payload,
            )
        payload["query"] = channel_ref
        return payload

    def crawl_channel_videos(self, channel_id: str, *, max_results: int = 25, since: str | None = None) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_videos")
        target = self._max_channel_videos(int(max_results or 25))
        published_after = _since_to_rfc3339(since)
        if not self.api_key:
            return self._crawl_channel_videos_apify(channel_id, max_results=target, fallback_from="youtube_api_not_configured", since=published_after)
        profile = self._request("channels", {"part": "contentDetails", "id": channel_id})
        if self._should_use_apify_fallback(profile):
            return self._crawl_channel_videos_apify(channel_id, max_results=target, fallback_from="youtube_api", reason_payload=profile, since=published_after)
        upload_playlist_id = self._upload_playlist_id_from_payload(profile)
        # since 下推:playlistItems 不支持 publishedAfter,只 search 端点支持 → 有 since 强制走 search 拿真增量。
        if upload_playlist_id and not published_after:
            result = self._crawl_upload_playlist(upload_playlist_id, target)
            if self._should_use_apify_fallback(result):
                return self._crawl_channel_videos_apify(channel_id, max_results=target, fallback_from="youtube_api", reason_payload=result, since=published_after)
            return result
        result = self._crawl_channel_videos_by_search(channel_id, target, published_after=published_after)
        if self._should_use_apify_fallback(result):
            return self._crawl_channel_videos_apify(channel_id, max_results=target, fallback_from="youtube_api", reason_payload=result, since=published_after)
        return result

    def _upload_playlist_id(self, channel_id: str) -> str:
        profile = self._request("channels", {"part": "contentDetails", "id": channel_id})
        return self._upload_playlist_id_from_payload(profile)

    @staticmethod
    def _upload_playlist_id_from_payload(profile: dict[str, Any]) -> str:
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
                    "error_reason": page.get("error_reason"),
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

    def _crawl_channel_videos_by_search(self, channel_id: str, target: int, *, published_after: str = "") -> dict[str, Any]:
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
            if videos.get("provider_status") != "ok":
                return {
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
        return {
            "provider": "youtube",
            "provider_status": "ok",
            "sync_status": "synced",
            "items": video_items,
            "search_raw": raw,
        }

    def _crawl_channel_profile_apify(
        self,
        handle_or_url: str,
        *,
        channel_ref: dict[str, str],
        max_results: int,
        fallback_from: str,
        reason_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.apify_token:
            return self._apify_not_configured("crawl_channel_profile", reason_payload)
        run_input = self._apify_channel_input(handle_or_url, channel_ref, max_results=max_results)
        result = self._start_apify_run(run_input, operation="crawl_channel_profile")
        if result.get("provider_status") != "ok":
            result["fallback_from"] = fallback_from
            result["youtube_api"] = reason_payload or {}
            return result
        actor_items = [item for item in result.get("items") or [] if isinstance(item, dict)]
        videos = [self._normalize_apify_video_item(item) for item in actor_items]
        profile = self._profile_from_apify_items(actor_items, channel_ref)
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
            "raw": {
                **(result.get("raw") or {}),
                "fallback_reason": reason_payload or {},
                "actor_item_count": len(actor_items),
            },
        }

    def _crawl_channel_videos_apify(
        self,
        channel_id_or_handle: str,
        *,
        max_results: int,
        fallback_from: str,
        reason_payload: dict[str, Any] | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        if not self.apify_token:
            return self._apify_not_configured("crawl_channel_videos", reason_payload)
        ref = self.normalize_channel_ref(channel_id_or_handle)
        if ref["kind"] == "query" and str(channel_id_or_handle or "").startswith("UC"):
            ref = {"kind": "channel_id", "value": str(channel_id_or_handle or "").strip()}
        run_input = self._apify_channel_input(channel_id_or_handle, ref, max_results=max_results, since=since)
        result = self._start_apify_run(run_input, operation="crawl_channel_videos")
        if result.get("provider_status") != "ok":
            result["fallback_from"] = fallback_from
            result["youtube_api"] = reason_payload or {}
            return result
        actor_items = [item for item in result.get("items") or [] if isinstance(item, dict)]
        videos = [self._normalize_apify_video_item(item) for item in actor_items]
        status = "synced" if videos else "no_results"
        return {
            "provider": "youtube",
            "provider_source": "apify",
            "provider_status": "ok" if videos else "no_results",
            "sync_status": status,
            "fallback_from": fallback_from,
            "items": videos,
            "raw": {
                **(result.get("raw") or {}),
                "fallback_reason": reason_payload or {},
                "actor_item_count": len(actor_items),
            },
        }

    def _apify_channel_input(self, handle_or_url: str, channel_ref: dict[str, str], *, max_results: int, since: str | None = None) -> dict[str, Any]:
        limit = max(1, min(self._max_channel_videos(max_results or 1), 50))
        channel_url = self._channel_videos_url(handle_or_url, channel_ref)
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

    def _channel_videos_url(self, handle_or_url: str, channel_ref: dict[str, str]) -> str:
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
        if kind in {"handle", "query"} and re.match(r"^[A-Za-z0-9_.-]+$", value):
            return f"https://www.youtube.com/@{value}/videos"
        return ""

    def _profile_from_apify_items(self, items: list[dict[str, Any]], channel_ref: dict[str, str]) -> dict[str, Any]:
        first = items[0] if items else {}
        channel_url = str(first.get("channelUrl") or first.get("channel_url") or "").strip()
        channel_id = self._extract_channel_id(channel_url)
        handle = str(channel_ref.get("value") or "").strip().lstrip("@")
        title = str(first.get("channelName") or first.get("channelTitle") or handle or "").strip()
        avatar_url = str(
            first.get("channelAvatarUrl")
            or first.get("channelAvatar")
            or first.get("channelThumbnailUrl")
            or first.get("channelThumbnail")
            or ""
        ).strip()
        subscriber_count = self._int(first.get("numberOfSubscribers") or first.get("subscriberCount") or first.get("subscribers"))
        total_views = sum(self._int(item.get("viewCount") or item.get("views")) or 0 for item in items) if items else None
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

    def _normalize_apify_video_item(self, item: dict[str, Any]) -> dict[str, Any]:
        video_url = str(item.get("url") or item.get("videoUrl") or "").strip()
        video_id = self._extract_video_id(video_url) or str(item.get("id") or item.get("videoId") or "").strip()
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
                "channelId": self._extract_channel_id(str(item.get("channelUrl") or "")),
                "thumbnails": {"high": {"url": thumbnail_url}, "default": {"url": thumbnail_url}} if thumbnail_url else {},
            },
            "statistics": {
                "viewCount": self._int(item.get("viewCount") or item.get("views")),
                "likeCount": self._int(item.get("likes") or item.get("likeCount")),
                "commentCount": self._int(item.get("commentsCount") or item.get("comments") or item.get("commentCount")),
            },
            "contentDetails": {"duration": item.get("duration") or ""},
            "provider_source": "apify",
        }

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(float(str(value).replace(",", "")))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_channel_id(url_or_id: str) -> str:
        raw = str(url_or_id or "").strip()
        if raw.startswith("UC") and "/" not in raw:
            return raw
        parsed = urllib.parse.urlparse(raw if "://" in raw else "")
        path = parsed.path.strip("/") if parsed.netloc else raw.strip("/")
        if path.lower().startswith("channel/"):
            return path.split("/", 1)[1].split("/", 1)[0]
        return ""

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
