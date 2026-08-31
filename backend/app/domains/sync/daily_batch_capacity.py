"""Capacity admission and bounded-loss fan-out for the durable daily batch."""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


DEFAULT_DAILY_KOL_LIMIT = 90
DEFAULT_DAILY_WORKER_COUNT = 2
DEFAULT_DAILY_CHILD_TIMEOUT_SECONDS = 300
DEFAULT_DAILY_CAPACITY_WINDOW_SECONDS = 17_100.0


class DailyBatchCapacityError(RuntimeError):
    """The requested fan-out cannot finish inside the reviewed capacity window."""

    def __init__(self, diagnostic: dict[str, Any]) -> None:
        self.diagnostic = dict(diagnostic)
        super().__init__(
            "daily_batch_capacity_exceeded:"
            f"requested={self.diagnostic.get('requested_tasks')}:"
            f"hard_limit={self.diagnostic.get('hard_task_limit')}:"
            f"projected_seconds={self.diagnostic.get('projected_seconds')}:"
            f"window_seconds={self.diagnostic.get('capacity_window_seconds')}"
        )


def capacity_admission(
    *,
    official_count: int,
    kol_count: int,
    worker_count: int = DEFAULT_DAILY_WORKER_COUNT,
    child_timeout_seconds: int = DEFAULT_DAILY_CHILD_TIMEOUT_SECONDS,
    capacity_window_seconds: float = DEFAULT_DAILY_CAPACITY_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Return the worst-case worker-seconds admission decision."""

    workers = max(1, min(4, int(worker_count or DEFAULT_DAILY_WORKER_COUNT)))
    timeout = max(
        1,
        min(
            86_400,
            int(child_timeout_seconds or DEFAULT_DAILY_CHILD_TIMEOUT_SECONDS),
        ),
    )
    window = max(0.0, min(86_400.0, float(capacity_window_seconds or 0.0)))
    official = max(0, int(official_count or 0))
    kol = max(0, int(kol_count or 0))
    requested = official + kol
    hard_limit = int(window // timeout) * workers
    projected = int(math.ceil(requested / workers) * timeout) if requested else 0
    return {
        "admitted": projected <= window,
        "algorithm": "worst_case_worker_seconds_v1",
        "queue_backlog_assumption": "not_included_in_formula",
        "official_tasks": official,
        "kol_tasks": kol,
        "requested_tasks": requested,
        "worker_count": workers,
        "child_timeout_seconds": timeout,
        "capacity_window_seconds": window,
        "hard_task_limit": hard_limit,
        "projected_seconds": projected,
        "headroom_tasks": hard_limit - requested,
    }


def normalize_runtime_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], float, float]:
    """Normalize the single capacity contract shared by admission and workers."""

    normalized = dict(payload)
    completion_wait_explicit = "completion_wait_seconds" in normalized
    wait_seconds = max(
        0.0,
        min(19_800.0, float(normalized.get("completion_wait_seconds") or 0.0)),
    )
    poll_seconds = max(
        0.05,
        min(60.0, float(normalized.get("completion_poll_seconds") or 10.0)),
    )
    if normalized.get("capacity_window_seconds") is not None:
        raw_capacity_window = normalized["capacity_window_seconds"]
    elif completion_wait_explicit:
        raw_capacity_window = wait_seconds
    else:
        raw_capacity_window = DEFAULT_DAILY_CAPACITY_WINDOW_SECONDS
    normalized.update({
        "worker_count": max(
            1,
            min(4, int(normalized.get("worker_count") or DEFAULT_DAILY_WORKER_COUNT)),
        ),
        "child_timeout_seconds": max(
            1,
            min(
                86_400,
                int(
                    normalized.get("child_timeout_seconds")
                    or DEFAULT_DAILY_CHILD_TIMEOUT_SECONDS
                ),
            ),
        ),
        "capacity_window_seconds": max(
            0.0,
            min(19_800.0, float(raw_capacity_window)),
        ),
    })
    return normalized, wait_seconds, poll_seconds


def rejection_summary(
    batch_id: str,
    admission: dict[str, Any],
    official_rows: list[dict[str, Any]],
    kol_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    requested = len(official_rows) + len(kol_rows)
    return {
        "phase": "planned",
        "processed": 0,
        "total": requested,
        "batch": {
            "batch_id": batch_id,
            "parent_persisted": True,
            "identity_scope": "durable_parent_before_fanout",
            "requested": requested,
            "enqueued": 0,
            "task_ids": [],
            "task_links": [],
        },
        "admission": dict(admission),
        "official": {
            "channels_requested": len(official_rows),
            "channels_enqueued": 0,
            "channels_failed_to_enqueue": 0,
            "task_ids": [],
            "failed": [],
        },
        "kol_pool_light": {
            "requested": len(kol_rows),
            "enqueued": 0,
            "failed_to_enqueue": 0,
            "stopped_before_enqueue": len(kol_rows),
            "stop_reason": "capacity_admission_rejected",
            "task_ids": [],
            "failed": [],
        },
        "enqueue_failures": 0,
    }


def _proof_unavailable(requested_workers: int, reason: str) -> dict[str, Any]:
    return {
        "proof_available": False,
        "proof_source": "ledger_runtime_stats+redis_worker_heartbeat",
        "proof_error": str(reason or "unknown")[:300],
        "requested_worker_count": requested_workers,
        "fresh_consumer_count": 0,
        "effective_worker_count": 0,
        "waiting_tasks": None,
        "processing_tasks": None,
        "active_backlog_tasks": None,
        "backlog_policy": "reject_nonempty",
    }


def _worker_fleet_snapshot() -> dict[str, Any]:
    from app.services.jobs.capacity_readiness import worker_fleet_snapshot

    return worker_fleet_snapshot()


def runtime_proof_from_snapshots(
    stats: dict[str, Any],
    fleet: dict[str, Any],
    requested_workers: int,
) -> dict[str, Any]:
    requested = max(1, min(4, int(requested_workers or 1)))
    summary = stats.get("summary") if isinstance(stats, dict) else None
    if not isinstance(summary, dict) or summary.get("note"):
        return _proof_unavailable(requested, "ledger_queue_summary_unavailable")
    if "waiting" not in summary or "processing" not in summary:
        return _proof_unavailable(requested, "ledger_backlog_counts_missing")
    workers = fleet.get("workers") if isinstance(fleet, dict) else None
    if not isinstance(workers, list):
        return _proof_unavailable(requested, "worker_heartbeat_rows_unavailable")
    if fleet.get("unique_names") is False or fleet.get("unique_pids") is False:
        return _proof_unavailable(requested, "worker_heartbeat_identity_collision")
    release_sha = str(fleet.get("capacity_release_sha") or "").strip().lower()
    if len(release_sha) != 40 or fleet.get("all_worker_sha_aligned") is not True:
        return _proof_unavailable(requested, "worker_release_sha_unaligned")
    try:
        waiting = max(0, int(summary["waiting"]))
        processing = max(0, int(summary["processing"]))
    except (TypeError, ValueError):
        return _proof_unavailable(requested, "ledger_backlog_counts_invalid")
    stream_key = str(stats.get("stream_key") or "")
    group = str(stats.get("group") or "")
    if not stream_key or not group:
        return _proof_unavailable(requested, "redis_stream_identity_missing")
    fresh_consumers = 0
    fresh_workers = 0
    for worker in workers:
        if not isinstance(worker, dict) or worker.get("online") is not True:
            continue
        if int(worker.get("redis_ready_sequence") or 0) < 2:
            continue
        if str(worker.get("worker_sha") or "").strip().lower() != release_sha:
            continue
        if str(worker.get("redis_stream_key") or "") != stream_key:
            continue
        if str(worker.get("redis_group_name") or "") != group:
            continue
        consumers = max(0, int(worker.get("redis_consumer_count") or 0))
        if consumers:
            fresh_workers += 1
            fresh_consumers += consumers
    return {
        "proof_available": True,
        "proof_source": "ledger_runtime_stats+redis_worker_heartbeat",
        "proof_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_worker_count": requested,
        "fresh_worker_processes": fresh_workers,
        "fresh_consumer_count": fresh_consumers,
        "effective_worker_count": min(requested, fresh_consumers),
        "minimum_ready_sequence": 2,
        "release_sha": release_sha,
        "waiting_tasks": waiting,
        "processing_tasks": processing,
        "active_backlog_tasks": waiting + processing,
        "backlog_policy": "reject_nonempty",
        "stream_key": stream_key,
        "group": group,
    }


async def runtime_capacity_proof(
    queue: Any,
    requested_workers: int,
) -> dict[str, Any]:
    requested = max(1, min(4, int(requested_workers or 1)))
    stats_reader = getattr(queue, "runtime_stats", None)
    if not callable(stats_reader):
        return _proof_unavailable(requested, "queue_runtime_stats_unavailable")
    try:
        stats = await stats_reader()
        fleet = await asyncio.to_thread(_worker_fleet_snapshot)
        return runtime_proof_from_snapshots(stats, fleet, requested)
    except Exception as exc:
        return _proof_unavailable(
            requested, f"{type(exc).__name__}: {str(exc)[:240]}"
        )


async def reject_if_over_capacity(
    parent_api: Any,
    batch_id: str,
    payload: dict[str, Any],
    official_rows: list[dict[str, Any]],
    kol_rows: list[dict[str, Any]],
    queue: Any,
) -> dict[str, Any]:
    """Checkpoint a diagnostic and stop before the first child enqueue."""

    requested_workers = int(payload["worker_count"])
    requested_tasks = len(official_rows) + len(kol_rows)
    proof = (
        await runtime_capacity_proof(queue, requested_workers)
        if requested_tasks
        else {
            **_proof_unavailable(requested_workers, "not_required_no_work"),
            "proof_available": True,
            "proof_source": "not_required_no_work",
            "active_backlog_tasks": 0,
            "effective_worker_count": requested_workers,
        }
    )
    effective_workers = int(proof.get("effective_worker_count") or 0)
    admission = capacity_admission(
        official_count=len(official_rows),
        kol_count=len(kol_rows),
        worker_count=max(1, effective_workers),
        child_timeout_seconds=int(payload["child_timeout_seconds"]),
        capacity_window_seconds=float(payload["capacity_window_seconds"]),
    )
    proof_available = proof.get("proof_available") is True
    backlog = proof.get("active_backlog_tasks")
    if not proof_available:
        reason = "runtime_capacity_proof_unavailable"
    elif backlog not in (0, None):
        reason = "active_queue_backlog_present"
    elif effective_workers <= 0:
        reason = "no_fresh_worker_capacity"
    elif not admission["admitted"]:
        reason = "projected_seconds_exceed_window"
    else:
        reason = "within_verified_capacity"
    admission.update({
        "admitted": reason == "within_verified_capacity",
        "admission_reason": reason,
        "configured_worker_count": requested_workers,
        "worker_count": effective_workers,
        "queue_backlog_assumption": (
            "verified_empty_at_admission"
            if backlog == 0 and proof_available
            else "rejected_unverified_or_nonempty"
        ),
        "runtime_proof": proof,
    })
    if effective_workers <= 0:
        admission.update({
            "hard_task_limit": 0,
            "projected_seconds": None,
            "headroom_tasks": -requested_tasks,
        })
    if admission["admitted"]:
        return admission
    parent_api.checkpoint_parent(
        batch_id,
        rejection_summary(batch_id, admission, official_rows, kol_rows),
    )
    raise DailyBatchCapacityError(admission)


def initial_queue_state(
    *,
    official_skipped: bool,
    kol_allowed: bool,
) -> dict[str, Any]:
    official: dict[str, Any] = {"skipped": True} if official_skipped else {
        "channels_enqueued": 0,
        "channels_requested": 0,
        "channels_failed_to_enqueue": 0,
        "task_ids": [],
        "failed": [],
    }
    kol_result: dict[str, Any] = {"skipped": True} if not kol_allowed else {
        "requested": 0,
        "enqueued": 0,
        "failed_to_enqueue": 0,
        "task_ids": [],
        "failed": [],
    }
    return {
        "official": official,
        "kol_pool_light": kol_result,
        "task_ids": [],
        "task_links": [],
        "scheduler": "round_robin_v1",
    }


async def emit_enqueue_receipt(
    payload: dict[str, Any],
    batch: dict[str, Any],
    official: dict[str, Any],
    kol_result: dict[str, Any],
    enqueue_failures: int,
    *,
    logger: Any,
) -> None:
    callback = payload.get("_batch_receipt_callback")
    if not callable(callback):
        return
    try:
        callback_result = callback({
            **batch,
            "phase": "children_enqueued",
            "official": official,
            "kol_pool_light": kol_result,
            "enqueue_failures": enqueue_failures,
        })
        if hasattr(callback_result, "__await__"):
            await callback_result
    except Exception:
        logger.warning("daily batch enqueue receipt emission failed", exc_info=True)


def completion_contract(
    checkpoint: dict[str, Any],
    payload: dict[str, Any],
    wait_seconds: float,
    capacity_started: float,
) -> tuple[dict[str, Any], int, float]:
    loss_limit = (
        checkpoint.get("loss_limit")
        if isinstance(checkpoint.get("loss_limit"), dict)
        else {}
    )
    kol_result = checkpoint.get("kol_pool_light")
    stopped = int(
        (kol_result.get("stopped_before_enqueue") or 0)
        if isinstance(kol_result, dict)
        else 0
    )
    remaining = remaining_completion_wait(payload, wait_seconds, capacity_started)
    return loss_limit, stopped, remaining


def remaining_completion_wait(
    payload: dict[str, Any],
    wait_seconds: float,
    capacity_started: float,
) -> float:
    if int(payload.get("kol_error_stop_threshold") or 0) <= 0:
        return wait_seconds
    return max(0.0, wait_seconds - (time.monotonic() - capacity_started))


async def queue_batch(
    official_rows: list[dict[str, Any]],
    kol_rows: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    staff: dict[str, Any] | None,
    queue: Any,
    batch_id: str,
    progress_callback: Any | None,
    schedule_rows: Callable[
        [list[dict[str, Any]], list[dict[str, Any]]],
        list[tuple[str, dict[str, Any]]],
    ],
    observer: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Enqueue fairly while a rolling KOL window enforces the error stop."""

    if queue is None:
        raise RuntimeError("durable job queue unavailable")
    if str(getattr(queue, "backend_name", "")) == "inprocess":
        raise RuntimeError(
            "durable_queue_required:inprocess_queue_has_no_provider_execution_fence"
        )
    import app.domains.tasks.enqueue as task_enqueue

    official_max = int(
        payload.get("official_max_posts")
        or payload.get("channel_max_posts")
        or payload.get("max_posts")
        or 50
    )
    kol_max = max(
        1,
        min(3, int(payload.get("kol_max_posts") or payload.get("max_posts") or 1)),
    )
    child_timeout = max(
        1,
        min(
            86_400,
            int(payload.get("child_timeout_seconds") or DEFAULT_DAILY_CHILD_TIMEOUT_SECONDS),
        ),
    )
    loss_threshold = max(
        0, min(100, int(payload.get("kol_error_stop_threshold") or 0))
    )
    loss_worker_count = max(
        1, min(4, int(payload.get("worker_count") or DEFAULT_DAILY_WORKER_COUNT))
    )
    loss_wait_seconds = max(
        0.0,
        min(
            86_400.0,
            float(
                payload.get("capacity_window_seconds")
                if payload.get("capacity_window_seconds") is not None
                else payload.get("completion_wait_seconds")
                if payload.get("completion_wait_seconds") is not None
                else DEFAULT_DAILY_CAPACITY_WINDOW_SECONDS
            ),
        ),
    )
    loss_poll_seconds = max(
        0.05, min(60.0, float(payload.get("completion_poll_seconds") or 10.0))
    )
    enqueue_staff = staff or {
        "id": 0,
        "staff_id": 0,
        "user_id": 0,
        "role": "admin",
        "is_owner": 1,
        "email": "",
    }
    ids: dict[str, list[str]] = {"official": [], "kol_pool_light": []}
    failures: dict[str, list[dict[str, Any]]] = {
        "official": [],
        "kol_pool_light": [],
    }
    ordered_ids: list[str] = []
    task_links: list[dict[str, Any]] = []
    processed = 0
    loss_started = time.monotonic()
    loss_probe_ids: list[str] = []
    loss_provider_errors = 0
    loss_enqueue_errors = 0
    loss_stopped_targets: list[int] = []
    loss_stop_reason = ""

    def receipt() -> dict[str, Any]:
        official_ids = list(dict.fromkeys(ids["official"]))
        kol_ids = list(dict.fromkeys(ids["kol_pool_light"]))
        result = {
            "official": {
                "channels_enqueued": len(official_ids),
                "channels_requested": len(official_rows),
                "channels_failed_to_enqueue": len(failures["official"]),
                "task_ids": official_ids,
                "failed": list(failures["official"][:20]),
            },
            "kol_pool_light": {
                "requested": len(kol_rows),
                "enqueued": len(kol_ids),
                "failed_to_enqueue": len(failures["kol_pool_light"]),
                "task_ids": kol_ids,
                "failed": list(failures["kol_pool_light"][:20]),
            },
            "task_ids": list(dict.fromkeys(ordered_ids)),
            "task_links": [dict(link) for link in task_links],
            "scheduler": "round_robin_v1",
            "processed": processed,
            "total": len(official_rows) + len(kol_rows),
        }
        if loss_threshold:
            loss = {
                "enabled": True,
                "threshold": loss_threshold,
                "worker_count": loss_worker_count,
                "stop_check": "after_each_worker_wave",
                "max_threshold_overshoot": loss_worker_count - 1,
                "provider_errors_seen": loss_provider_errors,
                "enqueue_errors_seen": loss_enqueue_errors,
                "errors_seen": loss_provider_errors + loss_enqueue_errors,
                "stopped_before_enqueue": len(loss_stopped_targets),
                "stopped_target_ids": list(loss_stopped_targets[:20]),
                "stop_reason": loss_stop_reason,
            }
            result["loss_limit"] = loss
            result["kol_pool_light"].update({
                "stopped_before_enqueue": loss["stopped_before_enqueue"],
                "stop_reason": loss_stop_reason,
            })
        return result

    async def probe_kol_window() -> None:
        nonlocal loss_provider_errors, loss_probe_ids, loss_stop_reason
        if not loss_threshold or not loss_probe_ids or loss_stop_reason:
            return
        remaining = loss_wait_seconds - (time.monotonic() - loss_started)
        if remaining <= 0:
            loss_stop_reason = "kol_loss_limit_observation_window_exhausted"
            return
        completion = await observer(
            queue,
            list(loss_probe_ids),
            wait_seconds=remaining,
            poll_seconds=loss_poll_seconds,
        )
        loss_probe_ids = []
        loss_provider_errors += int(completion.get("tasks_failed") or 0)
        loss_provider_errors += int(completion.get("tasks_partial") or 0)
        if not completion.get("complete"):
            loss_stop_reason = "kol_loss_limit_observation_window_exhausted"
        elif loss_provider_errors + loss_enqueue_errors >= loss_threshold:
            loss_stop_reason = "kol_error_stop_threshold_reached"

    for target_index, (lane, row) in enumerate(
        schedule_rows(official_rows, kol_rows)
    ):
        key = "channel_id" if lane == "official" else "kol_pool_id"
        target_id = int(row.get("id") or 0)
        if lane == "kol_pool_light" and loss_stop_reason:
            loss_stopped_targets.append(target_id)
            processed += 1
            current = receipt()
            if callable(progress_callback):
                progress_result = progress_callback(current)
                if hasattr(progress_result, "__await__"):
                    await progress_result
            continue
        if target_id <= 0:
            failures[lane].append({
                key: target_id,
                "error": "ValueError: positive target id required",
            })
            if lane == "kol_pool_light" and loss_threshold:
                loss_enqueue_errors += 1
        else:
            if lane == "official":
                task_type = task_enqueue.VKPI_OFFICIAL_CHANNEL_SYNC
                params = {
                    "channel_id": target_id,
                    "max_posts": int(row.get("_requested_max_posts") or official_max),
                }
            else:
                task_type = task_enqueue.VKPI_KOL_POOL_ON_DEMAND_REFRESH
                params = {
                    "kol_pool_id": target_id,
                    "reason": "daily_incremental_sync",
                    "max_posts": kol_max,
                }
            try:
                queued = await task_enqueue.enqueue_vkpi_task(
                    queue,
                    task_type,
                    {
                        **params,
                        "orchestration_batch_id": batch_id,
                        "orchestration_lane": lane,
                    },
                    staff=enqueue_staff,
                    priority=5,
                    timeout_seconds=child_timeout,
                )
                task_id = str(queued.get("task_id") or "").strip()
                if not task_id:
                    raise RuntimeError("enqueue returned an empty task_id")
                ids[lane].append(task_id)
                ordered_ids.append(task_id)
                task_links.append({
                    "task_id": task_id,
                    "lane": lane,
                    key: target_id,
                    "target_index": target_index,
                })
                if lane == "kol_pool_light" and loss_threshold:
                    loss_probe_ids.append(task_id)
            except Exception as exc:
                failures[lane].append({
                    key: target_id,
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                })
                if lane == "kol_pool_light" and loss_threshold:
                    loss_enqueue_errors += 1
        processed += 1
        current = receipt()
        if callable(progress_callback):
            progress_result = progress_callback(current)
            if hasattr(progress_result, "__await__"):
                await progress_result
        if lane == "kol_pool_light" and loss_threshold:
            if loss_provider_errors + loss_enqueue_errors >= loss_threshold:
                loss_stop_reason = "kol_error_stop_threshold_reached"
                continue
            # Preserve the same worker-width assumption used by capacity
            # admission.  A full asynchronous wave can overshoot the error
            # threshold by at most worker_count-1 before the stop is observed.
            if len(loss_probe_ids) >= loss_worker_count:
                await probe_kol_window()
    return receipt()
