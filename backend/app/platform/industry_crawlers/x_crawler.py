"""backend/app/services/vkpi/industry_crawlers/x_crawler.py

R-Phase2-B: X (Twitter) 抓取适配器

策略 (双重 fallback):
  1. 优先用官方 X API v2 (BEARER token)
  2. 没 token 时降级到 Apify scraper

Apify Actor:
  - apidojo/twitter-scraper-lite

环境变量:
  X_BEARER_TOKEN: X API v2 Bearer Token (优先)
  APIFY_TOKEN: Apify token (备用)
  APIFY_X_ACTOR_ID: 默认 apidojo/twitter-scraper-lite

注意:
  - X 官方 API 免费层 100 reads/月,基本不够用
  - 推荐用 Apify (~$0.02/账号,$30/月够 1500 账号)
  - rate limit 严重,失败要重试
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

from app.platform.apify_budget import ApifyBudgetBlocked, call_apify_actor
from app.platform.apify_lifecycle import managed_apify_client


X_API_BASE = "https://api.twitter.com/2"
DEFAULT_ACTOR_ID = "apidojo~twitter-scraper-lite"
DEFAULT_MAX_X_RESULTS = 5000


def _max_x_results() -> int:
    try:
        return max(1, min(10000, int(os.environ.get("VKPI_X_MAX_RESULTS") or DEFAULT_MAX_X_RESULTS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_X_RESULTS


class XCrawler:
    """X (Twitter) via X API + Apify fallback."""

    def __init__(
        self,
        bearer_token: str | None = None,
        api_token: str | None = None,
        *,
        actor_id: str | None = None,
        timeout_seconds: int = 30,
        run_timeout_seconds: int = 180,
    ) -> None:
        self.bearer_token = (bearer_token or os.environ.get("X_BEARER_TOKEN") or "").strip()
        self.api_token = (api_token or os.environ.get("APIFY_TOKEN") or "").strip()
        self.actor_id = (actor_id or os.environ.get("APIFY_X_ACTOR_ID") or DEFAULT_ACTOR_ID).strip()
        self.comments_actor_id = (os.environ.get("APIFY_X_COMMENTS_ACTOR_ID") or "").strip()
        self.timeout_seconds = max(3, min(60, int(timeout_seconds or 30)))
        self.run_timeout_seconds = max(60, min(600, int(run_timeout_seconds or 180)))

    @property
    def configured(self) -> bool:
        # 任一可用即视为 configured
        return bool(self.bearer_token or self.api_token)

    @property
    def use_official_api(self) -> bool:
        return bool(self.bearer_token)

    def provider_status(self) -> dict[str, Any]:
        mode = "official_api" if self.use_official_api else ("apify" if self.api_token else "not_configured")
        return {
            "provider": "x",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "mode": mode,
            "actor_id": self.actor_id if mode == "apify" else "",
            "comments_actor_id": self.comments_actor_id,
            "key_visible": False,
        }

    def _not_configured(self, operation: str) -> dict[str, Any]:
        return {
            "provider": "x",
            "operation": operation,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "raw": {},
            "message": "X_BEARER_TOKEN 和 APIFY_TOKEN 都未配置,X 抓取未执行。",
        }

    @staticmethod
    def normalize_handle_ref(value: str) -> dict[str, str]:
        raw = str(value or "").strip()
        if not raw:
            return {"kind": "empty", "value": ""}
        
        # URL: https://x.com/{handle} 或 https://twitter.com/{handle}
        if "://" in raw:
            match = re.search(r"(?:x\.com|twitter\.com)/([a-zA-Z0-9_]+)", raw)
            if match:
                return {"kind": "handle", "value": match.group(1)}
        
        if raw.startswith("@"):
            return {"kind": "handle", "value": raw[1:]}
        
        if re.match(r"^[a-zA-Z0-9_]{1,15}$", raw):
            return {"kind": "handle", "value": raw}
        
        return {"kind": "query", "value": raw}

    # ─── X API v2 (官方,优先) ────────────────────

    def _x_api_request(self, endpoint: str, params: dict | None = None) -> dict[str, Any]:
        if not self.bearer_token:
            return {}
        
        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in (params or {}).items())
        url = f"{X_API_BASE}{endpoint}{'?' + query if query else ''}"
        
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self.bearer_token}",
                    "Accept": "application/json",
                    "User-Agent": "ViltroxMarketing/1.0",
                },
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                body = response.read().decode("utf-8")
            return json.loads(body or "{}")
        except Exception as exc:  # pragma: no cover
            return {"error": str(exc)}

    # ─── Apify (备用) ──────────────────────────

    def _apify_run(self, input_payload: dict[str, Any], *, actor_id: str | None = None) -> dict[str, Any]:
        if not self.api_token:
            return self._not_configured("apify_run")

        try:
            from apify_client import ApifyClient  # type: ignore

            resolved_actor_id = (actor_id or self.actor_id).replace("/", "~")
            with managed_apify_client(ApifyClient(self.api_token)) as client:
                run = call_apify_actor(
                    client,
                    resolved_actor_id,
                    platform="x",
                    operation="apify_run",
                    source="industry_crawlers",
                    run_input=input_payload,
                    timeout_secs=self.run_timeout_seconds,
                    wait_secs=self.run_timeout_seconds,
                )
                if not run or str(run.get("status") or "").upper() != "SUCCEEDED":
                    return {
                        "provider": "x", "provider_status": "error", "sync_status": "error",
                        "items": [], "error": f"X actor did not finish: {str((run or {}).get('status') or 'unknown')}",
                        "raw": {"actor_id": resolved_actor_id, "input": input_payload},
                    }
                from . import record_apify_run_cost

                record_apify_run_cost(run, platform="x", actor_id=resolved_actor_id, operation="apify_run")
                items = list(client.dataset(run.get("defaultDatasetId")).iterate_items())
                return {
                    "provider": "x", "provider_status": "ok", "sync_status": "synced",
                    "items": items,
                    "raw": {"actor_id": resolved_actor_id, "input": input_payload},
                }
        except ApifyBudgetBlocked as exc:
            return {**exc.payload(), "items": [], "provider": "x"}
        except ImportError:
            return {
                "provider": "x", "provider_status": "error", "sync_status": "error",
                "items": [], "error": "apify-client not installed",
            }
        except Exception as exc:  # pragma: no cover
            return {
                "provider": "x", "provider_status": "error", "sync_status": "error",
                "items": [], "error": str(exc),
            }

    # ─── 公开接口 ─────────────────────────────────

    @staticmethod
    def _tweet_id(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if re.fullmatch(r"\d{8,25}", raw):
            return raw
        match = re.search(r"/status(?:es)?/(\d{8,25})", raw)
        if match:
            return match.group(1)
        return ""

    @classmethod
    def _tweet_url(cls, value: str) -> str:
        raw = str(value or "").strip()
        if raw.startswith(("http://", "https://")):
            return raw
        tweet_id = cls._tweet_id(raw)
        return f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""

    def _x_api_replies(self, tweet_ref: str, *, max_results: int) -> dict[str, Any]:
        tweet_id = self._tweet_id(tweet_ref)
        if not tweet_id:
            return {
                "provider": "x",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": "could not extract tweet id for X replies",
            }
        target = max(1, min(300, int(max_results or 50)))
        items: list[dict[str, Any]] = []
        next_token = ""
        while len(items) < target:
            params: dict[str, Any] = {
                "query": f"conversation_id:{tweet_id} -is:retweet -is:quote",
                "max_results": max(10, min(100, target - len(items))),
                "tweet.fields": "author_id,conversation_id,created_at,public_metrics,referenced_tweets,in_reply_to_user_id",
                "expansions": "author_id",
                "user.fields": "id,name,username,profile_image_url,verified,public_metrics",
            }
            if next_token:
                params["next_token"] = next_token
            payload = self._x_api_request("/tweets/search/recent", params)
            if payload.get("error"):
                return {
                    "provider": "x",
                    "provider_status": "error",
                    "sync_status": "error",
                    "items": items,
                    "error": str(payload.get("error"))[:500],
                    "raw": payload,
                }
            users = {
                str(user.get("id")): user
                for user in ((payload.get("includes") or {}).get("users") or [])
                if isinstance(user, dict)
            }
            for tweet in payload.get("data") or []:
                if not isinstance(tweet, dict) or str(tweet.get("id")) == tweet_id:
                    continue
                author = users.get(str(tweet.get("author_id"))) or {}
                items.append({**tweet, "author": author, "parent_id": tweet_id, "depth": 0})
                if len(items) >= target:
                    break
            next_token = str(((payload.get("meta") or {}).get("next_token")) or "")
            if not next_token:
                break
        return {
            "provider": "x",
            "provider_status": "ok" if items else "no_results",
            "sync_status": "synced" if items else "no_results",
            "items": items,
            "raw": {"tweet_id": tweet_id, "mode": "official_api"},
        }

    def _apify_replies(self, tweet_ref: str, *, max_results: int) -> dict[str, Any]:
        if not self.api_token or not self.comments_actor_id:
            return {
                "provider": "x",
                "provider_status": "not_configured",
                "sync_status": "not_configured",
                "items": [],
                "message": "X replies 需要 X_BEARER_TOKEN，或显式配置 APIFY_X_COMMENTS_ACTOR_ID。",
            }
        tweet_url = self._tweet_url(tweet_ref)
        if not tweet_url:
            return {
                "provider": "x",
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "error": "could not construct X post URL",
            }
        payload = {
            "startUrls": [{"url": tweet_url}],
            "tweetUrls": [tweet_url],
            "maxItems": max(1, min(300, int(max_results or 50))),
        }
        return self._apify_run(payload, actor_id=self.comments_actor_id)

    def crawl_channel_profile(self, handle_or_url: str, *, channel_id: str = "", max_posts: int = 12) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_channel_profile")
        
        ref = self.normalize_handle_ref(handle_or_url)
        if ref["kind"] == "empty":
            return {"provider": "x", "provider_status": "no_results", "sync_status": "no_results", "items": [], "message": "handle 为空"}
        
        # 优先官方 API
        if self.use_official_api and ref["kind"] == "handle":
            result = self._x_api_request(
                f"/users/by/username/{ref['value']}",
                {"user.fields": "public_metrics,description,verified,profile_image_url"},
            )
            if result.get("data"):
                return {
                    "provider": "x", "provider_status": "ok", "sync_status": "synced",
                    "items": [result["data"]], "query": ref, "mode": "official_api",
                }
        
        # 降级到 Apify
        if self.api_token:
            input_payload = {
                "twitterHandles": [ref["value"]] if ref["kind"] == "handle" else [],
                "maxItems": max(1, min(_max_x_results(), int(max_posts or 12))),
            }
            result = self._apify_run(input_payload)
            result["query"] = ref
            result["mode"] = "apify"
            return result
        
        return self._not_configured("crawl_channel_profile")

    def crawl_channel_videos(self, channel_id: str, *, max_results: int = 25) -> dict[str, Any]:
        return self.crawl_channel_profile(channel_id, max_posts=max_results)

    def crawl_video_comments(self, video_id: str, *, max_results: int = 50) -> dict[str, Any]:
        if not self.configured:
            return self._not_configured("crawl_video_comments")
        if self.bearer_token:
            return self._x_api_replies(video_id, max_results=max_results)
        return self._apify_replies(video_id, max_results=max_results)
