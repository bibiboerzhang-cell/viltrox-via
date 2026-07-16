#!/usr/bin/env python3
"""Read-only audit for official-channel comment coverage contracts.

This script reads existing official-channel posts and cached comment bodies. It
does not call Apify, YouTube, PRAW, X, Gemini, LLMs, or comment collectors.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.domains.comments import channel as channel_comments  # noqa: E402
from app.domains import channels  # noqa: E402


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return int(default or 0)


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
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _admin_staff() -> dict[str, Any]:
    return {"id": 0, "role": "admin", "is_owner": 1, "permissions": ["vkpi:admin"]}


def _post_external_ids(platform: str, post: dict[str, Any]) -> set[str]:
    values = {
        _text(post.get("id")),
        _text(post.get("source_id")),
        _text(post.get("short_code")),
        _text(post.get("url")),
    }
    if platform == "reddit":
        values |= {channels._reddit_external_id(value) for value in list(values)}
    return {value for value in values if value}


def _cached_comment_count(platform: str, external_ids: set[str], *, table_exists: bool) -> int:
    if not external_ids or not table_exists:
        return 0
    placeholders = ",".join("?" for _ in external_ids)
    row = get_conn().execute(
        f"""
        SELECT COUNT(*) AS n
        FROM vkpi_comments
        WHERE platform=? AND external_post_id IN ({placeholders})
        """,
        (platform, *sorted(external_ids)),
    ).fetchone()
    return _int(dict(row).get("n") if row else 0)


def _row_contract(
    *,
    platform: str,
    post: dict[str, Any],
    package_dir: str,
    cap: int,
    comments_table_exists: bool,
) -> dict[str, Any]:
    external_ids = _post_external_ids(platform, post)
    cached_total = _cached_comment_count(platform, external_ids, table_exists=comments_table_exists)
    if platform == "reddit":
        cached_total = max(cached_total, len(channel_comments._package_reddit_comments(package_dir, external_ids)))
    cached_visible = min(cached_total, cap)
    contract = channel_comments._comment_contract(
        declared=_int(post.get("comments")),
        cached=cached_visible,
        cap=cap,
        collect_supported=platform in channel_comments.COMMENT_COLLECT_PLATFORMS,
        missing_post_id=not external_ids,
    )
    return {
        "post_id": _text(post.get("source_id"), _text(post.get("id"), _text(post.get("url")))),
        "url": _text(post.get("url")),
        "title": _text(post.get("title"))[:120],
        "declared": contract["declared"],
        "cached": contract["cached"],
        "cached_total": cached_total,
        "cap": contract["cap"],
        "status": contract["status"],
        "collect_supported": platform in channel_comments.COMMENT_COLLECT_PLATFORMS,
    }


def build_report(*, limit_per_account: int = 50, comment_cap: int = 300) -> dict[str, Any]:
    safe_limit = max(1, min(50, int(limit_per_account or 50)))
    safe_cap = max(1, min(channel_comments.MAX_CHANNEL_COMMENT_CAP, int(comment_cap or 300)))
    comments_table_exists = _table_exists("vkpi_comments")
    status_counts: Counter[str] = Counter()
    platform_rows: dict[str, dict[str, Any]] = {}
    samples: list[dict[str, Any]] = []
    totals = {
        "posts": 0,
        "posts_with_declared_comments": 0,
        "declared_comments": 0,
        "cached_comment_bodies_visible": 0,
        "cached_comment_bodies_total": 0,
        "missing_contract": 0,
    }

    rows = channels._latest_official_channel_rows(staff=_admin_staff())
    for row in rows:
        platform = _text(row.get("platform"), "other").lower()
        posts, source, package_dir = channels._all_posts_for_channel(row)
        posts = posts[:safe_limit]
        platform_entry = platform_rows.setdefault(
            platform,
            {
                "platform": platform,
                "accounts": 0,
                "posts": 0,
                "declared_comments": 0,
                "cached_comment_bodies_visible": 0,
                "cached_comment_bodies_total": 0,
                "status_counts": Counter(),
                "sources": Counter(),
            },
        )
        platform_entry["accounts"] += 1
        platform_entry["sources"][source] += 1
        for post in posts:
            contract = _row_contract(platform=platform, post=post, package_dir=package_dir, cap=safe_cap, comments_table_exists=comments_table_exists)
            status = _text(contract.get("status"), "missing")
            platform_entry["posts"] += 1
            platform_entry["declared_comments"] += contract["declared"]
            platform_entry["cached_comment_bodies_visible"] += contract["cached"]
            platform_entry["cached_comment_bodies_total"] += contract["cached_total"]
            platform_entry["status_counts"][status] += 1
            status_counts[status] += 1
            totals["posts"] += 1
            totals["declared_comments"] += contract["declared"]
            totals["cached_comment_bodies_visible"] += contract["cached"]
            totals["cached_comment_bodies_total"] += contract["cached_total"]
            totals["posts_with_declared_comments"] += int(contract["declared"] > 0)
            missing_keys = [key for key in ("declared", "cached", "cap", "status") if key not in contract]
            totals["missing_contract"] += int(bool(missing_keys))
            if status in {"not_cached", "partial", "capped", "missing_post_id", "not_supported"} and len(samples) < 30:
                samples.append(
                    {
                        "platform": platform,
                        "channel_id": int(row.get("id") or 0),
                        "handle": _text(row.get("account_handle")),
                        **contract,
                    }
                )

    platform_reports = []
    for key in sorted(platform_rows):
        row = platform_rows[key]
        platform_reports.append(
            {
                "platform": row["platform"],
                "accounts": row["accounts"],
                "posts": row["posts"],
                "declared_comments": row["declared_comments"],
                "cached_comment_bodies_visible": row["cached_comment_bodies_visible"],
                "cached_comment_bodies_total": row["cached_comment_bodies_total"],
                "status_counts": dict(sorted(row["status_counts"].items())),
                "sources": dict(sorted(row["sources"].items())),
            }
        )

    return {
        "mode": "read_only_official_comment_contract_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls": False,
        "collector_calls": False,
        "passed": bool(totals["posts"] > 0 and totals["missing_contract"] == 0),
        "limit_per_account": safe_limit,
        "comment_cap": safe_cap,
        "vkpi_comments_table_exists": comments_table_exists,
        "account_count": len(rows),
        "totals": totals,
        "status_counts": dict(sorted(status_counts.items())),
        "platforms": platform_reports,
        "samples": samples,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    totals = report.get("totals") or {}
    lines = [
        "# Official Comment Contract Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- Collector calls: `{str(report['collector_calls']).lower()}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Accounts: `{report['account_count']}`",
        f"- Posts inspected: `{totals.get('posts', 0)}`",
        f"- Posts with declared comments: `{totals.get('posts_with_declared_comments', 0)}`",
        f"- Declared comments: `{totals.get('declared_comments', 0)}`",
        f"- Cached bodies visible under cap: `{totals.get('cached_comment_bodies_visible', 0)}`",
        f"- Cached bodies total: `{totals.get('cached_comment_bodies_total', 0)}`",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in (report.get("status_counts") or {}).items():
        lines.append(f"- `{status}`: `{count}`")
    lines.extend(["", "## Platform Counts", "", "| Platform | Accounts | Posts | Declared | Cached visible | Status counts |", "|---|---:|---:|---:|---:|---|"])
    for row in report.get("platforms") or []:
        counts = ", ".join(f"{key}={value}" for key, value in (row.get("status_counts") or {}).items())
        lines.append(
            f"| {row['platform']} | {row['accounts']} | {row['posts']} | {row['declared_comments']} | "
            f"{row['cached_comment_bodies_visible']} | {counts or '-'} |"
        )
    if report.get("samples"):
        lines.extend(["", "## Incomplete Samples", ""])
        for row in report["samples"]:
            lines.append(
                f"- `{row['platform']}` `{row['handle']}` `{row['post_id']}` "
                f"status=`{row['status']}` declared=`{row['declared']}` cached=`{row['cached']}` cap=`{row['cap']}`"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-per-account", type=int, default=50)
    parser.add_argument("--comment-cap", type=int, default=300)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(limit_per_account=args.limit_per_account, comment_cap=args.comment_cap)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    if args.md_out:
        write_markdown(report, Path(args.md_out))
    if args.json or not (args.json_out or args.md_out):
        stdout_out(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("passed") else 3


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    finally:
        try:
            asyncio.run(close_db_runtime())
        except Exception:
            pass
    raise SystemExit(exit_code)
