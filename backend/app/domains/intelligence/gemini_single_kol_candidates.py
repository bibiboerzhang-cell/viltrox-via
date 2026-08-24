"""Pure cached-video selection for the single-KOL Gemini preflight.

This module owns URL validation, safe public projection, durable/raw candidate
merge, and deterministic Top1 ranking.  It performs no network, provider, or
database writes.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse, urlunparse

from app.core.coerce import _text


DURABLE_EVIDENCE_SCAN_LIMIT = 201
YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_VIDEO_PATHS = frozenset({"embed", "live", "shorts", "v"})
VILTROX_RE = re.compile(r"(?<![a-z0-9])viltrox", re.I)


def _lower(value: Any) -> str:
    return _text(value).lower()


def _int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _nested_dict(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _first_int(*values: Any) -> int:
    for value in values:
        parsed = _int(value)
        if parsed:
            return parsed
    return 0


def _canonical_youtube_url(value: Any) -> tuple[str, str]:
    """Strictly validate a YouTube video URL and drop all query baggage."""

    raw = _text(value).strip()
    if not raw or any(char.isspace() for char in raw):
        return "", ""
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except (TypeError, ValueError):
        return "", ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return "", ""
    if parsed.username or parsed.password:
        return "", ""
    if port is not None and port != (443 if parsed.scheme.lower() == "https" else 80):
        return "", ""
    host = (parsed.hostname or "").lower().rstrip(".")
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if host == "youtu.be" and len(parts) == 1:
        video_id = parts[0]
    elif host == "youtube.com":
        normalized_path = parsed.path.rstrip("/").lower()
        if normalized_path == "/watch":
            values = parse_qs(parsed.query, keep_blank_values=True).get("v") or []
            if len(values) == 1:
                video_id = values[0]
        elif len(parts) == 2 and parts[0].lower() in YOUTUBE_VIDEO_PATHS:
            video_id = parts[1]
    video_id = video_id.strip()
    if not YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
        return "", ""
    return f"https://www.youtube.com/watch?v={video_id}", video_id


def _public_url(value: Any) -> str:
    """Project a cached URL without credentials, query secrets, or fragments."""

    canonical, _video_id = _canonical_youtube_url(value)
    if canonical:
        return canonical
    raw = _text(value).strip()
    try:
        parsed = urlparse(raw)
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower().rstrip(".")
    return urlunparse((parsed.scheme.lower(), host, parsed.path or "/", "", "", ""))


def _is_youtube_url(url: str) -> bool:
    return bool(_canonical_youtube_url(url)[1])


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in (
            "items",
            "data",
            "results",
            "videos",
            "posts",
            "latestPosts",
            "latest_posts",
        ):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _looks_like_post(post: dict[str, Any]) -> bool:
    kind = _lower(post.get("kind"))
    if "channel" in kind and "video" not in kind:
        return False
    return any(
        key in post
        for key in (
            "video_url",
            "videoUrl",
            "webVideoUrl",
            "post_url",
            "permalink",
            "shareUrl",
            "content_url",
            "videoMeta",
            "playCount",
            "view_count",
            "statistics",
            "snippet",
            "shortCode",
            "caption",
            "title",
        )
    )


def _raw_posts(raw: Any, *, depth: int = 0) -> Iterable[dict[str, Any]]:
    if depth > 4:
        return
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                if _looks_like_post(item):
                    yield item
                yield from _raw_posts(item, depth=depth + 1)
        return
    if not isinstance(raw, dict):
        return
    for key in (
        "videos",
        "posts",
        "latestPosts",
        "latest_posts",
        "items",
        "data",
        "results",
    ):
        for item in _items(raw.get(key)):
            if _looks_like_post(item):
                yield item
            yield from _raw_posts(item, depth=depth + 1)
    for key in ("profile", "raw", "channel", "account"):
        value = raw.get(key)
        if isinstance(value, (dict, list)):
            yield from _raw_posts(value, depth=depth + 1)


def _candidate_from_post(
    post: dict[str, Any],
    *,
    item: dict[str, Any],
    index: int,
    source_kind: str,
) -> dict[str, Any] | None:
    snippet = _nested_dict(post, "snippet")
    localized = _nested_dict(snippet, "localized")
    stats = _nested_dict(post, "statistics", "stats", "metrics", "public_metrics")
    post_uid = _first_text(
        post.get("id"),
        post.get("post_uid"),
        post.get("post_id"),
        post.get("video_id"),
        post.get("videoId"),
        post.get("shortCode"),
        post.get("shortcode"),
    )
    raw_source_url = _first_text(
        post.get("source_url"),
        post.get("sourceUrl"),
        post.get("post_url"),
        post.get("url"),
        post.get("webVideoUrl"),
        post.get("permalink"),
        post.get("shareUrl"),
        post.get("content_url"),
        post.get("link"),
    )
    raw_video_url = _first_text(
        post.get("video_url"),
        post.get("videoUrl"),
        post.get("webVideoUrl"),
        raw_source_url,
    )
    kind = _lower(post.get("kind"))
    platform = _lower(item.get("platform"))
    canonical_video_url, canonical_video_id = _canonical_youtube_url(raw_video_url)
    canonical_source_url, canonical_source_id = _canonical_youtube_url(raw_source_url)
    youtube_url = canonical_video_url or canonical_source_url
    youtube_video_id = canonical_video_id or canonical_source_id
    if (
        not youtube_url
        and (kind == "youtube#video" or platform == "youtube")
        and YOUTUBE_VIDEO_ID_RE.fullmatch(post_uid)
    ):
        youtube_video_id = post_uid
        youtube_url = f"https://www.youtube.com/watch?v={post_uid}"
    source_url = canonical_source_url or _public_url(raw_source_url)
    video_url = youtube_url or _public_url(raw_video_url)
    title = _first_text(
        post.get("title"),
        post.get("caption"),
        post.get("text"),
        snippet.get("title"),
        localized.get("title"),
        post.get("description"),
        snippet.get("description"),
    )
    if not any((video_url, source_url, title)):
        return None
    views = _first_int(
        post.get("views"),
        post.get("view_count"),
        post.get("play_count"),
        post.get("playCount"),
        stats.get("viewCount"),
        stats.get("playCount"),
    )
    likes = _first_int(
        post.get("likes"),
        post.get("like_count"),
        post.get("diggCount"),
        stats.get("likeCount"),
    )
    comments = _first_int(
        post.get("comments"),
        post.get("comment_count"),
        post.get("commentCount"),
        stats.get("commentCount"),
    )
    relevance_text = " ".join(
        _text(value)
        for value in (
            post.get("title"),
            post.get("caption"),
            post.get("text"),
            post.get("description"),
            snippet.get("title"),
            snippet.get("description"),
            localized.get("title"),
            localized.get("description"),
            source_url,
            video_url,
        )
        if _text(value)
    )
    viltrox_relevant = bool(VILTROX_RE.search(relevance_text))
    reasons: list[str] = [source_kind]
    if youtube_video_id:
        reasons.append("youtube_url")
    if viltrox_relevant:
        reasons.append("viltrox_text_match")
    if views:
        reasons.append(f"views={views}")
    if likes:
        reasons.append(f"likes={likes}")
    score = 0
    score += 1_000_000 if youtube_video_id else 0
    score += 500 if video_url else 0
    score += 100_000 if viltrox_relevant else 0
    score += min(views, 10_000_000) // 1000
    score += min(likes, 500_000) // 100
    score += min(comments, 100_000) // 20
    score -= index
    return {
        "rank_basis": "youtube_then_viltrox_relevance_then_engagement_v1",
        "candidate_score": int(score),
        "source_kind": source_kind,
        "platform": _text(item.get("platform")),
        "handle": _text(item.get("handle")),
        "post_uid": youtube_video_id or post_uid,
        "title": title[:280],
        "url": youtube_url or source_url or video_url,
        "video_url": video_url,
        "source_url": source_url,
        "published_at": _first_text(
            post.get("published_at"),
            post.get("publishedAt"),
            post.get("timestamp"),
            post.get("createTimeISO"),
            post.get("date"),
            snippet.get("publishedAt"),
        ),
        "views": views,
        "likes": likes,
        "comments": comments,
        "reasons": reasons,
    }


def _cached_video_candidates(
    item: dict[str, Any],
    *,
    raw_platform_data: Any = None,
    durable_video_evidence: Iterable[dict[str, Any]] = (),
    limit: int = 24,
) -> list[dict[str, Any]]:
    """Merge durable and raw evidence, deduplicate, score, and bound output."""

    candidates: list[dict[str, Any]] = []
    position_by_key: dict[str, int] = {}

    def _merge_or_add(candidate: dict[str, Any]) -> None:
        key = _first_text(
            candidate.get("video_url"),
            candidate.get("url"),
            candidate.get("post_uid"),
            candidate.get("title"),
        )
        if not key:
            return
        position = position_by_key.get(key)
        if position is None:
            position_by_key[key] = len(candidates)
            candidates.append(candidate)
            return
        current = candidates[position]
        current["candidate_score"] = max(
            _int(current.get("candidate_score")),
            _int(candidate.get("candidate_score")),
        )
        current["reasons"] = list(
            dict.fromkeys([*(current.get("reasons") or []), *(candidate.get("reasons") or [])])
        )
        current["merged_source_kinds"] = list(
            dict.fromkeys(
                [
                    *(current.get("merged_source_kinds") or [current.get("source_kind")]),
                    candidate.get("source_kind"),
                ]
            )
        )
        for metric in ("views", "likes", "comments"):
            current[metric] = max(_int(current.get(metric)), _int(candidate.get(metric)))
        if not _text(current.get("title")) and _text(candidate.get("title")):
            current["title"] = candidate["title"]

    for index, evidence in enumerate(durable_video_evidence):
        if not isinstance(evidence, dict) or evidence.get("is_active") in (False, 0):
            continue
        if _lower(evidence.get("evidence_type") or "video") != "video":
            continue
        candidate = _candidate_from_post(
            evidence,
            item={
                **item,
                "platform": evidence.get("platform") or item.get("platform"),
            },
            index=index,
            source_kind="vkpi_kol_video_evidence",
        )
        if not candidate:
            continue
        _merge_or_add(candidate)

    raw = _loads(
        raw_platform_data
        if raw_platform_data is not None
        else item.get("raw_platform_data"),
        {},
    )
    for index, post in enumerate(_raw_posts(raw)):
        candidate = _candidate_from_post(
            post,
            item=item,
            index=index + len(candidates),
            source_kind="vkpi_kol_pool.raw_platform_data",
        )
        if not candidate:
            continue
        _merge_or_add(candidate)
    profile_candidate = _candidate_from_post(
        {
            "id": item.get("handle"),
            "title": item.get("display_name") or item.get("handle"),
            "url": item.get("profile_url"),
            "views": item.get("avg_views"),
        },
        item=item,
        index=len(candidates) + 1000,
        source_kind="vkpi_kol_pool.profile_fallback",
    )
    if profile_candidate and _is_youtube_url(
        _text(profile_candidate.get("video_url") or profile_candidate.get("url"))
    ):
        _merge_or_add(profile_candidate)
    candidates.sort(key=lambda row: int(row.get("candidate_score") or 0), reverse=True)
    return candidates[: max(1, min(100, int(limit or 24)))]


def _url_readiness(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if not candidate:
        return {
            "valid_video_url": False,
            "provider_path": "unsupported_or_missing_video_url",
            "blocked_reason": "no_cached_video_candidates",
            "risk_flags": ["no_cached_video_candidates"],
        }
    raw_url = _text(candidate.get("video_url") or candidate.get("url"))
    url, video_id = _canonical_youtube_url(raw_url)
    flags: list[str] = ["availability_unknown_no_network_check"]
    if not raw_url:
        flags.append("missing_video_url")
    if raw_url and not video_id:
        flags.append("non_youtube_url")
    if "shorts/" in _lower(raw_url):
        flags.append("likely_short")
    valid = bool(video_id)
    return {
        "valid_video_url": valid,
        "provider_path": (
            "youtube_direct_url_preflight"
            if valid
            else "unsupported_or_missing_video_url"
        ),
        "youtube_video_id": video_id,
        "video_url": url,
        "blocked_reason": (
            "" if valid else ("missing_video_url" if not raw_url else "non_youtube_url")
        ),
        "risk_flags": flags,
    }


__all__ = [
    "DURABLE_EVIDENCE_SCAN_LIMIT",
    "_cached_video_candidates",
    "_canonical_youtube_url",
    "_int",
    "_lower",
    "_url_readiness",
]
