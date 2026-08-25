"""Narrow, read-only evidence projections for GTM SKU aggregate cards.

The public SKU performance reader needs content URLs, dates and deep-analysis
scores for its item list.  The GTM summary only publishes ``_aggregate``
metrics, so carrying those columns through its bounded 2k/8k evidence scans is
avoidable work.  Keep the original ordering and limits here: they are part of
the aggregate truth contract.
"""
from __future__ import annotations

from typing import Any


def build_aggregate_item(row: dict[str, Any]) -> dict[str, Any]:
    """Build only fields consumed by sku_performance._aggregate."""

    from app.domains.products import sku_performance

    likes = sku_performance._int_or_none(row.get("like_count"))
    comments = sku_performance._int_or_none(row.get("comment_count"))
    engagement = (
        (likes or 0) + (comments or 0)
        if likes is not None or comments is not None
        else None
    )
    return {
        "view_count": sku_performance._int_or_none(row.get("view_count")),
        "like_count": likes,
        "comment_count": comments,
        "engagement": engagement,
        "kol": {
            "kol_pool_id": sku_performance._int_or_none(row.get("kol_pool_id")),
            "handle": sku_performance._text(row.get("handle"), 120),
            "display_name": sku_performance._text(row.get("display_name"), 160),
            "platform": sku_performance._text(row.get("kol_platform"), 40),
        },
    }


def deep_haystacks(row: dict[str, Any]) -> dict[str, str]:
    """Prepare the five deep-match fields once for every SKU in one GTM batch."""

    from app.domains.products import sku_performance

    products_text, presence_text = sku_performance._presence_texts(row.get("product_presence"))
    return {
        "final_v1_products": products_text,
        "final_v1_presence": presence_text,
        "evidence_title": sku_performance._text(row.get("title"), 400),
        "final_v1_brand_exposure": sku_performance._text(row.get("brand_exposure"), 2000),
        "final_v1_summary": sku_performance._text(row.get("content_summary"), 2000),
    }


def match_alias(matcher: Any, text_norm: str) -> dict[str, Any] | None:
    """Equivalent token-boundary match without one regex search per alias."""

    if not text_norm:
        return None
    text_length = len(text_norm)
    for item, _pattern in matcher._compiled:
        needle = str(item.get("alias_norm") or "")
        start = 0
        while needle:
            index = text_norm.find(needle, start)
            if index < 0:
                break
            end = index + len(needle)
            left_ok = index == 0 or text_norm[index - 1] not in "abcdefghijklmnopqrstuvwxyz0123456789"
            right_ok = end == text_length or text_norm[end] not in "abcdefghijklmnopqrstuvwxyz0123456789"
            if left_ok and right_ok:
                return item
            start = index + 1
    return None


def match_haystacks(
    haystacks: dict[str, str],
    matcher: Any,
    normalized: dict[str, str],
) -> tuple[dict[str, Any], str] | None:
    """Preserve field priority while sharing row-local lazy normalization."""

    from app.domains.products import sku_performance

    for field in sku_performance._MATCH_FIELD_PRIORITY:
        hay = haystacks.get(field) or ""
        if not hay:
            continue
        if field not in normalized:
            normalized[field] = sku_performance._norm(hay)
        matched = match_alias(matcher, normalized[field])
        if matched:
            return matched, field
    return None


def load_deep_rows(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            e.id AS evidence_id,
            e.kol_pool_id,
            COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), e.content_url) AS title,
            e.view_count,
            e.like_count,
            e.comment_count,
            p.handle,
            p.display_name,
            p.platform AS kol_platform,
            ac.result #>> '{layer1_visual_content,product_presence}' AS product_presence,
            ac.result #>> '{layer1_visual_content,brand_exposure}' AS brand_exposure,
            ac.result #>> '{layer1_visual_content,content_summary}' AS content_summary
        FROM vkpi_analysis_cache ac
        JOIN vkpi_kol_video_evidence e ON e.id::text = ac.target_id
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE ac.target_type = 'video'
          AND ac.derive_method = 'video_analysis_final_v1'
          AND ac.status = 'ready'
        ORDER BY ac.id DESC
        LIMIT 2000
        """,
    ).fetchall()
    return [dict(row) for row in rows]


def load_title_rows(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            e.id AS evidence_id,
            e.kol_pool_id,
            COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, '')) AS title,
            e.view_count,
            e.like_count,
            e.comment_count,
            p.handle,
            p.display_name,
            p.platform AS kol_platform
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.is_active = TRUE
          AND (NULLIF(e.title, '') IS NOT NULL OR NULLIF(e.video_title, '') IS NOT NULL)
        ORDER BY COALESCE(e.view_count, 0) DESC, e.id DESC
        LIMIT 8000
        """,
    ).fetchall()
    return [dict(row) for row in rows]
