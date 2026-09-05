"""Bounded uploads-playlist refresh; dates are evidence, never sort assumptions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def _date(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def _filter_dated_items(
    returned: list[dict[str, Any]], ids: list[str],
    dates: dict[str, datetime | None], cutoff: datetime,
) -> tuple[list[dict[str, Any]], int]:
    returned_ids = {str(item.get("id") or "") for item in returned}
    unknown = len(set(ids) - returned_ids)
    items: list[dict[str, Any]] = []
    for item in returned:
        published = _date((item.get("snippet") or {}).get("publishedAt")) or dates.get(str(item.get("id") or ""))
        if published is None:
            unknown += 1
        elif published >= cutoff:
            items.append(item)
    return items, unknown


def crawl_incremental_uploads(
    request: Callable[..., dict[str, Any]], playlist_id: str, target: int,
    published_after: str, video_details: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    cutoff = _date(published_after)
    if cutoff is None:
        return {"status": "failed", "provider_status": "error", "sync_status": "error",
                "error_code": "invalid_since", "items": []}
    # Preserve a bounded request envelope. A date does not authorize scanning
    # the entire account or changing to paid search/fallback automatically.
    max_pages = max(1, min(20, (target + 49) // 50))
    pages: list[dict[str, Any]] = []
    ids: list[str] = []
    dates: dict[str, datetime | None] = {}
    token = ""
    tokens: set[str] = set()
    failure: dict[str, Any] = {}
    for _ in range(max_pages):
        page = request("playlistItems", {"part": "contentDetails", "playlistId": playlist_id,
                                         "maxResults": 50, "pageToken": token})
        pages.append({"provider_status": page.get("provider_status"),
                      "sync_status": page.get("sync_status"), "error_reason": page.get("error_reason")})
        if page.get("provider_status") != "ok":
            failure = page
            break
        for row in page.get("items") or []:
            details = row.get("contentDetails") or {}
            video_id = str(details.get("videoId") or "")
            published = _date(details.get("videoPublishedAt"))
            if video_id and video_id not in dates:
                dates[video_id] = published
                if published is None or published >= cutoff:
                    ids.append(video_id)
        token = str(page.get("nextPageToken") or "")
        if not token or len(ids) >= target:
            break
        if token in tokens:
            failure = {"error_reason": "repeated_page_token"}
            break
        tokens.add(token)
    raw = {"mode": "uploads_playlist", "since": published_after, "pages": pages,
           "video_count": len(ids), "scan_page_limit": max_pages}
    if failure and not ids:
        return {**failure, "provider": "youtube", "status": "failed", "items": [],
                "search_raw": raw, "metadata": {"exhaustion_proven": False}}
    detail_calls = 0
    def counted_request(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal detail_calls
        detail_calls += 1
        return request(endpoint, params)

    result = video_details(counted_request, ids, raw)
    items, unknown = _filter_dated_items(result.get("items") or [], ids, dates, cutoff)
    incomplete = bool(token or failure or unknown or len(items) > target
                      or result.get("provider_status") not in {"ok", "no_results"})
    result["items"] = items[:target]
    result["search_raw"] = raw
    failed = bool(failure or result.get("provider_status") not in {"ok", "no_results", "partial"})
    result["status"] = "partial" if incomplete and items else "failed" if failed else "partial" if incomplete else "done" if items else "empty"
    if result["status"] == "partial":
        result.update(provider_status="partial", sync_status="partial")
    result["metadata"] = {
        "pagination_supported": True, "has_more": bool(token or len(items) > target),
        "next_page_token": token, "exhaustion_proven": not incomplete,
        "date_filter": "video_publication_time", "date_window_complete": not incomplete,
        "date_unknown_count": unknown, "retry_safe": False,
        "quota_accounting_schema": "youtube_data_api_quota_v2_2026_06_01",
        "youtube_search_calls": 0,
        "youtube_combined_quota_units": 1 + len(pages) + detail_calls,
    }
    if failure:
        result["metadata"]["error_code"] = failure.get("error_reason") or "playlist_read_failed"
    return result
