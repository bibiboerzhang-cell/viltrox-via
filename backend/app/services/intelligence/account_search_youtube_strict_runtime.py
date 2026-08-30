"""Strict YouTube video-evidence search orchestration helpers.

The helpers keep search pagination, creator de-duplication, profile metrics and
representative-video evidence distinct.  They do not expand QueryCells or
switch providers.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List


@dataclass(frozen=True)
class StrictSearchPlan:
    search_query: str
    variants: List[str]
    anchors: Dict[str, Any]
    result_limit: int
    page_tokens: Dict[str, str]
    published_after: str
    relevance_language: str
    exact_query: bool
    exhausted_token: str


@dataclass(frozen=True)
class StrictPageResult:
    raw_items: List[Dict[str, Any]]
    used_queries: List[str]
    any_ok: bool
    next_tokens: Dict[str, str]
    term_ledger: List[Dict[str, Any]]
    search_calls: int


@dataclass(frozen=True)
class StrictPageState:
    unvisited: List[str]
    next_tokens: Dict[str, str]
    has_more: bool


@dataclass(frozen=True)
class StrictEnrichment:
    stats_by_channel_id: Dict[str, Dict[str, Any]]
    video_stats_by_id: Dict[str, Dict[str, Any]]
    channels_list_calls: int
    videos_list_calls: int
    channel_status: str
    video_status: str


def build_strict_search_plan(
    search_query: str,
    *,
    safe_limit: int,
    relevance_language: str,
    page_cursor: Any,
    exact_query: bool,
    query_variants: Callable[..., List[str]],
    anchor_index: Callable[..., Dict[str, Any]],
    precision_terms_default: int,
    exhausted_token: str,
) -> StrictSearchPlan | None:
    normalized_exact = " ".join(str(search_query or "").split())
    variants = (
        [normalized_exact]
        if exact_query and normalized_exact
        else query_variants(search_query, max_variants=precision_terms_default)
    )
    if not variants:
        return None
    anchors = (
        {normalized_exact: ("query_cell_exact", normalized_exact)}
        if exact_query and normalized_exact
        else anchor_index(search_query, max_terms=precision_terms_default)
    )
    page_tokens: Dict[str, str] = {}
    if isinstance(page_cursor, dict):
        page_tokens = {
            str(key): str(value).strip()
            for key, value in page_cursor.items()
            if str(value or "").strip()
        }
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=45)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    return StrictSearchPlan(
        search_query=search_query,
        variants=variants,
        anchors=anchors,
        result_limit=max(1, min(50, int(safe_limit or 25))),
        page_tokens=page_tokens,
        published_after=published_after,
        relevance_language=(relevance_language or "en").strip().lower() or "en",
        exact_query=exact_query,
        exhausted_token=exhausted_token,
    )


def _video_identity(raw: Dict[str, Any]) -> tuple[str, str]:
    snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
    return (
        str(snippet.get("channelId") or "").strip(),
        str(((raw.get("id") or {}).get("videoId")) or "").strip(),
    )


def _append_channel_sample(
    root: Dict[str, Any],
    raw: Dict[str, Any],
    video_id: str,
) -> None:
    samples = root.setdefault("_channel_video_samples", [root])
    sample_ids = {
        str(((sample.get("id") or {}).get("videoId")) or "").strip()
        for sample in samples
        if isinstance(sample, dict)
    }
    if video_id not in sample_ids and len(samples) < 3:
        samples.append(raw)


def run_strict_search_pages(
    crawler: Any,
    plan: StrictSearchPlan,
    *,
    ledger_row: Callable[..., Dict[str, Any]],
) -> StrictPageResult:
    """Issue a bounded round of official ``search.list`` page requests."""

    merged: List[Dict[str, Any]] = []
    channel_roots: Dict[str, Dict[str, Any]] = {}
    used_queries: List[str] = []
    next_tokens: Dict[str, str] = {}
    ledger: List[Dict[str, Any]] = []
    any_ok = False
    search_calls = 0
    ordered = sorted(
        plan.variants,
        key=lambda query: 1 if plan.page_tokens.get(query) else 0,
    )
    for query_variant in ordered:
        if plan.page_tokens.get(query_variant) == plan.exhausted_token:
            ledger.append(ledger_row(
                query_variant,
                anchors=plan.anchors,
                quota_units=0,
                exhausted=True,
                skipped="exhausted_previous_round",
            ))
            continue
        payload = crawler._request(
            "search",
            {
                "part": "snippet",
                "type": "video",
                "q": query_variant,
                "publishedAfter": plan.published_after,
                "maxResults": plan.result_limit,
                "relevanceLanguage": plan.relevance_language,
                "safeSearch": "none",
                "pageToken": plan.page_tokens.get(query_variant) or None,
            },
        )
        search_calls += 1
        if (
            crawler._should_use_apify_fallback(payload)
            or str(payload.get("provider_status") or "") == "error"
        ):
            ledger.append(ledger_row(
                query_variant,
                anchors=plan.anchors,
                quota_units=0,
                youtube_search_calls=1,
                provider_status=str(payload.get("provider_status") or "error"),
            ))
            continue
        any_ok = True
        used_queries.append(query_variant)
        next_page = str(payload.get("nextPageToken") or "").strip()
        next_tokens[query_variant] = next_page or plan.exhausted_token
        fresh = 0
        for raw in payload.get("items") or []:
            if not isinstance(raw, dict):
                continue
            channel_id, video_id = _video_identity(raw)
            if not channel_id or not video_id:
                continue
            raw["_discovery_query_variant"] = query_variant
            root = channel_roots.get(channel_id)
            if root is not None:
                _append_channel_sample(root, raw, video_id)
                continue
            if len(merged) >= plan.result_limit:
                continue
            channel_roots[channel_id] = raw
            raw["_channel_video_samples"] = [raw]
            merged.append(raw)
            fresh += 1
        ledger.append(ledger_row(
            query_variant,
            anchors=plan.anchors,
            page_token_in=plan.page_tokens.get(query_variant) or "",
            channels_new=fresh,
            exhausted=not next_page,
        ))
        if len(merged) >= plan.result_limit:
            break
    return StrictPageResult(
        raw_items=merged,
        used_queries=used_queries,
        any_ok=any_ok,
        next_tokens=next_tokens,
        term_ledger=ledger,
        search_calls=search_calls,
    )


def merge_strict_page_state(
    plan: StrictSearchPlan,
    page_result: StrictPageResult,
) -> StrictPageState:
    unvisited = [
        item
        for item in plan.variants
        if item not in page_result.used_queries
        and plan.page_tokens.get(item) != plan.exhausted_token
    ]
    next_tokens = dict(page_result.next_tokens)
    for stale in unvisited:
        if plan.page_tokens.get(stale):
            next_tokens.setdefault(stale, plan.page_tokens[stale])
    for done in plan.variants:
        if plan.page_tokens.get(done) == plan.exhausted_token:
            next_tokens.setdefault(done, plan.exhausted_token)
    live_tokens = {
        key: value
        for key, value in next_tokens.items()
        if value != plan.exhausted_token
    }
    return StrictPageState(
        unvisited=unvisited,
        next_tokens=next_tokens,
        has_more=bool(live_tokens) or bool(unvisited),
    )


async def enrich_strict_search_rows(
    crawler: Any,
    raw_items: List[Dict[str, Any]],
    *,
    channel_statistics: Callable[[Any, List[str]], Dict[str, Dict[str, Any]]],
    sample_video_ids: Callable[[List[Dict[str, Any]]], List[str]],
    video_statistics: Callable[[Any, List[str]], Dict[str, Dict[str, Any]]],
    logger: Any,
) -> StrictEnrichment:
    channel_ids = [_video_identity(raw)[0] for raw in raw_items]
    video_ids = sample_video_ids(raw_items)
    channel_result, video_result = await asyncio.gather(
        asyncio.to_thread(channel_statistics, crawler, channel_ids),
        asyncio.to_thread(video_statistics, crawler, video_ids),
        return_exceptions=True,
    )
    if isinstance(channel_result, BaseException):
        logger.warning(
            "scanner.youtube_strict_channel_stats_failed",
            extra={"error": str(channel_result)},
        )
        stats_by_id: Dict[str, Dict[str, Any]] = {}
        channel_status = "provider_error"
    else:
        stats_by_id = channel_result
        channel_status = "ok"
    if isinstance(video_result, BaseException):
        logger.warning(
            "scanner.youtube_strict_video_stats_failed",
            extra={"error": str(video_result)},
        )
        video_stats_by_id: Dict[str, Dict[str, Any]] = {}
        video_status = "provider_error"
    else:
        video_stats_by_id = video_result
        video_status = "ok"
    return StrictEnrichment(
        stats_by_channel_id=stats_by_id,
        video_stats_by_id=video_stats_by_id,
        channels_list_calls=1 if channel_ids else 0,
        videos_list_calls=1 if video_ids else 0,
        channel_status=channel_status,
        video_status=video_status,
    )


def _strict_item(
    raw: Dict[str, Any],
    enrichment: StrictEnrichment,
    *,
    exact_query: bool,
    activation_summary_fn: Callable[..., Dict[str, Any]],
    normalize_int: Callable[[Any], int],
) -> Dict[str, Any]:
    snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
    channel_id, video_id = _video_identity(raw)
    stats = enrichment.stats_by_channel_id.get(channel_id) or {}
    custom_handle = str(stats.get("custom_url") or "").lstrip("@").strip()
    followers = normalize_int(stats.get("subscribers"))
    activation_summary = (
        activation_summary_fn(
            raw,
            enrichment.video_stats_by_id,
            followers=followers or None,
            query_mode="exact_query_cell" if exact_query else "expanded_ladder",
        )
        if enrichment.video_status == "ok"
        else {
            "activation_evidence_status": "provider_error",
            "claim_status": "descriptive_only",
        }
    )
    representative_video_id = str(
        activation_summary.get("representative_video_id") or video_id
    )
    representative_url = f"https://www.youtube.com/watch?v={representative_video_id}"
    channel_video_count = normalize_int(stats.get("video_count"))
    channel_total_views = normalize_int(stats.get("view_count"))
    lifetime_average = (
        round(channel_total_views / channel_video_count, 6)
        if channel_total_views > 0 and channel_video_count > 0
        else None
    )
    country = str(stats.get("country") or "").strip()
    language = str(
        stats.get("default_language") or snippet.get("defaultAudioLanguage") or ""
    ).strip()
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    thumbnail = ((thumbnails.get("high") or thumbnails.get("default") or {}).get("url") or "")
    item = {
        "platform": "youtube",
        "channel_id": channel_id,
        "channel_name": str(snippet.get("channelTitle") or "Unknown creator").strip(),
        "handle": custom_handle or channel_id,
        "channel_url": f"https://www.youtube.com/channel/{channel_id}",
        "source_url": representative_url,
        "content_url": representative_url,
        "video_id": representative_video_id,
        "sample_title": str(
            activation_summary.get("representative_video_title")
            or snippet.get("title")
            or ""
        )[:300],
        "sample_description": str(
            activation_summary.get("representative_video_description")
            or snippet.get("description")
            or ""
        )[:2000],
        "published": str(
            activation_summary.get("representative_video_published_at")
            or snippet.get("publishedAt")
            or ""
        ).strip(),
        "avatar_url": str(stats.get("avatar_url") or "").strip(),
        "avatar_url_status": str(stats.get("avatar_url_status") or "missing"),
        "thumbnail_url": str(thumbnail).strip(),
        "bio": str(stats.get("description") or "")[:500],
        "provider_actor": "youtube-data-api/search.list:video",
        "discovery_query": str(
            activation_summary.get("representative_discovery_query")
            or raw.get("_discovery_query_variant")
            or ""
        ).strip(),
        **({"followers": followers} if followers > 0 else {}),
        **activation_summary,
    }
    if lifetime_average is not None:
        item.update({
            "channel_lifetime_views": channel_total_views,
            "channel_public_video_count": channel_video_count,
            "channel_lifetime_views_per_public_video": lifetime_average,
            "channel_lifetime_metrics_source": "youtube_data_api.channels.list",
            "channel_lifetime_metrics_scope": "historical_scale_display_only",
        })
    if country:
        item.update({"country": country, "country_source": "platform_profile"})
    if language:
        item.update({"language": language, "language_source": "platform_profile"})
    return item


def normalize_strict_search_rows(
    raw_items: List[Dict[str, Any]],
    enrichment: StrictEnrichment,
    *,
    exact_query: bool,
    activation_summary_fn: Callable[..., Dict[str, Any]],
    normalize_int: Callable[[Any], int],
) -> List[Dict[str, Any]]:
    return [
        _strict_item(
            raw,
            enrichment,
            exact_query=exact_query,
            activation_summary_fn=activation_summary_fn,
            normalize_int=normalize_int,
        )
        for raw in raw_items
    ]


def build_strict_search_result(
    *,
    plan: StrictSearchPlan,
    page_result: StrictPageResult,
    page_state: StrictPageState,
    enrichment: StrictEnrichment,
    items: List[Dict[str, Any]],
    market: str,
    quota_metadata: Callable[..., Dict[str, Any]],
    activation_coverage: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    query_anchor_signals: Callable[[str], Any],
) -> Dict[str, Any]:
    return {
        "status": "done",
        "platform": "youtube",
        "query": (plan.search_query or "").strip(),
        "market": (market or "").strip().upper(),
        "items": items,
        "metadata": {
            "actor_id": "youtube-data-api/search.list:video",
            "provider": "youtube_data_api",
            "strict_video_evidence": True,
            "requested": plan.result_limit,
            "returned": len(items),
            "provider_queries": page_result.used_queries,
            "query_mode": "exact_query_cell" if plan.exact_query else "expanded_ladder",
            "published_after": plan.published_after,
            **quota_metadata(
                search_calls=page_result.search_calls,
                channels_list_calls=enrichment.channels_list_calls,
                videos_list_calls=enrichment.videos_list_calls,
            ),
            "channels_enriched": sum(1 for item in items if item.get("followers")),
            "channel_enrichment_status": enrichment.channel_status,
            "video_enrichment_status": enrichment.video_status,
            **activation_coverage(items),
            "term_ledger": page_result.term_ledger,
            "query_anchor_signals": query_anchor_signals(plan.search_query),
            "pagination_supported": True,
            "next_page_cursor": dict(page_state.next_tokens),
            "has_more": page_state.has_more,
            "page_cursor_in": dict(plan.page_tokens),
        },
    }
