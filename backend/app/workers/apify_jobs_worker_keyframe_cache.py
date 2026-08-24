"""Source-fenced existing-cache probe for keyframe-only video QA jobs."""
from __future__ import annotations

import re
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.domains.kol.video_keyframe_qa_cache import (
    KEYFRAME_QA_DERIVE_METHOD,
    final_v1_payload_from_cache_result,
    final_v1_payload_sha256,
    qa_cache_matches_source,
)


def keyframe_qa_cache_exists_for_source(
    conn: psycopg.Connection[Any],
    *,
    target_type: str,
    target_id: str,
    derive_method: str,
    payload: dict[str, Any],
) -> bool:
    """Return true only when the ready QA cache reviews this job's exact source."""
    if target_type != "video" or derive_method != KEYFRAME_QA_DERIVE_METHOD:
        return False
    try:
        evidence_id = int(target_id)
        expected_cache_id = int(payload.get("source_final_v1_cache_id") or 0)
    except (TypeError, ValueError):
        return False
    expected_sha = str(payload.get("source_final_v1_sha256") or "").strip().lower()
    if evidence_id <= 0 or expected_cache_id <= 0 or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
        return False
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            WITH latest_source AS (
                SELECT id, result
                FROM vkpi_analysis_cache
                WHERE target_type='video' AND target_id=%s
                  AND derive_method='video_analysis_final_v1' AND status='ready'
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            ), latest_qa AS (
                SELECT result
                FROM vkpi_analysis_cache
                WHERE target_type='video' AND target_id=%s
                  AND derive_method=%s AND status='ready'
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            )
            SELECT latest_source.id AS source_cache_id,
                   latest_source.result AS source_result,
                   latest_qa.result AS qa_result
            FROM latest_source CROSS JOIN latest_qa
            """,
            (str(evidence_id), str(evidence_id), KEYFRAME_QA_DERIVE_METHOD),
        )
        row = cur.fetchone()
    if not row:
        return False
    item = dict(row)
    if int(item.get("source_cache_id") or 0) != expected_cache_id:
        return False
    source_payload = final_v1_payload_from_cache_result(item.get("source_result"))
    if not source_payload or final_v1_payload_sha256(source_payload) != expected_sha:
        return False
    return qa_cache_matches_source(
        item.get("qa_result"),
        evidence_id=evidence_id,
        source_cache_id=expected_cache_id,
        source_payload_sha256=expected_sha,
    )


__all__ = ["keyframe_qa_cache_exists_for_source"]
