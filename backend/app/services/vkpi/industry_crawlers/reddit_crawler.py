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

import json
import os
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# PRAW import is optional - graceful degradation
try:
    import praw  # type: ignore
    _PRAW_AVAILABLE = True
except ImportError:
    _PRAW_AVAILABLE = False
    praw = None


DEFAULT_USER_AGENT = "VKPIMarketing/1.0 (by /u/vkpi_default)"
DEFAULT_APIFY_REDDIT_ACTOR = "trudax~reddit-scraper-lite"


class RedditCrawler:
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

        self._praw_client = None  # lazy init

    @property
    def configured(self) -> bool:
        """True if PRAW, Apify, or Reddit's public JSON listing is usable."""
        praw_ok = bool(self.client_id and self.client_secret) and _PRAW_AVAILABLE
        apify_ok = bool(self.apify_token)
        return praw_ok or apify_ok or True

    @property
    def primary_path(self) -> str:
        """Which path will be used: 'praw' / 'apify' / 'none'."""
        if self.client_id and self.client_secret and _PRAW_AVAILABLE:
            return "praw"
        return "json"

    def provider_status(self) -> dict[str, Any]:
        """Return provider readiness without exposing tokens."""
        return {
            "provider": "reddit",
            "configured": self.configured,
            "provider_status": "configured" if self.configured else "not_configured",
            "primary_path": self.primary_path,
            "praw_available": _PRAW_AVAILABLE,
            "json_listing": True,
            "apify_actor": self.apify_actor,
            "key_visible": False,
        }

    # ─── PRAW Path ──────────────────────────────────────────────

    def _get_praw_client(self):
        """Lazy-init PRAW Reddit client. Returns None if not available."""
        if not _PRAW_AVAILABLE:
            return None
        if not (self.client_id and self.client_secret):
            return None

        if self._praw_client is None:
            try:
                self._praw_client = praw.Reddit(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    user_agent=self.user_agent,
                    check_for_async=False,
                )
                # Read-only mode (no user OAuth)
                self._praw_client.read_only = True
            except Exception:
                self._praw_client = None

        return self._praw_client

    def _crawl_subreddit_via_praw(self, subreddit: str, limit: int) -> dict[str, Any]:
        """PRAW path: subreddit profile + posts."""
        client = self._get_praw_client()
        if client is None:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
                "provider": "praw",
                "error": "PRAW client unavailable",
            }

        try:
            sr = client.subreddit(subreddit)
            # Profile data
            profile_item = {
                "type": "subreddit_profile",
                "id": sr.id,
                "display_name": sr.display_name,
                "title": sr.title,
                "description": sr.public_description,
                "subscribers": sr.subscribers,  # ← maps to followers
                "accounts_active": sr.accounts_active,
                "created_utc": sr.created_utc,
                "over18": sr.over18,
                "url": f"https://reddit.com/r/{sr.display_name}",
            }
            # Posts (hot)
            posts = []
            for submission in sr.hot(limit=limit):
                posts.append(self._praw_post_to_dict(submission))

            return {
                "items": [profile_item] + posts,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "praw",
            }
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "praw",
                "error": str(exc)[:500],
            }

    def _crawl_brand_mentions_via_praw(
        self, query: str, limit: int
    ) -> dict[str, Any]:
        """PRAW path: search-based brand mention discovery."""
        client = self._get_praw_client()
        if client is None:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
                "provider": "praw",
            }

        try:
            results = client.subreddit("all").search(
                query, sort="new", time_filter="month", limit=limit
            )
            posts = [self._praw_post_to_dict(s) for s in results]
            return {
                "items": posts,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "praw",
                "query": query,
            }
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "praw",
                "error": str(exc)[:500],
            }

    def _crawl_post_comments_via_praw(
        self, post_id: str, max_depth: int = 3
    ) -> dict[str, Any]:
        """PRAW path: nested comments with depth limit."""
        client = self._get_praw_client()
        if client is None:
            return {
                "items": [],
                "provider_status": "not_configured",
                "sync_status": "skip",
            }

        try:
            submission = client.submission(id=post_id)
            submission.comments.replace_more(limit=0)  # ignore "more" placeholders

            comments = []
            self._flatten_comments(
                submission.comments,
                comments,
                depth=0,
                max_depth=max_depth,
                parent_id=None,
            )

            return {
                "items": comments,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "praw",
                "post_id": post_id,
            }
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "praw",
                "error": str(exc)[:500],
            }

    def _flatten_comments(
        self,
        comment_forest,
        out: list,
        *,
        depth: int,
        max_depth: int,
        parent_id: str | None,
    ) -> None:
        """Recursively flatten Reddit CommentForest with depth limit."""
        if depth > max_depth:
            return
        for comment in comment_forest:
            if not hasattr(comment, "id"):
                continue
            out.append(
                {
                    "id": comment.id,
                    "parent_id": parent_id,
                    "depth": depth,
                    "author": (
                        comment.author.name if comment.author else "[deleted]"
                    ),
                    "body": (comment.body or "")[:5000],  # cap text length
                    "score": comment.score,
                    "created_utc": comment.created_utc,
                    "is_submitter": comment.is_submitter,
                }
            )
            # Recurse
            if hasattr(comment, "replies") and depth < max_depth:
                self._flatten_comments(
                    comment.replies,
                    out,
                    depth=depth + 1,
                    max_depth=max_depth,
                    parent_id=comment.id,
                )

    def _praw_post_to_dict(self, submission) -> dict[str, Any]:
        """Convert PRAW Submission to V-KPI dict format."""
        return {
            "type": "post",
            "id": submission.id,
            "title": submission.title,
            "author": (submission.author.name if submission.author else "[deleted]"),
            "subreddit": submission.subreddit.display_name,
            "permalink": f"https://reddit.com{submission.permalink}",
            "url": submission.url,
            "selftext": (submission.selftext or "")[:5000],
            "score": submission.score,  # ← maps to likes
            "upvote_ratio": submission.upvote_ratio,
            "num_comments": submission.num_comments,  # ← maps to comments
            "created_utc": submission.created_utc,
            "is_video": submission.is_video,
            "is_self": submission.is_self,
            "over_18": submission.over_18,
            "stickied": submission.stickied,
        }

    # ─── Apify Fallback Path ────────────────────────────────────

    def _reddit_get_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _clean_media_url(value: Any) -> str:
        url = str(value or "").replace("&amp;", "&").strip()
        if url.startswith("//"):
            url = f"https:{url}"
        return url if url.startswith(("http://", "https://")) else ""

    def _push_media_url(self, urls: list[str], value: Any) -> None:
        url = self._clean_media_url(value)
        if url and url not in urls:
            urls.append(url)

    @staticmethod
    def _reddit_video_url(data: dict[str, Any]) -> str:
        for key in ("secure_media", "media"):
            media = data.get(key) if isinstance(data.get(key), dict) else {}
            video = media.get("reddit_video") if isinstance(media.get("reddit_video"), dict) else {}
            fallback = str(video.get("fallback_url") or "").replace("&amp;", "&").strip()
            if fallback.startswith(("http://", "https://")):
                return fallback
        return ""

    def _json_post_to_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        created = data.get("created_utc")
        created_at = ""
        if created is not None:
            try:
                created_at = datetime.utcfromtimestamp(float(created)).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                created_at = ""
        media_urls: list[str] = []
        preview = data.get("preview") if isinstance(data.get("preview"), dict) else {}
        images = preview.get("images") if isinstance(preview.get("images"), list) else []
        for image in images:
            source = image.get("source") if isinstance(image, dict) and isinstance(image.get("source"), dict) else {}
            self._push_media_url(media_urls, source.get("url"))
        gallery = data.get("gallery_data") if isinstance(data.get("gallery_data"), dict) else {}
        gallery_items = gallery.get("items") if isinstance(gallery.get("items"), list) else []
        media_metadata = data.get("media_metadata") if isinstance(data.get("media_metadata"), dict) else {}
        for gallery_item in gallery_items:
            media_id = gallery_item.get("media_id") if isinstance(gallery_item, dict) else ""
            metadata = media_metadata.get(str(media_id)) if isinstance(media_metadata.get(str(media_id)), dict) else {}
            source = metadata.get("s") if isinstance(metadata.get("s"), dict) else {}
            self._push_media_url(media_urls, source.get("u"))
        direct_url = data.get("url_overridden_by_dest") or data.get("url")
        if str(data.get("post_hint") or "").lower() == "image":
            self._push_media_url(media_urls, direct_url)
        video_url = self._reddit_video_url(data)
        return {
            "dataType": "post",
            "type": "post",
            "id": data.get("name") or f"t3_{data.get('id')}",
            "parsedId": data.get("id"),
            "name": data.get("name"),
            "title": data.get("title"),
            "body": data.get("selftext"),
            "author": data.get("author"),
            "username": data.get("author"),
            "subreddit": data.get("subreddit"),
            "communityName": data.get("subreddit"),
            "url": f"https://www.reddit.com{data.get('permalink')}" if data.get("permalink") else data.get("url"),
            "permalink": f"https://www.reddit.com{data.get('permalink')}" if data.get("permalink") else "",
            "externalUrl": data.get("url_overridden_by_dest") or data.get("url"),
            "thumbnailUrl": data.get("thumbnail") if str(data.get("thumbnail") or "").startswith("http") else (media_urls[0] if media_urls else ""),
            "images": media_urls,
            "videoUrl": video_url,
            "flair": data.get("link_flair_text"),
            "linkFlairText": data.get("link_flair_text"),
            "createdAt": created_at,
            "created_utc": created,
            "score": data.get("score"),
            "upVotes": data.get("ups"),
            "ups": data.get("ups"),
            "downs": data.get("downs"),
            "upvoteRatio": data.get("upvote_ratio"),
            "upvote_ratio": data.get("upvote_ratio"),
            "numberOfComments": data.get("num_comments"),
            "num_comments": data.get("num_comments"),
            "isVideo": data.get("is_video"),
            "isSelf": data.get("is_self"),
            "over18": data.get("over_18"),
            "pinned": data.get("stickied"),
            "stickied": data.get("stickied"),
            "locked": data.get("locked"),
            "removed": bool(data.get("removed_by_category")),
            "removedByCategory": data.get("removed_by_category"),
        }

    def _crawl_subreddit_via_json_api(self, subreddit: str, limit: int) -> dict[str, Any]:
        """Public Reddit listing path: newest subreddit posts via after pagination."""
        max_items = max(1, min(5000, int(limit or 100)))
        items: list[dict[str, Any]] = []
        after = ""
        try:
            about = self._reddit_get_json(f"https://www.reddit.com/r/{subreddit}/about.json")
            data = about.get("data") if isinstance(about.get("data"), dict) else {}
            items.append(
                {
                    "dataType": "community",
                    "type": "subreddit_profile",
                    "id": data.get("id"),
                    "display_name": data.get("display_name") or subreddit,
                    "title": data.get("title"),
                    "description": data.get("public_description"),
                    "subscribers": data.get("subscribers"),
                    "numberOfMembers": data.get("subscribers"),
                    "accounts_active": data.get("active_user_count"),
                    "createdAt": datetime.utcfromtimestamp(float(data.get("created_utc") or 0)).strftime("%Y-%m-%dT%H:%M:%SZ") if data.get("created_utc") else "",
                    "url": f"https://www.reddit.com/r/{data.get('display_name') or subreddit}/",
                    "iconUrl": data.get("community_icon") or data.get("icon_img"),
                    "bannerUrl": data.get("banner_background_image") or data.get("banner_img"),
                }
            )
            while len(items) - 1 < max_items:
                remaining = max_items - (len(items) - 1)
                params = {"limit": min(100, remaining), "raw_json": 1}
                if after:
                    params["after"] = after
                listing = self._reddit_get_json(f"https://www.reddit.com/r/{subreddit}/new.json?{urlencode(params)}")
                listing_data = listing.get("data") if isinstance(listing.get("data"), dict) else {}
                children = listing_data.get("children") if isinstance(listing_data.get("children"), list) else []
                if not children:
                    break
                for child in children:
                    if not isinstance(child, dict) or child.get("kind") != "t3":
                        continue
                    post_data = child.get("data") if isinstance(child.get("data"), dict) else {}
                    if post_data:
                        items.append(self._json_post_to_dict(post_data))
                after = str(listing_data.get("after") or "")
                if not after:
                    break
                time.sleep(0.25)
            return {
                "items": items,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "reddit_json",
            }
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "reddit_json",
                "error": str(exc)[:500],
            }

    def _json_comment_to_dict(
        self,
        data: dict[str, Any],
        *,
        depth: int,
        parent_id: str | None,
        post_id: str,
    ) -> dict[str, Any]:
        created = data.get("created_utc")
        created_at = ""
        if created is not None:
            try:
                created_at = datetime.utcfromtimestamp(float(created)).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                created_at = ""
        return {
            "id": data.get("id"),
            "parent_id": parent_id or data.get("parent_id"),
            "postId": post_id,
            "depth": depth,
            "author": data.get("author"),
            "body": data.get("body"),
            "score": data.get("score"),
            "ups": data.get("ups"),
            "created_utc": created,
            "created_at": created_at,
            "is_submitter": data.get("is_submitter"),
        }

    def _flatten_json_comments(
        self,
        children: list[Any],
        out: list[dict[str, Any]],
        *,
        depth: int,
        max_depth: int,
        parent_id: str | None,
        post_id: str,
    ) -> None:
        if depth > max_depth:
            return
        for child in children:
            if not isinstance(child, dict) or child.get("kind") != "t1":
                continue
            data = child.get("data") if isinstance(child.get("data"), dict) else {}
            comment_id = str(data.get("id") or "").strip()
            body = str(data.get("body") or "").strip()
            if not comment_id or not body:
                continue
            out.append(self._json_comment_to_dict(data, depth=depth, parent_id=parent_id, post_id=post_id))
            replies = data.get("replies")
            if depth >= max_depth or not isinstance(replies, dict):
                continue
            replies_data = replies.get("data") if isinstance(replies.get("data"), dict) else {}
            reply_children = replies_data.get("children") if isinstance(replies_data.get("children"), list) else []
            self._flatten_json_comments(
                reply_children,
                out,
                depth=depth + 1,
                max_depth=max_depth,
                parent_id=comment_id,
                post_id=post_id,
            )

    def _crawl_post_comments_via_json_api(
        self,
        post_id: str,
        max_depth: int = 3,
    ) -> dict[str, Any]:
        """Public Reddit JSON comments path used when PRAW is not configured."""
        clean_post_id = post_id.replace("t3_", "").strip()
        try:
            payload = self._reddit_get_json(
                f"https://www.reddit.com/comments/{clean_post_id}.json?limit=500&raw_json=1"
            )
            listing = payload[1] if isinstance(payload, list) and len(payload) > 1 else {}
            data = listing.get("data") if isinstance(listing, dict) and isinstance(listing.get("data"), dict) else {}
            children = data.get("children") if isinstance(data.get("children"), list) else []
            comments: list[dict[str, Any]] = []
            self._flatten_json_comments(
                children,
                comments,
                depth=0,
                max_depth=max(0, min(8, int(max_depth or 3))),
                parent_id=None,
                post_id=clean_post_id,
            )
            return {
                "items": comments,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "reddit_json",
                "post_id": clean_post_id,
            }
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "reddit_json",
                "post_id": clean_post_id,
                "error": str(exc)[:500],
            }

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

        clean_post_id = post_id.replace("t3_", "").strip()
        post_url = f"https://www.reddit.com/comments/{clean_post_id}/"
        limit = max(1, min(300, int(max_results or 100)))
        try:
            client = ApifyClient(self.apify_token)
            run = client.actor(self.apify_actor).call(
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
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "apify",
                "post_id": clean_post_id,
                "error": str(exc)[:500],
            }

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
            run = client.actor(self.apify_actor).call(
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
            dataset_id = run.get("defaultDatasetId")
            items = list(client.dataset(dataset_id).iterate_items())

            return {
                "items": items,
                "provider_status": "ok",
                "sync_status": "ok",
                "provider": "apify",
            }
        except Exception as exc:
            return {
                "items": [],
                "provider_status": "error",
                "sync_status": "fail",
                "provider": "apify",
                "error": str(exc)[:500],
            }

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
        # Strip prefix if any
        post_id = post_id.replace("t3_", "")
        if self.primary_path == "praw":
            result = self._crawl_post_comments_via_praw(post_id, max_depth)
            if result.get("provider_status") == "ok":
                return result
        result = self._crawl_post_comments_via_json_api(post_id, max_depth)
        if result.get("provider_status") == "ok" or not self.apify_token:
            return result
        return self._crawl_post_comments_via_apify(post_id, max_results=max_results)

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
        post_id = self._normalize_post_id(video_id_or_url)
        return self.crawl_post_comments(post_id, max_depth=3, max_results=max_results)

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
