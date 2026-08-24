"""Truthful progress semantics for progressive KOL search sessions.

``complete`` and ``required_tasks_complete`` are retained as compatibility
aliases for "every requested task has reached a terminal state".  They are not
evidence that the optional full-analysis pipeline ran.  The strict
``full_analysis_complete`` flag additionally requires observable, durable data
for every profile/video/comments/audience stage; a finished job alone is not a
completed analysis.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


FULL_ANALYSIS_ROLES = ("video", "comments", "audience")
PROGRESS_CONTRACT_SCHEMA = "kol_search_progress_v1"
PROGRESS_STAGE_KEYS = ("search", "profile", *FULL_ANALYSIS_ROLES)

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
_HEARTBEAT_WINDOW_SECONDS = 120
_EXACT_RELEASE_SHA = re.compile(r"^[0-9a-f]{40}$")
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _count(stage: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(stage.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def completion_contract(
    *,
    base_count: int,
    total: int,
    terminal_count: int,
    ready_count: int,
    profile_failed: int = 0,
    active_tasks: int = 0,
    stage_progress: Mapping[str, Mapping[str, Any]] | None = None,
    requested_tasks_terminal: bool | None = None,
) -> dict[str, bool]:
    """Return backward-compatible and strict progressive-completion flags.

    ``requested_tasks_terminal`` may be supplied by an orchestrator that knows
    more work will be registered after the current profile batch.  Otherwise it
    is derived from terminal item counts and active tasks.
    """

    safe_total = max(0, int(total or 0))
    safe_base = max(0, int(base_count or 0))
    safe_terminal = max(0, int(terminal_count or 0))
    safe_ready = max(0, int(ready_count or 0))
    safe_profile_failed = max(0, int(profile_failed or 0))
    safe_active = max(0, int(active_tasks or 0))

    base_complete = safe_total > 0 and safe_base >= safe_total
    terminal = (
        bool(requested_tasks_terminal)
        if requested_tasks_terminal is not None
        else safe_total > 0 and safe_terminal >= safe_total and safe_active == 0
    )

    stages = stage_progress if isinstance(stage_progress, Mapping) else None
    full_analysis_execution_complete = bool(
        terminal
        and stages is not None
        and safe_total > 0
        and safe_ready >= safe_total
        and safe_profile_failed == 0
    )
    if full_analysis_execution_complete:
        for role in FULL_ANALYSIS_ROLES:
            raw_stage = stages.get(role)
            if not isinstance(raw_stage, Mapping):
                full_analysis_execution_complete = False
                break
            if (
                _count(raw_stage, "ready") < safe_total
                or _count(raw_stage, "active") > 0
                or _count(raw_stage, "failed") > 0
                or _count(raw_stage, "not_requested") > 0
            ):
                full_analysis_execution_complete = False
                break

    full_analysis_observable = bool(
        stages is not None
        and safe_total > 0
        and all(
            isinstance(stages.get(role), Mapping)
            and stages[role].get("data_ready") is not None
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    full_analysis_complete = bool(
        full_analysis_execution_complete
        and full_analysis_observable
        and all(
            _count(stages[role], "data_ready") >= safe_total
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    decision_eligible = bool(
        full_analysis_complete
        and safe_profile_failed == 0
        and safe_ready >= safe_total
    )
    return {
        "base_complete": base_complete,
        "requested_tasks_terminal": terminal,
        "full_analysis_execution_complete": full_analysis_execution_complete,
        "full_analysis_observable": full_analysis_observable,
        "full_analysis_complete": full_analysis_complete,
        "decision_eligible": decision_eligible,
        # Compatibility aliases.  They intentionally retain terminal—not full
        # analysis—semantics for old clients.
        "required_tasks_complete": terminal,
        "complete": terminal,
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _expected_worker_count() -> int | None:
    raw = str(os.getenv("APIFY_WORKER_EXPECTED_INSTANCES", "") or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def _exact_release_sha(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if _EXACT_RELEASE_SHA.fullmatch(normalized) else None


def _observed_app_release_sha() -> tuple[str | None, str]:
    """Read only explicit, sealed release identity; never infer it from git."""

    env_sha = _exact_release_sha(os.getenv("APP_GIT_SHA"))
    if env_sha:
        return env_sha, "env:APP_GIT_SHA"
    try:
        build_sha = _exact_release_sha((_PROJECT_ROOT / "BUILD_GIT_SHA").read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        build_sha = None
    if build_sha:
        return build_sha, "build_file:BUILD_GIT_SHA"
    return None, "unavailable"


def unobserved_worker_health(
    *,
    reason: str = "heartbeat_not_read",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    release_sha, release_sha_source = _observed_app_release_sha()
    return {
        "observed": False,
        "source": "vkpi_worker_heartbeat",
        "state": "unknown",
        "online": None,
        "online_count": None,
        "expected_count": _expected_worker_count(),
        "capacity_ready": None,
        "release_sha": release_sha,
        "release_sha_source": release_sha_source,
        "worker_sha": None,
        "worker_shas": [],
        "sha_aligned": None,
        "latest_heartbeat_at": None,
        "observed_at": _iso(now),
        "reason": reason,
    }


def observe_worker_health(
    conn: Any,
    *,
    now: datetime | None = None,
    expected_count: int | None = None,
) -> dict[str, Any]:
    """Read the durable Apify-worker heartbeat table without guessing liveness.

    A missing table/read failure is ``unknown`` rather than ``offline``.  Redis
    workers are excluded because they do not consume the ``apify_jobs`` lanes
    used by KOL profile/video/comment/audience work.
    """

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expected = expected_count if expected_count is not None else _expected_worker_count()
    release_sha, release_sha_source = _observed_app_release_sha()
    try:
        rows = conn.execute(
            """
            SELECT worker_name, last_heartbeat_at, worker_git_sha
            FROM vkpi_worker_heartbeat
            WHERE last_heartbeat_at IS NOT NULL
              AND worker_name NOT LIKE ?
            ORDER BY last_heartbeat_at DESC
            LIMIT 64
            """,
            ("redis-worker-%",),
        ).fetchall()
    except Exception:
        return {
            "observed": False,
            "source": "vkpi_worker_heartbeat",
            "state": "unknown",
            "online": None,
            "online_count": None,
            "expected_count": expected,
            "capacity_ready": None,
            "release_sha": release_sha,
            "release_sha_source": release_sha_source,
            "worker_sha": None,
            "worker_shas": [],
            "sha_aligned": None,
            "latest_heartbeat_at": None,
            "observed_at": _iso(observed_at),
            "reason": "heartbeat_unavailable",
        }

    parsed_rows: list[dict[str, Any]] = []
    for raw in rows or []:
        row = dict(raw)
        heartbeat = _utc(row.get("last_heartbeat_at"))
        age = (observed_at - heartbeat).total_seconds() if heartbeat else None
        parsed_rows.append(
            {
                "heartbeat": heartbeat,
                "online": bool(age is not None and -30 <= age <= _HEARTBEAT_WINDOW_SECONDS),
                "worker_sha": _exact_release_sha(row.get("worker_git_sha")),
            }
        )
    online_rows = [row for row in parsed_rows if row["online"]]
    online_count = len(online_rows)
    worker_shas = sorted(
        {str(row["worker_sha"]) for row in online_rows if row.get("worker_sha")}
    )
    worker_sha = worker_shas[0] if len(worker_shas) == 1 else None
    sha_aligned: bool | None = None
    if release_sha and online_rows:
        sha_aligned = bool(
            len(worker_shas) == 1
            and worker_sha == release_sha
            and all(row.get("worker_sha") == release_sha for row in online_rows)
        )
    latest = max((row["heartbeat"] for row in parsed_rows if row["heartbeat"]), default=None)
    count_ready = bool(online_count > 0 and (expected is None or online_count >= expected))
    capacity_ready = bool(count_ready and sha_aligned is not False)
    if online_count <= 0:
        state = "offline"
    elif expected is not None and online_count < expected:
        state = "under_capacity"
    elif sha_aligned is False:
        state = "release_mismatch"
    else:
        state = "online"
    reason = {
        "offline": "no_fresh_apify_worker_heartbeat",
        "under_capacity": "worker_count_below_expected",
        "release_mismatch": "worker_release_sha_mismatch",
        "online": "fresh_heartbeat",
    }[state]
    return {
        "observed": True,
        "source": "vkpi_worker_heartbeat",
        "state": state,
        "online": bool(online_count > 0),
        "online_count": online_count,
        "expected_count": expected,
        "capacity_ready": capacity_ready,
        "release_sha": release_sha,
        "release_sha_source": release_sha_source,
        "worker_sha": worker_sha,
        "worker_shas": worker_shas,
        "sha_aligned": sha_aligned,
        "latest_heartbeat_at": _iso(latest),
        "observed_at": _iso(observed_at),
        "reason": reason,
    }


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
    """Preserve why a terminal search stopped when requested rows are absent.

    A ready/partial provider shortfall is a partial result.  Failed work remains
    failed, while user/system cancellation is skipped work.  Collapsing all
    three into ``partial`` made failed and cancelled 0/N sessions look like
    ordinary provider underfill and hid their actual terminal reason.
    """

    if session_status in {"ready", "partial"}:
        return "partial"
    if session_status == "failed":
        return "failed"
    if session_status in {"cancelled", "canceled"}:
        return "skipped"
    return "unknown"


def _profile_bucket(item: Mapping[str, Any]) -> str:
    payload = _mapping(item.get("payload"))
    profile = {**_mapping(payload.get("profile_flow")), **_mapping(payload.get("profile_execute"))}
    explicit = _state_bucket(profile.get("status"))
    if explicit != "not_requested":
        return explicit
    advance_job = _mapping(payload.get("profile_advance_job"))
    queued = _state_bucket(advance_job.get("status"))
    if queued != "not_requested":
        return queued
    stage = _text(item.get("stage"))
    if stage in {"profile", "evidence", "analysis", "summary"}:
        return _state_bucket(item.get("status"))
    return "not_requested"


def _downstream_bucket(item: Mapping[str, Any], role: str) -> str:
    payload = _mapping(item.get("payload"))
    downstream = _mapping(_mapping(payload.get("downstream_jobs")).get(role))
    if role == "audience":
        profile = {**_mapping(payload.get("profile_flow")), **_mapping(payload.get("profile_execute"))}
        enrichment = _mapping(profile.get("audience_enrichment"))
        preview = _mapping(payload.get("audience_preview"))
        preview_bucket = _state_bucket(preview.get("status"))
        enrichment_bucket = _state_bucket(enrichment.get("status"))
        queue_bucket = _state_bucket(enrichment.get("queue_status"))
        downstream_bucket = _state_bucket(downstream.get("state"))
        downstream_job_ids = downstream.get("job_ids")
        has_concrete_job = bool(
            _positive_int(enrichment.get("job_id"))
            or (
                isinstance(downstream_job_ids, Sequence)
                and not isinstance(downstream_job_ids, (str, bytes))
                and any(_positive_int(job_id) for job_id in downstream_job_ids)
            )
        )

        # Materialized audience data is stronger than any stale queue snapshot.
        if preview_bucket == "ready":
            return "ready"

        # ``queue_status`` is refreshed from apify_jobs at read time.  A done
        # job only proves execution finished: without a ready preview (or an
        # explicit ready enrichment result), it must remain partial rather than
        # being counted as a successful audience result.
        if queue_bucket != "not_requested":
            if queue_bucket == "ready":
                if enrichment_bucket == "ready":
                    return "ready"
                if enrichment_bucket in _TERMINAL_BUCKETS:
                    return enrichment_bucket
                if preview_bucket in _TERMINAL_BUCKETS:
                    return preview_bucket
                return "partial"
            return queue_bucket

        # The persisted lineage is the next-strongest job truth.  Apply the same
        # execution-vs-data boundary when an old row says the job completed.
        if downstream_bucket != "not_requested":
            if downstream_bucket == "ready":
                if enrichment_bucket == "ready":
                    return "ready"
                if enrichment_bucket in _TERMINAL_BUCKETS:
                    return enrichment_bucket
                if preview_bucket in _TERMINAL_BUCKETS:
                    return preview_bucket
                return "partial"
            return downstream_bucket

        # Legacy profile failures recorded a synthetic waiting marker without
        # ever creating an audience job.  It may shadow an active upstream
        # profile, but once that upstream has failed/partial evidence it is a
        # skipped optional stage, not an eternal queued unit.  A ready profile
        # can still be inside the short orchestration-registration window and
        # therefore must retain the waiting marker until stronger truth arrives.
        if enrichment_bucket in {"queued", "running", "active"} and not has_concrete_job:
            profile_bucket = _profile_bucket(item)
            if profile_bucket in {"partial", "failed", "skipped"}:
                return "skipped"
        if enrichment_bucket != "not_requested":
            return enrichment_bucket
        if preview_bucket != "not_requested":
            return preview_bucket

    explicit = _state_bucket(downstream.get("state"))
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


def project_search_progress(
    session: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    *,
    worker_health: Mapping[str, Any] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Project one read-only, evidence-based progress contract.

    Queue creation is visible as ``queued`` but contributes zero to
    ``successful`` and ``progress_pct``.  Failed/partial work contributes to
    terminal progress only, never to successful progress.
    """

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

    profile_buckets = [_profile_bucket(item) for item in safe_items]
    downstream_buckets = {
        role: [_downstream_bucket(item, role) for item in safe_items]
        for role in FULL_ANALYSIS_ROLES
    }
    stages = {
        "search": _stage_projection("search", base_buckets, population=intended_total, data_ready=len(safe_items)),
        "profile": _stage_projection(
            "profile",
            profile_buckets,
            population=len(safe_items),
            data_ready=sum(_profile_data_ready(item, bucket) for item, bucket in zip(safe_items, profile_buckets)),
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
    worker = (
        dict(worker_health)
        if isinstance(worker_health, Mapping)
        else unobserved_worker_health(observed_at=observed_at)
    )
    orchestration_pending = _orchestration_pending(session, stored_progress)
    active_units_total = queued_units + running_units + active_units
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
    not_requested_stages = [
        key
        for key in ("profile", *FULL_ANALYSIS_ROLES)
        if stages[key]["state"] == "not_requested"
    ]
    blocked_by_worker = bool(
        worker.get("observed") is True
        and worker.get("online") is False
        and (queued_units > 0 or running_units > 0 or active_units > 0 or orchestration_pending)
    )
    if blocked_by_worker:
        state = "blocked_by_worker"
    elif running_units:
        state = "running"
    elif orchestration_pending:
        # 管线在跑(running)= 编排器本身就是活跃执行体;仍在队列(queued)= 尚未开跑。
        state = "running" if _text(session.get("status")) == "running" else "queued"
    elif active_units:
        state = "active"
    elif queued_units:
        state = "queued"
    elif failed_units:
        # Preserve a total failure as failed.  If any requested unit did
        # succeed, the aggregate is truthfully partial instead.
        state = "failed" if raw_session_status == "failed" and successful_units == 0 else "partial"
    elif requested_units and successful_units >= requested_units:
        state = "ready"
    elif raw_session_status in _TERMINAL_SESSION_STATES:
        # A partial/failed session with zero materialized rows is still a
        # terminal empty result.  Calling it "planned" made historical searches
        # look as if they were waiting forever even though no task remained.
        state = raw_session_status
    else:
        state = "planned"

    full_analysis_execution_complete = bool(
        safe_items
        and all(
            stages[role]["requested"] == len(safe_items)
            and stages[role]["successful"] == len(safe_items)
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    full_analysis_observable = bool(
        safe_items
        and all(
            stages[role]["data_ready"] is not None
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    full_analysis_complete = bool(
        full_analysis_execution_complete
        and full_analysis_observable
        and all(
            int(stages[role]["data_ready"] or 0) == len(safe_items)
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    empty_result = bool(
        not safe_items
        and requested_tasks_terminal
        and raw_session_status in {"ready", "partial"}
    )

    if blocked_by_worker:
        completion_kind = "blocked_by_worker"
    elif orchestration_pending or active_units_total > 0:
        completion_kind = "active"
    elif full_analysis_complete:
        completion_kind = "full_analysis"
    elif empty_result:
        completion_kind = "empty_result"
    elif requested_tasks_successful:
        completion_kind = "requested_stages"
    elif requested_tasks_terminal:
        completion_kind = "partial"
    else:
        completion_kind = "planned"

    return {
        "schema": PROGRESS_CONTRACT_SCHEMA,
        "claim_status": "observed_execution_only",
        "state": state,
        "session_status": _text(session.get("status")) or "planned",
        "phase": _text(summary.get("phase")) or None,
        "requested_units": requested_units,
        "successful_units": successful_units,
        "terminal_units": terminal_units,
        "queued_units": queued_units,
        "running_units": running_units,
        "active_units": active_units,
        "failed_units": failed_units,
        "requested_tasks_terminal": requested_tasks_terminal,
        "requested_tasks_successful": requested_tasks_successful,
        "completion_kind": completion_kind,
        "not_requested_stages": not_requested_stages,
        "empty_result": empty_result,
        "orchestration_pending": orchestration_pending,
        "orchestration_pending_basis": (
            "session_running_and_orchestrator_declares_more_tasks" if orchestration_pending else None
        ),
        "progress_pct": round(successful_units * 100 / requested_units, 1) if requested_units else 0.0,
        "terminal_pct": round(terminal_units * 100 / requested_units, 1) if requested_units else 0.0,
        "progress_pct_basis": "durable_success_only; queued_running_active_failed_not_counted_as_success",
        "stages": stages,
        "worker": worker,
        "blocked_by_worker": blocked_by_worker,
        "full_analysis_execution_complete": full_analysis_execution_complete,
        "full_analysis_observable": full_analysis_observable,
        "full_analysis_complete": full_analysis_complete,
        "observed_at": _iso((observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)),
        "sources": [
            "vkpi_kol_search_sessions.result_summary_json",
            "vkpi_kol_search_session_items.payload_json",
            "vkpi_worker_heartbeat",
        ],
    }


__all__ = [
    "FULL_ANALYSIS_ROLES",
    "PROGRESS_CONTRACT_SCHEMA",
    "PROGRESS_STAGE_KEYS",
    "completion_contract",
    "observe_worker_health",
    "project_search_progress",
    "unobserved_worker_health",
]
