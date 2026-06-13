"""URL classifier and safe profile/video-flow handler for URL deep crawl.

The default execute=false path is read-only. execute=true profile writes go
through the profile-basics whitelist service; video writes only create/reuse
evidence, enqueue final_v1 work, and record a crawl run. Neither path touches
V6 Fit fields.
"""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger("viltrox.domains.kol.url_deep_crawl")

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
from app.domains.kol.video_analysis_enqueue import _enqueue_final_v1_video_analysis
from app.domains.kol.video_evidence import ensure_video_evidence_from_url
from app.domains.kol.video_evidence_sources import profile_crawl_source
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
    if classified.url_type == "video" and not matches and video_flow and _video_creator_resolved(video_flow):
        profile_flow = {
            "status": "ready_to_execute" if execute else "dry_run_ready",
            "operation": "new_creator_video_analysis",
            "kol_pool_id": None,
            "message": "creator resolved but not in KOL Pool; execute will crawl profile basics, create a new KOL, create/reuse evidence, and enqueue final_v1 analysis.",
            "crawl_performed": False,
            "business_tables_written": False,
        }
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
        safety["worker_touched"] = bool(profile_flow.get("worker_touched"))
        safety["viltrox_fit_touched"] = bool(profile_flow.get("viltrox_fit_score_changed_ids"))
    elif execute and classified.url_type == "video" and len(matches) == 1:
        video_flow = _execute_existing_creator_video_flow(classified, matches, video_flow or {}, body)
        safety["business_tables_written"] = bool(video_flow.get("business_tables_written"))
        safety["worker_touched"] = bool(video_flow.get("worker_touched"))
        safety["viltrox_fit_touched"] = bool(video_flow.get("viltrox_fit_score_changed_ids"))
        safety["provider_calls_performed"] = safety["provider_calls_performed"] or bool(video_flow.get("provider_calls_performed"))
    elif execute and classified.url_type == "video" and video_flow and _video_creator_resolved(video_flow):
        video_flow = _execute_new_creator_video_flow(classified, video_flow, body)
        if isinstance(video_flow.get("profile_flow"), dict):
            profile_flow = video_flow["profile_flow"]
        safety["crawl_performed"] = bool(video_flow.get("crawl_performed"))
        safety["business_tables_written"] = bool(video_flow.get("business_tables_written"))
        safety["worker_touched"] = bool(video_flow.get("worker_touched"))
        safety["viltrox_fit_touched"] = bool(video_flow.get("viltrox_fit_score_changed_ids"))
        safety["provider_calls_performed"] = safety["provider_calls_performed"] or bool(video_flow.get("provider_calls_performed"))
    elif execute and classified.url_type == "video" and video_flow:
        video_flow = {
            **video_flow,
            "status": "execute_not_connected",
            "message": "video URL execute requires a resolved creator before creating evidence or enqueueing analysis.",
            "business_tables_written": False,
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }
    result_kol_pool_id = matched_id
    if execute and video_flow and video_flow.get("kol_pool_id"):
        result_kol_pool_id = int(video_flow["kol_pool_id"])

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
        "in_pool": len(matches) == 1 or bool(execute and result_kol_pool_id),
        "matched_kol_pool_id": result_kol_pool_id,
        "candidates": matches,
        "next_action": _next_action(classified, matches, video_flow=video_flow),
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
        if len(matches) > 1:
            return {
                "status": "needs_human_choice",
                "message": "multiple KOL Pool candidates matched; choose one before executing video analysis.",
                "candidate_count": len(matches),
                "crawl_performed": False,
                "business_tables_written": False,
            }
        if len(matches) == 1:
            return {
                "status": "ready_to_execute" if execute else "dry_run_ready",
                "operation": "existing_creator_video_analysis",
                "kol_pool_id": int(matches[0]["kol_pool_id"]),
                "message": "existing creator matched; execute will create/reuse evidence and enqueue final_v1 analysis for this video only.",
                "crawl_performed": False,
                "business_tables_written": False,
            }
        return {
            "status": "creator_not_in_pool" if not execute else "execute_not_connected",
            "message": "video URL creator is not in KOL Pool yet; new-creator build + evidence + enqueue will be handled by the next knife.",
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
    representative_enabled = _profile_should_enqueue_representative_videos(body)
    history_enabled = _profile_should_materialize_history_videos(body)
    incremental_state = _profile_incremental_state(kol_pool_id)
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
            "uses_worker": representative_enabled and classified.platform != "tiktok",
        },
        "representative_video_analysis": {
            "enabled": representative_enabled,
            "status": "will_try_after_profile_crawl" if representative_enabled else "disabled_profile_only",
            "limit": _profile_representative_video_limit(body) if representative_enabled else 0,
            "note": "TikTok profile video analysis is skipped until the TikTok video resolver is fixed." if representative_enabled and classified.platform == "tiktok" else "",
            "incremental": incremental_state,
        },
        "history_video_evidence": {
            "enabled": history_enabled,
            "status": "will_materialize_after_profile_crawl" if history_enabled else "disabled",
            "limit": _profile_history_video_limit(body) if history_enabled else 0,
            "enqueue_final_v1": False,
            "note": "History videos are materialized as evidence only; batch/on-demand analysis can enqueue final_v1 later.",
            "incremental": incremental_state,
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
    incremental_state = _profile_incremental_state(kol_pool_id)
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
    representative_video_analysis = _execute_profile_representative_video_analysis(
        conn,
        classified=classified,
        kol_pool_id=written_kol_pool_id,
        crawl=crawl,
        body=body,
        incremental_state=incremental_state,
    )
    history_video_evidence = _execute_profile_history_video_evidence(
        conn,
        classified=classified,
        kol_pool_id=written_kol_pool_id,
        crawl=crawl,
        body=body,
        incremental_state=incremental_state,
    )
    worker_touched = bool(representative_video_analysis.get("worker_touched"))
    account_dossier_extract_job = None
    if written_kol_pool_id and not worker_touched:
        account_dossier_extract_job = _enqueue_account_dossier_extract_followup(
            conn,
            kol_pool_id=written_kol_pool_id,
            source="kol_url_profile_flow",
            trigger=str(representative_video_analysis.get("status") or "profile_ready_no_video_job"),
            source_url=classified.normalized_url,
            query_text=f"profile account dossier - {target}",
        )
    changed_ids = sorted(
        set(
            _fit_changed_ids(write_result)
            + _fit_changed_ids(representative_video_analysis)
            + _fit_changed_ids(history_video_evidence)
            + _fit_changed_ids(account_dossier_extract_job or {})
        )
    )
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
            "representative_video_analysis": representative_video_analysis,
            "history_video_evidence": history_video_evidence,
            "account_dossier_extract_job": account_dossier_extract_job,
            "incremental_state": incremental_state,
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
        "representative_video_analysis": representative_video_analysis,
        "history_video_evidence": history_video_evidence,
        "account_dossier_extract_job": account_dossier_extract_job,
        "run_id": run_id,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "crawl_performed": True,
        "business_tables_written": True,
        "worker_touched": worker_touched or bool(account_dossier_extract_job and account_dossier_extract_job.get("status") == "queued"),
        "provider_source": crawl.get("provider_source"),
        "crawl_status": crawl.get("status"),
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
    }


def _cache_video_flow_url(
    classified: ClassifiedUrl,
    metadata: dict[str, Any] | None,
    evidence_id: int | None,
) -> tuple[str | None, bool]:
    """为 video URL 结果区把 IG/TikTok 视频就地喂 R2,返回 (cached_video_url, provider_called)。

    YouTube 走前端 embed 不缓存;失败/skip 不毁主链(媒体缓存属增强)。
    模式照搬 url_deep_crawl 媒体回灌段(cache_video_for_item)。
    """
    platform_key = str(getattr(classified, "platform", "") or "").lower()
    if not evidence_id or not platform_key or platform_key == "youtube":
        return None, False
    content_url = ""
    if isinstance(metadata, dict):
        content_url = str(metadata.get("content_url") or "").strip()
    content_url = content_url or classified.normalized_url
    if not content_url:
        return None, False
    try:
        from app.domains.media.cache import cache_video_for_item

        vid = cache_video_for_item(platform_key, str(evidence_id), content_url)
        cached_url = str(vid.get("cached_url") or "").strip() or None
        logger.info(
            "video_flow r2 warm evidence_id=%s platform=%s status=%s",
            evidence_id,
            platform_key,
            vid.get("status"),
        )
        return cached_url, True
    except Exception:
        logger.warning("video_flow r2 warm failed evidence_id=%s platform=%s", evidence_id, platform_key)
        return None, True


def _execute_existing_creator_video_flow(
    classified: ClassifiedUrl,
    matches: list[dict[str, Any]],
    video_flow: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    kol_pool_id = int(matches[0]["kol_pool_id"])
    metadata = video_flow.get("video_metadata")
    if not isinstance(metadata, dict):
        metadata = None

    conn = get_conn()
    evidence_result: dict[str, Any] = {}
    enqueue_result: dict[str, Any] = {}
    status = "failed"
    error = ""
    evidence_id: int | None = None
    changed_ids: list[int] = []
    cached_video_url: str | None = None
    video_provider_called = False

    try:
        evidence_result = ensure_video_evidence_from_url(
            kol_pool_id,
            classified.normalized_url,
            metadata,
            dry_run=False,
            conn=conn,
        )
        changed_ids.extend(_fit_changed_ids(evidence_result))
        if not evidence_result.get("ok"):
            status = str(evidence_result.get("status") or "evidence_failed")
        else:
            evidence_id = int(evidence_result.get("evidence_id") or 0) or None
            if not evidence_id:
                status = "evidence_missing_id"
            else:
                enqueue_result = _enqueue_final_v1_video_analysis(
                    conn,
                    kol_pool_id=kol_pool_id,
                    evidence_id=evidence_id,
                    source="kol_url_deep_crawl",
                    batch="url_existing_creator",
                    commit=True,
                )
                changed_ids.extend(_fit_changed_ids(enqueue_result))
                status = str(enqueue_result.get("status") or "enqueue_unknown")
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        error = str(exc)[:500]
        status = "failed"

    cached_video_url, video_provider_called = _cache_video_flow_url(classified, metadata, evidence_id)

    account_dossier_extract_job = None
    if status == "already_analyzed":
        account_dossier_extract_job = _enqueue_account_dossier_extract_followup(
            conn,
            kol_pool_id=kol_pool_id,
            source="kol_url_video_flow",
            trigger="video_already_analyzed",
            source_url=classified.normalized_url,
            query_text=f"video account dossier - kol_pool #{kol_pool_id}",
        )
        changed_ids.extend(_fit_changed_ids(account_dossier_extract_job or {}))

    run_status = "ready" if status in {"queued", "already_queued", "already_analyzed"} else "failed"
    run_id = _record_deep_crawl_run(
        conn,
        kol_pool_id=kol_pool_id,
        source_url=classified.normalized_url,
        url_type="video",
        mode=_video_execute_mode(body),
        status=run_status,
        dry_run=False,
        summary={
            "operation": "existing_creator_video_analysis",
            "status": status,
            "error": error or None,
            "creator_identity": video_flow.get("creator_identity"),
            "video_metadata": video_flow.get("video_metadata"),
            "evidence_result": _compact_video_evidence_result(evidence_result),
            "enqueue_result": _compact_enqueue_result(enqueue_result),
            "account_dossier_extract_job": account_dossier_extract_job,
            "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    )

    worker_touched = status == "queued"
    business_tables_written = bool(run_id) or bool(evidence_result.get("status") in {"created", "reused"}) or worker_touched
    return {
        **video_flow,
        "status": status,
        "operation": "existing_creator_video_analysis",
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "evidence_result": _compact_video_evidence_result(evidence_result),
        "enqueue_result": _compact_enqueue_result(enqueue_result),
        "account_dossier_extract_job": account_dossier_extract_job,
        "run_id": run_id,
        "run_status": run_status,
        "error": error or None,
        "business_tables_written": business_tables_written,
        "worker_touched": worker_touched or bool(account_dossier_extract_job and account_dossier_extract_job.get("status") == "queued"),
        "write_db": business_tables_written,
        "writes": ["vkpi_kol_video_evidence", "apify_jobs", "vkpi_kol_url_deep_crawl_runs"],
        "cached_video_url": cached_video_url,
        "provider_calls_performed": video_provider_called,
        "llm_calls_performed": False,
        "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
        "viltrox_fit_score_untouched": not changed_ids,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _execute_new_creator_video_flow(
    classified: ClassifiedUrl,
    video_flow: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    max_posts = _max_posts(body)
    profile_classified = _profile_classified_from_video_flow(classified, video_flow)
    conn = get_conn()
    crawl: dict[str, Any] = {}
    profile_data: dict[str, Any] = {}
    write_result: dict[str, Any] = {}
    evidence_result: dict[str, Any] = {}
    enqueue_result: dict[str, Any] = {}
    representative_video_analysis: dict[str, Any] = {}
    history_video_evidence: dict[str, Any] = {}
    kol_pool_id: int | None = None
    evidence_id: int | None = None
    status = "failed"
    error = ""
    changed_ids: list[int] = []
    cached_video_url: str | None = None
    video_provider_called = False

    if not profile_classified:
        run_id = _record_deep_crawl_run(
            conn,
            kol_pool_id=None,
            source_url=classified.normalized_url,
            url_type="video",
            mode=_video_execute_mode(body),
            status="failed",
            dry_run=False,
            summary={
                "operation": "new_creator_video_analysis",
                "status": "creator_unresolved",
                "reason": "resolved video creator lacks a usable profile identity",
                "creator_identity": video_flow.get("creator_identity"),
                "video_metadata": video_flow.get("video_metadata"),
                "viltrox_fit_score_changed_ids": [],
            },
        )
        return {
            **video_flow,
            "status": "creator_unresolved",
            "operation": "new_creator_video_analysis",
            "message": "video creator could not be converted into a profile identity; refused to create an anonymous KOL.",
            "run_id": run_id,
            "business_tables_written": bool(run_id),
            "worker_touched": False,
            "llm_calls_performed": False,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    try:
        crawl = _crawl_profile_basics(profile_classified, target=_profile_target(profile_classified), max_posts=max_posts)
        if str(crawl.get("status") or "").lower() not in {"ok", "synced"}:
            status = "profile_crawl_failed"
            error = "profile_crawl_not_ready"
        else:
            profile_data = _profile_data_for_new_video_creator(
                profile_classified,
                crawl,
                video_flow,
                max_posts=max_posts,
            )
            write_result = write_kol_profile_basics(None, profile_data, dry_run=False, conn=conn)
            changed_ids.extend(_fit_changed_ids(write_result))
            kol_pool_id = int(write_result.get("kol_pool_id") or 0) or None
            if not kol_pool_id:
                status = "kol_create_missing_id"
            else:
                metadata = video_flow.get("video_metadata")
                if not isinstance(metadata, dict):
                    metadata = None
                evidence_result = ensure_video_evidence_from_url(
                    kol_pool_id,
                    classified.normalized_url,
                    metadata,
                    dry_run=False,
                    conn=conn,
                )
                changed_ids.extend(_fit_changed_ids(evidence_result))
                if not evidence_result.get("ok"):
                    status = str(evidence_result.get("status") or "evidence_failed")
                else:
                    evidence_id = int(evidence_result.get("evidence_id") or 0) or None
                    if not evidence_id:
                        status = "evidence_missing_id"
                    else:
                        enqueue_result = _enqueue_final_v1_video_analysis(
                            conn,
                            kol_pool_id=kol_pool_id,
                            evidence_id=evidence_id,
                            source="kol_url_deep_crawl",
                            batch="url_new_creator",
                            commit=True,
                        )
                        changed_ids.extend(_fit_changed_ids(enqueue_result))
                        status = str(enqueue_result.get("status") or "enqueue_unknown")
                if kol_pool_id:
                    onboarding_body = {
                        **body,
                        "mode": "account_deep",
                        "representative_video_limit": body.get("representative_video_limit") or 3,
                        "history_video_limit": body.get("history_video_limit") or max_posts,
                        "materialize_history_videos": True,
                        "exclude_video_urls": [classified.normalized_url],
                    }
                    incremental_state = _profile_incremental_state(None)
                    representative_video_analysis = _execute_profile_representative_video_analysis(
                        conn,
                        classified=profile_classified,
                        kol_pool_id=kol_pool_id,
                        crawl=crawl,
                        body=onboarding_body,
                        incremental_state=incremental_state,
                    )
                    history_video_evidence = _execute_profile_history_video_evidence(
                        conn,
                        classified=profile_classified,
                        kol_pool_id=kol_pool_id,
                        crawl=crawl,
                        body=onboarding_body,
                        incremental_state=incremental_state,
                    )
                    changed_ids.extend(_fit_changed_ids(representative_video_analysis))
                    changed_ids.extend(_fit_changed_ids(history_video_evidence))
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        error = str(exc)[:500]
        status = "failed"

    cached_video_url, video_provider_called = _cache_video_flow_url(
        classified, video_flow.get("video_metadata") if isinstance(video_flow.get("video_metadata"), dict) else None, evidence_id
    )

    account_dossier_extract_job = None
    if status == "already_analyzed" and kol_pool_id:
        account_dossier_extract_job = _enqueue_account_dossier_extract_followup(
            conn,
            kol_pool_id=kol_pool_id,
            source="kol_url_video_new_creator_flow",
            trigger="video_already_analyzed",
            source_url=classified.normalized_url,
            query_text=f"new creator account dossier - kol_pool #{kol_pool_id}",
        )
        changed_ids.extend(_fit_changed_ids(account_dossier_extract_job or {}))

    representative_worker_touched = bool(representative_video_analysis.get("worker_touched"))
    run_status = "ready" if status in {"queued", "already_queued", "already_analyzed"} or representative_worker_touched else "failed"
    run_id = _record_deep_crawl_run(
        conn,
        kol_pool_id=kol_pool_id,
        source_url=classified.normalized_url,
        url_type="video",
        mode=_video_execute_mode(body),
        status=run_status,
        dry_run=False,
        summary={
            "operation": "new_creator_video_analysis",
            "status": status,
            "error": error or None,
            "creator_identity": video_flow.get("creator_identity"),
            "profile_url": profile_classified.normalized_url,
            "profile_crawl_status": crawl.get("status"),
            "profile_provider_source": crawl.get("provider_source"),
            "profile_write_result": _compact_profile_write_result(write_result),
            "profile_data": _public_profile_data(profile_data),
            "video_metadata": video_flow.get("video_metadata"),
            "evidence_result": _compact_video_evidence_result(evidence_result),
            "enqueue_result": _compact_enqueue_result(enqueue_result),
            "representative_video_analysis": representative_video_analysis,
            "history_video_evidence": history_video_evidence,
            "account_dossier_extract_job": account_dossier_extract_job,
            "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    )

    worker_touched = status == "queued"
    history_written = bool((history_video_evidence.get("materialized") or 0) or (history_video_evidence.get("reused") or 0))
    business_tables_written = (
        bool(kol_pool_id)
        or bool(evidence_result.get("status") in {"created", "reused"})
        or worker_touched
        or representative_worker_touched
        or history_written
        or bool(run_id)
    )
    return {
        **video_flow,
        "status": status,
        "operation": "new_creator_video_analysis",
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "profile_flow": {
            "status": "ready" if kol_pool_id else status,
            "operation": "insert",
            "kol_pool_id": kol_pool_id,
            "target": _profile_target(profile_classified),
            "profile_data": _public_profile_data(profile_data),
            "write_result": _compact_profile_write_result(write_result),
            "crawl_status": crawl.get("status"),
            "provider_source": crawl.get("provider_source"),
            "viltrox_fit_score_changed_ids": _fit_changed_ids(write_result),
            "viltrox_fit_score_untouched": not _fit_changed_ids(write_result),
        },
        "evidence_result": _compact_video_evidence_result(evidence_result),
        "enqueue_result": _compact_enqueue_result(enqueue_result),
        "representative_video_analysis": representative_video_analysis,
        "history_video_evidence": history_video_evidence,
        "account_dossier_extract_job": account_dossier_extract_job,
        "run_id": run_id,
        "run_status": run_status,
        "error": error or None,
        "crawl_performed": bool(crawl),
        "business_tables_written": business_tables_written,
        "worker_touched": worker_touched
        or representative_worker_touched
        or bool(account_dossier_extract_job and account_dossier_extract_job.get("status") == "queued"),
        "write_db": business_tables_written,
        "writes": ["vkpi_kol_pool", "vkpi_kol_video_evidence", "apify_jobs", "vkpi_kol_url_deep_crawl_runs"],
        "cached_video_url": cached_video_url,
        "provider_calls_performed": video_provider_called,
        "llm_calls_performed": False,
        "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
        "viltrox_fit_score_untouched": not changed_ids,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _execute_profile_representative_video_analysis(
    conn: Any,
    *,
    classified: ClassifiedUrl,
    kol_pool_id: int | None,
    crawl: dict[str, Any],
    body: dict[str, Any],
    incremental_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    incremental_state = incremental_state if isinstance(incremental_state, dict) else {}
    if not _profile_should_enqueue_representative_videos(body):
        return {
            "enabled": False,
            "status": "disabled_profile_only",
            "items": [],
            "queued": 0,
            "skipped": 0,
            "errors": 0,
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }
    if not kol_pool_id:
        return {
            "enabled": True,
            "status": "missing_kol_pool_id",
            "items": [],
            "queued": 0,
            "skipped": 0,
            "errors": 1,
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }
    if classified.platform == "tiktok":
        return {
            "enabled": True,
            "status": "skipped_tiktok_video_resolver_known_issue",
            "items": [],
            "queued": 0,
            "skipped": 1,
            "errors": 0,
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }

    limit = _profile_representative_video_limit(body)
    all_videos = _profile_representative_video_metadata(
        classified,
        crawl,
        limit=max(limit, _max_posts(body)),
        exclude_video_urls=_profile_exclude_video_urls(body),
    )
    videos, skipped_by_incremental = _filter_incremental_profile_videos(all_videos, incremental_state, limit=limit)
    if not videos:
        return {
            "enabled": True,
            "status": "no_new_representative_video_after_cutoff" if skipped_by_incremental else "no_representative_video_url",
            "items": [],
            "queued": 0,
            "skipped": skipped_by_incremental,
            "errors": 0,
            "candidate_count": len(all_videos),
            "incremental": incremental_state,
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
        }

    items: list[dict[str, Any]] = []
    queued = 0
    skipped = 0
    errors = 0
    changed_ids: list[int] = []
    for metadata in videos:
        evidence_result: dict[str, Any] = {}
        enqueue_result: dict[str, Any] = {}
        item_status = "failed"
        error = ""
        try:
            evidence_result = ensure_video_evidence_from_url(
                int(kol_pool_id),
                str(metadata.get("content_url") or ""),
                metadata,
                dry_run=False,
                conn=conn,
            )
            changed_ids.extend(_fit_changed_ids(evidence_result))
            if not evidence_result.get("ok"):
                item_status = str(evidence_result.get("status") or "evidence_failed")
                skipped += 1
            else:
                evidence_id = int(evidence_result.get("evidence_id") or 0)
                enqueue_result = _enqueue_final_v1_video_analysis(
                    conn,
                    kol_pool_id=int(kol_pool_id),
                    evidence_id=evidence_id,
                    source="kol_url_deep_crawl",
                    batch="url_profile_representative",
                    commit=True,
                )
                changed_ids.extend(_fit_changed_ids(enqueue_result))
                item_status = str(enqueue_result.get("status") or "enqueue_unknown")
                if item_status == "queued":
                    queued += 1
                else:
                    skipped += 1
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            errors += 1
            error = str(exc)[:500]
            item_status = "error"
        items.append(
            {
                "status": item_status,
                "metadata": _public_video_metadata(metadata),
                "evidence_result": _compact_video_evidence_result(evidence_result),
                "enqueue_result": _compact_enqueue_result(enqueue_result),
                "error": error or None,
            }
        )

    return {
        "enabled": True,
        "status": "completed" if not errors else "completed_with_errors",
        "limit": limit,
        "requested": len(videos),
        "candidate_count": len(all_videos),
        "skipped_by_incremental": skipped_by_incremental,
        "queued": queued,
        "skipped": skipped,
        "errors": errors,
        "items": items,
        "incremental": incremental_state,
        "worker_touched": queued > 0,
        "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
        "viltrox_fit_score_untouched": not changed_ids,
    }


def _execute_profile_history_video_evidence(
    conn: Any,
    *,
    classified: ClassifiedUrl,
    kol_pool_id: int | None,
    crawl: dict[str, Any],
    body: dict[str, Any],
    incremental_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    incremental_state = incremental_state if isinstance(incremental_state, dict) else {}
    if not _profile_should_materialize_history_videos(body):
        return {
            "enabled": False,
            "status": "disabled",
            "items": [],
            "materialized": 0,
            "reused": 0,
            "skipped": 0,
            "errors": 0,
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }
    if not kol_pool_id:
        return {
            "enabled": True,
            "status": "missing_kol_pool_id",
            "items": [],
            "materialized": 0,
            "reused": 0,
            "skipped": 0,
            "errors": 1,
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }

    limit = _profile_history_video_limit(body)
    all_videos = _profile_representative_video_metadata(
        classified,
        crawl,
        limit=max(limit, _max_posts(body)),
        exclude_video_urls=_profile_exclude_video_urls(body),
    )
    videos, skipped_by_incremental = _filter_incremental_profile_videos(all_videos, incremental_state, limit=limit)
    if not videos:
        return {
            "enabled": True,
            "status": "no_new_history_video_after_cutoff" if skipped_by_incremental else "no_history_video_url",
            "items": [],
            "materialized": 0,
            "reused": 0,
            "skipped": skipped_by_incremental,
            "errors": 0,
            "candidate_count": len(all_videos),
            "worker_touched": False,
            "viltrox_fit_score_changed_ids": [],
            "incremental": incremental_state,
        }

    items: list[dict[str, Any]] = []
    materialized = 0
    reused = 0
    skipped = 0
    errors = 0
    changed_ids: list[int] = []
    for metadata in videos:
        evidence_result: dict[str, Any] = {}
        item_status = "failed"
        error = ""
        try:
            evidence_result = ensure_video_evidence_from_url(
                int(kol_pool_id),
                str(metadata.get("content_url") or ""),
                metadata,
                dry_run=False,
                conn=conn,
                method="url_profile_history_video_evidence_v1",
            )
            changed_ids.extend(_fit_changed_ids(evidence_result))
            if not evidence_result.get("ok"):
                item_status = str(evidence_result.get("status") or "evidence_failed")
                skipped += 1
            else:
                item_status = str(evidence_result.get("status") or "evidence_unknown")
                if item_status == "created":
                    materialized += 1
                else:
                    reused += 1
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            errors += 1
            error = str(exc)[:500]
            item_status = "error"
        items.append(
            {
                "status": item_status,
                "metadata": _public_video_metadata(metadata),
                "evidence_result": _compact_video_evidence_result(evidence_result),
                "error": error or None,
            }
        )

    return {
        "enabled": True,
        "status": "completed" if not errors else "completed_with_errors",
        "limit": limit,
        "requested": len(videos),
        "candidate_count": len(all_videos),
        "skipped_by_incremental": skipped_by_incremental,
        "materialized": materialized,
        "reused": reused,
        "skipped": skipped,
        "errors": errors,
        "items": items,
        "incremental": incremental_state,
        "worker_touched": False,
        "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
        "viltrox_fit_score_untouched": not changed_ids,
    }


def _video_execute_mode(body: dict[str, Any]) -> str:
    mode = str(body.get("mode") or "").strip()
    if mode in {"auto", "video_deep"}:
        return mode
    return "video_deep"


def _profile_should_enqueue_representative_videos(body: dict[str, Any]) -> bool:
    mode = str(body.get("mode") or "").strip()
    return mode in {"auto", "profile_with_video", "account_deep"}


def _profile_representative_video_limit(body: dict[str, Any]) -> int:
    try:
        value = int(body.get("representative_video_limit") or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(3, value))


def _profile_should_materialize_history_videos(body: dict[str, Any]) -> bool:
    mode = str(body.get("mode") or "").strip()
    return mode == "account_deep" or bool(
        body.get("full_history")
        or body.get("materialize_history_videos")
        or body.get("history_video_limit")
    )


def _profile_history_video_limit(body: dict[str, Any]) -> int:
    default = _max_posts(body) if _profile_should_materialize_history_videos(body) else 0
    try:
        value = int(body.get("history_video_limit") or default or 0)
    except (TypeError, ValueError):
        value = default
    return max(1, min(120, int(value or 1)))


def _profile_representative_video_metadata(
    classified: ClassifiedUrl,
    crawl: dict[str, Any],
    *,
    limit: int,
    exclude_video_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    items = crawl.get("videos_items") if isinstance(crawl.get("videos_items"), list) else []
    provider_source = str(crawl.get("provider_source") or "").strip()
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    excluded_keys = {
        _profile_video_dedupe_key(classified.platform, url)
        for url in (exclude_video_urls or [])
        if str(url or "").strip()
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = _metadata_from_profile_video_item(classified, item, provider_source=provider_source)
        content_url = str(metadata.get("content_url") or "").strip()
        if not content_url:
            continue
        dedupe_key = _profile_video_dedupe_key(classified.platform, content_url)
        if dedupe_key in excluded_keys:
            continue
        if dedupe_key in seen_urls:
            continue
        seen_urls.add(dedupe_key)
        results.append(metadata)
        if len(results) >= limit:
            break
    return results


def _profile_exclude_video_urls(body: dict[str, Any]) -> list[str]:
    values = body.get("exclude_video_urls")
    if values is None:
        values = body.get("exclude_video_url")
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value or "").strip()]


def _filter_incremental_profile_videos(
    videos: list[dict[str, Any]],
    incremental_state: dict[str, Any],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    cutoff = _metadata_text((incremental_state or {}).get("last_video_at"))
    if not cutoff:
        return videos[:limit], 0
    selected: list[dict[str, Any]] = []
    skipped = 0
    for metadata in videos:
        if _profile_video_is_newer_than_cutoff(metadata, cutoff):
            selected.append(metadata)
            if len(selected) >= limit:
                break
        else:
            skipped += 1
    return selected, skipped


def _profile_video_is_newer_than_cutoff(metadata: dict[str, Any], cutoff: str) -> bool:
    cutoff_date = _parse_date(cutoff)
    if not cutoff_date:
        return True
    posted_at = _parse_date(
        _first_present(
            metadata.get("posted_at"),
            metadata.get("publish_date"),
            metadata.get("published_at"),
            metadata.get("publishedAt"),
        )
    )
    if not posted_at:
        return False
    return posted_at > cutoff_date


def _metadata_from_profile_video_item(
    classified: ClassifiedUrl,
    item: dict[str, Any],
    *,
    provider_source: str,
) -> dict[str, Any]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    content_details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    platform = classified.platform
    video_id = _profile_video_id(platform, item)
    content_url = _profile_video_url(platform, item, video_id=video_id, classified=classified)
    published = _first_present(
        snippet.get("publishedAt"),
        item.get("publish_date"),
        item.get("published_at"),
        item.get("publishedAt"),
        item.get("uploadDate"),
        item.get("date"),
        item.get("timestamp"),
        item.get("createTimeISO"),
        item.get("createTime"),
        item.get("takenAtIso"),
    )
    channel_id = _metadata_text(_first_present(snippet.get("channelId"), item.get("channel_id"), item.get("channelId"), classified.channel_id))
    channel_name = _metadata_text(
        _first_present(
            snippet.get("channelTitle"),
            item.get("channel_name"),
            item.get("channelName"),
            item.get("authorName"),
            item.get("ownerUsername"),
            classified.handle,
        )
    )
    return {
        "platform": platform,
        "content_url": content_url,
        "title": _metadata_text(_first_present(item.get("title"), snippet.get("title"), item.get("caption"), item.get("text"))) or content_url,
        "description": _metadata_text(_first_present(item.get("description"), snippet.get("description"), item.get("caption"), item.get("text"))),
        "view_count": _int_or_none(_first_present(stats.get("viewCount"), item.get("view_count"), item.get("viewCount"), item.get("views"), item.get("playCount"), item.get("videoPlayCount"), item.get("videoViewCount"))),
        "like_count": _int_or_none(_first_present(stats.get("likeCount"), item.get("like_count"), item.get("likeCount"), item.get("likes"), item.get("likesCount"), item.get("diggCount"))),
        "comment_count": _int_or_none(_first_present(stats.get("commentCount"), item.get("comment_count"), item.get("commentCount"), item.get("comments"), item.get("commentsCount"), item.get("commentCount"))),
        "share_count": _int_or_none(_first_present(item.get("share_count"), item.get("shareCount"), item.get("shares"), item.get("sharesCount"))),
        "publish_date": published,
        "posted_at": _parse_date(published),
        "duration_seconds": _duration_seconds(_first_present(content_details.get("duration"), item.get("duration_seconds"), item.get("durationSeconds"), item.get("duration"))),
        "thumbnail_url": _profile_video_thumbnail(item, snippet),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "scrape_source": _metadata_text(item.get("provider_source")) or provider_source or profile_crawl_source(platform),
        "scrape_status": "success",
    }


def _profile_video_id(platform: str, item: dict[str, Any]) -> str:
    raw_id = _metadata_text(_first_present(item.get("id"), item.get("video_id"), item.get("videoId"), item.get("shortCode"), item.get("shortcode"), item.get("code")))
    if platform == "youtube":
        if isinstance(item.get("id"), dict):
            raw_id = _metadata_text((item.get("id") or {}).get("videoId"))
        if raw_id:
            return raw_id
        video_url = _profile_video_url("youtube", item, video_id="", classified=None)
        parsed = urlparse(video_url)
        return _video_id("youtube", parsed.netloc, parsed.path, parsed.query)
    return raw_id


def _profile_video_url(
    platform: str,
    item: dict[str, Any],
    *,
    video_id: str,
    classified: ClassifiedUrl | None,
) -> str:
    for key in ("content_url", "url", "videoUrl", "video_url", "web_url", "webUrl", "permalink", "link"):
        value = _metadata_text(item.get(key))
        if value.startswith("http"):
            return value
    if platform == "youtube" and video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    if platform == "instagram" and video_id:
        return f"https://www.instagram.com/p/{video_id.strip('/')}/"
    if platform == "tiktok" and video_id and classified and classified.handle:
        return f"https://www.tiktok.com/@{classified.handle.lstrip('@')}/video/{video_id}"
    return ""


def _profile_video_thumbnail(item: dict[str, Any], snippet: dict[str, Any]) -> str:
    for key in ("thumbnail_url", "thumbnailUrl", "thumbnail", "displayUrl", "imageUrl", "coverUrl", "cover"):
        value = _metadata_text(item.get(key))
        if value.startswith("http"):
            return value
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        value = thumbnails.get(key)
        if isinstance(value, dict):
            url = _metadata_text(value.get("url"))
            if url.startswith("http"):
                return url
    return ""


def _duration_seconds(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number if number >= 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text)
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds
    return None


def _profile_video_dedupe_key(platform: str, content_url: str) -> str:
    parsed = urlparse(str(content_url or ""))
    video_id = _video_id(platform, parsed.netloc, parsed.path, parsed.query)
    if video_id:
        return f"{platform}:{video_id}"
    return _canonical_url(content_url) or str(content_url or "").strip()


def _profile_incremental_state(kol_pool_id: int | None) -> dict[str, Any]:
    if not kol_pool_id:
        return {
            "enabled": True,
            "kol_pool_id": None,
            "last_video_at": "",
            "profile_backfilled_at": "",
            "mode": "full_first_profile_crawl",
        }
    conn = get_conn()
    columns = _table_columns(conn, "vkpi_kol_pool")
    selected = ["id"]
    for column in ("last_video_at", "raw_platform_data"):
        if column in columns:
            selected.append(column)
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        return {
            "enabled": True,
            "kol_pool_id": int(kol_pool_id),
            "last_video_at": "",
            "profile_backfilled_at": "",
            "mode": "missing_existing_profile",
        }
    row_dict = dict(row)
    raw_payload = _load_json(row_dict.get("raw_platform_data"))
    profile_backfilled_at = _raw_profile_backfilled_at(raw_payload)
    last_video_at = _parse_date(row_dict.get("last_video_at")) or ""
    return {
        "enabled": True,
        "kol_pool_id": int(kol_pool_id),
        "last_video_at": last_video_at,
        "profile_backfilled_at": profile_backfilled_at,
        "mode": "incremental_after_last_video" if last_video_at else "full_no_last_video_at",
    }


def _raw_profile_backfilled_at(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    backfill = payload.get("profile_basics_backfill")
    if isinstance(backfill, dict):
        value = _metadata_text(backfill.get("profile_backfilled_at"))
        if value:
            return value
    profile_backfill = payload.get("profile_backfill")
    if isinstance(profile_backfill, dict):
        value = _metadata_text(profile_backfill.get("profile_backfilled_at") or profile_backfill.get("created_at"))
        if value:
            return value
    value = _metadata_text(payload.get("profile_backfilled_at"))
    return value


def _fit_changed_ids(result: dict[str, Any]) -> list[int]:
    ids = result.get("viltrox_fit_score_changed_ids") if isinstance(result, dict) else []
    if not isinstance(ids, list):
        return []
    changed: list[int] = []
    for item in ids:
        try:
            changed.append(int(item))
        except (TypeError, ValueError):
            continue
    return changed


def _video_creator_resolved(video_flow: dict[str, Any]) -> bool:
    identity = video_flow.get("creator_identity") if isinstance(video_flow, dict) else {}
    return isinstance(identity, dict) and _has_matchable_creator_identity(identity)


def _profile_classified_from_video_flow(
    classified: ClassifiedUrl,
    video_flow: dict[str, Any],
) -> ClassifiedUrl | None:
    identity = video_flow.get("creator_identity") if isinstance(video_flow, dict) else {}
    if not isinstance(identity, dict):
        return None
    return _classified_from_creator_identity(classified, identity)


def _profile_data_for_new_video_creator(
    profile_classified: ClassifiedUrl,
    crawl: dict[str, Any],
    video_flow: dict[str, Any],
    *,
    max_posts: int,
) -> dict[str, Any]:
    identity = video_flow.get("creator_identity") if isinstance(video_flow.get("creator_identity"), dict) else {}
    metadata = video_flow.get("video_metadata") if isinstance(video_flow.get("video_metadata"), dict) else {}
    profile_data = _profile_data_from_crawl(profile_classified, crawl, existing_match={}, max_posts=max_posts)
    now = datetime.now(timezone.utc).isoformat()

    profile_data["platform"] = profile_classified.platform
    profile_data["handle"] = profile_classified.handle or profile_classified.channel_id
    profile_data["profile_url"] = profile_data.get("profile_url") or profile_classified.normalized_url
    if not profile_data.get("avatar_url") and identity.get("avatar_url"):
        profile_data["avatar_url"] = identity.get("avatar_url")
    if not profile_data.get("followers") and identity.get("followers") is not None:
        profile_data["followers"] = identity.get("followers")
    if not profile_data.get("bio") and identity.get("bio"):
        profile_data["bio"] = identity.get("bio")
    profile_data["profile_backfilled_at"] = now
    if not profile_data.get("last_video_at"):
        profile_data["last_video_at"] = _video_metadata_date(metadata)

    raw_payload = _load_json(profile_data.get("raw_platform_data"))
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    raw_payload["video_url_creator_bootstrap"] = {
        "method": "url_deep_crawl_video_new_creator_v1",
        "source_url": metadata.get("content_url") or "",
        "video_title": metadata.get("title") or "",
        "video_id": metadata.get("video_id") or metadata.get("id") or "",
        "creator_identity": identity,
        "profile_backfilled_at": now,
    }
    profile_data["raw_platform_data"] = _json(raw_payload)
    return profile_data


def _video_metadata_date(metadata: dict[str, Any]) -> str:
    if not isinstance(metadata, dict):
        return ""
    for key in ("publish_date", "posted_at", "published_at", "publishedAt", "uploadDate", "date", "timestamp"):
        parsed = _parse_date(metadata.get(key))
        if parsed:
            return parsed
    return ""


def _compact_profile_write_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or not result:
        return {}
    return {
        "ok": result.get("ok"),
        "operation": result.get("operation"),
        "kol_pool_id": result.get("kol_pool_id"),
        "fields_written": result.get("fields_written"),
        "ignored_fields": result.get("ignored_fields"),
        "missing_columns": result.get("missing_columns"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
        "method": result.get("method"),
    }


def _compact_video_evidence_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or not result:
        return {}
    return {
        "ok": result.get("ok"),
        "status": result.get("status"),
        "operation": result.get("operation"),
        "kol_pool_id": result.get("kol_pool_id"),
        "evidence_id": result.get("evidence_id"),
        "source_url": result.get("source_url"),
        "fields_written": result.get("fields_written"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
        "method": result.get("method"),
    }


def _compact_enqueue_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or not result:
        return {}
    job = result.get("job")
    cache = result.get("cache")
    compact: dict[str, Any] = {
        "status": result.get("status"),
        "kol_pool_id": result.get("kol_pool_id"),
        "evidence_id": result.get("evidence_id"),
        "derive_method": result.get("derive_method"),
        "provider_calls": result.get("provider_calls"),
        "write_db": result.get("write_db"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids") or [],
    }
    if isinstance(job, dict):
        compact["job"] = {
            "id": job.get("id"),
            "job_type": job.get("job_type"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
        }
    if isinstance(cache, dict):
        compact["cache"] = {
            "id": cache.get("id"),
            "status": cache.get("status"),
            "derive_method": cache.get("derive_method"),
        }
    if result.get("reason"):
        compact["reason"] = result.get("reason")
    return compact


def _enqueue_account_dossier_extract_followup(
    conn: Any,
    *,
    kol_pool_id: int | None,
    source: str,
    trigger: str,
    source_url: str,
    query_text: str = "",
) -> dict[str, Any] | None:
    if not kol_pool_id:
        return None
    existing = conn.execute(
        """
        SELECT id, job_type, status, created_at, updated_at
        FROM apify_jobs
        WHERE job_type='account_dossier_extract'
          AND status IN ('queued', 'running')
          AND payload->>'target_type'='kol_pool'
          AND payload->>'target_id'=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (str(int(kol_pool_id)),),
    ).fetchone()
    if existing:
        return {
            "status": "already_queued" if existing["status"] == "queued" else "already_running",
            "job": {
                "id": existing["id"],
                "job_type": existing["job_type"],
                "status": existing["status"],
                "created_at": existing["created_at"],
                "updated_at": existing["updated_at"],
            },
            "kol_pool_id": int(kol_pool_id),
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }
    payload = {
        "target_type": "kol_pool",
        "target_id": str(int(kol_pool_id)),
        "derive_method": "kol_account_dossier_extract_v1",
        "analysis_kind": "profile_llm",
        "source": source,
        "trigger": trigger,
        "source_url": source_url,
        "query_text": query_text or f"account dossier - kol_pool #{int(kol_pool_id)}",
    }
    row = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES ('account_dossier_extract', ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, created_at, updated_at
        """,
        (json.dumps(payload, ensure_ascii=False, default=str),),
    ).fetchone()
    try:
        conn.commit()
    except Exception:
        pass
    return {
        "status": "queued",
        "job": dict(row) if row else None,
        "kol_pool_id": int(kol_pool_id),
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
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
        if not videos_items and isinstance(profile_items, list) and profile_items and isinstance(profile_items[0], dict):
            # IG 断点(2026-06-12 审计):instagram-profile-scraper 的 dataset item 是 profile 对象,
            # 帖子嵌在 profile.latestPosts 里——此前没人下钻这层,IG 账号分析永远 no_history_video_url。
            # 下钻口径对齐 industry/snapshot_collector.py。
            profile_obj = profile_items[0]
            for nested_key in ("latestPosts", "posts", "videos"):
                nested = profile_obj.get(nested_key)
                if isinstance(nested, list) and nested:
                    videos_items = [item for item in nested if isinstance(item, dict) and _looks_like_content_item(item)]
                    if videos_items:
                        break

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
        value = int(body.get("max_posts") or body.get("history_video_limit") or 3)
    except (TypeError, ValueError):
        value = 3
    upper = 120 if _profile_should_materialize_history_videos(body) else 12
    return max(1, min(upper, value))


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


def _next_action(
    classified: ClassifiedUrl,
    matches: list[dict[str, Any]],
    *,
    video_flow: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
                "description": "下一步可打开现有档案，或在确认后执行安全基础补档；auto 模式会尝试分析 1 条代表视频。",
            }
        return {
            "code": "profile_not_in_pool",
            "label": "不在库",
            "description": "下一步可在确认后新建最小档案并执行安全基础补档；auto 模式会尝试分析 1 条代表视频。",
        }
    if classified.url_type == "video":
        if matches:
            return {
                "code": "video_creator_found_in_pool",
                "label": "视频创作者已在库",
                "description": "下一步可在确认后做单帖预览或排入 final_v1 深度分析。",
            }
        if video_flow and _video_creator_resolved(video_flow):
            identity = video_flow.get("creator_identity") if isinstance(video_flow.get("creator_identity"), dict) else {}
            creator = _metadata_text(identity.get("display_name") or identity.get("handle") or identity.get("channel_id"))
            return {
                "code": "video_creator_resolved_not_in_pool",
                "label": "视频创作者已解析，未在库",
                "description": f"下一步可确认后为 {creator or '该创作者'} 建档，并分析当前视频。",
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
        if len(parts) >= 3 and lowered[1] in {"p", "reel", "tv"}:
            return parts[2]
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


# ── 队列铁律(2026-06-12 裁令:所有 LLM 搜索都要进左侧队列)──
DEEP_CRAWL_JOB_TYPE = "kol_profile_deep_crawl"


def enqueue_profile_deep_crawl_job(
    url: str,
    *,
    kol_pool_id: int | None = None,
    max_posts: int = 3,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把账号深爬 execute 入 apify_jobs 队列(泳道可见),替代同步 HTTP 内爬。

    幂等:同 URL 已有 queued/running 任务则返回 already_queued。
    """
    import json as _json

    conn = get_conn()
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("url required")
    active = conn.execute(
        """
        SELECT id FROM apify_jobs
        WHERE job_type=? AND status IN ('queued','running')
          AND payload->>'url'=? LIMIT 1
        """,
        (DEEP_CRAWL_JOB_TYPE, clean_url),
    ).fetchone()
    if active:
        return {"status": "already_queued", "job_id": int(dict(active)["id"])}
    payload = {
        "url": clean_url,
        "kol_pool_id": int(kol_pool_id) if kol_pool_id else None,
        "max_posts": max(1, min(12, int(max_posts or 3))),
        # 泳道 label=kind+query_text,kind 已是「账号分析」——query_text 只留 URL,
        # 否则显示成"账号分析 · 账号分析 · url"(2026-06-12 截图案)。
        "query_text": clean_url[:96],
        "target_type": "kol_profile",
        # target_id=泳道点击回跳 MY KOL 的定位键(2026-06-12 裁令:从哪发起回哪去)
        "target_id": int(kol_pool_id) if kol_pool_id else None,
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
    }
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id
        """,
        (DEEP_CRAWL_JOB_TYPE, _json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    return {"status": "queued", "job_id": int(dict(job)["id"]) if job else None}


def run_profile_deep_crawl_for_job(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """worker 入口:执行账号深爬(与 HTTP execute 同一内核 dry_run_url_deep_crawl execute=True)。

    mode=account_deep 是视频采集的总开关(_profile_should_enqueue_representative_videos /
    _profile_should_materialize_history_videos 都按 mode 判),漏传则退化成纯资料刷新——
    job 921 案:8 秒 done 零视频。history 落 evidence(max_posts 条),代表作入析 1 条。
    """
    body = {
        "url": str(payload.get("url") or ""),
        "execute": True,
        "mode": "account_deep",
        "max_posts": payload.get("max_posts") or 3,
        "source": "queue:kol_profile_deep_crawl",
    }
    result = dry_run_url_deep_crawl(body)
    # 队列路径不经 HTTP 路由的 _attach_smart_url_session——session 必须在此自建,
    # 否则任务完成后 payload 无 search_session_id,泳道「最近完成」按规则将其滤掉(一闪而过案)。
    try:
        from app.domains.kol import search_sessions as kol_search_sessions

        session = kol_search_sessions.ensure_session_for_result(
            session_id=None,
            create=True,
            query_text=f"账号分析 · {body['url'][:80]}",
            query_type="url",
            source="queue:kol_profile_deep_crawl",
            input_payload={key: value for key, value in body.items() if key != "api_token"},
            staff=staff,
        )
        if session:
            result["search_session"] = kol_search_sessions.attach_url_result(int(session["id"]), result)
            result["search_session_id"] = int(session["id"])
    except Exception:
        logger.warning("deep_crawl session attach failed url=%s", body.get("url"))
    # 媒体进 R2(2026-06-12 裁令:"理论都是在 R2 然后回传"):深爬产出的 evidence
    # 缩略图(cache_image)与非 YT 平台视频(cache_video_for_item,YT 走 embed 不缓存)
    # 就地喂缓存——失败不毁任务(媒体缓存属增强,非主链)。
    kol_pool_id = payload.get("kol_pool_id")
    if kol_pool_id:
        try:
            from app.domains.media.cache import cache_image, cache_video_for_item

            conn = get_conn()
            rows = conn.execute(
                "SELECT id, platform, content_url, thumbnail_url FROM vkpi_kol_video_evidence "
                "WHERE kol_pool_id=? ORDER BY id DESC LIMIT 12",
                (int(kol_pool_id),),
            ).fetchall()
            warm_stats: list[str] = []
            videos_warmed = 0
            for row in rows:
                item = dict(row)
                if item.get("thumbnail_url"):
                    img = cache_image(item["thumbnail_url"])
                    warm_stats.append(f"img#{item['id']}:{img.get('status')}")
                platform_key = str(item.get("platform") or "").lower()
                # 视频下载重(IG 经 ytdlp ~100s/条),只喂前 3 条免得串行 worker 被卡 20 分钟;
                # 缩略图轻,12 条全喂。
                if platform_key and platform_key != "youtube" and item.get("content_url") and videos_warmed < 3:
                    vid = cache_video_for_item(platform_key, str(item["id"]), item["content_url"])
                    videos_warmed += 1
                    reason = vid.get("skip_reason") or vid.get("reason") or ""
                    warm_stats.append(f"vid#{item['id']}:{vid.get('status')}{':' + str(reason) if reason else ''}")
            logger.info(
                "deep_crawl media r2 warm kol_pool_id=%s %s",
                kol_pool_id,
                " ".join(warm_stats) or "no_evidence_rows",
            )
        except Exception:
            logger.warning("deep_crawl media r2 warm failed kol_pool_id=%s", kol_pool_id)
    return result
