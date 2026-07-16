#!/usr/bin/env python3
"""Bounded Reddit market signal smoke for V-KPI.

Default behavior is deliberately conservative:
  - no database writes
  - no LLM/Gemini calls
  - no sync/deep-scan trigger
  - Apify fallback disabled unless --allow-apify is passed
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.platform.industry_crawlers.reddit_crawler import RedditCrawler
from app.domains.market.signal_taxonomy import (
    KEYWORD_GROUPS,
    TIER1_GROUPS,
    TIER2_GROUPS,
    keyword_groups,
    keyword_hits,
    summarize_keyword_groups,
)
from app.domains.market.reddit_stability_strategy import RECOMMENDED_WATCHLIST, SOURCE_LIMITS


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_env(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _is_profile(item: dict[str, Any]) -> bool:
    return item.get("type") == "subreddit_profile" or item.get("dataType") == "community"


def _text_of(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(key) or "") for key in ("title", "body", "selftext", "caption", "text"))


def _normalize_post(item: dict[str, Any]) -> dict[str, Any]:
    raw_id = str(item.get("id") or item.get("postId") or item.get("name") or "")
    post_id = raw_id.replace("t3_", "")
    body = _text_of(item)
    hits = keyword_hits(body)
    groups = keyword_groups(hits)
    return {
        "source_uid": f"reddit:{post_id}" if post_id else "",
        "platform": "reddit",
        "subreddit": item.get("subreddit") or item.get("communityName") or "",
        "post_id": post_id,
        "title": str(item.get("title") or "")[:300],
        "author": item.get("author") or item.get("username") or "",
        "published_at": item.get("created_at") or item.get("createdAt") or "",
        "score": item.get("score"),
        "upvote_ratio": item.get("upvote_ratio"),
        "num_comments": item.get("num_comments") or item.get("comments"),
        "source_url": item.get("permalink") or item.get("url") or "",
        "keyword_hits": hits,
        "keyword_groups": groups,
        "tier1_hit_count": sum(len(groups.get(name, [])) for name in TIER1_GROUPS),
        "tier2_hit_count": sum(len(groups.get(name, [])) for name in TIER2_GROUPS),
        "viltrox_product_hit_count": len(groups.get("viltrox_products", [])),
        "text_len": len(body),
        "raw_payload_hash": hashlib.sha256(
            json.dumps(item, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()[:16],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Reddit Signal Smoke v0",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- apify_fallback_disabled: `{not report['scope']['allow_apify']}`",
        f"- write_db: `{report['write_db']}`",
        f"- llm_calls: `{report['llm_calls']}`",
        f"- gemini_calls: `{report['gemini_calls']}`",
        f"- subreddits: `{report['summary']['subreddits_ok']}/{report['summary']['subreddits_attempted']}` ok",
        f"- posts: `{report['summary']['total_posts']}`",
        f"- keyword_hit_posts: `{report['summary']['keyword_hit_posts']}`",
        f"- elapsed_seconds: `{report['summary']['elapsed_seconds']}`",
        "",
        "## Subreddit Results",
        "",
        "| subreddit | provider | status | posts | seconds | error |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in report["subreddit_results"]:
        error = str(row.get("error") or "").replace("|", "/")[:100]
        lines.append(
            f"| r/{row['subreddit']} | {row.get('provider') or ''} | "
            f"{row.get('provider_status') or ''} | {row.get('posts_count') or 0} | "
            f"{row.get('elapsed_seconds') or 0} | {error} |"
        )
    lines.extend(["", "## Top Keywords", ""])
    for keyword, count in report["summary"]["top_keywords"][:20]:
        lines.append(f"- `{keyword}`: {count}")
    lines.extend(["", "## Keyword Groups", ""])
    for group_name, rows in report["summary"]["top_keywords_by_group"].items():
        if not rows:
            continue
        formatted = ", ".join(f"`{keyword}` {count}" for keyword, count in rows[:8])
        lines.append(f"- `{group_name}`: {formatted}")
    lines.extend(["", "## Top Signal Candidates", ""])
    for post in report["top_signal_candidates"][:12]:
        hits = ", ".join(post["keyword_hits"]) or "none"
        groups = ", ".join(post.get("keyword_groups", {}).keys()) or "none"
        lines.append(
            f"- r/{post['subreddit']} · score `{post.get('score')}` · "
            f"comments `{post.get('num_comments')}` · groups `{groups}` · hits `{hits}` · "
            f"{post['title']} · {post['source_url']}"
        )
    lines.extend(
        [
            "",
            "## Next Write Plan",
            "",
            "- Do not write smoke rows directly into decision tables.",
            "- Backup before the first DB write round.",
            "- Use `reddit:{post_id}` as stable UID and keep raw payload hash for dedupe/provenance.",
            "- Only reviewed/classified competitor rows should enter `vkpi_competitor_signals`.",
            "- Open Tier 2 keywords automatically when Tier 1 mentions are below 20 in a run.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_smoke(subreddits: list[str], *, limit: int, allow_apify: bool, out_dir: Path) -> dict[str, Any]:
    if not allow_apify:
        os.environ.pop("APIFY_TOKEN", None)
        os.environ.pop("APIFY_API_TOKEN", None)

    crawler = RedditCrawler()
    started = time.time()
    results: list[dict[str, Any]] = []
    posts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    safe_limit = max(1, min(int(limit or 25), int(SOURCE_LIMITS["subreddit_posts_hard_cap"])))
    for subreddit in subreddits:
        sub_started = time.time()
        result = crawler.crawl_subreddit(subreddit, limit=safe_limit)
        items = result.get("items") or []
        subreddit_posts = [
            _normalize_post(item)
            for item in items
            if isinstance(item, dict) and not _is_profile(item)
        ]
        posts.extend(subreddit_posts)
        row = {
            "subreddit": subreddit,
            "provider": result.get("provider"),
            "provider_status": result.get("provider_status"),
            "sync_status": result.get("sync_status"),
            "posts_count": len(subreddit_posts),
            "elapsed_seconds": round(time.time() - sub_started, 2),
            "error": result.get("error"),
            "sample_posts": subreddit_posts[:5],
        }
        if row["provider_status"] != "ok":
            errors.append(
                {
                    "subreddit": subreddit,
                    "provider": row["provider"],
                    "provider_status": row["provider_status"],
                    "sync_status": row["sync_status"],
                    "error": row["error"],
                }
            )
        results.append(row)
        time.sleep(0.35)

    keyword_summary = summarize_keyword_groups(posts)
    ranked_posts = sorted(
        posts,
        key=lambda post: (
            int(post.get("viltrox_product_hit_count") or 0) * 4
            + int(post.get("tier1_hit_count") or 0) * 3
            + int(post.get("tier2_hit_count") or 0) * 2,
            len(post["keyword_hits"]),
            int(post.get("num_comments") or 0),
            int(post.get("score") or 0),
        ),
        reverse=True,
    )
    report = {
        "mode": "reddit-signal-smoke-v0",
        "generated_at": _now_z(),
        "write_db": False,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "provider_calls": True,
        "provider_status": crawler.provider_status(),
        "scope": {
            "watchlist": subreddits,
            "limit_per_subreddit": safe_limit,
            "allow_apify": allow_apify,
            "no_full_reddit_claim": True,
            "comments_collected": False,
            "keyword_policy": {
                "tier1_groups": sorted(TIER1_GROUPS),
                "tier2_groups": sorted(TIER2_GROUPS),
                "open_tier2_when_tier1_mentions_below": 20,
            },
        },
        "summary": {
            "subreddits_attempted": len(subreddits),
            "subreddits_ok": sum(1 for row in results if row["provider_status"] == "ok"),
            "subreddits_error": len(errors),
            "total_posts": len(posts),
            "elapsed_seconds": round(time.time() - started, 2),
            **keyword_summary,
        },
        "subreddit_results": results,
        "top_signal_candidates": ranked_posts[:25],
        "errors": errors,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{_stamp()}-reddit-signal-smoke-v0"
    if not allow_apify:
        prefix += "-json-only"
    json_path = out_dir / f"{prefix}.json"
    md_path = out_dir / f"{prefix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    return {"json_path": str(json_path.resolve()), "md_path": str(md_path.resolve()), **report}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded V-KPI Reddit signal smoke.")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--subreddit", action="append", dest="subreddits")
    parser.add_argument("--out-dir", default="runtime/ops")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--allow-apify", action="store_true")
    args = parser.parse_args()

    _load_env(args.env_file)
    subreddits = args.subreddits or list(RECOMMENDED_WATCHLIST)
    result = run_smoke(
        subreddits,
        limit=args.limit,
        allow_apify=bool(args.allow_apify),
        out_dir=Path(args.out_dir),
    )
    stdout_out(
        json.dumps(
            {
                "json_path": result["json_path"],
                "md_path": result["md_path"],
                "summary": result["summary"],
                "keyword_policy": result["scope"]["keyword_policy"],
                "errors": result["errors"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
