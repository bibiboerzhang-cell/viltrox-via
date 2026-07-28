"""失败领养 / 陈旧 running 回收的运维簇,从 apify_jobs_worker.py 整簇 move 出来。

函数体逐字不变 → 行为必然不变;原文件 re-export 兜住所有调用点。
原文件留下的重试常量与 _provider_retry_delay_seconds 在本模块**底部** import
(避免循环导入;均在函数体内运行期解析)。红线:本簇零 fit 写。
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.logging import get_logger
from app.workers.apify_jobs_worker_helpers import (
    _error_category,
    _json,
    _provider_retry_reason,
)
from app.workers.apify_jobs_worker_session import _sync_search_session_job


logger = get_logger(__name__)
_TERMINAL_JOB_STATUSES = frozenset({"done", "blocked", "failed", "triage"})


def _reconcile_zero_item_profile_advance_session(
    conn: psycopg.Connection[Any],
    row: dict[str, Any],
) -> bool:
    """Close a terminal profile-advance session that never created item lineage.

    ``smart_search_profile_advance`` is session-scoped until its first candidate
    item is persisted.  If the worker connection is terminated before that
    write, the generic item-lineage reducer has nothing to replay.  Keep this
    fallback deliberately narrow and repeat every predicate in the UPDATE so a
    concurrent retry or item write wins without being overwritten.
    """

    if str(row.get("job_type") or "").strip().lower() != "smart_search_profile_advance":
        return False
    if int(row.get("session_item_count") or 0) != 0:
        return False
    raw_status = str(row.get("status") or "").strip().lower()
    if raw_status not in _TERMINAL_JOB_STATUSES:
        return False
    try:
        job_id = int(row["id"])
        session_id = int(row["session_id"])
    except (KeyError, TypeError, ValueError):
        return False

    reason = str(row.get("last_error") or "").strip()[:1000]
    summary_patch = {
        "status": "failed",
        "job_id": job_id,
        "terminal_job_status": raw_status,
        "error": reason or f"terminal_job_without_items:{raw_status}",
        "reconciled_reason": "terminal_job_without_items",
        "viltrox_fit_score_untouched": True,
    }
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE vkpi_kol_search_sessions AS session
                SET status='failed',
                    result_summary_json =
                        COALESCE(session.result_summary_json, '{}'::jsonb)
                        || jsonb_build_object(
                            'phase', 'failed',
                            'terminal_synced_at', NOW(),
                            'smart_search_profile_advance_job',
                            COALESCE(
                                session.result_summary_json->'smart_search_profile_advance_job',
                                '{}'::jsonb
                            ) || %s::jsonb
                        ),
                    updated_at=NOW()
                WHERE session.id=%s
                  AND session.status='running'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM vkpi_kol_search_session_items AS item
                      WHERE item.session_id=session.id
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM apify_jobs AS job
                      WHERE job.id=%s
                        AND job.job_type='smart_search_profile_advance'
                        AND job.status IN ('done', 'blocked', 'failed', 'triage')
                        AND CASE
                            WHEN COALESCE(job.payload->>'search_session_id', '') ~ '^[0-9]+$'
                            THEN (job.payload->>'search_session_id')::bigint
                        END = session.id
                  )
                RETURNING session.id
                """,
                (_json(summary_patch), session_id, job_id),
            )
            return bool(cur.fetchone())


def _reconcile_terminal_search_session_jobs(
    conn: psycopg.Connection[Any],
    *,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Replay terminal queue truth into still-running Smart Search sessions.

    Older handlers could persist a terminal ``apify_jobs.status`` without
    invoking the session reducer.  This bounded startup repair is deliberately
    narrower than a general queue replay: it considers only terminal jobs that
    carry explicit search-session lineage and only sessions that are still
    marked ``running``.  Replaying the reducer is idempotent and never changes
    queue job status or provider state.
    """

    bounded_limit = max(1, min(int(limit or 1), 5000))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            WITH terminal_lineage AS (
                SELECT
                    job.id,
                    job.status,
                    job.last_error,
                    job.job_type,
                    CASE
                        WHEN COALESCE(job.payload->>'search_session_id', '') ~ '^[0-9]+$'
                        THEN (job.payload->>'search_session_id')::bigint
                    END AS session_id
                FROM apify_jobs AS job
                WHERE job.status IN ('done', 'blocked', 'failed', 'triage')

                UNION ALL

                SELECT
                    job.id,
                    job.status,
                    job.last_error,
                    job.job_type,
                    CASE
                        WHEN COALESCE(lineage.value->>'search_session_id', '') ~ '^[0-9]+$'
                        THEN (lineage.value->>'search_session_id')::bigint
                    END AS session_id
                FROM apify_jobs AS job
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(job.payload->'search_session_lineage') = 'array'
                        THEN job.payload->'search_session_lineage'
                        ELSE '[]'::jsonb
                    END
                ) AS lineage(value)
                WHERE job.status IN ('done', 'blocked', 'failed', 'triage')
            )
            SELECT DISTINCT
                lineage.id,
                lineage.status,
                lineage.last_error,
                lineage.job_type,
                lineage.session_id,
                (
                    SELECT COUNT(*)
                    FROM vkpi_kol_search_session_items AS item
                    WHERE item.session_id=lineage.session_id
                ) AS session_item_count
            FROM terminal_lineage AS lineage
            JOIN vkpi_kol_search_sessions AS session
              ON session.id=lineage.session_id
             AND session.status='running'
            WHERE lineage.session_id IS NOT NULL
            ORDER BY lineage.id
            LIMIT %s
            """,
            (bounded_limit,),
        )
        candidates = [dict(row) for row in (cur.fetchall() or [])]

    replayed: list[dict[str, Any]] = []
    for row in candidates:
        job_id = int(row["id"])
        raw_status = str(row.get("status") or "failed").strip().lower()
        if not _sync_search_session_job(
            conn,
            job_id,
            raw_status=raw_status,
            reason=str(row.get("last_error") or ""),
        ):
            if not _reconcile_zero_item_profile_advance_session(conn, row):
                continue
        replayed.append(row)
    if replayed:
        logger.info(
            "search session terminal repair replayed | candidates=%s replayed=%s",
            len(candidates),
            len(replayed),
        )
    return replayed


def _reclaim_stale_running_jobs(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET
                  status = CASE WHEN attempts < %(max_attempts)s THEN 'queued' ELSE 'failed' END,
                  attempts = attempts + 1,
                  last_error = CASE
                    WHEN attempts < %(max_attempts)s THEN 'stale_running_reclaimed: requeued after worker heartbeat expired'
                    ELSE 'stale_running_reclaimed: max attempts reached'
                  END,
                  last_error_category = 'stale_running',
                  next_retry_at = NULL,
                  updated_at = NOW()
                WHERE status='running'
                  -- Fabric 增量2:判显式租约到期。向后兼容:170 之前 claim 的旧 running 行
                  -- lease_expires_at 为 NULL → COALESCE 回退到「updated_at + stale」旧判据(与历史完全一致);
                  -- 170 之后 claim 的行走 lease_expires_at(claim 设、heartbeat 续)。时序不变,语义更稳:
                  -- 心跳停=租约不再续→到期被回收;owner 列让未来多机认领可区分谁的活。
                  AND COALESCE(lease_expires_at, updated_at + make_interval(secs => %(stale_seconds)s)) < NOW()
                RETURNING id, status, attempts, payload, last_error
                """,
                {"max_attempts": MAX_JOB_ATTEMPTS, "stale_seconds": STALE_RECLAIM_SECONDS},
            )
            rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        logger.warning(
            "apify_jobs stale running reclaimed | id=%s target_id=%s status=%s attempts=%s",
            row.get("id"),
            payload.get("target_id"),
            row.get("status"),
            row.get("attempts"),
        )
        try:
            _sync_search_session_job(
                conn,
                int(row.get("id")),
                raw_status=str(row.get("status") or "queued"),
                reason=str(row.get("last_error") or "stale_running_reclaimed"),
            )
        except Exception as exc:
            logger.warning("search session stale job sync failed | job_id=%s error=%s", row.get("id"), exc)
    return rows


def _adopt_recent_provider_pressure_failures(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    if PROVIDER_RETRY_ADOPT_WINDOW_MINUTES <= 0:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, attempts, last_error, updated_at, payload
            FROM apify_jobs
            WHERE status='failed'
              AND attempts < %(max_attempts)s
              AND updated_at >= NOW() - make_interval(mins => %(window_minutes)s)
            ORDER BY updated_at DESC, id DESC
            LIMIT 25
            """,
            {
                "max_attempts": PROVIDER_RETRY_MAX_ATTEMPTS,
                "window_minutes": PROVIDER_RETRY_ADOPT_WINDOW_MINUTES,
            },
        )
        candidates = [dict(row) for row in cur.fetchall()]
    adopted: list[dict[str, Any]] = []
    for row in candidates:
        message = str(row.get("last_error") or "")[:2000]
        if _error_category(message) != "provider_pressure":
            continue
        attempts = int(row.get("attempts") or 0)
        delay_seconds = _provider_retry_delay_seconds(attempts or 1)
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE apify_jobs
                    SET status='queued',
                        last_error=%s,
                        last_error_category='provider_pressure',
                        next_retry_at=NOW() + make_interval(secs => %s),
                        updated_at=NOW()
                    WHERE id=%s
                      AND status='failed'
                    RETURNING id, status, attempts, payload, last_error, last_error_category, next_retry_at
                    """,
                    (
                        _provider_retry_reason(message),
                        delay_seconds,
                        int(row["id"]),
                    ),
                )
                updated = cur.fetchone()
        if not updated:
            continue
        adopted_row = dict(updated)
        adopted.append(adopted_row)
        payload = adopted_row.get("payload") if isinstance(adopted_row.get("payload"), dict) else {}
        logger.warning(
            "apify_jobs adopted provider pressure failure | id=%s target_id=%s attempts=%s delay_seconds=%s next_retry_at=%s",
            adopted_row.get("id"),
            payload.get("target_id"),
            adopted_row.get("attempts"),
            delay_seconds,
            adopted_row.get("next_retry_at"),
        )
        try:
            _sync_search_session_job(
                conn,
                int(adopted_row["id"]),
                raw_status="queued",
                reason=str(adopted_row.get("last_error") or "provider_pressure_retry_scheduled"),
            )
        except Exception as exc:
            logger.warning("search session adopted retry sync failed | job_id=%s error=%s", adopted_row.get("id"), exc)
    return adopted



# 原文件留下的重试常量 + _provider_retry_delay_seconds:放底部 import(避免循环导入)。
from app.workers.apify_jobs_worker import (  # noqa: E402
    MAX_JOB_ATTEMPTS,
    PROVIDER_RETRY_ADOPT_WINDOW_MINUTES,
    PROVIDER_RETRY_MAX_ATTEMPTS,
    STALE_RECLAIM_SECONDS,
    _provider_retry_delay_seconds,
)
