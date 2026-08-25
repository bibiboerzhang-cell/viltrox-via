"""Display projection for final_v1 cache with canonical reuse labelling."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse
from app.workers.apify_jobs_worker_helpers import (
    _derive_method,
    _int_or_none,
    _loads,
    _target,
)
from app.workers.apify_jobs_worker_session_analysis import (
    _search_session_analysis_summary_from_result,
)


def search_session_analysis_summary_from_ready_cache(
    conn: psycopg.Connection[Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    target_type, target_id = _target(payload)
    derive_method = _derive_method(payload)
    if derive_method != "video_analysis_final_v1" or target_type != "video" or not target_id:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, target_type, target_id, derive_method, model,
                   prompt_version, result, cost, status
            FROM vkpi_analysis_cache
            WHERE target_type=%s
              AND target_id=%s
              AND derive_method=%s
              AND status='ready'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (target_type, target_id, derive_method),
        )
        cache = cur.fetchone()
        cur.execute(
            """
            SELECT id, kol_pool_id, content_url, title, video_title
            FROM vkpi_kol_video_evidence
            WHERE id=%s
            LIMIT 1
            """,
            (_int_or_none(target_id),),
        )
        evidence = cur.fetchone() or {}
    if not cache:
        return None
    result = (
        cache.get("result")
        if isinstance(cache.get("result"), dict)
        else _loads(cache.get("result"), {})
    )
    summary = _search_session_analysis_summary_from_result(
        cache_id=_int_or_none(cache.get("id")),
        derive_method=derive_method,
        target_type=target_type,
        target_id=target_id,
        evidence=dict(evidence),
        result=result if isinstance(result, dict) else {},
        cost=float(cache.get("cost") or 0.0),
    )
    reuse = canonical_final_v1_cache_reuse(
        cache,
        target_type=target_type,
        target_id=target_id,
        derive_method=derive_method,
    )
    if reuse.get("reusable") is not True:
        summary.update(
            {
                "status": "legacy_unverified",
                "cache_reuse_status": "legacy_unverified",
                "revalidation_required": True,
                "evaluation_only": False,
                "production_authorized": False,
                "claim_status": "descriptive_only",
                "model_readiness_status": "legacy_cache_unverified",
            }
        )
    return summary


__all__ = ["search_session_analysis_summary_from_ready_cache"]
