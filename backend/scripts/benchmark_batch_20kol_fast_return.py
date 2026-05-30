#!/usr/bin/env python3
"""20 KOL three-platform Path B fast-return benchmark.

This is the low-cost round recommended by the PDF benchmark brief:
run Path B first, return real scrape/subtitle/cover availability and platform
bottleneck data quickly, and do not start the full Path A video-Gemini pass.

The script is cache/report-only. It uses the existing platform crawlers and
does not write vkpi_kol_pool or audit pipeline tables.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
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

for _logger_name in ("apify_client", "httpcore", "httpx", "urllib3", "google_genai"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)

from app.services.scraping.ytdlp import fetch_youtube_subtitles  # noqa: E402
from app.services.vkpi.industry_crawlers.instagram_crawler import InstagramCrawler  # noqa: E402
from app.services.vkpi.industry_crawlers.tiktok_crawler import TikTokCrawler  # noqa: E402
from app.services.vkpi.industry_crawlers.youtube_crawler import YouTubeCrawler  # noqa: E402


try:
    from dateutil import parser as date_parser  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    date_parser = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    YouTubeTranscriptApi = None  # type: ignore


KOLS: list[dict[str, Any]] = [
    {"slug": "matti_haapoja", "name": "Matti Haapoja", "youtube": "Matti Haapoja", "instagram": "mattih", "tiktok": "mattihaapoja"},
    {"slug": "peter_mckinnon", "name": "Peter McKinnon", "youtube": "Peter McKinnon", "instagram": "petermckinnon", "tiktok": "petermckinnon"},
    {"slug": "daniel_schiffer", "name": "Daniel Schiffer", "youtube": "Daniel Schiffer", "instagram": "daniel.schiffer", "tiktok": "danielschiffer"},
    {"slug": "sam_kolder", "name": "Sam Kolder", "youtube": "Sam Kolder", "instagram": "samkolder", "tiktok": "samkolder"},
    {"slug": "potatojet", "name": "PotatoJet (Gene Nagata)", "youtube": "Potato Jet", "instagram": "potatojet", "tiktok": ""},
    {"slug": "dslr_video_shooter", "name": "DSLR Video Shooter", "youtube": "DSLR Video Shooter", "instagram": "dslrvideoshooter", "tiktok": ""},
    {"slug": "gerald_undone", "name": "Gerald Undone", "youtube": "Gerald Undone", "instagram": "gerald_undone", "tiktok": ""},
    {"slug": "james_popsys", "name": "James Popsys", "youtube": "James Popsys", "instagram": "jamespopsysphoto", "tiktok": ""},
    {"slug": "dan_mace", "name": "Dan Mace", "youtube": "Dan Mace", "instagram": "danmace", "tiktok": "danmace"},
    {"slug": "mango_street", "name": "Mango Street", "youtube": "Mango Street", "instagram": "mangostreetlab", "tiktok": "mangostreet"},
    {"slug": "manny_ortiz", "name": "Manny Ortiz", "youtube": "Manny Ortiz", "instagram": "mannyortiz", "tiktok": ""},
    {"slug": "jessica_whitaker", "name": "Jessica Whitaker", "youtube": "Jessica Whitaker", "instagram": "jessicawhitaker", "tiktok": "jessicawhitaker"},
    {"slug": "chelsea_northrup", "name": "Chelsea Northrup", "youtube": "Chelsea Northrup", "instagram": "chelseanorthrup", "tiktok": ""},
    {"slug": "tony_chelsea_northrup", "name": "Tony & Chelsea Northrup", "youtube": "Tony & Chelsea Northrup", "instagram": "northrup", "tiktok": ""},
    {"slug": "matti_sulanto", "name": "Matti Sulanto", "youtube": "Matti Sulanto", "instagram": "mattisulanto", "tiktok": ""},
    {"slug": "brandon_li", "name": "Brandon Li", "youtube": "Brandon Li", "instagram": "brandon_l_li", "tiktok": "brandonli"},
    {"slug": "mark_wallace", "name": "Mark Wallace", "youtube": "Mark Wallace", "instagram": "markwallacephotography", "tiktok": ""},
    {"slug": "sean_tucker", "name": "Sean Tucker", "youtube": "Sean Tucker", "instagram": "seantuck", "tiktok": ""},
    {"slug": "the_slanted_lens", "name": "The Slanted Lens", "youtube": "The Slanted Lens", "instagram": "theslantedlens", "tiktok": ""},
    {"slug": "pat_kay", "name": "Pat Kay", "youtube": "Pat Kay", "instagram": "patkay", "tiktok": "patkay"},
]

PLATFORM_LIMITS = {"youtube": 50, "instagram": 30, "tiktok": 50}
PLATFORM_LABELS = {"youtube": "YT", "instagram": "IG", "tiktok": "TT"}
COMPETITOR_RE = re.compile(r"\b(sigma|tamron|sony|canon|nikon|zeiss|tokina|samyang|rokinon|godox|profoto|nanlite|aputure|sirui|laowa|ttartisan|7artisans)\b", re.I)
VILTROX_RE = re.compile(r"\b(viltrox|唯卓仕|af\s*\d{2,3}mm|pro\s*f?/?\d|lab)\b", re.I)
GENRE_PATTERNS = [
    ("review", re.compile(r"\b(review|tested?|test|vs|comparison|compared|thoughts|hands-on)\b", re.I)),
    ("tutorial", re.compile(r"\b(how to|tutorial|tips|guide|workflow|setup|behind the scenes|bts)\b", re.I)),
    ("cinematic", re.compile(r"\b(cinematic|film|short film|b-roll|travel film|sequence)\b", re.I)),
    ("vlog", re.compile(r"\b(vlog|day in the life|life update|story)\b", re.I)),
    ("unboxing", re.compile(r"\b(unboxing|first look|new gear)\b", re.I)),
    ("showcase", re.compile(r"\b(showcase|sample|photoshoot|portrait|street photography)\b", re.I)),
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
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
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
            return float(match.group("h") or 0) * 3600 + float(match.group("m") or 0) * 60 + float(match.group("s") or 0)
    if ":" in text:
        try:
            total = 0.0
            for part in [float(part) for part in text.split(":")]:
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
    if seconds >= 3600:
        return f"{seconds / 3600:.2f}h"
    if seconds >= 600:
        return f"{seconds / 60:.1f}min"
    return f"{seconds:.1f}s"


def pct(numerator: int | float, denominator: int | float) -> str:
    if not denominator:
        return "-"
    return f"{(float(numerator) / float(denominator)) * 100:.1f}%"


def mean(values: list[float]) -> float | None:
    values = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    return (sum(values) / len(values)) if values else None


def item_key(item: dict[str, Any], index: int = 0) -> str:
    return first_text(item.get("source_id"), item.get("url"), item.get("id"), f"idx-{index}")


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
    published = first_text(snippet.get("publishedAt"), item.get("publishedAt"), item.get("date"), item.get("uploadDate"), item.get("published"))
    return {
        "platform": "youtube",
        "source_id": video_id,
        "url": first_text(item.get("url"), item.get("webpage_url"), f"https://www.youtube.com/watch?v={video_id}" if video_id else ""),
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
    return {
        "platform": "tiktok",
        "source_id": first_text(item.get("id"), item.get("videoId")),
        "url": first_text(item.get("webVideoUrl"), item.get("url")),
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
    }


def normalize_item(platform: str, item: dict[str, Any]) -> dict[str, Any]:
    if platform == "youtube":
        return normalize_youtube_item(item)
    if platform == "instagram":
        return normalize_instagram_item(item)
    if platform == "tiktok":
        return normalize_tiktok_item(item)
    raise ValueError(platform)


def classify_genre(text: str) -> str:
    for genre, pattern in GENRE_PATTERNS:
        if pattern.search(text):
            return genre
    return "unknown"


def fast_signal(item: dict[str, Any], transcript: dict[str, Any] | None) -> dict[str, Any]:
    transcript_text = first_text((transcript or {}).get("text"))
    combined = " ".join(
        part
        for part in [
            first_text(item.get("title")),
            first_text(item.get("caption")),
            transcript_text[:2000],
        ]
        if part
    )
    competitor_brands = sorted({match.group(1).lower() for match in COMPETITOR_RE.finditer(combined)})
    duration = item.get("duration_seconds")
    return {
        "genre": classify_genre(combined),
        "viltrox_detected": bool(VILTROX_RE.search(combined)),
        "competitor_brands": competitor_brands,
        "thumbnail_hit": bool(first_text(item.get("thumbnail"))),
        "caption_hit": bool(first_text(item.get("caption"), item.get("title"))),
        "transcript_hit": bool(transcript_text),
        "text_chars": len(combined),
        "duration_seconds": duration,
        "short_form": bool(duration is not None and float(duration) <= 90),
    }


def _profile_summary(platform: str, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items") or []
    first = items[0] if items and isinstance(items[0], dict) else {}
    if platform == "youtube":
        snippet = first.get("snippet") if isinstance(first.get("snippet"), dict) else {}
        stats = first.get("statistics") if isinstance(first.get("statistics"), dict) else {}
        return {
            "id": first_text(first.get("id")),
            "name": first_text(snippet.get("title"), first.get("title"), first.get("name")),
            "followers": to_int(stats.get("subscriberCount") or first.get("numberOfSubscribers")),
            "bio": first_text(snippet.get("description"), first.get("description"))[:300],
        }
    return {}


class FastReturnBenchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.ts = args.ts or utc_ts()
        self.root = Path(args.bench_root).expanduser().resolve() / f"{self.ts}_batch_20kol"
        self.cache_dir = self.root / "cache"
        self.per_kol_dir = self.root / "per_kol"
        self.transcript_dir = self.cache_dir / "transcripts"
        self.run_log_path = self.root / "run_log.md"
        self.path_a_path = self.root / "path_a_results.md"
        self.path_b_path = self.root / "path_b_results.md"
        self.compare_path = self.root / "compare_summary.md"
        self.errors_path = self.root / "errors.md"
        for path in (self.root, self.cache_dir, self.per_kol_dir, self.transcript_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.youtube_sem = asyncio.Semaphore(max(1, args.youtube_concurrency))
        self.apify_sem = asyncio.Semaphore(max(1, args.apify_concurrency))
        self.video_sem = asyncio.Semaphore(max(1, args.video_concurrency))
        self.results: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.started = time.perf_counter()
        self.gemini_key_count = self._gemini_key_count()

    @staticmethod
    def _gemini_key_count() -> int:
        pooled = [key.strip() for key in os.environ.get("GEMINI_API_KEYS", "").split(",") if key.strip()]
        if pooled:
            return len(pooled)
        return 1 if os.environ.get("GEMINI_API_KEY") else 0

    def write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def append_log(self, text: str) -> None:
        with self.run_log_path.open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n\n")

    def init_reports(self) -> None:
        key_note = "SUBOPTIMAL" if self.gemini_key_count < 5 else "OK"
        self.run_log_path.write_text(
            "\n".join(
                [
                    "# Batch 20 KOL Path B Fast-Return Run Log",
                    "",
                    f"- Started: `{now_iso()}`",
                    f"- Repo: `{REPO_ROOT}`",
                    f"- Output dir: `{self.root}`",
                    f"- Mode: `Path B only, no full video Gemini`",
                    f"- Limits: YT={self.args.yt_limit}, IG={self.args.ig_limit}, TT={self.args.tt_limit}",
                    f"- Concurrency: KOL={self.args.kol_concurrency}, platform=3, video={self.args.video_concurrency}, Apify cap={self.args.apify_concurrency}",
                    f"- Gemini key pool: `{self.gemini_key_count}` keys, `{key_note}` for Path A or per-video AI",
                    f"- youtube-transcript-api: `{'installed' if YouTubeTranscriptApi else 'missing'}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.path_a_path.write_text(
            "\n".join(
                [
                    "# Path A Results",
                    "",
                    "Not run in this fast-return round.",
                    "",
                    "Reason: the previous Matti single-KOL deep run spent more than 30 minutes inside serial full-video Gemini before finishing run #3. "
                    "This round follows the PDF recommendation to run Path B first, then use those results to decide whether a smaller Path A sample is worth running.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def cache_path(self, *parts: str) -> Path:
        clean = [re.sub(r"[^a-zA-Z0-9_.-]+", "_", part).strip("_") for part in parts]
        return self.cache_dir / ("__".join(clean) + ".json")

    async def resolve_youtube_channel(self, kol: dict[str, Any]) -> dict[str, Any]:
        cache = self.cache_path(kol["slug"], "youtube_profile")
        if cache.exists() and not self.args.no_cache:
            return json.loads(cache.read_text(encoding="utf-8"))

        def _resolve() -> dict[str, Any]:
            crawler = YouTubeCrawler(timeout_seconds=30, run_timeout_seconds=300)
            query = first_text(kol.get("youtube"), kol.get("name"))
            search = crawler.search_channel_by_name(query, max_results=5)
            candidates = []
            for item in search.get("items") or []:
                raw_id = item.get("id")
                channel_id = first_text((raw_id or {}).get("channelId") if isinstance(raw_id, dict) else raw_id)
                if not channel_id:
                    continue
                profile = crawler.crawl_channel_profile("", channel_id=channel_id, max_posts=1)
                summary = _profile_summary("youtube", profile)
                if summary.get("id"):
                    candidates.append({"channel_id": summary["id"], "summary": summary})
            candidates.sort(key=lambda row: int((row.get("summary") or {}).get("followers") or 0), reverse=True)
            best = candidates[0] if candidates else {}
            return {
                "status": "ok" if best else "no_results",
                "query": query,
                "channel_id": first_text(best.get("channel_id") if isinstance(best, dict) else ""),
                "summary": best.get("summary") if isinstance(best, dict) else {},
                "candidate_count": len(candidates),
                "raw_status": first_text(search.get("provider_status"), search.get("sync_status")),
            }

        async with self.youtube_sem:
            payload = await asyncio.to_thread(_resolve)
        self.write_json(cache, payload)
        return payload

    async def fetch_list(self, kol: dict[str, Any], platform: str) -> dict[str, Any]:
        cache = self.cache_path(kol["slug"], platform, "list")
        if cache.exists() and not self.args.no_cache:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            cached["cache_hit"] = True
            return cached

        limit = {"youtube": self.args.yt_limit, "instagram": self.args.ig_limit, "tiktok": self.args.tt_limit}[platform]
        started = time.perf_counter()
        if platform == "youtube":
            profile = await self.resolve_youtube_channel(kol)
            channel_id = first_text(profile.get("channel_id"))
            if not channel_id:
                return {"status": "SKIP", "items": [], "elapsed_seconds": time.perf_counter() - started, "error": "youtube channel not resolved", "profile": profile}

            def _fetch_youtube() -> dict[str, Any]:
                crawler = YouTubeCrawler(timeout_seconds=30, run_timeout_seconds=600)
                return crawler.crawl_channel_videos(channel_id, max_results=limit)

            async with self.youtube_sem:
                payload = await asyncio.to_thread(_fetch_youtube)
            raw_items = payload.get("items") or []
            normalized = [normalize_item(platform, item) for item in raw_items if isinstance(item, dict)]
            normalized.sort(key=lambda item: parse_dt(item.get("published_ts") or item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            result = {
                "status": "OK" if normalized else "ERR",
                "provider_status": first_text(payload.get("provider_status"), payload.get("sync_status")),
                "items": normalized[:limit],
                "requested_limit": limit,
                "elapsed_seconds": time.perf_counter() - started,
                "api_calls": {"youtube_data_api": 1},
                "profile": profile,
                "error": payload.get("error") or payload.get("message") or "",
            }
        elif platform == "instagram":
            handle = first_text(kol.get("instagram"))
            if not handle:
                return {"status": "SKIP", "items": [], "elapsed_seconds": time.perf_counter() - started, "error": "no instagram handle in candidate list"}

            def _fetch_instagram() -> dict[str, Any]:
                crawler = InstagramCrawler(run_timeout_seconds=self.args.apify_timeout)
                return crawler.crawl_channel_videos(handle, max_results=limit)

            async with self.apify_sem:
                payload = await asyncio.to_thread(_fetch_instagram)
            raw_items = payload.get("items") or []
            normalized = [normalize_item(platform, item) for item in raw_items if isinstance(item, dict)]
            normalized.sort(key=lambda item: parse_dt(item.get("published_ts") or item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            result = {
                "status": "OK" if normalized else "ERR",
                "provider_status": first_text(payload.get("provider_status"), payload.get("sync_status")),
                "items": normalized[:limit],
                "requested_limit": limit,
                "elapsed_seconds": time.perf_counter() - started,
                "api_calls": {"apify_actor": 1},
                "handle": handle,
                "error": payload.get("error") or payload.get("message") or "",
            }
        else:
            handle = first_text(kol.get("tiktok"))
            if not handle:
                return {"status": "SKIP", "items": [], "elapsed_seconds": time.perf_counter() - started, "error": "no tiktok handle in candidate list"}

            def _fetch_tiktok() -> dict[str, Any]:
                crawler = TikTokCrawler(run_timeout_seconds=self.args.apify_timeout)
                return crawler.crawl_channel_videos(handle, max_results=limit)

            async with self.apify_sem:
                payload = await asyncio.to_thread(_fetch_tiktok)
            raw_items = payload.get("items") or []
            normalized = [normalize_item(platform, item) for item in raw_items if isinstance(item, dict)]
            normalized.sort(key=lambda item: parse_dt(item.get("published_ts") or item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            observed_handles = sorted({first_text(item.get("author_handle")).lstrip("@").lower() for item in normalized if first_text(item.get("author_handle"))})
            warning = ""
            if observed_handles and handle.lower().lstrip("@") not in observed_handles:
                warning = f"requested @{handle}, actor returned {observed_handles[:3]}"
            result = {
                "status": "OK" if normalized else "ERR",
                "provider_status": first_text(payload.get("provider_status"), payload.get("sync_status")),
                "items": normalized[:limit],
                "requested_limit": limit,
                "elapsed_seconds": time.perf_counter() - started,
                "api_calls": {"apify_actor": 1},
                "handle": handle,
                "observed_handles": observed_handles,
                "warning": warning,
                "error": payload.get("error") or payload.get("message") or "",
            }

        self.write_json(cache, result)
        return result

    def fetch_transcript_sync(self, video_id: str, url: str) -> dict[str, Any]:
        cache = self.transcript_dir / f"{video_id or re.sub(r'[^a-zA-Z0-9]+', '_', url)[:80]}.json"
        if cache.exists() and not self.args.no_cache:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            payload["cache_hit"] = True
            return payload
        if not video_id:
            return {"status": "missing_video_id", "text": "", "line_count": 0, "elapsed_seconds": 0.0}
        t0 = time.perf_counter()
        text = ""
        status = "ERR"
        error = ""
        method = "youtube_transcript_api"
        try:
            if YouTubeTranscriptApi is None:
                raise RuntimeError("youtube-transcript-api not installed")
            transcript = YouTubeTranscriptApi().fetch(video_id, languages=("en", "en-US", "zh-Hans", "zh-Hant", "zh"))
            lines = []
            for entry in transcript:
                start = float(getattr(entry, "start", 0.0) if not isinstance(entry, dict) else entry.get("start", 0.0))
                value = first_text(getattr(entry, "text", "") if not isinstance(entry, dict) else entry.get("text"))
                if value:
                    lines.append(f"[{int(start // 60):02d}:{int(start % 60):02d}] {value}")
            text = "\n".join(lines)
            if len(text) > self.args.transcript_chars:
                text = text[: self.args.transcript_chars] + "\n...[truncated]"
            status = "OK" if text else "empty"
        except Exception as exc:
            error = str(exc)[:500]
            if self.args.ytdlp_fallback and url:
                method = "yt_dlp_fallback"
                try:
                    text = fetch_youtube_subtitles(url, max_chars=self.args.transcript_chars)
                    status = "OK" if text else "empty"
                    error = error if not text else ""
                except Exception as fallback_exc:
                    status = "ERR"
                    error = f"{error}; fallback={fallback_exc}"[:500]
        payload = {
            "status": status,
            "method": method,
            "text": text,
            "line_count": text.count("\n") + (1 if text else 0),
            "char_count": len(text),
            "elapsed_seconds": time.perf_counter() - t0,
            "error": error,
        }
        self.write_json(cache, payload)
        return payload

    async def fetch_transcript(self, item: dict[str, Any]) -> dict[str, Any]:
        async with self.video_sem:
            return await asyncio.wait_for(
                asyncio.to_thread(self.fetch_transcript_sync, first_text(item.get("source_id")), first_text(item.get("url"))),
                timeout=self.args.transcript_timeout,
            )

    async def run_unit(self, kol: dict[str, Any], platform: str) -> dict[str, Any]:
        label = f"{kol['slug']} {PLATFORM_LABELS[platform]}"
        print(f"START {label}", flush=True)
        started = time.perf_counter()
        result: dict[str, Any] = {
            "kol_slug": kol["slug"],
            "kol_name": kol["name"],
            "platform": platform,
            "platform_label": PLATFORM_LABELS[platform],
            "status": "OK",
            "started_at": now_iso(),
            "finished_at": "",
            "phases": {"list_seconds": None, "transcript_seconds": None, "fast_signal_seconds": None},
            "items": [],
            "signals": [],
            "errors": [],
            "warnings": [],
            "api_calls": {},
        }
        try:
            list_payload = await self.fetch_list(kol, platform)
            result["phases"]["list_seconds"] = list_payload.get("elapsed_seconds")
            result["list_provider_status"] = list_payload.get("provider_status") or list_payload.get("status")
            result["api_calls"].update(list_payload.get("api_calls") or {})
            if list_payload.get("warning"):
                result["warnings"].append(str(list_payload.get("warning")))
            if list_payload.get("status") == "SKIP":
                result["status"] = "SKIP"
                result["errors"].append(first_text(list_payload.get("error"), "skipped"))
                return result
            items = list_payload.get("items") or []
            result["items"] = items
            if not items:
                result["status"] = "ERR"
                result["errors"].append(first_text(list_payload.get("error"), "no items returned"))
                return result
            requested_limit = int(list_payload.get("requested_limit") or {"youtube": self.args.yt_limit, "instagram": self.args.ig_limit, "tiktok": self.args.tt_limit}[platform])
            low_coverage_floor = max(3, int(requested_limit * 0.2))
            if len(items) < low_coverage_floor:
                result["warnings"].append(
                    f"low item count: got {len(items)} of requested {requested_limit}; verify handle/account or actor result shape"
                )

            transcripts: dict[str, dict[str, Any]] = {}
            if platform == "youtube":
                t0 = time.perf_counter()
                tasks = []
                for idx, item in enumerate(items):
                    tasks.append((item_key(item, idx), asyncio.create_task(self.fetch_transcript(item))))
                for key, task in tasks:
                    try:
                        transcripts[key] = await task
                    except Exception as exc:
                        transcripts[key] = {"status": "ERR", "text": "", "error": str(exc)[:500], "elapsed_seconds": 0.0}
                result["phases"]["transcript_seconds"] = time.perf_counter() - t0
                result["api_calls"]["youtube_transcript_api"] = len(items)
            else:
                result["phases"]["transcript_seconds"] = 0.0

            t0 = time.perf_counter()
            signals = []
            for idx, item in enumerate(items):
                key = item_key(item, idx)
                signal = fast_signal(item, transcripts.get(key))
                signal["source_id"] = key
                signal["title"] = first_text(item.get("title"), item.get("caption"))[:160]
                signals.append(signal)
            result["signals"] = signals
            result["phases"]["fast_signal_seconds"] = time.perf_counter() - t0
        except Exception as exc:
            result["status"] = "ERR"
            result["errors"].append(f"{type(exc).__name__}: {exc}")
            result["traceback"] = traceback.format_exc()
        finally:
            result["finished_at"] = now_iso()
            result["total_seconds"] = time.perf_counter() - started
            result["item_count"] = len(result.get("items") or [])
            result["avg_duration_seconds"] = mean([float(item["duration_seconds"]) for item in result.get("items") or [] if item.get("duration_seconds") is not None])
            result["transcript_hit_count"] = sum(1 for signal in result.get("signals") or [] if signal.get("transcript_hit"))
            result["thumbnail_hit_count"] = sum(1 for signal in result.get("signals") or [] if signal.get("thumbnail_hit"))
            result["caption_hit_count"] = sum(1 for signal in result.get("signals") or [] if signal.get("caption_hit"))
            result["viltrox_detected_count"] = sum(1 for signal in result.get("signals") or [] if signal.get("viltrox_detected"))
            result["competitor_hit_count"] = sum(1 for signal in result.get("signals") or [] if signal.get("competitor_brands"))
            if result.get("errors") and result["status"] == "OK":
                result["status"] = "ERR"
            self.write_json(self.cache_dir / f"unit__{kol['slug']}__{platform}.json", result)
            self.append_unit_log(result)
            print(
                f"DONE {label} status={result['status']} items={result.get('item_count')} "
                f"total={fmt_seconds(result.get('total_seconds'))}",
                flush=True,
            )
        return result

    def append_unit_log(self, result: dict[str, Any]) -> None:
        phases = result.get("phases") or {}
        lines = [
            f"## {result['kol_name']} · {result['platform_label']}",
            "",
            f"- Status: `{result['status']}`",
            f"- Items: `{result.get('item_count', 0)}`",
            f"- List: `{fmt_seconds(phases.get('list_seconds'))}`",
            f"- Transcript: `{fmt_seconds(phases.get('transcript_seconds'))}`",
            f"- Fast signal: `{fmt_seconds(phases.get('fast_signal_seconds'))}`",
            f"- Total: `{fmt_seconds(result.get('total_seconds'))}`",
            f"- Caption hit: `{pct(result.get('caption_hit_count', 0), result.get('item_count', 0))}`",
            f"- Thumbnail/cover hit: `{pct(result.get('thumbnail_hit_count', 0), result.get('item_count', 0))}`",
            f"- Transcript hit: `{pct(result.get('transcript_hit_count', 0), result.get('item_count', 0))}`",
        ]
        if result.get("warnings"):
            lines.extend(["", "Warnings:", *[f"- {warning}" for warning in result["warnings"][:5]]])
        if result.get("errors"):
            lines.extend(["", "Errors:", *[f"- {error}" for error in result["errors"][:5]]])
        self.append_log("\n".join(lines))

    async def run_kol(self, kol: dict[str, Any]) -> list[dict[str, Any]]:
        tasks = [asyncio.create_task(self.run_unit(kol, platform)) for platform in ("youtube", "instagram", "tiktok")]
        results = await asyncio.gather(*tasks)
        self.write_per_kol(kol, results)
        return results

    async def run(self) -> None:
        self.init_reports()
        selected = KOLS[: max(1, min(len(KOLS), self.args.max_kols))]
        sem = asyncio.Semaphore(max(1, self.args.kol_concurrency))

        async def _guarded(kol: dict[str, Any]) -> list[dict[str, Any]]:
            async with sem:
                return await self.run_kol(kol)

        batches = await asyncio.gather(*[asyncio.create_task(_guarded(kol)) for kol in selected])
        self.results = [unit for batch in batches for unit in batch]
        self.errors = [
            {
                "kol": result.get("kol_name"),
                "platform": result.get("platform"),
                "status": result.get("status"),
                "errors": result.get("errors"),
                "warnings": result.get("warnings"),
            }
            for result in self.results
            if result.get("status") != "OK" or result.get("warnings")
        ]
        self.write_final_reports()

    def write_per_kol(self, kol: dict[str, Any], results: list[dict[str, Any]]) -> None:
        rows = ["| Platform | Status | Items | Total | List | Transcript | Caption | Cover | Transcript Hit |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
        for result in results:
            phases = result.get("phases") or {}
            rows.append(
                f"| {result['platform_label']} | {result['status']} | {result.get('item_count', 0)} | "
                f"{fmt_seconds(result.get('total_seconds'))} | {fmt_seconds(phases.get('list_seconds'))} | "
                f"{fmt_seconds(phases.get('transcript_seconds'))} | {pct(result.get('caption_hit_count', 0), result.get('item_count', 0))} | "
                f"{pct(result.get('thumbnail_hit_count', 0), result.get('item_count', 0))} | "
                f"{pct(result.get('transcript_hit_count', 0), result.get('item_count', 0))} |"
            )
        body = [f"# {kol['name']}", "", *rows]
        for result in results:
            if result.get("errors") or result.get("warnings"):
                body.extend(["", f"## {result['platform_label']} Notes"])
                body.extend([f"- Warning: {warning}" for warning in result.get("warnings") or []])
                body.extend([f"- Error: {error}" for error in result.get("errors") or []])
        (self.per_kol_dir / f"{kol['slug']}.md").write_text("\n".join(body) + "\n", encoding="utf-8")

    def platform_stats(self, platform: str) -> dict[str, Any]:
        rows = [result for result in self.results if result.get("platform") == platform]
        ok_rows = [result for result in rows if result.get("status") == "OK"]
        total_items = sum(int(result.get("item_count") or 0) for result in rows)
        phases = Counter()
        api_calls = Counter()
        for result in rows:
            for key, value in (result.get("phases") or {}).items():
                phases[key] += float(value or 0.0)
            for key, value in (result.get("api_calls") or {}).items():
                api_calls[key] += int(value or 0)
        return {
            "units": len(rows),
            "ok_units": len(ok_rows),
            "skip_units": sum(1 for result in rows if result.get("status") == "SKIP"),
            "err_units": sum(1 for result in rows if result.get("status") == "ERR"),
            "items": total_items,
            "total_seconds": sum(float(result.get("total_seconds") or 0.0) for result in rows),
            "avg_unit_seconds": mean([float(result.get("total_seconds") or 0.0) for result in rows]),
            "avg_duration_seconds": mean([float(result.get("avg_duration_seconds")) for result in rows if result.get("avg_duration_seconds") is not None]),
            "caption_hits": sum(int(result.get("caption_hit_count") or 0) for result in rows),
            "cover_hits": sum(int(result.get("thumbnail_hit_count") or 0) for result in rows),
            "transcript_hits": sum(int(result.get("transcript_hit_count") or 0) for result in rows),
            "viltrox_hits": sum(int(result.get("viltrox_detected_count") or 0) for result in rows),
            "competitor_hits": sum(int(result.get("competitor_hit_count") or 0) for result in rows),
            "phases": dict(phases),
            "api_calls": dict(api_calls),
        }

    def write_final_reports(self) -> None:
        self.write_path_b_results()
        self.write_errors()
        self.write_compare()
        summary_json = {
            "ts": self.ts,
            "output_dir": str(self.root),
            "total_seconds": time.perf_counter() - self.started,
            "results": self.results,
            "errors": self.errors,
        }
        self.write_json(self.cache_dir / "summary.json", summary_json)

    def write_path_b_results(self) -> None:
        rows = [
            "| KOL | Platform | Status | Items | Total | List | Transcript | Caption | Cover | Transcript Hit | Viltrox | Competitor |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for result in sorted(self.results, key=lambda row: (row.get("kol_slug") or "", row.get("platform") or "")):
            phases = result.get("phases") or {}
            rows.append(
                f"| {result.get('kol_name')} | {result.get('platform_label')} | {result.get('status')} | {result.get('item_count', 0)} | "
                f"{fmt_seconds(result.get('total_seconds'))} | {fmt_seconds(phases.get('list_seconds'))} | {fmt_seconds(phases.get('transcript_seconds'))} | "
                f"{pct(result.get('caption_hit_count', 0), result.get('item_count', 0))} | {pct(result.get('thumbnail_hit_count', 0), result.get('item_count', 0))} | "
                f"{pct(result.get('transcript_hit_count', 0), result.get('item_count', 0))} | {result.get('viltrox_detected_count', 0)} | {result.get('competitor_hit_count', 0)} |"
            )
        self.path_b_path.write_text("# Path B Fast-Return Results\n\n" + "\n".join(rows) + "\n", encoding="utf-8")

    def write_errors(self) -> None:
        if not self.errors:
            self.errors_path.write_text("# Errors\n\nNo errors or warnings.\n", encoding="utf-8")
            return
        lines = ["# Errors / SKIP / Warnings", ""]
        for item in self.errors:
            lines.append(f"## {item.get('kol')} · {item.get('platform')} · {item.get('status')}")
            for warning in item.get("warnings") or []:
                lines.append(f"- Warning: {warning}")
            for error in item.get("errors") or []:
                lines.append(f"- Error: {error}")
            lines.append("")
        self.errors_path.write_text("\n".join(lines), encoding="utf-8")

    def write_compare(self) -> None:
        elapsed = time.perf_counter() - self.started
        stats = {platform: self.platform_stats(platform) for platform in ("youtube", "instagram", "tiktok")}
        total_units = len(self.results)
        ok_units = sum(1 for result in self.results if result.get("status") == "OK")
        total_items = sum(int(result.get("item_count") or 0) for result in self.results)
        total_api = Counter()
        for row in self.results:
            total_api.update({key: int(value or 0) for key, value in (row.get("api_calls") or {}).items()})
        rows = [
            "| Metric | YouTube | Instagram | TikTok |",
            "|---|---:|---:|---:|",
            f"| Units OK / total | {stats['youtube']['ok_units']}/{stats['youtube']['units']} | {stats['instagram']['ok_units']}/{stats['instagram']['units']} | {stats['tiktok']['ok_units']}/{stats['tiktok']['units']} |",
            f"| Items returned | {stats['youtube']['items']} | {stats['instagram']['items']} | {stats['tiktok']['items']} |",
            f"| Avg duration | {fmt_seconds(stats['youtube']['avg_duration_seconds'])} | {fmt_seconds(stats['instagram']['avg_duration_seconds'])} | {fmt_seconds(stats['tiktok']['avg_duration_seconds'])} |",
            f"| Total unit wall time | {fmt_seconds(stats['youtube']['total_seconds'])} | {fmt_seconds(stats['instagram']['total_seconds'])} | {fmt_seconds(stats['tiktok']['total_seconds'])} |",
            f"| Avg unit wall time | {fmt_seconds(stats['youtube']['avg_unit_seconds'])} | {fmt_seconds(stats['instagram']['avg_unit_seconds'])} | {fmt_seconds(stats['tiktok']['avg_unit_seconds'])} |",
            f"| List time | {fmt_seconds(stats['youtube']['phases'].get('list_seconds'))} | {fmt_seconds(stats['instagram']['phases'].get('list_seconds'))} | {fmt_seconds(stats['tiktok']['phases'].get('list_seconds'))} |",
            f"| Transcript time | {fmt_seconds(stats['youtube']['phases'].get('transcript_seconds'))} | {fmt_seconds(stats['instagram']['phases'].get('transcript_seconds'))} | {fmt_seconds(stats['tiktok']['phases'].get('transcript_seconds'))} |",
            f"| Caption hit | {pct(stats['youtube']['caption_hits'], stats['youtube']['items'])} | {pct(stats['instagram']['caption_hits'], stats['instagram']['items'])} | {pct(stats['tiktok']['caption_hits'], stats['tiktok']['items'])} |",
            f"| Thumbnail/cover hit | {pct(stats['youtube']['cover_hits'], stats['youtube']['items'])} | {pct(stats['instagram']['cover_hits'], stats['instagram']['items'])} | {pct(stats['tiktok']['cover_hits'], stats['tiktok']['items'])} |",
            f"| Transcript hit | {pct(stats['youtube']['transcript_hits'], stats['youtube']['items'])} | {pct(stats['instagram']['transcript_hits'], stats['instagram']['items'])} | {pct(stats['tiktok']['transcript_hits'], stats['tiktok']['items'])} |",
            f"| Viltrox text hit | {stats['youtube']['viltrox_hits']} | {stats['instagram']['viltrox_hits']} | {stats['tiktok']['viltrox_hits']} |",
            f"| Competitor text hit | {stats['youtube']['competitor_hits']} | {stats['instagram']['competitor_hits']} | {stats['tiktok']['competitor_hits']} |",
            f"| Failure/SKIP rate | {pct(stats['youtube']['units'] - stats['youtube']['ok_units'], stats['youtube']['units'])} | {pct(stats['instagram']['units'] - stats['instagram']['ok_units'], stats['instagram']['units'])} | {pct(stats['tiktok']['units'] - stats['tiktok']['ok_units'], stats['tiktok']['units'])} |",
        ]
        bottlenecks = self._bottlenecks(stats)
        production = self._production_projection(elapsed, total_units)
        body = [
            "# Batch Test · 20 KOL × 3 Platforms · Path B Fast Return",
            "",
            f"- Finished: `{now_iso()}`",
            f"- Wall time: `{fmt_seconds(elapsed)}`",
            f"- Units OK / total: `{ok_units}/{total_units}`",
            f"- Items returned: `{total_items}`",
            f"- API calls: `{json.dumps(dict(total_api), ensure_ascii=False)}`",
            f"- Gemini keys detected: `{self.gemini_key_count}` (`{'SUBOPTIMAL' if self.gemini_key_count < 5 else 'OK'}` for full Path A)",
            f"- Estimated LLM cost this round: `$0.00` (no per-video Gemini/Claude in fast-return round)",
            "",
            "## Platform Summary",
            "",
            *rows,
            "",
            "## Insights",
            "",
            "[Insight 1] Platform scrape speed",
            "",
            bottlenecks["speed"],
            "",
            "[Insight 2] Same creator, different platform data",
            "",
            "This fast round compares content shape from titles, captions, durations, covers, and YouTube transcript availability. It does not claim final V6 quality alignment because Path A full-video analysis was intentionally skipped.",
            "",
            "[Insight 3] Fast Path B coverage",
            "",
            bottlenecks["coverage"],
            "",
            "[Insight 4] Multi-key ROI projection",
            "",
            production,
            "",
            "## Recommended Next Run",
            "",
            "Run Path A only for 5 representative KOL after reviewing this file. The full 20 KOL Path A run should wait until the handle error rate and Path B coverage are acceptable.",
            "",
        ]
        self.compare_path.write_text("\n".join(body), encoding="utf-8")

    def _bottlenecks(self, stats: dict[str, dict[str, Any]]) -> dict[str, str]:
        speed_lines = []
        for platform in ("youtube", "instagram", "tiktok"):
            row = stats[platform]
            phases = row["phases"]
            total_phase = sum(float(value or 0) for value in phases.values()) or 1.0
            slowest = sorted(phases.items(), key=lambda pair: float(pair[1] or 0), reverse=True)[:2]
            parts = ", ".join(f"{name.replace('_seconds', '')} {fmt_seconds(value)} ({pct(value, total_phase)})" for name, value in slowest)
            speed_lines.append(f"- {PLATFORM_LABELS[platform]}: avg unit {fmt_seconds(row['avg_unit_seconds'])}; main time: {parts}.")
        coverage_lines = []
        for platform in ("youtube", "instagram", "tiktok"):
            row = stats[platform]
            coverage_lines.append(
                f"- {PLATFORM_LABELS[platform]}: caption {pct(row['caption_hits'], row['items'])}, "
                f"cover {pct(row['cover_hits'], row['items'])}, transcript {pct(row['transcript_hits'], row['items'])}."
            )
        return {"speed": "\n".join(speed_lines), "coverage": "\n".join(coverage_lines)}

    def _production_projection(self, elapsed: float, unit_count: int) -> str:
        if not unit_count:
            return "- No completed units; projection unavailable."
        seconds_per_unit = elapsed / max(1, unit_count)
        path_b_1000_units_5 = seconds_per_unit * 1000 / 5
        path_b_1000_units_10 = seconds_per_unit * 1000 / 10
        key_note = "Only one Gemini key is configured; Path A/video AI concurrency would be SUBOPTIMAL." if self.gemini_key_count < 5 else "Gemini key pool is sufficient for the PDF's 5-key baseline."
        return "\n".join(
            [
                f"- Observed fast-return wall seconds per platform unit: {seconds_per_unit:.2f}s.",
                f"- 1000 platform units, Path B at 5-way concurrency: {fmt_seconds(path_b_1000_units_5)}.",
                f"- 1000 platform units, Path B at 10-way concurrency: {fmt_seconds(path_b_1000_units_10)}.",
                f"- {key_note}",
            ]
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="20 KOL Path B fast-return benchmark")
    parser.add_argument("--bench-root", default=str(Path.home() / "v-kpi" / "benchmarks"))
    parser.add_argument("--ts", default="")
    parser.add_argument("--max-kols", type=int, default=20)
    parser.add_argument("--yt-limit", type=int, default=50)
    parser.add_argument("--ig-limit", type=int, default=30)
    parser.add_argument("--tt-limit", type=int, default=50)
    parser.add_argument("--kol-concurrency", type=int, default=5)
    parser.add_argument("--youtube-concurrency", type=int, default=5)
    parser.add_argument("--apify-concurrency", type=int, default=4)
    parser.add_argument("--video-concurrency", type=int, default=8)
    parser.add_argument("--apify-timeout", type=int, default=600)
    parser.add_argument("--transcript-timeout", type=int, default=20)
    parser.add_argument("--transcript-chars", type=int, default=6000)
    parser.add_argument("--ytdlp-fallback", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    return parser


async def main_async() -> None:
    args = build_parser().parse_args()
    bench = FastReturnBenchmark(args)
    await bench.run()
    print("SUMMARY", flush=True)
    print(f"output_dir={bench.root}", flush=True)
    print(f"run_log={bench.run_log_path}", flush=True)
    print(f"path_a={bench.path_a_path}", flush=True)
    print(f"path_b={bench.path_b_path}", flush=True)
    print(f"compare={bench.compare_path}", flush=True)
    print(f"errors={bench.errors_path}", flush=True)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
