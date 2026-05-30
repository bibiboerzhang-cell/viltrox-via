#!/usr/bin/env python3
"""Step 4a YouTube evidence POC.

Dry-run default:
  1. YouTube Data API first.
  2. Apify actor fallback when API cannot resolve/fetch the channel.
  3. Write CSV/Markdown reports only.

Commit mode is implemented for the eventual second round, but this POC should
be run with --dry-run until migration 089 is reviewed and applied.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


CATALOG_PATH = Path("artifacts/viltrox_product_sku_catalog_20260527.csv")
ENRICH_PATH = Path("/Users/bibiboer/Downloads/vkpi-final/scripts/enrich_video_urls.py")
ARTIFACT_DIR = Path("artifacts")
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
STEP4A_YOUTUBE_KOL_IDS = {3910, 3960, 3970, 4063, 4094, 4154, 4185, 4227, 4008, 4044}


@dataclass
class KolTarget:
    id: int
    display_name: str
    handle: str
    platform: str
    profile_url: str


@dataclass
class VideoCandidate:
    kol_id: int
    kol_name: str
    video_url: str
    title: str
    description: str
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    publish_date: str
    duration_seconds: int | None
    thumbnail_url: str
    channel_id: str
    channel_name: str
    scrape_source: str
    scrape_status: str
    scrape_error: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    confidence: str = "medium"


@dataclass
class KolRun:
    kol: KolTarget
    channel_url: str
    layer: str
    status: str
    scanned_count: int = 0
    matches: list[VideoCandidate] = field(default_factory=list)
    error: str = ""
    api_units: int = 0
    apify_run_id: str = ""
    apify_dataset_items: int = 0
    apify_sample_rows: list[dict[str, Any]] = field(default_factory=list)


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def text(value: Any) -> str:
    return str(value or "").strip()


def connect_db():
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)


def parse_ids(value: str) -> set[int] | None:
    if not value.strip():
        return None
    return {int(part.strip()) for part in value.split(",") if part.strip()}


def load_targets(kol_ids: set[int] | None) -> list[KolTarget]:
    where = ""
    params: list[Any] = []
    if kol_ids:
        where = "id = ANY(%s)"
        params.append(sorted(kol_ids))
    else:
        where = "id = ANY(%s)"
        params.append(sorted(STEP4A_YOUTUBE_KOL_IDS))
    query = f"""
        SELECT id, display_name, handle, platform, profile_url
        FROM vkpi_kol_pool
        WHERE {where}
        ORDER BY id
    """
    with connect_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [
                KolTarget(
                    id=int(row["id"]),
                    display_name=text(row["display_name"]),
                    handle=text(row["handle"]),
                    platform=text(row["platform"]),
                    profile_url=text(row["profile_url"]),
                )
                for row in cur.fetchall()
            ]


def extract_keywords_from_enrich(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8")
    match = re.search(r"VILTROX_KEYWORDS\s*=\s*\[(.*?)\]", content, re.S)
    if not match:
        return []
    return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))


def clean_keyword(value: str) -> str:
    keyword = re.sub(r"\s+", " ", text(value)).lower()
    if keyword.startswith("viltrox ") and len(keyword) > 12:
        keyword = keyword.removeprefix("viltrox ").strip()
    return keyword


def load_keywords() -> list[str]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"Missing catalog CSV: {CATALOG_PATH}")
    if not ENRICH_PATH.exists():
        raise FileNotFoundError(f"Missing enrich script: {ENRICH_PATH}")
    raw = ["viltrox"]
    with CATALOG_PATH.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            raw.extend([text(row.get("model_name")), text(row.get("marketing_name"))])
    raw.extend(extract_keywords_from_enrich(ENRICH_PATH))
    seen: set[str] = set()
    keywords: list[str] = []
    for item in raw:
        keyword = clean_keyword(item)
        if len(keyword) < 3 or keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
    return keywords


def iso8601_duration_seconds(value: str) -> int | None:
    if not value:
        return None
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value,
    )
    if not match:
        return None
    parts = {key: int(val or 0) for key, val in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_compact_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    raw = text(value).replace(",", "")
    if not raw:
        return None
    match = re.fullmatch(r"(?i)(\d+(?:\.\d+)?)([km])?", raw)
    if not match:
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else None
    number = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


def parse_clock_duration(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    raw = text(value)
    if not raw:
        return None
    if raw.startswith("PT"):
        return iso8601_duration_seconds(raw)
    parts = raw.split(":")
    if not all(part.isdigit() for part in parts):
        return None
    values = [int(part) for part in parts]
    if len(values) == 2:
        return values[0] * 60 + values[1]
    if len(values) == 3:
        return values[0] * 3600 + values[1] * 60 + values[2]
    return None


def normalize_publish_date(value: Any) -> tuple[str, str]:
    raw = text(value)
    if not raw:
        return "", ""
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw, ""
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}T00:00:00Z", ""
    if re.search(r"(?i)\b(second|minute|hour|day|week|month|year)s? ago\b", raw):
        return "", f"relative_publish_date:{raw}"
    return raw, ""


def channel_url_for(kol: KolTarget) -> str:
    if "youtube.com" in kol.profile_url.lower() or "youtu.be" in kol.profile_url.lower():
        return kol.profile_url
    handle = re.sub(r"\s+", "", kol.handle or kol.display_name).strip().lstrip("@")
    return f"https://www.youtube.com/@{handle}/videos" if handle else ""


def apify_videos_url(channel_url: str) -> str:
    url = channel_url.strip()
    if url.endswith("/videos"):
        return url
    if re.search(r"youtube\.com/@[^/?#]+/?$", url):
        return url.rstrip("/") + "/videos"
    if re.search(r"youtube\.com/channel/[^/?#]+/?$", url):
        return url.rstrip("/") + "/videos"
    if "youtube.com" not in url:
        print(f"[warn] Apify channel URL is not a YouTube URL, trying raw URL: {url}", flush=True)
    return url


def extract_channel_hint(channel_url: str, kol: KolTarget) -> tuple[str, str]:
    url = channel_url.strip()
    match = re.search(r"youtube\.com/channel/([^/?#]+)", url)
    if match:
        return "id", match.group(1)
    match = re.search(r"youtube\.com/@([^/?#]+)", url)
    if match:
        return "handle", "@" + match.group(1).strip("@")
    match = re.search(r"youtube\.com/([^/?#]+)", url)
    if match:
        username = match.group(1).strip()
        if username and username not in {"watch", "shorts", "playlist", "results"}:
            return "username", username
    fallback = re.sub(r"\s+", "", kol.handle or kol.display_name).strip().lstrip("@")
    return ("handle", "@" + fallback) if fallback else ("", "")


class YouTubeApi:
    def __init__(self, key: str):
        self.key = key
        self.units = 0
        self.quota_exhausted = False

    def get(self, endpoint: str, params: dict[str, Any], cost: int = 1) -> dict[str, Any]:
        params = {**params, "key": self.key}
        url = f"{YOUTUBE_API_BASE}/{endpoint}?{urllib.parse.urlencode(params, doseq=True)}"
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                self.units += cost
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403 and "quotaExceeded" in body:
                self.quota_exhausted = True
                raise RuntimeError("quotaExceeded")
            raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

    def resolve_channel(self, channel_url: str, kol: KolTarget) -> tuple[str, str]:
        hint_type, hint = extract_channel_hint(channel_url, kol)
        if not hint:
            raise RuntimeError("missing channel hint")
        if hint_type == "id":
            data = self.get("channels", {"part": "contentDetails,snippet", "id": hint})
        elif hint_type == "username":
            data = self.get("channels", {"part": "contentDetails,snippet", "forUsername": hint})
            if not data.get("items"):
                raise RuntimeError(f"channel not found forUsername={hint}")
        else:
            data = self.get("channels", {"part": "contentDetails,snippet", "forHandle": hint})
            if not data.get("items"):
                raise RuntimeError("channel not found")
        items = data.get("items") or []
        if not items:
            raise RuntimeError("channel not found")
        channel = items[0]
        uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        return channel["id"], uploads

    def fetch_recent_video_ids(self, uploads_playlist_id: str, max_per_channel: int) -> list[str]:
        data = self.get(
            "playlistItems",
            {
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": min(50, max_per_channel),
            },
        )
        return [
            text(item.get("contentDetails", {}).get("videoId"))
            for item in data.get("items", [])
            if text(item.get("contentDetails", {}).get("videoId"))
        ][:max_per_channel]

    def fetch_video_details(self, video_ids: list[str]) -> list[dict[str, Any]]:
        if not video_ids:
            return []
        data = self.get(
            "videos",
            {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids[:50]),
                "maxResults": len(video_ids[:50]),
            },
        )
        return data.get("items") or []


def match_keywords(title: str, description: str, keywords: list[str]) -> list[str]:
    haystack = f"{title} {description}".lower()
    return [keyword for keyword in keywords if keyword in haystack]


def from_youtube_item(kol: KolTarget, item: dict[str, Any], keywords: list[str]) -> VideoCandidate | None:
    snippet = item.get("snippet") or {}
    statistics = item.get("statistics") or {}
    details = item.get("contentDetails") or {}
    title = text(snippet.get("title"))
    description = text(snippet.get("description"))
    hits = match_keywords(title, description, keywords)
    if not hits:
        return None
    thumbs = snippet.get("thumbnails") or {}
    thumb = text((thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url"))
    video_id = text(item.get("id"))
    return VideoCandidate(
        kol_id=kol.id,
        kol_name=kol.display_name or kol.handle,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        title=title,
        description=description,
        view_count=as_int(statistics.get("viewCount")),
        like_count=as_int(statistics.get("likeCount")),
        comment_count=as_int(statistics.get("commentCount")),
        publish_date=text(snippet.get("publishedAt")),
        duration_seconds=iso8601_duration_seconds(text(details.get("duration"))),
        thumbnail_url=thumb,
        channel_id=text(snippet.get("channelId")),
        channel_name=text(snippet.get("channelTitle")),
        scrape_source="youtube_api",
        scrape_status="success",
        matched_keywords=hits[:20],
        confidence="high" if len(hits) >= 2 else "medium",
    )


def scrape_with_youtube_api(kol: KolTarget, keywords: list[str], max_per_channel: int) -> KolRun:
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    channel_url = channel_url_for(kol)
    if not channel_url:
        return KolRun(kol, channel_url, "youtube_api", "bad_url", error="missing channel url")
    if not key:
        return KolRun(kol, channel_url, "youtube_api", "skipped", error="YOUTUBE_API_KEY not set")
    api = YouTubeApi(key)
    try:
        channel_id, uploads = api.resolve_channel(channel_url, kol)
        video_ids = api.fetch_recent_video_ids(uploads, max_per_channel)
        details = api.fetch_video_details(video_ids)
        matches = [candidate for item in details if (candidate := from_youtube_item(kol, item, keywords))]
        print(
            f"[KOL {kol.id}] trying youtube_api... channel_id={channel_id}, "
            f"found {len(details)} videos, matched {len(matches)} viltrox",
            flush=True,
        )
        if not matches:
            return KolRun(kol, channel_url, "youtube_api", "no_match", len(details), matches, "no viltrox keyword match", api.units)
        return KolRun(kol, channel_url, "youtube_api", "success", len(details), matches, api_units=api.units)
    except RuntimeError as exc:
        status = "quota_exhausted" if "quotaExceeded" in str(exc) else "error"
        print(f"[KOL {kol.id}] trying youtube_api... {str(exc)[:160]}, falling back to apify...", flush=True)
        return KolRun(kol, channel_url, "youtube_api", status, error=str(exc)[:300], api_units=api.units)


def apify_run_input(channel_url: str, max_per_channel: int) -> dict[str, Any]:
    videos_url = apify_videos_url(channel_url)
    return {
        "startUrls": [{"url": videos_url}],
        "maxResults": max_per_channel,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
    }


def extract_channel_id_from_url(value: Any) -> str:
    raw = text(value)
    match = re.search(r"youtube\.com/channel/([^/?#]+)", raw)
    if match:
        return match.group(1)
    match = re.search(r"youtube\.com/@([^/?#]+)", raw)
    if match:
        return "@" + match.group(1)
    return ""


def map_apify_item(kol: KolTarget, item: dict[str, Any], keywords: list[str]) -> VideoCandidate | None:
    title = text(item.get("title") or item.get("text") or item.get("name"))
    description = text(item.get("description") or item.get("desc") or item.get("shortDescription"))
    hits = match_keywords(title, description, keywords)
    if not hits:
        return None
    url = text(item.get("url") or item.get("videoUrl") or item.get("webpageUrl") or item.get("link"))
    if not url and text(item.get("id")):
        url = f"https://www.youtube.com/watch?v={text(item.get('id'))}"
    publish_date, scrape_error = normalize_publish_date(
        item.get("date") or item.get("publishedAt") or item.get("uploadDate")
    )
    channel_url = item.get("channelUrl") or item.get("channelURL") or item.get("channel_url")
    return VideoCandidate(
        kol_id=kol.id,
        kol_name=kol.display_name or kol.handle,
        video_url=url,
        title=title,
        description=description,
        view_count=parse_compact_int(item.get("viewCount") or item.get("views")),
        like_count=parse_compact_int(item.get("likes") or item.get("likeCount")),
        comment_count=parse_compact_int(item.get("commentsCount") or item.get("commentCount")),
        publish_date=publish_date,
        duration_seconds=parse_clock_duration(item.get("duration") or item.get("durationSeconds")),
        thumbnail_url=text(item.get("thumbnailUrl") or item.get("thumbnail") or item.get("image")),
        channel_id=text(item.get("channelId") or item.get("channel_id") or extract_channel_id_from_url(channel_url)),
        channel_name=text(item.get("channelName") or item.get("channelTitle") or item.get("channel")),
        scrape_source="apify",
        scrape_status="success",
        scrape_error=scrape_error,
        matched_keywords=hits[:20],
        confidence="high" if len(hits) >= 2 else "medium",
    )


def scrape_with_apify(kol: KolTarget, keywords: list[str], max_per_channel: int) -> KolRun:
    token = os.environ.get("APIFY_API_TOKEN") or os.environ.get("APIFY_TOKEN") or ""
    actor_id = os.environ.get("APIFY_YOUTUBE_ACTOR_ID", "").strip()
    channel_url = channel_url_for(kol)
    if not channel_url:
        return KolRun(kol, channel_url, "apify", "bad_url", error="missing channel url")
    if not token.strip():
        return KolRun(kol, channel_url, "apify", "apify_missing_token", error="APIFY_API_TOKEN not set")
    if not actor_id:
        return KolRun(kol, channel_url, "apify", "apify_missing_actor", error="APIFY_YOUTUBE_ACTOR_ID not set")
    try:
        from apify_client import ApifyClient

        client = ApifyClient(token)
        run_input = apify_run_input(channel_url, max_per_channel)
        run = client.actor(actor_id).call(run_input=run_input)
        run_id = text(run.get("id"))
        print(f"[KOL {kol.id}] apify actor run started, run_id={run_id}", flush=True)
        dataset_id = run.get("defaultDatasetId")
        items = client.dataset(dataset_id).list_items().items if dataset_id else []
        for index, item in enumerate(items[:3], start=1):
            print(
                f"[KOL {kol.id}] apify sample {index}: "
                f"title->{text(item.get('title'))[:60]} | "
                f"url->{text(item.get('url'))[:80]} | "
                f"viewCount->{text(item.get('viewCount') or item.get('views'))} | "
                f"likes->{text(item.get('likes') or item.get('likeCount'))} | "
                f"commentsCount->{text(item.get('commentsCount') or item.get('commentCount'))} | "
                f"date->{text(item.get('date') or item.get('publishedAt'))} | "
                f"duration->{text(item.get('duration') or item.get('durationSeconds'))}",
                flush=True,
            )
        matches = [candidate for item in items if (candidate := map_apify_item(kol, item, keywords))]
        print(f"[KOL {kol.id}] apify dataset items: {len(items)}, matched {len(matches)} viltrox", flush=True)
        status = "success" if matches else "no_match"
        return KolRun(
            kol,
            channel_url,
            "apify",
            status,
            len(items),
            matches,
            apify_run_id=run_id,
            apify_dataset_items=len(items),
            apify_sample_rows=list(items[:3]),
        )
    except Exception as exc:
        return KolRun(kol, channel_url, "apify", "error", error=str(exc)[:300])


def scrape_kol_youtube(kol: KolTarget, keywords: list[str], max_per_channel: int, force_apify: bool = False) -> list[KolRun]:
    runs: list[KolRun] = []
    if not force_apify:
        api_run = scrape_with_youtube_api(kol, keywords, max_per_channel)
        runs.append(api_run)
        if api_run.status == "success":
            return runs
        if api_run.status == "no_match":
            return runs
    apify_run = scrape_with_apify(kol, keywords, max_per_channel)
    if apify_run.status.startswith("apify_missing"):
        print(f"[KOL {kol.id}] {apify_run.status}: {apify_run.error}", flush=True)
    runs.append(apify_run)
    return runs


def existing_urls(urls: list[str]) -> set[str]:
    if not urls:
        return set()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT content_url FROM vkpi_kol_video_evidence WHERE content_url = ANY(%s)", (urls,))
            return {row[0] for row in cur.fetchall()}


def write_csv(path: Path, candidates: list[VideoCandidate]) -> None:
    fields = [
        "kol_id", "kol_name", "video_url", "title", "view_count", "like_count",
        "comment_count", "publish_date", "duration_seconds", "channel_id",
        "channel_name", "scrape_source", "scrape_status", "matched_keywords", "confidence",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in candidates:
            writer.writerow(
                {
                    "kol_id": item.kol_id,
                    "kol_name": item.kol_name,
                    "video_url": item.video_url,
                    "title": item.title,
                    "view_count": item.view_count,
                    "like_count": item.like_count,
                    "comment_count": item.comment_count,
                    "publish_date": item.publish_date,
                    "duration_seconds": item.duration_seconds,
                    "channel_id": item.channel_id,
                    "channel_name": item.channel_name,
                    "scrape_source": item.scrape_source,
                    "scrape_status": item.scrape_status,
                    "matched_keywords": ", ".join(item.matched_keywords[:5]),
                    "confidence": item.confidence,
                }
            )


def apify_cost_estimate(apify_call_count: int) -> str:
    if apify_call_count <= 0:
        return "$0.00"
    return f"~${0.02 * apify_call_count:.2f}"


def completeness(candidates: list[VideoCandidate], source: str, field_name: str) -> str:
    scoped = [item for item in candidates if item.scrape_source == source]
    if not scoped:
        return "0/0"
    filled = sum(1 for item in scoped if getattr(item, field_name))
    return f"{filled}/{len(scoped)}"


def report_md(
    runs: list[KolRun],
    candidates: list[VideoCandidate],
    keyword_count: int,
    predicted_insert: int,
    duplicate_skip: int,
) -> str:
    status_counts = Counter(f"{run.layer}:{run.status}" for run in runs)
    keyword_hits = Counter()
    for item in candidates:
        keyword_hits.update(item.matched_keywords)
    api_units = sum(run.api_units for run in runs)
    apify_runs = [run for run in runs if run.layer == "apify" and run.apify_run_id]
    source_counts = Counter(candidate.scrape_source for candidate in candidates)

    lines = [
        "# Step 4a YouTube Full Dry-run Report",
        "",
        "## 总览",
        f"- 关键词总数: {keyword_count}",
        f"- KOL 数: {len({run.kol.id for run in runs})}",
        f"- 找到 Viltrox 视频: {len(candidates)}",
        f"- 新增 evidence 预测: {predicted_insert}",
        f"- ON CONFLICT 预计跳过重复 content_url: {duplicate_skip}",
        f"- YouTube API quota 估算消耗: {api_units} units",
        f"- Apify 调用次数: {len(apify_runs)}",
        f"- Apify cost 估算: {apify_cost_estimate(len(apify_runs))}",
        "",
        "## 每个 KOL 处理流程",
    ]
    for kol_id in sorted({run.kol.id for run in runs}):
        for run in [item for item in runs if item.kol.id == kol_id]:
            lines.append(
                f"- `{run.kol.id}` {run.kol.display_name} | layer={run.layer} | "
                f"status={run.status} | scanned={run.scanned_count} | matched={len(run.matches)}"
            )
            if run.apify_run_id:
                lines.append(f"  - Apify run id: `{run.apify_run_id}`")
            if run.error:
                lines.append(f"  - error: {run.error[:240]}")
            for candidate in run.matches[:3]:
                lines.append(f"  - {candidate.title[:160]}")

    lines.extend(["", "## scrape_status 分布"])
    for key, count in sorted(status_counts.items()):
        lines.append(f"- {key}: {count}")

    lines.extend(["", "## scrape_source 分布"])
    if source_counts:
        for key, count in sorted(source_counts.items()):
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- 无 matched evidence")

    lines.extend(["", "## 重点 KOL 检查"])
    for special_id in (4008, 4044, 3960):
        special_runs = [run for run in runs if run.kol.id == special_id]
        if not special_runs:
            continue
        for run in special_runs:
            lines.append(
                f"- `{run.kol.id}` {run.kol.display_name}: layer={run.layer}, "
                f"status={run.status}, scanned={run.scanned_count}, matched={len(run.matches)}, "
                f"url={run.channel_url}"
            )

    lines.extend(["", "## 字段完整度对比"])
    for source in ("youtube_api", "apify"):
        lines.append(f"- {source}")
        for field_name in ("view_count", "like_count", "comment_count", "publish_date", "duration_seconds", "thumbnail_url", "channel_id", "channel_name"):
            lines.append(f"  - {field_name}: {completeness(candidates, source, field_name)}")

    lines.extend(
        [
            "",
            "## Actor 字段映射",
            "- title -> title",
            "- url -> content_url",
            "- viewCount/views -> view_count",
            "- likes/likeCount -> like_count",
            "- commentsCount/commentCount -> comment_count",
            "- date/publishedAt/uploadDate -> publish_date",
            "- duration/durationSeconds -> duration_seconds",
            "- thumbnailUrl/thumbnail/image -> thumbnail_url",
            "- channelName/channelTitle/channel -> channel_name",
            "- channelUrl/channelId -> channel_id",
        ]
    )
    sample_runs = [run for run in runs if run.apify_sample_rows]
    if sample_runs:
        lines.append("")
        lines.append("## Apify Dataset Sample Fields")
        for run in sample_runs:
            lines.append(f"- `{run.kol.id}` {run.kol.display_name}")
            for idx, item in enumerate(run.apify_sample_rows[:3], start=1):
                lines.append(
                    f"  - sample {idx}: keys={', '.join(sorted(str(key) for key in item.keys())[:20])}"
                )
    lines.extend(
        [
            "",
            "## Apify vs YouTube API 字段质量评估",
            "- YouTube API: quota 可控；snippet/statistics/contentDetails 字段稳定，publish_date/duration/channel_id 完整。",
            "- Apify: 适合作为 API 失败 fallback；字段名依赖 actor 输出，需用 POC sample 校准映射。",
            "- 建议：YouTube API 作为主路径；仅 quota/404/network error 时进入 Apify，no_match 不 fallback。",
        ]
    )

    non_empty = max(1, len(candidates))
    high = sum(1 for item in candidates if item.confidence == "high")
    lines.extend(
        [
            "",
            "## 数据质量统计",
            f"- confidence=high 占比: {high / non_empty:.1%}",
            f"- view_count 有值占比: {sum(1 for item in candidates if item.view_count is not None) / non_empty:.1%}",
            f"- publish_date 有值占比: {sum(1 for item in candidates if item.publish_date) / non_empty:.1%}",
            "",
            "## 关键词命中 Top 10",
        ]
    )
    for keyword, count in keyword_hits.most_common(10):
        lines.append(f"- {keyword}: {count}")
    return "\n".join(lines) + "\n"


def insert_candidates(candidates: list[VideoCandidate]) -> int:
    sql = """
        INSERT INTO vkpi_kol_video_evidence (
          kol_pool_id, content_url, evidence_type, source, source_ref,
          confidence, title, view_count, like_count, comment_count,
          publish_date, duration_seconds, thumbnail_url, channel_id,
          channel_name, scrape_status, scraped_at, scrape_source
        )
        VALUES (
          %(kol_id)s, %(video_url)s, 'video', %(scrape_source)s, 'step4a_youtube',
          %(confidence)s, %(title)s, %(view_count)s, %(like_count)s, %(comment_count)s,
          %(publish_date)s, %(duration_seconds)s, %(thumbnail_url)s, %(channel_id)s,
          %(channel_name)s, %(scrape_status)s, NOW(), %(scrape_source)s
        )
        ON CONFLICT (content_url) DO NOTHING
    """
    rows = [item.__dict__ for item in candidates]
    with connect_db() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=100)
            return cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 4a YouTube API + Apify evidence scraper.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--commit", action="store_true", default=False)
    parser.add_argument("--kol-ids", default="")
    parser.add_argument("--max-per-channel", type=int, default=50)
    parser.add_argument("--force-apify", action="store_true")
    args = parser.parse_args()
    if not args.commit:
        args.dry_run = True

    load_env()
    keywords = load_keywords()
    targets = load_targets(parse_ids(args.kol_ids))
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    runs: list[KolRun] = []
    for kol in targets:
        runs.extend(scrape_kol_youtube(kol, keywords, args.max_per_channel, force_apify=args.force_apify))
        time.sleep(0.2)
    candidates = [candidate for run in runs for candidate in run.matches]
    existing = existing_urls([item.video_url for item in candidates])
    predicted = sum(1 for item in candidates if item.video_url not in existing)
    duplicate_skip = len(candidates) - predicted

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    prefix = "step4a_full" if len(targets) > 2 else "step4a_poc2"
    csv_path = ARTIFACT_DIR / f"{prefix}_dryrun_{stamp}.csv"
    report_path = ARTIFACT_DIR / f"{prefix}_report_{stamp}.md"
    write_csv(csv_path, candidates)
    report = report_md(runs, candidates, len(keywords), predicted, duplicate_skip)
    report_path.write_text(report, encoding="utf-8")
    print(f"CSV: {csv_path}")
    print(f"Markdown: {report_path}")
    print(report)

    if args.commit:
        inserted = insert_candidates([item for item in candidates if item.video_url not in existing])
        print(f"Inserted rows: {inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
