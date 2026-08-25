"""Terminal cache-skip handling with explicit canonical/legacy separation."""

from __future__ import annotations

from typing import Any, Mapping

import psycopg
from psycopg.rows import dict_row


def finish_skipped_impl(
    conn: psycopg.Connection[Any],
    job_id: int,
    reason: str,
    *,
    evaluation_only: bool,
    namespace: Mapping[str, Any],
) -> None:
    LOCAL_EVALUATION_CACHE_DERIVE_METHOD = namespace[
        "LOCAL_EVALUATION_CACHE_DERIVE_METHOD"
    ]
    _derive_method = namespace["_derive_method"]
    _enqueue_account_dossier_extract_after_final_v1 = namespace[
        "_enqueue_account_dossier_extract_after_final_v1"
    ]
    _enqueue_comments_collect_after_final_v1 = namespace[
        "_enqueue_comments_collect_after_final_v1"
    ]
    _enqueue_content_fit_after_final_v1 = namespace[
        "_enqueue_content_fit_after_final_v1"
    ]
    _int_or_none = namespace["_int_or_none"]
    _loads = namespace["_loads"]
    _search_session_analysis_summary_from_ready_cache = namespace[
        "_search_session_analysis_summary_from_ready_cache"
    ]
    _sync_deep_analysis_result_from_cache = namespace[
        "_sync_deep_analysis_result_from_cache"
    ]
    _sync_search_session_job = namespace["_sync_search_session_job"]
    logger = namespace["logger"]

    reason_text = str(reason or "")
    legacy_unverified = "skipped_legacy_cache_unverified" in reason_text
    analysis_summary: dict[str, Any] | None = (
        {
            "evaluation_only": True,
            "production_authorized": False,
            "claim_status": "descriptive_only",
            "model_readiness_status": "evaluation_only_not_production_ready",
            "cache_derive_method": LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
        }
        if evaluation_only
        else None
    )
    payload: dict[str, Any] = {}
    if not evaluation_only and (
        "skipped_existing_analysis_cache" in reason_text or legacy_unverified
    ):
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT payload FROM apify_jobs WHERE id=%s LIMIT 1",
                    (int(job_id),),
                )
                row = cur.fetchone() or {}
            loaded = (
                row.get("payload")
                if isinstance(row.get("payload"), dict)
                else _loads(row.get("payload"), {})
            )
            payload = loaded if isinstance(loaded, dict) else {}
            analysis_summary = _search_session_analysis_summary_from_ready_cache(
                conn, payload
            )
            if legacy_unverified:
                analysis_summary = {
                    **(analysis_summary or {}),
                    "status": "legacy_unverified",
                    "cache_reuse_status": "legacy_unverified",
                    "revalidation_required": True,
                    "evaluation_only": False,
                    "production_authorized": False,
                    "claim_status": "descriptive_only",
                    "model_readiness_status": "legacy_cache_unverified",
                }
        except Exception as exc:
            logger.warning(
                "skipped cache summary load failed | job_id=%s exception_type=%s",
                job_id,
                type(exc).__name__,
            )

    # Only canonical cache reuse may project deep data or fan out paid/followup
    # work. Legacy output remains display-only until a future explicit,
    # separately authorized revalidation succeeds.
    if (
        not evaluation_only
        and not legacy_unverified
        and "skipped_existing_analysis_cache" in reason_text
    ):
        try:
            cache_id = _int_or_none((analysis_summary or {}).get("cache_id"))
            deep_result = _sync_deep_analysis_result_from_cache(
                conn,
                cache_id=cache_id,
                derive_method=_derive_method(payload),
                job_id=int(job_id),
            )
            account_extract_job = _enqueue_account_dossier_extract_after_final_v1(
                conn,
                job_id=int(job_id),
                deep_result=deep_result,
            )
            content_fit_job = _enqueue_content_fit_after_final_v1(
                conn,
                job_id=int(job_id),
                deep_result=deep_result,
                source_payload=payload,
            )
            if analysis_summary and content_fit_job:
                analysis_summary["content_fit_job"] = content_fit_job
            comments_collect_job = _enqueue_comments_collect_after_final_v1(
                conn,
                job_id=int(job_id),
                deep_result=deep_result,
            )
            if analysis_summary and comments_collect_job:
                analysis_summary["comments_collect_job"] = comments_collect_job
            if analysis_summary and deep_result:
                analysis_summary["deep_result"] = deep_result
            if analysis_summary and account_extract_job:
                analysis_summary["account_dossier_extract_job"] = account_extract_job
        except Exception as exc:
            logger.warning(
                "skipped cache deep/account sync failed | job_id=%s exception_type=%s",
                job_id,
                type(exc).__name__,
            )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='done',
                    last_error=%s,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (reason_text[:2000], job_id),
            )
    _sync_search_session_job(
        conn,
        job_id,
        raw_status="done",
        reason=reason_text,
        analysis_summary=analysis_summary,
    )


__all__ = ["finish_skipped_impl"]
