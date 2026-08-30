"""
backend/app/services/vkpi/industry_crawlers/reddit_crawler.py

Reddit crawler for V-KPI marketing dashboard.

Strategy:
  - Primary: PRAW (official Python Reddit API Wrapper)
    - Free tier: 60 req/min
    - OAuth2 client_credentials
    - Stable schema
  
  - Fallback: Apify trudax/reddit-scraper-lite
    - $0.01/subreddit
    - No OAuth needed
    - Used when PRAW not configured or fails

Modes:
  1. crawl_subreddit(name, limit=25) - subreddit posts
  2. crawl_brand_mentions(query, limit=50) - search mentions
  3. crawl_post_comments(post_id, max_depth=3) - nested comments
  4. crawl_channel_profile(handle, ...) - unified V-KPI interface
  5. crawl_channel_videos(handle, ...) - V-KPI compat (returns subreddit posts)

Environment:
  REDDIT_CLIENT_ID         - PRAW OAuth client ID (required for PRAW path)
  REDDIT_CLIENT_SECRET     - PRAW OAuth client secret
  REDDIT_USER_AGENT        - Required by Reddit, must be unique
  APIFY_TOKEN              - Apify token (for fallback)
  APIFY_REDDIT_ACTOR_ID    - Apify actor (default: trudax~reddit-scraper-lite)
"""

from __future__ import annotations

import os
from typing import Any

from app.platform.apify_budget import ApifyBudgetBlocked, call_apify_actor
from app.platform.apify_lifecycle import close_apify_client
from app.platform.industry_crawlers.reddit_json_path import RedditJsonPathMixin

# W4 class-LOC 拆刀:PRAW 段(client 懒初始化/抓取/评论扁平化/Submission 转 dict)
# 逐字搬进兄弟文件 reddit_praw_path.py 的 mixin;可选 praw import 与
# _PRAW_AVAILABLE 一并随段落走,这里只回引可用性标志。
from app.platform.industry_crawlers.reddit_praw_path import (
    _PRAW_AVAILABLE,
    RedditPrawPathMixin,
)

DEFAULT_USER_AGENT = "VKPIMarketing/1.0 (by /u/vkpi_default)"
DEFAULT_APIFY_REDDIT_ACTOR = "trudax~reddit-scraper-lite"


def _env_enabled(name: str, *, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


class RedditCrawler(RedditPrawPathMixin, RedditJsonPathMixin):
    """V-KPI compatible Reddit crawler with PRAW + Apify fallback."""

    PLATFORM = "reddit"

    def __init__(self) -> None:
        self.client_id = os.environ.get("REDDIT_CLIENT_ID", "")
        self.client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        self.user_agent = os.environ.get("REDDIT_USER_AGENT") or DEFAULT_USER_AGENT
        self.apify_token = os.environ.get("APIFY_TOKEN", "")
        self.apify_actor = (
            os.environ.get("APIFY_REDDIT_ACTOR_ID") or DEFAULT_APIFY_REDDIT_ACTOR
        ).replace("/", "~")  # Apify uses ~ for namespace
        self.run_timeout_seconds = max(60, min(900, int(os.environ.get("APIFY_REDDIT_RUN_TIMEOUT_SECONDS") or 240)))
        self.public_json_enabled = _env_enabled("VKPI_REDDIT_PUBLIC_JSON_ENABLED", default=True)

        self._praw_client = None  # lazy init

    @property
    def configured(self) -> bool:
        """True if PRAW, Apify, or Reddit's public JSON listing is usable."""
        praw_ok = bool(self.client_id and self.client_secret) and _PRAW_AVAILABLE
        apify_ok = bool(self.apify_token)
        return praw_ok or apify_ok or self.public_json_enabled

    @property
    def primary_path(self) -> str:
        """Which path will be used: 'praw' / 'json' / 'apify' / 'none'."""
        if self.client_id and self.client_secret and _PRAW_AVAILABLE:
            return "praw"
        if self.public_json_enabled:
            return "json"
        if self.apify_token:
            return "apify"
        return "none"

    def provider_status(self) -> dict[str, Any]:
        """Return provider readiness without exposing tokens."""
        return {
            "provider": "reddit",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "primary_path": self.primary_path,
            "praw_available": _PRAW_AVAILABLE,
            "public_json_enabled": self.public_json_enabled,
            "json_listing": self.public_json_enabled,
            "apify_configured": bool(self.apify_token),
            "apify_actor": self.apify_actor,
            "key_visible": False,
        }

    # ─── PRAW Path ──────────────────────────────────────────────
    # 逐字搬至 reddit_praw_path.RedditPrawPathMixin(_get_praw_client /
    # _crawl_subreddit_via_praw / _crawl_brand_mentions_via_praw /
    # _crawl_post_comments_via_praw / _flatten_comments / _praw_post_to_dict)。

    # ─── Apify Fallback Path ────────────────────────────────────

    def _crawl_post_comments_via_apify(
        self,
        post_id: str,
        *,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Apify fallback for post comments when Reddit public JSON is blocked."""
        if not self.apify_token:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
                "provider": "apify",
                "error": "APIFY_TOKEN not configured",
            }
        try:
            from apify_client import ApifyClient  # type: ignore
        except ImportError:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "apify",
                "error": "apify-client not installed",
            }

        raw_ref = str(post_id or "").strip()
        clean_post_id = self._normalize_post_id(raw_ref).replace("t3_", "").strip()
        post_url = raw_ref if raw_ref.startswith(("http://", "https://")) else f"https://www.reddit.com/comments/{clean_post_id}/"
        limit = max(1, min(300, int(max_results or 100)))
        client: Any | None = None
        try:
            client = ApifyClient(self.apify_token)
            run = call_apify_actor(
                client,
                self.apify_actor,
                platform="reddit",
                operation="crawl_post_comments",
                source="industry_crawlers",
                run_input={
                    "startUrls": [{"url": post_url}],
                    "skipComments": False,
                    "maxComments": limit,
                    "maxItems": limit + 1,
                    "proxy": {
                        "useApifyProxy": True,
                        "apifyProxyGroups": ["RESIDENTIAL"],
                    },
                },
                timeout_secs=self.run_timeout_seconds,
                wait_secs=self.run_timeout_seconds,
            )
            if not run or str(run.get("status") or "").upper() != "SUCCEEDED":
                return {
                    "items": [],
                    "provider_status": "error",
                    "sync_status": "fail",
                    "provider": "apify",
                    "error": f"Reddit comment actor did not finish: {str((run or {}).get('status') or 'unknown')}",
                }
            from . import record_apify_run_cost

            record_apify_run_cost(run, platform="reddit", actor_id=self.apify_actor, operation="crawl_post_comments")
            dataset_id = run.get("defaultDatasetId")
            items = list(client.dataset(dataset_id).iterate_items()) if dataset_id else []
            comments = [
                item
                for item in items
                if isinstance(item, dict) and str(item.get("dataType") or "").lower() == "comment"
            ]
            return {
                "items": comments,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "apify",
                "post_id": clean_post_id,
                "raw": {"actor_id": self.apify_actor, "post_url": post_url},
            }
        except ApifyBudgetBlocked as exc:
            return {**exc.payload(), "items": [], "provider": "apify"}
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "apify",
                "post_id": clean_post_id,
                "error": str(exc)[:500],
            }
        finally:
            close_apify_client(client)

    def _crawl_subreddit_via_apify(
        self, subreddit: str, limit: int
    ) -> dict[str, Any]:
        """Apify fallback: trudax/reddit-scraper-lite."""
        if not self.apify_token:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
                "provider": "apify",
            }
        try:
            from apify_client import ApifyClient  # type: ignore
        except ImportError:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "apify",
                "error": "apify-client not installed",
            }

        client: Any | None = None
        try:
            client = ApifyClient(self.apify_token)
            run_input = {
                "startUrls": [
                    {"url": f"https://www.reddit.com/r/{subreddit}/"}
                ],
                "maxItems": limit,
                "type": "posts",
                "sort": "hot",
            }
            run = call_apify_actor(
                client,
                self.apify_actor,
                platform="reddit",
                operation="crawl_subreddit",
                source="industry_crawlers",
                run_input=run_input,
                timeout_secs=self.run_timeout_seconds,
                wait_secs=self.run_timeout_seconds,
            )
            if not run or str(run.get("status") or "").upper() != "SUCCEEDED":
                return {
                    "items": [],
                    "provider_status": "error",
                    "sync_status": "fail",
                    "provider": "apify",
                    "error": f"Reddit actor did not finish: {str((run or {}).get('status') or 'unknown')}",
                }
            from . import record_apify_run_cost

            record_apify_run_cost(run, platform="reddit", actor_id=self.apify_actor, operation="crawl_subreddit")
            dataset_id = run.get("defaultDatasetId")
            items = list(client.dataset(dataset_id).iterate_items())

            return {
                "items": items,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "apify",
            }
        except ApifyBudgetBlocked as exc:
            return {**exc.payload(), "items": [], "provider": "apify"}
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "apify",
                "error": str(exc)[:500],
            }
        finally:
            close_apify_client(client)

    # ─── Public API (V-KPI compatible) ──────────────────────────

    def crawl_subreddit(self, subreddit: str, *, limit: int = 25) -> dict[str, Any]:
        """Crawl a subreddit's profile + posts.
        
        Tries PRAW first, falls back to Apify if PRAW unavailable.
        """
        # Strip r/ prefix if user passes it
        subreddit = subreddit.lstrip("/").replace("r/", "").strip()
        if not subreddit:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "error": "empty subreddit name",
            }

        path = self.primary_path
        if path == "praw":
            result = self._crawl_subreddit_via_praw(subreddit, limit)
            if result.get("provider_status") == "ok":
                return result
            json_result = self._crawl_subreddit_via_json_api(subreddit, limit)
            if json_result.get("provider_status") == "ok":
                return json_result
            if self.apify_token:
                return self._crawl_subreddit_via_apify(subreddit, limit)
            return result
        elif path == "json":
            result = self._crawl_subreddit_via_json_api(subreddit, limit)
            if result.get("provider_status") == "ok" or not self.apify_token:
                return result
            return self._crawl_subreddit_via_apify(subreddit, limit)
        elif path == "apify":
            return self._crawl_subreddit_via_apify(subreddit, limit)
        else:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
                "error": "Neither PRAW nor Apify configured",
            }

    def crawl_brand_mentions(
        self, query: str, *, limit: int = 50
    ) -> dict[str, Any]:
        """Search Reddit for brand mentions."""
        if not query.strip():
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "error": "empty query",
            }
        if self.primary_path == "praw":
            return self._crawl_brand_mentions_via_praw(query, limit)
        # Apify fallback for search not implemented in this round
        # Team can add later
        return {
            "items": [],
            "provider_status": "not_configured",
            "sync_status": "skip",
            "error": "brand_mentions requires PRAW configuration",
        }

    def crawl_post_comments(
        self, post_id: str, *, max_depth: int = 3, max_results: int = 100
    ) -> dict[str, Any]:
        """Crawl nested comments of a post."""
        if not post_id.strip():
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "error": "empty post_id",
            }
        raw_ref = post_id.strip()
        # Strip prefix if any
        post_id = self._normalize_post_id(raw_ref).replace("t3_", "")
        if self.primary_path == "praw":
            result = self._crawl_post_comments_via_praw(post_id, max_depth)
            if result.get("provider_status") == "ok":
                return result
        if self.public_json_enabled:
            result = self._crawl_post_comments_via_json_api(post_id, max_depth)
            if result.get("provider_status") == "ok" or not self.apify_token:
                return result
        if self.apify_token:
            return self._crawl_post_comments_via_apify(raw_ref, max_results=max_results)
        return {
            "items": [],
            "provider_status": "not_configured",
            "sync_status": "skip",
            "error": "Reddit public JSON, PRAW, and Apify are not configured",
        }

    # ─── V-KPI Unified Interface ────────────────────────────────

    def crawl_channel_profile(
        self,
        handle_or_url: str,
        *,
        channel_id: str = "",
        max_posts: int = 12,
    ) -> dict[str, Any]:
        """V-KPI unified interface - subreddit treated as 'channel'."""
        # Extract subreddit name from URL or use as-is
        subreddit = self._normalize_subreddit_name(handle_or_url, channel_id)
        return self.crawl_subreddit(subreddit, limit=max_posts)

    def crawl_channel_videos(
        self,
        handle_or_url: str,
        *,
        channel_id: str = "",
        max_posts: int = 12,
    ) -> dict[str, Any]:
        """V-KPI unified interface - returns subreddit posts as 'videos'."""
        # Same as profile in Reddit context (no separate video stream)
        return self.crawl_channel_profile(
            handle_or_url, channel_id=channel_id, max_posts=max_posts
        )

    def crawl_video_comments(
        self,
        video_id_or_url: str,
        *,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """V-KPI unified interface - 'video' = post for Reddit."""
        return self.crawl_post_comments(video_id_or_url, max_depth=3, max_results=max_results)

    @staticmethod
    def _normalize_subreddit_name(handle: str, channel_id: str = "") -> str:
        """Extract subreddit name from URL/handle/id."""
        if channel_id:
            return channel_id.lstrip("/").replace("r/", "")
        if not handle:
            return ""
        if "reddit.com" in handle:
            # Extract from URL like https://reddit.com/r/cinematography/...
            parts = handle.split("/r/")
            if len(parts) > 1:
                return parts[1].split("/")[0]
        return handle.lstrip("/").replace("r/", "").strip()

    @staticmethod
    def _normalize_post_id(handle: str) -> str:
        """Extract post id from URL/handle."""
        if "reddit.com" in handle:
            # Extract from URL like https://reddit.com/r/.../comments/POST_ID/...
            parts = handle.split("/comments/")
            if len(parts) > 1:
                return parts[1].split("/")[0]
        return handle.replace("t3_", "").strip()
