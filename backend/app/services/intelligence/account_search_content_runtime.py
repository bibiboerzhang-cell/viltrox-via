"""Bounded Apify content-search planning and candidate normalization.

This module is a leaf of ``account_search_discovery``.  It owns no provider
selection policy beyond the existing platform-to-actor mapping and performs no
ranking or fit scoring.  In particular, titles, descriptions, captions and
transcripts remain separate evidence fields.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List


@dataclass(frozen=True)
class ContentRuntimeDependencies:
    """Injected legacy helpers keep this module outside the intelligence SCC."""

    avatar_url_policy: Callable[..., tuple[str, str]]
    candidate_identity_key: Callable[[Dict[str, Any]], str]
    clean_url: Callable[[Any], str]
    douyin_actor_id: Callable[[str], str]
    douyin_search_payload: Callable[[str, int], Dict[str, Any]]
    known_text: Callable[..., str]
    normalize_douyin_item: Callable[..., Dict[str, Any]]
    normalize_int: Callable[[Any], int]
    published_value: Callable[[Dict[str, Any]], str]
    source_key: Callable[..., str]
    instagram_collapse_owner_posts: Callable[..., List[Dict[str, Any]]]
    instagram_hashtags: Callable[[str], List[str]]
    instagram_enrich_min_budget_seconds: Callable[[], float]
    instagram_enrich_targets: Callable[..., tuple[List[str], int]]
    short_search_queries: Callable[[str], List[str]]
    tiktok_collapse_author_videos: Callable[..., List[Dict[str, Any]]]


@dataclass(frozen=True)
class ActorPlan:
    actor_id: str
    payload: Dict[str, Any]
    timeout: int
    provider_queries: List[str]


@dataclass(frozen=True)
class PreparedActorItems:
    raw_items: List[Dict[str, Any]]
    instagram_profiles: Dict[str, Dict[str, Any]]
    instagram_raw_posts: int
    instagram_enrich_prefiltered: int
    instagram_enrich_requested: int
    instagram_enrich_note: str


def _instagram_actor_plan(
    search_query: str,
    safe_limit: int,
    deps: ContentRuntimeDependencies,
) -> tuple[ActorPlan | None, Dict[str, Any] | None]:
    hashtags = deps.instagram_hashtags(search_query)
    if not hashtags:
        return None, {
            "status": "invalid_query",
            "items": [],
            "message": "instagram hashtag query is empty after normalization",
        }
    return ActorPlan(
        actor_id="apify/instagram-hashtag-scraper",
        payload={
            "hashtags": hashtags,
            "resultsLimit": min(max(1, safe_limit) * 3, 100),
            "resultsType": "posts",
        },
        timeout=300,
        provider_queries=[search_query],
    ), None


def _douyin_actor_plan(
    search_query: str,
    safe_limit: int,
    deps: ContentRuntimeDependencies,
) -> tuple[ActorPlan | None, Dict[str, Any] | None]:
    actor_id = deps.douyin_actor_id("search")
    if not actor_id:
        return None, {
            "status": "actor_not_configured",
            "platform": "douyin",
            "items": [],
            "message": "APIFY_DOUYIN_SEARCH_ACTOR_ID or APIFY_DOUYIN_ACTOR_ID is not configured",
        }
    return ActorPlan(
        actor_id=actor_id,
        payload=deps.douyin_search_payload(search_query, safe_limit),
        timeout=360,
        provider_queries=[search_query],
    ), None


def build_actor_plan(
    normalized_platform: str,
    search_query: str,
    safe_limit: int,
    *,
    exact_query: bool,
    deps: ContentRuntimeDependencies,
) -> tuple[ActorPlan | None, Dict[str, Any] | None]:
    """Return the legacy actor input or its exact public error payload."""

    if normalized_platform == "youtube":
        return ActorPlan(
            actor_id="streamers/youtube-scraper",
            payload={
                "searchQueries": [search_query],
                "maxResults": safe_limit,
                "maxResultsShorts": 0,
                "maxResultStreams": 0,
            },
            timeout=240,
            provider_queries=[search_query],
        ), None
    if normalized_platform == "tiktok":
        provider_queries = (
            [search_query] if exact_query else deps.short_search_queries(search_query)
        )
        return ActorPlan(
            actor_id="clockworks/free-tiktok-scraper",
            payload={
                "searchQueries": provider_queries,
                "resultsPerPage": max(
                    3,
                    (safe_limit + len(provider_queries) - 1) // len(provider_queries),
                ),
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            },
            timeout=240,
            provider_queries=provider_queries,
        ), None
    if normalized_platform == "instagram":
        return _instagram_actor_plan(search_query, safe_limit, deps)
    if normalized_platform == "facebook":
        actor_id = (
            os.getenv("APIFY_FACEBOOK_SEARCH_ACTOR_ID")
            or "apify/facebook-search-scraper"
        ).strip()
        return ActorPlan(
            actor_id=actor_id,
            payload={
                "searchQueries": [search_query],
                "searchType": "posts",
                "resultsLimit": safe_limit,
            },
            timeout=300,
            provider_queries=[search_query],
        ), None
    if normalized_platform == "douyin":
        return _douyin_actor_plan(search_query, safe_limit, deps)
    return None, {
        "status": "unsupported_platform",
        "items": [],
        "message": f"{normalized_platform} platform search is not configured",
    }


async def _instagram_profiles_with_budget(
    targets: List[str],
    *,
    deadline_seconds: float | None,
    leg_started_monotonic: float,
    owner_profiles: Callable[[List[str]], Awaitable[Dict[str, Dict[str, Any]]]],
    logger: Any,
    deps: ContentRuntimeDependencies,
) -> tuple[Dict[str, Dict[str, Any]], str]:
    budget = (
        None
        if deadline_seconds is None
        else float(deadline_seconds) - (time.monotonic() - leg_started_monotonic)
    )
    if (
        budget is not None
        and budget < deps.instagram_enrich_min_budget_seconds()
    ):
        logger.info(
            "scanner.instagram_profile_enrich_skipped budget_left=%.1fs targets=%d",
            budget,
            len(targets),
        )
        return {}, "deadline_budget_exhausted"
    try:
        if budget is None:
            return await owner_profiles(targets), ""
        return await asyncio.wait_for(owner_profiles(targets), timeout=budget), ""
    except asyncio.TimeoutError:
        logger.warning("scanner.instagram_profile_enrich_timeout budget=%.1fs", budget)
        return {}, "deadline_timeout"
    except Exception as exc:
        logger.warning(
            "scanner.instagram_profile_enrich_failed",
            extra={"error": str(exc)[:300]},
        )
        return {}, "actor_failed"


async def prepare_actor_items(
    normalized_platform: str,
    raw_items: List[Dict[str, Any]],
    *,
    safe_limit: int,
    enrich_prefilter: Any,
    deadline_seconds: float | None,
    leg_started_monotonic: float,
    owner_profiles: Callable[[List[str]], Awaitable[Dict[str, Dict[str, Any]]]],
    logger: Any,
    deps: ContentRuntimeDependencies,
) -> PreparedActorItems:
    """Collapse creator duplicates and enrich only eligible Instagram owners."""

    instagram_raw_posts = len(raw_items)
    if normalized_platform == "tiktok" and raw_items:
        raw_items = deps.tiktok_collapse_author_videos(raw_items, safe_limit)
    if normalized_platform != "instagram" or not raw_items:
        return PreparedActorItems(raw_items, {}, instagram_raw_posts, 0, 0, "")

    raw_items = deps.instagram_collapse_owner_posts(raw_items, safe_limit)
    targets, prefiltered = deps.instagram_enrich_targets(raw_items, enrich_prefilter)
    if not targets:
        return PreparedActorItems(
            raw_items,
            {},
            instagram_raw_posts,
            prefiltered,
            0,
            "no_target" if prefiltered else "",
        )
    profiles, note = await _instagram_profiles_with_budget(
        targets,
        deadline_seconds=deadline_seconds,
        leg_started_monotonic=leg_started_monotonic,
        owner_profiles=owner_profiles,
        logger=logger,
        deps=deps,
    )
    return PreparedActorItems(
        raw_items,
        profiles,
        instagram_raw_posts,
        prefiltered,
        len(targets),
        note,
    )


def _empty_fields() -> Dict[str, Any]:
    return {
        "handle": "",
        "avatar_url": "",
        "avatar_url_status": "missing",
        "thumbnail_url": "",
        "bio": "",
        "followers": 0,
        "channel_id": "",
        "content_language": "",
        "content_description": "",
        "content_caption": "",
        "content_transcript": "",
    }


def _youtube_fields(
    item: Dict[str, Any],
    deps: ContentRuntimeDependencies,
) -> Dict[str, Any]:
    _source_key = deps.source_key
    _avatar_url_policy = deps.avatar_url_policy
    _clean_url = deps.clean_url
    _normalize_int = deps.normalize_int
    fields = _empty_fields()
    channel_url = _source_key(item, "channelUrl", "channelURL")
    channel_id = _source_key(item, "channelId", "channel.id")
    if not channel_id:
        match = re.search(
            r"(?:youtube\.com)/(?:channel)/([^/?#]+)",
            channel_url,
            re.IGNORECASE,
        )
        channel_id = str(match.group(1) if match else "").strip()
    avatar_url, avatar_status = _avatar_url_policy(
        _source_key(
            item,
            "channelAvatar",
            "channelThumbnail",
            "channelImage",
            "avatarUrl",
            "authorThumbnail",
        )
    )
    fields.update({
        "channel_name": _source_key(item, "channelName", "channelTitle", "author"),
        "channel_url": channel_url,
        "channel_id": channel_id,
        "handle": _source_key(item, "channelHandle", "channelUsername", "handle") or channel_id,
        "avatar_url": avatar_url,
        "avatar_url_status": avatar_status,
        "thumbnail_url": _clean_url(_source_key(item, "thumbnailUrl", "thumbnail", "image", "cover")),
        "source_url": _source_key(item, "url", "link"),
        "title": _source_key(item, "title", "text"),
        "content_description": _source_key(item, "description", "shortDescription"),
        "content_transcript": _source_key(item, "transcript", "subtitle", "subtitles"),
        "views": _normalize_int(item.get("viewCount") or item.get("views")),
        "likes": _normalize_int(item.get("likes")),
        "comments": _normalize_int(item.get("commentsCount") or item.get("comments")),
        "followers": _normalize_int(
            item.get("numberOfSubscribers")
            or item.get("subscriberCount")
            or item.get("subscribers")
            or 0
        ),
        "content_language": _source_key(
            item, "videoLanguage", "defaultAudioLanguage", "language",
        ),
    })
    return fields


def _tiktok_fields(
    item: Dict[str, Any],
    deps: ContentRuntimeDependencies,
) -> Dict[str, Any]:
    _source_key = deps.source_key
    _avatar_url_policy = deps.avatar_url_policy
    _clean_url = deps.clean_url
    _normalize_int = deps.normalize_int
    fields = _empty_fields()
    author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
    handle = _source_key(author, "name") or _source_key(item, "author")
    title = _source_key(item, "text", "desc", "title")
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    avatar_url, avatar_status = _avatar_url_policy(
        _source_key(
            author,
            "avatar",
            "avatarThumb",
            "avatarMedium",
            "avatarLarger",
            "profilePicture",
        )
    )
    fields.update({
        "channel_name": _source_key(author, "nickName", "name") or _source_key(item, "authorName", "author"),
        "handle": handle,
        "channel_url": f"https://www.tiktok.com/@{handle}" if handle else "",
        "avatar_url": avatar_url,
        "avatar_url_status": avatar_status,
        "thumbnail_url": _clean_url(_source_key(item, "videoMeta.coverUrl", "cover", "coverUrl", "thumbnail")),
        "source_url": _source_key(item, "webVideoUrl", "url"),
        "title": title,
        "content_caption": title,
        "content_transcript": _source_key(item, "transcript", "subtitle", "subtitles"),
        "views": _normalize_int(item.get("playCount") or stats.get("playCount")),
        "likes": _normalize_int(item.get("diggCount") or stats.get("diggCount")),
        "comments": _normalize_int(item.get("commentCount") or stats.get("commentCount")),
        "followers": _normalize_int(
            author.get("fans") or author.get("followers") or author.get("followerCount") or 0
        ),
    })
    return fields


def _facebook_handle(channel_url: str, handle: str) -> str:
    if handle or "facebook.com/" not in channel_url:
        return handle
    slug = (
        channel_url.split("facebook.com/", 1)[1]
        .strip("/")
        .split("/", 1)[0]
        .split("?", 1)[0]
    )
    reserved = {
        "profile.php", "people", "pages", "groups", "watch", "reel",
        "reels", "search",
    }
    return slug if slug and slug.lower() not in reserved else ""


def _facebook_fields(
    item: Dict[str, Any],
    deps: ContentRuntimeDependencies,
) -> Dict[str, Any]:
    _source_key = deps.source_key
    _avatar_url_policy = deps.avatar_url_policy
    _clean_url = deps.clean_url
    _normalize_int = deps.normalize_int
    fields = _empty_fields()
    channel_url = _clean_url(
        _source_key(item, "pageUrl", "user.profileUrl", "facebookUrl", "topLevelUrl")
    )
    handle = _facebook_handle(
        channel_url,
        _source_key(item, "pageHandle", "username", "user.username", "pageUsername"),
    )
    if not channel_url and handle:
        channel_url = f"https://www.facebook.com/{handle}"
    title = _source_key(item, "text", "message", "title")
    avatar_url, avatar_status = _avatar_url_policy(
        _source_key(item, "user.profilePic", "pageProfilePic", "profilePic", "avatar")
    )
    fields.update({
        "channel_name": _source_key(item, "pageName", "user.name", "authorName", "pageTitle", "name"),
        "channel_url": channel_url,
        "handle": handle,
        "avatar_url": avatar_url,
        "avatar_url_status": avatar_status,
        "thumbnail_url": _clean_url(_source_key(item, "thumbnail", "imageUrl", "image")),
        "source_url": _source_key(item, "url", "postUrl", "topLevelUrl"),
        "title": title,
        # Facebook's ``text``/``message`` is the post body, not a separate
        # provider title, so preserving it as description is real evidence.
        "content_description": title,
        "views": _normalize_int(item.get("viewsCount") or item.get("views")),
        "likes": _normalize_int(item.get("likesCount") or item.get("reactionsCount") or item.get("likes")),
        "comments": _normalize_int(item.get("commentsCount") or item.get("comments")),
        "followers": _normalize_int(
            _source_key(item, "followers", "followersCount", "pageFollowers", "user.followers") or 0
        ),
    })
    return fields


def _douyin_fields(
    item: Dict[str, Any],
    deps: ContentRuntimeDependencies,
) -> Dict[str, Any]:
    _normalize_douyin_item = deps.normalize_douyin_item
    _avatar_url_policy = deps.avatar_url_policy
    _normalize_int = deps.normalize_int
    fields = _empty_fields()
    post = _normalize_douyin_item(item)
    channel_name = str(post.get("channel") or "Unknown creator")
    avatar_url, avatar_status = _avatar_url_policy(post.get("avatar_url"))
    title = str(post.get("title") or "")
    fields.update({
        "channel_name": channel_name,
        "handle": str(post.get("handle") or channel_name),
        "channel_url": str(post.get("channel_url") or ""),
        "avatar_url": avatar_url,
        "avatar_url_status": avatar_status,
        "thumbnail_url": str(post.get("thumbnail") or ""),
        "source_url": str(post.get("url") or ""),
        "title": title,
        "content_caption": title,
        "views": _normalize_int(post.get("views")),
        "likes": _normalize_int(post.get("likes")),
        "comments": _normalize_int(post.get("comments")),
    })
    return fields


def _instagram_fields(
    item: Dict[str, Any],
    profiles: Dict[str, Dict[str, Any]],
    deps: ContentRuntimeDependencies,
) -> Dict[str, Any]:
    _source_key = deps.source_key
    _avatar_url_policy = deps.avatar_url_policy
    _clean_url = deps.clean_url
    _normalize_int = deps.normalize_int
    fields = _empty_fields()
    channel_name = _source_key(item, "ownerUsername", "username", "ownerFullName")
    handle = _source_key(item, "ownerUsername", "username")
    avatar_url, avatar_status = _avatar_url_policy(
        _source_key(
            item,
            "ownerProfilePicUrl",
            "profilePicUrl",
            "profilePictureUrl",
            "displayProfilePicUrl",
            "avatarUrl",
        )
    )
    title = _source_key(item, "caption", "title", "text")
    fields.update({
        "channel_name": channel_name,
        "handle": handle,
        "channel_url": f"https://www.instagram.com/{channel_name}/" if channel_name else _source_key(item, "ownerProfileUrl"),
        "avatar_url": avatar_url,
        "avatar_url_status": avatar_status,
        "thumbnail_url": _clean_url(_source_key(item, "displayUrl", "imageUrl", "thumbnailUrl", "thumbnail", "image")),
        "source_url": _source_key(item, "url", "shortCode"),
        "title": title,
        "content_caption": title,
        "views": _normalize_int(item.get("videoViewCount") or item.get("videoPlayCount")),
        "likes": _normalize_int(item.get("likesCount")),
        "comments": _normalize_int(item.get("commentsCount")),
    })
    profile = profiles.get(str(handle or channel_name or "").strip().lower())
    if not isinstance(profile, dict):
        return fields
    fields["followers"] = _normalize_int(
        profile.get("followersCount") or profile.get("followers") or 0
    )
    fields["channel_name"] = _source_key(profile, "fullName") or channel_name
    refreshed_avatar, refreshed_status = _avatar_url_policy(
        _source_key(profile, "profilePicUrlHD", "profilePicUrl")
    )
    if refreshed_avatar:
        fields["avatar_url"] = refreshed_avatar
        fields["avatar_url_status"] = refreshed_status
    elif not fields["avatar_url"] and refreshed_status != "missing":
        fields["avatar_url_status"] = refreshed_status
    fields["bio"] = str(profile.get("biography") or "").strip()[:500]
    return fields


def _platform_fields(
    normalized_platform: str,
    item: Dict[str, Any],
    instagram_profiles: Dict[str, Dict[str, Any]],
    deps: ContentRuntimeDependencies,
) -> Dict[str, Any]:
    if normalized_platform == "youtube":
        return _youtube_fields(item, deps)
    if normalized_platform == "tiktok":
        return _tiktok_fields(item, deps)
    if normalized_platform == "facebook":
        return _facebook_fields(item, deps)
    if normalized_platform == "douyin":
        return _douyin_fields(item, deps)
    return _instagram_fields(item, instagram_profiles, deps)


def _candidate_from_fields(
    fields: Dict[str, Any],
    item: Dict[str, Any],
    *,
    normalized_platform: str,
    market: str,
    normalized_query: str,
    actor_id: str,
    deps: ContentRuntimeDependencies,
) -> Dict[str, Any]:
    _known_text = deps.known_text
    _published_value = deps.published_value
    channel_name = fields["channel_name"]
    handle = fields["handle"]
    candidate = {
        "platform": normalized_platform,
        "channel_name": _known_text(channel_name, handle) or "Unknown creator",
        "handle": _known_text(handle, channel_name),
        "avatar_url": fields["avatar_url"],
        "avatar_url_status": fields["avatar_url_status"],
        "thumbnail_url": fields["thumbnail_url"],
        "channel_url": fields["channel_url"],
        "source_url": fields["source_url"],
        "sample_title": fields["title"][:300],
        **({"sample_description": fields["content_description"][:2000]} if fields["content_description"] else {}),
        **({"sample_caption": fields["content_caption"][:2000]} if fields["content_caption"] else {}),
        **({"sample_transcript": fields["content_transcript"][:6000]} if fields["content_transcript"] else {}),
        "views": fields["views"],
        "likes": fields["likes"],
        "comments": fields["comments"],
        "avg_views": fields["views"],
        "published": _published_value(item),
        "market": (market or "").strip().upper(),
        "search_query": normalized_query,
        "provider_actor": actor_id,
        **({"followers": fields["followers"]} if fields["followers"] > 0 else {}),
        **({"bio": fields["bio"]} if fields["bio"] else {}),
        **({"channel_id": fields["channel_id"]} if fields["channel_id"] else {}),
    }
    if fields["content_language"]:
        candidate.update({
            "language": fields["content_language"],
            "language_source": "platform_content_metadata",
        })
    return candidate


def normalize_actor_items(
    normalized_platform: str,
    raw_items: List[Dict[str, Any]],
    *,
    safe_limit: int,
    market: str,
    normalized_query: str,
    actor_id: str,
    instagram_profiles: Dict[str, Dict[str, Any]],
    deps: ContentRuntimeDependencies,
) -> List[Dict[str, Any]]:
    """Normalize provider rows and preserve first-row creator ordering."""

    items: List[Dict[str, Any]] = []
    seen_identities: set[str] = set()
    for item in raw_items[:safe_limit]:
        fields = _platform_fields(
            normalized_platform,
            item,
            instagram_profiles,
            deps,
        )
        candidate = _candidate_from_fields(
            fields,
            item,
            normalized_platform=normalized_platform,
            market=market,
            normalized_query=normalized_query,
            actor_id=actor_id,
            deps=deps,
        )
        identity_key = deps.candidate_identity_key(candidate)
        if identity_key and identity_key in seen_identities:
            continue
        if identity_key:
            seen_identities.add(identity_key)
        items.append(candidate)
    return items


def build_actor_result(
    *,
    normalized_platform: str,
    normalized_query: str,
    market: str,
    safe_limit: int,
    items: List[Dict[str, Any]],
    plan: ActorPlan,
    searched_at: str,
    prepared: PreparedActorItems,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "actor_id": plan.actor_id,
        "requested": safe_limit,
    }
    metadata.update({
        "returned": len(items),
        "provider_queries": plan.provider_queries,
        "searched_at": searched_at,
        "pagination_supported": False,
        "pagination_unsupported_reason": "actor_input_schema_has_no_cursor",
        "has_more": False,
    })
    if normalized_platform == "instagram":
        metadata.update({
            "raw_posts": prepared.instagram_raw_posts,
            "unique_owners": len(items),
            "profile_enriched": len(prepared.instagram_profiles),
            "profile_enrich_prefiltered": prepared.instagram_enrich_prefiltered,
            "profile_enrich_requested": prepared.instagram_enrich_requested,
        })
        if prepared.instagram_enrich_note:
            metadata["profile_enrich_degraded"] = prepared.instagram_enrich_note
    return {
        "status": "done",
        "platform": normalized_platform,
        "query": normalized_query,
        "market": (market or "").strip().upper(),
        "items": items,
        "metadata": metadata,
    }
