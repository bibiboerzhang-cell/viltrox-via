"""URL classifier and safe profile-flow handler for URL deep crawl.

The default execute=false path is read-only. execute=true is only wired for
profile URLs and writes through the profile-basics whitelist service; it never
queues workers, calls LLMs, or touches V6 Fit fields.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from app.db.connection import get_conn
from app.domains.industry.snapshot_kpis import calculate_kpis
from app.domains.kol.pool_common import (
    _bio,
    _content_items_from_payload,
    _first_present,
    _int_or_none,
    _json,
    _looks_like_content_item,
    _platform,
    _profile_item,
    _profile_stats,
    _profile_url,
    _table_columns,
    _thumb_url,
)
from app.domains.kol.profile_basics import write_kol_profile_basics
from app.domains.projects.workflow_evidence import _fetch_video_metadata
from app.platform.industry_crawlers.instagram_crawler import InstagramCrawler
from app.platform.industry_crawlers.tiktok_crawler import TikTokCrawler
from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler
from app.services.verification.viltrox_official import (
    detect_platform_from_profile_url,
    extract_handle_from_profile_url,
)
from app.utils.handles import extract_handle_from_url

SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok"}
PROFILE_GENERIC_SEGMENTS = {
    "",
    "about",
    "accounts",
    "channel",
    "direct",
    "explore",
    "feed",
    "p",
    "reel",
    "shorts",
    "stories",
    "tagged",
    "tv",
    "user",
    "watch",
}
RAW_CHANNEL_KEYS = {
    "channel_id",
    "channelid",
    "channelId",
    "youtube_channel_id",
    "youtubeChannelId",
    "channel_url",
    "channelUrl",
    "channel",
    "external_id",
    "externalId",
}
RAW_HANDLE_KEYS = {
    "handle",
    "username",
    "user_name",
    "userName",
    "author_handle",
    "authorHandle",
    "platform_user_id",
    "platformUserId",
    "screen_name",
    "screenName",
}
RAW_URL_KEYS = {
    "url",
    "profile_url",
    "profileUrl",
    "channel_url",
    "channelUrl",
    "account_url",
    "accountUrl",
    "web_url",
    "webUrl",
}


@dataclass(frozen=True)
class ClassifiedUrl:
    original_url: str
    normalized_url: str
    url_type: str
    platform: str
    handle: str
    channel_id: str
    video_id: str
    confidence: str


def dry_run_url_deep_crawl(body: dict[str, Any]) -> dict[str, Any]:
    """Classify a user URL; optionally execute safe profile basics flow."""
    execute = bool(body.get("execute", False))

    url = str(body.get("url") or "").strip()
    if not url:
        raise ValueError("url is required")

    classified = classify_url(url)
    matches = _match_pool(classified) if classified.platform in SUPPORTED_PLATFORMS else []
    video_flow: dict[str, Any] | None = None
    if classified.url_type == "video" and classified.platform in SUPPORTED_PLATFORMS:
        video_flow, matches = _video_flow_plan(classified, matches)

    matched_id = matches[0]["kol_pool_id"] if len(matches) == 1 else None
    profile_flow = _profile_flow_plan(classified, matches, body, execute=execute)
    safety = {
        "crawl_performed": False,
        "provider_calls_performed": bool(video_flow and video_flow.get("provider_calls_performed")),
        "llm_calls_performed": False,
        "worker_touched": False,
        "viltrox_fit_touched": False,
        "business_tables_written": False,
    }

    if execute and classified.url_type == "profile" and profile_flow.get("status") == "ready_to_execute":
        profile_flow = _execute_profile_flow(classified, matches, body)
        safety["crawl_performed"] = bool(profile_flow.get("crawl_performed"))
        safety["business_tables_written"] = bool(profile_flow.get("business_tables_written"))

    return {
        "method": "kol_url_deep_crawl_profile_flow_v1",
        "dry_run": not execute,
        "execute": execute,
        "writes_performed": safety["business_tables_written"],
        "provider_calls_performed": safety["crawl_performed"] or safety["provider_calls_performed"],
        "url": {
            "input": classified.original_url,
            "normalized": classified.normalized_url,
        },
        "url_type": classified.url_type,
        "platform": classified.platform or None,
        "handle": classified.handle or None,
        "channel_id": classified.channel_id or None,
        "video_id": classified.video_id or None,
        "in_pool": len(matches) == 1,
        "matched_kol_pool_id": matched_id,
        "candidates": matches,
        "next_action": _next_action(classified, matches),
        "profile_flow": profile_flow,
        "video_flow": video_flow,
        "video_metadata": video_flow.get("video_metadata") if video_flow else None,
        "creator_identity": video_flow.get("creator_identity") if video_flow else None,
        "safety": safety,
    }


def classify_url(raw_url: str) -> ClassifiedUrl:
    original = str(raw_url or "").strip()
    normalized = _normalize_input_url(original)
    parsed = urlparse(normalized)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.strip("/")
    lowered_path = path.lower()

    platform = (detect_platform_from_profile_url(normalized) or _platform_from_host(host) or "").lower()
    if platform not in SUPPORTED_PLATFORMS:
        return ClassifiedUrl(original, normalized, "unknown", "", "", "", "", "unsupported_platform")

    video_id = _video_id(platform, host, path, parsed.query)
    if video_id:
        handle_hint = _normalise_handle(platform, extract_handle_from_url(normalized))
        channel_id = _channel_id_from_handle(platform, handle_hint)
        return ClassifiedUrl(
            original,
            normalized,
            "video",
            platform,
            "" if channel_id else handle_hint,
            channel_id,
            video_id,
            "video_pattern",
        )

    profile_handle = extract_handle_from_profile_url(normalized, platform) or extract_handle_from_url(normalized)
    profile_handle = _normalise_handle(platform, profile_handle)
    channel_id = _channel_id_from_handle(platform, profile_handle)
    if channel_id:
        profile_handle = ""

    if profile_handle or channel_id:
        return ClassifiedUrl(
            original,
            normalized,
            "profile",
            platform,
            profile_handle,
            channel_id,
            "",
            "profile_pattern",
        )

    if platform == "instagram" and lowered_path.split("/", 1)[0] not in PROFILE_GENERIC_SEGMENTS:
        handle = _normalise_handle(platform, lowered_path.split("/", 1)[0])
        return ClassifiedUrl(original, normalized, "profile", platform, handle, "", "", "profile_fallback")

    return ClassifiedUrl(original, normalized, "unknown", platform, "", "", "", "no_extractable_identity")


def _match_pool(classified: ClassifiedUrl) -> list[dict[str, Any]]:
    rows = _pool_rows()
    ranked: dict[int, tuple[int, dict[str, Any]]] = {}
    canonical_input = _canonical_url(classified.normalized_url)

    for row in rows:
        row_platform = _normalise_platform(row.get("platform"))
        if classified.platform and row_platform != classified.platform:
            continue

        row_dict = dict(row)
        raw_payload = _load_json(row_dict.get("raw_platform_data"))
        source = ""
        priority = 999

        if classified.channel_id and classified.platform == "youtube":
            channel_values = _raw_values(raw_payload, RAW_CHANNEL_KEYS)
            channel_values.extend([row_dict.get("handle"), row_dict.get("profile_url")])
            if _contains_identity(channel_values, classified.channel_id):
                source = "platform_channel_id"
                priority = 1

        if not source and classified.handle:
            row_handle = _normalise_handle(row_platform, row_dict.get("handle"))
            raw_handles = [_normalise_handle(row_platform, item) for item in _raw_values(raw_payload, RAW_HANDLE_KEYS)]
            if row_handle == classified.handle or classified.handle in raw_handles:
                source = "platform_handle"
                priority = 2

        if not source and classified.url_type == "profile" and canonical_input:
            url_values = [row_dict.get("profile_url"), *_raw_values(raw_payload, RAW_URL_KEYS)]
            canonical_values = {_canonical_url(str(item or "")) for item in url_values if item}
            if canonical_input in canonical_values:
                source = "profile_url"
                priority = 3

        if not source and (classified.handle or classified.channel_id):
            needle = classified.channel_id or classified.handle
            raw_values = _all_raw_strings(raw_payload)
            if _contains_identity(raw_values, needle):
                source = "raw_platform_data"
                priority = 4

        if source:
            kol_id = int(row_dict["id"])
            candidate = {
                "kol_pool_id": kol_id,
                "platform": row_platform,
                "handle": row_dict.get("handle") or "",
                "display_name": row_dict.get("display_name") or "",
                "profile_url": row_dict.get("profile_url") or "",
                "match_source": source,
                "match_priority": priority,
            }
            current = ranked.get(kol_id)
            if current is None or priority < current[0]:
                ranked[kol_id] = (priority, candidate)

    return [
        candidate
        for _, candidate in sorted(
            ranked.values(),
            key=lambda item: (
                int(item[0]),
                str(item[1].get("platform") or ""),
                str(item[1].get("handle") or ""),
                int(item[1].get("kol_pool_id") or 0),
            ),
        )
    ][:10]


def _video_flow_plan(
    classified: ClassifiedUrl,
    initial_matches: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    provider_source = ""
    metadata: dict[str, Any] = {}
    creator_identity = _creator_identity_from_classified(classified)
    matches = initial_matches
    error = ""

    try:
        metadata = _fetch_video_metadata(classified.normalized_url)
        provider_source = str(metadata.get("scrape_source") or "").strip()
        metadata_identity = _creator_identity_from_video_metadata(classified, metadata)
        if metadata_identity:
            creator_identity = metadata_identity
        creator_classified = _classified_from_creator_identity(classified, creator_identity)
        if creator_classified:
            matches = _match_pool(creator_classified)
    except Exception as exc:
        error = str(exc)[:500]

    resolved = _has_matchable_creator_identity(creator_identity)
    status = "ready_to_execute" if resolved else "creator_unresolved"
    if error and not metadata:
        status = "metadata_failed"

    return (
        {
            "status": status,
            "operation": "video_creator_resolve",
            "provider_calls_performed": True,
            "provider_source": provider_source or None,
            "creator_resolution_status": "resolved" if resolved else "unresolved",
            "creator_identity": creator_identity or None,
            "video_metadata": _public_video_metadata(metadata) if metadata else None,
            "metadata_error": error or None,
            "would_write": False,
            "would_enqueue_worker": False,
            "business_tables_written": False,
            "llm_calls_performed": False,
            "viltrox_fit_touched": False,
        },
        matches,
    )


def _creator_identity_from_classified(classified: ClassifiedUrl) -> dict[str, Any]:
    if not classified.platform:
        return {}
    handle = _normalise_handle(classified.platform, classified.handle)
    channel_id = _channel_id_from_handle(classified.platform, classified.channel_id or handle)
    if channel_id:
        handle = ""
    if not handle and not channel_id:
        return {}
    return {
        "platform": classified.platform,
        "handle": handle,
        "channel_id": channel_id or "",
        "display_name": "",
        "profile_url": _profile_url_for_creator(classified.platform, handle, channel_id),
        "avatar_url": None,
        "followers": None,
        "bio": None,
        "source": "url_pattern",
    }


def _creator_identity_from_video_metadata(classified: ClassifiedUrl, metadata: dict[str, Any]) -> dict[str, Any]:
    platform = _normalise_platform(metadata.get("platform")) or classified.platform
    if platform not in SUPPORTED_PLATFORMS:
        return {}

    raw_channel_id = _metadata_text(metadata.get("channel_id"))
    channel_id = raw_channel_id if platform == "youtube" and raw_channel_id.startswith("UC") else ""
    display_name = _metadata_text(metadata.get("channel_name"))
    handle = ""

    if platform in {"instagram", "tiktok"}:
        handle = _normalise_handle(platform, display_name or classified.handle)
    elif platform == "youtube" and not channel_id:
        handle = _normalise_handle(platform, classified.handle)

    profile_url = _profile_url_for_creator(platform, handle, channel_id)
    return {
        "platform": platform,
        "handle": handle,
        "channel_id": channel_id or raw_channel_id,
        "display_name": display_name,
        "profile_url": profile_url,
        "avatar_url": None,
        "followers": None,
        "bio": None,
        "source": _metadata_text(metadata.get("scrape_source")) or "video_metadata",
    }


def _classified_from_creator_identity(
    original: ClassifiedUrl,
    identity: dict[str, Any],
) -> ClassifiedUrl | None:
    platform = _normalise_platform(identity.get("platform"))
    if platform not in SUPPORTED_PLATFORMS:
        return None
    channel_id = _metadata_text(identity.get("channel_id"))
    handle = _normalise_handle(platform, identity.get("handle"))
    if platform == "youtube" and channel_id.startswith("UC"):
        handle = ""
    else:
        channel_id = ""
    if not handle and not channel_id:
        return None
    profile_url = _metadata_text(identity.get("profile_url")) or _profile_url_for_creator(platform, handle, channel_id)
    return ClassifiedUrl(
        original.original_url,
        profile_url or original.normalized_url,
        "profile",
        platform,
        handle,
        channel_id,
        "",
        "video_metadata_creator",
    )


def _has_matchable_creator_identity(identity: dict[str, Any]) -> bool:
    platform = _normalise_platform(identity.get("platform"))
    if platform == "youtube":
        return bool(_metadata_text(identity.get("channel_id")).startswith("UC") or _metadata_text(identity.get("handle")))
    if platform in {"instagram", "tiktok"}:
        return bool(_metadata_text(identity.get("handle")))
    return False


def _profile_url_for_creator(platform: str, handle: str, channel_id: str) -> str:
    if platform == "youtube":
        if channel_id:
            return f"https://www.youtube.com/channel/{channel_id}"
        if handle:
            return f"https://www.youtube.com/@{handle.lstrip('@')}"
    if platform == "instagram" and handle:
        return f"https://www.instagram.com/{handle.lstrip('@')}/"
    if platform == "tiktok" and handle:
        return f"https://www.tiktok.com/@{handle.lstrip('@')}"
    return ""


def _public_video_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "platform",
        "content_url",
        "title",
        "description",
        "view_count",
        "like_count",
        "comment_count",
        "share_count",
        "publish_date",
        "posted_at",
        "duration_seconds",
        "thumbnail_url",
        "channel_id",
        "channel_name",
        "scrape_source",
        "scrape_status",
        "scrape_error",
        "apify_run_id",
    ):
        value = metadata.get(key)
        if value in (None, ""):
            continue
        if key == "description":
            value = str(value)[:1000]
        result[key] = value
    return result


def _metadata_text(value: Any) -> str:
    return str(value or "").strip()


def _profile_flow_plan(
    classified: ClassifiedUrl,
    matches: list[dict[str, Any]],
    body: dict[str, Any],
    *,
    execute: bool,
) -> dict[str, Any]:
    if classified.url_type == "video":
        return {
            "status": "dry_run_only" if not execute else "execute_not_connected",
            "message": "video URL creator resolver is read-only in this knife; execute/write/enqueue will be handled by later knives.",
            "crawl_performed": False,
            "business_tables_written": False,
        }
    if classified.url_type != "profile":
        return {
            "status": "unsupported",
            "message": "profile flow only supports recognized YouTube/Instagram/TikTok profile URLs.",
            "crawl_performed": False,
            "business_tables_written": False,
        }
    if len(matches) > 1:
        return {
            "status": "needs_human_choice",
            "message": "multiple KOL Pool candidates matched; choose one before executing.",
            "candidate_count": len(matches),
            "crawl_performed": False,
            "business_tables_written": False,
        }

    target = _profile_target(classified)
    kol_pool_id = int(matches[0]["kol_pool_id"]) if len(matches) == 1 else None
    profile_data = _identity_profile_data(classified)
    writer_plan = write_kol_profile_basics(kol_pool_id, profile_data, dry_run=True)
    return {
        "status": "ready_to_execute" if execute else "dry_run_ready",
        "operation": "update" if kol_pool_id else "insert",
        "kol_pool_id": kol_pool_id,
        "target": target,
        "max_posts": _max_posts(body),
        "would_crawl": {
            "platform": classified.platform,
            "target": target,
            "crawler": f"{classified.platform}_crawler",
            "uses_decodo": False,
            "uses_gemini": False,
            "uses_worker": False,
        },
        "safe_writer_dry_run": {
            "operation": writer_plan.get("operation"),
            "fields_to_write": writer_plan.get("fields_to_write"),
            "ignored_fields": writer_plan.get("ignored_fields"),
            "missing_columns": writer_plan.get("missing_columns"),
            "viltrox_fit_score_changed_ids": writer_plan.get("viltrox_fit_score_changed_ids"),
            "viltrox_fit_score_untouched": writer_plan.get("viltrox_fit_score_untouched"),
        },
        "crawl_performed": False,
        "business_tables_written": False,
    }


def _execute_profile_flow(
    classified: ClassifiedUrl,
    matches: list[dict[str, Any]],
    body: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    target = _profile_target(classified)
    max_posts = _max_posts(body)
    kol_pool_id = int(matches[0]["kol_pool_id"]) if len(matches) == 1 else None
    operation = "update" if kol_pool_id else "insert"
    conn = get_conn()

    crawl = _crawl_profile_basics(classified, target=target, max_posts=max_posts)
    if str(crawl.get("status") or "").lower() not in {"ok", "synced"}:
        run_id = _record_deep_crawl_run(
            conn,
            kol_pool_id=kol_pool_id,
            source_url=classified.normalized_url,
            url_type="profile",
            mode=str(body.get("mode") or "profile_only"),
            status="failed",
            dry_run=False,
            summary={
                "reason": "profile_crawl_not_ready",
                "crawl_status": crawl.get("status"),
                "elapsed_ms": crawl.get("elapsed_ms"),
                "provider_source": crawl.get("provider_source"),
            },
        )
        return {
            "status": "crawl_failed",
            "operation": operation,
            "kol_pool_id": kol_pool_id,
            "target": target,
            "crawl_status": crawl.get("status"),
            "provider_source": crawl.get("provider_source"),
            "run_id": run_id,
            "crawl_performed": True,
            "business_tables_written": bool(run_id),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    profile_data = _profile_data_from_crawl(
        classified,
        crawl,
        existing_match=matches[0] if matches else {},
        max_posts=max_posts,
    )
    write_result = write_kol_profile_basics(kol_pool_id, profile_data, dry_run=False, conn=conn)
    written_kol_pool_id = int(write_result.get("kol_pool_id") or kol_pool_id or 0) or None
    run_id = _record_deep_crawl_run(
        conn,
        kol_pool_id=written_kol_pool_id,
        source_url=classified.normalized_url,
        url_type="profile",
        mode=str(body.get("mode") or "profile_only"),
        status="ready",
        dry_run=False,
        summary={
            "operation": operation,
            "target": target,
            "crawl_status": crawl.get("status"),
            "provider_source": crawl.get("provider_source"),
            "elapsed_ms": crawl.get("elapsed_ms"),
            "fields_written": write_result.get("fields_written"),
            "viltrox_fit_score_changed_ids": write_result.get("viltrox_fit_score_changed_ids"),
        },
    )
    return {
        "status": "ready",
        "operation": operation,
        "kol_pool_id": written_kol_pool_id,
        "target": target,
        "max_posts": max_posts,
        "profile_data": _public_profile_data(profile_data),
        "write_result": {
            "fields_written": write_result.get("fields_written"),
            "ignored_fields": write_result.get("ignored_fields"),
            "missing_columns": write_result.get("missing_columns"),
            "viltrox_fit_score_changed_ids": write_result.get("viltrox_fit_score_changed_ids"),
            "viltrox_fit_score_untouched": write_result.get("viltrox_fit_score_untouched"),
        },
        "run_id": run_id,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "crawl_performed": True,
        "business_tables_written": True,
        "provider_source": crawl.get("provider_source"),
        "crawl_status": crawl.get("status"),
        "viltrox_fit_score_changed_ids": write_result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": bool(write_result.get("viltrox_fit_score_untouched")),
    }


def _crawl_profile_basics(classified: ClassifiedUrl, *, target: str, max_posts: int) -> dict[str, Any]:
    crawler = _crawler_for(classified.platform)
    started = time.monotonic()
    profile_payload: dict[str, Any] = {}
    videos_payload: dict[str, Any] = {}
    videos_items: list[dict[str, Any]] = []

    if classified.platform == "youtube":
        profile_payload = crawler.crawl_channel_profile(target, channel_id="", max_posts=max_posts)
        profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else []
        profile = profile_items[0] if isinstance(profile_items, list) and profile_items and isinstance(profile_items[0], dict) else {}
        channel_id = str(profile.get("id") or classified.channel_id or "")
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

    provider_source = str((profile_payload or {}).get("provider_source") or (videos_payload or {}).get("provider_source") or "")
    status = str(
        (profile_payload or {}).get("sync_status")
        or (profile_payload or {}).get("provider_status")
        or (videos_payload or {}).get("sync_status")
        or (videos_payload or {}).get("provider_status")
        or "unknown"
    )
    return {
        "profile_payload": profile_payload if isinstance(profile_payload, dict) else {},
        "videos_payload": videos_payload if isinstance(videos_payload, dict) else {},
        "videos_items": videos_items,
        "status": status,
        "provider_source": provider_source,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _profile_data_from_crawl(
    classified: ClassifiedUrl,
    crawl: dict[str, Any],
    *,
    existing_match: dict[str, Any],
    max_posts: int,
) -> dict[str, Any]:
    profile_payload = crawl.get("profile_payload") if isinstance(crawl.get("profile_payload"), dict) else {}
    videos_payload = crawl.get("videos_payload") if isinstance(crawl.get("videos_payload"), dict) else {}
    videos_items = crawl.get("videos_items") if isinstance(crawl.get("videos_items"), list) else []
    handle = classified.handle or classified.channel_id or str(existing_match.get("handle") or "")
    raw_data = {
        "source": f"{classified.platform}_url_deep_crawl_profile",
        "profile": profile_payload,
        "videos": videos_items,
        "kpi_status": crawl.get("status") or "unknown",
        "source_ref": classified.normalized_url,
        "profile_backfill": {
            "method": "url_deep_crawl_profile_v1",
            "max_posts": int(max_posts),
            "target": _profile_target(classified),
            "provider_source": crawl.get("provider_source") or "",
            "elapsed_ms": crawl.get("elapsed_ms"),
        },
    }
    if classified.platform == "youtube":
        source = str(profile_payload.get("provider_source") or videos_payload.get("provider_source") or "").strip()
        raw_data["source"] = "youtube_url_deep_crawl_profile_apify" if source == "apify" else "youtube_url_deep_crawl_profile_api"
        raw_data["youtube_provider_source"] = source or "youtube_api"

    kpis = calculate_kpis(raw_data)
    profile = _profile_item(raw_data)
    stats = _profile_stats(profile)
    return {
        "platform": classified.platform,
        "handle": handle,
        "profile_url": _profile_url(classified.platform, profile, handle, classified.normalized_url),
        "avatar_url": _thumb_url(profile),
        "bio": _bio(profile),
        "followers": _int_or_none(_first_present(kpis.get("followers"), stats.get("followers"), stats.get("followersCount"))),
        "posts_count": _int_or_none(_first_present(kpis.get("posts"), stats.get("posts"), stats.get("postsCount"))),
        "last_video_at": _latest_video_date([item for item in videos_items if isinstance(item, dict)]),
        "raw_platform_data": _json(raw_data),
    }


def _record_deep_crawl_run(
    conn: Any,
    *,
    kol_pool_id: int | None,
    source_url: str,
    url_type: str,
    mode: str,
    status: str,
    dry_run: bool,
    summary: dict[str, Any],
) -> int | None:
    columns = _table_columns(conn, "vkpi_kol_url_deep_crawl_runs")
    if "id" not in columns:
        raise RuntimeError("vkpi_kol_url_deep_crawl_runs table is missing; apply migration 102")
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_url_deep_crawl_runs
          (kol_pool_id, source_url, url_type, mode, status, dry_run, result_summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?::jsonb)
        RETURNING id
        """,
        (
            int(kol_pool_id) if kol_pool_id else None,
            source_url,
            url_type,
            mode if mode in {"auto", "profile_only", "video_deep", "dry_run"} else "profile_only",
            status,
            bool(dry_run),
            json.dumps(summary or {}, ensure_ascii=False, default=str),
        ),
    ).fetchone()
    try:
        conn.commit()
    except Exception:
        pass
    return int(row["id"]) if row and row["id"] is not None else None


def _profile_target(classified: ClassifiedUrl) -> str:
    if classified.url_type == "profile" and classified.normalized_url:
        return classified.normalized_url
    return classified.channel_id or classified.handle


def _identity_profile_data(classified: ClassifiedUrl) -> dict[str, Any]:
    return {
        "platform": classified.platform,
        "handle": classified.handle or classified.channel_id,
        "profile_url": classified.normalized_url if classified.url_type == "profile" else "",
        "raw_platform_data": {
            "source": "url_deep_crawl_profile_identity_dry_run",
            "profile_backfill": {
                "method": "url_deep_crawl_profile_v1",
                "source_url": classified.normalized_url,
            },
        },
    }


def _public_profile_data(profile_data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: profile_data.get(key)
        for key in ("platform", "handle", "profile_url", "avatar_url", "followers", "bio", "posts_count", "last_video_at")
        if profile_data.get(key) not in (None, "")
    }


def _crawler_for(platform: str) -> Any:
    if platform == "youtube":
        return YouTubeCrawler(run_timeout_seconds=240)
    if platform == "instagram":
        return InstagramCrawler(run_timeout_seconds=180)
    if platform == "tiktok":
        return TikTokCrawler(run_timeout_seconds=240)
    raise ValueError(f"unsupported platform: {platform}")


def _max_posts(body: dict[str, Any]) -> int:
    try:
        return max(1, min(12, int(body.get("max_posts") or 3)))
    except (TypeError, ValueError):
        return 3


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
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
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


def _pool_rows() -> list[dict[str, Any]]:
    conn = get_conn()
    columns = _table_columns(conn, "vkpi_kol_pool")
    required = ["id", "platform", "handle", "display_name", "profile_url", "raw_platform_data"]
    selected = [column for column in required if column in columns]
    if "id" not in selected:
        return []
    rows = conn.execute(f"SELECT {', '.join(selected)} FROM vkpi_kol_pool").fetchall()
    return [dict(row) for row in rows]


def _next_action(classified: ClassifiedUrl, matches: list[dict[str, Any]]) -> dict[str, Any]:
    if classified.url_type == "unknown":
        return {
            "code": "unsupported_or_unresolved_url",
            "label": "无法识别 URL 类型",
            "description": "未执行抓取。请确认 URL 是 YouTube/Instagram/TikTok profile 或 video URL。",
        }
    if len(matches) > 1:
        return {
            "code": "choose_existing_candidate",
            "label": "发现多个候选",
            "description": "需要人工选择目标 KOL，dry-run 不会自动合并。",
        }
    if classified.url_type == "profile":
        if matches:
            return {
                "code": "profile_found_in_pool",
                "label": "已在库",
                "description": "下一步可打开现有档案，或在确认后执行安全基础补档。",
            }
        return {
            "code": "profile_not_in_pool",
            "label": "不在库",
            "description": "下一步可在确认后新建最小档案并执行安全基础补档。",
        }
    if classified.url_type == "video":
        if matches:
            return {
                "code": "video_creator_found_in_pool",
                "label": "视频创作者已在库",
                "description": "下一步可在确认后做单帖预览或排入 final_v1 深度分析。",
            }
        return {
            "code": "video_creator_unresolved_or_not_in_pool",
            "label": "视频已识别，创作者未确认在库",
            "description": "下一步可在确认后先做单帖预览，解析创作者后再决定是否建档。",
        }
    return {
        "code": "dry_run_only",
        "label": "仅识别",
        "description": "未执行抓取或写库。",
    }


def _normalize_input_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value.lstrip("/")
    return value


def _platform_from_host(host: str) -> str:
    if "youtube.com" in host or host == "youtu.be":
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return ""


def _video_id(platform: str, host: str, path: str, query: str) -> str:
    parts = [part for part in path.split("/") if part]
    lowered = [part.lower() for part in parts]
    if platform == "youtube":
        if host == "youtu.be" and parts:
            return parts[0]
        if lowered[:1] == ["watch"]:
            values = parse_qs(query).get("v") or []
            return str(values[0] or "").strip()
        if len(parts) >= 2 and lowered[0] in {"shorts", "embed", "live"}:
            return parts[1]
    if platform == "instagram":
        if len(parts) >= 2 and lowered[0] in {"p", "reel", "tv"}:
            return parts[1]
    if platform == "tiktok":
        for index, part in enumerate(lowered):
            if part == "video" and index + 1 < len(parts):
                return parts[index + 1]
    return ""


def _normalise_platform(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"yt", "youtube", "youtube.com"}:
        return "youtube"
    if text in {"ig", "instagram", "instagram.com"}:
        return "instagram"
    if text in {"tt", "tiktok", "tiktok.com"}:
        return "tiktok"
    return text


def _normalise_handle(platform: str, value: Any) -> str:
    text = str(value or "").strip().strip("/")
    if not text:
        return ""
    if text.startswith("@"):
        text = text[1:]
    lowered = text.lower()
    if platform in {"instagram", "tiktok"}:
        return lowered
    if platform == "youtube":
        return text if text.startswith("UC") else lowered
    return lowered


def _channel_id_from_handle(platform: str, handle: str) -> str:
    if platform == "youtube" and str(handle or "").startswith("UC"):
        return str(handle)
    return ""


def _canonical_url(value: str) -> str:
    text = _normalize_input_url(str(value or "").strip())
    if not text:
        return ""
    parsed = urlparse(text)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return urlunparse(("https", host, path, "", "", ""))


def _load_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return {}
    try:
        return json.loads(str(value))
    except Exception:
        return {}


def _raw_values(payload: Any, keys: set[str]) -> list[str]:
    values: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, (str, int, float)):
                values.append(str(value))
            if isinstance(value, (dict, list)):
                values.extend(_raw_values(value, keys))
    elif isinstance(payload, list):
        for item in payload:
            values.extend(_raw_values(item, keys))
    return values


def _all_raw_strings(payload: Any) -> list[str]:
    values: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, (str, int, float)):
                values.append(str(value))
            elif isinstance(value, (dict, list)):
                values.extend(_all_raw_strings(value))
    elif isinstance(payload, list):
        for item in payload:
            values.extend(_all_raw_strings(item))
    return values


def _contains_identity(values: list[Any], identity: str) -> bool:
    needle = _normalise_identity(identity)
    if not needle:
        return False
    for value in values:
        candidate = _normalise_identity(value)
        if candidate == needle:
            return True
        if needle.startswith("uc") and needle in candidate:
            return True
    return False


def _normalise_identity(value: Any) -> str:
    text = str(value or "").strip().lower().strip("/")
    if not text:
        return ""
    if text.startswith("@"):
        text = text[1:]
    if "://" in text or "." in text:
        try:
            parsed = urlparse(_normalize_input_url(text))
            path_parts = [part for part in parsed.path.split("/") if part]
            if path_parts:
                if path_parts[0].lower() == "channel" and len(path_parts) > 1:
                    return path_parts[1].lower()
                if path_parts[0].startswith("@"):
                    return path_parts[0][1:].lower()
                return path_parts[-1].lower()
        except ValueError:
            pass
    return text
