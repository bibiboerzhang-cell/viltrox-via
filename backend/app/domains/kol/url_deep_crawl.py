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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

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
from app.domains.kol.video_evidence import (
    ensure_video_evidence_from_url,
    find_video_evidence_by_url,
)
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

# Pure helpers live in url_deep_crawl_helpers; re-exported here under their
# original names so every internal reference and external import path is
# unchanged (behavior-preserving refactor — see that module's header).
from app.domains.kol.url_deep_crawl_helpers import (  # noqa: F401
    CN_VIDEO_ANALYSIS_PLATFORMS,
    _all_raw_strings,
    _canonical_url,
    _channel_id_from_handle,
    _cn_platform_from_host,
    _cn_video_id,
    _compact_enqueue_result,
    _compact_profile_write_result,
    _compact_video_evidence_result,
    _contains_identity,
    _duration_seconds,
    _fit_changed_ids,
    _has_matchable_creator_identity,
    _latest_video_date,
    _load_json,
    _max_posts,
    _metadata_text,
    _normalise_handle,
    _normalise_identity,
    _normalise_platform,
    _normalize_input_url,
    _parse_date,
    _platform_from_host,
    _profile_exclude_video_urls,
    _profile_history_video_limit,
    _profile_representative_video_limit,
    _profile_should_enqueue_representative_videos,
    _profile_should_materialize_history_videos,
    _profile_url_for_creator,
    _profile_video_dedupe_key,
    _profile_video_is_newer_than_cutoff,
    _public_profile_data,
    _public_video_metadata,
    _raw_profile_backfilled_at,
    _raw_values,
    _video_execute_mode,
    _video_id,
    _video_metadata_date,
)

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
    if classified.url_type == "video" and classified.platform in CN_VIDEO_ANALYSIS_PLATFORMS:
        # 中国平台「仅视频分析」:不匹配 KOL 池、不建档;真实取数/下载/深析
        # 全部发生在 durable worker(enqueue_video_url_resolve_job 队列)里。
        from app.domains.kol.cn_platform_video import cn_platform_video_flow_plan

        video_flow = cn_platform_video_flow_plan(classified)
    elif classified.url_type == "video" and classified.platform in SUPPORTED_PLATFORMS:
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

    if classified.url_type == "video" and classified.platform in CN_VIDEO_ANALYSIS_PLATFORMS:
        # CN 平台视频的 execute 全在 durable worker 队列里发生;HTTP 层永远只
        # 返回既定计划(_run_url_deep_crawl 会另行 enqueue_video_url_resolve_job)。
        pass
    elif execute and classified.url_type == "profile" and profile_flow.get("status") == "ready_to_execute":
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

    cn_platform = _cn_platform_from_host(host)
    if cn_platform:
        # 中国平台走「仅视频分析」通道:能识别视频/短链就按 video 分类;
        # 账号主页等其它形态诚实标注仅支持视频链接(不做 profile 建档)。
        cn_video_id = _cn_video_id(cn_platform, host, path, parsed.query)
        if cn_video_id:
            return ClassifiedUrl(original, normalized, "video", cn_platform, "", "", cn_video_id, "cn_video_pattern")
        return ClassifiedUrl(original, normalized, "unknown", cn_platform, "", "", "", "cn_platform_video_only")

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
    provider_called = False
    stored_evidence: dict[str, Any] | None = None

    try:
        stored_evidence = find_video_evidence_by_url(classified.normalized_url)
    except Exception:
        # A missing/old local evidence schema must not break URL classification;
        # the normal deferred provider path below remains available.
        logger.debug("url deep crawl stored video evidence lookup failed", exc_info=True)

    try:
        if stored_evidence:
            metadata = _video_metadata_from_stored_evidence(stored_evidence)
            provider_source = "stored_video_evidence"
        else:
            metadata = _fetch_video_metadata(classified.normalized_url)
            provider_source = str(metadata.get("scrape_source") or "").strip()
            provider_called = str(metadata.get("scrape_status") or "").lower() != "pending"
        metadata_identity = _creator_identity_from_video_metadata(classified, metadata)
        if metadata_identity:
            creator_identity = metadata_identity
        creator_classified = _classified_from_creator_identity(classified, creator_identity)
        if creator_classified:
            matches = _match_pool(creator_classified)
        if not matches and stored_evidence:
            evidence_match = _pool_candidate_from_stored_evidence(stored_evidence)
            if evidence_match:
                matches = [evidence_match]
    except Exception as exc:
        logger.warning("url deep crawl video metadata failed", exc_info=True)
        error = "video_metadata_unavailable"

    resolved = _has_matchable_creator_identity(creator_identity)
    provider_deferred = str(metadata.get("scrape_status") or "").lower() == "pending"
    status = "provider_refresh_pending" if provider_deferred else ("ready_to_execute" if resolved else "creator_unresolved")
    if error and not metadata:
        status = "metadata_failed"

    return (
        {
            "status": status,
            "operation": "video_creator_resolve",
            "provider_calls_performed": provider_called,
            "provider_source": provider_source or None,
            "creator_resolution_status": "resolved" if resolved else "unresolved",
            "creator_identity": creator_identity or None,
            "video_metadata": _public_video_metadata(metadata) if metadata else None,
            "evidence_id": _int_or_none((stored_evidence or {}).get("id")),
            "evidence_lookup_source": "stored_video_evidence" if stored_evidence else None,
            "metadata_error": error or None,
            "would_write": False,
            "would_enqueue_worker": provider_deferred,
            "business_tables_written": False,
            "llm_calls_performed": False,
            "viltrox_fit_touched": False,
        },
        matches,
    )


def _video_metadata_from_stored_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Project one stored evidence row into the public video-metadata shape."""

    return {
        "platform": evidence.get("platform"),
        "content_url": evidence.get("content_url"),
        "title": evidence.get("title") or evidence.get("video_title"),
        "view_count": evidence.get("view_count"),
        "like_count": evidence.get("like_count"),
        "comment_count": evidence.get("comment_count"),
        "share_count": evidence.get("share_count"),
        "publish_date": evidence.get("publish_date") or evidence.get("posted_at"),
        "posted_at": evidence.get("posted_at"),
        "duration_seconds": evidence.get("duration_seconds"),
        "thumbnail_url": evidence.get("thumbnail_url"),
        "media_kind": evidence.get("media_kind"),
        "image_urls": evidence.get("image_urls"),
        "channel_id": evidence.get("channel_id"),
        "channel_name": evidence.get("channel_name"),
        "scrape_source": evidence.get("scrape_source") or "stored_video_evidence",
        "scrape_status": evidence.get("scrape_status") or "success",
        "scrape_error": evidence.get("scrape_error"),
    }


def _pool_candidate_from_stored_evidence(evidence: dict[str, Any]) -> dict[str, Any] | None:
    """Use the evidence FK as a truthful fallback when old rows lack channel IDs."""

    kol_pool_id = _int_or_none(evidence.get("kol_pool_id"))
    if not kol_pool_id:
        return None
    for row in _pool_rows():
        if _int_or_none(row.get("id")) != kol_pool_id:
            continue
        return {
            "kol_pool_id": kol_pool_id,
            "platform": _normalise_platform(row.get("platform")),
            "handle": row.get("handle") or "",
            "display_name": row.get("display_name") or evidence.get("channel_name") or "",
            "profile_url": row.get("profile_url") or "",
            "match_source": "video_evidence",
            "match_priority": 0,
        }
    return None


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


def _profile_flow_plan(
    classified: ClassifiedUrl,
    matches: list[dict[str, Any]],
    body: dict[str, Any],
    *,
    execute: bool,
) -> dict[str, Any]:
    if classified.platform in CN_VIDEO_ANALYSIS_PLATFORMS:
        return {
            "status": "not_applicable",
            "operation": "cn_platform_video_analysis",
            "kol_pool_id": None,
            "message": "中国平台视频：仅做内容分析，不建人选档案（按地区规避不入 KOL 池）。",
            "crawl_performed": False,
            "business_tables_written": False,
        }
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
    incremental_state = _profile_incremental_state(kol_pool_id, force_full=_profile_force_full_history(body))
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



# execute + profile-build 簇已抽到 url_deep_crawl_execute.py(行为不变,函数体逐字搬运)。
# 下划线私有名在此显式 re-export,兜住本文件内调用点与任何外部导入路径。
from app.domains.kol.url_deep_crawl_execute import (  # noqa: F401,E402
    _cache_video_flow_url,
    _crawl_profile_basics,
    _crawler_for,
    _enqueue_account_dossier_extract_followup,
    _execute_existing_creator_video_flow,
    _execute_new_creator_video_flow,
    _execute_profile_flow,
    _execute_profile_history_video_evidence,
    _execute_profile_representative_video_analysis,
    _identity_profile_data,
    _profile_classified_from_video_flow,
    _profile_data_for_new_video_creator,
    _profile_data_from_crawl,
    _profile_force_full_history,
    _profile_incremental_state,
    _profile_target,
    _record_deep_crawl_run,
    _video_creator_resolved,
)


# 视频 metadata 提取簇已抽到 url_deep_crawl_video_meta.py(行为不变,re-export 兜调用点)。
from app.domains.kol.url_deep_crawl_video_meta import (  # noqa: F401,E402
    _filter_incremental_profile_videos,
    _metadata_from_profile_video_item,
    _profile_representative_video_metadata,
    _profile_video_id,
    _profile_video_thumbnail,
    _profile_video_url,
)

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
    if classified.platform in CN_VIDEO_ANALYSIS_PLATFORMS:
        if classified.url_type == "video":
            return {
                "code": "cn_platform_video",
                "label": "中国平台视频 · 仅内容分析",
                "description": "识别为中国平台视频链接。确认后仅做视频内容分析（元数据 + 视频深析），不建人选档案。",
            }
        return {
            "code": "cn_platform_video_only",
            "label": "中国平台 · 仅支持视频链接",
            "description": "bilibili / 抖音 / 小红书目前只支持粘贴具体视频（笔记）链接做内容分析，不支持账号主页。",
        }
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


# 队列/worker 入口簇已抽到 url_deep_crawl_queue.py(行为不变,re-export 兜调用点)。
# run_profile_deep_crawl_for_job 内对 dry_run_url_deep_crawl 用懒导入避免循环依赖。
from app.domains.kol.url_deep_crawl_queue import (  # noqa: F401,E402
    DEEP_CRAWL_JOB_TYPE,
    enqueue_profile_deep_crawl_job,
    enqueue_stored_video_analysis_job,
    profile_deep_crawl_is_fresh,
    run_profile_deep_crawl_for_job,
)

from app.domains.kol.video_url_resolver import (  # noqa: F401,E402
    VIDEO_URL_RESOLVE_JOB_TYPE,
    enqueue_video_url_resolve_job,
    run_video_url_resolve_for_job,
)
