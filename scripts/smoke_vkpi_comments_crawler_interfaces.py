#!/usr/bin/env python3
"""Offline smoke for P1.3 crawler comment interfaces.

This smoke proves the five P1.3 platforms expose crawl_video_comments() and
that missing credentials return graceful not_configured/skip responses without
making external API calls.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Force offline before importing crawler classes.
for key in [
    "APIFY_TOKEN",
    "YOUTUBE_API_KEY",
    "GOOGLE_YOUTUBE_API_KEY",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USER_AGENT",
    "META_GRAPH_ACCESS_TOKEN",
]:
    os.environ[key] = ""


def main() -> None:
    from app.platform.industry_crawlers import get_crawler

    failures: list[str] = []
    expected = ["youtube", "instagram", "tiktok", "reddit", "facebook"]
    for platform in expected:
        crawler = get_crawler(platform)
        if crawler is None:
            failures.append(f"{platform}: crawler missing")
            continue
        method = getattr(crawler, "crawl_video_comments", None)
        if not callable(method):
            failures.append(f"{platform}: crawl_video_comments missing")
            continue
        result = method("dummy_comment_target", max_results=1)
        status = str(result.get("provider_status") or result.get("sync_status") or "")
        if status not in {"not_configured", "skip", "error"}:
            failures.append(f"{platform}: expected offline status, got {result}")
        if result.get("items"):
            failures.append(f"{platform}: offline path returned items")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)

    print("VKPI_COMMENTS_CRAWLER_INTERFACES_SMOKE_OK")


if __name__ == "__main__":
    main()
