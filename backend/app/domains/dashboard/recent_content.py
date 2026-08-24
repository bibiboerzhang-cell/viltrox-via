"""Dashboard recent-content and official-summary helpers."""
from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.market import brand_signal_detector
from app.domains import channels


logger = get_logger(__name__)

def _recent_content_sort_key(row: dict[str, Any]) -> float:
    raw = str(
        row.get("posted_at")
        or row.get("published_at")
        or row.get("detected_at")
        or row.get("captured_at")
        or row.get("created_at")
        or ""
    )
    if not raw:
        return 0.0
    try:
        normalized = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0.0


def _dashboard_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _dashboard_official_matrix_summary(limit: int = 20) -> dict[str, Any]:
    try:
        matrix = channels.official_account_matrix_summary(limit=limit)
    except Exception:
        logger.debug("Failed to read official matrix summary", exc_info=True)
        return {}
    return {
        "account_count": _dashboard_int(matrix.get("account_count")),
        "post_count": _dashboard_int(matrix.get("post_count")),
        "total_views": _dashboard_int(matrix.get("total_views")),
        "platform_count": _dashboard_int(matrix.get("platform_count")),
        "source": "official-channel-matrix",
    }


def _dashboard_recent_official_content(limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    try:
        matrix = channels.official_account_matrix(limit=limit)
        for platform in matrix.get("platforms") or []:
            for account in platform.get("accounts") or []:
                for post in account.get("posts") or []:
                    rows.append(
                        {
                            "content_kind": "official",
                            "title": post.get("title") or "官方内容",
                            "url": post.get("url") or post.get("canonical_url") or "",
                            "platform": post.get("platform") or account.get("platform") or platform.get("platform") or "",
                            "account_handle": account.get("handle") or "",
                            "account_display_name": account.get("display_name") or account.get("handle") or "",
                            "posted_at": post.get("posted_at") or post.get("published_at") or "",
                            "views": _dashboard_int(post.get("views") or post.get("total_views") or post.get("play_count")),
                            "likes": _dashboard_int(post.get("likes") or post.get("like_count")),
                            "comments": _dashboard_int(post.get("comments") or post.get("comment_count")),
                            "shares": _dashboard_int(post.get("shares") or post.get("share_count")),
                            "media_type": post.get("content_type") or post.get("media_type") or post.get("media_kind") or "",
                            "thumbnail_url": post.get("thumbnail_url") or post.get("thumbnail") or post.get("media_url") or "",
                            "source_table": "vkpi_employee_channel_metrics",
                            "source_id": post.get("canonical_post_uid") or post.get("provider_post_id") or post.get("id") or post.get("url") or "",
                        }
                    )
    except Exception:
        rows = []

    if rows:
        return sorted(rows, key=_recent_content_sort_key, reverse=True)[:limit]

    from app.db.repositories.viltrox_matrix import get_latest_viltrox_scan_bundle

    bundle = get_latest_viltrox_scan_bundle()
    for post in bundle.get("posts") or []:
        rows.append(
            {
                "content_kind": "official",
                "title": post.get("title") or "官方内容",
                "url": post.get("post_url") or "",
                "platform": post.get("platform") or "",
                "account_handle": post.get("handle") or "",
                "account_display_name": post.get("name") or post.get("handle") or "",
                "posted_at": post.get("published_at") or "",
                "views": int(post.get("views") or 0),
                "likes": int(post.get("likes") or 0),
                "comments": int(post.get("comments") or 0),
                "shares": int(post.get("shares") or 0),
                "media_type": post.get("content_type") or "",
                "source_table": "viltrox_matrix_scan_posts",
                "source_id": post.get("post_url") or f"{post.get('account_id')}:{post.get('published_at')}",
            }
        )
    return sorted(rows, key=_recent_content_sort_key, reverse=True)[:limit]


def _dashboard_recent_ugc_content(limit: int) -> list[dict[str, Any]]:
    payload = brand_signal_detector.list_brand_signals(status="new", limit=limit)
    rows: list[dict[str, Any]] = []
    for signal in payload.get("signals") or []:
        source_url = str(signal.get("source_url") or signal.get("post_url") or "").strip()
        if not source_url:
            continue
        rows.append(
            {
                "content_kind": "ugc",
                "title": signal.get("title") or signal.get("signal_type") or signal.get("match_context") or "品牌提及",
                "url": source_url,
                "platform": signal.get("platform") or signal.get("source_platform") or "",
                "account_handle": signal.get("author_handle") or signal.get("handle") or signal.get("kol_entity_uid") or "",
                "posted_at": signal.get("published_at") or signal.get("detected_at") or "",
                "views": int(signal.get("views") or signal.get("view_count") or signal.get("impressions") or 0),
                "likes": int(signal.get("likes") or signal.get("like_count") or 0),
                "comments": int(signal.get("comments") or signal.get("comment_count") or 0),
                "shares": int(signal.get("shares") or signal.get("share_count") or 0),
                "media_type": signal.get("content_type") or "",
                "source_table": "vkpi_brand_signal",
                "source_id": signal.get("id"),
            }
        )
    return sorted(rows, key=_recent_content_sort_key, reverse=True)[:limit]


def build_dashboard_recent_content(limit: int) -> dict[str, Any]:
    official = _dashboard_recent_official_content(limit=limit)
    ugc = _dashboard_recent_ugc_content(limit=limit)
    items = sorted([*official, *ugc], key=_recent_content_sort_key, reverse=True)[:limit]
    counts: dict[str, int] = {}
    for item in items:
        kind = str(item.get("content_kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "items": items,
        "count": len(items),
        "kind_counts": counts,
        "sources": ["viltrox_matrix_scan_posts", "vkpi_brand_signal"],
        "is_real": True,
    }
