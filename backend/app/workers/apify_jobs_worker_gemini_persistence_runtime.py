"""Persistence and follow-up stages for Gemini video orchestration."""
from __future__ import annotations

from typing import Any

from app.workers.apify_jobs_worker_gemini_contract import (
    AnalyzerRun,
    FinalizedVideo,
    GeminiVideoRuntimeDependencies,
)


def _persist_cache(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
    run: AnalyzerRun,
    finalized: FinalizedVideo,
    dependencies: GeminiVideoRuntimeDependencies,
) -> tuple[int, dict[str, Any], dict[str, Any], str, str]:
    route = run.route
    raw = finalized.raw
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    shaped = dependencies.shape_result(
        job=job,
        evidence=route.evidence,
        raw={**raw, "ledger": finalized.ledger},
        cost=finalized.cost,
        cost_basis=finalized.cost_basis,
        preflight_cost=preflight_cost,
        latency_ms=finalized.latency_ms,
        derive_method=route.derive_method,
    )
    execution = dependencies.execution_metadata(raw)
    cache_derive_method = (
        execution["cache_derive_method"]
        if execution["evaluation_only"]
        else route.derive_method
    )
    cache_target_type = (
        dependencies.quality_triage_target_type(route.target_type)
        if finalized.cache_status == "quality_incomplete"
        else route.target_type
    )
    persist_started = dependencies.monotonic()
    with conn.transaction():
        with conn.cursor() as cur:
            cache_id = dependencies.upsert_cache(
                cur,
                target_type=cache_target_type,
                target_id=route.target_id,
                model=str(raw.get("model") or raw.get("method") or "gemini_video"),
                derive_method=cache_derive_method,
                result_json=dependencies.json_dump(shaped),
                cost=finalized.cost,
                triggered_by_user_id=dependencies.int_or_none(triggered_by),
                prompt_version=dependencies.cache_prompt_version(route.derive_method),
                status=finalized.cache_status,
            )
            dependencies.finish_cache_job(
                cur,
                job_id=int(job["id"]),
                cache_status=finalized.cache_status,
                raw=raw,
            )
    run.clock.add("persist", persist_started)
    return cache_id, shaped, execution, cache_derive_method, cache_target_type


def _finish_quality_incomplete(
    conn: Any,
    job: dict[str, Any],
    run: AnalyzerRun,
    finalized: FinalizedVideo,
    cache_id: int,
    cache_derive_method: str,
    cache_target_type: str,
    dependencies: GeminiVideoRuntimeDependencies,
) -> None:
    reason = dependencies.quality_incomplete_reason(finalized.raw)
    dependencies.sync_search_session_job(
        conn,
        int(job["id"]),
        raw_status="triage",
        reason=str(reason["reason"]),
        analysis_summary={
            "status": "quality_incomplete",
            "cache_id": cache_id,
            "derive_method": cache_derive_method,
            "target_type": run.route.target_type,
            "cache_target_type": cache_target_type,
            "target_id": run.route.target_id,
            "quality_issues": reason["quality_issues"],
        },
    )
    dependencies.record_diagnostics(
        conn,
        job_id=int(job["id"]),
        raw=finalized.raw,
        clock=run.clock,
        platform=run.route.platform,
        error=str(reason["reason"]),
    )


def _finish_evaluation_only(
    conn: Any,
    job: dict[str, Any],
    run: AnalyzerRun,
    cache_id: int,
    cache_derive_method: str,
    execution: dict[str, Any],
    dependencies: GeminiVideoRuntimeDependencies,
) -> None:
    dependencies.sync_search_session_job(
        conn,
        int(job["id"]),
        raw_status="done",
        analysis_summary={
            "cache_id": cache_id,
            "derive_method": cache_derive_method,
            "base_derive_method": run.route.derive_method,
            "target_type": run.route.target_type,
            "target_id": run.route.target_id,
            "evaluation_only": True,
            "production_authorized": False,
            "claim_status": "descriptive_only",
            "model_readiness_status": execution["model_readiness_status"],
        },
    )


def _enqueue_production_followups(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    run: AnalyzerRun,
    finalized: FinalizedVideo,
    cache_id: int,
    shaped: dict[str, Any],
    dependencies: GeminiVideoRuntimeDependencies,
) -> None:
    route = run.route
    deep_result = dependencies.sync_deep_result(
        conn,
        cache_id=cache_id,
        derive_method=route.derive_method,
        job_id=int(job["id"]),
    )
    account_extract_job = None
    try:
        account_extract_job = dependencies.enqueue_account(
            conn,
            job_id=int(job["id"]),
            deep_result=deep_result,
        )
        dependencies.enqueue_content_fit(
            conn,
            job_id=int(job["id"]),
            deep_result=deep_result,
            source_payload=payload,
        )
    except Exception as exc:
        dependencies.logger.warning(
            "final_v1 followup enqueue failed (non-fatal) | job_id=%s exception_type=%s",
            job.get("id"),
            type(exc).__name__,
        )
    dependencies.extract_lens(
        cache_id=cache_id,
        derive_method=route.derive_method,
        job_id=job.get("id"),
    )
    analysis_summary = dependencies.search_session_summary(
        cache_id=cache_id,
        derive_method=route.derive_method,
        target_type=route.target_type,
        target_id=route.target_id,
        evidence=route.evidence,
        result=shaped,
        cost=finalized.cost,
    )
    if analysis_summary and deep_result:
        analysis_summary["deep_result"] = deep_result
    if analysis_summary and account_extract_job:
        analysis_summary["account_dossier_extract_job"] = account_extract_job
    dependencies.sync_search_session_job(
        conn,
        int(job["id"]),
        raw_status="done",
        analysis_summary=analysis_summary,
    )


def persist_and_follow_up(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
    run: AnalyzerRun,
    finalized: FinalizedVideo,
    dependencies: GeminiVideoRuntimeDependencies,
) -> None:
    cache_id, shaped, execution, cache_derive_method, cache_target_type = (
        _persist_cache(
            conn,
            job,
            payload,
            preflight_cost,
            run,
            finalized,
            dependencies,
        )
    )
    if finalized.cache_status == "quality_incomplete":
        _finish_quality_incomplete(
            conn,
            job,
            run,
            finalized,
            cache_id,
            cache_derive_method,
            cache_target_type,
            dependencies,
        )
        return
    followups_started = dependencies.monotonic()
    if execution["evaluation_only"]:
        _finish_evaluation_only(
            conn,
            job,
            run,
            cache_id,
            cache_derive_method,
            execution,
            dependencies,
        )
        return
    _enqueue_production_followups(
        conn,
        job,
        payload,
        run,
        finalized,
        cache_id,
        shaped,
        dependencies,
    )
    run.clock.add("followups", followups_started)
    dependencies.record_diagnostics(
        conn,
        job_id=int(job["id"]),
        raw=finalized.raw,
        clock=run.clock,
        platform=run.route.platform,
    )
