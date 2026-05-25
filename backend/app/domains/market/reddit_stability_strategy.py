"""P5.67 Reddit stability strategy and readiness report."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.platform.industry_crawlers import get_crawler, is_supported


REDDIT_SOURCE_CONTRACT = {
    "identity": ["source_uid", "platform", "source_url", "subreddit", "post_id"],
    "content": ["title", "body", "author", "published_at", "permalink"],
    "metrics": ["score", "upvote_ratio", "num_comments"],
    "comments": ["comment_id", "parent_id", "depth", "author", "body", "score", "created_at"],
    "provenance": ["provider", "provider_path", "captured_at", "raw_payload_hash"],
    "review": ["review_status", "signal_type", "brand", "product_hint", "confidence"],
}

SOURCE_LIMITS = {
    "subreddit_posts_default": 25,
    "subreddit_posts_hard_cap": 100,
    "brand_search_default": 25,
    "brand_search_hard_cap": 50,
    "post_comments_default": 100,
    "post_comments_hard_cap": 300,
    "comment_depth_default": 3,
    "comment_depth_hard_cap": 5,
}

RECOMMENDED_WATCHLIST = [
    "photography",
    "videography",
    "cinematography",
    "SonyAlpha",
    "fujifilm",
    "nikon",
    "M43",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _env_present(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _count(table_name: str) -> int:
    if not _table_exists(table_name):
        return 0
    try:
        row = get_conn().execute(f"SELECT COUNT(*) AS n FROM {table_name}").fetchone()
        return int(row["n"] or 0) if row else 0
    except Exception:
        return 0


def _provider_modes() -> dict[str, Any]:
    crawler = get_crawler("reddit")
    status = crawler.provider_status() if crawler is not None else {}
    oauth_ready = bool(_env_present("REDDIT_CLIENT_ID") and _env_present("REDDIT_CLIENT_SECRET"))
    apify_ready = _env_present("APIFY_TOKEN")
    public_json_enabled = bool(status.get("public_json_enabled"))
    preferred = "praw_oauth" if oauth_ready and status.get("praw_available") else "public_json_best_effort" if public_json_enabled else "apify_fallback" if apify_ready else "none"
    return {
        "crawler_registered": is_supported("reddit") and crawler is not None,
        "provider_status": status,
        "oauth_ready": oauth_ready,
        "praw_available": bool(status.get("praw_available")),
        "public_json_enabled": public_json_enabled,
        "apify_ready": apify_ready,
        "preferred_path": preferred,
        "env": {
            "REDDIT_CLIENT_ID": _env_present("REDDIT_CLIENT_ID"),
            "REDDIT_CLIENT_SECRET": _env_present("REDDIT_CLIENT_SECRET"),
            "REDDIT_USER_AGENT": _env_present("REDDIT_USER_AGENT"),
            "VKPI_REDDIT_PUBLIC_JSON_ENABLED": os.environ.get("VKPI_REDDIT_PUBLIC_JSON_ENABLED", "default_true"),
            "APIFY_TOKEN": apify_ready,
            "APIFY_REDDIT_ACTOR_ID": _env_present("APIFY_REDDIT_ACTOR_ID"),
        },
    }


def build_reddit_stability_report() -> dict[str, Any]:
    modes = _provider_modes()
    tables = {
        "vkpi_market_scan_runs": {"exists": _table_exists("vkpi_market_scan_runs"), "rows": _count("vkpi_market_scan_runs")},
        "vkpi_market_sources": {"exists": _table_exists("vkpi_market_sources"), "rows": _count("vkpi_market_sources")},
        "vkpi_market_mentions": {"exists": _table_exists("vkpi_market_mentions"), "rows": _count("vkpi_market_mentions")},
        "vkpi_competitor_signals": {"exists": _table_exists("vkpi_competitor_signals"), "rows": _count("vkpi_competitor_signals")},
    }
    checks = {
        "reddit_crawler_registered": bool(modes["crawler_registered"]),
        "provider_paths_classified": modes["preferred_path"] in {"praw_oauth", "public_json_best_effort", "apify_fallback", "none"},
        "best_effort_is_explicit": bool(modes["public_json_enabled"] or modes["oauth_ready"] or modes["apify_ready"] or modes["preferred_path"] == "none"),
        "no_full_reddit_promise": True,
        "watchlist_required": bool(RECOMMENDED_WATCHLIST),
        "hard_caps_defined": all(value > 0 for value in SOURCE_LIMITS.values()),
        "source_contract_defined": all(bool(fields) for fields in REDDIT_SOURCE_CONTRACT.values()),
        "market_storage_ready": all(tables[name]["exists"] for name in ("vkpi_market_scan_runs", "vkpi_market_sources", "vkpi_market_mentions")),
        "review_storage_ready": bool(tables["vkpi_competitor_signals"]["exists"]),
        "external_calls_blocked": True,
        "writes_blocked": True,
        "provider_calls_blocked": True,
        "sync_blocked": True,
    }
    decision = "go_strategy_only"
    if modes["preferred_path"] == "praw_oauth":
        next_step = "P5.67 can proceed to one manual OAuth smoke after approval."
    elif modes["preferred_path"] == "public_json_best_effort":
        next_step = "Use public JSON only as best-effort for allowlisted subreddits; do not promise completeness."
    elif modes["preferred_path"] == "apify_fallback":
        next_step = "Apify fallback is available but should remain behind budget approval."
    else:
        next_step = "No Reddit provider path configured; keep strategy only."
    return {
        "mode": "p5_67_reddit_stability_strategy",
        "generated_at": _now(),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "decision": decision,
        "next_step": next_step,
        "checks": checks,
        "provider_modes": modes,
        "limits": SOURCE_LIMITS,
        "recommended_watchlist": RECOMMENDED_WATCHLIST,
        "source_contract": REDDIT_SOURCE_CONTRACT,
        "tables": tables,
        "policy": {
            "scope": "allowlisted_subreddits_and_selected_posts_only",
            "no_full_reddit_claim": True,
            "no_broad_all_reddit_search": True,
            "comments_require_selected_post": True,
            "apify_requires_budget_approval": True,
            "storage": "market_scan_tables_first_then_reviewed_competitor_signals",
        },
    }
