#!/usr/bin/env python3
"""Read-only audit for official-channel canonical post identity.

This script reads existing official-channel snapshots. It does not call Apify,
YouTube, Gemini, LLMs, or any crawler.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
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


def _candidate_metric_uids(platform: str, post: dict[str, Any]) -> set[str]:
    values = {
        _text(post.get("provider_post_id")),
        _text(post.get("canonical_post_uid")),
        _text(post.get("canonical_url")),
        _text(post.get("source_id")),
        _text(post.get("short_code")),
        _text(post.get("id")),
        _text(post.get("url")),
    }
    if platform == "reddit":
        values |= {channels._reddit_external_id(value) for value in list(values)}
    return {value for value in values if value}


def _metric_post_uids(channel_id: int, *, table_exists: bool) -> set[str]:
    if not table_exists:
        return set()
    try:
        rows = get_conn().execute(
            """
            SELECT post_uid
            FROM vkpi_channel_post_metrics
            WHERE channel_id=?
            """,
            (int(channel_id),),
        ).fetchall()
    except Exception:
        return set()
    return {_text(dict(row).get("post_uid")) for row in rows if _text(dict(row).get("post_uid"))}


def build_report(*, limit_per_account: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(50, int(limit_per_account or 50)))
    metric_table_exists = _table_exists("vkpi_channel_post_metrics")
    rows = channels._latest_official_channel_rows(staff=_admin_staff())
    platform_rows: dict[str, dict[str, Any]] = {}
    source_counts: Counter[str] = Counter()
    global_identity_counts: Counter[tuple[str, str]] = Counter()
    duplicate_samples: list[dict[str, Any]] = []
    missing_identity_samples: list[dict[str, Any]] = []
    metric_match_samples: list[dict[str, Any]] = []
    totals = {
        "posts": 0,
        "missing_canonical_uid": 0,
        "missing_provider_post_id": 0,
        "url_fallback_count": 0,
        "account_duplicate_canonical_uid_count": 0,
        "global_duplicate_canonical_uid_count": 0,
        "metric_rows": 0,
        "metric_rows_matched": 0,
    }

    for row in rows:
        platform = _text(row.get("platform"), "other").lower()
        posts, source, _package_dir = channels._all_posts_for_channel(row)
        posts = posts[:safe_limit]
        metric_uids = _metric_post_uids(_int(row.get("id")), table_exists=metric_table_exists)
        account_matched_metric_uids: set[str] = set()
        account_identity_counts: Counter[str] = Counter()
        platform_entry = platform_rows.setdefault(
            platform,
            {
                "platform": platform,
                "accounts": 0,
                "posts": 0,
                "missing_canonical_uid": 0,
                "missing_provider_post_id": 0,
                "url_fallback_count": 0,
                "metric_rows": 0,
                "metric_rows_matched": 0,
                "identity_sources": Counter(),
                "sources": Counter(),
            },
        )
        platform_entry["accounts"] += 1
        platform_entry["sources"][source] += 1
        platform_entry["metric_rows"] += len(metric_uids)
        totals["metric_rows"] += len(metric_uids)
        for post in posts:
            canonical_uid = _text(post.get("canonical_post_uid"))
            provider_id = _text(post.get("provider_post_id"))
            identity_source = _text(post.get("post_identity_source"), "missing")
            platform_entry["posts"] += 1
            platform_entry["identity_sources"][identity_source] += 1
            source_counts[identity_source] += 1
            totals["posts"] += 1
            if not canonical_uid:
                totals["missing_canonical_uid"] += 1
                platform_entry["missing_canonical_uid"] += 1
                if len(missing_identity_samples) < 30:
                    missing_identity_samples.append(
                        {
                            "platform": platform,
                            "channel_id": _int(row.get("id")),
                            "handle": _text(row.get("account_handle")),
                            "post_id": _text(post.get("source_id"), _text(post.get("id"), _text(post.get("url")))),
                        }
                    )
            else:
                global_identity_counts[(platform, canonical_uid)] += 1
                account_identity_counts[canonical_uid] += 1
            if not provider_id:
                totals["missing_provider_post_id"] += 1
                platform_entry["missing_provider_post_id"] += 1
            if identity_source == "canonical_url":
                totals["url_fallback_count"] += 1
                platform_entry["url_fallback_count"] += 1
            matched = metric_uids & _candidate_metric_uids(platform, post)
            account_matched_metric_uids.update(matched)

        platform_entry["metric_rows_matched"] += len(account_matched_metric_uids)
        totals["metric_rows_matched"] += len(account_matched_metric_uids)
        if metric_uids and not account_matched_metric_uids and len(metric_match_samples) < 30:
            metric_match_samples.append(
                {
                    "platform": platform,
                    "channel_id": _int(row.get("id")),
                    "handle": _text(row.get("account_handle")),
                    "metric_rows": len(metric_uids),
                    "posts_checked": len(posts),
                }
            )
        for uid, count in account_identity_counts.items():
            if count > 1:
                totals["account_duplicate_canonical_uid_count"] += count - 1
            if count > 1 and len(duplicate_samples) < 30:
                duplicate_samples.append(
                    {
                        "scope": "account",
                        "platform": platform,
                        "channel_id": _int(row.get("id")),
                        "handle": _text(row.get("account_handle")),
                        "canonical_post_uid": uid,
                        "count": count,
                    }
                )

    for (platform, uid), count in global_identity_counts.items():
        if count > 1:
            totals["global_duplicate_canonical_uid_count"] += count - 1
            if len(duplicate_samples) < 30:
                duplicate_samples.append(
                    {
                        "scope": "global",
                        "platform": platform,
                        "canonical_post_uid": uid,
                        "count": count,
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
                "missing_canonical_uid": row["missing_canonical_uid"],
                "missing_provider_post_id": row["missing_provider_post_id"],
                "url_fallback_count": row["url_fallback_count"],
                "metric_rows": row["metric_rows"],
                "metric_rows_matched": row["metric_rows_matched"],
                "identity_sources": dict(sorted(row["identity_sources"].items())),
                "sources": dict(sorted(row["sources"].items())),
            }
        )

    return {
        "mode": "read_only_official_post_identity_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls": False,
        "collector_calls": False,
        "passed": bool(
            totals["posts"] > 0
            and totals["missing_canonical_uid"] == 0
            and totals["account_duplicate_canonical_uid_count"] == 0
        ),
        "limit_per_account": safe_limit,
        "metric_table_exists": metric_table_exists,
        "account_count": len(rows),
        "totals": totals,
        "identity_sources": dict(sorted(source_counts.items())),
        "platforms": platform_reports,
        "missing_identity_samples": missing_identity_samples,
        "duplicate_identity_samples": duplicate_samples,
        "metric_match_samples": metric_match_samples,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    totals = report.get("totals") or {}
    lines = [
        "# Official Post Identity Audit",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- Collector calls: `{str(report['collector_calls']).lower()}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Accounts: `{report['account_count']}`",
        f"- Posts inspected: `{totals.get('posts', 0)}`",
        f"- Missing canonical uid: `{totals.get('missing_canonical_uid', 0)}`",
        f"- Missing provider post id: `{totals.get('missing_provider_post_id', 0)}`",
        f"- URL fallback count: `{totals.get('url_fallback_count', 0)}`",
        f"- Account duplicate canonical uid count: `{totals.get('account_duplicate_canonical_uid_count', 0)}`",
        f"- Global duplicate canonical uid count: `{totals.get('global_duplicate_canonical_uid_count', 0)}`",
        f"- Metric rows matched: `{totals.get('metric_rows_matched', 0)}/{totals.get('metric_rows', 0)}`",
        "",
        "## Identity Sources",
        "",
    ]
    for source, count in (report.get("identity_sources") or {}).items():
        lines.append(f"- `{source}`: `{count}`")
    lines.extend(
        [
            "",
            "## Platform Counts",
            "",
            "| Platform | Accounts | Posts | Missing UID | Missing Provider ID | URL Fallback | Metric Match | Identity sources |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in report.get("platforms") or []:
        sources = ", ".join(f"{key}={value}" for key, value in (row.get("identity_sources") or {}).items())
        lines.append(
            f"| {row['platform']} | {row['accounts']} | {row['posts']} | {row['missing_canonical_uid']} | "
            f"{row['missing_provider_post_id']} | {row['url_fallback_count']} | "
            f"{row['metric_rows_matched']}/{row['metric_rows']} | {sources or '-'} |"
        )
    if report.get("missing_identity_samples"):
        lines.extend(["", "## Missing Identity Samples", ""])
        for row in report["missing_identity_samples"]:
            lines.append(f"- `{row['platform']}` channel=`{row['channel_id']}` handle=`{row['handle']}` post=`{row['post_id']}`")
    if report.get("duplicate_identity_samples"):
        lines.extend(["", "## Duplicate Identity Samples", ""])
        for row in report["duplicate_identity_samples"]:
            lines.append(
                f"- `{row['scope']}` `{row['platform']}` uid=`{row['canonical_post_uid']}` count=`{row['count']}`"
            )
    if report.get("metric_match_samples"):
        lines.extend(["", "## Metric Match Samples", ""])
        for row in report["metric_match_samples"]:
            lines.append(
                f"- `{row['platform']}` channel=`{row['channel_id']}` handle=`{row['handle']}` "
                f"metric_rows=`{row['metric_rows']}` posts_checked=`{row['posts_checked']}`"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-per-account", type=int, default=50)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(limit_per_account=args.limit_per_account)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    if args.md_out:
        write_markdown(report, Path(args.md_out))
    if args.json or not (args.json_out or args.md_out):
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
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
