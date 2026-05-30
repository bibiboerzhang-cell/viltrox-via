#!/usr/bin/env python3
"""Matti Haapoja 3-platform x 4 benchmark.

This script is intentionally cache/report-only. It uses the existing crawler
and AI service modules, but does not call the audit pipeline paths that write
submissions, creator profiles, or vkpi_kol_pool.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file(REPO_ROOT / ".env")
_load_env_file(BACKEND_ROOT / ".env")

from app.services.ai import orchestrator as orchestrator_mod  # noqa: E402
from app.services.ai.analyzers.claude_text import analyze_text_content  # noqa: E402
from app.services.ai.analyzers.gemini_video import analyze_youtube_with_gemini  # noqa: E402
from app.services.audit.similarity import analyze_comments_for_spam  # noqa: E402
from app.services.scraping.ytdlp_enhanced import YTDLP_AVAILABLE, YTDLP_BIN  # noqa: E402
from app.services.vkpi import comment_intelligence_rules as ci_rules  # noqa: E402
from app.services.vkpi.industry_crawlers.instagram_crawler import InstagramCrawler  # noqa: E402
from app.services.vkpi.industry_crawlers.tiktok_crawler import TikTokCrawler  # noqa: E402
from app.services.vkpi.industry_crawlers.youtube_crawler import YouTubeCrawler  # noqa: E402


try:
    from dateutil import parser as date_parser  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    date_parser = None


TARGETS = {
    "youtube": {
        "label": "YT",
        "platform_label": "YouTube",
        "handle": "MattiHaapoja",
        "url": "https://youtube.com/@MattiHaapoja",
    },
    "instagram": {
        "label": "IG",
        "platform_label": "Instagram",
        "handle": "mattih",
        "url": "https://instagram.com/mattih",
    },
    "tiktok": {
        "label": "TT",
        "platform_label": "TikTok",
        "handle": "mattihaapoja",
        "url": "https://www.tiktok.com/@mattihaapoja",
    },
}

RUN_MATRIX = [
    (1, "youtube", "recent", "basic"),
    (2, "youtube", "global", "basic"),
    (3, "youtube", "recent", "deep"),
    (4, "youtube", "global", "deep"),
    (5, "instagram", "recent", "basic"),
    (6, "instagram", "global", "basic"),
    (7, "instagram", "recent", "deep"),
    (8, "instagram", "global", "deep"),
    (9, "tiktok", "recent", "basic"),
    (10, "tiktok", "global", "basic"),
    (11, "tiktok", "recent", "deep"),
    (12, "tiktok", "global", "deep"),
]


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def to_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(str(value).replace(",", "")))
    except Exception:
        return 0


def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds = seconds / 1000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 8:
        try:
            return datetime(int(text[:4]), int(text[4:6]), int(text[6:8]), tzinfo=timezone.utc)
        except Exception:
            return None
    if text.isdigit():
        return parse_dt(int(text))
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    if date_parser is not None:
        try:
            dt = date_parser.parse(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def parse_duration_seconds(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    if text.startswith("PT"):
        match = re.fullmatch(
            r"PT(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<m>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?",
            text,
        )
        if match:
            hours = float(match.group("h") or 0)
            minutes = float(match.group("m") or 0)
            seconds = float(match.group("s") or 0)
            return hours * 3600 + minutes * 60 + seconds
    if ":" in text:
        try:
            parts = [float(part) for part in text.split(":")]
            total = 0.0
            for part in parts:
                total = total * 60 + part
            return total
        except Exception:
            return None
    return None


def fmt_seconds(value: Any) -> str:
    if value is None:
        return "-"
    try:
        seconds = float(value)
    except Exception:
        return "-"
    if seconds >= 600:
        return f"{seconds / 60:.1f}min"
    return f"{seconds:.1f}s"


def fmt_minutes(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value) / 60:.1f}min"
    except Exception:
        return "-"


def fmt_money(value: Any) -> str:
    try:
        return f"${float(value):.3f}"
    except Exception:
        return "$-"


def mean(values: list[float]) -> float | None:
    values = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not values:
        return None
    return sum(values) / len(values)


def item_key(item: dict[str, Any], index: int = 0) -> str:
    return first_text(item.get("source_id"), item.get("url"), item.get("id"), f"idx-{index}")


def make_run_result(run_no: int, platform: str, window: str, mode: str) -> dict[str, Any]:
    target = TARGETS[platform]
    return {
        "run_no": run_no,
        "platform": platform,
        "platform_label": target["label"],
        "window": window,
        "mode": mode,
        "started_at": now_iso(),
        "finished_at": "",
        "status": "OK",
        "errors": [],
        "warnings": [],
        "phases": {
            "list_seconds": None,
            "comments_seconds": None,
            "gemini_seconds": None,
            "comment_auth_seconds": None,
            "claude_seconds": None,
        },
        "api_calls": {},
        "video_count": 0,
        "avg_duration_seconds": None,
        "basic_total_seconds": None,
        "deep_total_seconds": None,
        "total_seconds": None,
        "estimated_cost_usd": 0.0,
        "item_ops": 0,
        "item_failures": 0,
        "items": [],
        "comments_by_item": {},
        "deep_results": {},
        "cache_path": "",
        "detail_path": "",
    }


def add_error(run: dict[str, Any], message: str, *, item_failure: bool = False) -> None:
    run["errors"].append(str(message)[:1000])
    if item_failure:
        run["item_failures"] = int(run.get("item_failures") or 0) + 1


def add_warning(run: dict[str, Any], message: str) -> None:
    run["warnings"].append(str(message)[:1000])


def add_api_call(run: dict[str, Any], key: str, count: int = 1) -> None:
    calls = run.setdefault("api_calls", {})
    calls[key] = int(calls.get(key) or 0) + int(count)


def estimate_run_cost(run: dict[str, Any]) -> float:
    calls = run.get("api_calls") or {}
    # Rough local benchmark estimate. Provider billing exports are the source of truth.
    cost = 0.0
    cost += float(calls.get("apify_actor", 0)) * 0.001
    cost += float(calls.get("youtube_data_api", 0)) * 0.0
    cost += float(calls.get("yt_dlp", 0)) * 0.0
    cost += float(calls.get("gemini_video", 0)) * 0.010
    cost += float(calls.get("claude_text", 0)) * 0.006
    return round(cost, 4)


def normalize_youtube_item(item: dict[str, Any]) -> dict[str, Any]:
    raw_id = item.get("id")
    video_id = ""
    if isinstance(raw_id, dict):
        video_id = first_text(raw_id.get("videoId"), raw_id.get("id"))
    else:
        video_id = first_text(raw_id, item.get("videoId"))
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    thumbnail = ""
    for key in ("maxres", "high", "medium", "default"):
        thumb = thumbnails.get(key) if isinstance(thumbnails.get(key), dict) else {}
        thumbnail = first_text(thumbnail, thumb.get("url"))
    url = first_text(item.get("url"), item.get("webpage_url"), f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
    published = first_text(snippet.get("publishedAt"), item.get("publishedAt"), item.get("date"), item.get("uploadDate"), item.get("published"))
    return {
        "platform": "youtube",
        "source_id": video_id,
        "url": url,
        "title": first_text(snippet.get("title"), item.get("title")),
        "caption": first_text(snippet.get("description"), item.get("description"), item.get("text")),
        "published_at": published,
        "published_ts": parse_dt(published).isoformat() if parse_dt(published) else "",
        "views": to_int(stats.get("viewCount") or item.get("viewCount") or item.get("views")),
        "likes": to_int(stats.get("likeCount") or item.get("likes") or item.get("likeCount")),
        "comments": to_int(stats.get("commentCount") or item.get("commentsCount") or item.get("comments")),
        "shares": to_int(item.get("shares")),
        "thumbnail": first_text(thumbnail, item.get("thumbnailUrl"), item.get("thumbnail")),
        "duration_seconds": parse_duration_seconds(first_text(details.get("duration"), item.get("duration"), item.get("durationSeconds"), item.get("duration_seconds"))),
        "content_type": "video",
    }


def normalize_instagram_item(item: dict[str, Any]) -> dict[str, Any]:
    short_code = first_text(item.get("shortCode"), item.get("shortcode"), item.get("code"))
    url = first_text(item.get("url"), f"https://www.instagram.com/p/{short_code}/" if short_code else "")
    is_video = bool(item.get("isVideo")) or bool(item.get("videoUrl")) or "/reel/" in url
    published = first_text(item.get("timestamp"), item.get("takenAtTimestamp"), item.get("createdAt"))
    return {
        "platform": "instagram",
        "source_id": first_text(short_code, item.get("id")),
        "url": url,
        "title": first_text(item.get("caption"))[:300],
        "caption": first_text(item.get("caption")),
        "published_at": published,
        "published_ts": parse_dt(published).isoformat() if parse_dt(published) else "",
        "views": to_int(item.get("videoViewCount") or item.get("videoPlayCount") or item.get("viewsCount")),
        "likes": to_int(item.get("likesCount") or item.get("likes")),
        "comments": to_int(item.get("commentsCount") or item.get("comments")),
        "shares": to_int(item.get("sharesCount") or item.get("shares")),
        "thumbnail": first_text(item.get("displayUrl"), item.get("thumbnailUrl"), item.get("imageUrl")),
        "duration_seconds": parse_duration_seconds(item.get("videoDuration") or item.get("duration")),
        "content_type": "video" if is_video else "image",
        "video_url": first_text(item.get("videoUrl")),
    }


def normalize_tiktok_item(item: dict[str, Any]) -> dict[str, Any]:
    author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    video = item.get("videoMeta") if isinstance(item.get("videoMeta"), dict) else {}
    covers = item.get("covers") if isinstance(item.get("covers"), list) else []
    published = first_text(item.get("createTimeISO"), item.get("createTime"), item.get("createdAt"))
    url = first_text(item.get("webVideoUrl"), item.get("url"))
    return {
        "platform": "tiktok",
        "source_id": first_text(item.get("id"), item.get("videoId")),
        "url": url,
        "title": first_text(item.get("text"), item.get("desc"), item.get("title"))[:300],
        "caption": first_text(item.get("text"), item.get("desc"), item.get("title")),
        "published_at": published,
        "published_ts": parse_dt(published).isoformat() if parse_dt(published) else "",
        "views": to_int(item.get("playCount") or stats.get("playCount")),
        "likes": to_int(item.get("diggCount") or stats.get("diggCount")),
        "comments": to_int(item.get("commentCount") or stats.get("commentCount")),
        "shares": to_int(item.get("shareCount") or stats.get("shareCount")),
        "thumbnail": first_text(video.get("coverUrl"), item.get("cover"), item.get("thumbnail"), covers[0] if covers else ""),
        "duration_seconds": parse_duration_seconds(video.get("duration") or item.get("duration")),
        "content_type": "video",
        "author_handle": first_text(author.get("name"), item.get("author")),
        "author_name": first_text(author.get("nickName"), item.get("authorName")),
    }


def normalize_item(platform: str, item: dict[str, Any]) -> dict[str, Any]:
    if platform == "youtube":
        return normalize_youtube_item(item)
    if platform == "instagram":
        return normalize_instagram_item(item)
    if platform == "tiktok":
        return normalize_tiktok_item(item)
    raise ValueError(f"unsupported platform={platform}")


def normalize_comment(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"text": str(item or "")[:1000], "author": "", "likes": 0, "published_at": ""}
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    text = first_text(
        item.get("text"),
        item.get("comment"),
        item.get("message"),
        item.get("commentText"),
        item.get("textOriginal"),
        item.get("textDisplay"),
        snippet.get("textOriginal"),
        snippet.get("textDisplay"),
    )
    return {
        "text": text[:1000],
        "author": first_text(
            item.get("author"),
            item.get("username"),
            item.get("ownerUsername"),
            item.get("authorUsername"),
            snippet.get("authorDisplayName"),
            snippet.get("authorChannelUrl"),
        ),
        "likes": to_int(item.get("likes") or item.get("likeCount") or item.get("likesCount") or snippet.get("likeCount")),
        "published_at": first_text(item.get("publishedAt"), item.get("createdAt"), item.get("timestamp"), snippet.get("publishedAt")),
    }


def filter_window(items: list[dict[str, Any]], window: str, cutoff: datetime) -> list[dict[str, Any]]:
    sortable: list[tuple[datetime | None, dict[str, Any]]] = []
    for item in items:
        dt = parse_dt(item.get("published_ts") or item.get("published_at"))
        if window == "recent" and (not dt or dt < cutoff):
            continue
        sortable.append((dt, item))
    sortable.sort(key=lambda pair: pair[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return [item for _, item in sortable]


def item_rows_md(items: list[dict[str, Any]], limit: int = 30) -> str:
    rows = ["| # | Published | Type | Title | Views | Likes | Comments | Duration |", "|---:|---|---|---|---:|---:|---:|---:|"]
    for idx, item in enumerate(items[:limit], 1):
        title = first_text(item.get("title"), item.get("caption")).replace("|", "\\|")[:90]
        rows.append(
            f"| {idx} | {first_text(item.get('published_ts'), item.get('published_at'))[:19]} | "
            f"{item.get('content_type') or '-'} | {title} | {item.get('views') or 0} | "
            f"{item.get('likes') or 0} | {item.get('comments') or 0} | {fmt_seconds(item.get('duration_seconds'))} |"
        )
    if len(items) > limit:
        rows.append(f"| ... | ... | ... | {len(items) - limit} more items omitted | | | | |")
    return "\n".join(rows)


class Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.ts = args.ts or utc_ts()
        self.bench_dir = Path(args.bench_dir).expanduser().resolve()
        self.cache_dir = self.bench_dir / "cache"
        self.detail_dir = self.bench_dir / f"{self.ts}_matti_run_details"
        self.log_path = self.bench_dir / f"{self.ts}_matti_run_log.md"
        self.compare_path = self.bench_dir / f"{self.ts}_matti_compare.md"
        self.detail_index_path = self.bench_dir / f"{self.ts}_matti_run_details_index.md"
        self.error_path = self.bench_dir / f"{self.ts}_matti_errors.md"
        self.summary_json_path = self.cache_dir / f"{self.ts}_matti_summary.json"
        self.cutoff = datetime.now(timezone.utc) - timedelta(days=args.recent_days)
        self.results: list[dict[str, Any]] = []
        self.prep: dict[str, Any] = {}
        for path in (self.bench_dir, self.cache_dir, self.detail_dir):
            path.mkdir(parents=True, exist_ok=True)

    def append_log(self, text: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n\n")

    def write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def init_reports(self) -> None:
        header = (
            f"# Matti Haapoja 3x4 Benchmark Run Log\n\n"
            f"- Started: {now_iso()}\n"
            f"- Repo: `{REPO_ROOT}`\n"
            f"- Benchmark dir: `{self.bench_dir}`\n"
            f"- Recent cutoff: `{self.cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}`\n"
            f"- Limits: global={self.args.max_global}, comments/video={self.args.comment_limit}\n"
            f"- Execution: serial, cache/report only\n"
        )
        self.log_path.write_text(header + "\n", encoding="utf-8")

    def profile_summary(self, platform: str, payload: dict[str, Any]) -> dict[str, Any]:
        items = payload.get("items") or []
        first = items[0] if items and isinstance(items[0], dict) else {}
        if platform == "youtube":
            snippet = first.get("snippet") if isinstance(first.get("snippet"), dict) else {}
            stats = first.get("statistics") if isinstance(first.get("statistics"), dict) else {}
            return {
                "id": first_text(first.get("id")),
                "name": first_text(snippet.get("title"), first.get("title"), first.get("name")),
                "url": first_text(first.get("url"), first.get("profile_url"), TARGETS[platform]["url"]),
                "followers": to_int(stats.get("subscriberCount") or first.get("subscriber_count") or first.get("numberOfSubscribers")),
                "following": None,
                "bio": first_text(snippet.get("description"), first.get("description"))[:500],
                "item_count": len(items),
                "provider_status": first_text(payload.get("provider_status"), payload.get("sync_status")),
            }
        if platform == "instagram":
            return {
                "id": first_text(first.get("id"), first.get("username"), TARGETS[platform]["handle"]),
                "name": first_text(first.get("fullName"), first.get("full_name"), first.get("username")),
                "url": first_text(first.get("url"), TARGETS[platform]["url"]),
                "followers": to_int(first.get("followersCount") or first.get("followers")),
                "following": to_int(first.get("followsCount") or first.get("following")),
                "bio": first_text(first.get("biography"), first.get("bio"))[:500],
                "item_count": len(items),
                "provider_status": first_text(payload.get("provider_status"), payload.get("sync_status")),
            }
        author = first.get("authorMeta") if isinstance(first.get("authorMeta"), dict) else {}
        return {
            "id": first_text(author.get("id"), author.get("name"), first.get("author"), TARGETS[platform]["handle"]),
            "name": first_text(author.get("nickName"), first.get("authorName"), author.get("name")),
            "url": TARGETS[platform]["url"],
            "followers": to_int(author.get("fans") or author.get("followerCount") or first.get("authorStats.followerCount")),
            "following": to_int(author.get("following") or author.get("followingCount")),
            "bio": first_text(author.get("signature"), first.get("signature"))[:500],
            "item_count": len(items),
            "provider_status": first_text(payload.get("provider_status"), payload.get("sync_status")),
            "observed_handle": first_text(author.get("name"), first.get("author")),
        }

    def resolve_youtube_profile(self, crawler: YouTubeCrawler) -> dict[str, Any]:
        """Resolve the requested handle to the actual Matti channel when needed.

        YouTube Data API can return a low-subscriber duplicate for the literal
        @MattiHaapoja handle. Search results identify the real channel with the
        Learn, Make, Repeat description and materially larger subscriber count.
        """
        initial = crawler.crawl_channel_profile(TARGETS["youtube"]["url"], max_posts=1)
        summary = self.profile_summary("youtube", initial)
        if int(summary.get("followers") or 0) >= 10_000:
            return initial

        search = crawler.search_channel_by_name("Matti Haapoja", max_results=5)
        candidates: list[dict[str, Any]] = []
        for item in search.get("items") or []:
            channel_id = ""
            raw_id = item.get("id")
            if isinstance(raw_id, dict):
                channel_id = first_text(raw_id.get("channelId"))
            else:
                channel_id = first_text(raw_id)
            if not channel_id:
                continue
            profile = crawler.crawl_channel_profile("", channel_id=channel_id, max_posts=1)
            candidate_summary = self.profile_summary("youtube", profile)
            candidates.append({"profile": profile, "summary": candidate_summary})
        if not candidates:
            initial.setdefault("benchmark_warning", "YouTube handle lookup looked suspicious, but search returned no candidate.")
            return initial
        candidates.sort(key=lambda row: int((row.get("summary") or {}).get("followers") or 0), reverse=True)
        best = candidates[0]
        if int((best.get("summary") or {}).get("followers") or 0) > int(summary.get("followers") or 0):
            corrected = dict(best["profile"])
            corrected["benchmark_warning"] = (
                "Requested URL handle resolved to a low-subscriber duplicate; "
                f"benchmark corrected to channel_id={best['summary'].get('id')} via YouTube search."
            )
            corrected["benchmark_initial_profile"] = summary
            return corrected
        initial.setdefault("benchmark_warning", "YouTube handle lookup looked suspicious, but no better search candidate was found.")
        return initial

    def prep_profiles(self) -> None:
        started = time.perf_counter()
        self.append_log("## Prep\n\nProfile sanity check started. Prep time is not included in the 12 run timers.")
        yt = YouTubeCrawler(timeout_seconds=30, run_timeout_seconds=600)
        ig = InstagramCrawler(run_timeout_seconds=600)
        tt = TikTokCrawler(run_timeout_seconds=600)
        prep_payloads: dict[str, Any] = {}
        calls = [
            ("youtube", lambda: self.resolve_youtube_profile(yt)),
            ("instagram", lambda: ig.crawl_channel_profile(TARGETS["instagram"]["handle"], max_posts=1)),
            ("tiktok", lambda: tt.crawl_channel_profile(TARGETS["tiktok"]["handle"], max_posts=2)),
        ]
        for platform, fn in calls:
            t0 = time.perf_counter()
            try:
                payload = fn()
                elapsed = time.perf_counter() - t0
                summary = self.profile_summary(platform, payload)
                prep_payloads[platform] = {"elapsed_seconds": elapsed, "summary": summary, "raw": payload}
                items = payload.get("items") or []
                status = first_text(payload.get("provider_status"), payload.get("sync_status"))
                if not items and status not in {"ok", "synced"}:
                    raise RuntimeError(f"{platform} prep failed: status={status} error={payload.get('error') or payload.get('message')}")
                if platform == "tiktok":
                    observed = first_text(summary.get("observed_handle"))
                    expected = TARGETS["tiktok"]["handle"]
                    if observed and observed.lower().lstrip("@") != expected.lower().lstrip("@"):
                        raise RuntimeError(f"TikTok handle mismatch: requested @{expected}, actor returned @{observed}")
                if platform == "youtube" and payload.get("benchmark_warning"):
                    prep_payloads[platform]["warning"] = payload.get("benchmark_warning")
                print(f"prep {platform}: {elapsed:.1f}s status={status} items={len(items)}", flush=True)
            except Exception as exc:
                prep_payloads[platform] = {"elapsed_seconds": time.perf_counter() - t0, "error": str(exc)}
                self.prep = prep_payloads
                self.write_json(self.cache_dir / f"{self.ts}_matti_prep.json", prep_payloads)
                self.append_log(f"Prep failed on `{platform}`: `{exc}`")
                raise
        self.prep = prep_payloads
        self.write_json(self.cache_dir / f"{self.ts}_matti_prep.json", prep_payloads)
        elapsed = time.perf_counter() - started
        rows = ["| Platform | Status | Items | Followers | Handle/ID | Prep time |", "|---|---|---:|---:|---|---:|"]
        for platform, payload in prep_payloads.items():
            summary = payload.get("summary") or {}
            rows.append(
                f"| {TARGETS[platform]['label']} | {summary.get('provider_status') or 'ok'} | "
                f"{summary.get('item_count') or 0} | {summary.get('followers') or 0} | "
                f"{first_text(summary.get('observed_handle'), summary.get('id'))} | {fmt_seconds(payload.get('elapsed_seconds'))} |"
            )
        prep_warnings = [f"- {platform}: {payload.get('warning')}" for platform, payload in prep_payloads.items() if payload.get("warning")]
        block = ["Prep complete.", "", *rows, "", f"Prep total: {fmt_seconds(elapsed)}"]
        if prep_warnings:
            block.extend(["", "Prep warnings:", *prep_warnings])
        self.append_log("\n".join(block))

    def run_ytdlp_flat(self, limit: int) -> dict[str, Any]:
        if not YTDLP_AVAILABLE:
            return {"status": "not_available", "items": [], "error": "yt-dlp not available"}
        profile_summary = ((self.prep.get("youtube") or {}).get("summary") or {})
        channel_id = first_text(profile_summary.get("id"))
        if channel_id.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_id}/videos"
        else:
            url = TARGETS["youtube"]["url"].rstrip("/") + "/videos"
        cmd = [
            YTDLP_BIN,
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-end",
            str(max(1, min(100, limit))),
            "--skip-download",
            "--no-warnings",
            "--quiet",
            url,
        ]
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.args.ytdlp_flat_timeout)
            elapsed = time.perf_counter() - t0
            if proc.returncode != 0 or not proc.stdout.strip():
                return {"status": "error", "items": [], "elapsed_seconds": elapsed, "error": (proc.stderr or "")[:1000]}
            payload = json.loads(proc.stdout)
            entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
            return {"status": "ok", "items": entries, "elapsed_seconds": elapsed, "entry_count": len(entries)}
        except Exception as exc:
            return {"status": "error", "items": [], "elapsed_seconds": time.perf_counter() - t0, "error": str(exc)}

    def fetch_list(self, platform: str, max_items: int, run: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if platform == "youtube":
            ytdlp_payload = self.run_ytdlp_flat(max_items)
            add_api_call(run, "yt_dlp", 1)
            if ytdlp_payload.get("status") != "ok":
                add_warning(run, f"yt-dlp flat list failed: {ytdlp_payload.get('error') or ytdlp_payload.get('status')}")
            profile_summary = ((self.prep.get("youtube") or {}).get("summary") or {})
            channel_id = first_text(profile_summary.get("id"))
            crawler = YouTubeCrawler(timeout_seconds=30, run_timeout_seconds=900)
            payload = crawler.crawl_channel_videos(channel_id or TARGETS["youtube"]["url"], max_results=max_items)
            add_api_call(run, "youtube_data_api", 1)
            raw_items = payload.get("items") or []
            if not raw_items and ytdlp_payload.get("items"):
                raw_items = ytdlp_payload.get("items") or []
                payload = {"provider_status": "yt_dlp_flat_fallback", "items": raw_items, "raw": {"youtube_data_api": payload, "yt_dlp": ytdlp_payload}}
            items = [normalize_item(platform, item) for item in raw_items if isinstance(item, dict)]
            return items, {"provider": payload, "yt_dlp_flat": ytdlp_payload}
        if platform == "instagram":
            crawler = InstagramCrawler(run_timeout_seconds=900)
            payload = crawler.crawl_channel_videos(TARGETS[platform]["handle"], max_results=max_items)
            add_api_call(run, "apify_actor", 1)
            items = [normalize_item(platform, item) for item in (payload.get("items") or []) if isinstance(item, dict)]
            return items, {"provider": payload}
        if platform == "tiktok":
            crawler = TikTokCrawler(run_timeout_seconds=600)
            payload = crawler.crawl_channel_videos(TARGETS[platform]["handle"], max_results=max_items)
            add_api_call(run, "apify_actor", 1)
            items = [normalize_item(platform, item) for item in (payload.get("items") or []) if isinstance(item, dict)]
            return items, {"provider": payload}
        raise ValueError(platform)

    def fetch_comments_for_item(self, platform: str, item: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        target = first_text(item.get("url"), item.get("source_id"))
        if not target:
            return {"provider_status": "missing_url", "items": [], "error": "missing url/source_id"}
        if platform == "youtube":
            crawler = YouTubeCrawler(timeout_seconds=30, run_timeout_seconds=600)
            add_api_call(run, "youtube_data_api", 1)
            return crawler.crawl_video_comments(target, max_results=self.args.comment_limit)
        if platform == "instagram":
            crawler = InstagramCrawler(run_timeout_seconds=600)
            add_api_call(run, "apify_actor", 1)
            return crawler.crawl_video_comments(target, max_results=self.args.comment_limit)
        if platform == "tiktok":
            crawler = TikTokCrawler(run_timeout_seconds=600)
            add_api_call(run, "apify_actor", 1)
            return crawler.crawl_video_comments(target, max_results=self.args.comment_limit)
        raise ValueError(platform)

    def run_basic_steps(self, run: dict[str, Any]) -> dict[str, Any]:
        max_fetch = self.args.max_global
        platform = run["platform"]
        window = run["window"]

        t0 = time.perf_counter()
        items, raw_list = self.fetch_list(platform, max_fetch, run)
        selected = filter_window(items, window, self.cutoff)[: self.args.max_global]
        run["phases"]["list_seconds"] = time.perf_counter() - t0
        run["items"] = selected
        run["video_count"] = len(selected)
        run["avg_duration_seconds"] = mean([float(item["duration_seconds"]) for item in selected if item.get("duration_seconds") is not None])
        if not selected:
            add_error(run, f"no items after {window} filter")

        t0 = time.perf_counter()
        comments_by_item: dict[str, Any] = {}
        for idx, item in enumerate(selected):
            key = item_key(item, idx)
            run["item_ops"] = int(run.get("item_ops") or 0) + 1
            try:
                payload = self.fetch_comments_for_item(platform, item, run)
                status = first_text(payload.get("provider_status"), payload.get("sync_status"))
                raw_comments = payload.get("items") or []
                comments = [normalize_comment(comment) for comment in raw_comments]
                comments = [comment for comment in comments if comment.get("text")]
                comments_by_item[key] = {
                    "status": status or "ok",
                    "declared_count": item.get("comments") or 0,
                    "fetched_count": len(comments),
                    "comments": comments[: self.args.comment_limit],
                    "error": payload.get("error") or payload.get("message") or "",
                }
                if status and status not in {"ok", "synced"} and not comments:
                    add_error(run, f"comments {key}: status={status} error={comments_by_item[key]['error']}", item_failure=True)
            except Exception as exc:
                comments_by_item[key] = {"status": "ERR", "declared_count": item.get("comments") or 0, "fetched_count": 0, "comments": [], "error": str(exc)}
                add_error(run, f"comments {key}: {exc}", item_failure=True)
        run["phases"]["comments_seconds"] = time.perf_counter() - t0
        run["comments_by_item"] = comments_by_item
        run["basic_total_seconds"] = (run["phases"]["list_seconds"] or 0) + (run["phases"]["comments_seconds"] or 0)
        run.setdefault("raw", {})["list"] = raw_list
        return run

    def is_deep_video_candidate(self, platform: str, item: dict[str, Any]) -> bool:
        if platform in {"youtube", "tiktok"}:
            return bool(item.get("url"))
        return item.get("content_type") == "video" and bool(item.get("url") or item.get("video_url"))

    async def run_gemini_phase(self, run: dict[str, Any]) -> None:
        platform = run["platform"]
        handle = TARGETS[platform]["handle"]
        results: dict[str, Any] = {}
        t0 = time.perf_counter()
        for idx, item in enumerate(run.get("items") or []):
            key = item_key(item, idx)
            if not self.is_deep_video_candidate(platform, item):
                results[key] = {"status": "SKIP", "reason": "non_video_or_missing_url"}
                continue
            url = first_text(item.get("url"), item.get("video_url"))
            title = first_text(item.get("title"), item.get("caption"), url)
            run["item_ops"] = int(run.get("item_ops") or 0) + 1
            add_api_call(run, "gemini_video", 1)
            try:
                payload = await asyncio.wait_for(
                    analyze_youtube_with_gemini(url, title, creator_handle=handle),
                    timeout=self.args.gemini_timeout,
                )
                ok = bool(payload.get("analyzed"))
                results[key] = {
                    "status": "OK" if ok else "ERR",
                    "method": payload.get("method"),
                    "analyzed": ok,
                    "error": payload.get("error") or "",
                    "content_genre": payload.get("content_genre") or "",
                    "quality_overall": payload.get("quality_overall") or 0,
                    "marketing_potential": payload.get("marketing_potential") or "",
                }
                if not ok:
                    add_error(run, f"gemini {key}: {payload.get('error') or 'not analyzed'}", item_failure=True)
            except Exception as exc:
                results[key] = {"status": "ERR", "error": f"{type(exc).__name__}: {exc}"}
                add_error(run, f"gemini {key}: {type(exc).__name__}: {exc}", item_failure=True)
        run["phases"]["gemini_seconds"] = time.perf_counter() - t0
        run.setdefault("deep_results", {})["gemini"] = results

    def run_comment_auth_phase(self, run: dict[str, Any]) -> None:
        results: dict[str, Any] = {}
        t0 = time.perf_counter()
        for idx, item in enumerate(run.get("items") or []):
            key = item_key(item, idx)
            comments = ((run.get("comments_by_item") or {}).get(key) or {}).get("comments") or []
            texts = [first_text(comment.get("text")) for comment in comments if first_text(comment.get("text"))]
            spam = analyze_comments_for_spam(texts)
            sentiments = Counter()
            tags = Counter()
            for text in texts:
                sentiments[ci_rules._rule_sentiment(text)] += 1
                for tag in ci_rules._rule_tags(text):
                    tags[tag] += 1
            authentic_rate = 1.0 - float(spam.get("spam_ratio") or 0.0)
            results[key] = {
                "status": "OK",
                "sample_size": len(texts),
                "authentic_rate": round(authentic_rate, 3),
                "spam": spam,
                "sentiments": dict(sentiments),
                "tags": dict(tags),
            }
        run["phases"]["comment_auth_seconds"] = time.perf_counter() - t0
        run.setdefault("deep_results", {})["comment_authenticity"] = results

    async def run_claude_phase(self, run: dict[str, Any]) -> None:
        platform = run["platform"]
        platform_label = TARGETS[platform]["platform_label"]
        handle = TARGETS[platform]["handle"]
        analyzer = orchestrator_mod.ClaudeAnalyzer(analyze_text_content)
        results: dict[str, Any] = {}
        t0 = time.perf_counter()
        for idx, item in enumerate(run.get("items") or []):
            key = item_key(item, idx)
            run["item_ops"] = int(run.get("item_ops") or 0) + 1
            add_api_call(run, "claude_text", 1)
            try:
                job = orchestrator_mod.VideoJobInput(
                    submission_id=0,
                    url=first_text(item.get("url"), item.get("video_url")),
                    title=first_text(item.get("title")),
                    handle=handle,
                    platform=platform_label,
                    caption=first_text(item.get("caption")),
                    scraped_text=first_text(item.get("caption"), item.get("title")),
                    og_image=first_text(item.get("thumbnail")),
                    metrics={
                        "views": to_int(item.get("views")),
                        "likes": to_int(item.get("likes")),
                        "comments": to_int(item.get("comments")),
                        "shares": to_int(item.get("shares")),
                    },
                )
                task = orchestrator_mod.VideoTask(task_id=f"matti-bench-{run['run_no']}-{idx}", job=job)
                outcome = await asyncio.wait_for(analyzer.analyze(task), timeout=self.args.claude_timeout)
                results[key] = {
                    "status": "OK" if outcome.ok else "ERR",
                    "latency_ms": outcome.latency_ms,
                    "error": outcome.error,
                    "content_genre": (outcome.payload or {}).get("content_genre") or "",
                    "quality_overall": (outcome.payload or {}).get("quality_overall") or 0,
                    "marketing_potential": (outcome.payload or {}).get("marketing_potential") or "",
                    "method": (outcome.payload or {}).get("method") or "claude_text",
                }
                if not outcome.ok:
                    add_error(run, f"claude {key}: {outcome.error or 'not analyzed'}", item_failure=True)
            except Exception as exc:
                results[key] = {"status": "ERR", "error": f"{type(exc).__name__}: {exc}"}
                add_error(run, f"claude {key}: {type(exc).__name__}: {exc}", item_failure=True)
        run["phases"]["claude_seconds"] = time.perf_counter() - t0
        run.setdefault("deep_results", {})["claude"] = results

    async def execute_run(self, run_no: int, platform: str, window: str, mode: str) -> dict[str, Any]:
        run = make_run_result(run_no, platform, window, mode)
        label = f"#{run_no} {TARGETS[platform]['label']} {window} {mode}"
        print(f"START {label}", flush=True)
        started = time.perf_counter()
        try:
            self.run_basic_steps(run)
            if mode == "deep":
                await self.run_gemini_phase(run)
                self.run_comment_auth_phase(run)
                await self.run_claude_phase(run)
                run["deep_total_seconds"] = (
                    (run["phases"]["gemini_seconds"] or 0)
                    + (run["phases"]["comment_auth_seconds"] or 0)
                    + (run["phases"]["claude_seconds"] or 0)
                )
            if run["errors"]:
                run["status"] = "ERR"
        except Exception as exc:
            run["status"] = "ERR"
            add_error(run, f"run failed: {type(exc).__name__}: {exc}")
            run["traceback"] = traceback.format_exc()
        finally:
            run["finished_at"] = now_iso()
            run["total_seconds"] = time.perf_counter() - started
            run["estimated_cost_usd"] = estimate_run_cost(run)
            cache_path = self.cache_dir / f"{self.ts}_run_{run_no:02d}_{platform}_{window}_{mode}.json"
            detail_path = self.detail_dir / f"run_{run_no:02d}_{platform}_{window}_{mode}.md"
            run["cache_path"] = str(cache_path)
            run["detail_path"] = str(detail_path)
            self.write_json(cache_path, run)
            self.write_run_detail(run, detail_path)
            self.append_single_run_log(run)
            print(
                f"DONE {label} status={run['status']} items={run.get('video_count')} "
                f"total={fmt_minutes(run.get('total_seconds'))} cache={cache_path}",
                flush=True,
            )
        self.results.append(run)
        return run

    def append_single_run_log(self, run: dict[str, Any]) -> None:
        phases = run.get("phases") or {}
        api_calls = sum(int(v or 0) for v in (run.get("api_calls") or {}).values())
        lines = [
            f"## Run #{run['run_no']} {run['platform_label']} {run['window']} {run['mode']}",
            "",
            f"- Status: `{run['status']}`",
            f"- Items: `{run.get('video_count')}`",
            f"- Avg duration: `{fmt_seconds(run.get('avg_duration_seconds'))}`",
            f"- Phase 1 list: `{fmt_seconds(phases.get('list_seconds'))}`",
            f"- Phase 2 comments: `{fmt_seconds(phases.get('comments_seconds'))}`",
            f"- Phase 3 Gemini: `{fmt_seconds(phases.get('gemini_seconds'))}`",
            f"- Phase 4 comment authenticity: `{fmt_seconds(phases.get('comment_auth_seconds'))}`",
            f"- Phase 5 Claude: `{fmt_seconds(phases.get('claude_seconds'))}`",
            f"- Total: `{fmt_minutes(run.get('total_seconds'))}`",
            f"- API calls: `{api_calls}` `{json.dumps(run.get('api_calls') or {}, ensure_ascii=False)}`",
            f"- Estimated cost: `{fmt_money(run.get('estimated_cost_usd'))}`",
            f"- Cache: `{run.get('cache_path')}`",
            f"- Detail: `{run.get('detail_path')}`",
        ]
        if run.get("warnings"):
            lines.extend(["", "Warnings:"] + [f"- {warning}" for warning in run["warnings"][:10]])
        if run.get("errors"):
            lines.extend(["", "Errors:"] + [f"- {error}" for error in run["errors"][:20]])
        self.append_log("\n".join(lines))

    def write_run_detail(self, run: dict[str, Any], path: Path) -> None:
        phases = run.get("phases") or {}
        comments = run.get("comments_by_item") or {}
        comment_counts = [int((value or {}).get("fetched_count") or 0) for value in comments.values()]
        body = [
            f"# Run #{run['run_no']} {run['platform_label']} {run['window']} {run['mode']}",
            "",
            f"- Status: `{run['status']}`",
            f"- Started: `{run.get('started_at')}`",
            f"- Finished: `{run.get('finished_at')}`",
            f"- Total: `{fmt_minutes(run.get('total_seconds'))}`",
            f"- Items: `{run.get('video_count')}`",
            f"- Avg duration: `{fmt_seconds(run.get('avg_duration_seconds'))}`",
            f"- Comment samples fetched: `{sum(comment_counts)}`",
            f"- API calls: `{json.dumps(run.get('api_calls') or {}, ensure_ascii=False)}`",
            f"- Estimated cost: `{fmt_money(run.get('estimated_cost_usd'))}`",
            "",
            "| Phase | Seconds |",
            "|---|---:|",
            f"| Phase 1 list | {fmt_seconds(phases.get('list_seconds'))} |",
            f"| Phase 2 comments | {fmt_seconds(phases.get('comments_seconds'))} |",
            f"| Basic total | {fmt_seconds(run.get('basic_total_seconds'))} |",
            f"| Phase 3 Gemini | {fmt_seconds(phases.get('gemini_seconds'))} |",
            f"| Phase 4 comment authenticity | {fmt_seconds(phases.get('comment_auth_seconds'))} |",
            f"| Phase 5 Claude | {fmt_seconds(phases.get('claude_seconds'))} |",
            f"| Deep total | {fmt_seconds(run.get('deep_total_seconds'))} |",
            "",
            "## Items",
            "",
            item_rows_md(run.get("items") or []),
        ]
        if run.get("warnings"):
            body.extend(["", "## Warnings", "", *[f"- {warning}" for warning in run["warnings"]]])
        if run.get("errors"):
            body.extend(["", "## Errors", "", *[f"- {error}" for error in run["errors"]]])
        path.write_text("\n".join(body) + "\n", encoding="utf-8")

    def run_by(self, platform: str, window: str, mode: str) -> dict[str, Any] | None:
        for run in self.results:
            if run["platform"] == platform and run["window"] == window and run["mode"] == mode:
                return run
        return None

    def cell_metric(self, platform: str, window: str, metric: str) -> Any:
        basic = self.run_by(platform, window, "basic")
        deep = self.run_by(platform, window, "deep")
        if metric == "video_count":
            b = basic.get("video_count") if basic else None
            d = deep.get("video_count") if deep else None
            if b is not None and d is not None and b != d:
                return f"{b}/{d}"
            return b if b is not None else d
        if metric == "avg_duration_seconds":
            return (basic or deep or {}).get("avg_duration_seconds")
        if metric in {"list_seconds", "comments_seconds"}:
            return ((basic or {}).get("phases") or {}).get(metric)
        if metric == "basic_total_seconds":
            return (basic or {}).get(metric)
        if metric in {"gemini_seconds", "comment_auth_seconds", "claude_seconds"}:
            return ((deep or {}).get("phases") or {}).get(metric)
        if metric == "deep_total_seconds":
            return (deep or {}).get(metric)
        if metric == "total_seconds":
            return (deep or basic or {}).get(metric)
        if metric == "api_calls":
            total = 0
            for run in (basic, deep):
                if run:
                    total += sum(int(v or 0) for v in (run.get("api_calls") or {}).values())
            return total
        if metric == "estimated_cost_usd":
            total = 0.0
            for run in (basic, deep):
                if run:
                    total += float(run.get("estimated_cost_usd") or 0.0)
            return total
        if metric == "failure_rate":
            failures = 0
            ops = 0
            for run in (basic, deep):
                if run:
                    failures += int(run.get("item_failures") or 0)
                    ops += int(run.get("item_ops") or 0)
                    if run.get("status") == "ERR" and not run.get("item_ops"):
                        failures += 1
                        ops += 1
            return (failures / max(1, ops)) * 100
        if metric == "per_video_wall":
            total = (deep or basic or {}).get("total_seconds")
            n = (deep or basic or {}).get("video_count") or 0
            return float(total or 0) / max(1, int(n))
        return None

    def table_cell(self, platform: str, window: str, metric: str) -> str:
        value = self.cell_metric(platform, window, metric)
        if metric in {"video_count", "api_calls"}:
            return "-" if value is None else str(value)
        if metric == "estimated_cost_usd":
            return fmt_money(value)
        if metric == "failure_rate":
            return f"{float(value or 0):.1f}%"
        if metric == "total_seconds":
            return fmt_minutes(value)
        return fmt_seconds(value)

    def compare_table(self) -> str:
        columns = [
            ("youtube", "recent", "YT 2M"),
            ("youtube", "global", "YT 全"),
            ("instagram", "recent", "IG 2M"),
            ("instagram", "global", "IG 全"),
            ("tiktok", "recent", "TT 2M"),
            ("tiktok", "global", "TT 全"),
        ]
        rows = [
            ("视频数", "video_count"),
            ("视频长度均值", "avg_duration_seconds"),
            ("Phase 1·拉列表", "list_seconds"),
            ("Phase 2·拉评论", "comments_seconds"),
            ("基础数据合计", "basic_total_seconds"),
            ("Phase 3·Gemini", "gemini_seconds"),
            ("Phase 4·评论真实", "comment_auth_seconds"),
            ("Phase 5·Claude", "claude_seconds"),
            ("深度合计", "deep_total_seconds"),
            ("全部总耗时", "total_seconds"),
            ("API 调用次数", "api_calls"),
            ("估算费用", "estimated_cost_usd"),
            ("失败率", "failure_rate"),
            ("单视频墙钟均值", "per_video_wall"),
        ]
        lines = ["| Phase | " + " | ".join(label for _, _, label in columns) + " |"]
        lines.append("|---|" + "|".join("---:" for _ in columns) + "|")
        for label, metric in rows:
            lines.append("| " + label + " | " + " | ".join(self.table_cell(p, w, metric) for p, w, _ in columns) + " |")
        return "\n".join(lines)

    def scrape_ratio_text(self) -> str:
        parts = []
        yt = self.cell_metric("youtube", "global", "basic_total_seconds")
        for platform in ("instagram", "tiktok"):
            other = self.cell_metric(platform, "global", "basic_total_seconds")
            if yt and other:
                parts.append(f"{TARGETS[platform]['label']} global/basic is {float(other) / max(0.001, float(yt)):.1f}x YT wall time")
        return "; ".join(parts) or "insufficient successful cells for ratio"

    def avg_auth_rate(self, platform: str, window: str = "global") -> float | None:
        run = self.run_by(platform, window, "deep")
        auth = (((run or {}).get("deep_results") or {}).get("comment_authenticity") or {})
        values = [float((item or {}).get("authentic_rate")) for item in auth.values() if (item or {}).get("sample_size")]
        return mean(values)

    def multi_key_projection(self) -> str:
        rows = ["| Platform | Window | Keys=1 | Keys=5 | Keys=8 | Keys=10 | Suggested default |", "|---|---|---:|---:|---:|---:|---|"]
        for platform in ("youtube", "instagram", "tiktok"):
            for window in ("recent", "global"):
                run = self.run_by(platform, window, "deep")
                if not run or not run.get("video_count"):
                    rows.append(f"| {TARGETS[platform]['label']} | {window} | ERR | ERR | ERR | ERR | needs rerun |")
                    continue
                phases = run.get("phases") or {}
                fixed = float(phases.get("list_seconds") or 0) + float(phases.get("comments_seconds") or 0) + float(phases.get("comment_auth_seconds") or 0)
                ai = float(phases.get("gemini_seconds") or 0) + float(phases.get("claude_seconds") or 0)
                total = float(run.get("total_seconds") or fixed + ai)
                cells = []
                for keys in (1, 5, 8, 10):
                    estimated = fixed + ai / max(1, min(keys, int(run.get("video_count") or 1)))
                    speedup = total / max(0.001, estimated)
                    cells.append(f"{fmt_minutes(estimated)} ({speedup:.1f}x)")
                suggested = "5" if window == "recent" else "8"
                rows.append(f"| {TARGETS[platform]['label']} | {window} | " + " | ".join(cells) + f" | {suggested} keys |")
        return "\n".join(rows)

    def generate_compare_report(self) -> None:
        lines = [
            "# Matti Haapoja 三平台 x 4 速度基准对比",
            "",
            f"- Generated: `{now_iso()}`",
            f"- Recent cutoff: `{self.cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}`",
            f"- Repo: `{REPO_ROOT}`",
            f"- Cache dir: `{self.cache_dir}`",
            f"- Cost note: estimated cost is a rough local heuristic; provider billing exports remain authoritative.",
            "",
            self.compare_table(),
            "",
            "## 洞察 1 三平台抓取速度对比",
            "",
            f"- {self.scrape_ratio_text()}",
            "- YT list phase uses YouTube Data API plus a yt-dlp flat-list pass. IG/TT use Apify actor runs, so startup and dataset materialization are visible in Phase 1.",
            "- IG/TT comment phase can dominate when comment actors cold-start or paginate slowly; the run log marks provider errors instead of hiding them.",
            "",
            "## 洞察 2 同一人不同平台数据差异",
            "",
            f"- Global counts: YT={self.cell_metric('youtube', 'global', 'video_count')}, IG={self.cell_metric('instagram', 'global', 'video_count')}, TT={self.cell_metric('tiktok', 'global', 'video_count')}.",
            f"- Avg duration: YT={fmt_seconds(self.cell_metric('youtube', 'global', 'avg_duration_seconds'))}, IG={fmt_seconds(self.cell_metric('instagram', 'global', 'avg_duration_seconds'))}, TT={fmt_seconds(self.cell_metric('tiktok', 'global', 'avg_duration_seconds'))}.",
            f"- Comment authentic-rate sample: YT={self._fmt_rate(self.avg_auth_rate('youtube'))}, IG={self._fmt_rate(self.avg_auth_rate('instagram'))}, TT={self._fmt_rate(self.avg_auth_rate('tiktok'))}.",
            "- Longer YT videos should push Gemini wall time up; shorter IG/TT clips are faster per item if URLs resolve cleanly.",
            "",
            "## 洞察 3 近 2 月 vs 全局",
            "",
            f"- YT 2M/global N: {self.cell_metric('youtube', 'recent', 'video_count')} / {self.cell_metric('youtube', 'global', 'video_count')}.",
            f"- IG 2M/global N: {self.cell_metric('instagram', 'recent', 'video_count')} / {self.cell_metric('instagram', 'global', 'video_count')}.",
            f"- TT 2M/global N: {self.cell_metric('tiktok', 'recent', 'video_count')} / {self.cell_metric('tiktok', 'global', 'video_count')}.",
            "- Production default should prefer the 60-day window when N is high enough for directionality; use global only for sparse channels or historical positioning.",
            "",
            "## 洞察 4 多 key 加速 ROI 推算",
            "",
            self.multi_key_projection(),
            "",
            "- Projection model: fixed scrape/comment time stays serial; Gemini+Claude time is divided by min(keys, video_count). Real speedup depends on provider rate limits and actor queueing.",
        ]
        self.compare_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _fmt_rate(value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value * 100:.1f}%"

    def generate_error_report(self) -> None:
        lines = ["# Matti Haapoja Benchmark Errors", ""]
        any_error = False
        for run in self.results:
            if not run.get("errors") and not run.get("warnings"):
                continue
            any_error = True
            lines.extend([f"## Run #{run['run_no']} {run['platform_label']} {run['window']} {run['mode']}", ""])
            if run.get("warnings"):
                lines.extend(["Warnings:", *[f"- {warning}" for warning in run["warnings"]], ""])
            if run.get("errors"):
                lines.extend(["Errors:", *[f"- {error}" for error in run["errors"]], ""])
        if not any_error:
            lines.append("No run-level warnings or errors.")
        self.error_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def generate_detail_index(self) -> None:
        lines = ["# Matti Haapoja Run Detail Index", ""]
        rows = ["| Run | Status | Items | Total | Detail | Cache |", "|---:|---|---:|---:|---|---|"]
        for run in self.results:
            rows.append(
                f"| {run['run_no']} | {run['status']} | {run.get('video_count') or 0} | "
                f"{fmt_minutes(run.get('total_seconds'))} | `{run.get('detail_path')}` | `{run.get('cache_path')}` |"
            )
        lines.extend(rows)
        self.detail_index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def run_all(self) -> None:
        self.init_reports()
        self.prep_profiles()
        for run_no, platform, window, mode in RUN_MATRIX:
            await self.execute_run(run_no, platform, window, mode)
            self.write_json(self.summary_json_path, {"ts": self.ts, "prep": self.prep, "results": self.results})
        self.generate_compare_report()
        self.generate_error_report()
        self.generate_detail_index()
        self.write_json(self.summary_json_path, {"ts": self.ts, "prep": self.prep, "results": self.results})
        print("\nSUMMARY", flush=True)
        print(f"log_md={self.log_path}", flush=True)
        print(f"compare_md={self.compare_path}", flush=True)
        print(f"detail_index_md={self.detail_index_path}", flush=True)
        print(f"errors_md={self.error_path}", flush=True)
        print(f"detail_dir={self.detail_dir}", flush=True)
        print(f"summary_json={self.summary_json_path}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Matti Haapoja 3x4 benchmark")
    parser.add_argument("--bench-dir", default=str(Path.home() / "v-kpi" / "benchmarks"))
    parser.add_argument("--ts", default="")
    parser.add_argument("--recent-days", type=int, default=60)
    parser.add_argument("--max-global", type=int, default=100)
    parser.add_argument("--comment-limit", type=int, default=50)
    parser.add_argument("--gemini-timeout", type=int, default=int(os.environ.get("VKPI_BENCHMARK_GEMINI_TIMEOUT", "900")))
    parser.add_argument("--claude-timeout", type=int, default=int(os.environ.get("VKPI_BENCHMARK_CLAUDE_TIMEOUT", "360")))
    parser.add_argument("--ytdlp-flat-timeout", type=int, default=int(os.environ.get("VKPI_BENCHMARK_YTDLP_FLAT_TIMEOUT", "120")))
    return parser.parse_args()


async def amain() -> int:
    args = parse_args()
    bench = Benchmark(args)
    try:
        await bench.run_all()
        return 0
    except Exception as exc:
        bench.generate_error_report()
        print(f"FATAL {type(exc).__name__}: {exc}", flush=True)
        print(f"log_md={bench.log_path}", flush=True)
        print(f"errors_md={bench.error_path}", flush=True)
        return 2


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
