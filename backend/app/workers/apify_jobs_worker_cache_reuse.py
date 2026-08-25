"""Read-only cache lookup used before the worker's paid execution boundary."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.video_analysis_contract import FINAL_V1_DERIVE_METHOD
from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse


def analysis_cache_reuse_decision(
    conn: psycopg.Connection[Any],
    target_type: str,
    target_id: str,
    derive_method: str,
) -> dict[str, Any]:
    """Classify an exact ready row without mutating or replacing paid output."""

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, target_type, target_id, derive_method, model,
                   prompt_version, result, status
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
        row = cur.fetchone()
    if not row:
        return {
            "exists": False,
            "reusable": False,
            "cache_id": None,
            "cache_reuse_status": "missing",
            "revalidation_required": False,
            "claim_status": "descriptive_only",
            "reasons": [],
        }
    if str(derive_method or "").strip() != FINAL_V1_DERIVE_METHOD:
        return {
            "exists": True,
            "reusable": True,
            "cache_id": row.get("id"),
            "cache_reuse_status": "legacy_compatible",
            "revalidation_required": False,
            "claim_status": "descriptive_only",
            "reasons": [],
        }
    return canonical_final_v1_cache_reuse(
        row,
        target_type=target_type,
        target_id=target_id,
        derive_method=derive_method,
    )


__all__ = ["analysis_cache_reuse_decision"]
