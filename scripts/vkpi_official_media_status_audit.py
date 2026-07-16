#!/usr/bin/env python3
"""Read-only media status audit for V-KPI official-channel posts.

This script reads the official channel matrix from the current database. It does
not call Apify, YouTube, Gemini, LLMs, or any crawler.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domains import channels
from app.db.connection import close_db_runtime


def _text(value: Any, fallback: str = "") -> str:
    return str(value if value not in (None, "") else fallback).strip()


def build_report(*, limit: int = 50) -> dict[str, Any]:
    matrix = channels.official_account_matrix(limit=limit)
    platforms = matrix.get("platforms") if isinstance(matrix.get("platforms"), list) else []
    status_counts: Counter[str] = Counter()
    platform_rows: dict[str, dict[str, Any]] = {}
    missing_status: list[dict[str, Any]] = []

    for platform in platforms:
        if not isinstance(platform, dict):
            continue
        platform_key = _text(platform.get("platform"), "other").lower()
        platform_counts: Counter[str] = Counter()
        accounts = platform.get("accounts") if isinstance(platform.get("accounts"), list) else []
        post_count = 0
        for account in accounts:
            if not isinstance(account, dict):
                continue
            posts = account.get("posts") if isinstance(account.get("posts"), list) else []
            for post in posts:
                if not isinstance(post, dict):
                    continue
                post_count += 1
                status = _text(post.get("media_status"), "missing")
                status_counts[status] += 1
                platform_counts[status] += 1
                if status == "missing":
                    missing_status.append(
                        {
                            "platform": platform_key,
                            "account": _text(account.get("handle") or account.get("display_name")),
                            "post_id": _text(post.get("id") or post.get("source_id") or post.get("url")),
                        }
                    )
        platform_rows[platform_key] = {
            "platform": platform_key,
            "accounts": len(accounts),
            "posts": post_count,
            "status_counts": dict(sorted(platform_counts.items())),
        }

    total_posts = sum(row["posts"] for row in platform_rows.values())
    passed = bool(total_posts) and not missing_status
    return {
        "mode": "read_only_official_media_status_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls": False,
        "passed": passed,
        "limit_per_account": limit,
        "account_count": int(matrix.get("account_count") or 0),
        "post_count": total_posts,
        "status_counts": dict(sorted(status_counts.items())),
        "platforms": [platform_rows[key] for key in sorted(platform_rows)],
        "missing_status": missing_status[:50],
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Official Media Status Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Accounts: `{report['account_count']}`",
        f"- Posts inspected: `{report['post_count']}`",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in (report.get("status_counts") or {}).items():
        lines.append(f"- `{status}`: `{count}`")
    lines.extend(["", "## Platform Counts", "", "| Platform | Accounts | Posts | Status counts |", "|---|---:|---:|---|"])
    for row in report.get("platforms") or []:
        counts = ", ".join(f"{key}={value}" for key, value in (row.get("status_counts") or {}).items())
        lines.append(f"| {row['platform']} | {row['accounts']} | {row['posts']} | {counts or '-'} |")
    if report.get("missing_status"):
        lines.extend(["", "## Missing Status Samples", ""])
        for row in report["missing_status"]:
            lines.append(f"- `{row['platform']}` `{row['account']}` `{row['post_id']}`")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(limit=max(1, min(50, args.limit)))
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
