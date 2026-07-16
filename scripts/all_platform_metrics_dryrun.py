#!/usr/bin/env python3
"""Dry-run refresh of evidence metrics across video/social platforms."""

from __future__ import annotations

from stdout_utils import out

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


ARTIFACT_DIR = Path("artifacts")
BAD_URL_IDS = {61, 115, 263, 266, 310, 348, 376, 391, 417, 504, 507, 523, 596}
ACTORS = {
    "youtube": os.getenv("APIFY_YOUTUBE_ACTOR_ID") or "streamers/youtube-scraper",
    "tiktok": os.getenv("APIFY_TIKTOK_ACTOR_ID") or "clockworks/tiktok-scraper",
    "instagram": os.getenv("APIFY_INSTAGRAM_ACTOR_ID") or "apify/instagram-scraper",
    "facebook": os.getenv("APIFY_FACEBOOK_ACTOR_ID") or "apify/facebook-posts-scraper",
}


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = int(value)
        return value if value >= 0 else None
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"none", "null", "nan", "hidden"}:
        return None
    match = re.match(r"^(-?[0-9]*\.?[0-9]+)\s*([kKmMbB])?$", text)
    if not match:
        return None
    number = float(match.group(1))
    if number < 0:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
        (match.group(2) or "").lower(),
        1,
    )
    return int(number * multiplier)


def pick(source: dict[str, Any], paths: list[str]) -> Any:
    for path in paths:
        current: Any = source
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and current not in (None, ""):
            return current
    return None


def youtube_id(url: str) -> str | None:
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


def tiktok_id(url: str) -> str | None:
    match = re.search(r"/video/(\d+)", urllib.parse.urlparse(url).path)
    return match.group(1) if match else None


def instagram_shortcode(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if "instagram.com" not in parsed.netloc.lower():
        return None
    match = re.search(r"/(?:p|reel|tv)/([^/?#]+)/?", parsed.path)
    return match.group(1) if match else None


def facebook_post_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if "fb.watch" in host:
        return parsed.path.strip("/") or None
    if "facebook.com" not in host:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("story_fbid", "fbid", "v"):
        if query.get(key):
            return query[key][0]
    for pattern in (r"/posts/([^/?#]+)", r"/videos/([^/?#]+)", r"/reel/([^/?#]+)", r"/watch/([^/?#]+)"):
        match = re.search(pattern, parsed.path)
        if match:
            return match.group(1)
    return None


def classify_url(url: str) -> tuple[str | None, str | None]:
    if youtube_id(url):
        return "youtube", f"youtube:{youtube_id(url)}"
    if tiktok_id(url):
        return "tiktok", f"tiktok:{tiktok_id(url)}"
    if instagram_shortcode(url):
        return "instagram", f"instagram:{instagram_shortcode(url)}"
    if facebook_post_id(url):
        return "facebook", f"facebook:{facebook_post_id(url)}"
    return None, None


def normalize_instagram_url(url: str) -> str:
    shortcode = instagram_shortcode(url)
    if "/reel/" in urllib.parse.urlparse(url).path:
        return f"https://www.instagram.com/reel/{shortcode}/"
    return f"https://www.instagram.com/p/{shortcode}/"


def load_rows(conn) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, COALESCE(platform, '') AS platform, evidence_type, content_url,
                   view_count, like_count, comment_count, share_count
            FROM vkpi_kol_video_evidence
            WHERE content_url IS NOT NULL
              AND COALESCE(evidence_type, 'video') <> 'media_article'
            ORDER BY id
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT COUNT(*) FILTER (WHERE view_count IS NOT NULL),
                   COALESCE(SUM(view_count), 0)
            FROM vkpi_kol_video_evidence
            """
        )
        global_stats = cur.fetchone()
    values = list(global_stats.values()) if isinstance(global_stats, dict) else global_stats
    return rows, (int(values[0] or 0), int(values[1] or 0))


def run_input(platform: str, urls: list[str]) -> dict[str, Any]:
    if platform == "youtube":
        return {"startUrls": [{"url": url} for url in urls], "maxResults": len(urls)}
    if platform == "tiktok":
        return {
            "postURLs": urls,
            "resultsPerPage": len(urls),
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }
    if platform == "instagram":
        return {
            "directUrls": [normalize_instagram_url(url) for url in urls],
            "resultsType": "posts",
            "resultsLimit": len(urls),
        }
    return {"startUrls": [{"url": url} for url in urls], "resultsLimit": len(urls)}


def usage(run: dict[str, Any]) -> dict[str, Any]:
    use = run.get("usage") or {}
    return {
        "usageTotalUsd": float(use.get("usageTotalUsd") or run.get("usageTotalUsd") or 0),
        "computeUnits": (use.get("stats") or run.get("stats") or {}).get("computeUnits"),
    }


def map_youtube(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_key": classify_url(item.get("url") or item.get("input") or "")[1],
        "returned_url": item.get("url") or item.get("input"),
        "view_count": to_int(pick(item, ["viewCount", "views", "view_count"])),
        "like_count": to_int(pick(item, ["likes", "likeCount", "like_count"])),
        "comment_count": to_int(pick(item, ["commentsCount", "commentCount", "comments"])),
        "share_count": None,
        "content_kind": item.get("type") or "video",
    }


def map_tiktok(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_key": classify_url(item.get("webVideoUrl") or item.get("submittedVideoUrl") or "")[1],
        "returned_url": item.get("webVideoUrl") or item.get("submittedVideoUrl"),
        "view_count": to_int(pick(item, ["playCount", "viewCount", "views"])),
        "like_count": to_int(pick(item, ["diggCount", "likeCount", "likes"])),
        "comment_count": to_int(pick(item, ["commentCount", "commentsCount", "comments"])),
        "share_count": to_int(pick(item, ["shareCount", "shares"])),
        "content_kind": "video",
    }


def map_instagram(item: dict[str, Any]) -> dict[str, Any]:
    url = item.get("url") or item.get("inputUrl") or ""
    shortcode = item.get("shortCode") or item.get("shortcode") or instagram_shortcode(url)
    typename = str(item.get("typeName") or item.get("type") or item.get("__typename") or "")
    is_video = bool(item.get("videoUrl") or item.get("videoViewCount") is not None or "video" in typename.lower())
    return {
        "match_key": f"instagram:{shortcode}" if shortcode else classify_url(url)[1],
        "returned_url": url,
        "view_count": to_int(pick(item, ["videoViewCount", "videoPlayCount", "viewCount", "viewsCount"])),
        "like_count": to_int(pick(item, ["likesCount", "likeCount", "likes"])),
        "comment_count": to_int(pick(item, ["commentsCount", "commentCount", "comments"])),
        "share_count": None,
        "content_kind": "reel_or_video" if is_video else "image_or_carousel",
    }


def map_facebook(item: dict[str, Any]) -> dict[str, Any]:
    url = item.get("url") or item.get("postUrl") or item.get("facebookUrl") or item.get("inputUrl") or ""
    match_url = item.get("facebookUrl") or url
    return {
        "match_key": classify_url(match_url)[1],
        "returned_url": url,
        "view_count": to_int(pick(item, ["viewsCount", "videoViewCount", "viewCount", "views"])),
        "like_count": to_int(pick(item, ["likesCount", "likeCount", "likes", "reactionsCount"])),
        "comment_count": to_int(pick(item, ["commentsCount", "commentCount", "comments"])),
        "share_count": to_int(pick(item, ["sharesCount", "shareCount", "shares"])),
        "content_kind": "facebook_post",
    }


MAPPERS = {
    "youtube": map_youtube,
    "tiktok": map_tiktok,
    "instagram": map_instagram,
    "facebook": map_facebook,
}


def call_actor(client: ApifyClient, platform: str, batch: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    urls = [row["content_url"] for row in batch]
    started = time.time()
    try:
        run = client.actor(ACTORS[platform]).call(run_input=run_input(platform, urls), timeout_secs=900)
        dataset_id = run.get("defaultDatasetId")
        items = client.dataset(dataset_id).list_items(limit=max(1000, len(urls) + 20)).items if dataset_id else []
        mapped = [MAPPERS[platform](dict(item)) for item in items]
        info = {
            "platform": platform,
            "actor": ACTORS[platform],
            "run_id": run.get("id"),
            "status": run.get("status"),
            "input_count": len(urls),
            "item_count": len(items),
            "duration_sec": round(time.time() - started, 2),
            "error": "",
            **usage(run),
        }
        return mapped, info
    except Exception as exc:
        if len(batch) > 10:
            all_mapped: list[dict[str, Any]] = []
            infos: list[dict[str, Any]] = []
            for index in range(0, len(batch), 10):
                mapped, info = call_actor(client, platform, batch[index : index + 10])
                all_mapped.extend(mapped)
                infos.append(info)
            return all_mapped, {
                "platform": platform,
                "actor": ACTORS[platform],
                "run_id": "split-after-error",
                "status": "SPLIT",
                "input_count": len(urls),
                "item_count": sum(info.get("item_count", 0) for info in infos),
                "duration_sec": round(sum(info.get("duration_sec", 0) for info in infos), 2),
                "usageTotalUsd": round(sum(info.get("usageTotalUsd", 0) for info in infos), 4),
                "computeUnits": None,
                "error": str(exc)[:300],
            }
        return [], {
            "platform": platform,
            "actor": ACTORS[platform],
            "run_id": "",
            "status": "ERROR",
            "input_count": len(urls),
            "item_count": 0,
            "duration_sec": round(time.time() - started, 2),
            "usageTotalUsd": 0,
            "computeUnits": None,
            "error": str(exc)[:300],
        }


def status_for(row: dict[str, Any], mapped: dict[str, Any] | None) -> str:
    if row["id"] in BAD_URL_IDS:
        return "skipped_known_bad_url"
    if not row.get("match_key"):
        return "bad_url"
    if not mapped:
        return "failed_no_return"
    old = {field: row.get(field) for field in ("view_count", "like_count", "comment_count")}
    new = {field: mapped.get(field) for field in ("view_count", "like_count", "comment_count")}
    if all(value is None for value in new.values()):
        return "returned_no_metrics"
    if any(old[field] is None and new[field] is not None for field in old):
        return "补齐"
    changed = [field.replace("_count", "") for field in old if old[field] is not None and new[field] is not None and int(old[field]) != int(new[field])]
    return "数字变化:" + ",".join(changed) if changed else "对齐"


def write_outputs(rows: list[dict[str, Any]], runs: list[dict[str, Any]], global_stats: tuple[int, int]) -> tuple[Path, Path]:
    ARTIFACT_DIR.mkdir(exist_ok=True)
    current = stamp()
    csv_path = ARTIFACT_DIR / f"all_platform_metrics_dryrun_{current}.csv"
    report_path = ARTIFACT_DIR / f"all_platform_metrics_report_{current}.md"
    fields = [
        "id",
        "platform",
        "db_platform",
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
        "content_kind",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in rows)

    old_with_view, old_exposure = global_stats
    old_scope_with_view = sum(1 for row in rows if row["old_view_count"] is not None)
    new_scope_with_view = sum(1 for row in rows if row["new_view_count"] is not None)
    old_scope_sum = sum(int(row["old_view_count"] or 0) for row in rows)
    new_scope_sum = sum(int(row["new_view_count"] or row["old_view_count"] or 0) for row in rows)
    predicted_with_view = old_with_view - old_scope_with_view + new_scope_with_view
    predicted_exposure = old_exposure - old_scope_sum + new_scope_sum
    total_cost = sum(float(run.get("usageTotalUsd") or 0) for run in runs)
    total_duration = sum(float(run.get("duration_sec") or 0) for run in runs)
    by_platform: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_platform[row["platform"]][row["status"].split(":")[0]] += 1
    match_failures = sum(1 for row in rows if row["status"] == "failed_no_return")

    lines = [
        "# All Platform Evidence Metrics Dry-Run",
        "",
        "## 全局",
        f"- scope rows: {len(rows)}",
        f"- view_count 覆盖率预测: {old_with_view} -> {predicted_with_view}",
        f"- Total Exposure 预测: {old_exposure:,} -> {predicted_exposure:,}",
        f"- URL match 失败数: {match_failures}",
        f"- Apify runs: {len(runs)}",
        f"- 总 cost: ${total_cost:.4f}",
        f"- 总耗时: {total_duration:.2f}s",
        "",
        "## 平台分段",
    ]
    for platform in ("youtube", "tiktok", "instagram", "facebook"):
        platform_rows = [row for row in rows if row["platform"] == platform]
        if not platform_rows:
            continue
        kind_counter = Counter(row.get("content_kind") or "unknown" for row in platform_rows)
        view_rows = sum(1 for row in platform_rows if row["new_view_count"] is not None)
        like_rows = sum(1 for row in platform_rows if row["new_like_count"] is not None)
        comment_rows = sum(1 for row in platform_rows if row["new_comment_count"] is not None)
        lines += [
            f"### {platform}",
            f"- 抓取/评估: {len(platform_rows)}",
            f"- 状态: {dict(by_platform[platform])}",
            f"- 新 view/like/comment 有值: {view_rows}/{like_rows}/{comment_rows}",
            f"- content kind: {dict(kind_counter)}",
        ]
        failures = [row for row in platform_rows if row["status"] not in {"对齐", "补齐"} and not row["status"].startswith("数字变化")]
        if failures:
            lines.append("- 失败/bad_url:")
            for row in failures[:60]:
                lines.append(f"  - {row['id']} {row['status']} {row['content_url']}")
        else:
            lines.append("- 失败/bad_url: 无")
    lines += [
        "",
        "## Run 明细",
        "| platform | run_id | status | input | returned | duration | cost | error |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for run in runs:
        lines.append(
            f"| {run['platform']} | {run.get('run_id')} | {run.get('status')} | "
            f"{run.get('input_count')} | {run.get('item_count')} | {run.get('duration_sec')}s | "
            f"${float(run.get('usageTotalUsd') or 0):.4f} | {run.get('error') or ''} |"
        )
    lines += [
        "",
        "## IG/FB 首验评估",
        "- Instagram: 如果 post/reel 返回 likes/comments，图文 view 为 NULL 属正常；reel/video 有 videoViewCount/videoPlayCount 才能补 view。",
        "- Facebook: view 字段不稳定，commit 前重点看 likes/comments 是否稳定返回，以及 URL key 是否能 match。",
    ]
    report_path.write_text("\n".join(lines) + "\n")
    return csv_path, report_path


def main() -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    rows, global_stats = load_rows(conn)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        platform, key = classify_url(row["content_url"])
        if platform not in ACTORS:
            continue
        candidate = {
            "id": row["id"],
            "platform": platform,
            "db_platform": row["platform"],
            "content_url": row["content_url"],
            "old_view_count": row["view_count"],
            "old_like_count": row["like_count"],
            "old_comment_count": row["comment_count"],
            "match_key": key,
        }
        candidates.append(candidate)

    client = ApifyClient(os.getenv("APIFY_API_TOKEN") or os.getenv("APIFY_TOKEN"))
    runs: list[dict[str, Any]] = []
    mapped_by_platform: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for platform in ("youtube", "tiktok", "instagram", "facebook"):
        platform_candidates = [row for row in candidates if row["platform"] == platform and row["id"] not in BAD_URL_IDS]
        for index in range(0, len(platform_candidates), 50):
            batch = platform_candidates[index : index + 50]
            if not batch:
                continue
            out(f"[{platform}] batch {index // 50 + 1}: {len(batch)} urls", flush=True)
            mapped, info = call_actor(client, platform, batch)
            runs.append(info)
            for item in mapped:
                if item.get("match_key"):
                    mapped_by_platform[platform][item["match_key"]] = item

    output_rows: list[dict[str, Any]] = []
    for row in candidates:
        mapped = mapped_by_platform[row["platform"]].get(row["match_key"])
        output_rows.append(
            {
                **row,
                "new_view_count": mapped.get("view_count") if mapped else None,
                "new_like_count": mapped.get("like_count") if mapped else None,
                "new_comment_count": mapped.get("comment_count") if mapped else None,
                "new_share_count": mapped.get("share_count") if mapped else None,
                "returned_url": mapped.get("returned_url") if mapped else None,
                "content_kind": mapped.get("content_kind") if mapped else "",
                "status": status_for(row, mapped),
            }
        )
    csv_path, report_path = write_outputs(output_rows, runs, global_stats)
    out(f"CSV={csv_path}")
    out(f"REPORT={report_path}")
    out(report_path.read_text())


if __name__ == "__main__":
    main()
