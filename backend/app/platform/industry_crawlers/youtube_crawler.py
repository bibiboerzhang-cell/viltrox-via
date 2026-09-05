"""YouTube Data API adapter for industry account snapshots.

The crawler is intentionally gated: without a configured API key it returns a
not_configured status and no metric values. It uses stdlib HTTP to avoid adding
a heavy dependency while keeping the future YouTube KPI insertion point stable.

W4 class-LOC 拆刀:解析/分页/富化协作函数在兄弟文件
``youtube_crawler_support.py``;本类保薄门面 + 全部 patch 面
(``_request`` / ``_should_use_apify_fallback`` / ``_start_apify_run`` 被
strict_video 路径与测试 monkeypatch,逐字保留)。兄弟文件一律函数体内
lazy import(包 ``__init__`` 顶层 import 本模块,顶层反向 import 会进
import-time 环棘轮)。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.platform.apify_budget import ApifyBudgetBlocked, ApifyExecutionClaimBlocked, ApifyProviderReplayBlocked, call_apify_actor
from app.platform.apify_result_contract import ActorRunError, crawler_failure, read_actor_dataset
from app.platform.apify_lifecycle import managed_apify_client

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
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
        if payload.get("items") or payload.get("status") == "partial" or payload.get("provider_outcome_unknown"):
            return False
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

            actor_path = self.apify_actor_id.replace("/", "~")
            with managed_apify_client(ApifyClient(self.apify_token)) as client:
                run = call_apify_actor(
                    client,
                    actor_path,
                    platform="youtube",
                    operation=operation,
                    source="industry_crawlers",
                    run_input=input_payload,
                    timeout_secs=self.run_timeout_seconds,
                    wait_secs=self.run_timeout_seconds,
                )
                items = read_actor_dataset(client, run)
                from . import record_apify_run_cost

                record_apify_run_cost(run, platform="youtube", actor_id=self.apify_actor_id, operation="start_apify_run")
                return {
                    "status": "done" if items else "empty",
                    "provider": "youtube",
                    "provider_status": "ok",
                    "sync_status": "synced",
                    "items": [item for item in items if isinstance(item, dict)],
                    "raw": {"actor_id": self.apify_actor_id, "input": input_payload},
                }
        except ApifyBudgetBlocked as exc:
            return {**exc.payload(), "items": [], "provider": "youtube"}
        except (ApifyExecutionClaimBlocked, ApifyProviderReplayBlocked):
            raise
        except ActorRunError as exc:
            return crawler_failure(exc, "youtube")
        except ImportError:
            return {
                "provider": "youtube",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": "apify-client not installed",
                "raw": {"actor_id": self.apify_actor_id},
            }
        except Exception:
            return crawler_failure(ActorRunError("actor_provider_failed", provider_outcome_unknown=True), "youtube")

    @staticmethod
    def normalize_channel_ref(value: str) -> dict[str, str]:
        from .youtube_crawler_support import _normalize_channel_ref

        return _normalize_channel_ref(value)

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
        from .youtube_crawler_support import (
            _crawl_channel_videos_by_search,
            _crawl_upload_playlist,
            _max_channel_videos,
            _since_to_rfc3339,
            _upload_playlist_id_from_payload,
        )

        if not self.configured:
            return self._not_configured("crawl_channel_videos")
        target = _max_channel_videos(int(max_results or 25))
        published_after = _since_to_rfc3339(since)
        if str(since or "").strip() and not published_after:
            return {"status": "failed", "provider_status": "error", "sync_status": "error",
                    "error_code": "invalid_since", "items": []}
        if not self.api_key:
            return self._crawl_channel_videos_apify(channel_id, max_results=target, fallback_from="youtube_api_not_configured", since=published_after)
        profile = self._request("channels", {"part": "contentDetails", "id": channel_id})
        if self._should_use_apify_fallback(profile):
            return self._crawl_channel_videos_apify(channel_id, max_results=target, fallback_from="youtube_api", reason_payload=profile, since=published_after)
        if profile.get("provider_status") != "ok":
            return profile
        upload_playlist_id = _upload_playlist_id_from_payload(profile)
        # Known channels use uploads even with since; publication evidence is
        # filtered locally and an unfinished window is explicitly partial.
        if upload_playlist_id:
            result = _crawl_upload_playlist(self._request, upload_playlist_id, target, published_after=published_after)
            if self._should_use_apify_fallback(result):
                return self._crawl_channel_videos_apify(channel_id, max_results=target, fallback_from="youtube_api", reason_payload=result, since=published_after)
            return result
        result = _crawl_channel_videos_by_search(self._request, channel_id, target, published_after=published_after)
        if self._should_use_apify_fallback(result):
            return self._crawl_channel_videos_apify(channel_id, max_results=target, fallback_from="youtube_api", reason_payload=result, since=published_after)
        return result

    def _upload_playlist_id(self, channel_id: str) -> str:
        from .youtube_crawler_support import _upload_playlist_id_from_payload

        profile = self._request("channels", {"part": "contentDetails", "id": channel_id})
        return _upload_playlist_id_from_payload(profile)

    def _crawl_channel_profile_apify(
        self,
        handle_or_url: str,
        *,
        channel_ref: dict[str, str],
        max_results: int,
        fallback_from: str,
        reason_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .youtube_crawler_support import _apify_channel_input, _finish_profile_fallback

        if not self.apify_token:
            return self._apify_not_configured("crawl_channel_profile", reason_payload)
        run_input = _apify_channel_input(handle_or_url, channel_ref, max_results=max_results)
        result = self._start_apify_run(run_input, operation="crawl_channel_profile")
        return _finish_profile_fallback(result, channel_ref=channel_ref, fallback_from=fallback_from, reason_payload=reason_payload)

    def _crawl_channel_videos_apify(
        self,
        channel_id_or_handle: str,
        *,
        max_results: int,
        fallback_from: str,
        reason_payload: dict[str, Any] | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        from .youtube_crawler_support import _apify_channel_input, _finish_videos_fallback, _videos_channel_ref

        if not self.apify_token:
            return self._apify_not_configured("crawl_channel_videos", reason_payload)
        ref = _videos_channel_ref(channel_id_or_handle)
        run_input = _apify_channel_input(channel_id_or_handle, ref, max_results=max_results, since=since)
        result = self._start_apify_run(run_input, operation="crawl_channel_videos")
        return _finish_videos_fallback(result, fallback_from=fallback_from, reason_payload=reason_payload)

    def crawl_video_comments(self, video_id: str, *, max_results: int = 50) -> dict[str, Any]:
        from .youtube_crawler_support import _extract_video_id, _flatten_comment_threads

        if not self.configured:
            return self._not_configured("crawl_video_comments")
        video_id = _extract_video_id(video_id)
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
            "items": _flatten_comment_threads(self._request, threads.get("items") or [], limit),
            "raw": {"video_id": video_id, "thread_count": len(threads.get("items") or [])},
        }
