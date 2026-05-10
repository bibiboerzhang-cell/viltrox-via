"""scripts/smoke_vkpi_facebook_offline.py

P1.2 Facebook crawler offline smoke.

Tests:
  1. Crawler registration
  2. Configured property
  3. crawl_page_profile graceful degradation
  4. crawl_brand_mentions returns not_supported in P1.2
  5. crawl_video_comments returns not_configured without token
  6. _normalize_page_url handles URLs/handles/IDs
  7. V-KPI unified interface

Run:
  PYTHONPATH=backend .venv/bin/python scripts/smoke_vkpi_facebook_offline.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main():
    failures = []

    # Force unset env for true offline test
    for key in [
        "APIFY_TOKEN",
        "APIFY_FACEBOOK_PAGES_ACTOR_ID",
        "APIFY_FACEBOOK_POSTS_ACTOR_ID",
        "META_GRAPH_ACCESS_TOKEN",
        "META_GRAPH_API_VERSION",
    ]:
        os.environ.pop(key, None)

    from app.services.vkpi.industry_crawlers import (
        get_crawler,
        is_supported,
        supported_platforms,
    )

    print("[1] Crawler registration...")
    if not is_supported("facebook"):
        failures.append("facebook not in supported_platforms()")
    if "facebook" not in supported_platforms():
        failures.append("'facebook' missing from supported_platforms()")

    crawler = get_crawler("facebook")
    if crawler is None:
        failures.append("get_crawler('facebook') returned None")
        print("  FAIL")
        for f in failures:
            print(f"    - {f}")
        sys.exit(1)
    print("  ✓ facebook registered")

    print("[2] configured (no env)...")
    if crawler.configured:
        failures.append("crawler.configured should be False with no env")
    else:
        print("  ✓ False")

    print("[3] primary_path with no env...")
    if crawler.primary_path != "none":
        failures.append(f"primary_path='none' expected, got '{crawler.primary_path}'")
    else:
        print("  ✓ 'none'")

    print("[4] crawl_page_profile graceful...")
    result = crawler.crawl_page_profile(
        "https://www.facebook.com/ViltroxLens/", max_posts=5
    )
    if result.get("provider_status") != "not_configured":
        failures.append(
            f"Expected not_configured, got '{result.get('provider_status')}'"
        )
    if result.get("items"):
        failures.append(f"Expected empty items, got {len(result.get('items', []))}")
    print("  ✓ not_configured")

    print("[5] crawl_brand_mentions returns not_supported...")
    result = crawler.crawl_brand_mentions("viltrox", limit=10)
    # Either not_configured OR not_supported (in P1.2 brand search is limited)
    if result.get("provider_status") not in ("not_supported", "not_configured", "skip"):
        failures.append(
            f"Expected not_supported/not_configured, got '{result.get('provider_status')}'"
        )
    print("  ✓ graceful")

    print("[6] crawl_video_comments returns not_configured without token...")
    result = crawler.crawl_video_comments("post_id_test", max_results=10)
    if result.get("provider_status") != "not_configured":
        failures.append(
            f"Expected not_configured, got '{result.get('provider_status')}'"
        )
    print("  ✓ not_configured")

    print("[7] _normalize_page_url cases...")
    cases = [
        ("https://www.facebook.com/ViltroxLens/", "https://www.facebook.com/ViltroxLens"),
        ("https://www.facebook.com/ViltroxLens", "https://www.facebook.com/ViltroxLens"),
        ("facebook.com/ViltroxLens", "https://www.facebook.com/ViltroxLens"),
        ("ViltroxLens", "https://www.facebook.com/ViltroxLens"),
        ("@ViltroxLens", "https://www.facebook.com/ViltroxLens"),
    ]
    for input_val, expected in cases:
        result = crawler._normalize_page_url(input_val)
        if result != expected:
            failures.append(
                f"_normalize_page_url({input_val!r}) = {result!r}, expected {expected!r}"
            )
    print(f"  ✓ {len(cases)} cases")

    print("[8] V-KPI unified interface...")
    result = crawler.crawl_channel_profile("ViltroxLens", max_posts=3)
    if result.get("provider_status") not in ("not_configured", "skip"):
        failures.append(
            "crawl_channel_profile should respect not_configured"
        )
    
    result = crawler.crawl_channel_videos(
        "https://www.facebook.com/ViltroxLens/", max_posts=3
    )
    if result.get("provider_status") not in ("not_configured", "skip"):
        failures.append(
            "crawl_channel_videos should respect not_configured"
        )
    print("  ✓ unified interface")

    print("[9] Empty input handling...")
    result = crawler.crawl_page_profile("", max_posts=5)
    if result.get("provider_status") not in ("error", "not_configured"):
        failures.append("Empty page_url should error")

    result = crawler.crawl_brand_mentions("", limit=5)
    if result.get("provider_status") not in ("error", "not_configured", "not_supported"):
        failures.append("Empty query should error")
    print("  ✓ empty inputs")

    print("[10] handle_to_page_url...")
    result = crawler._handle_to_page_url("ViltroxLens")
    if result != "https://www.facebook.com/ViltroxLens":
        failures.append(f"handle conversion failed: {result}")
    
    result = crawler._handle_to_page_url("", channel_id="ViltroxLens")
    if result != "https://www.facebook.com/ViltroxLens":
        failures.append(f"channel_id fallback failed: {result}")
    print("  ✓ handle conversion")

    # Final
    print()
    if failures:
        print(f"FAIL: {len(failures)} issues:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("VKPI_FACEBOOK_OFFLINE_SMOKE_OK")


if __name__ == "__main__":
    main()
