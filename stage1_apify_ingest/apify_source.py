"""
Apify source layer.

This is the only module that talks to Apify. It returns plain dicts so the
runner stays storage/transport agnostic.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from apify_client import ApifyClient

from .config import Settings


def load_kol_list(settings: Settings) -> list[dict]:
    """
    Return [{"kol_id": str, "channel": str | None, "platform": "youtube",
             "url_source": "url" | "handle" | "none"}, ...].

    The default source is the V-KPI `vkpi_kol_pool` table. `kol_id` is the table
    primary key (`id`) as a string so Stage 2 can join results back to V-KPI.
    A CSV source can be supplied with STAGE1_KOL_CSV/KOL_SOURCE_CSV.
    """
    del settings
    csv_path = _env("STAGE1_KOL_CSV", "KOL_SOURCE_CSV")
    rows = _load_kols_from_csv(csv_path) if csv_path else _load_kols_from_db()
    return _dedupe_kols(rows)


class ApifySource:
    def __init__(self, settings: Settings):
        self.s = settings
        self.client = ApifyClient(settings.apify_token)

    def fetch_channel(self, channel: str) -> dict:
        run_input = {
            "startUrls": [{"url": self._channel_url(channel)}],
            "maxResults": self.s.max_videos_per_kol or 99999,
        }
        run = self.client.actor(self._actor_path(self.s.apify_channel_actor)).call(run_input=run_input)
        items = list(self.client.dataset(run["defaultDatasetId"]).iterate_items())
        _print_shape("channel", items)
        channel_meta = self._extract_channel_meta(items)
        videos = [self._extract_video(item) for item in items if self._is_video(item)]
        videos = [video for video in videos if video.get("video_id")]
        return {
            "channel_meta": channel_meta,
            "videos": videos,
            "source": {
                "actor_id": self.s.apify_channel_actor,
                "run_id": run.get("id"),
                "dataset_id": run.get("defaultDatasetId"),
                "item_count": len(items),
            },
        }

    def fetch_transcript(self, video_id: str) -> dict:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        run_input = {"videoUrl": video_url}
        run = self.client.actor(self._actor_path(self.s.apify_transcript_actor)).call(run_input=run_input)
        items = list(self.client.dataset(run["defaultDatasetId"]).iterate_items())
        _print_shape("transcript", items)
        transcript = _extract_transcript_items(video_id, items)
        transcript["source"] = {
            "actor_id": self.s.apify_transcript_actor,
            "run_id": run.get("id"),
            "dataset_id": run.get("defaultDatasetId"),
            "item_count": len(items),
        }
        return transcript

    @staticmethod
    def _actor_path(actor_id: str) -> str:
        return str(actor_id or "").strip().replace("/", "~")

    @staticmethod
    def _channel_url(channel: str) -> str:
        raw = str(channel or "").strip()
        if not raw:
            raise ValueError("empty YouTube channel")
        if raw.startswith("http"):
            if "youtu.be" in raw.lower():
                return raw
            return _youtube_videos_url(raw)
        if raw.startswith("UC"):
            return f"https://www.youtube.com/channel/{raw}/videos"
        handle = raw.lstrip("@")
        return f"https://www.youtube.com/@{handle}/videos"

    @staticmethod
    def _is_video(item: dict) -> bool:
        if not isinstance(item, dict):
            return False
        if _extract_video_id(_first_key_text(item, "url", "videoUrl", "webpage_url")):
            return True
        candidate = _first_key_text(item, "id", "videoId", "video_id")
        return bool(candidate and not candidate.startswith("UC"))

    @staticmethod
    def _extract_video(item: dict) -> dict:
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        content_details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
        url = _first_key_text(item, "url", "videoUrl", "webpage_url")
        video_id = _extract_video_id(url) or _first_key_text(item, "id", "videoId", "video_id")
        thumbnail = _thumbnail_url(item, snippet)
        raw_published_at = _first_text(
            item.get("date"),
            item.get("publishedAt"),
            item.get("published_at"),
            item.get("uploadDate"),
            snippet.get("publishedAt"),
        )
        raw_duration = _first_present(item.get("duration"), content_details.get("duration"))
        return {
            "video_id": video_id,
            "title": _first_text(item.get("title"), snippet.get("title")),
            "published_at": normalize_published_at(raw_published_at),
            "duration_s": normalize_duration_s(raw_duration),
            "view_count": normalize_count(_first_present(item.get("viewCount"), item.get("views"), statistics.get("viewCount"))),
            "like_count": normalize_count(_first_present(item.get("likeCount"), item.get("likes"), statistics.get("likeCount"))),
            "comment_count": _int_or_none(
                _first_present(item.get("commentCount"), item.get("commentsCount"), item.get("comments"), statistics.get("commentCount"))
            ),
            "url": url or (f"https://www.youtube.com/watch?v={video_id}" if video_id else ""),
            "thumbnail_url": thumbnail,
            "description": _first_text(item.get("description"), item.get("text"), snippet.get("description")),
            "channel_id": _first_text(item.get("channelId"), snippet.get("channelId"), _extract_channel_id(_first_key_text(item, "channelUrl"))),
            "channel_name": _first_text(item.get("channelName"), item.get("channelTitle"), snippet.get("channelTitle")),
            "raw": item,
        }

    @staticmethod
    def _extract_channel_meta(items: Iterable[dict]) -> dict:
        item_list = [item for item in items if isinstance(item, dict)]
        first = item_list[0] if item_list else {}
        snippet = first.get("snippet") if isinstance(first.get("snippet"), dict) else {}
        statistics = first.get("statistics") if isinstance(first.get("statistics"), dict) else {}
        about = first.get("aboutChannelInfo") if isinstance(first.get("aboutChannelInfo"), dict) else {}
        channel_url = _first_text(_first_key_text(first, "channelUrl", "channel_url"), about.get("channelUrl"))
        channel_id = _first_text(first.get("channelId"), about.get("channelId"), snippet.get("channelId"), _extract_channel_id(channel_url))
        name = _first_text(first.get("channelName"), about.get("channelName"), first.get("channelTitle"), snippet.get("channelTitle"))
        return {
            "channel_id": channel_id,
            "name": name,
            "handle": _extract_handle(channel_url),
            "url": channel_url,
            "subscribers": _int_or_none(
                _first_present(
                    first.get("numberOfSubscribers"),
                    about.get("numberOfSubscribers"),
                    first.get("subscriberCount"),
                    first.get("subscribers"),
                    statistics.get("subscriberCount"),
                )
            ),
            "video_count": _int_or_none(about.get("channelTotalVideos")) or len(item_list),
            "raw_sample": first,
        }


def _load_kols_from_csv(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result: list[dict] = []
    for row in rows:
        kol_id = _first_text(row.get("kol_id"), row.get("id"), row.get("kol_pool_id"))
        profile_url = _first_text(row.get("profile_url"), row.get("channel_url"))
        handle = _first_text(row.get("handle"))
        platform = _first_text(row.get("platform"), "youtube").lower()
        if kol_id and platform == "youtube":
            result.append(_shape_kol_row(kol_id, profile_url, handle))
    if not result:
        raise RuntimeError(f"no youtube KOL rows loaded from CSV: {path}")
    return result


def _load_kols_from_db() -> list[dict]:
    database_url = _env("DATABASE_URL", "LOCAL_DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL or STAGE1_KOL_CSV is required for load_kol_list()")
    sql = """
    COPY (
      SELECT
        id::text AS kol_id,
        trim(coalesce(profile_url, '')) AS profile_url,
        trim(coalesce(handle, '')) AS handle,
        'youtube' AS platform
      FROM vkpi_kol_pool
      WHERE lower(coalesce(platform, '')) = 'youtube'
      ORDER BY id
    ) TO STDOUT WITH CSV HEADER
    """
    proc = subprocess.run(
        ["psql", database_url, "-X", "-q", "-c", sql],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed loading vkpi_kol_pool: {proc.stderr.strip()}")
    return list(csv.DictReader(proc.stdout.splitlines()))


def _dedupe_kols(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        kol_id = str(row.get("kol_id") or "").strip()
        if not kol_id or kol_id in seen:
            continue
        if "url_source" in row:
            shaped = dict(row)
        else:
            shaped = _shape_kol_row(kol_id, str(row.get("profile_url") or ""), str(row.get("handle") or ""))
        result.append(shaped)
        seen.add(kol_id)
    if not result:
        raise RuntimeError("load_kol_list() found no YouTube KOLs")
    return result


def _shape_kol_row(kol_id: str, profile_url: str, handle: str) -> dict:
    profile_url = str(profile_url or "").strip()
    handle = str(handle or "").strip().lstrip("@")
    if _is_youtube_url(profile_url):
        return {"kol_id": kol_id, "channel": profile_url, "platform": "youtube", "url_source": "url"}
    if handle:
        return {
            "kol_id": kol_id,
            "channel": f"https://www.youtube.com/@{handle}",
            "platform": "youtube",
            "url_source": "handle",
        }
    return {
        "kol_id": kol_id,
        "channel": None,
        "platform": "youtube",
        "url_source": "none",
        "source_url_status": "no_resolvable_url",
    }


def _extract_transcript_items(video_id: str, items: list[dict]) -> dict:
    segments: list[dict] = []
    language = ""
    full_text = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        language = language or _first_key_text(item, "lang", "language", "languageCode")
        full_text = full_text or _first_key_text(item, "text", "transcript", "content")
        segment_source = _first_present(
            item.get("segments"),
            item.get("captions"),
            item.get("subtitles"),
            item.get("transcriptItems"),
            item.get("data"),
        )
        if isinstance(segment_source, list):
            segments.extend(_normalize_segments(segment_source))
    if not segments:
        segments = _normalize_segments(items)
    if not full_text and segments:
        full_text = " ".join(segment["text"] for segment in segments if segment.get("text")).strip()
    return {
        "video_id": video_id,
        "lang": language,
        "segments": segments,
        "text": full_text,
        "raw": items,
    }


def _normalize_segments(items: list[Any]) -> list[dict]:
    segments: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _first_key_text(item, "text", "content", "caption")
        if not text:
            continue
        segments.append(
            {
                "start": _float_or_none(_first_present(item.get("start"), item.get("startTime"), item.get("offset"))) or 0.0,
                "dur": _float_or_none(_first_present(item.get("dur"), item.get("duration"), item.get("duration_s"))) or 0.0,
                "text": text,
            }
        )
    return segments


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, dict):
            continue
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_key_text(mapping: dict, *keys: str) -> str:
    for key in keys:
        text = str(mapping.get(key) or "").strip()
        if text:
            return text
    return ""


def _int_or_none(value: Any) -> int | None:
    return normalize_count(value)


def normalize_count(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip().replace(",", "")
        match = re.match(r"^(-?\d+(?:\.\d+)?)([KMB])?$", text, flags=re.I)
        if match:
            number = float(match.group(1))
            suffix = (match.group(2) or "").upper()
            multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
            return int(number * multiplier)
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _duration_seconds(value: Any) -> int | None:
    return normalize_duration_s(value)


def normalize_duration_s(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    iso = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", text)
    if iso:
        days, hours, minutes, seconds = (int(part or 0) for part in iso.groups())
        return days * 86400 + hours * 3600 + minutes * 60 + seconds
    if re.match(r"^\d+(?::\d{1,2}){1,2}$", text) or re.match(r"^\d+:\d{2}$", text):
        parts = [int(part) for part in text.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return _int_or_none(text)


def normalize_published_at(value: Any, now: datetime | None = None) -> str:
    if value in (None, ""):
        return ""
    if now is None:
        now = datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    text = str(value).strip()
    if not text:
        return ""
    if re.match(r"^\d+(?:\.\d+)?$", text):
        return datetime.fromtimestamp(float(text), tz=timezone.utc).isoformat()
    parsed = _parse_datetime_text(text)
    if parsed:
        return parsed.astimezone(timezone.utc).isoformat()
    relative = _parse_relative_datetime(text, now)
    if relative:
        return relative.astimezone(timezone.utc).isoformat()
    return ""


def _parse_datetime_text(text: str) -> datetime | None:
    normalized = text
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    for candidate in (normalized, normalized.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_relative_datetime(text: str, now: datetime) -> datetime | None:
    match = re.match(
        r"^(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago$",
        text.strip(),
        flags=re.I,
    )
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "second":
        delta = timedelta(seconds=amount)
    elif unit == "minute":
        delta = timedelta(minutes=amount)
    elif unit == "hour":
        delta = timedelta(hours=amount)
    elif unit == "day":
        delta = timedelta(days=amount)
    elif unit == "week":
        delta = timedelta(weeks=amount)
    elif unit == "month":
        delta = timedelta(days=30 * amount)
    else:
        delta = timedelta(days=365 * amount)
    return now - delta


def _extract_video_id(url_or_id: str) -> str:
    raw = str(url_or_id or "").strip()
    if not raw:
        return ""
    if len(raw) == 11 and "/" not in raw and "?" not in raw:
        return raw
    parsed = urlparse(raw if "://" in raw else "")
    if parsed.netloc:
        if parsed.netloc.endswith("youtu.be"):
            return parsed.path.strip("/").split("/")[0]
        query_video = parse_qs(parsed.query).get("v", [""])[0]
        if query_video:
            return query_video
        if "/shorts/" in parsed.path:
            return parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
    return ""


def _extract_channel_id(url_or_id: str) -> str:
    raw = str(url_or_id or "").strip()
    if raw.startswith("UC") and "/" not in raw:
        return raw
    parsed = urlparse(raw if "://" in raw else "")
    path = parsed.path.strip("/") if parsed.netloc else raw.strip("/")
    if path.lower().startswith("channel/"):
        return path.split("/", 1)[1].split("/", 1)[0]
    return ""


def _extract_handle(url: str) -> str:
    match = re.search(r"(?:youtube\.com/)?@([^/?#]+)", str(url or ""), flags=re.I)
    return match.group(1) if match else ""


def _youtube_videos_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path.endswith("/videos"):
        return url
    if path:
        return f"{parsed.scheme}://{parsed.netloc}{path}/videos"
    return url


def _is_youtube_url(value: str) -> bool:
    text = str(value or "").strip().lower()
    return "youtube.com" in text or "youtu.be" in text


def _thumbnail_url(item: dict, snippet: dict) -> str:
    value = _first_text(item.get("thumbnailUrl"), item.get("thumbnail"))
    if value:
        return value
    thumbs = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        thumb = thumbs.get(key)
        if isinstance(thumb, dict) and thumb.get("url"):
            return str(thumb["url"])
    return ""


def _print_shape(label: str, items: list[dict]) -> None:
    if os.environ.get("STAGE1_PRINT_APIFY_SHAPE", "").strip() != "1":
        return
    sample = next((item for item in items if isinstance(item, dict)), {})
    print(f"[shape:{label}] item_count={len(items)}")
    print(json.dumps({"keys": sorted(sample.keys()), "sample": sample}, ensure_ascii=False, indent=2)[:8000])
