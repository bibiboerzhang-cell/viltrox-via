"""Shared KOL platform-search workflow for HTTP and durable workers."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

from app.core.logging import get_logger


logger = get_logger(__name__)


def _provider_unavailable(reason: str, operation: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "status": "unavailable",
            "reason": reason,
            "operation": operation,
            "retryable": True,
        },
    )


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _resolve_persistence(
    persist_result: Callable[..., list[int]] | None,
    persist_candidates: Callable[[list[dict], dict, str, str], list[int]] | None,
    log_activity: Callable[..., None] | None,
) -> tuple[
    Callable[..., list[int]] | None,
    Callable[[list[dict], dict, str, str], list[int]] | None,
    Callable[..., None] | None,
]:
    if persist_result is None and persist_candidates is None and log_activity is None:
        from app.services.kol.ops_persistence import _persist_platform_search_result

        return _persist_platform_search_result, None, None
    if persist_result is None and (
        persist_candidates is None or log_activity is None
    ):
        from app.services.kol.ops_persistence import (
            _log_activity_commit,
            _persist_search_candidates,
        )

        persist_candidates = persist_candidates or _persist_search_candidates
        log_activity = log_activity or _log_activity_commit
    return persist_result, persist_candidates, log_activity


async def execute_platform_search(
    body: dict,
    *,
    staff: dict,
    search_content: Callable[..., Awaitable[dict]] | None = None,
    annotate_items: Callable[..., list[dict]] | None = None,
    db_write_fn: Callable[[Callable[[], Any]], Awaitable[Any]] | None = None,
    persist_result: Callable[..., list[int]] | None = None,
    persist_candidates: Callable[[list[dict], dict, str, str], list[int]] | None = None,
    log_activity: Callable[..., None] | None = None,
    country_in_excluded_region: Callable[..., bool] | None = None,
    unavailable_error: Callable[[str, str], Exception] | None = None,
) -> dict:
    """Execute one paid platform search without depending on the API layer.

    Optional collaborators keep the legacy router monkeypatch surface intact;
    durable workers use the same implementation with production defaults.
    """
    if search_content is None:
        from app.services.intelligence.account_scan_service import (
            search_platform_content,
        )

        search_content = search_platform_content
    if annotate_items is None:
        from app.domains.kol.history_match import annotate_platform_items

        annotate_items = annotate_platform_items
    if db_write_fn is None:
        from app.db.connection import db_write

        db_write_fn = db_write
    persist_result, persist_candidates, log_activity = _resolve_persistence(
        persist_result,
        persist_candidates,
        log_activity,
    )
    if country_in_excluded_region is None:
        from app.domains.kol.profile_discovery import _country_in_excluded_region

        country_in_excluded_region = _country_in_excluded_region
    unavailable_error = unavailable_error or _provider_unavailable

    query = str(body.get("query") or "").strip()
    platform = str(body.get("platform") or "youtube").strip().lower()
    market = str(body.get("market") or "").strip().upper()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    if platform == "douyin":
        return {
            "status": "unsupported_platform",
            "items": [],
            "candidate_ids": [],
            "saved_candidates": 0,
            "message": "douyin is not a supported discovery platform",
            "platform": platform,
        }

    exclude_region = bool(body.get("exclude_chinese", True))
    if exclude_region and country_in_excluded_region(market):
        return {
            "status": "excluded_region",
            "items": [],
            "candidate_ids": [],
            "saved_candidates": 0,
            "message": "market is in excluded region {CN/HK/TW}; no candidates persisted",
            "market": market,
        }

    try:
        result = await search_content(
            platform,
            query,
            market=market,
            max_results=_int(body.get("max_results"), 25),
        )
    except Exception as exc:  # noqa: BLE001 - stable retryable provider state
        logger.warning(
            "kol platform search provider failed platform=%s exception_type=%s",
            platform,
            type(exc).__name__,
        )
        raise unavailable_error(
            "platform_search_unavailable",
            "platform_search",
        ) from exc

    result_items = [dict(item or {}) for item in (result.get("items", []) or [])]
    enriched_items = await db_write_fn(
        lambda: annotate_items(result_items, platform=platform)
    )
    if exclude_region:
        enriched_items = [
            item
            for item in enriched_items
            if not country_in_excluded_region(
                market,
                item.get("country"),
                item.get("region"),
            )
        ]
    result["items"] = enriched_items
    api_provider = str(result.get("provider") or result.get("source") or platform)
    if persist_result is not None:
        candidate_ids = await db_write_fn(
            lambda: persist_result(
                enriched_items,
                body,
                platform,
                market,
                staff=staff,
                query=query,
                api_provider=api_provider,
            )
        )
    else:
        candidate_ids = await db_write_fn(
            lambda: persist_candidates(enriched_items, body, platform, market)
        )
        await db_write_fn(
            lambda: log_activity(
                staff,
                "platform_search",
                query=query,
                platform=platform,
                market=market,
                api_provider=api_provider,
                api_calls=1,
                result_count=len(enriched_items),
                metadata={
                    "saved_candidates": len(candidate_ids),
                    "history_matches": sum(
                        1 for item in enriched_items if item.get("historical_match")
                    ),
                    "niche": body.get("niche", ""),
                },
            )
        )
    return {
        **result,
        "candidate_ids": candidate_ids,
        "saved_candidates": len(candidate_ids),
    }
