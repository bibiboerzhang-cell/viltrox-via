"""Bounded YouTube metadata enrichers for account discovery.

The helpers in this module never broaden a search.  They only enrich IDs that
``search.list`` already returned, in batches supported by the YouTube Data API.
Missing public counters remain absent instead of being converted to zero.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping

from app.services.intelligence.account_scan_helpers import (
    _avatar_url_policy,
    _normalize_int,
)


_ISO_DURATION_RE = re.compile(
    r"^P(?:\d+D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
MAX_ACTIVATION_VIDEOS_PER_CHANNEL = 3


def _unique_ids(values: Iterable[Any], *, limit: int = 50) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item_id = str(value or "").strip()
        if item_id and item_id not in seen:
            seen.add(item_id)
            output.append(item_id)
        if len(output) >= limit:
            break
    return output


def _optional_int(value: Any) -> int | None:
    """Parse an observed public counter; missing is not the same as zero."""

    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _duration_seconds(value: Any) -> int | float | None:
    text = str(value or "").strip()
    match = _ISO_DURATION_RE.fullmatch(text)
    if not match:
        return None
    seconds = (
        int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + float(match.group("seconds") or 0)
    )
    return int(seconds) if seconds.is_integer() else round(seconds, 3)


def _youtube_channel_statistics(
    crawler: Any,
    channel_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """``channels.list`` public profile/statistics enrichment (50 IDs/call)."""

    ids = _unique_ids(channel_ids, limit=max(50, len(channel_ids or [])))
    if not ids or not getattr(crawler, "api_key", ""):
        return {}
    output: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        payload = crawler._request(
            "channels",
            {"part": "snippet,statistics", "id": ",".join(batch), "maxResults": 50},
        )
        if str(payload.get("provider_status") or "") != "ok":
            raise RuntimeError("youtube channels.list enrichment failed")
        for row in payload.get("items") or []:
            if not isinstance(row, dict):
                continue
            channel_id = str(row.get("id") or "").strip()
            if not channel_id:
                continue
            snippet = row.get("snippet") if isinstance(row.get("snippet"), dict) else {}
            stats = row.get("statistics") if isinstance(row.get("statistics"), dict) else {}
            thumbs = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
            avatar_url, avatar_status = _avatar_url_policy(
                ((thumbs.get("high") or thumbs.get("medium") or thumbs.get("default")) or {}).get("url")
            )
            hidden = bool(stats.get("hiddenSubscriberCount"))
            output[channel_id] = {
                "subscribers": 0 if hidden else _normalize_int(stats.get("subscriberCount")),
                "hidden_subscribers": hidden,
                "video_count": _normalize_int(stats.get("videoCount")),
                "view_count": _normalize_int(stats.get("viewCount")),
                "custom_url": str(snippet.get("customUrl") or "").strip(),
                "country": str(snippet.get("country") or "").strip(),
                "description": str(snippet.get("description") or "").strip(),
                "default_language": str(snippet.get("defaultLanguage") or "").strip(),
                "avatar_url": avatar_url,
                "avatar_url_status": avatar_status,
            }
    return output


def youtube_video_statistics(
    crawler: Any,
    video_ids: Iterable[Any],
) -> Dict[str, Dict[str, Any]]:
    """Fetch at most one ``videos.list`` batch for already-found video IDs.

    The returned fields are explicitly representative-video observations.  A
    single video's counters are never renamed to ``avg_*``. Provider failure
    raises so callers can distinguish an unavailable source from missing public
    counters without widening the original query.
    """

    ids = _unique_ids(video_ids, limit=50)
    if not ids or not getattr(crawler, "api_key", ""):
        return {}
    payload = crawler._request(
        "videos",
        {
            "part": "statistics,contentDetails",
            "id": ",".join(ids),
        },
    )
    if str(payload.get("provider_status") or "") != "ok":
        raise RuntimeError("youtube videos.list enrichment failed")
    output: Dict[str, Dict[str, Any]] = {}
    for row in payload.get("items") or []:
        if not isinstance(row, dict):
            continue
        video_id = str(row.get("id") or "").strip()
        if not video_id:
            continue
        statistics = row.get("statistics") if isinstance(row.get("statistics"), dict) else {}
        details = row.get("contentDetails") if isinstance(row.get("contentDetails"), dict) else {}
        duration = str(details.get("duration") or "").strip()
        observed = {
            "representative_video_views": _optional_int(statistics.get("viewCount")),
            "representative_video_likes": _optional_int(statistics.get("likeCount")),
            "representative_video_comments": _optional_int(statistics.get("commentCount")),
            "representative_video_duration": duration or None,
            "representative_video_duration_seconds": _duration_seconds(duration),
        }
        output[video_id] = {key: value for key, value in observed.items() if value is not None}
    return output


def youtube_sample_video_ids(
    raw_items: Iterable[Mapping[str, Any]],
    *,
    limit: int = 50,
) -> list[str]:
    """Flatten bounded per-channel search hits into one ``videos.list`` batch."""

    per_channel: list[list[str]] = []
    for raw in raw_items:
        samples = raw.get("_channel_video_samples")
        if not isinstance(samples, list):
            samples = [raw]
        channel_ids: list[str] = []
        for sample in samples:
            if not isinstance(sample, Mapping):
                continue
            raw_id = sample.get("id") if isinstance(sample.get("id"), Mapping) else {}
            video_id = str(raw_id.get("videoId") or "").strip()
            if video_id and video_id not in channel_ids:
                channel_ids.append(video_id)
            if len(channel_ids) >= MAX_ACTIVATION_VIDEOS_PER_CHANNEL:
                break
        if channel_ids:
            per_channel.append(channel_ids)
    cap = max(1, min(50, int(limit or 50)))
    values = [channel[0] for channel in per_channel][:cap]
    for channel in per_channel:
        for video_id in channel[1:]:
            if len(values) >= cap:
                return _unique_ids(values, limit=cap)
            values.append(video_id)
    return _unique_ids(values, limit=cap)


def _mean_observed(rows: Iterable[Mapping[str, Any]], field: str) -> float | None:
    values = [
        float(value)
        for row in rows
        if (value := _optional_int(row.get(field))) is not None
    ]
    return round(sum(values) / len(values), 6) if values else None


def _observed_ints(rows: Iterable[Mapping[str, Any]], field: str) -> list[int]:
    return [
        value
        for row in rows
        if (value := _optional_int(row.get(field))) is not None
    ]


def youtube_channel_activation_summary(
    raw: Mapping[str, Any],
    video_stats_by_id: Mapping[str, Mapping[str, Any]],
    *,
    followers: int | None = None,
    query_mode: str = "exact_query_cell",
) -> dict[str, Any]:
    """Build a no-extra-call 1–3 video evidence bundle for one channel.

    Repeated videos from the original exact-query page are retained as recent
    content evidence instead of being thrown away during channel dedupe.  A
    single sample keeps the historical representative-only contract.  Only a
    multi-sample bundle receives aggregate ``avg_*`` fields, and strict market
    activation still requires at least three observations downstream.
    """

    samples = raw.get("_channel_video_samples")
    if not isinstance(samples, list):
        samples = [raw]
    observed: list[dict[str, Any]] = []
    public_samples: list[dict[str, Any]] = []
    seen_video_ids: set[str] = set()
    for sample in samples:
        if len(public_samples) >= MAX_ACTIVATION_VIDEOS_PER_CHANNEL:
            break
        if not isinstance(sample, Mapping):
            continue
        raw_id = sample.get("id") if isinstance(sample.get("id"), Mapping) else {}
        video_id = str(raw_id.get("videoId") or "").strip()
        if not video_id or video_id in seen_video_ids or video_id not in video_stats_by_id:
            continue
        seen_video_ids.add(video_id)
        snippet = sample.get("snippet") if isinstance(sample.get("snippet"), Mapping) else {}
        metrics = dict(video_stats_by_id.get(video_id) or {})
        observed.append(metrics)
        public_samples.append({
            "video_id": video_id,
            "title": str(snippet.get("title") or "")[:500],
            "description": str(snippet.get("description") or "")[:2_000],
            "published": str(snippet.get("publishedAt") or "")[:80],
            "content_url": f"https://www.youtube.com/watch?v={video_id}",
            "discovery_query": str(sample.get("_discovery_query_variant") or "")[:500],
            **metrics,
        })
    if not observed:
        return {"activation_evidence_status": "pending_content_evidence"}

    first = public_samples[0]
    output: dict[str, Any] = {
        key: first[key]
        for key in (
            "representative_video_views",
            "representative_video_likes",
            "representative_video_comments",
            "representative_video_duration",
            "representative_video_duration_seconds",
        )
        if first.get(key) is not None
    }
    sample_count = len(observed)
    view_values = _observed_ints(observed, "representative_video_views")
    like_values = _observed_ints(observed, "representative_video_likes")
    comment_values = _observed_ints(observed, "representative_video_comments")
    engagement_rows = [
        (views, likes, comments)
        for row in observed
        if (views := _optional_int(row.get("representative_video_views"))) is not None
        and views > 0
        and (likes := _optional_int(row.get("representative_video_likes"))) is not None
        and (comments := _optional_int(row.get("representative_video_comments"))) is not None
    ]
    metric_sample_counts = {
        "avg_views": len(view_values),
        "engagement": len(engagement_rows),
        "views_per_follower": len(view_values) if followers is not None and followers > 0 else 0,
        "comments_per_follower": (
            len(comment_values) if followers is not None and followers > 0 else 0
        ),
    }
    scope_prefix = "exact_query" if query_mode == "exact_query_cell" else "expanded_query"
    metrics_scope = (
        f"{scope_prefix}_hit_45d"
        if sample_count == 1
        else f"{scope_prefix}_hits_45d_provisional"
        if sample_count < MAX_ACTIVATION_VIDEOS_PER_CHANNEL
        else f"{scope_prefix}_hits_45d_aggregate"
    )
    output.update({
        "representative_video_id": first.get("video_id"),
        "representative_video_title": first.get("title"),
        "representative_video_description": first.get("description"),
        "representative_video_url": first.get("content_url"),
        "representative_discovery_query": first.get("discovery_query"),
        "representative_video_published_at": first.get("published") or None,
        "recent_videos": public_samples,
        "recent_video_sample_count": sample_count,
        "activation_sample_count": sample_count,
        "activation_metric_sample_counts": metric_sample_counts,
        "activation_metrics_source": "youtube_data_api.videos.list",
        "activation_query_mode": query_mode,
        "activation_metrics_scope": metrics_scope,
        "claim_status": "descriptive_only",
        "activation_evidence_status": (
            "observed_single_sample"
            if sample_count == 1
            else "observed_insufficient_sample"
            if sample_count < MAX_ACTIVATION_VIDEOS_PER_CHANNEL
            else "observed_multi_sample"
        ),
    })
    if sample_count < 2:
        return output

    for source, destination in (
        ("representative_video_views", "avg_views"),
        ("representative_video_likes", "avg_likes"),
        ("representative_video_comments", "avg_comments"),
    ):
        value = _mean_observed(observed, source)
        if value is not None:
            output[destination] = value
    avg_views = output.get("avg_views")
    avg_likes = output.get("avg_likes")
    avg_comments = output.get("avg_comments")
    if engagement_rows:
        total_views = sum(row[0] for row in engagement_rows)
        total_interactions = sum(row[1] + row[2] for row in engagement_rows)
        output["engagement_rate"] = round(total_interactions / total_views, 8)
    if followers is not None and followers > 0:
        if avg_views is not None:
            output["views_per_follower"] = round(avg_views / followers, 8)
        if avg_comments is not None:
            output["comments_per_follower"] = round(avg_comments / followers, 8)
    output["avg_views_source"] = "youtube_data_api.videos.list"
    output["avg_views_scope"] = output["activation_metrics_scope"]
    return output


def youtube_activation_coverage(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(items)
    counts = [int(row.get("activation_sample_count") or 0) for row in rows]
    return {
        "activation_sampling_policy": "one_per_channel_then_top_ranked_completion_to_3_batch_cap_50",
        "activation_samples_observed": sum(counts),
        "activation_multi_sample_candidates": sum(count >= 3 for count in counts),
        "activation_insufficient_sample_candidates": sum(0 < count < 3 for count in counts),
        "activation_pending_candidates": sum(count == 0 for count in counts),
    }


def youtube_quota_metadata(
    *,
    search_calls: int,
    channels_list_calls: int,
    videos_list_calls: int = 0,
) -> dict[str, Any]:
    """Return v2 usage without adding the independent quota buckets."""

    search = max(0, int(search_calls))
    combined = max(0, int(channels_list_calls)) + max(0, int(videos_list_calls))
    return {
        "quota_accounting_schema": "youtube_data_api_quota_v2_2026_06_01",
        "youtube_search_calls": search,
        "youtube_combined_quota_units": combined,
        "youtube_api_calls": search + combined,
        "quota_units": combined,
        "quota_units_deprecated": True,
        "quota_units_deprecated_alias_of": "youtube_combined_quota_units",
    }


def youtube_exact_query_failure(
    *, query: str, market: str, actor_id: str, search_calls: int,
) -> dict[str, Any]:
    """Return an accounted exact-query failure without authorizing fallback."""

    return {
        "status": "provider_error",
        "platform": "youtube",
        "query": str(query or "").strip(),
        "market": str(market or "").strip().upper(),
        "items": [],
        "message": "YouTube exact query failed; paid fallback is disabled",
        "metadata": {
            "actor_id": actor_id,
            "provider": "youtube_data_api",
            "query_mode": "exact_query_cell",
            "fallback_policy": "disabled_unforecast_provider_switch",
            **youtube_quota_metadata(
                search_calls=search_calls,
                channels_list_calls=0,
            ),
        },
    }


__all__ = [
    "MAX_ACTIVATION_VIDEOS_PER_CHANNEL",
    "_youtube_channel_statistics",
    "youtube_activation_coverage",
    "youtube_channel_activation_summary",
    "youtube_exact_query_failure",
    "youtube_quota_metadata",
    "youtube_sample_video_ids",
    "youtube_video_statistics",
]
