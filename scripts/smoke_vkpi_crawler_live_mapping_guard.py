#!/usr/bin/env python3
"""Safe live-crawl readiness and optional one-account mapping check.

Default mode is offline and does not call external providers. It verifies that
the seven crawler adapters are registered, settings rows exist, and budget gates
are visible. Use --live explicitly to run one real provider request.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")

from app.services.vkpi import platform_crawl_settings  # noqa: E402
from app.services.vkpi.industry_crawlers import get_crawler, supported_platforms  # noqa: E402
from app.services.vkpi.industry_snapshot_collector import calculate_kpis  # noqa: E402


EXPECTED_PLATFORMS = {
    "youtube",
    "instagram",
    "tiktok",
    "xiaohongshu",
    "bilibili",
    "x",
    "twitch",
    "reddit",
    "facebook",
}
KPI_KEYS = ("followers", "posts", "views", "views_30d", "likes", "comments", "shares", "saves")


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _settings_map() -> dict[str, dict[str, Any]]:
    rows = platform_crawl_settings.platform_settings().get("platforms") or []
    return {str(row.get("platform") or "").lower(): dict(row) for row in rows}


def _provider_status(platform: str) -> dict[str, Any]:
    crawler = get_crawler(platform)
    if crawler is None:
        return {"provider": platform, "configured": False, "provider_status": "not_registered"}
    status_fn = getattr(crawler, "provider_status", None)
    if callable(status_fn):
        status = status_fn()
        if isinstance(status, dict):
            return status
    return {"provider": platform, "configured": bool(getattr(crawler, "configured", False)), "provider_status": "unknown"}


def _redacted_readiness() -> dict[str, Any]:
    settings = _settings_map()
    platforms = supported_platforms()
    missing = sorted(EXPECTED_PLATFORMS - set(platforms))
    rows: list[dict[str, Any]] = []
    for platform in sorted(EXPECTED_PLATFORMS):
        config = settings.get(platform) or {}
        provider = _provider_status(platform)
        crawl_enabled = _as_bool(config.get("crawl_enabled"))
        monthly_budget = _as_float(config.get("monthly_budget_usd"))
        rows.append(
            {
                "platform": platform,
                "registered": platform in platforms,
                "configured": bool(provider.get("configured")),
                "provider_status": provider.get("provider_status"),
                "crawl_enabled": crawl_enabled,
                "monthly_budget_usd": monthly_budget,
                "live_gate": "open" if crawl_enabled and monthly_budget > 0 else "closed",
            }
        )
    return {"supported_platforms": platforms, "missing_expected": missing, "platforms": rows}


def _call_profile(platform: str, handle: str, *, channel_id: str = "", max_posts: int = 3) -> dict[str, Any]:
    crawler = get_crawler(platform)
    if crawler is None:
        raise RuntimeError(f"{platform} crawler not registered")
    if not bool(getattr(crawler, "configured", False)):
        raise RuntimeError(f"{platform} crawler not configured")
    try:
        return crawler.crawl_channel_profile(handle, channel_id=channel_id, max_posts=max_posts)
    except TypeError:
        return crawler.crawl_channel_profile(handle, channel_id=channel_id)


def _raw_for_kpis(platform: str, profile_payload: dict[str, Any]) -> dict[str, Any]:
    items = profile_payload.get("items") or []
    videos: list[dict[str, Any]] = []
    if items and isinstance(items[0], dict):
        first = items[0]
        for key in ("latestPosts", "posts", "videos", "items"):
            value = first.get(key)
            if isinstance(value, list):
                videos = [item for item in value if isinstance(item, dict)]
                break
    return {
        "source": f"{platform}_live_mapping_guard",
        "profile": profile_payload,
        "videos": videos,
        "kpi_status": profile_payload.get("sync_status") or profile_payload.get("provider_status"),
    }


def _run_live(args: argparse.Namespace) -> dict[str, Any]:
    platform = str(args.platform or "").strip().lower()
    if platform not in EXPECTED_PLATFORMS:
        raise SystemExit(f"--platform must be one of: {', '.join(sorted(EXPECTED_PLATFORMS))}")
    if not args.handle:
        raise SystemExit("--live requires --handle")

    settings = _settings_map().get(platform) or {}
    if not args.ignore_gates:
        if not _as_bool(settings.get("crawl_enabled")):
            raise SystemExit(f"{platform} crawl_enabled is false. Enable it before live crawl, or pass --ignore-gates.")
        if _as_float(settings.get("monthly_budget_usd")) <= 0:
            raise SystemExit(f"{platform} monthly_budget_usd is 0. Set budget before live crawl, or pass --ignore-gates.")

    profile_payload = _call_profile(platform, args.handle, channel_id=args.channel_id or "", max_posts=max(1, min(5, int(args.max_posts or 3))))
    raw_data = _raw_for_kpis(platform, profile_payload)
    kpis = calculate_kpis(raw_data)
    non_null = {key: kpis.get(key) for key in KPI_KEYS if kpis.get(key) is not None}
    item_count = len(profile_payload.get("items") or [])
    mapping_status = "mapped" if non_null else ("no_items" if item_count == 0 else "unmapped")
    if profile_payload.get("provider_status") in {"ok", "configured", "synced"} and item_count and not non_null:
        raise AssertionError(f"{platform} returned profile items but KPI mapping produced no known metrics")
    return {
        "mode": "live",
        "platform": platform,
        "provider_status": profile_payload.get("provider_status"),
        "sync_status": profile_payload.get("sync_status"),
        "items": item_count,
        "mapping_status": mapping_status,
        "mapped_kpis": non_null,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe live mapping guard for V-KPI crawlers")
    parser.add_argument("--live", action="store_true", help="Run one real crawl. Default only checks readiness.")
    parser.add_argument("--platform", default="", help="Platform for --live")
    parser.add_argument("--handle", default="", help="Handle or profile URL for --live")
    parser.add_argument("--channel-id", default="", help="Optional provider channel/user id")
    parser.add_argument("--max-posts", type=int, default=3, help="Max posts for --live, capped at 5")
    parser.add_argument("--ignore-gates", action="store_true", help="Bypass local crawl_enabled/budget guard for one manual live check")
    args = parser.parse_args()

    readiness = _redacted_readiness()
    if readiness["missing_expected"]:
        raise AssertionError(f"missing crawler registrations: {readiness['missing_expected']}")

    if args.live:
        result = _run_live(args)
        print(json.dumps({"readiness": readiness, "live_result": result}, ensure_ascii=False, indent=2, default=str))
        print("VKPI_CRAWLER_LIVE_MAPPING_GUARD_SMOKE_OK")
        return

    print(json.dumps(readiness, ensure_ascii=False, indent=2, default=str))
    print("VKPI_CRAWLER_LIVE_MAPPING_GUARD_SMOKE_OK")


if __name__ == "__main__":
    main()
