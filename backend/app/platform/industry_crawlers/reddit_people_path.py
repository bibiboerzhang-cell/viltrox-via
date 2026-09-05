"""OAuth-only Reddit author discovery. Public community JSON is not a person API.

Contract: PRAW Redditor.id/name/submissions and Subreddit.search; see
https://praw.readthedocs.io/en/stable/code_overview/models/redditor.html
No karma, subreddit size, or inferred audience is reported as followers.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import time
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class RedditPeoplePathMixin:
    def crawl_person_profile(self, value: str, *, max_posts: int = 12) -> dict[str, Any]:
        from app.platform.industry_crawlers.reddit_people_normalize import person_handle, profile_payload
        username = person_handle("reddit", value)
        return profile_payload(self.crawl_user_profile(username, max_posts=max_posts))

    def people_provider_status(self) -> dict[str, Any]:
        from app.platform.industry_crawlers.reddit_praw_path import _PRAW_AVAILABLE

        configured = bool(
            self.client_id and self.client_secret and _PRAW_AVAILABLE
            and os.environ.get("REDDIT_USER_AGENT", "").strip()
        )
        return {
            "configured": configured, "provider": "reddit", "mode": "oauth_praw",
            "provider_status": "configured" if configured else "not_configured",
            "required_configuration": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT", "praw"],
            "public_json_person_discovery": False, "fallback_policy": "disabled",
        }

    @staticmethod
    def _people_failure(code: str, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "status": "partial" if records else ("not_configured" if code == "not_configured" else "failed"),
            "records": records or [], "metadata": {
                "provider_status": code, "error_code": code, "retry_safe": False,
                "provider_outcome_unknown": code == "provider_error",
                "has_more": False, "pagination_supported": False,
                "fallback_policy": "disabled",
            },
        }

    @staticmethod
    def _people_error_code(exc: Exception) -> str:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return {401: "authentication_required", 403: "permission_denied", 429: "rate_limited"}.get(status, "provider_error")

    @staticmethod
    def _person_record(submission: Any, author: Any) -> dict[str, Any] | None:
        # Deleted/banned accounts lack IDs. Never fall back to a display name.
        name, native_id = str(getattr(author, "name", "")), str(getattr(author, "id", ""))
        post_id = str(getattr(submission, "id", ""))
        if (not re.fullmatch(r"[A-Za-z0-9_-]{3,20}", name)
                or name.lower() in {"automoderator", "deleted"}
                or not re.fullmatch(r"[a-z0-9]+", native_id)
                or not re.fullmatch(r"[a-z0-9]+", post_id)
                or bool(getattr(author, "is_suspended", False))):
            return None
        user_subreddit = getattr(author, "subreddit", None)
        def field(key: str) -> Any:
            return user_subreddit.get(key) if isinstance(user_subreddit, dict) else getattr(user_subreddit, key, "")
        profile = {
            "platform": "reddit", "handle": name, "platform_user_id": f"t2_{native_id}",
            "account_id": f"t2_{native_id}", "profile_url": f"https://www.reddit.com/user/{name}/",
            "display_name": str(field("title") or name)[:240],
            "bio": str(field("public_description") or "")[:2000],
            "avatar_url": str(getattr(author, "icon_img", "") or ""),
            "followers": None, "follower_count": None, "followers_source": "unknown",
            "audience_market_distribution": None,
            "account_metrics": {"comment_karma": getattr(author, "comment_karma", None), "link_karma": getattr(author, "link_karma", None)},
        }
        created = getattr(submission, "created_utc", None)
        try:
            published = datetime.fromtimestamp(float(created), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError, OSError, OverflowError):
            published = ""
        def metric(key: str) -> int:
            try:
                return max(0, int(getattr(submission, key, 0) or 0))
            except (TypeError, ValueError):
                return 0
        post = {
            "content_id": f"t3_{post_id}", "author_id": f"t2_{native_id}", "channel": name,
            "title": str(getattr(submission, "title", "") or "")[:500],
            "description": str(getattr(submission, "selftext", "") or "")[:2000],
            "url": f"https://www.reddit.com/comments/{post_id}/", "published": published,
            "type": "post", "views": 0, "likes": metric("score"), "comments": metric("num_comments"),
            "shares": 0, "community": str(getattr(getattr(submission, "subreddit", None), "display_name", ""))[:80],
        }
        return {"profile": profile, "post": post}

    def search_people(self, query: str, *, max_results: int = 25, deadline_seconds: float = 30) -> dict[str, Any]:
        if not self.people_provider_status()["configured"]:
            return self._people_failure("not_configured")
        safe_limit = max(1, min(int(max_results or 25), 25))
        return self._people_listing(query, safe_limit, deadline_seconds=deadline_seconds)

    def crawl_user_profile(self, username: str, *, max_posts: int = 12, deadline_seconds: float = 30) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,20}", username):
            return self._people_failure("invalid_person_identity")
        if not self.people_provider_status()["configured"]:
            return self._people_failure("not_configured")
        return self._people_listing(username, max(1, min(int(max_posts or 12), 25)),
                                    deadline_seconds=deadline_seconds, profile_only=True)

    def _people_listing(self, value: str, limit: int, *, deadline_seconds: float, profile_only: bool = False) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        started = time.monotonic()
        client = None
        # One listing page and at most 25 bounded account lookups; no fan-out,
        # cross-provider fallback, comments expansion, or unbounded listings.
        try:
            client = self._get_praw_client()
            if client is None:
                return self._people_failure("not_configured")
            owner = client.redditor(value) if profile_only else None
            listing = owner.submissions.new(limit=limit) if owner else client.subreddit("all").search(value, sort="new", time_filter="month", limit=limit)
            skipped = 0
            for index, submission in enumerate(listing):
                if index >= limit:
                    break
                if time.monotonic() - started >= max(0.1, min(float(deadline_seconds), 60)):
                    return self._people_failure("deadline_exceeded", records)
                author = getattr(submission, "author", None)
                if author is None or (owner and str(getattr(author, "name", "")).lower() != value.lower()):
                    skipped += 1
                    continue
                record = self._person_record(submission, owner or author)
                if record:
                    records.append(record)
                else:
                    skipped += 1
            return {"status": "done" if records else ("partial" if skipped else "empty"), "records": records,
                    "metadata": {"provider_status": "succeeded", "provider_mode": "oauth_praw", "has_more": False,
                                 "pagination_supported": False, "rejected_identity_count": skipped,
                                 "bounded_listing_limit": limit, "fallback_policy": "disabled"}}
        except Exception as exc:
            return self._people_failure(self._people_error_code(exc), records)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.warning("reddit.people_cleanup_failed", extra={"error_type": type(exc).__name__})
            self._praw_client = None
