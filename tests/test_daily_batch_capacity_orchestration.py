from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.sync import cron, daily_batch, daily_batch_capacity  # noqa: E402


class DurableQueue:
    backend_name = "redis-stream"

    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self.statuses = statuses or {}

    async def get_status(self, task_id: str) -> dict[str, str]:
        return {"status": self.statuses.get(task_id, "queued")}


def _runtime_proof(
    *,
    effective_workers: int = 2,
    active_backlog: int = 0,
    available: bool = True,
) -> dict[str, Any]:
    return {
        "proof_available": available,
        "proof_source": "test-ledger+heartbeat",
        "proof_error": "unavailable" if not available else "",
        "requested_worker_count": 2,
        "fresh_consumer_count": effective_workers,
        "effective_worker_count": effective_workers,
        "waiting_tasks": active_backlog,
        "processing_tasks": 0,
        "active_backlog_tasks": active_backlog if available else None,
        "backlog_policy": "reject_nonempty",
    }


def test_capacity_admission_uses_worker_seconds_and_rejects_one_over_limit() -> None:
    admitted = daily_batch.capacity_admission(
        official_count=18,
        kol_count=90,
        worker_count=2,
        child_timeout_seconds=300,
        capacity_window_seconds=17_100,
    )
    rejected = daily_batch.capacity_admission(
        official_count=18,
        kol_count=97,
        worker_count=2,
        child_timeout_seconds=300,
        capacity_window_seconds=17_100,
    )
    partial_wave_rejected = daily_batch.capacity_admission(
        official_count=0,
        kol_count=3,
        worker_count=2,
        child_timeout_seconds=300,
        capacity_window_seconds=450,
    )

    assert admitted == {
        "admitted": True,
        "algorithm": "worst_case_worker_seconds_v1",
        "queue_backlog_assumption": "not_included_in_formula",
        "official_tasks": 18,
        "kol_tasks": 90,
        "requested_tasks": 108,
        "worker_count": 2,
        "child_timeout_seconds": 300,
        "capacity_window_seconds": 17_100.0,
        "hard_task_limit": 114,
        "projected_seconds": 16_200,
        "headroom_tasks": 6,
    }
    assert rejected["requested_tasks"] == 115
    assert rejected["hard_task_limit"] == 114
    assert rejected["projected_seconds"] == 17_400
    assert rejected["admitted"] is False
    assert partial_wave_rejected["hard_task_limit"] == 2
    assert partial_wave_rejected["projected_seconds"] == 600
    assert partial_wave_rejected["admitted"] is False


def test_runtime_proof_uses_verified_backlog_and_fresh_aligned_consumers() -> None:
    release_sha = "a" * 40
    proof = daily_batch_capacity.runtime_proof_from_snapshots(
        {
            "stream_key": "vkpi:jobs",
            "group": "vkpi-workers",
            "summary": {"waiting": 1, "processing": 2},
        },
        {
            "capacity_release_sha": release_sha,
            "unique_names": True,
            "unique_pids": True,
            "all_worker_sha_aligned": True,
            "workers": [
                {
                    "online": True,
                    "worker_sha": release_sha,
                    "redis_ready_sequence": 3,
                    "redis_stream_key": "vkpi:jobs",
                    "redis_group_name": "vkpi-workers",
                    "redis_consumer_count": 1,
                },
                {
                    "online": False,
                    "worker_sha": release_sha,
                    "redis_ready_sequence": 99,
                    "redis_stream_key": "vkpi:jobs",
                    "redis_group_name": "vkpi-workers",
                    "redis_consumer_count": 4,
                },
            ],
        },
        2,
    )

    assert proof["proof_available"] is True
    assert proof["waiting_tasks"] == 1
    assert proof["processing_tasks"] == 2
    assert proof["active_backlog_tasks"] == 3
    assert proof["fresh_consumer_count"] == 1
    assert proof["effective_worker_count"] == 1
    assert proof["release_sha"] == release_sha


def test_runtime_proof_fails_closed_for_release_mismatch() -> None:
    proof = daily_batch_capacity.runtime_proof_from_snapshots(
        {
            "stream_key": "vkpi:jobs",
            "group": "vkpi-workers",
            "summary": {"waiting": 0, "processing": 0},
        },
        {
            "capacity_release_sha": "a" * 40,
            "unique_names": True,
            "unique_pids": True,
            "all_worker_sha_aligned": False,
            "workers": [],
        },
        2,
    )

    assert proof["proof_available"] is False
    assert proof["proof_error"] == "worker_release_sha_unaligned"
    assert proof["effective_worker_count"] == 0


@pytest.mark.parametrize(
    ("proof", "official_count", "expected_reason"),
    [
        (_runtime_proof(active_backlog=1), 1, "active_queue_backlog_present"),
        (_runtime_proof(effective_workers=1), 2, "projected_seconds_exceed_window"),
        (_runtime_proof(available=False), 1, "runtime_capacity_proof_unavailable"),
    ],
)
def test_runtime_admission_rejects_backlog_reduced_capacity_and_missing_proof(
    monkeypatch: pytest.MonkeyPatch,
    proof: dict[str, Any],
    official_count: int,
    expected_reason: str,
) -> None:
    checkpoints: list[dict[str, Any]] = []

    class Parent:
        @staticmethod
        def checkpoint_parent(_batch_id: str, summary: dict[str, Any]) -> None:
            checkpoints.append(summary)

    async def runtime_proof(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(proof)

    monkeypatch.setattr(
        daily_batch_capacity, "runtime_capacity_proof", runtime_proof
    )
    with pytest.raises(daily_batch.DailyBatchCapacityError):
        asyncio.run(
            daily_batch_capacity.reject_if_over_capacity(
                Parent,
                "runtime-capacity-reject",
                {
                    "worker_count": 2,
                    "child_timeout_seconds": 300,
                    "capacity_window_seconds": 300,
                },
                [{"id": index + 1} for index in range(official_count)],
                [],
                object(),
            )
        )

    admission = checkpoints[0]["admission"]
    assert admission["admitted"] is False
    assert admission["admission_reason"] == expected_reason
    assert admission["runtime_proof"] == proof


def test_runtime_capacity_proof_fails_closed_without_readiness_api() -> None:
    proof = asyncio.run(daily_batch_capacity.runtime_capacity_proof(object(), 2))

    assert proof["proof_available"] is False
    assert proof["proof_error"] == "queue_runtime_stats_unavailable"
    assert proof["effective_worker_count"] == 0


def test_capacity_failure_uses_diagnostic_parent_reason() -> None:
    writes: list[tuple[str, tuple[Any, ...]]] = []
    diagnostic = daily_batch.capacity_admission(
        official_count=1,
        kol_count=2,
        worker_count=1,
        child_timeout_seconds=300,
        capacity_window_seconds=300,
    )

    daily_batch.fail_parent(
        "batch-over-capacity",
        daily_batch.DailyBatchCapacityError(diagnostic),
        write=lambda sql, params: writes.append((sql, params)) or 1,
    )

    sql, params = writes[0]
    assert "error_type='capacity'" in sql
    assert params[1] == "capacity_admission_rejected"
    assert params[2] == "DailyBatchCapacityError"
    assert "requested=3:hard_limit=1" in str(params[3])


def test_async_kol_error_stop_threshold_halts_remaining_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domains.tasks.enqueue as task_enqueue

    enqueued: list[int] = []

    async def enqueue_task(
        _queue: Any,
        _task_type: str,
        params: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, str]:
        assert kwargs["timeout_seconds"] == 300
        target_id = int(params["kol_pool_id"])
        enqueued.append(target_id)
        return {"task_id": f"kol-{target_id}"}

    class FailedQueue(DurableQueue):
        async def get_status(self, task_id: str) -> dict[str, str]:
            assert task_id.startswith("kol-")
            return {"status": "failed"}

    monkeypatch.setattr(task_enqueue, "enqueue_vkpi_task", enqueue_task)
    receipt = asyncio.run(
        daily_batch.queue_batch(
            [],
            [{"id": index} for index in range(1, 6)],
            payload={
                "kol_error_stop_threshold": 2,
                "worker_count": 2,
                "child_timeout_seconds": 300,
                "completion_wait_seconds": 10,
                "completion_poll_seconds": 0.05,
            },
            staff=None,
            queue=FailedQueue(),
            batch_id="batch-loss-limit",
        )
    )

    assert enqueued == [1, 2]
    assert receipt["processed"] == 5
    assert receipt["kol_pool_light"]["enqueued"] == 2
    assert receipt["kol_pool_light"]["stopped_before_enqueue"] == 3
    assert receipt["kol_pool_light"]["stop_reason"] == "kol_error_stop_threshold_reached"
    assert receipt["loss_limit"] == {
        "enabled": True,
        "threshold": 2,
        "worker_count": 2,
        "stop_check": "after_each_worker_wave",
        "max_threshold_overshoot": 1,
        "provider_errors_seen": 2,
        "enqueue_errors_seen": 0,
        "errors_seen": 2,
        "stopped_before_enqueue": 3,
        "stopped_target_ids": [3, 4, 5],
        "stop_reason": "kol_error_stop_threshold_reached",
    }


def test_loss_probe_preserves_admitted_worker_width_after_early_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domains.tasks.enqueue as task_enqueue

    observed_waves: list[list[str]] = []

    async def enqueue_task(
        _queue: Any,
        _task_type: str,
        params: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, str]:
        return {"task_id": f"kol-{int(params['kol_pool_id'])}"}

    async def observe_wave(
        _queue: Any,
        task_ids: list[str],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        observed_waves.append(list(task_ids))
        failures = 2 if len(observed_waves) == 1 else 0
        return {
            "complete": True,
            "tasks_failed": failures,
            "tasks_partial": 0,
        }

    monkeypatch.setattr(task_enqueue, "enqueue_vkpi_task", enqueue_task)
    monkeypatch.setattr(daily_batch, "observe", observe_wave)
    receipt = asyncio.run(
        daily_batch.queue_batch(
            [],
            [{"id": index} for index in range(1, 9)],
            payload={
                "kol_error_stop_threshold": 3,
                "worker_count": 2,
                "child_timeout_seconds": 300,
                "capacity_window_seconds": 1_200,
                "completion_poll_seconds": 0.05,
            },
            staff=None,
            queue=DurableQueue(),
            batch_id="batch-fixed-loss-waves",
            progress_callback=None,
        )
    )

    assert receipt["kol_pool_light"]["enqueued"] == 8
    assert receipt["kol_pool_light"]["stopped_before_enqueue"] == 0
    assert observed_waves == [
        ["kol-1", "kol-2"],
        ["kol-3", "kol-4"],
        ["kol-5", "kol-6"],
        ["kol-7", "kol-8"],
    ]


def test_capacity_rejection_is_checkpointed_after_parent_insert_and_before_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains import channels
    from app.domains.sync import daily_sync

    events: list[str] = []
    checkpoints: list[dict[str, Any]] = []
    failures: list[BaseException] = []

    async def runtime_proof(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {**_runtime_proof(effective_workers=1), "requested_worker_count": 1}

    monkeypatch.setattr(
        daily_batch_capacity, "runtime_capacity_proof", runtime_proof
    )
    monkeypatch.setattr(daily_sync, "check_daily_sync_guard", lambda _payload: None)
    monkeypatch.setattr(daily_sync, "_kol_refresh_selector", lambda _payload: "qualified")
    monkeypatch.setattr(channels, "list_channels", lambda **_kwargs: {"channels": [{"id": 1}]})
    monkeypatch.setattr(
        daily_batch,
        "kol_rows",
        lambda *_args, **_kwargs: [{"id": 11}, {"id": 12}],
    )
    monkeypatch.setattr(
        daily_batch,
        "reconcile_recent_parents",
        lambda _queue: asyncio.sleep(
            0, result={"checked": 0, "reconciled": 0, "pending": 0, "failed": 0}
        ),
    )
    monkeypatch.setattr(
        daily_batch,
        "insert_parent",
        lambda *_args, **_kwargs: events.append("parent_inserted"),
    )
    monkeypatch.setattr(
        daily_batch,
        "checkpoint_parent",
        lambda _batch_id, summary: (
            events.append("capacity_checkpointed"), checkpoints.append(summary)
        ),
    )
    monkeypatch.setattr(
        daily_batch,
        "fail_parent",
        lambda _batch_id, exc: (events.append("parent_failed"), failures.append(exc)),
    )
    monkeypatch.setattr(
        daily_batch,
        "queue_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("capacity rejection must happen before child fanout")
        ),
    )

    with pytest.raises(daily_batch.DailyBatchCapacityError, match="requested=3:hard_limit=1"):
        asyncio.run(
            cron.run_job(
                "daily_incremental_sync",
                {
                    "allow_qualified_kol_refresh": True,
                    "worker_count": 1,
                    "child_timeout_seconds": 300,
                    "capacity_window_seconds": 300,
                },
                queue=DurableQueue(),
            )
        )

    assert events == ["parent_inserted", "capacity_checkpointed", "parent_failed"]
    assert len(failures) == 1
    assert isinstance(failures[0], daily_batch.DailyBatchCapacityError)
    diagnostic = checkpoints[0]
    assert diagnostic["phase"] == "planned"
    assert diagnostic["batch"]["enqueued"] == 0
    assert diagnostic["admission"]["admitted"] is False
    assert diagnostic["admission"]["requested_tasks"] == 3
    assert diagnostic["admission"]["hard_task_limit"] == 1
    assert diagnostic["kol_pool_light"]["stop_reason"] == "capacity_admission_rejected"
