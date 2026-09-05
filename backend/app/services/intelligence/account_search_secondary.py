"""Strict secondary-platform discovery and account refresh; no hidden fallback."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.platform.industry_crawlers.reddit_crawler import RedditCrawler
from app.platform.industry_crawlers.x_crawler import XCrawler
from app.services.intelligence.account_scan_helpers import _build_scan_result
from app.services.intelligence.account_scan_outcome import ActorRunError
from app.services.intelligence.account_search_secondary_normalize import (
    author_candidates, person_handle, x_records,
)


def _metadata() -> dict[str, Any]:
    return {"has_more": False, "pagination_supported": False,
            "fallback_policy": "disabled", "content_kind": "post",
            "audience_inference": "disabled", "refresh_route": "account_scan_secondary"}


async def _records(platform: str, value: str, *, limit: int, operation: str,
                   deadline_seconds: float | None = None) -> dict[str, Any]:
    if deadline_seconds is not None and deadline_seconds <= 0:
        return {"status": "failed", "records": [], "metadata": {"error_code": "deadline_exceeded", "provider_calls": 0}}
    if platform == "reddit":
        crawler = RedditCrawler()
        call = crawler.search_people if operation == "search" else crawler.crawl_user_profile
        kwargs = {"max_results" if operation == "search" else "max_posts": limit,
                  "deadline_seconds": deadline_seconds if deadline_seconds is not None else 30}
        return await asyncio.to_thread(call, value, **kwargs)
    plan = XCrawler().people_actor_plan(operation, value, limit=limit)
    if plan["status"] != "configured":
        return {**plan, "records": [], "metadata": {"provider_status": "not_configured", "error_code": plan["error_code"], "provider_calls": 0}}
    from app.services.intelligence import account_scan_service

    failure = None
    try:
        # Same global reservations/execution claim/replay fence as IG/YT/TT.
        raw = await account_scan_service._run_actor(plan["actor_id"], plan["payload"],
                                                  timeout=max(1, min(180, int(deadline_seconds or 180))))
    except ActorRunError as exc:
        failure = exc.as_result(platform)
        raw = exc.partial_items
    records, rejected = x_records(raw[:limit], expected_handle=value if operation == "profile" else "")
    metadata = {"provider_status": "succeeded", "provider_mode": "apify", "actor_id": plan["actor_id"],
                "bounded_item_limit": limit, "rejected_identity_count": rejected}
    if failure:
        metadata.update(failure["metadata"])
    return {"status": failure["status"] if failure else ("partial" if rejected else "done" if records else "empty"),
            "records": records, "metadata": metadata}


async def search_secondary_people(platform: str, query: str, *, market: str = "", max_results: int = 25,
                                  deadline_seconds: float | None = None, page_cursor: Any = None) -> dict[str, Any]:
    platform = "x" if platform == "twitter" else platform
    if platform not in {"x", "reddit"}:
        return {"status": "unsupported_platform", "platform": platform, "items": [], "metadata": {"provider_calls": 0}}
    if page_cursor:
        return {"status": "unsupported_pagination", "platform": platform, "items": [], "metadata": {**_metadata(), "provider_calls": 0}}
    limit = max(1, min(int(max_results or 25), 100 if platform == "x" else 25))
    result = await _records(platform, query, limit=limit, operation="search", deadline_seconds=deadline_seconds)
    items = author_candidates(result["records"], limit=limit)
    capabilities = {"followers_available": platform == "x", "followers_unavailable": platform == "reddit",
                    "qualification_pending_reason": "followers_unknown" if platform == "reddit" else "",
                    "audience_market_available": False}
    return {"status": result["status"], "platform": platform, "query": query, "market": market, "items": items,
            "capabilities": capabilities,
            "metadata": {**_metadata(), **result.get("metadata", {}), "capabilities": capabilities, "unique_author_count": len(items)}}


async def scan_secondary_profile(platform: str, handle_or_url: str, max_posts: int = 12) -> dict[str, Any]:
    platform = "x" if platform == "twitter" else platform
    if platform not in {"x", "reddit"}:
        return {**_build_scan_result(platform, "", [], 0), "status": "unsupported_platform", "items": []}
    handle = person_handle(platform, handle_or_url)
    if not handle:
        return {**_build_scan_result(platform, "", [], 0), "status": "invalid_person_identity", "items": [],
                "metadata": {**_metadata(), "provider_calls": 0}}
    started = time.monotonic()
    limit = max(1, min(int(max_posts or 12), 100 if platform == "x" else 25))
    result = await _records(platform, handle, limit=limit, operation="profile")
    candidates = author_candidates(result["records"], limit=1)
    candidate = candidates[0] if candidates else {}
    posts = candidate.get("posts", [])
    profile = {key: value for key, value in candidate.items() if key not in {"posts", "representative_evidence"}}
    scanned = _build_scan_result(platform, handle, posts, time.monotonic() - started, profile=profile)
    return {**scanned, "status": result["status"], "items": posts,
            "follower_count": profile.get("followers"),
            "metadata": {**_metadata(), **result.get("metadata", {})}}
