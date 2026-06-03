#!/usr/bin/env python3
"""Backfill KOL Pool profile basics without touching fit scores.

This script is intentionally separate from kol_pool.refresh_pool_item because
that path recalculates viltrox_fit_score. This script only writes profile-level
fields and raw crawler payloads.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - local dependency guard.
    load_dotenv = None  # type: ignore[assignment]

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.domains.industry.snapshot_kpis import calculate_kpis  # noqa: E402
from app.domains.kol.pool_common import (  # noqa: E402
    _average_from_total,
    _bio,
    _content_items_from_payload,
    _display_name,
    _first_present,
    _float_or_none,
    _int_or_none,
    _json,
    _looks_like_content_item,
    _normalize_sync_status,
    _platform,
    _profile_item,
    _profile_stats,
    _profile_url,
    _thumb_url,
)
from app.platform.industry_crawlers.instagram_crawler import InstagramCrawler  # noqa: E402
from app.platform.industry_crawlers.tiktok_crawler import TikTokCrawler  # noqa: E402
from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler  # noqa: E402


SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok"}
GENERIC_BAD_HANDLES = {
    "about",
    "blog",
    "c",
    "channel",
    "contact",
    "dp",
    "explore",
    "p",
    "post",
    "posts",
    "product",
    "products",
    "reel",
    "reels",
    "short",
    "shorts",
    "tv",
    "user",
    "videos",
    "watch",
}
PILOT_QUOTAS: dict[str, list[tuple[str, int]]] = {
    "youtube": [("recallable", 3), ("pending_data", 3), ("empty", 4)],
    "instagram": [("recallable", 3), ("pending_data", 3), ("empty", 4)],
    "tiktok": [("recallable", 4), ("pending_data", 2), ("empty", 4)],
}
APIFY_PRICE_PER_1000 = {
    "apify~instagram-profile-scraper": 1.60,
    "apify/instagram-profile-scraper": 1.60,
    "clockworks~tiktok-scraper": 1.70,
    "clockworks/tiktok-scraper": 1.70,
    "streamers~youtube-scraper": 2.40,
    "streamers/youtube-scraper": 2.40,
}


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    return value


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _is_url(value: str) -> bool:
    return bool(re.match(r"https?://", str(value or "").strip(), flags=re.I))


def _valid_handle(platform: str, handle: str) -> bool:
    raw = str(handle or "").strip()
    handle = raw.lstrip("@")
    if not handle or handle.lower() in {"unknown", "n/a", "na", "none", "null", "-", *GENERIC_BAD_HANDLES}:
        return False
    if _is_url(handle):
        return False
    if platform == "instagram":
        return bool(re.match(r"^[A-Za-z0-9._]{1,30}$", handle)) and " " not in handle
    if platform == "tiktok":
        return bool(re.match(r"^[A-Za-z0-9._]{1,40}$", handle)) and " " not in handle
    if platform == "youtube":
        return bool(re.match(r"^@?[A-Za-z0-9._-]{2,80}$", raw)) and " " not in raw
    return False


def _target_for(row: dict[str, Any]) -> tuple[str, str]:
    platform = _platform(row.get("platform") or "")
    handle = _text(row.get("handle"))
    profile_url = _text(row.get("profile_url"))
    lower_url = profile_url.lower()
    if platform not in SUPPORTED_PLATFORMS:
        return "non_target_platform", ""
    if _is_url(profile_url) and platform in lower_url and _profile_url_is_account(platform, profile_url):
        return "direct_url", profile_url
    if _valid_handle(platform, handle):
        return "direct_handle", handle
    if platform == "youtube" and (handle or profile_url):
        return "query_candidate", handle or profile_url
    return "no_target", ""


def _profile_url_is_account(platform: str, value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    path = parsed.path.strip("/")
    if not path:
        return False
    parts = [part for part in path.split("/") if part]
    if not parts:
        return False
    first = parts[0].strip()
    first_lower = first.lower().lstrip("@")
    if first_lower in GENERIC_BAD_HANDLES:
        return False
    if platform == "instagram":
        return bool(re.match(r"^[A-Za-z0-9._]{1,30}$", first)) and first_lower not in {"reel", "p", "tv", "stories"}
    if platform == "tiktok":
        return first.startswith("@") and _valid_handle(platform, first)
    if platform == "youtube":
        if first.startswith("@"):
            return _valid_handle(platform, first)
        if first_lower == "channel" and len(parts) >= 2 and parts[1].startswith("UC"):
            return True
        if first_lower in {"c", "user"} and len(parts) >= 2:
            return _valid_handle(platform, parts[1])
        return False
    return False


def _missing_score(row: dict[str, Any]) -> int:
    fields = ("avatar_url", "followers", "bio", "posts_count", "last_video_at", "avg_views", "engagement_rate")
    score = 0
    for field in fields:
        value = row.get(field)
        if value is None or str(value).strip() == "":
            score += 1
    return score


def _load_candidates(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              k.id, k.platform, k.handle, k.display_name, k.profile_url, k.avatar_url,
              k.bio, k.followers, k.following, k.posts_count, k.avg_views, k.avg_likes,
              k.avg_comments, k.avg_shares, k.engagement_rate, k.last_video_at,
              k.sync_status, k.raw_platform_data, k.viltrox_fit_score, k.viltrox_fit_reason,
              s.status AS recall_status
            FROM vkpi_kol_pool k
            JOIN vkpi_kol_profile_recall_status s ON s.kol_pool_id = k.id
            WHERE lower(coalesce(k.platform, '')) IN ('youtube', 'instagram', 'tiktok')
              AND s.status IN ('recallable', 'pending_data', 'empty')
            ORDER BY k.id
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        kind, target = _target_for(row)
        row["target_kind"] = kind
        row["target"] = target
        row["missing_score"] = _missing_score(row)
    return rows


def select_pilot_targets(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    direct = [row for row in rows if row.get("target_kind") in {"direct_url", "direct_handle"}]
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in direct:
        by_key[(_platform(row.get("platform") or ""), str(row.get("recall_status") or ""))].append(row)
    for items in by_key.values():
        items.sort(key=lambda row: (0 if row.get("target_kind") == "direct_url" else 1, -int(row.get("missing_score") or 0), int(row.get("id") or 0)))

    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    for platform, quotas in PILOT_QUOTAS.items():
        platform_selected = 0
        for status, quota in quotas:
            for row in by_key.get((platform, status), []):
                if platform_selected >= 10 or quota <= 0:
                    break
                row_id = int(row["id"])
                if row_id in selected_ids:
                    continue
                selected.append(row)
                selected_ids.add(row_id)
                platform_selected += 1
                quota -= 1
        if platform_selected < 10:
            fallback = [row for row in direct if _platform(row.get("platform") or "") == platform and int(row["id"]) not in selected_ids]
            fallback.sort(
                key=lambda row: (
                    0 if row.get("target_kind") == "direct_url" else 1,
                    -int(row.get("missing_score") or 0),
                    str(row.get("recall_status") or ""),
                    int(row.get("id") or 0),
                )
            )
            for row in fallback:
                if platform_selected >= 10:
                    break
                selected.append(row)
                selected_ids.add(int(row["id"]))
                platform_selected += 1
    return selected[: max(1, int(limit or 30))]


def _run_items_from_page(page: Any) -> list[dict[str, Any]]:
    items = getattr(page, "items", None)
    if items is None and isinstance(page, dict):
        items = page.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


class ApifyRunTracker:
    def __init__(self) -> None:
        self.token = os.getenv("APIFY_TOKEN", "").strip()
        self.client = None
        if self.token:
            try:
                from apify_client import ApifyClient

                self.client = ApifyClient(self.token)
            except Exception:
                self.client = None

    @staticmethod
    def actor_path(actor_id: str) -> str:
        return str(actor_id or "").strip().replace("/", "~")

    def snapshot(self, actor_id: str) -> set[str]:
        if self.client is None or not actor_id:
            return set()
        try:
            page = self.client.actor(self.actor_path(actor_id)).runs().list(limit=8, desc=True)
            return {str(item.get("id") or "") for item in _run_items_from_page(page) if item.get("id")}
        except Exception:
            return set()

    def new_run(self, actor_id: str, before: set[str]) -> dict[str, Any]:
        if self.client is None or not actor_id:
            return {}
        try:
            page = self.client.actor(self.actor_path(actor_id)).runs().list(limit=8, desc=True)
            for item in _run_items_from_page(page):
                run_id = str(item.get("id") or "")
                if run_id and run_id not in before:
                    return item
        except Exception:
            return {}
        return {}


def _actor_id_for(platform: str, crawler: Any) -> str:
    if platform == "youtube":
        return str(getattr(crawler, "apify_actor_id", "") or "")
    if platform in {"instagram", "tiktok"}:
        return str(getattr(crawler, "actor_id", "") or "")
    return ""


def _run_cost_usd(run: dict[str, Any]) -> float | None:
    for key in ("usageTotalUsd", "usageUsdTotal", "chargedAmount"):
        value = run.get(key)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    usage_usd = run.get("usageUsd")
    if isinstance(usage_usd, dict):
        try:
            return float(sum(float(value or 0) for value in usage_usd.values()))
        except (TypeError, ValueError):
            return None
    return None


def _estimated_cost(actor_id: str, result_count: int) -> float:
    price = APIFY_PRICE_PER_1000.get(actor_id) or APIFY_PRICE_PER_1000.get(actor_id.replace("~", "/")) or 0.0
    return float(price) * max(0, int(result_count or 0)) / 1000.0


def _crawler_for(platform: str) -> Any:
    if platform == "youtube":
        return YouTubeCrawler(run_timeout_seconds=240)
    if platform == "instagram":
        return InstagramCrawler(run_timeout_seconds=180)
    if platform == "tiktok":
        return TikTokCrawler(run_timeout_seconds=240)
    raise ValueError(f"unsupported platform: {platform}")


def _crawl(row: dict[str, Any], *, max_posts: int, tracker: ApifyRunTracker) -> dict[str, Any]:
    platform = _platform(row.get("platform") or "")
    crawler = _crawler_for(platform)
    actor_id = _actor_id_for(platform, crawler)
    before = tracker.snapshot(actor_id) if actor_id else set()
    started = time.monotonic()
    target = str(row.get("target") or "")
    profile_payload: dict[str, Any]
    videos_payload: dict[str, Any] = {}
    videos_items: list[dict[str, Any]] = []
    if platform == "youtube":
        profile_payload = crawler.crawl_channel_profile(target, channel_id="", max_posts=max_posts)
        profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else []
        profile = profile_items[0] if isinstance(profile_items, list) and profile_items and isinstance(profile_items[0], dict) else {}
        channel_id = str(profile.get("id") or "")
        if channel_id and hasattr(crawler, "crawl_channel_videos"):
            videos_payload = crawler.crawl_channel_videos(channel_id, max_results=max_posts)
            videos = videos_payload.get("items") if isinstance(videos_payload, dict) else []
            videos_items = [video for video in videos if isinstance(video, dict)] if isinstance(videos, list) else []
        fallback_videos = profile_payload.get("videos") if isinstance(profile_payload, dict) else None
        if not videos_items and isinstance(fallback_videos, list):
            videos_items = [video for video in fallback_videos if isinstance(video, dict)]
    else:
        profile_payload = crawler.crawl_channel_profile(target, channel_id="", max_posts=max_posts)
        payload_items = _content_items_from_payload(profile_payload) if isinstance(profile_payload, dict) else []
        profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else []
        if payload_items and _looks_like_content_item(payload_items[0]):
            videos_items = payload_items
        elif isinstance(profile_items, list):
            videos_items = [item for item in profile_items if isinstance(item, dict) and _looks_like_content_item(item)]
    elapsed_ms = int((time.monotonic() - started) * 1000)
    run = tracker.new_run(actor_id, before) if actor_id else {}
    provider_source = ""
    if isinstance(profile_payload, dict):
        provider_source = str(profile_payload.get("provider_source") or "")
    if not provider_source and isinstance(videos_payload, dict):
        provider_source = str(videos_payload.get("provider_source") or "")
    status = str(
        (profile_payload or {}).get("sync_status")
        or (profile_payload or {}).get("provider_status")
        or (videos_payload or {}).get("sync_status")
        or (videos_payload or {}).get("provider_status")
        or "unknown"
    )
    actor_was_used = bool(run) or platform in {"instagram", "tiktok"} or provider_source == "apify"
    result_count = 0
    if isinstance(profile_payload, dict):
        result_count += len([item for item in profile_payload.get("items") or [] if isinstance(item, dict)])
    result_count += len(videos_items)
    actual_cost = _run_cost_usd(run)
    estimated_cost = _estimated_cost(actor_id, max(1, result_count)) if actor_was_used else 0.0
    return {
        "profile_payload": profile_payload if isinstance(profile_payload, dict) else {},
        "videos_payload": videos_payload if isinstance(videos_payload, dict) else {},
        "videos_items": videos_items,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "actor_id": actor_id if actor_was_used else "",
        "apify_run": _summarize_run(run),
        "actual_cost_usd": actual_cost,
        "estimated_cost_usd": estimated_cost,
    }


def _summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    if not run:
        return {}
    return {
        "id": run.get("id"),
        "status": run.get("status"),
        "startedAt": str(run.get("startedAt") or ""),
        "finishedAt": str(run.get("finishedAt") or ""),
        "usageTotalUsd": run.get("usageTotalUsd"),
        "usageUsd": run.get("usageUsd"),
        "defaultDatasetId": run.get("defaultDatasetId"),
    }


def _parse_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).date().isoformat()
        except Exception:
            return ""
    text = str(value or "").strip()
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date().isoformat()
    except Exception:
        pass
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def _latest_video_date(items: list[dict[str, Any]]) -> str:
    dates: list[str] = []
    for item in items:
        author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
        candidates = [
            item.get("publish_date"),
            item.get("published_at"),
            item.get("publishedAt"),
            item.get("uploadDate"),
            item.get("date"),
            item.get("timestamp"),
            item.get("createTimeISO"),
            item.get("createTime"),
            item.get("takenAtIso"),
            author.get("createTime"),
        ]
        for candidate in candidates:
            parsed = _parse_date(candidate)
            if parsed:
                dates.append(parsed)
                break
    return max(dates) if dates else ""


def _field_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "avatar_url",
        "bio",
        "followers",
        "following",
        "posts_count",
        "avg_views",
        "avg_likes",
        "avg_comments",
        "avg_shares",
        "engagement_rate",
        "profile_url",
        "last_video_at",
        "sync_status",
    )
    return {field: row.get(field) for field in fields}


def _shape_update(row: dict[str, Any], crawled: dict[str, Any], *, max_posts: int) -> dict[str, Any]:
    platform = _platform(row.get("platform") or "")
    profile_payload = crawled["profile_payload"]
    videos_payload = crawled["videos_payload"]
    videos_items = crawled["videos_items"]
    raw_data = {
        "source": f"{platform}_profile_basics_backfill",
        "profile": profile_payload,
        "videos": videos_items,
        "kpi_status": crawled.get("status") or "unknown",
        "source_ref": f"kol_pool:{row['id']}",
        "profile_backfill": {
            "method": "profile_basics_pilot_v1",
            "max_posts": int(max_posts),
            "target_kind": row.get("target_kind"),
            "target": row.get("target"),
            "actor_id": crawled.get("actor_id") or "",
            "apify_run": crawled.get("apify_run") or {},
            "elapsed_ms": crawled.get("elapsed_ms"),
            "previous_raw_source": _previous_raw_source(row.get("raw_platform_data")),
        },
    }
    if platform == "youtube":
        source = str(profile_payload.get("provider_source") or videos_payload.get("provider_source") or "").strip()
        raw_data["source"] = "youtube_apify_profile_basics_backfill" if source == "apify" else "youtube_api_profile_basics_backfill"
        raw_data["youtube_provider_source"] = source or "youtube_api"
    kpis = calculate_kpis(raw_data)
    profile = _profile_item(raw_data)
    stats = _profile_stats(profile)
    followers = _int_or_none(_first_present(kpis.get("followers"), row.get("followers")))
    posts_count = _int_or_none(_first_present(kpis.get("posts"), row.get("posts_count")))
    avg_views = _int_or_none(_first_present(kpis.get("avg_views"), row.get("avg_views")))
    sample_count = len(videos_items) or int(posts_count or 0)
    avg_likes = _average_from_total(kpis.get("likes"), sample_count, row.get("avg_likes"))
    avg_comments = _average_from_total(kpis.get("comments"), sample_count, row.get("avg_comments"))
    avg_shares = _average_from_total(kpis.get("shares"), sample_count, row.get("avg_shares"))
    engagement_ratio = _float_or_none(kpis.get("engagement_rate"))
    engagement_rate = (engagement_ratio * 100.0) if engagement_ratio is not None and engagement_ratio <= 1 else engagement_ratio
    last_video_at = _latest_video_date(videos_items) or (str(row.get("last_video_at") or "")[:10] if row.get("last_video_at") else "")
    return {
        "profile_url": _profile_url(platform, profile, str(row.get("handle") or ""), str(row.get("profile_url") or "")),
        "display_name": _display_name(profile, str(row.get("display_name") or row.get("handle") or "")),
        "avatar_url": _thumb_url(profile) or str(row.get("avatar_url") or ""),
        "bio": _bio(profile) or str(row.get("bio") or ""),
        "followers": followers,
        "following": _int_or_none(_first_present(stats.get("following"), stats.get("followingCount"), stats.get("followsCount"), row.get("following"))),
        "posts_count": posts_count,
        "avg_views": avg_views,
        "avg_likes": avg_likes,
        "avg_comments": avg_comments,
        "avg_shares": avg_shares,
        "engagement_rate": engagement_rate if engagement_rate is not None else row.get("engagement_rate"),
        "sync_status": _normalize_sync_status(raw_data.get("kpi_status")),
        "raw_platform_data": _json(raw_data),
        "last_video_at": last_video_at,
        "schema_sample": _schema_sample(platform, profile_payload, videos_items, profile),
    }


def _previous_raw_source(raw: Any) -> str:
    if raw in (None, ""):
        return ""
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return ""
    return str(parsed.get("source") or "")


def _schema_sample(platform: str, profile_payload: dict[str, Any], videos_items: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    profile_items = profile_payload.get("items") if isinstance(profile_payload.get("items"), list) else []
    first_item = profile_items[0] if profile_items and isinstance(profile_items[0], dict) else profile
    first_video = videos_items[0] if videos_items else {}
    return {
        "platform": platform,
        "profile_payload_keys": sorted([str(key) for key in profile_payload.keys()])[:40],
        "profile_item_keys": sorted([str(key) for key in first_item.keys()])[:60] if isinstance(first_item, dict) else [],
        "content_item_keys": sorted([str(key) for key in first_video.keys()])[:60] if isinstance(first_video, dict) else [],
    }


def _nonempty_delta(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for field, after_value in after.items():
        before_has = before.get(field) not in (None, "")
        after_has = after_value not in (None, "")
        if not before_has and after_has:
            changed.append(field)
    return changed


def _write_update(conn: psycopg.Connection[Any], row_id: int, update: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE vkpi_kol_pool
            SET profile_url = COALESCE(NULLIF(%s, ''), profile_url),
                display_name = COALESCE(NULLIF(%s, ''), display_name),
                avatar_url = COALESCE(NULLIF(%s, ''), avatar_url),
                bio = COALESCE(NULLIF(%s, ''), bio),
                followers = COALESCE(%s, followers),
                following = COALESCE(%s, following),
                posts_count = COALESCE(%s, posts_count),
                avg_views = COALESCE(%s, avg_views),
                avg_likes = COALESCE(%s, avg_likes),
                avg_comments = COALESCE(%s, avg_comments),
                avg_shares = COALESCE(%s, avg_shares),
                engagement_rate = COALESCE(%s, engagement_rate),
                sync_status = COALESCE(NULLIF(%s, ''), sync_status),
                raw_platform_data = %s,
                last_seen_at = NOW(),
                last_video_at = COALESCE(%s::date, last_video_at),
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                update.get("profile_url") or "",
                update.get("display_name") or "",
                update.get("avatar_url") or "",
                update.get("bio") or "",
                update.get("followers"),
                update.get("following"),
                update.get("posts_count"),
                update.get("avg_views"),
                update.get("avg_likes"),
                update.get("avg_comments"),
                update.get("avg_shares"),
                update.get("engagement_rate"),
                update.get("sync_status") or "",
                update.get("raw_platform_data") or "{}",
                update.get("last_video_at") or None,
                int(row_id),
            ),
        )


def _reload_score_fields(conn: psycopg.Connection[Any], ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, viltrox_fit_score, viltrox_fit_reason
            FROM vkpi_kol_pool
            WHERE id = ANY(%s)
            """,
            (ids,),
        )
        return {int(row["id"]): dict(row) for row in cur.fetchall()}


def _print_targets(targets: list[dict[str, Any]]) -> None:
    print("pilot_targets:")
    for idx, row in enumerate(targets, 1):
        print(
            f"{idx:02d}\t{row['id']}\t{_platform(row.get('platform') or '')}\t"
            f"{row.get('recall_status')}\t{row.get('target_kind')}\t"
            f"{row.get('handle') or row.get('display_name') or '-'}\t{row.get('target')}"
        )
    print("target_platforms:", dict(Counter(_platform(row.get("platform") or "") for row in targets)))
    print("target_statuses:", dict(Counter(str(row.get("recall_status") or "") for row in targets)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill profile basics for KOL Pool without touching fit scores.")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--max-posts", type=int, default=3)
    parser.add_argument("--plan-only", action="store_true", help="Only print selected pilot targets; no provider calls.")
    parser.add_argument("--execute", action="store_true", help="Call YouTube API / Apify crawlers.")
    parser.add_argument("--commit", action="store_true", help="Write profile basics to vkpi_kol_pool.")
    args = parser.parse_args()

    _load_env()
    conn = psycopg.connect(_database_url())
    conn.autocommit = False
    try:
        rows = _load_candidates(conn)
        targets = select_pilot_targets(rows, args.limit)
        _print_targets(targets)
        if args.plan_only:
            return
        if not args.execute:
            raise SystemExit("Refusing provider calls without --execute. Use --plan-only to inspect targets.")
        if args.commit:
            print("write_mode: commit_profile_basics_only")
        else:
            print("write_mode: dry_run_no_db_writes")
        tracker = ApifyRunTracker()
        before_scores = {int(row["id"]): {"viltrox_fit_score": row.get("viltrox_fit_score"), "viltrox_fit_reason": row.get("viltrox_fit_reason")} for row in targets}
        results: list[dict[str, Any]] = []
        schema_samples: dict[str, dict[str, Any]] = {}
        for idx, row in enumerate(targets, 1):
            platform = _platform(row.get("platform") or "")
            label = f"{idx:02d}/{len(targets)} id={row['id']} {platform} {row.get('handle') or row.get('display_name')}"
            print(f"crawl_start\t{label}", flush=True)
            before_fields = _field_snapshot(row)
            try:
                crawled = _crawl(row, max_posts=max(1, min(12, int(args.max_posts or 3))), tracker=tracker)
                update = _shape_update(row, crawled, max_posts=max(1, min(12, int(args.max_posts or 3))))
                after_fields = {key: update.get(key) for key in before_fields}
                filled = _nonempty_delta(before_fields, after_fields)
                provider_ok = str(crawled.get("status") or "").lower() in {"ok", "synced"}
                if args.commit:
                    _write_update(conn, int(row["id"]), update)
                    conn.commit()
                if platform not in schema_samples and provider_ok:
                    schema_samples[platform] = update.get("schema_sample") or {}
                result = {
                    "id": int(row["id"]),
                    "platform": platform,
                    "handle": row.get("handle") or "",
                    "display_name": row.get("display_name") or "",
                    "status": row.get("recall_status") or "",
                    "provider_status": crawled.get("status") or "",
                    "success": provider_ok,
                    "elapsed_ms": crawled.get("elapsed_ms"),
                    "actor_id": crawled.get("actor_id") or "",
                    "apify_run_id": (crawled.get("apify_run") or {}).get("id") or "",
                    "actual_cost_usd": crawled.get("actual_cost_usd"),
                    "estimated_cost_usd": crawled.get("estimated_cost_usd"),
                    "filled_fields": filled,
                    "avatar_present": bool(update.get("avatar_url")),
                    "followers_present": update.get("followers") is not None,
                    "bio_present": bool(str(update.get("bio") or "").strip()),
                    "last_video_at": update.get("last_video_at") or "",
                    "error": "",
                }
                print(
                    "crawl_done\t"
                    f"id={result['id']} status={result['provider_status']} success={result['success']} "
                    f"filled={','.join(filled) or '-'} actual_cost={result['actual_cost_usd']} "
                    f"est_cost={result['estimated_cost_usd']:.6f}",
                    flush=True,
                )
            except Exception as exc:
                conn.rollback()
                result = {
                    "id": int(row["id"]),
                    "platform": platform,
                    "handle": row.get("handle") or "",
                    "display_name": row.get("display_name") or "",
                    "status": row.get("recall_status") or "",
                    "provider_status": "exception",
                    "success": False,
                    "elapsed_ms": None,
                    "actor_id": "",
                    "apify_run_id": "",
                    "actual_cost_usd": None,
                    "estimated_cost_usd": 0.0,
                    "filled_fields": [],
                    "avatar_present": bool(row.get("avatar_url")),
                    "followers_present": row.get("followers") is not None,
                    "bio_present": bool(str(row.get("bio") or "").strip()),
                    "last_video_at": str(row.get("last_video_at") or ""),
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                }
                print(f"crawl_error\tid={row['id']} error={result['error']}", flush=True)
            results.append(result)
        ids = [int(row["id"]) for row in targets]
        after_scores = _reload_score_fields(conn, ids)
        score_changed: list[int] = []
        for row_id, before in before_scores.items():
            after = after_scores.get(row_id, {})
            if before.get("viltrox_fit_score") != after.get("viltrox_fit_score") or before.get("viltrox_fit_reason") != after.get("viltrox_fit_reason"):
                score_changed.append(row_id)
        summary = _summary(results, targets, score_changed, schema_samples, committed=bool(args.commit))
        print("SUMMARY_JSON_START")
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        print("SUMMARY_JSON_END")
    finally:
        conn.close()


def _summary(
    results: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    score_changed: list[int],
    schema_samples: dict[str, dict[str, Any]],
    *,
    committed: bool,
) -> dict[str, Any]:
    success = [item for item in results if item.get("success")]
    failures = [item for item in results if not item.get("success")]
    actual_cost = sum(float(item.get("actual_cost_usd") or 0.0) for item in results)
    estimated_cost = sum(float(item.get("estimated_cost_usd") or 0.0) for item in results)
    cost_basis = "actual_apify_run_usage_when_available_plus_estimated_for_missing_usage"
    if any(item.get("actual_cost_usd") is not None for item in results):
        cost_basis = "actual_apify_run_usage_available_for_some_runs"
    filled_counter: Counter[str] = Counter()
    for item in results:
        filled_counter.update([str(field) for field in item.get("filled_fields") or []])
    d_results = [item for item in results if item.get("status") == "empty"]
    return {
        "committed": committed,
        "target_count": len(targets),
        "target_platforms": dict(Counter(_platform(row.get("platform") or "") for row in targets)),
        "target_statuses": dict(Counter(str(row.get("recall_status") or "") for row in targets)),
        "success_count": len(success),
        "failure_count": len(failures),
        "failures": [
            {
                "id": item.get("id"),
                "platform": item.get("platform"),
                "handle": item.get("handle"),
                "provider_status": item.get("provider_status"),
                "error": item.get("error"),
            }
            for item in failures
        ],
        "cost": {
            "actual_cost_usd_known_sum": round(actual_cost, 6),
            "estimated_cost_usd_sum": round(estimated_cost, 6),
            "basis": cost_basis,
            "extrapolated_983_usd_from_estimated": round((estimated_cost / max(1, len(results))) * 983, 4),
        },
        "filled_fields": dict(sorted(filled_counter.items())),
        "post_run_presence": {
            "avatar_present": sum(1 for item in results if item.get("avatar_present")),
            "followers_present": sum(1 for item in results if item.get("followers_present")),
            "bio_present": sum(1 for item in results if item.get("bio_present")),
            "last_video_at_present": sum(1 for item in results if item.get("last_video_at")),
        },
        "empty_status_results": {
            "targeted": len(d_results),
            "success": sum(1 for item in d_results if item.get("success")),
            "with_avatar": sum(1 for item in d_results if item.get("avatar_present")),
            "with_followers": sum(1 for item in d_results if item.get("followers_present")),
            "with_bio": sum(1 for item in d_results if item.get("bio_present")),
        },
        "score_guard": {
            "viltrox_fit_score_changed_ids": score_changed,
            "viltrox_fit_score_untouched": not score_changed,
        },
        "schema_samples": schema_samples,
        "results": results,
    }


if __name__ == "__main__":
    main()
