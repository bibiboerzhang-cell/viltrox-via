"""scripts/smoke_vkpi_reddit_offline.py

P1.1 Reddit crawler offline smoke.

Tests:
  1. Crawler registration in _CRAWLER_REGISTRY
  2. Configured property handles missing token gracefully
  3. crawl_subreddit with no token returns not_configured
  4. crawl_brand_mentions with no token returns not_configured
  5. crawl_post_comments with no token returns not_configured
  6. _normalize_subreddit_name handles URLs/handles/IDs
  7. _normalize_post_id handles URLs/IDs
  8. V-KPI unified interface (crawl_channel_profile/videos/comments)

Does NOT do:
  - Real Reddit API calls (use smoke_vkpi_crawler_live_mapping_guard for that)
  - PRAW token validation
  - Network requests

Run:
  PYTHONPATH=backend .venv/bin/python scripts/smoke_vkpi_reddit_offline.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main():
    failures = []

    # Force unset env vars for true offline test
    for key in [
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USER_AGENT",
        "APIFY_TOKEN",
    ]:
        os.environ.pop(key, None)
    os.environ["VKPI_REDDIT_PUBLIC_JSON_ENABLED"] = "0"

    from app.services.vkpi.industry_crawlers import (
        get_crawler,
        is_supported,
        supported_platforms,
    )

    # Test 1: Registration
    print("[1] Crawler registration...")
    if not is_supported("reddit"):
        failures.append("reddit not in supported_platforms()")
    if "reddit" not in supported_platforms():
        failures.append("'reddit' missing from supported_platforms() list")

    crawler = get_crawler("reddit")
    if crawler is None:
        failures.append("get_crawler('reddit') returned None")
        print("  FAIL: cannot get crawler instance")
        for f in failures:
            print(f"    - {f}")
        sys.exit(1)
    print("  ✓ reddit registered")

    # Test 2: configured property (no token)
    print("[2] configured property (no env)...")
    if crawler.configured:
        failures.append("crawler.configured should be False with no env")
    else:
        print("  ✓ configured=False as expected")

    # Test 3: primary_path
    print("[3] primary_path with no env...")
    if crawler.primary_path != "none":
        failures.append(
            f"primary_path should be 'none', got '{crawler.primary_path}'"
        )
    else:
        print("  ✓ primary_path='none'")

    # Test 4: crawl_subreddit graceful degradation
    print("[4] crawl_subreddit with no config...")
    result = crawler.crawl_subreddit("cinematography", limit=5)
    if result.get("provider_status") != "not_configured":
        failures.append(
            f"Expected provider_status='not_configured', got "
            f"'{result.get('provider_status')}'"
        )
    if result.get("items"):
        failures.append(
            f"Expected empty items, got {len(result.get('items', []))}"
        )
    if result.get("sync_status") != "skip":
        failures.append(
            f"Expected sync_status='skip', got '{result.get('sync_status')}'"
        )
    print("  ✓ graceful not_configured")

    # Test 5: crawl_brand_mentions graceful
    print("[5] crawl_brand_mentions with no config...")
    result = crawler.crawl_brand_mentions("viltrox", limit=10)
    if result.get("provider_status") not in ("not_configured", "skip"):
        failures.append(
            f"Expected not_configured, got '{result.get('provider_status')}'"
        )
    print("  ✓ graceful")

    # Test 6: crawl_post_comments graceful
    print("[6] crawl_post_comments with no config...")
    result = crawler.crawl_post_comments("abc123", max_depth=3)
    if result.get("provider_status") not in ("not_configured", "skip"):
        failures.append(
            f"Expected not_configured, got '{result.get('provider_status')}'"
        )
    print("  ✓ graceful")

    # Test 7: _normalize_subreddit_name
    print("[7] subreddit name normalization...")
    cases = [
        ("cinematography", "cinematography"),
        ("/r/cinematography", "cinematography"),
        ("r/cinematography", "cinematography"),
        (
            "https://reddit.com/r/cinematography/",
            "cinematography",
        ),
        (
            "https://www.reddit.com/r/cinematography/comments/abc/",
            "cinematography",
        ),
    ]
    for input_val, expected in cases:
        result = crawler._normalize_subreddit_name(input_val)
        if result != expected:
            failures.append(
                f"_normalize_subreddit_name({input_val!r}) = "
                f"{result!r}, expected {expected!r}"
            )
    print(f"  ✓ {len(cases)} cases passed")

    # Test 8: _normalize_post_id
    print("[8] post id normalization...")
    cases = [
        ("abc123", "abc123"),
        ("t3_abc123", "abc123"),
        (
            "https://reddit.com/r/cinematography/comments/abc123/title/",
            "abc123",
        ),
    ]
    for input_val, expected in cases:
        result = crawler._normalize_post_id(input_val)
        if result != expected:
            failures.append(
                f"_normalize_post_id({input_val!r}) = "
                f"{result!r}, expected {expected!r}"
            )
    print(f"  ✓ {len(cases)} cases passed")

    # Test 9: V-KPI unified interface
    print("[9] V-KPI unified interface...")
    result = crawler.crawl_channel_profile(
        "https://reddit.com/r/cinematography/", max_posts=3
    )
    if result.get("provider_status") not in ("not_configured", "skip"):
        failures.append(
            "crawl_channel_profile should respect not_configured"
        )

    result = crawler.crawl_channel_videos(
        "cinematography", max_posts=3
    )
    if result.get("provider_status") not in ("not_configured", "skip"):
        failures.append(
            "crawl_channel_videos should respect not_configured"
        )

    result = crawler.crawl_video_comments("abc123", max_results=10)
    if result.get("provider_status") not in ("not_configured", "skip"):
        failures.append(
            "crawl_video_comments should respect not_configured"
        )
    print("  ✓ unified interface respects not_configured")

    # Test 10: Empty input handling
    print("[10] Empty input handling...")
    result = crawler.crawl_subreddit("", limit=5)
    if result.get("provider_status") not in ("error", "not_configured"):
        failures.append(
            "Empty subreddit should return error or not_configured"
        )

    result = crawler.crawl_brand_mentions("", limit=5)
    if result.get("provider_status") not in ("error", "not_configured"):
        failures.append(
            "Empty query should return error or not_configured"
        )

    result = crawler.crawl_post_comments("", max_depth=3)
    if result.get("provider_status") not in ("error", "not_configured"):
        failures.append(
            "Empty post_id should return error or not_configured"
        )
    print("  ✓ empty inputs handled")

    # Final
    print()
    if failures:
        print(f"FAIL: {len(failures)} issues:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("VKPI_REDDIT_OFFLINE_SMOKE_OK")


if __name__ == "__main__":
    main()
