#!/usr/bin/env python3
"""Read-only dry run for official-channel post-level deltas."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except (TypeError, ValueError):
        return int(default or 0)


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


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


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _latest_official_rows() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT c.id AS channel_id,
               c.platform,
               c.account_handle,
               c.account_display_name,
               c.account_url,
               c.staff_id,
               COALESCE(u.name, u.email, 'Staff ' || c.staff_id) AS staff_name,
               COALESCE(c.last_sync_status, '') AS last_sync_status,
               c.last_sync_at,
               COALESCE(c.last_sync_error, '') AS last_sync_error,
               m.id AS metric_id,
               m.snapshot_date,
               m.captured_at,
               m.followers AS metric_followers,
               m.posts_count AS metric_posts,
               m.total_views AS metric_views,
               m.total_likes AS metric_likes,
               m.total_comments AS metric_comments,
               m.total_shares AS metric_shares,
               m.followers_delta AS metric_followers_delta,
               m.posts_delta AS metric_posts_delta,
               m.views_delta_24h AS metric_views_delta,
               m.likes_delta_24h AS metric_likes_delta,
               m.raw_payload_json AS metric_raw_payload_json
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
            SELECT id FROM vkpi_channel_metrics mm
            WHERE mm.channel_id = c.id
            ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
            LIMIT 1
        )
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE c.deleted_at IS NULL AND c.status='active'
        ORDER BY c.platform ASC, c.account_handle ASC, c.id ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _latest_post_metric_aggregate(channel_id: int) -> dict[str, Any]:
    if not _table_exists("vkpi_channel_post_metrics"):
        return {"table_exists": False}
    conn = get_conn()
    latest = conn.execute(
        """
        SELECT snapshot_date
        FROM vkpi_channel_post_metrics
        WHERE channel_id=?
        ORDER BY snapshot_date DESC, captured_at DESC, id DESC
        LIMIT 1
        """,
        (int(channel_id),),
    ).fetchone()
    if not latest:
        return {"table_exists": True, "post_metric_rows": 0}
    snapshot_date = _text(_row_dict(latest).get("snapshot_date"))
    row = conn.execute(
        """
        SELECT COUNT(*) AS post_metric_rows,
               COALESCE(SUM(views_delta), 0) AS views_delta,
               COALESCE(SUM(likes_delta), 0) AS likes_delta,
               COALESCE(SUM(comments_delta), 0) AS comments_delta,
               COALESCE(SUM(shares_delta), 0) AS shares_delta,
               COALESCE(SUM(CASE WHEN views_delta > 0 OR likes_delta > 0 OR comments_delta > 0 OR shares_delta > 0 THEN 1 ELSE 0 END), 0) AS posts_with_positive_delta,
               COALESCE(SUM(CASE WHEN views_delta = 0 AND likes_delta = 0 AND comments_delta = 0 AND shares_delta = 0 THEN 1 ELSE 0 END), 0) AS posts_without_delta,
               CAST(MAX(captured_at) AS TEXT) AS latest_post_captured_at
        FROM vkpi_channel_post_metrics
        WHERE channel_id=? AND snapshot_date=?
        """,
        (int(channel_id), snapshot_date),
    ).fetchone()
    payload = _row_dict(row)
    return {
        "table_exists": True,
        "snapshot_date": snapshot_date,
        "post_metric_rows": _int(payload.get("post_metric_rows")),
        "views_delta": _int(payload.get("views_delta")),
        "likes_delta": _int(payload.get("likes_delta")),
        "comments_delta": _int(payload.get("comments_delta")),
        "shares_delta": _int(payload.get("shares_delta")),
        "posts_with_positive_delta": _int(payload.get("posts_with_positive_delta")),
        "posts_without_delta": _int(payload.get("posts_without_delta")),
        "latest_post_captured_at": _text(payload.get("latest_post_captured_at")),
    }


def _cumulative_floor_fields(raw_payload: dict[str, Any]) -> list[str]:
    floor = raw_payload.get("cumulative_floor") if isinstance(raw_payload.get("cumulative_floor"), dict) else {}
    fields = floor.get("fields") if isinstance(floor.get("fields"), dict) else {}
    return sorted(str(key) for key, value in fields.items() if value is not None)


def _post_level_delta(raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = raw_payload.get("post_level_delta") if isinstance(raw_payload.get("post_level_delta"), dict) else {}
    return {
        "sample_count": _int(payload.get("sample_count")),
        "matched_posts": _int(payload.get("matched_posts")),
        "new_posts": _int(payload.get("new_posts")),
        "first_seen_existing_posts": _int(payload.get("first_seen_existing_posts")),
        "views_delta": _int(payload.get("views_delta")),
        "likes_delta": _int(payload.get("likes_delta")),
        "comments_delta": _int(payload.get("comments_delta")),
        "shares_delta": _int(payload.get("shares_delta")),
        "method": _text(payload.get("method")),
    }


def _explain(row: dict[str, Any], post_delta: dict[str, Any], post_metrics: dict[str, Any], baseline_fields: list[str]) -> str:
    positive_post_delta = sum(
        _int(post_metrics.get(key))
        for key in ("views_delta", "likes_delta", "comments_delta", "shares_delta")
    )
    positive_raw_delta = sum(
        _int(post_delta.get(key))
        for key in ("views_delta", "likes_delta", "comments_delta", "shares_delta")
    )
    account_positive = any(
        _int(row.get(key)) > 0
        for key in ("metric_followers_delta", "metric_posts_delta", "metric_views_delta", "metric_likes_delta")
    )
    if positive_post_delta or positive_raw_delta:
        return "real_post_level_delta"
    if account_positive:
        return "account_level_delta_only"
    if _int(post_delta.get("first_seen_existing_posts")) and not positive_raw_delta:
        return "first_seen_existing_posts_not_counted_as_growth"
    if baseline_fields:
        return "baseline_protected_no_positive_delta"
    if not _int(post_metrics.get("post_metric_rows")) and not _int(post_delta.get("sample_count")):
        return "no_post_level_sample"
    return "no_positive_delta"


def build_report() -> dict[str, Any]:
    rows = _latest_official_rows()
    accounts: list[dict[str, Any]] = []
    platform_totals: dict[str, dict[str, Any]] = {}
    totals = {
        "accounts": 0,
        "channels_with_metrics": 0,
        "channels_with_post_metrics": 0,
        "baseline_protected_accounts": 0,
        "post_metric_rows": 0,
        "raw_sample_posts": 0,
        "matched_posts": 0,
        "new_posts": 0,
        "first_seen_existing_posts": 0,
        "views_delta": 0,
        "likes_delta": 0,
        "comments_delta": 0,
        "shares_delta": 0,
        "accounts_with_positive_post_delta": 0,
        "accounts_without_positive_delta": 0,
        "accounts_missing_post_metrics": 0,
    }
    for row in rows:
        raw_payload = _json_loads(row.get("metric_raw_payload_json"))
        post_delta = _post_level_delta(raw_payload)
        post_metrics = _latest_post_metric_aggregate(_int(row.get("channel_id")))
        baseline_fields = _cumulative_floor_fields(raw_payload)
        explanation = _explain(row, post_delta, post_metrics, baseline_fields)
        account = {
            "channel_id": _int(row.get("channel_id")),
            "platform": _text(row.get("platform")).lower(),
            "handle": _text(row.get("account_handle")),
            "display_name": _text(row.get("account_display_name"), _text(row.get("account_handle"))),
            "staff_id": _int(row.get("staff_id")),
            "staff_name": _text(row.get("staff_name")),
            "sync_status": _text(row.get("last_sync_status")),
            "last_sync_at": _text(row.get("last_sync_at")),
            "snapshot_date": _text(row.get("snapshot_date")),
            "captured_at": _text(row.get("captured_at")),
            "account_metrics": {
                "followers": _int(row.get("metric_followers")),
                "posts": _int(row.get("metric_posts")),
                "views": _int(row.get("metric_views")),
                "likes": _int(row.get("metric_likes")),
                "comments": _int(row.get("metric_comments")),
                "shares": _int(row.get("metric_shares")),
                "followers_delta": _int(row.get("metric_followers_delta")),
                "posts_delta": _int(row.get("metric_posts_delta")),
                "views_delta": _int(row.get("metric_views_delta")),
                "likes_delta": _int(row.get("metric_likes_delta")),
            },
            "baseline_protected": bool(baseline_fields),
            "baseline_protected_fields": baseline_fields,
            "post_level_delta": post_delta,
            "latest_post_metrics": post_metrics,
            "explanation": explanation,
        }
        accounts.append(account)

        platform = account["platform"] or "other"
        platform_row = platform_totals.setdefault(
            platform,
            {
                "accounts": 0,
                "baseline_protected_accounts": 0,
                "post_metric_rows": 0,
                "views_delta": 0,
                "likes_delta": 0,
                "comments_delta": 0,
                "shares_delta": 0,
                "accounts_with_positive_post_delta": 0,
                "accounts_missing_post_metrics": 0,
            },
        )
        totals["accounts"] += 1
        platform_row["accounts"] += 1
        if row.get("metric_id"):
            totals["channels_with_metrics"] += 1
        if baseline_fields:
            totals["baseline_protected_accounts"] += 1
            platform_row["baseline_protected_accounts"] += 1
        post_rows = _int(post_metrics.get("post_metric_rows"))
        totals["post_metric_rows"] += post_rows
        platform_row["post_metric_rows"] += post_rows
        if post_rows:
            totals["channels_with_post_metrics"] += 1
        else:
            totals["accounts_missing_post_metrics"] += 1
            platform_row["accounts_missing_post_metrics"] += 1
        for key in ("views_delta", "likes_delta", "comments_delta", "shares_delta"):
            value = _int(post_metrics.get(key))
            totals[key] += value
            platform_row[key] += value
        for key in ("sample_count", "matched_posts", "new_posts", "first_seen_existing_posts"):
            total_key = "raw_sample_posts" if key == "sample_count" else key
            totals[total_key] += _int(post_delta.get(key))
        if sum(_int(post_metrics.get(key)) for key in ("views_delta", "likes_delta", "comments_delta", "shares_delta")) > 0:
            totals["accounts_with_positive_post_delta"] += 1
            platform_row["accounts_with_positive_post_delta"] += 1
        else:
            totals["accounts_without_positive_delta"] += 1

    return {
        "mode": "read_only_dry_run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls": False,
        "selector": "official_accounts",
        "delta_method": "post_metric_delta_v1",
        "totals": totals,
        "platforms": dict(sorted(platform_totals.items())),
        "accounts": accounts,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Official Channel Post-Level Delta Dry Run",
        "",
        "Read-only dry run. This report does not call Apify, YouTube, or any external provider.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Accounts: {totals['accounts']}",
        f"- Channels with latest metrics: {totals['channels_with_metrics']}",
        f"- Channels with post metrics: {totals['channels_with_post_metrics']}",
        f"- Post metric rows in latest snapshots: {totals['post_metric_rows']}",
        f"- Baseline protected accounts: {totals['baseline_protected_accounts']}",
        f"- Post-level views delta: {totals['views_delta']}",
        f"- Post-level likes delta: {totals['likes_delta']}",
        f"- Post-level comments delta: {totals['comments_delta']}",
        f"- First-seen existing posts not counted as growth: {totals['first_seen_existing_posts']}",
        "",
        "| ID | Platform | Handle | Snapshot | Rows | Views Delta | Likes Delta | Comments Delta | Baseline | Explanation |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for account in report["accounts"]:
        post_metrics = account["latest_post_metrics"]
        baseline = ",".join(account["baseline_protected_fields"]) if account["baseline_protected_fields"] else "-"
        lines.append(
            "| {channel_id} | {platform} | {handle} | {snapshot_date} | {rows} | {views} | {likes} | {comments} | {baseline} | {explanation} |".format(
                channel_id=account["channel_id"],
                platform=account["platform"],
                handle=account["handle"],
                snapshot_date=account["snapshot_date"] or "-",
                rows=_int(post_metrics.get("post_metric_rows")),
                views=_int(post_metrics.get("views_delta")),
                likes=_int(post_metrics.get("likes_delta")),
                comments=_int(post_metrics.get("comments_delta")),
                baseline=baseline,
                explanation=account["explanation"],
            )
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only official-channel post-level delta dry run.")
    parser.add_argument("--json-out", default="", help="Write JSON report to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown report to this path")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    return parser.parse_args(argv)


def _write(path_value: str, content: str) -> None:
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = build_report()
        markdown = render_markdown(report)
        if args.json_out:
            _write(args.json_out, json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        if args.md_out:
            _write(args.md_out, markdown)
        if args.json:
            stdout_out(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            stdout_out(markdown)
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
