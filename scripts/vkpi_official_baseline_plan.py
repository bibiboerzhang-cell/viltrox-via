#!/usr/bin/env python3
"""Dry-run plan for official-channel baseline crawling.

This script does not call providers. It reads the current official account
matrix and prints a controlled crawl plan for the one-time baseline plus the
daily recent refresh that should follow it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PlatformPolicy:
    provider: str
    baseline_target: int
    current_safe_limit: int
    daily_recent_limit: int
    hot_refresh_limit: int
    views_quality: str
    notes: str


POLICIES: dict[str, PlatformPolicy] = {
    "youtube": PlatformPolicy(
        provider="youtube_api",
        baseline_target=1000,
        current_safe_limit=50,
        daily_recent_limit=30,
        hot_refresh_limit=50,
        views_quality="high",
        notes="Current crawler needs pageToken pagination for full history beyond 50 videos.",
    ),
    "instagram": PlatformPolicy(
        provider="apify_instagram",
        baseline_target=500,
        current_safe_limit=100,
        daily_recent_limit=30,
        hot_refresh_limit=50,
        views_quality="medium_video_only",
        notes="Reels/video views can be present; image posts should not be treated as view-bearing.",
    ),
    "tiktok": PlatformPolicy(
        provider="apify_tiktok",
        baseline_target=300,
        current_safe_limit=100,
        daily_recent_limit=30,
        hot_refresh_limit=50,
        views_quality="high_when_actor_returns_playCount",
        notes="Keep video downloads disabled; cache covers only.",
    ),
    "facebook": PlatformPolicy(
        provider="apify_facebook",
        baseline_target=250,
        current_safe_limit=100,
        daily_recent_limit=25,
        hot_refresh_limit=40,
        views_quality="low_without_reels_actor",
        notes="Current page/posts scraper returns post media and engagement but usually not views; Reels/video views need a separate path.",
    ),
    "reddit": PlatformPolicy(
        provider="praw_or_apify_reddit",
        baseline_target=150,
        current_safe_limit=100,
        daily_recent_limit=25,
        hot_refresh_limit=25,
        views_quality="not_available",
        notes="Treat as community posts + upvotes/comments; do not chase play views.",
    ),
    "x": PlatformPolicy(
        provider="x_api_or_apify",
        baseline_target=200,
        current_safe_limit=50,
        daily_recent_limit=25,
        hot_refresh_limit=25,
        views_quality="low_rate_limited",
        notes="Use as supplemental source unless a stable actor/token is confirmed.",
    ),
}


PRIORITY = {
    "youtube": 10,
    "instagram": 20,
    "tiktok": 30,
    "facebook": 40,
    "reddit": 50,
    "x": 60,
}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _accounts() -> list[dict[str, Any]]:
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.vkpi import channels  # noqa: WPS433

    matrix = channels.official_account_matrix(staff={"id": 1, "role": "admin"}, limit=8)
    rows: list[dict[str, Any]] = []
    for platform in matrix.get("platforms") or []:
        for account in platform.get("accounts") or []:
            rows.append(dict(account))
    return rows


def _recommended_target(account: dict[str, Any], policy: PlatformPolicy) -> int:
    known_posts = _int(account.get("posts_count"))
    if known_posts <= 0:
        return policy.daily_recent_limit
    return min(max(policy.daily_recent_limit, known_posts), policy.baseline_target)


def _row(account: dict[str, Any]) -> dict[str, Any]:
    platform = str(account.get("platform") or "other").lower()
    policy = POLICIES.get(platform)
    if policy is None:
        policy = PlatformPolicy(
            provider="unsupported",
            baseline_target=0,
            current_safe_limit=0,
            daily_recent_limit=0,
            hot_refresh_limit=0,
            views_quality="unknown",
            notes="No current official-channel crawler policy.",
        )
    target = _recommended_target(account, policy)
    current_limit = min(target, policy.current_safe_limit) if policy.current_safe_limit else 0
    needs_full_unlock = target > current_limit
    sync_status = str(account.get("sync_status") or "")
    views = _int(account.get("total_views"))
    posts = _int(account.get("posts_count"))
    return {
        "channel_id": _int(account.get("id")),
        "platform": platform,
        "account": str(account.get("display_name") or account.get("handle") or ""),
        "handle": str(account.get("handle") or ""),
        "url": str(account.get("account_url") or ""),
        "staff": str(account.get("staff_name") or ""),
        "sync_status": sync_status,
        "known_posts": posts,
        "known_views": views,
        "provider": policy.provider,
        "baseline_target": target,
        "current_safe_limit": current_limit,
        "daily_recent_limit": policy.daily_recent_limit,
        "hot_refresh_limit": policy.hot_refresh_limit,
        "views_quality": policy.views_quality,
        "needs_full_unlock": needs_full_unlock,
        "first_batch_action": "fix_or_confirm_handle" if sync_status == "no_results" else "baseline_partial" if needs_full_unlock else "baseline_full_current",
        "notes": policy.notes,
    }


def build_plan() -> dict[str, Any]:
    accounts = sorted(
        (_row(account) for account in _accounts()),
        key=lambda item: (PRIORITY.get(item["platform"], 99), item["account"].lower(), item["channel_id"]),
    )
    return {
        "mode": "dry_run",
        "account_count": len(accounts),
        "platforms": sorted({item["platform"] for item in accounts}),
        "totals": {
            "baseline_target_items": sum(item["baseline_target"] for item in accounts),
            "current_safe_first_batch_items": sum(item["current_safe_limit"] for item in accounts),
            "daily_recent_items": sum(item["daily_recent_limit"] for item in accounts),
            "hot_refresh_items": sum(item["hot_refresh_limit"] for item in accounts),
            "accounts_needing_full_unlock": sum(1 for item in accounts if item["needs_full_unlock"]),
        },
        "accounts": accounts,
    }


def _markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Official Channel Baseline Crawl Plan",
        "",
        "Dry run only. This output does not call Apify or any external provider.",
        "",
        f"- Accounts: {plan['account_count']}",
        f"- Platforms: {', '.join(plan['platforms'])}",
        f"- First safe batch items: {plan['totals']['current_safe_first_batch_items']:,}",
        f"- Baseline target items: {plan['totals']['baseline_target_items']:,}",
        f"- Daily recent refresh items: {plan['totals']['daily_recent_items']:,}",
        f"- Accounts needing pagination/special actor unlock: {plan['totals']['accounts_needing_full_unlock']}",
        "",
        "| ID | Platform | Account | Status | Known Posts | Views | First Batch | Target | Views Quality | Action |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in plan["accounts"]:
        lines.append(
            "| {channel_id} | {platform} | {account} | {sync_status} | {known_posts} | {known_views} | "
            "{current_safe_limit} | {baseline_target} | {views_quality} | {first_batch_action} |".format(**item)
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Do not run baseline on page load.",
            "- Manual full baseline must be a background job with an explicit confirmation.",
            "- Daily refresh should only crawl recent content after the baseline.",
            "- Facebook views require a separate Reels/video path; current page/post actor is not enough.",
            "- Reddit should be treated as posts plus interaction, not play/view volume.",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print JSON instead of markdown.")
    args = parser.parse_args()
    try:
        plan = build_plan()
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print(_markdown(plan))
    finally:
        try:
            from app.db.connection import close_db_runtime  # noqa: WPS433

            asyncio.run(close_db_runtime())
        except Exception:
            pass


if __name__ == "__main__":
    main()
