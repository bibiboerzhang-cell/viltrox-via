"""Orchestration runtime for the Gemini video worker.

This module deliberately imports no application modules.  The worker binds its
current collaborators for every call, which keeps the long provider workflow
testable and preserves the monkeypatch seams used by the worker test suite.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.workers.apify_jobs_worker_gemini_contract import (
    AnalyzerRun,
    FinalizedVideo,
    GeminiVideoRuntimeDependencies,
    RoutedVideo,
)
from app.workers.apify_jobs_worker_gemini_persistence_runtime import (
    persist_and_follow_up,
)


def _dispatch_special_route(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
    derive_method: str,
    dependencies: GeminiVideoRuntimeDependencies,
) -> bool:
    processors = {
        dependencies.final_v1_keyframe_qa_derive_method: dependencies.process_keyframe_qa,
        "gemini_video_v2_flash_pro_judge": dependencies.process_flash_pro_judge,
        "gemini_video_v2_flash_gpt55_judge": dependencies.process_flash_gpt55_judge,
        "gemini_video_v2_flash_claude_judge": dependencies.process_flash_claude_judge,
    }
    processor = processors.get(derive_method)
    if processor is None:
        return False
    processor(conn, job, payload, evidence, preflight_cost)
    return True


def _route_video(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
    dependencies: GeminiVideoRuntimeDependencies,
) -> RoutedVideo | None:
    target_type, target_id = dependencies.target(payload)
    derive_method = dependencies.derive_method(payload)
    if target_type != "video":
        dependencies.block_job(
            conn,
            int(job["id"]),
            "unsupported_gemini_target_type",
            {"target_type": target_type},
        )
        return None
    evidence = dependencies.load_video_evidence(conn, target_id)
    content_url = str(evidence.get("content_url") or "")
    platform = dependencies.platform_from_content_url(content_url)
    if platform == "unsupported":
        dependencies.block_job(
            conn,
            int(job["id"]),
            "unsupported_platform",
            {"source_url_host": dependencies.url_host(content_url)},
        )
        return None
    if (
        platform in {"instagram", "tiktok"}
        and derive_method not in dependencies.v2_derive_methods
        and derive_method not in dependencies.final_derive_methods
    ):
        dependencies.block_job(
            conn,
            int(job["id"]),
            "unsupported_media_derive_method",
            {"platform": platform, "derive_method": derive_method},
        )
        return None
    if _dispatch_special_route(
        conn,
        job,
        payload,
        evidence,
        preflight_cost,
        derive_method,
        dependencies,
    ):
        return None
    return RoutedVideo(
        target_type=str(target_type),
        target_id=str(target_id),
        derive_method=str(derive_method),
        evidence=evidence,
        platform=str(platform),
    )


def _build_analyzer_run(
    job: dict[str, Any],
    payload: dict[str, Any],
    route: RoutedVideo,
    dependencies: GeminiVideoRuntimeDependencies,
) -> AnalyzerRun:
    dependencies.logger.info(
        "apify_jobs gemini video start | job_id=%s target_id=%s url=%s",
        job.get("id"),
        route.target_id,
        str(route.evidence.get("content_url") or "")[:120],
    )
    started = dependencies.monotonic()
    clock = dependencies.stage_clock_factory()
    analyzer_payload = {
        **dependencies.gemini_analyzer_payload(payload, route.derive_method),
        "job_id": int(job["id"]),
    }
    model_chain = list(
        analyzer_payload.get("gemini_models") or [dependencies.worker_model]
    )
    authorization = (
        payload.get("_llm_execution")
        if isinstance(payload.get("_llm_execution"), dict)
        else {}
    )
    analyzer_payload["llm_context"] = _llm_context(
        job,
        payload,
        route,
        authorization,
        dependencies,
    )
    return AnalyzerRun(
        route=route,
        started=started,
        clock=clock,
        analyzer_payload=analyzer_payload,
        model_chain=model_chain,
        authorization=authorization,
    )


def _llm_context(
    job: dict[str, Any],
    payload: dict[str, Any],
    route: RoutedVideo,
    authorization: Mapping[str, Any],
    dependencies: GeminiVideoRuntimeDependencies,
) -> dict[str, Any]:
    return {
        "purpose": "audit_video_analysis",
        "cost_tag": dependencies.llm_budget_scope,
        "triggered_by": payload.get("staff_id")
        or payload.get("triggered_by_user_id", payload.get("user_id")),
        "execution_class": str(
            authorization.get("execution_class")
            or dependencies.worker_execution_class
        ),
        "metadata": {
            "surface": "apify_jobs_worker",
            "task_binding": "audit_video_analysis",
            "parent_job_id": job.get("id"),
            "target_type": route.target_type,
            "target_id": route.target_id,
            "platform": route.platform,
            "phase": "video_analysis",
            "target_label": f"video:{route.target_id}",
        },
    }


def _analysis_context(
    run: AnalyzerRun, dependencies: GeminiVideoRuntimeDependencies
) -> dict[str, Any]:
    if run.route.derive_method in dependencies.final_derive_methods:
        return dependencies.video_final_context(run.route.evidence)
    return dependencies.video_performance_context(run.route.evidence)


def _download_local_video(
    evidence: dict[str, Any],
    resolved: dict[str, Any],
    tmpdir: str,
    clock: Any,
    dependencies: GeminiVideoRuntimeDependencies,
) -> dict[str, Any]:
    if resolved.get("cache_hit") or resolved.get("local_path_ready"):
        return {
            "success": True,
            "path": str(resolved.get("path") or ""),
            "bytes": int(resolved.get("bytes") or 0),
            "error": None,
            "cache_hit": True,
        }
    with clock.stage("worker_download"):
        return dependencies.download_direct_video_url(
            str(resolved.get("direct_video_url") or ""),
            tmpdir,
            referer=str(evidence.get("content_url") or ""),
        )


def _raise_download_error(download: dict[str, Any], platform: str) -> None:
    if download.get("precheck_terminal"):
        raise RuntimeError(
            str(download.get("error") or f"content unavailable: {platform}")
        )
    raise RuntimeError(
        f"direct_video_download_failed: {download.get('error') or platform}"
    )


def _handle_unresolved_media(
    conn: Any,
    job: dict[str, Any],
    evidence: dict[str, Any],
    platform: str,
    resolved: dict[str, Any],
    dependencies: GeminiVideoRuntimeDependencies,
) -> None:
    resolve_reason = str(resolved.get("reason") or "")
    if (
        resolved.get("confirmed_non_video") is True
        or resolved.get("no_video_confirmed") is True
    ):
        dependencies.persist_image_post_verdict(conn, evidence)
        dependencies.block_job(
            conn, int(job["id"]), "image_post_no_video", resolved
        )
        return
    raise RuntimeError(resolve_reason or f"media_resolve_failed:{platform}")


def _local_analyzer_payload(
    run: AnalyzerRun,
    download: dict[str, Any],
    dependencies: GeminiVideoRuntimeDependencies,
) -> dict[str, Any]:
    route = run.route
    return {
        **run.analyzer_payload,
        "mode": "local",
        "video_path": str(download["path"]),
        "title": str(route.evidence.get("title") or ""),
        "creator_handle": str(route.evidence.get("creator_handle") or ""),
        "schema_version": (
            "final_v1"
            if route.derive_method in dependencies.final_derive_methods
            else "v2"
        ),
        "performance_context": _analysis_context(run, dependencies),
    }


def _annotate_local_result(
    raw: dict[str, Any],
    run: AnalyzerRun,
    resolved: dict[str, Any],
    download: dict[str, Any],
    dependencies: GeminiVideoRuntimeDependencies,
) -> None:
    content_url = str(run.route.evidence.get("content_url") or "")
    raw["media_resolution"] = {
        "contract": resolved.get("media_resolution_contract"),
        "state": resolved.get("media_resolution_state"),
        "platform": run.route.platform,
        "source_url_host": dependencies.url_host(content_url),
        "direct_video_url_host": resolved.get("direct_video_url_host"),
        "status": resolved.get("status"),
        "scrape_success": bool(resolved.get("scrape_success")),
        "media_resolved": bool(resolved.get("media_resolved")),
        "downloadable": bool(resolved.get("downloadable")),
        "confirmed_non_video": bool(resolved.get("confirmed_non_video")),
        "cache_hit": bool(resolved.get("cache_hit")),
        "cache_source": resolved.get("cache_source"),
        "cache_asset_id": resolved.get("cache_asset_id"),
        "cache_lookup_reason": resolved.get("cache_lookup_reason"),
    }
    raw["local_video_input"] = {
        "download_bytes": int(download.get("bytes") or 0),
        "temporary_files_cleaned": True,
        "download_error": download.get("error"),
    }


def _run_local_analysis(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    run: AnalyzerRun,
    dependencies: GeminiVideoRuntimeDependencies,
) -> dict[str, Any] | None:
    route = run.route
    with dependencies.temporary_directory(prefix="vkpi-analysis-video-") as tmpdir:
        with run.clock.stage("media_resolve"):
            resolved = dependencies.resolve_cached_or_provider_video(
                conn, route.evidence, tmpdir
            )
        if str(resolved.get("status") or "") == "blocked":
            dependencies.block_job(
                conn,
                int(job["id"]),
                str(resolved.get("reason") or "media_resolve_blocked"),
                resolved,
            )
            return None
        if not resolved.get("ok"):
            _handle_unresolved_media(
                conn,
                job,
                route.evidence,
                route.platform,
                resolved,
                dependencies,
            )
            return None
        download = _download_local_video(
            route.evidence, resolved, tmpdir, run.clock, dependencies
        )
        if not download.get("success") or not download.get("path"):
            _raise_download_error(download, route.platform)
        if not dependencies.scope_checkpoint(
            conn,
            job,
            payload,
            route.derive_method,
            provider_calls_performed=bool(resolved.get("provider_calls_performed")),
        ):
            return None
        analyzer_input = _local_analyzer_payload(run, download, dependencies)
        analyzer_started = dependencies.monotonic()
        raw = dependencies.run_analyzer(
            analyzer_input,
            job_id=job.get("id"),
            target_id=route.target_id,
            platform=route.platform,
        )
        run.clock.add("analyzer_subprocess", analyzer_started)
        if not dependencies.scope_checkpoint(
            conn,
            job,
            payload,
            route.derive_method,
            provider_calls_performed=True,
            raw=raw,
        ):
            return None
        _annotate_local_result(raw, run, resolved, download, dependencies)
        if not resolved.get("cache_hit"):
            with run.clock.stage("r2_warm"):
                dependencies.warm_video_to_r2(
                    job_id=job.get("id"),
                    platform=route.platform,
                    content_url=str(route.evidence.get("content_url") or ""),
                    direct_video_url=str(resolved.get("direct_video_url") or ""),
                    local_path=str(download.get("path") or ""),
                )
        return raw


def _youtube_schema(
    derive_method: str, dependencies: GeminiVideoRuntimeDependencies
) -> str:
    if derive_method in dependencies.final_derive_methods:
        return "final_v1"
    if derive_method in dependencies.v2_derive_methods:
        return "v2"
    return "legacy"


def _run_youtube_analysis(
    job: dict[str, Any],
    run: AnalyzerRun,
    dependencies: GeminiVideoRuntimeDependencies,
) -> dict[str, Any]:
    route = run.route
    analysis_context = _analysis_context(run, dependencies)
    analyzer_started = dependencies.monotonic()
    uses_performance_context = (
        route.derive_method in dependencies.v2_derive_methods
        or route.derive_method in dependencies.final_derive_methods
    )
    raw = dependencies.run_analyzer(
        {
            **run.analyzer_payload,
            "mode": "youtube",
            "url": str(route.evidence.get("content_url") or ""),
            "title": str(route.evidence.get("title") or ""),
            "creator_handle": str(route.evidence.get("creator_handle") or ""),
            "schema_version": _youtube_schema(route.derive_method, dependencies),
            "performance_context": (
                analysis_context if uses_performance_context else None
            ),
        },
        job_id=job.get("id"),
        target_id=route.target_id,
        platform=route.platform,
    )
    run.clock.add("analyzer_subprocess", analyzer_started)
    return raw


def _run_provider_analysis(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    run: AnalyzerRun,
    dependencies: GeminiVideoRuntimeDependencies,
) -> dict[str, Any] | None:
    if run.route.platform in {"instagram", "tiktok"}:
        return _run_local_analysis(conn, job, payload, run, dependencies)
    return _run_youtube_analysis(job, run, dependencies)


def _normalize_selected_model(raw: dict[str, Any]) -> tuple[str, str]:
    selected_model = str(raw.get("selected_model") or raw.get("model") or "").strip()
    provider_model = str(raw.get("provider_reported_model") or "").strip()
    raw["selected_model"] = selected_model or None
    raw["provider_reported_model"] = provider_model or None
    return selected_model, provider_model


def _bind_selected_model(
    raw: dict[str, Any],
    run: AnalyzerRun,
    dependencies: GeminiVideoRuntimeDependencies,
    *,
    selected_model: str,
    provider_model: str,
) -> None:
    raw["llm_execution"] = dependencies.bind_execution_authorization(
        run.authorization,
        selected_model=selected_model,
        provider_reported_model=provider_model,
        model_chain=run.model_chain,
        worker_execution_class=dependencies.worker_execution_class,
        worker_gemini_model=dependencies.worker_model,
    )
    if run.route.derive_method in dependencies.final_derive_methods:
        dependencies.mark_authorization_snapshot_missing(raw)


def _cache_status(
    raw: dict[str, Any],
    derive_method: str,
    dependencies: GeminiVideoRuntimeDependencies,
) -> tuple[str, BaseException | None]:
    if derive_method not in dependencies.final_derive_methods or not raw.get(
        "analyzed"
    ):
        return "ready", None
    try:
        return dependencies.ensure_final_v1_result_cacheable(raw) or "ready", None
    except dependencies.invalid_final_v1_error as exc:
        return "ready", exc


def _raise_analysis_failure(
    conn: Any,
    job: dict[str, Any],
    raw: dict[str, Any],
    run: AnalyzerRun,
    validation_error: BaseException | None,
    dependencies: GeminiVideoRuntimeDependencies,
) -> None:
    if validation_error is not None:
        dependencies.record_diagnostics(
            conn,
            job_id=int(job["id"]),
            raw=raw,
            clock=run.clock,
            platform=run.route.platform,
            error=str(validation_error),
        )
        raise validation_error
    if raw.get("analyzed"):
        return
    raw_error = str(raw.get("error") or "not_analyzed")
    dependencies.record_diagnostics(
        conn,
        job_id=int(job["id"]),
        raw=raw,
        clock=run.clock,
        platform=run.route.platform,
        error=raw_error,
    )
    if raw_error == "gemini_call_timeout":
        raise RuntimeError("gemini_call_timeout")
    raise RuntimeError(f"Gemini video analysis failed: {raw_error}")


def _finalize_provider_result(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
    run: AnalyzerRun,
    raw: dict[str, Any],
    dependencies: GeminiVideoRuntimeDependencies,
) -> FinalizedVideo | None:
    latency_ms = int((dependencies.monotonic() - run.started) * 1000)
    selected_model, provider_model = _normalize_selected_model(raw)
    if not dependencies.scope_checkpoint(
        conn,
        job,
        payload,
        run.route.derive_method,
        provider_calls_performed=True,
        raw=raw,
    ):
        return None
    _bind_selected_model(
        raw,
        run,
        dependencies,
        selected_model=selected_model,
        provider_model=provider_model,
    )
    dependencies.logger.info(
        "apify_jobs gemini video returned | job_id=%s analyzed=%s method=%s latency_ms=%s",
        job.get("id"),
        bool(raw.get("analyzed")),
        raw.get("method"),
        latency_ms,
    )
    cache_status, validation_error = _cache_status(
        raw, run.route.derive_method, dependencies
    )
    cost, cost_basis, tokens_in, tokens_out = dependencies.authoritative_cost(
        raw, preflight_cost
    )
    with run.clock.stage("cost_record"):
        ledger = dependencies.record_cost(
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
    raw["worker_stage_timings_ms"] = dict(run.clock.timings)
    _raise_analysis_failure(
        conn, job, raw, run, validation_error, dependencies
    )
    return FinalizedVideo(
        raw=raw,
        latency_ms=latency_ms,
        cache_status=cache_status,
        cost=cost,
        cost_basis=cost_basis,
        ledger=ledger,
    )



def process_gemini_video(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
    *,
    dependencies: GeminiVideoRuntimeDependencies,
) -> None:
    route = _route_video(
        conn, job, payload, preflight_cost, dependencies
    )
    if route is None:
        return
    run = _build_analyzer_run(job, payload, route, dependencies)
    raw = _run_provider_analysis(conn, job, payload, run, dependencies)
    if raw is None:
        return
    finalized = _finalize_provider_result(
        conn,
        job,
        payload,
        preflight_cost,
        run,
        raw,
        dependencies,
    )
    if finalized is None:
        return
    persist_and_follow_up(
        conn,
        job,
        payload,
        preflight_cost,
        run,
        finalized,
        dependencies,
    )
