"""Narrow, read-only evidence projections for GTM SKU aggregate cards.

The public SKU performance reader needs content URLs, dates and deep-analysis
scores for its item list.  The GTM summary only publishes ``_aggregate``
metrics, so carrying those columns through its bounded 2k/8k evidence scans is
avoidable work.  Keep the original ordering and limits here: they are part of
the aggregate truth contract.
"""
from __future__ import annotations

from typing import Any


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
