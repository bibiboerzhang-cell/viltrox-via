"""
backend/app/services/vkpi/industry_crawlers/facebook_crawler.py

Facebook crawler for V-KPI marketing dashboard.

Strategy:
  - Primary (short-term): Apify
    - apify/facebook-pages-scraper for Page profiles
    - apify/facebook-posts-scraper for Page posts
    - Residential proxy required (Facebook blocks datacenter IPs)
    - $0.30 / 1000 posts
    - No Meta App Review needed
  
  - Long-term: Meta Graph API
    - Free but requires Meta Business App registration
    - Stable + official support
    - Reserved env hooks (META_GRAPH_ACCESS_TOKEN)
    - Migration path documented

Modes:
  1. crawl_page_profile(url, max_posts=10) - Page + recent posts
  2. crawl_brand_mentions(query, limit=50) - search (limited scope)
  3. crawl_channel_profile(handle_or_url, ...) - V-KPI unified
  4. crawl_channel_videos(handle_or_url, ...) - V-KPI compat
  5. crawl_video_comments(post_id, ...) - V-KPI unified

Environment:
  APIFY_TOKEN                       - Apify token (shared with bili/xhs)
  APIFY_FACEBOOK_PAGES_ACTOR_ID     - Default: apify~facebook-pages-scraper
  APIFY_FACEBOOK_POSTS_ACTOR_ID     - Default: apify~facebook-posts-scraper
  
  META_GRAPH_ACCESS_TOKEN           - Long-term (not used in P1.2)
  META_GRAPH_API_VERSION            - Default: v18.0
"""

from __future__ import annotations

import os
from typing import Any

from app.platform.apify_budget import ApifyBudgetBlocked, call_apify_actor
from app.platform.apify_lifecycle import close_apify_client


DEFAULT_PAGES_ACTOR = "apify~facebook-pages-scraper"
DEFAULT_POSTS_ACTOR = "apify~facebook-posts-scraper"


class FacebookCrawler:
    """V-KPI compatible Facebook crawler via Apify (short-term)."""

    PLATFORM = "facebook"

    def __init__(self) -> None:
        self.apify_token = os.environ.get("APIFY_TOKEN", "")
        self.pages_actor = (
            os.environ.get("APIFY_FACEBOOK_PAGES_ACTOR_ID") or DEFAULT_PAGES_ACTOR
        ).replace("/", "~")
        self.posts_actor = (
            os.environ.get("APIFY_FACEBOOK_POSTS_ACTOR_ID") or DEFAULT_POSTS_ACTOR
        ).replace("/", "~")

        # Long-term Meta Graph hook (P1.2 doesn't use)
        self.meta_token = os.environ.get("META_GRAPH_ACCESS_TOKEN", "")
        self.meta_version = os.environ.get("META_GRAPH_API_VERSION") or "v18.0"
        self.run_timeout_seconds = max(60, min(900, int(os.environ.get("APIFY_FACEBOOK_RUN_TIMEOUT_SECONDS") or 420)))

    @property
    def configured(self) -> bool:
        """True if Apify token available (Meta Graph long-term)."""
        return bool(self.apify_token) or bool(self.meta_token)

    @property
    def primary_path(self) -> str:
        """Which path will be used: 'apify' / 'meta_graph' / 'none'."""
        if self.apify_token:
            return "apify"
        if self.meta_token:
            return "meta_graph"
        return "none"

    def provider_status(self) -> dict[str, Any]:
        """Return provider readiness without exposing configured tokens."""
        path = self.primary_path
        return {
            "provider": "facebook",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "primary_path": path,
            "pages_actor": self.pages_actor,
            "posts_actor": self.posts_actor,
            "meta_graph_reserved": bool(self.meta_token),
            "key_visible": False,
        }

    # ─── Apify Path ─────────────────────────────────────────────

    def _crawl_page_profile_via_apify(
        self, page_url: str, max_posts: int
    ) -> dict[str, Any]:
        """Apify path: Page profile via facebook-pages-scraper."""
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

        # Step 1: Fetch Page profile
        client: Any | None = None
        try:
            client = ApifyClient(self.apify_token)
            
            # Pages actor input
            pages_input = {
                "startUrls": [{"url": page_url}],
                "maxItems": 1,
                # Residential proxy required for Facebook
                "proxyConfiguration": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"],
                },
            }
            run = call_apify_actor(
                client,
                self.pages_actor,
                platform="facebook",
                operation="crawl_page_pages_actor",
                source="industry_crawlers",
                run_input=pages_input,
                timeout_secs=self.run_timeout_seconds,
                wait_secs=self.run_timeout_seconds,
            )
            if not run or str(run.get("status") or "").upper() != "SUCCEEDED":
                return {
                    "items": [],
                    "provider_status": "error",
                    "sync_status": "fail",
                    "provider": "apify",
                    "error": f"Facebook page actor did not finish: {str((run or {}).get('status') or 'unknown')}",
                }
            from . import record_apify_run_cost

            record_apify_run_cost(run, platform="facebook", actor_id=self.pages_actor, operation="crawl_page_pages_actor")
            dataset_id = run.get("defaultDatasetId")
            page_items = list(client.dataset(dataset_id).iterate_items())

            if not page_items:
                return {
                    "items": [],
                    "provider_status": "ok",
                    "sync_status": "no_results",
                    "provider": "apify",
                    "error": "Page not found or restricted",
                }

            # Step 2: Fetch Page posts (separate actor)
            posts_input = {
                "startUrls": [{"url": page_url}],
                "resultsLimit": max_posts,
                "proxyConfiguration": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"],
                },
            }
            posts_run = call_apify_actor(
                client,
                self.posts_actor,
                platform="facebook",
                operation="crawl_page_posts_actor",
                source="industry_crawlers",
                run_input=posts_input,
                timeout_secs=self.run_timeout_seconds,
                wait_secs=self.run_timeout_seconds,
            )
            if not posts_run or str(posts_run.get("status") or "").upper() != "SUCCEEDED":
                return {
                    "items": page_items,
                    "provider_status": "error",
                    "sync_status": "fail",
                    "provider": "apify",
                    "error": f"Facebook posts actor did not finish: {str((posts_run or {}).get('status') or 'unknown')}",
                }
            from . import record_apify_run_cost

            record_apify_run_cost(posts_run, platform="facebook", actor_id=self.posts_actor, operation="crawl_page_posts_actor")
            posts_dataset_id = posts_run.get("defaultDatasetId")
            post_items = list(
                client.dataset(posts_dataset_id).iterate_items()
            )

            # Combine: profile + posts
            return {
                "items": page_items + post_items,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "apify",
                "page_count": len(page_items),
                "post_count": len(post_items),
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

    def _crawl_brand_mentions_via_apify(
        self, query: str, limit: int
    ) -> dict[str, Any]:
        """
        Apify path: brand mention search.
        
        Note: Facebook search via Apify is limited. Most actors don't support
        full-text search across all of Facebook (privacy/ToS). This method
        attempts a best-effort approach using a search-targeted actor if
        configured, otherwise returns 'not_supported'.
        """
        # Facebook search is not well-supported by public Apify actors
        # P1.2 returns optimistic placeholder - team can extend if needed
        return {
            "items": [],
            "provider_status": "not_supported",
            "sync_status": "skip",
            "provider": "apify",
            "error": (
                "Facebook brand mention search not supported in P1.2. "
                "Use Page profile monitoring instead. "
                "Team: consider Meta Graph API for proper search (long-term)."
            ),
            "query": query,
        }

    # ─── Meta Graph API (long-term, not used in P1.2) ───────────

    def _crawl_page_profile_via_meta_graph(
        self, page_url: str, max_posts: int
    ) -> dict[str, Any]:
        """
        Meta Graph API path - reserved for long-term migration.
        Not implemented in P1.2.
        """
        return {
            "items": [],
            "provider_status": "not_implemented",
            "sync_status": "skip",
            "provider": "meta_graph",
            "error": (
                "Meta Graph API not implemented in P1.2. "
                "Reserved for long-term migration after Viltrox Meta App Review."
            ),
        }

    # ─── Public API (V-KPI compatible) ──────────────────────────

    def crawl_page_profile(
        self, page_url: str, *, max_posts: int = 10
    ) -> dict[str, Any]:
        """Crawl Facebook Page profile + recent posts."""
        if not page_url.strip():
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "error": "empty page_url",
            }

        # Normalize URL
        page_url = self._normalize_page_url(page_url)

        path = self.primary_path
        if path == "apify":
            return self._crawl_page_profile_via_apify(page_url, max_posts)
        elif path == "meta_graph":
            return self._crawl_page_profile_via_meta_graph(page_url, max_posts)
        else:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
                "error": "Neither Apify nor Meta Graph configured",
            }

    def crawl_brand_mentions(
        self, query: str, *, limit: int = 50
    ) -> dict[str, Any]:
        """Search Facebook for brand mentions (limited scope)."""
        if not query.strip():
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "error": "empty query",
            }

        path = self.primary_path
        if path == "apify":
            return self._crawl_brand_mentions_via_apify(query, limit)
        else:
            return {
                "items": [],
                "provider_status": "not_supported",
                "sync_status": "skip",
                "error": "brand mention search not supported in P1.2",
            }

    # ─── V-KPI Unified Interface ────────────────────────────────

    def crawl_channel_profile(
        self,
        handle_or_url: str,
        *,
        channel_id: str = "",
        max_posts: int = 12,
    ) -> dict[str, Any]:
        """V-KPI unified interface."""
        page_url = self._handle_to_page_url(handle_or_url, channel_id)
        return self.crawl_page_profile(page_url, max_posts=max_posts)

    def crawl_channel_videos(
        self,
        handle_or_url: str,
        *,
        channel_id: str = "",
        max_posts: int = 12,
    ) -> dict[str, Any]:
        """V-KPI unified interface - returns Page posts."""
        return self.crawl_channel_profile(
            handle_or_url, channel_id=channel_id, max_posts=max_posts
        )

    def crawl_video_comments(
        self,
        video_id_or_url: str,
        *,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Facebook post comments via Apify comment actor."""
        if not self.apify_token:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
                "provider": "apify",
            }

        post_url = self._normalize_post_url(video_id_or_url)
        if not post_url:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "apify",
                "error": "could not construct Facebook post URL",
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

        actor_id = (os.environ.get("APIFY_FACEBOOK_COMMENTS_ACTOR_ID") or "apify~facebook-comments-scraper").replace("/", "~")
        client: Any | None = None
        try:
            client = ApifyClient(self.apify_token)
            run = call_apify_actor(
                client,
                actor_id,
                platform="facebook",
                operation="crawl_post_comments",
                source="industry_crawlers",
                run_input={
                    "startUrls": [{"url": post_url}],
                    "resultsLimit": max(1, min(100, int(max_results or 100))),
                    "proxyConfiguration": {
                        "useApifyProxy": True,
                        "apifyProxyGroups": ["RESIDENTIAL"],
                    },
                },
                timeout_secs=self.run_timeout_seconds,
                wait_secs=self.run_timeout_seconds,
            )
            from . import record_apify_run_cost

            record_apify_run_cost(run, platform="facebook", actor_id=actor_id, operation="crawl_post_comments")
            dataset_id = run.get("defaultDatasetId")
            items = list(client.dataset(dataset_id).iterate_items()) if dataset_id else []
            return {
                "items": items,
                "provider_status": "ok",
                "sync_status": "synced",
                "provider": "apify",
                "raw": {"actor_id": actor_id, "post_url": post_url},
            }
        except ApifyBudgetBlocked as exc:
            return {**exc.payload(), "items": [], "raw": {"actor_id": actor_id, "post_url": post_url}}
        except Exception as exc:  # pragma: no cover - live provider path
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "apify",
                "error": str(exc)[:500],
                "raw": {"actor_id": actor_id, "post_url": post_url},
            }
        finally:
            close_apify_client(client)

    # ─── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _normalize_page_url(url_or_handle: str) -> str:
        """Convert handle/page name/URL to canonical Facebook Page URL."""
        s = url_or_handle.strip().rstrip("/")
        if not s:
            return ""
        if s.startswith("https://") or s.startswith("http://"):
            return s
        if s.startswith("facebook.com/"):
            return f"https://www.{s}"
        if s.startswith("www.facebook.com/"):
            return f"https://{s}"
        # Assume it's a Page handle/name
        return f"https://www.facebook.com/{s.lstrip('@/')}"

    @staticmethod
    def _handle_to_page_url(handle: str, channel_id: str = "") -> str:
        """V-KPI handle → Facebook Page URL."""
        if channel_id and channel_id.strip():
            # Channel ID treated as Page handle
            return FacebookCrawler._normalize_page_url(channel_id)
        return FacebookCrawler._normalize_page_url(handle)

    @staticmethod
    def _normalize_post_url(url_or_id: str) -> str:
        """Normalize Facebook post URL/id for comment actor input."""
        raw = str(url_or_id or "").strip()
        if not raw:
            return ""
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        return f"https://www.facebook.com/posts/{raw.strip('/')}"
