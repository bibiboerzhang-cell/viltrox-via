"""Pure stage and aggregate projection helpers for KOL search progress.

This module performs no I/O.  The public contract and worker-health observation
boundary remain in :mod:`search_progress_contract`.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


FULL_ANALYSIS_ROLES = ("video", "comments", "audience")

_SUCCESS_STATES = frozenset({"ready", "done", "ok", "already_analyzed", "recently_done"})
_QUEUED_STATES = frozenset({"queued", "pending", "already_queued", "waiting_for_evidence", "waiting_for_profile"})
_RUNNING_STATES = frozenset({"running", "processing", "retrying", "already_running"})
_ACTIVE_STATES = frozenset({"active"})
_FAILED_STATES = frozenset({"failed", "error", "blocked", "triage", "crawl_failed", "unsupported"})
_PARTIAL_STATES = frozenset({"partial", "empty", "no_data", "no_posts", "no_comments", "not_found"})
_SKIPPED_STATES = frozenset({"skipped", "cancelled", "canceled"})
_TERMINAL_BUCKETS = ("ready", "partial", "failed", "skipped")
# 编排挂起(2026-08-22 会话 1106 案):召回项先到、全网发现/档案批次尚未登记的窗口里,按会话项
# 证据投影会得到 30/30 ready → 前端判终态停轮询,一分多钟后才落库的发现项再也没被取走
# (「搜索完成后新发现区不显示」真因)。编排器在 result_summary.progress 里显式写了
# requested_tasks_terminal=False(「后面还会登记任务」),且会话仍 queued/running —— 这是
# 管线自己落的持久证据,不是杜撰:契约在此期间必须报 running,而非 ready。
_ORCHESTRATION_PENDING_SESSION_STATES = frozenset({"queued", "running"})
_TERMINAL_SESSION_STATES = frozenset({"ready", "partial", "failed", "cancelled", "canceled"})


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _state_bucket(value: Any) -> str:
    state = _text(value)
    if state in _SUCCESS_STATES:
        return "ready"
    if state in _QUEUED_STATES:
        return "queued"
    if state in _RUNNING_STATES:
        return "running"
    if state in _ACTIVE_STATES:
        # Lineage reducers intentionally collapse queued/running/retrying into
        # ``active``.  Keep that ambiguity instead of inventing a worker claim.
        return "active"
    if state in _FAILED_STATES or "failed" in state:
        return "failed"
    if state in _PARTIAL_STATES:
        return "partial"
    if state in _SKIPPED_STATES:
        return "skipped"
    return "not_requested"


def _terminal_shortfall_bucket(session_status: str) -> str:
    """Classify missing requested rows without erasing terminal cause."""
    if session_status in {"ready", "partial"}:
        return "partial"
    if session_status == "failed":
        return "failed"
    if session_status in {"cancelled", "canceled"}:
        return "skipped"
    return "unknown"


def _has_job_reference(*records: Mapping[str, Any]) -> bool:
    for record in records:
        if _positive_int(record.get("job_id")):
            return True
        job_ids = record.get("job_ids")
        if isinstance(job_ids, Sequence) and not isinstance(job_ids, (str, bytes)) and any(_positive_int(job_id) for job_id in job_ids):
            return True
    return False


def _queue_truth(*records: Mapping[str, Any]) -> str:
    buckets = tuple(_state_bucket(record.get("queue_status")) for record in records)
    registered = tuple(_state_bucket(record.get("status")) for record, queue in zip(records, buckets) if queue == "not_requested" and _has_job_reference(record))
    for candidates in (buckets, registered):
        active = next((state for state in ("running", "active", "queued") if state in candidates), "")
        if active:
            return active
    return next((state for state in reversed(buckets) if state != "not_requested"), "not_requested")


def _terminal_job_bucket(job_bucket: str, *data_buckets: str) -> str:
    return job_bucket if job_bucket != "ready" else next((bucket for bucket in data_buckets if bucket in _TERMINAL_BUCKETS), "partial")


def _profile_bucket(item: Mapping[str, Any], *, session_status: str = "") -> str:
    payload = _mapping(item.get("payload"))
    flow, execute = _mapping(payload.get("profile_flow")), _mapping(payload.get("profile_execute"))
    profile = {**flow, **execute}
    advance_job = _mapping(payload.get("profile_advance_job"))
    explicit, advance = _state_bucket(profile.get("status")), _state_bucket(advance_job.get("status"))
    concrete = _queue_truth(flow, execute, advance_job)
    stage = _text(item.get("stage"))
    item_bucket = _state_bucket(item.get("status")) if stage in {"profile", "evidence", "analysis", "summary"} else "not_requested"
    active = {"queued", "running", "active"}
    if concrete in active:  # any refreshed apify_jobs activity wins, including retry
        return concrete
    if concrete in _TERMINAL_BUCKETS:
        default = "partial" if concrete == "ready" else concrete
        return next((state for state in (item_bucket, explicit) if state in _TERMINAL_BUCKETS), default)
    if explicit in active:
        if advance in active and _has_job_reference(advance_job):
            return advance
        if advance in _TERMINAL_BUCKETS:
            return advance
        if item_bucket in _TERMINAL_BUCKETS:
            return item_bucket
        terminal = _terminal_shortfall_bucket(_text(session_status))
        if terminal != "unknown":  # closes legacy 965/898 stale profile_flow
            return terminal
        return next((state for state in ("running", "active", "queued") if state in {explicit, item_bucket}), explicit)
    if explicit != "not_requested":
        return explicit
    if advance in active and not _has_job_reference(advance_job):
        terminal = _terminal_shortfall_bucket(_text(session_status))
        return advance if terminal == "unknown" else terminal
    return advance if advance != "not_requested" else item_bucket


def _downstream_bucket(item: Mapping[str, Any], role: str, *, session_status: str = "") -> str:
    payload = _mapping(item.get("payload"))
    downstream = _mapping(_mapping(payload.get("downstream_jobs")).get(role))
    if role == "audience":
        flow, execute = _mapping(payload.get("profile_flow")), _mapping(payload.get("profile_execute"))
        enrichments = tuple(_mapping(profile.get("audience_enrichment")) for profile in (flow, execute))
        enrichment = {**enrichments[0], **enrichments[1]}
        preview = _mapping(payload.get("audience_preview"))
        preview_bucket = _state_bucket(preview.get("status"))
        enrichment_bucket = _state_bucket(enrichment.get("status"))
        queue_bucket = _queue_truth(*enrichments)
        downstream_bucket = _state_bucket(downstream.get("state"))
        has_concrete_job = _has_job_reference(*enrichments, downstream)
        if queue_bucket in {"queued", "running", "active"}:
            return queue_bucket
        if preview_bucket == "ready":
            return "ready"
        if queue_bucket != "not_requested":
            return _terminal_job_bucket(queue_bucket, enrichment_bucket, preview_bucket)
        synthetic_active = any(bucket in {"queued", "running", "active"} for bucket in (enrichment_bucket, preview_bucket, downstream_bucket))
        if (
            _text(session_status) in _TERMINAL_SESSION_STATES
            and synthetic_active
            and not has_concrete_job
        ):
            return "skipped"
        if downstream_bucket != "not_requested":
            return _terminal_job_bucket(downstream_bucket, enrichment_bucket, preview_bucket)
        if enrichment_bucket in {"queued", "running", "active"} and not has_concrete_job:
            profile_bucket = _profile_bucket(item, session_status=session_status)
            if profile_bucket in {"partial", "failed", "skipped"}:
                return "skipped"
        if enrichment_bucket != "not_requested":
            return enrichment_bucket
        if preview_bucket != "not_requested":
            return preview_bucket
    explicit = _state_bucket(downstream.get("state"))
    if _text(session_status) in _TERMINAL_SESSION_STATES and explicit in {"queued", "running", "active"} and not _has_job_reference(downstream):
        return "skipped"  # unregistered planner marker cannot outlive session
    if explicit != "not_requested":
        return explicit
    if role == "video" and _mapping(payload.get("analysis")):
        return "ready"
    return "not_requested"


def _stage_projection(
    key: str,
    buckets: Sequence[str],
    *,
    population: int,
    data_ready: int | None = None,
) -> dict[str, Any]:
    counts = {
        bucket: sum(1 for value in buckets if value == bucket)
        for bucket in (
            "ready",
            "queued",
            "running",
            "active",
            "partial",
            "failed",
            "skipped",
            "not_requested",
            "unknown",
        )
    }
    requested = max(0, len(buckets) - counts["not_requested"])
    terminal = sum(counts[bucket] for bucket in _TERMINAL_BUCKETS)
    successful = counts["ready"]
    if requested <= 0:
        state = "not_requested"
    elif counts["running"]:
        state = "running"
    elif counts["active"]:
        state = "active"
    elif counts["queued"]:
        state = "queued"
    elif counts["failed"] or counts["partial"]:
        state = "partial"
    elif terminal >= requested and successful >= requested:
        state = "ready"
    elif terminal >= requested:
        state = "partial"
    else:
        state = "pending"
    return {
        "key": key,
        "population": max(0, int(population or 0)),
        "requested": requested,
        "successful": successful,
        "terminal": terminal,
        "remaining": max(0, requested - terminal),
        "success_pct": round(successful * 100 / requested, 1) if requested else None,
        "terminal_pct": round(terminal * 100 / requested, 1) if requested else None,
        "state": state,
        "counts": counts,
        "data_ready": data_ready,
        "data_ready_basis": "durable_field_evidence" if data_ready is not None else "not_observable_from_session",
    }


def _profile_data_ready(item: Mapping[str, Any], bucket: str) -> bool:
    if bucket != "ready":
        return False
    payload = _mapping(item.get("payload"))
    profile = {**_mapping(payload.get("profile_flow")), **_mapping(payload.get("profile_execute"))}
    return bool(item.get("kol_pool_id") or profile.get("kol_pool_id") or profile.get("profile_data"))


def _video_data_ready(item: Mapping[str, Any], bucket: str) -> bool:
    if bucket != "ready":
        return False
    payload = _mapping(item.get("payload"))
    video_flow = _mapping(payload.get("video_flow"))
    return bool(item.get("evidence_id") or _mapping(payload.get("analysis")) or video_flow.get("evidence_id"))


def _audience_data_ready(item: Mapping[str, Any], bucket: str) -> bool:
    if bucket != "ready":
        return False
    payload = _mapping(item.get("payload"))
    return _text(_mapping(payload.get("audience_preview")).get("status")) == "ready"


def _orchestration_pending(session: Mapping[str, Any], stored_progress: Mapping[str, Any]) -> bool:
    """编排器是否仍会登记更多任务(会话项证据之外的持久声明)。

    仅当两者同时成立才为 True:① 会话行状态仍是 queued/running(管线在跑);② 编排器在
    progress 里显式写了 requested_tasks_terminal=False(布尔 False,缺省/None 不算)。
    终态会话(ready/partial/failed)即便仍带 False 也不挂起——那是「下游任务另行登记」的
    旧语义,由会话项自身的 queued/running 证据接管。
    """
    session_status = _text(session.get("status"))
    if session_status not in _ORCHESTRATION_PENDING_SESSION_STATES:
        return False
    return stored_progress.get("requested_tasks_terminal") is False


def _project_progress_stages(
    session: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    list[Mapping[str, Any]],
    str,
    dict[str, dict[str, Any]],
]:
    summary = _mapping(session.get("result_summary"))
    stored_progress = _mapping(summary.get("progress"))
    safe_items = [item for item in items if isinstance(item, Mapping)]
    raw_session_status = _text(session.get("status"))
    intended_total = max(
        len(safe_items),
        _positive_int(stored_progress.get("total")),
        _positive_int(stored_progress.get("base")),
    )
    base_buckets = ["ready"] * min(len(safe_items), intended_total)
    # A terminal search can legitimately return fewer rows than its requested
    # target after strict filtering/provider shortfall.  Those absent rows are
    # a terminal partial result, not work that is still pending.  Keeping them
    # as ``unknown`` makes a 26/30 terminal session poll for twelve minutes even
    # though there is no queued/running unit left.  Active sessions retain the
    # unknown bucket so the short orchestration-registration window stays open.
    shortfall_bucket = _terminal_shortfall_bucket(raw_session_status)
    base_buckets.extend([shortfall_bucket] * max(0, intended_total - len(base_buckets)))

    profile_buckets = [
        _profile_bucket(item, session_status=raw_session_status)
        for item in safe_items
    ]
    downstream_buckets = {
        role: [
            _downstream_bucket(item, role, session_status=raw_session_status)
            for item in safe_items
        ]
        for role in FULL_ANALYSIS_ROLES
    }
    stages = {
        "search": _stage_projection(
            "search",
            base_buckets,
            population=intended_total,
            data_ready=len(safe_items),
        ),
        "profile": _stage_projection(
            "profile",
            profile_buckets,
            population=len(safe_items),
            data_ready=sum(
                _profile_data_ready(item, bucket)
                for item, bucket in zip(safe_items, profile_buckets)
            ),
        ),
        "video": _stage_projection(
            "video",
            downstream_buckets["video"],
            population=len(safe_items),
            data_ready=sum(
                _video_data_ready(item, bucket)
                for item, bucket in zip(safe_items, downstream_buckets["video"])
            ),
        ),
        # The session lineage proves the comments job finished, but the compact
        # session payload does not prove how many usable comments materialized.
        "comments": _stage_projection(
            "comments",
            downstream_buckets["comments"],
            population=len(safe_items),
            data_ready=None,
        ),
        "audience": _stage_projection(
            "audience",
            downstream_buckets["audience"],
            population=len(safe_items),
            data_ready=sum(
                _audience_data_ready(item, bucket)
                for item, bucket in zip(safe_items, downstream_buckets["audience"])
            ),
        ),
    }
    return summary, stored_progress, safe_items, raw_session_status, stages


def _progress_unit_totals(
    stages: Mapping[str, Mapping[str, Any]],
) -> tuple[int, int, int, int, int, int, int]:
    requested_units = sum(int(stage["requested"] or 0) for stage in stages.values())
    successful_units = sum(int(stage["successful"] or 0) for stage in stages.values())
    terminal_units = sum(int(stage["terminal"] or 0) for stage in stages.values())
    queued_units = sum(int(stage["counts"]["queued"] or 0) for stage in stages.values())
    running_units = sum(int(stage["counts"]["running"] or 0) for stage in stages.values())
    active_units = sum(int(stage["counts"]["active"] or 0) for stage in stages.values())
    failed_units = sum(
        int(stage["counts"]["failed"] or 0) + int(stage["counts"]["partial"] or 0)
        for stage in stages.values()
    )
    return (
        requested_units,
        successful_units,
        terminal_units,
        queued_units,
        running_units,
        active_units,
        failed_units,
    )


def _requested_progress_outcomes(
    *,
    orchestration_pending: bool,
    active_units_total: int,
    requested_units: int,
    terminal_units: int,
    successful_units: int,
    failed_units: int,
    raw_session_status: str,
) -> tuple[bool, bool]:
    # Requested-unit totals are the strongest read-time evidence.  Empty-result
    # sessions are the important exception: there are deliberately no units to
    # count, so the durable terminal session status is the closure evidence.
    requested_tasks_terminal = bool(
        not orchestration_pending
        and active_units_total == 0
        and (
            (requested_units > 0 and terminal_units >= requested_units)
            or (requested_units == 0 and raw_session_status in _TERMINAL_SESSION_STATES)
        )
    )
    requested_tasks_successful = bool(
        requested_tasks_terminal
        and (
            (requested_units > 0 and successful_units >= requested_units and failed_units == 0)
            or (requested_units == 0 and raw_session_status == "ready")
        )
    )
    return requested_tasks_terminal, requested_tasks_successful


def _aggregate_progress_state(
    session: Mapping[str, Any],
    *,
    blocked_by_worker: bool,
    running_units: int,
    orchestration_pending: bool,
    active_units: int,
    queued_units: int,
    failed_units: int,
    requested_units: int,
    successful_units: int,
    raw_session_status: str,
) -> str:
    if blocked_by_worker:
        return "blocked_by_worker"
    if running_units:
        return "running"
    if orchestration_pending:
        # 管线在跑(running)= 编排器本身就是活跃执行体;仍在队列(queued)= 尚未开跑。
        return "running" if _text(session.get("status")) == "running" else "queued"
    if active_units:
        return "active"
    if queued_units:
        return "queued"
    if failed_units:
        # Preserve a total failure as failed.  If any requested unit did
        # succeed, the aggregate is truthfully partial instead.
        return "failed" if raw_session_status == "failed" and successful_units == 0 else "partial"
    if requested_units and successful_units >= requested_units:
        return "ready"
    if raw_session_status in _TERMINAL_SESSION_STATES:
        # A partial/failed session with zero materialized rows is still a
        # terminal empty result.  Calling it "planned" made historical searches
        # look as if they were waiting forever even though no task remained.
        return raw_session_status
    return "planned"


def _full_analysis_flags(
    stages: Mapping[str, Mapping[str, Any]],
    *,
    item_count: int,
) -> tuple[bool, bool, bool]:
    full_analysis_execution_complete = bool(
        item_count
        and all(
            stages[role]["requested"] == item_count
            and stages[role]["successful"] == item_count
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    full_analysis_observable = bool(
        item_count
        and all(
            stages[role]["data_ready"] is not None
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    full_analysis_complete = bool(
        full_analysis_execution_complete
        and full_analysis_observable
        and all(
            int(stages[role]["data_ready"] or 0) == item_count
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    return (
        full_analysis_execution_complete,
        full_analysis_observable,
        full_analysis_complete,
    )


def _completion_kind(
    *,
    blocked_by_worker: bool,
    orchestration_pending: bool,
    active_units_total: int,
    full_analysis_complete: bool,
    empty_result: bool,
    requested_tasks_successful: bool,
    requested_tasks_terminal: bool,
) -> str:
    if blocked_by_worker:
        return "blocked_by_worker"
    if orchestration_pending or active_units_total > 0:
        return "active"
    if full_analysis_complete:
        return "full_analysis"
    if empty_result:
        return "empty_result"
    if requested_tasks_successful:
        return "requested_stages"
    if requested_tasks_terminal:
        return "partial"
    return "planned"
