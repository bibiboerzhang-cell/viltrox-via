#!/usr/bin/env python3
"""Refresh existing evidence metrics from Apify.

Dry-run is the default. Commit mode updates existing rows only; it never inserts
new evidence.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import time
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import psycopg2
from apify_client import ApifyClient
from psycopg2.extras import RealDictCursor


YOUTUBE_ACTOR = os.getenv("APIFY_YOUTUBE_ACTOR_ID") or "streamers/youtube-scraper"
TIKTOK_ACTOR = os.getenv("APIFY_TIKTOK_ACTOR_ID") or "clockworks/tiktok-scraper"
ARTIFACT_DIR = Path("artifacts")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--platform", choices=["all", "youtube", "tiktok"], default="all")
    parser.add_argument("--batch-size", type=int, default=50)
    return parser.parse_args()


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    match = re.match(r"^([0-9]*\.?[0-9]+)\s*([kKmMbB])?$", text)
    if match:
        number = float(match.group(1))
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
            (match.group(2) or "").lower(),
            1,
        )
        return int(number * multiplier)
    try:
        return int(float(text))
    except ValueError:
        return None


def pick(source: dict[str, Any], paths: list[str]) -> Any:
    for path in paths:
        current: Any = source
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, ""):
            return current
    return None


def youtube_video_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        return parsed.path.strip("/").split("/")[0] or None
    if "youtube.com" not in host:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("v"):
        return query["v"][0]
    for pattern in (r"/shorts/([^/?#]+)", r"/embed/([^/?#]+)"):
        match = re.search(pattern, parsed.path)
        if match:
            return match.group(1)
    return None


def tiktok_video_id(url: str) -> str | None:
    match = re.search(r"/video/(\d+)", url)
    return match.group(1) if match else None


def evidence_key(platform: str, url: str) -> str | None:
    if platform == "youtube":
        video_id = youtube_video_id(url)
    else:
        video_id = tiktok_video_id(url)
    return f"{platform}:{video_id}" if video_id else None


def load_rows(conn, platform: str) -> list[dict[str, Any]]:
    where = "platform IN ('youtube','tiktok')"
    params: list[Any] = []
    if platform != "all":
        where = "platform = %s"
        params.append(platform)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id, platform, content_url, view_count, like_count, comment_count
            FROM vkpi_kol_video_evidence
            WHERE {where}
              AND evidence_type = 'video'
            ORDER BY platform, id
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]


def apify_usage(run: dict[str, Any]) -> dict[str, Any]:
    usage = run.get("usage") or {}
    return {
        "usageTotalUsd": usage.get("usageTotalUsd", run.get("usageTotalUsd")),
        "computeUnits": (usage.get("stats") or run.get("stats") or {}).get("computeUnits"),
    }


def map_youtube(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": evidence_key("youtube", item.get("url") or item.get("input") or ""),
        "returned_url": item.get("url") or item.get("input"),
        "view_count": to_int(pick(item, ["viewCount", "views", "view_count"])),
        "like_count": to_int(pick(item, ["likes", "likeCount", "like_count"])),
        "comment_count": to_int(pick(item, ["commentsCount", "commentCount", "comments"])),
        "share_count": None,
        "title": item.get("title") or "",
    }


def map_tiktok(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": evidence_key("tiktok", item.get("webVideoUrl") or item.get("submittedVideoUrl") or ""),
        "returned_url": item.get("webVideoUrl") or item.get("submittedVideoUrl"),
        "view_count": to_int(pick(item, ["playCount", "viewCount", "views"])),
        "like_count": to_int(pick(item, ["diggCount", "likeCount", "likes"])),
        "comment_count": to_int(pick(item, ["commentCount", "commentsCount", "comments"])),
        "share_count": to_int(pick(item, ["shareCount", "shares"])),
        "title": item.get("text") or "",
    }


def call_actor(client: ApifyClient, platform: str, urls: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start = time.time()
    if platform == "youtube":
        run = client.actor(YOUTUBE_ACTOR).call(
            run_input={"startUrls": [{"url": url} for url in urls], "maxResults": len(urls)},
            timeout_secs=600,
        )
    else:
        run = client.actor(TIKTOK_ACTOR).call(
            run_input={
                "postURLs": urls,
                "resultsPerPage": len(urls),
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            },
            timeout_secs=600,
        )
    dataset_id = run.get("defaultDatasetId")
    items = client.dataset(dataset_id).list_items(limit=max(1000, len(urls) + 10)).items if dataset_id else []
    mapper = map_youtube if platform == "youtube" else map_tiktok
    run_info = {
        "platform": platform,
        "actor": YOUTUBE_ACTOR if platform == "youtube" else TIKTOK_ACTOR,
        "run_id": run.get("id"),
        "status": run.get("status"),
        "input_count": len(urls),
        "item_count": len(items),
        "duration_sec": round(time.time() - start, 2),
        **apify_usage(run),
    }
    return [mapper(dict(item)) for item in items], run_info


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def build_status(row: dict[str, Any], mapped: dict[str, Any] | None) -> str:
    if not evidence_key(row["platform"], row["content_url"]):
        return "bad_url"
    if not mapped:
        return "failed_no_return"
    if row["view_count"] is None and mapped["view_count"] is not None:
        return "补齐"
    changed = []
    for field in ("view_count", "like_count", "comment_count"):
        old = row[field]
        new = mapped[field]
        if old is not None and new is not None and int(old) != int(new):
            changed.append(field.replace("_count", ""))
    return "数字变化:" + ",".join(changed) if changed else "对齐"


def maybe_update(conn, rows: list[dict[str, Any]]) -> int:
    columns = existing_columns(conn)
    optional = {
        "metrics_scraped_at": "NOW()",
        "metrics_source": "%s",
        "share_count": "%s",
    }
    updated = 0
    with conn.cursor() as cur:
        for row in rows:
            if not row.get("new_view_count") and row.get("new_view_count") != 0:
                continue
            set_parts = ["view_count = %s", "like_count = %s", "comment_count = %s"]
            params: list[Any] = [row["new_view_count"], row["new_like_count"], row["new_comment_count"]]
            if "share_count" in columns:
                set_parts.append("share_count = %s")
                params.append(row["new_share_count"])
            if "metrics_source" in columns:
                set_parts.append("metrics_source = %s")
                params.append("apify")
            if "metrics_scraped_at" in columns:
                set_parts.append("metrics_scraped_at = NOW()")
            params.append(row["id"])
            cur.execute(
                f"UPDATE vkpi_kol_video_evidence SET {', '.join(set_parts)} WHERE id = %s",
                params,
            )
            updated += cur.rowcount
    return updated


def existing_columns(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'vkpi_kol_video_evidence'
            """
        )
        return {row[0] for row in cur.fetchall()}


def global_metrics(conn) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE view_count IS NOT NULL),
              COALESCE(SUM(view_count), 0)
            FROM vkpi_kol_video_evidence
            """
        )
        count_with_view, exposure_sum = cur.fetchone()
    return int(count_with_view or 0), int(exposure_sum or 0)


def write_outputs(
    comparison: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    before_sum: int,
    global_with_view: int,
    global_exposure: int,
    stamp: str,
) -> tuple[Path, Path]:
    ARTIFACT_DIR.mkdir(exist_ok=True)
    csv_path = ARTIFACT_DIR / f"metrics_refresh_dryrun_{stamp}.csv"
    md_path = ARTIFACT_DIR / f"metrics_refresh_report_{stamp}.md"
    fields = [
        "id",
        "platform",
        "content_url",
        "old_view_count",
        "old_like_count",
        "old_comment_count",
        "new_view_count",
        "new_like_count",
        "new_comment_count",
        "new_share_count",
        "status",
        "match_key",
        "returned_url",
    ]
    with csv_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in comparison)

    counts = Counter(row["status"].split(":")[0] for row in comparison)
    by_platform = defaultdict(Counter)
    for row in comparison:
        by_platform[row["platform"]][row["status"].split(":")[0]] += 1
    old_with_view = sum(1 for row in comparison if row["old_view_count"] is not None)
    new_with_view = sum(1 for row in comparison if row["new_view_count"] is not None)
    new_sum = sum(int(row["new_view_count"] or row["old_view_count"] or 0) for row in comparison)
    predicted_global_with_view = global_with_view - old_with_view + new_with_view
    predicted_global_exposure = global_exposure - before_sum + new_sum
    failures = [row for row in comparison if row["status"] in {"bad_url", "failed_no_return"}]
    match_failures = sum(1 for row in comparison if row["status"] == "failed_no_return")
    total_cost = sum(float(run.get("usageTotalUsd") or 0) for run in runs)
    total_duration = sum(float(run.get("duration_sec") or 0) for run in runs)

    lines = [
        "# Evidence Metrics Refresh Dry-Run",
        "",
        "## 总览",
        f"- 抓取 evidence: {len(comparison)} 条",
        f"- 状态统计: {dict(counts)}",
        f"- view_count 覆盖率预测: {old_with_view} -> {new_with_view}",
        f"- 本轮样本 SUM(view_count): {before_sum:,} -> {new_sum:,}",
        f"- 全表 view_count 覆盖率预测: {global_with_view} -> {predicted_global_with_view}",
        f"- Total Exposure 预测: {global_exposure:,} -> {predicted_global_exposure:,}",
        f"- Apify runs: {len(runs)}",
        f"- 实际 cost: ${total_cost:.4f}",
        f"- Apify run 耗时合计: {total_duration:.2f}s",
        "",
        "## 按平台",
    ]
    for platform, counter in sorted(by_platform.items()):
        lines.append(f"- {platform}: {dict(counter)}")
    lines += [
        "",
        "## Run 明细",
        "| platform | run_id | input | returned | duration | cost |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for run in runs:
        lines.append(
            f"| {run['platform']} | {run.get('run_id')} | {run.get('input_count')} | "
            f"{run.get('item_count')} | {run.get('duration_sec')}s | ${float(run.get('usageTotalUsd') or 0):.4f} |"
        )
    lines += [
        "",
        "## 失败清单",
    ]
    if failures:
        for row in failures:
            lines.append(f"- {row['id']} {row['platform']} {row['status']} {row['content_url']}")
    else:
        lines.append("- 无")
    lines += [
        "",
        "## URL Match",
        f"- match 失败: {match_failures}",
        "- 匹配方式: youtube 提取 v/shorts/embed video id；tiktok 提取 /video/<id>；不按返回顺序。",
    ]
    md_path.write_text("\n".join(lines) + "\n")
    return csv_path, md_path


def main() -> None:
    args = parse_args()
    commit = bool(args.commit)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    rows = load_rows(conn, args.platform)
    global_with_view, global_exposure = global_metrics(conn)
    before_sum = sum(int(row["view_count"] or 0) for row in rows)
    runnable = [row for row in rows if evidence_key(row["platform"], row["content_url"])]
    bad = [row for row in rows if not evidence_key(row["platform"], row["content_url"])]

    client = ApifyClient(os.getenv("APIFY_API_TOKEN") or os.getenv("APIFY_TOKEN"))
    runs: list[dict[str, Any]] = []
    mapped_by_key: dict[str, dict[str, Any]] = {}
    for platform in ("youtube", "tiktok"):
        platform_rows = [row for row in runnable if row["platform"] == platform]
        for batch in chunked(platform_rows, args.batch_size):
            mapped, run_info = call_actor(client, platform, [row["content_url"] for row in batch])
            runs.append(run_info)
            mapped_by_key.update({item["key"]: item for item in mapped if item.get("key")})

    comparison: list[dict[str, Any]] = []
    for row in rows:
        key = evidence_key(row["platform"], row["content_url"])
        mapped = mapped_by_key.get(key) if key else None
        comparison.append(
            {
                "id": row["id"],
                "platform": row["platform"],
                "content_url": row["content_url"],
                "old_view_count": row["view_count"],
                "old_like_count": row["like_count"],
                "old_comment_count": row["comment_count"],
                "new_view_count": mapped.get("view_count") if mapped else None,
                "new_like_count": mapped.get("like_count") if mapped else None,
                "new_comment_count": mapped.get("comment_count") if mapped else None,
                "new_share_count": mapped.get("share_count") if mapped else None,
                "status": "bad_url" if row in bad else build_status(row, mapped),
                "match_key": key,
                "returned_url": mapped.get("returned_url") if mapped else None,
            }
        )

    stamp = now_stamp()
    csv_path, md_path = write_outputs(comparison, runs, before_sum, global_with_view, global_exposure, stamp)
    if commit:
        updated = maybe_update(conn, comparison)
        conn.commit()
        print(f"UPDATED_ROWS={updated}")
    else:
        conn.rollback()
    print(f"CSV={csv_path}")
    print(f"REPORT={md_path}")
    print(md_path.read_text())


if __name__ == "__main__":
    main()
