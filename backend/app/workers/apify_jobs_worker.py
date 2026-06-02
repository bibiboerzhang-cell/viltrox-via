"""Persistent apify_jobs worker with mock analysis and LLM brake controls."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from app.core.config import DB_RUNTIME_URL
from app.core.logging import get_logger
from app.db.connection import close_db_runtime_sync, db_connection_sync_scope
from app.domains.costs import budget_guard
from app.platform import llm_gateway
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer


logger = get_logger(__name__)
POLL_SECONDS = float(os.environ.get("APIFY_WORKER_POLL_SECONDS", "2"))
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
LLM_CONCURRENCY_LIMIT = max(1, min(2, int(os.environ.get("APIFY_WORKER_LLM_CONCURRENCY", "1"))))
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "1200"))
LLM_TARGET_TYPES = {"video", "contract"}
_stop_event = threading.Event()


def _request_stop(_signum: int, _frame: Any) -> None:
    _stop_event.set()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


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
    with db_connection_sync_scope():
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


def _load_video_evidence(conn: psycopg.Connection[Any], target_id: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              e.id,
              e.content_url,
              COALESCE(e.video_title, e.title, '') AS title,
              e.platform,
              e.view_count,
              e.duration_seconds,
              e.publish_date,
              e.project_id,
              e.kol_pool_id,
              COALESCE(kp.handle, '') AS creator_handle,
              COALESCE(kp.display_name, '') AS creator_name
            FROM vkpi_kol_video_evidence e
            LEFT JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
            WHERE e.id = %s
            LIMIT 1
            """,
            (_int_or_none(target_id) or 0,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"video evidence not found: {target_id}")
    if not str(row.get("content_url") or "").strip():
        raise ValueError(f"video evidence has no content_url: {target_id}")
    return dict(row)


def _usage_count(metadata: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            parsed = _int_or_none(value)
            return parsed or 0
    return 0


def _gemini_cost(result: dict[str, Any], fallback_cost: float) -> tuple[float, str, int, int]:
    metadata = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else {}
    tokens_in = _usage_count(metadata, "prompt_token_count", "promptTokenCount")
    tokens_out = _usage_count(metadata, "candidates_token_count", "candidatesTokenCount")
    if tokens_in or tokens_out:
        config = llm_gateway.PROVIDER_CONFIG.get("google") or {}
        input_cents = float(config.get("input_cents_per_million") or 0)
        output_cents = float(config.get("output_cents_per_million") or 0)
        cost = ((tokens_in * input_cents) + (tokens_out * output_cents)) / 100_000_000
        return round(max(0.0, cost), 6), "gemini_usage_metadata_local_rate", tokens_in, tokens_out
    return round(max(0.0, float(fallback_cost or 0.0)), 6), "llm_gateway_budget_preflight", 0, 0


def _low_scores(scores: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for key, value in scores.items():
        if isinstance(value, (int, float)) and value <= 6:
            output.append({"dimension": key, "score": value})
    return output[:8]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def _gemini_worker_overrides(payload: dict[str, Any]):
    model_override = str(payload.get("gemini_model") or os.environ.get("APIFY_WORKER_GEMINI_MODEL") or "").strip()
    skip_subtitles = _truthy(
        payload.get("skip_subtitles", payload.get("gemini_skip_subtitles", os.environ.get("APIFY_WORKER_GEMINI_SKIP_SUBTITLES")))
    )
    with ExitStack() as stack:
        if skip_subtitles:
            stack.enter_context(patch.object(gemini_video_analyzer, "fetch_youtube_subtitles", lambda *_args, **_kwargs: ""))
        if model_override and getattr(gemini_video_analyzer, "gemini_client", None):
            original_generate = gemini_video_analyzer.gemini_client.models.generate_content

            def _forced_generate_content(*args: Any, **kwargs: Any):
                kwargs["model"] = model_override
                return original_generate(*args, **kwargs)

            stack.enter_context(patch.object(gemini_video_analyzer.gemini_client.models, "generate_content", _forced_generate_content))
        yield model_override


def _shape_gemini_result(
    *,
    job: dict[str, Any],
    evidence: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    preflight_cost: float,
    latency_ms: int,
) -> dict[str, Any]:
    quality_scores = raw.get("quality_scores") if isinstance(raw.get("quality_scores"), dict) else {}
    return {
        "mock": False,
        "analysis_method": "gemini",
        "job_id": job.get("id"),
        "target_type": "video",
        "target_id": str(evidence.get("id")),
        "source": {
            "url": evidence.get("content_url"),
            "title": evidence.get("title"),
            "platform": evidence.get("platform"),
            "creator_handle": evidence.get("creator_handle"),
            "project_id": evidence.get("project_id"),
            "kol_pool_id": evidence.get("kol_pool_id"),
        },
        "platform_algorithm_rules": {
            "content_genre": raw.get("content_genre"),
            "target_audience": raw.get("target_audience"),
            "hook_analysis": raw.get("hook_analysis"),
            "marketing_potential": raw.get("marketing_potential"),
            "brand_integration_depth": raw.get("brand_integration_depth"),
            "community_value": raw.get("community_value"),
            "quality_scores": quality_scores,
        },
        "weak_performance_reasons": {
            "quality_summary": raw.get("quality_summary"),
            "vertical_quality_notes": raw.get("vertical_quality_notes"),
            "marketing_notes": raw.get("marketing_notes"),
            "tech_floor": raw.get("tech_floor"),
            "low_scores": _low_scores(quality_scores),
        },
        "improvement_suggestions": raw.get("improvements") if isinstance(raw.get("improvements"), list) else [],
        "raw_gemini_video": raw,
        "cost": {
            "recorded_cost_usd": cost,
            "cost_basis": cost_basis,
            "preflight_estimated_cost_usd": preflight_cost,
            "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
            "latency_ms": latency_ms,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _record_gemini_cost(
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    preflight_cost: float,
) -> dict[str, Any]:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    with db_connection_sync_scope():
        return budget_guard.record_cost(
            scope=LLM_BUDGET_SCOPE,
            cron_task="vkpi_analysis_worker",
            ai_provider="gemini",
            model_name=str(raw.get("model") or raw.get("method") or "gemini_video"),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            metadata={
                "status": "success" if raw.get("analyzed") else "provider_error",
                "job_id": job.get("id"),
                "target_type": payload.get("target_type"),
                "target_id": str(payload.get("target_id") or ""),
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "latency_ms": latency_ms,
                "triggered_by_user_id": triggered_by,
                "error": raw.get("error") or "",
            },
            extra_scopes=["monthly_total", "single_call", "provider:gemini"],
        )


def _process_gemini_video(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
) -> None:
    target_type, target_id = _target(payload)
    if target_type != "video":
        _block_job(conn, int(job["id"]), "unsupported_gemini_target_type", {"target_type": target_type})
        return
    evidence = _load_video_evidence(conn, target_id)
    logger.info(
        "apify_jobs gemini video start | job_id=%s target_id=%s url=%s",
        job.get("id"),
        target_id,
        str(evidence.get("content_url") or "")[:120],
    )
    started = time.monotonic()
    with _gemini_worker_overrides(payload) as model_override:
        raw = asyncio.run(
            gemini_video_analyzer.analyze_youtube_with_gemini(
                str(evidence.get("content_url") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("creator_handle") or ""),
            )
        )
    if model_override and raw.get("analyzed"):
        raw["model"] = model_override
        method = str(raw.get("method") or "")
        if method.startswith("gemini_direct_"):
            raw["method"] = f"gemini_direct_{model_override}"
        elif method.startswith("gemini_fileapi_"):
            raw["method"] = f"gemini_fileapi_{model_override}"
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "apify_jobs gemini video returned | job_id=%s analyzed=%s method=%s latency_ms=%s",
        job.get("id"),
        bool(raw.get("analyzed")),
        raw.get("method"),
        latency_ms,
    )
    cost, cost_basis, tokens_in, tokens_out = _gemini_cost(raw, preflight_cost)
    ledger = _record_gemini_cost(
        job=job,
        payload=payload,
        raw=raw,
        cost=cost,
        cost_basis=cost_basis,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        preflight_cost=preflight_cost,
    )
    if not raw.get("analyzed"):
        raise RuntimeError(f"Gemini video analysis failed: {raw.get('error') or 'not_analyzed'}")
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    triggered_by_user_id = _int_or_none(triggered_by)
    shaped = _shape_gemini_result(
        job=job,
        evidence=evidence,
        raw={**raw, "ledger": ledger},
        cost=cost,
        cost_basis=cost_basis,
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_analysis_cache (
                  target_type, target_id, model, derive_method, result, cost,
                  status, triggered_by_user_id, created_at, updated_at
                )
                VALUES (%s, %s, %s, 'gemini', %s::jsonb, %s, 'ready', %s, NOW(), NOW())
                ON CONFLICT (target_type, target_id, derive_method)
                DO UPDATE SET
                  model = EXCLUDED.model,
                  result = EXCLUDED.result,
                  cost = EXCLUDED.cost,
                  status = 'ready',
                  triggered_by_user_id = EXCLUDED.triggered_by_user_id,
                  updated_at = NOW()
                """,
                (
                    target_type,
                    target_id,
                    str(raw.get("model") or raw.get("method") or "gemini_video"),
                    _json(shaped),
                    cost,
                    triggered_by_user_id,
                ),
            )
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )


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
            if derive_method == "gemini":
                _process_gemini_video(conn, job, payload, estimated_cost)
                return
            _block_job(conn, int(job["id"]), "unsupported_llm_derive_method", {"derive_method": derive_method})
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
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
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
    try:
        with psycopg.connect(DB_RUNTIME_URL, autocommit=True) as conn:
            while not _stop_event.is_set():
                job = _claim_job(conn)
                if not job:
                    _stop_event.wait(POLL_SECONDS)
                    continue
                try:
                    _process_job(conn, job)
                    logger.info("apify_jobs mock job done | id=%s", job["id"])
                except Exception as exc:
                    logger.exception("apify_jobs mock job failed | id=%s", job.get("id"))
                    _fail_job(conn, int(job["id"]), exc)
    finally:
        close_db_runtime_sync()
        logger.info("apify_jobs mock worker stopped")


if __name__ == "__main__":
    run_worker()
