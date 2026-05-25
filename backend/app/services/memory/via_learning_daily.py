"""Daily Via learning orchestration."""
from __future__ import annotations

from app.services.memory.via_learning_common import *
from app.services.memory.via_learning_evaluator import run_via_offline_evaluator
from app.services.memory.via_learning_internal import (
    _store_account_snapshot,
    _store_bh_snapshot,
    _store_comment_snapshot,
    _store_internal_system_snapshot,
)

async def run_via_daily_learning() -> dict[str, Any]:
    if not VIA_ENABLE_DAILY_LEARNING:
        return {"ok": False, "skipped": True, "reason": "VIA_ENABLE_DAILY_LEARNING=0"}

    summary: dict[str, Any] = {
        "ok": True,
        "official_accounts": [],
        "comment_sources": [],
        "bh": {"fetched": 0},
        "internal": {},
        "stored": {
            "observations": 0,
            "memory": 0,
            "feedback": 0,
            "products": 0,
            "regions": 0,
        },
    }

    for account in _official_accounts():
        platform = account["platform"]
        handle = account["handle"]
        result = await scan_account(platform, handle, max_posts=VIA_LEARNING_MAX_POSTS)
        refs = await _store_account_snapshot(platform, handle, result)
        summary["official_accounts"].append(
            {
                "platform": platform,
                "handle": handle,
                "posts": len(result.get("posts") or []),
                "stats": result.get("stats") or {},
            }
        )
        for key, value in refs.items():
            summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    comment_platforms = [
        ("youtube", VIA_OFFICIAL_YOUTUBE_HANDLE),
        ("instagram", VIA_OFFICIAL_INSTAGRAM_HANDLE),
        ("tiktok", VIA_OFFICIAL_TIKTOK_HANDLE),
    ]
    for platform, handle in comment_platforms:
        if not handle:
            continue
        fetch_kwargs = {}
        if platform == "youtube":
            fetch_kwargs = {
                "max_videos": max(6, VIA_LEARNING_MAX_POSTS),
                "max_comments_per_video": VIA_LEARNING_COMMENT_LIMIT,
                "channel_handle": handle,
            }
        elif platform == "instagram":
            fetch_kwargs = {
                "max_posts": max(6, VIA_LEARNING_MAX_POSTS),
                "max_comments_per_post": VIA_LEARNING_COMMENT_LIMIT,
                "account_handle": handle,
            }
        elif platform == "tiktok":
            fetch_kwargs = {
                "max_videos": max(6, VIA_LEARNING_MAX_POSTS),
                "max_comments_per_video": VIA_LEARNING_COMMENT_LIMIT,
                "profile_handle": handle,
            }
        comments = await fetch_viltrox_comments(platform, **fetch_kwargs)
        refs = await _store_comment_snapshot(platform, handle, comments)
        summary["comment_sources"].append(
            {
                "platform": platform,
                "handle": handle,
                "comments": len(comments),
            }
        )
        for key, value in refs.items():
            summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    bh_products = await fetch_bh_viltrox_products(max_items=VIA_LEARNING_BH_MAX_ITEMS)
    summary["bh"]["fetched"] = len(bh_products)
    bh_refs = await _store_bh_snapshot(bh_products)
    for key, value in bh_refs.items():
        summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    internal = await _store_internal_system_snapshot(window_days=30)
    summary["internal"] = internal
    for key, value in internal.get("stored", {}).items():
        summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    evaluator = await run_via_offline_evaluator(window_days=14, limit=240)
    summary["evaluator"] = evaluator
    for key, value in evaluator.get("stored", {}).items():
        summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    return summary


__all__ = [name for name in globals() if not name.startswith("__")]
