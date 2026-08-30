"""Low-complexity orchestration for profile search-session advancement."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


SUPPORTED_ITEM_TYPES = {
    "new_creator",
    "existing_kol",
    "recall_candidate",
    "online_qualified_candidate",
}
TERMINAL_STATUSES = {"ready", "queued", "running", "already_queued", "already_analyzed"}


@dataclass(frozen=True)
class AdvanceDependencies:
    session_store: Any
    profile_url_from_item: Callable[[dict[str, Any]], str]
    plan_profile: Callable[..., dict[str, Any]]
    execute_profile: Callable[..., dict[str, Any]]
    utc_progress_time: Callable[[], str]
    profile_progress_item: Callable[..., dict[str, Any]]
    profile_stage_timing: Callable[..., dict[str, Any]]
    persist_progress: Callable[..., None]
    completion_contract: Callable[..., dict[str, Any]]
    monotonic: Callable[[], float]


@dataclass(frozen=True)
class AdvanceOptions:
    session_id: int
    body: dict[str, Any]
    execute: bool
    pipeline_running: bool
    limit: int
    max_posts: int
    mode: str
    include_completed: bool
    item_ids: set[int]
    allowed_types: set[str]
    smart_local_contract: bool


@dataclass
class AdvanceRun:
    candidates: list[dict[str, Any]]
    selected: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    overflow: int
    items: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    profile_ready: int = 0
    profile_failed: int = 0
    changed_ids: list[int] = field(default_factory=list)
    stage_started_at: str = ""
    stage_started_monotonic: float = 0.0
    last_current_item: dict[str, Any] = field(default_factory=dict)
    last_timing: dict[str, Any] = field(default_factory=dict)


def build_advance_options(
    *, session_id: int, body: dict[str, Any] | None, smart_local_contract: bool, int_value: Callable[..., int], text_value: Callable[[Any], str]
) -> AdvanceOptions:
    payload = body or {}
    limit_cap = 30 if smart_local_contract else 15
    mode = text_value(payload.get("mode") or "profile_only")
    if mode not in {"profile_only", "auto", "profile_with_video", "account_deep"}:
        mode = "profile_only"
    item_ids_raw = payload.get("item_ids")
    allowed_types_raw = payload.get("item_types")
    item_ids = {
        int_value(value) for value in (item_ids_raw if isinstance(item_ids_raw, list) else [])
        if int_value(value) > 0
    }
    allowed_types = {
        text_value(value) for value in (allowed_types_raw if isinstance(allowed_types_raw, list) else [])
        if text_value(value)
    } or set(SUPPORTED_ITEM_TYPES)
    return AdvanceOptions(
        session_id=int(session_id), body=payload, execute=bool(payload.get("execute")),
        pipeline_running=bool(payload.get("_pipeline_running")),
        limit=max(1, min(int_value(payload.get("limit"), 5), limit_cap)),
        max_posts=max(1, min(int_value(payload.get("max_posts"), 12), 12)), mode=mode,
        include_completed=bool(payload.get("include_completed")), item_ids=item_ids,
        allowed_types=allowed_types, smart_local_contract=smart_local_contract,
    )


def _candidate_decision(
    item: dict[str, Any], options: AdvanceOptions, approved_pool_ids: set[int],
    *, int_value: Callable[..., int], text_value: Callable[[Any], str], profile_url_from_item: Callable[[dict[str, Any]], str],
) -> tuple[bool, dict[str, Any] | None]:
    item_id = int_value(item.get("id"))
    item_type = text_value(item.get("item_type"))
    item_status = text_value(item.get("status"))
    if options.item_ids and item_id not in options.item_ids:
        return False, None
    if item_type not in options.allowed_types:
        return False, None
    if item_type not in SUPPORTED_ITEM_TYPES:
        return False, {"item_id": item_id, "status": "skipped", "reason": "unsupported_item_type", "item_type": item_type}
    if item_type == "online_qualified_candidate" and int_value(item.get("kol_pool_id")) not in approved_pool_ids:
        return False, {"item_id": item_id, "status": "skipped", "reason": "approval_required", "item_type": item_type}
    if options.smart_local_contract and item_type != "recall_candidate":
        return False, {"item_id": item_id, "status": "skipped", "reason": "reserved_for_online_lane", "item_type": item_type}
    if not options.include_completed and item_status in TERMINAL_STATUSES:
        return False, {"item_id": item_id, "status": "skipped", "reason": "already_terminal", "item_status": item_status}
    if not profile_url_from_item(item):
        return False, {"item_id": item_id, "status": "skipped", "reason": "missing_profile_url", "item_status": item_status}
    return True, None


def _select_candidates(
    session: dict[str, Any], options: AdvanceOptions, dependencies: AdvanceDependencies,
    *, int_value: Callable[..., int], text_value: Callable[[Any], str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    approved = {int_value(value) for value in (session.get("approved_kol_ids") or []) if int_value(value) > 0}
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in session.get("items") or []:
        eligible, reason = _candidate_decision(
            item, options, approved, int_value=int_value, text_value=text_value,
            profile_url_from_item=dependencies.profile_url_from_item,
        )
        if eligible:
            candidates.append(item)
        elif reason is not None:
            skipped.append(reason)
    return candidates, skipped


def _new_run(
    candidates: list[dict[str, Any]], skipped: list[dict[str, Any]], options: AdvanceOptions,
    dependencies: AdvanceDependencies,
) -> AdvanceRun:
    selected = candidates[:options.limit]
    started_at = dependencies.utc_progress_time()
    started_monotonic = dependencies.monotonic()
    return AdvanceRun(
        candidates=candidates, selected=selected, skipped=skipped,
        overflow=max(0, len(candidates) - len(selected)),
        counts={"planned": 0, "executed": 0, "ready": 0, "partial": 0, "failed": 0, "skipped": len(skipped), "errors": 0},
        stage_started_at=started_at, stage_started_monotonic=started_monotonic,
        last_timing={
            "stage_started_at": started_at, "stage_updated_at": started_at,
            "stage_finished_at": None, "stage_elapsed_ms": 0,
            "current_item_started_at": None, "current_item_finished_at": None,
            "current_item_elapsed_ms": 0,
        },
    )


def _append_execution_result(
    run: AdvanceRun, item_id: int, options: AdvanceOptions, dependencies: AdvanceDependencies,
    *, int_value: Callable[..., int], text_value: Callable[[Any], str],
) -> tuple[str, str]:
    result = dependencies.execute_profile(
        session_id=options.session_id, item_id=item_id,
        body={**options.body, "execute": True, "max_posts": options.max_posts, "mode": options.mode},
    )
    run.counts["executed"] += 1
    status = text_value(result.get("status")).lower() or "unknown"
    profile_status = text_value(result.get("profile_status") or status).lower()
    if profile_status in {"ready", "already_analyzed"}:
        run.profile_ready += 1
    elif "failed" in profile_status or profile_status == "error":
        run.profile_failed += 1
    _count_execution_status(run.counts, status)
    for changed_id in result.get("viltrox_fit_score_changed_ids") or []:
        parsed = int_value(changed_id)
        if parsed > 0 and parsed not in run.changed_ids:
            run.changed_ids.append(parsed)
    run.items.append({"item_id": item_id, "status": status, "result": result})
    return status, profile_status


def _count_execution_status(counts: dict[str, int], status: str) -> None:
    if status == "ready":
        counts["ready"] += 1
    elif status in {"failed", "crawl_failed", "profile_crawl_failed"} or "failed" in status:
        counts["failed"] += 1
    else:
        counts["partial"] += 1


def _checkpoint_item(
    run: AdvanceRun, item: dict[str, Any], item_id: int, status: str, profile_status: str,
    item_started_at: str, item_started_monotonic: float, options: AdvanceOptions,
    dependencies: AdvanceDependencies,
) -> None:
    item_finished_at = dependencies.utc_progress_time()
    run.last_current_item = dependencies.profile_progress_item(
        item, item_id=item_id, status=status, profile_status=profile_status,
    )
    run.last_timing = dependencies.profile_stage_timing(
        stage_started_at=run.stage_started_at,
        stage_started_monotonic=run.stage_started_monotonic,
        item_started_at=item_started_at, item_started_monotonic=item_started_monotonic,
        item_finished_at=item_finished_at,
    )
    dependencies.persist_progress(
        session_id=options.session_id, mode=options.mode, limit=options.limit,
        base_count=len(run.candidates), selected_count=len(run.selected), overflow=run.overflow,
        counts=run.counts, completed_count=len(run.items), profile_ready=run.profile_ready,
        profile_failed=run.profile_failed, current_item=run.last_current_item,
        timing=run.last_timing, pipeline_running=options.pipeline_running,
    )


def _advance_item(
    run: AdvanceRun, item: dict[str, Any], options: AdvanceOptions, dependencies: AdvanceDependencies,
    *, int_value: Callable[..., int], text_value: Callable[[Any], str],
) -> None:
    item_id = int_value(item.get("id"))
    item_started_at = dependencies.utc_progress_time()
    item_started_monotonic = dependencies.monotonic()
    status = "unknown"
    profile_status = "unknown"
    try:
        if not options.execute:
            plan = dependencies.plan_profile(
                session_id=options.session_id, item_id=item_id,
                max_posts=options.max_posts, mode=options.mode,
            )
            run.counts["planned"] += 1
            run.items.append({"item_id": item_id, "status": "planned", "plan": plan})
        else:
            status, profile_status = _append_execution_result(
                run, item_id, options, dependencies, int_value=int_value, text_value=text_value,
            )
    except Exception:
        run.counts["errors"] += 1
        run.profile_failed += 1
        status, profile_status = "error", "failed"
        run.items.append({"item_id": item_id, "status": "error", "reason": "profile_crawl_failed"})
    if options.execute:
        _checkpoint_item(
            run, item, item_id, status, profile_status, item_started_at,
            item_started_monotonic, options, dependencies,
        )


def _append_over_limit_skips(
    run: AdvanceRun, options: AdvanceOptions, *, int_value: Callable[..., int], text_value: Callable[[Any], str],
) -> None:
    run.skipped.extend(
        {"item_id": int_value(item.get("id")), "status": "skipped", "reason": "over_limit", "item_status": text_value(item.get("status"))}
        for item in run.candidates[options.limit:]
    )
    run.counts["skipped"] = len(run.skipped)


def _batch_status(run: AdvanceRun, execute: bool) -> str:
    if not execute:
        return "planned"
    if not run.selected:
        return "partial"
    if run.counts["failed"] or run.counts["errors"]:
        return "partial" if run.counts["ready"] or run.counts["partial"] else "failed"
    if run.counts["partial"] or run.counts["ready"] != len(run.selected):
        return "partial"
    return "ready"


def _final_contract(run: AdvanceRun, options: AdvanceOptions, dependencies: AdvanceDependencies) -> dict[str, Any]:
    return dependencies.completion_contract(
        base_count=len(run.candidates), total=len(run.selected), terminal_count=len(run.items),
        ready_count=run.profile_ready, profile_failed=run.profile_failed, active_tasks=0,
        stage_progress=None, requested_tasks_terminal=False if options.pipeline_running else None,
    )


def _finish_timing(run: AdvanceRun, dependencies: AdvanceDependencies) -> None:
    finished_at = dependencies.utc_progress_time()
    run.last_timing = {
        **run.last_timing, "stage_updated_at": finished_at, "stage_finished_at": finished_at,
        "stage_elapsed_ms": max(0, int((dependencies.monotonic() - run.stage_started_monotonic) * 1000)),
    }


def _progress_summary(run: AdvanceRun, contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "base": len(run.candidates), "total": len(run.selected),
        "profile_ready": run.profile_ready, "profile_failed": run.profile_failed,
        "profile_completed": len(run.items),
        "profile_succeeded": max(0, len(run.items) - run.profile_failed),
        "profile_remaining": max(0, len(run.selected) - len(run.items)),
        "complete_ready": int(run.counts.get("ready") or 0),
        "complete_partial": int(run.counts.get("partial") or 0),
        "current_item": dict(run.last_current_item), "stage_timing": dict(run.last_timing),
        **contract,
    }


def _persist_final_summary(
    run: AdvanceRun, options: AdvanceOptions, dependencies: AdvanceDependencies, batch_status: str,
) -> None:
    _finish_timing(run, dependencies)
    contract = _final_contract(run, options, dependencies)
    dependencies.session_store.update_session_result_summary(
        options.session_id, status="running" if options.pipeline_running else batch_status,
        summary_patch={
            "phase": "profile" if options.pipeline_running else ("complete" if batch_status == "ready" else "partial"),
            "progress": _progress_summary(run, contract), **contract,
            "profile_batch_advance": {
                "status": batch_status, "mode": options.mode, "limit": options.limit,
                "selected": len(run.selected), "overflow": run.overflow, "completed": len(run.items),
                "succeeded": max(0, len(run.items) - run.profile_failed), "failed": run.profile_failed,
                "counts": dict(run.counts), "current_item": dict(run.last_current_item),
                "timing": dict(run.last_timing), "viltrox_fit_score_changed_ids": run.changed_ids,
                "viltrox_fit_score_untouched": not run.changed_ids,
            },
        },
    )


def _result(run: AdvanceRun, options: AdvanceOptions, batch_status: str) -> dict[str, Any]:
    has_writes = options.execute and bool(run.selected)
    return {
        "status": batch_status, "execute": options.execute, "session_id": options.session_id,
        "mode": options.mode, "limit": options.limit, "selected": len(run.selected),
        "eligible": len(run.candidates), "overflow": run.overflow, "counts": run.counts,
        "items": run.items, "skipped": run.skipped[:max(0, 50 - len(run.items))],
        "viltrox_fit_score_changed_ids": run.changed_ids,
        "viltrox_fit_score_untouched": not run.changed_ids,
        "provider_calls_performed": has_writes, "write_db": has_writes,
        "writes": ["vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs", "vkpi_kol_search_sessions", "vkpi_kol_search_session_items"] if has_writes else [],
    }


def advance_session_items(
    *, options: AdvanceOptions, dependencies: AdvanceDependencies,
    int_value: Callable[..., int], text_value: Callable[[Any], str],
) -> dict[str, Any]:
    session = dependencies.session_store.get_session(options.session_id)
    candidates, skipped = _select_candidates(
        session, options, dependencies, int_value=int_value, text_value=text_value,
    )
    run = _new_run(candidates, skipped, options, dependencies)
    for item in run.selected:
        _advance_item(
            run, item, options, dependencies, int_value=int_value, text_value=text_value,
        )
    _append_over_limit_skips(run, options, int_value=int_value, text_value=text_value)
    batch_status = _batch_status(run, options.execute)
    if options.execute:
        _persist_final_summary(run, options, dependencies, batch_status)
    return _result(run, options, batch_status)
