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
    _provider_retry_reason,
)
from app.workers.apify_jobs_worker_session import _sync_search_session_job


logger = get_logger(__name__)


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
