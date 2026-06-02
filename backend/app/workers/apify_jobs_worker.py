"""Persistent apify_jobs worker with mock analysis and LLM brake controls."""
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
from app.platform import llm_gateway


logger = get_logger(__name__)
POLL_SECONDS = float(os.environ.get("APIFY_WORKER_POLL_SECONDS", "2"))
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
LLM_CONCURRENCY_LIMIT = max(1, min(2, int(os.environ.get("APIFY_WORKER_LLM_CONCURRENCY", "1"))))
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "1200"))
LLM_TARGET_TYPES = {"video", "contract"}
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


def _derive_method(payload: dict[str, Any]) -> str:
    return str(payload.get("derive_method") or payload.get("analysis_method") or "mock").strip().lower() or "mock"


def _target(payload: dict[str, Any]) -> tuple[str, str]:
    return str(payload.get("target_type") or "").strip(), str(payload.get("target_id") or "").strip()


def _advisory_lock(conn: psycopg.Connection[Any], scope: str, key: str) -> bool:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s)) AS locked", (scope, key))
        row = cur.fetchone() or {}
        return bool(row.get("locked"))


def _advisory_unlock(conn: psycopg.Connection[Any], scope: str, key: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))", (scope, key))


def _acquire_llm_slot(conn: psycopg.Connection[Any]) -> str | None:
    for index in range(LLM_CONCURRENCY_LIMIT):
        slot = str(index)
        if _advisory_lock(conn, "vkpi_analysis_worker_llm_slot", slot):
            return slot
    return None


def _analysis_cache_exists(conn: psycopg.Connection[Any], target_type: str, target_id: str, derive_method: str) -> bool:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT 1
            FROM vkpi_analysis_cache
            WHERE target_type=%s AND target_id=%s AND derive_method=%s AND status='ready'
            LIMIT 1
            """,
            (target_type, target_id, derive_method),
        )
        return cur.fetchone() is not None


def _finish_skipped(conn: psycopg.Connection[Any], job_id: int, reason: str) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=%s, updated_at=NOW() WHERE id=%s",
                (reason[:2000], job_id),
            )


def _block_job(conn: psycopg.Connection[Any], job_id: int, reason: str, detail: dict[str, Any] | None = None) -> None:
    payload = {"reason": reason, **(detail or {})}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='blocked', last_error=%s, updated_at=NOW() WHERE id=%s",
                (_json(payload)[:2000], job_id),
            )


def _requeue_job(conn: psycopg.Connection[Any], job_id: int, reason: str) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='queued', last_error=%s, updated_at=NOW() WHERE id=%s",
                (reason[:2000], job_id),
            )


def _llm_budget_preflight(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    target_type, target_id = _target(payload)
    prompt = str(payload.get("prompt") or f"{job.get('job_type') or 'analysis'} {target_type}:{target_id}")
    return llm_gateway.budget_preflight(
        prompt,
        purpose="vkpi_analysis_worker",
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        preferred_provider="google",
        cost_tag=LLM_BUDGET_SCOPE,
    )


def _google_allowed(preflight: dict[str, Any]) -> tuple[bool, str, float]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    google = next((item for item in providers if item.get("provider") == "google"), {})
    reason = str(preflight.get("provider_gate_reason") or google.get("provider_gate_reason") or "provider_calls_blocked")
    return bool(google.get("provider_calls_allowed")), reason, float(google.get("estimated_cost_usd") or 0.0)


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
    target_type, target_id = _target(payload)
    if not target_type or not target_id:
        raise ValueError("payload must include target_type and target_id")
    derive_method = _derive_method(payload)
    if derive_method != "mock":
        if target_type not in LLM_TARGET_TYPES:
            _block_job(conn, int(job["id"]), "unsupported_llm_target_type", {"target_type": target_type})
            return
        target_lock = f"{target_type}:{target_id}:{derive_method}"
        if not _advisory_lock(conn, "vkpi_analysis_worker_target", target_lock):
            _requeue_job(conn, int(job["id"]), "analysis target already in progress")
            return
        slot = _acquire_llm_slot(conn)
        try:
            if slot is None:
                _requeue_job(conn, int(job["id"]), "llm concurrency limit reached")
                return
            if _analysis_cache_exists(conn, target_type, target_id, derive_method):
                _finish_skipped(conn, int(job["id"]), "skipped_existing_analysis_cache")
                return
            preflight = _llm_budget_preflight(job, payload)
            allowed, reason, estimated_cost = _google_allowed(preflight)
            if not allowed:
                _block_job(conn, int(job["id"]), reason, {"estimated_cost_usd": estimated_cost, "budget_scope": LLM_BUDGET_SCOPE})
                return
            _block_job(conn, int(job["id"]), "llm_analyzer_not_connected_c4", {"budget_scope": LLM_BUDGET_SCOPE})
            return
        finally:
            if slot is not None:
                _advisory_unlock(conn, "vkpi_analysis_worker_llm_slot", slot)
            _advisory_unlock(conn, "vkpi_analysis_worker_target", target_lock)
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
