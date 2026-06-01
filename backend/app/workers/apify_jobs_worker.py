"""Persistent apify_jobs worker with local mock analysis only."""
from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.config import DB_RUNTIME_URL
from app.core.logging import get_logger


logger = get_logger(__name__)
POLL_SECONDS = float(os.environ.get("APIFY_WORKER_POLL_SECONDS", "2"))
_stop_requested = False


def _request_stop(_signum: int, _frame: Any) -> None:
    global _stop_requested
    _stop_requested = True


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _mock_result(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "mock": True,
        "note": "placeholder analysis result; no LLM or network call was made",
        "job_id": job["id"],
        "job_type": job.get("job_type"),
        "target_type": payload.get("target_type"),
        "target_id": str(payload.get("target_id")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _claim_job(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, job_type, payload
                FROM apify_jobs
                WHERE status = 'queued'
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            job = cur.fetchone()
            if not job:
                return None
            cur.execute(
                "UPDATE apify_jobs SET status='running', updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )
            return dict(job)


def _process_job(conn: psycopg.Connection[Any], job: dict[str, Any]) -> None:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    target_type = str(payload.get("target_type") or "").strip()
    target_id = str(payload.get("target_id") or "").strip()
    if not target_type or not target_id:
        raise ValueError("payload must include target_type and target_id")
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    triggered_by_user_id = int(triggered_by) if triggered_by not in (None, "") else None
    result = _mock_result(job, payload)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_analysis_cache (
                  target_type, target_id, model, derive_method, result, cost,
                  status, triggered_by_user_id, created_at, updated_at
                )
                VALUES (%s, %s, 'mock', 'mock', %s::jsonb, 0, 'ready', %s, NOW(), NOW())
                ON CONFLICT (target_type, target_id, derive_method)
                DO UPDATE SET
                  model = EXCLUDED.model,
                  result = EXCLUDED.result,
                  cost = EXCLUDED.cost,
                  status = 'ready',
                  triggered_by_user_id = EXCLUDED.triggered_by_user_id,
                  updated_at = NOW()
                """,
                (target_type, target_id, _json(result), triggered_by_user_id),
            )
            cur.execute(
                "UPDATE apify_jobs SET status='done', updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )


def _fail_job(conn: psycopg.Connection[Any], job_id: int, exc: Exception) -> None:
    message = f"{type(exc).__name__}: {exc}"[:2000]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='failed', attempts=attempts+1, last_error=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (message, job_id),
            )


def run_worker() -> None:
    if not DB_RUNTIME_URL:
        raise RuntimeError("DATABASE_URL is required for apify_jobs worker")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    logger.info("apify_jobs mock worker started | poll_seconds=%s", POLL_SECONDS)
    with psycopg.connect(DB_RUNTIME_URL) as conn:
        while not _stop_requested:
            job = _claim_job(conn)
            if not job:
                time.sleep(POLL_SECONDS)
                continue
            try:
                _process_job(conn, job)
                logger.info("apify_jobs mock job done | id=%s", job["id"])
            except Exception as exc:
                logger.exception("apify_jobs mock job failed | id=%s", job.get("id"))
                _fail_job(conn, int(job["id"]), exc)
    logger.info("apify_jobs mock worker stopped")


if __name__ == "__main__":
    run_worker()
