from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
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


def _completion(*statuses: str, scope: str = "provider_terminal") -> dict[str, Any]:
    ids = [f"task-{index}" for index in range(len(statuses))]
    return daily_batch.completion_snapshot(
        ids,
        dict(zip(ids, statuses)),
        wait_seconds=0,
        poll_seconds=0,
        scope=scope,
        sla_expired=False,
    )


def _terminal_summary(*, requested: int = 1, failures: int = 0) -> dict[str, Any]:
    task_ids = ["task-0"]
    return {
        "batch": {"requested": requested, "task_ids": task_ids},
        "official": {"channels_failed_to_enqueue": failures},
        "kol_pool_light": {"failed_to_enqueue": 0},
        "completion": _completion("done"),
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


def test_parent_insert_is_insert_only_sanitized_and_rowcount_checked() -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def writer(sql: str, params: tuple[Any, ...]) -> int:
        calls.append((sql, params))
        return 1

    daily_batch.insert_parent(
        "batch-1",
        {
            "official_max_posts": 50,
            "completion_wait_seconds": 10,
            "staff": {"email": "secret@example.com"},
            "token": "must-not-persist",
            "_batch_receipt_callback": object(),
        },
        [{"id": 1}],
        [{"id": 9}],
        write=writer,
    )

    sql, params = calls[0]
    persisted = json.loads(str(params[7]))
    assert "ON CONFLICT" not in sql.upper()
    assert persisted == {
        "parameters": {"completion_wait_seconds": 10, "official_max_posts": 50},
        "official_target_ids": [1],
        "kol_target_ids": [9],
    }
    assert "secret" not in str(params)
    with pytest.raises(RuntimeError, match="daily_batch_parent_insert_failed"):
        daily_batch.insert_parent("batch-2", {}, [], [], write=lambda *_args: 0)


def test_sqlite_parent_transaction_blocks_overlap_and_never_revives_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.db.connection as connection

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE vkpi_sync_runs (
            run_id TEXT PRIMARY KEY, job_name TEXT, stage TEXT, started_at TEXT,
            finished_at TEXT, status TEXT, total_targets INTEGER,
            last_success_index INTEGER, reason TEXT, error_type TEXT,
            error_class TEXT, error_message TEXT, payload_json TEXT,
            summary_json TEXT, updated_at TEXT
        )"""
    )
    monkeypatch.setattr(connection, "open_standalone_conn", lambda: conn)
    monkeypatch.setattr(connection, "close_standalone_conn", lambda _conn: None)
    monkeypatch.setattr(connection, "is_postgres_runtime", lambda: False)

    daily_batch.insert_parent("batch-one", {}, [], [])
    with pytest.raises(RuntimeError, match="daily_batch_parent_insert_failed"):
        daily_batch.insert_parent("batch-two", {}, [], [])
    conn.execute("UPDATE vkpi_sync_runs SET status='completed' WHERE run_id='batch-one'")
    conn.commit()
    with pytest.raises(RuntimeError, match="daily_batch_parent_insert_failed"):
        daily_batch.insert_parent("batch-one", {}, [], [])
    assert conn.execute(
        "SELECT status FROM vkpi_sync_runs WHERE run_id='batch-one'"
    ).fetchone()[0] == "completed"


def test_checkpoint_and_finish_reject_zero_row_or_missing_terminal_evidence() -> None:
    with pytest.raises(RuntimeError, match="checkpoint_rejected"):
        daily_batch.checkpoint_parent(
            "batch-1", {"phase": "children_enqueued"}, write=lambda *_args: 0
        )
    pending = {
        "batch": {"requested": 1, "task_ids": ["task-1"]},
        "completion": _completion("queued", scope="bounded_observation"),
    }
    with pytest.raises(RuntimeError, match="terminal_evidence_required"):
        daily_batch.finish_parent("batch-1", "partial", pending, write=lambda *_args: 1)

    terminal = _terminal_summary()
    daily_batch.finish_parent(
        "batch-1",
        "completed",
        terminal,
        write=lambda *_args: 0,
        read=lambda _batch_id: {"status": "completed"},
    )
    with pytest.raises(RuntimeError, match="finish_rejected"):
        daily_batch.finish_parent(
            "batch-1",
            "completed",
            terminal,
            write=lambda *_args: 0,
            read=lambda _batch_id: {"status": "failed"},
        )


def test_parent_failure_threshold_keeps_small_partial_completed_but_blocks_over_ten_percent() -> None:
    writes: list[tuple[str, tuple[Any, ...]]] = []

    def writer(sql: str, params: tuple[Any, ...]) -> int:
        writes.append((sql, params))
        return 1

    daily_batch.finish_parent(
        "small-error",
        "partial",
        _terminal_summary(requested=10, failures=1),
        write=writer,
    )
    daily_batch.finish_parent(
        "large-error",
        "partial",
        _terminal_summary(requested=10, failures=2),
        write=writer,
    )

    assert writes[0][1][1] == "completed"
    assert writes[0][1][3] == "completed_with_errors:1/10"
    assert writes[1][1][1] == "failed"
    assert writes[1][1][3] == "failure_threshold_exceeded:2/10"


def test_parent_progress_counts_successes_and_labels_low_rate_infrastructure_failure() -> None:
    writes: list[tuple[str, tuple[Any, ...]]] = []
    summary = {
        "batch": {"requested": 20, "task_ids": ["task-0", "task-1"]},
        "official": {"channels_failed_to_enqueue": 0},
        "kol_pool_light": {"failed_to_enqueue": 0},
        "completion": _completion("done", "timeout"),
    }

    daily_batch.finish_parent(
        "infra",
        "partial",
        summary,
        write=lambda sql, params: writes.append((sql, params)) or 1,
    )

    assert writes[0][1][1] == "failed"
    assert writes[0][1][2] == 0
    assert writes[0][1][3] == "infrastructure_child_failure:1/20"

    queued_completion = _completion("done", "queued", scope="bounded_observation")
    queued_completion["sla_expired"] = True
    daily_batch.finish_parent(
        "queued",
        "queued",
        {"batch": {"task_ids": ["task-0", "task-1"]}, "completion": queued_completion},
        write=lambda sql, params: writes.append((sql, params)) or 1,
    )
    assert writes[1][1][0] == "completion_sla_expired"
    assert writes[1][1][1] == 0


@pytest.mark.parametrize(
    ("statuses", "result_status"),
    [(('done', 'done'), "completed"), (('failed', 'done'), "partial")],
)
def test_last_success_index_is_zero_based_target_index_not_terminal_count(
    statuses: tuple[str, str],
    result_status: str,
) -> None:
    writes: list[tuple[str, tuple[Any, ...]]] = []
    summary = {
        "batch": {
            "requested": 2,
            "task_ids": ["task-0", "task-1"],
            "task_links": [
                {"task_id": "task-0", "target_index": 0},
                {"task_id": "task-1", "target_index": 1},
            ],
        },
        "official": {"channels_failed_to_enqueue": 0},
        "kol_pool_light": {"failed_to_enqueue": 0},
        "completion": _completion(*statuses),
    }

    daily_batch.finish_parent(
        "indexed",
        result_status,
        summary,
        write=lambda sql, params: writes.append((sql, params)) or 1,
    )

    assert writes[0][1][2] == 1


def test_enqueue_progress_survives_third_target_crash_and_reconcile_blocks_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domains.tasks.enqueue as task_enqueue

    calls = 0
    checkpoints: list[dict[str, Any]] = []
    checkpoint_reasons: list[str] = []

    async def enqueue(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise SystemExit("simulated process crash")
        return {"task_id": f"task-{calls}"}

    def progress(snapshot: dict[str, Any]) -> None:
        summary = daily_batch.checkpoint_summary(
            "batch-crash", 3, snapshot, phase="enqueueing"
        )

        def writer(_sql: str, params: tuple[Any, ...]) -> int:
            checkpoint_reasons.append(str(params[0]))
            checkpoints.append(json.loads(str(params[1])))
            return 1

        daily_batch.checkpoint_parent("batch-crash", summary, write=writer)

    monkeypatch.setattr(task_enqueue, "enqueue_vkpi_task", enqueue)
    with pytest.raises(SystemExit, match="simulated process crash"):
        asyncio.run(
            daily_batch.queue_batch(
                [{"id": 1}, {"id": 2}, {"id": 3}],
                [],
                payload={},
                staff=None,
                queue=DurableQueue(),
                batch_id="batch-crash",
                progress_callback=progress,
            )
        )

    durable = checkpoints[-1]
    assert checkpoint_reasons == ["children_enqueueing", "children_enqueueing"]
    assert durable["phase"] == "enqueueing"
    assert (durable["processed"], durable["total"]) == (2, 3)
    assert durable["batch"]["task_ids"] == ["task-1", "task-2"]
    assert durable["batch"]["task_links"] == [
        {"task_id": "task-1", "lane": "official", "channel_id": 1, "target_index": 0},
        {"task_id": "task-2", "lane": "official", "channel_id": 2, "target_index": 1},
    ]
    reconciled = asyncio.run(
        daily_batch.reconcile_recent_parents(
            DurableQueue({"task-1": "done", "task-2": "done"}),
            load=lambda: [{
                "run_id": "batch-crash",
                "started_at": datetime.now(timezone.utc) - timedelta(hours=1),
                "updated_at": datetime.now(timezone.utc),
                "summary_json": json.dumps(durable),
            }],
            write=lambda *_args: 1,
        )
    )
    assert reconciled == {"checked": 1, "reconciled": 0, "pending": 1, "failed": 0}


def test_progress_checkpoint_failure_aborts_enqueue_instead_of_becoming_target_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domains.tasks.enqueue as task_enqueue

    calls = 0

    async def enqueue(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"task_id": f"task-{calls}"}

    monkeypatch.setattr(task_enqueue, "enqueue_vkpi_task", enqueue)
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        asyncio.run(
            daily_batch.queue_batch(
                [{"id": 1}, {"id": 2}],
                [],
                payload={},
                staff=None,
                queue=DurableQueue(),
                batch_id="batch-checkpoint-error",
                progress_callback=lambda _receipt: (_ for _ in ()).throw(
                    RuntimeError("checkpoint unavailable")
                ),
            )
        )
    assert calls == 1


def test_reconcile_recent_planned_parent_waits_then_fails_stale_parent() -> None:
    now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    summary = json.dumps({"phase": "planned", "batch_id": "old"})
    writes: list[tuple[str, tuple[Any, ...]]] = []

    recent = asyncio.run(
        daily_batch.reconcile_recent_parents(
            DurableQueue(),
            load=lambda: [{
                "run_id": "recent",
                "started_at": now - timedelta(hours=1),
                "updated_at": now,
                "summary_json": summary,
            }],
            write=lambda *_args: 1,
            now=now,
        )
    )
    stale = asyncio.run(
        daily_batch.reconcile_recent_parents(
            DurableQueue(),
            load=lambda: [{
                "run_id": "stale",
                "started_at": now - timedelta(hours=1),
                "updated_at": now - timedelta(minutes=16),
                "summary_json": summary,
            }],
            write=lambda sql, params: writes.append((sql, params)) or 1,
            now=now,
        )
    )

    assert recent == {"checked": 1, "reconciled": 0, "pending": 1, "failed": 0}
    assert stale == {"checked": 1, "reconciled": 0, "pending": 0, "failed": 1}
    assert "status='failed'" in writes[0][0]
    assert writes[0][1][1] == "orchestration_failed"


def test_reconcile_accepts_generator_loader() -> None:
    result = asyncio.run(
        daily_batch.reconcile_recent_parents(
            DurableQueue(),
            load=lambda: iter(()),
        )
    )

    assert result == {"checked": 0, "reconciled": 0, "pending": 0, "failed": 0}


def test_reconcile_streams_generator_before_late_loader_failure() -> None:
    writes: list[tuple[str, tuple[Any, ...]]] = []
    now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)

    def rows():
        yield {
            "run_id": "stale-before-loader-error",
            "started_at": now - timedelta(hours=1),
            "updated_at": now - timedelta(minutes=16),
            "summary_json": json.dumps({"phase": "planned"}),
        }
        raise RuntimeError("loader interrupted")

    with pytest.raises(RuntimeError, match="loader interrupted"):
        asyncio.run(
            daily_batch.reconcile_recent_parents(
                DurableQueue(),
                load=rows,
                write=lambda sql, params: writes.append((sql, params)) or 1,
                now=now,
            )
        )

    assert len(writes) == 1
    assert "status='failed'" in writes[0][0]


@pytest.mark.parametrize(
    "summary_json",
    [
        "[]",
        "null",
        '"text"',
        json.dumps({"phase": "children_enqueued", "batch": {"task_ids": "task-1"}}),
        json.dumps({"phase": "children_enqueued", "batch": {"task_ids": [None]}}),
        json.dumps({"phase": "bogus", "batch": {"task_ids": ["task-1"]}}),
    ],
)
def test_reconcile_fails_closed_for_non_mapping_summary_or_non_list_task_ids(
    summary_json: str,
) -> None:
    writes: list[tuple[str, tuple[Any, ...]]] = []

    result = asyncio.run(
        daily_batch.reconcile_recent_parents(
            DurableQueue({"task-1": "done"}),
            load=lambda: [{
                "run_id": "invalid-parent",
                "updated_at": datetime.now(timezone.utc),
                "summary_json": summary_json,
            }],
            write=lambda sql, params: writes.append((sql, params)) or 1,
        )
    )

    assert result == {"checked": 1, "reconciled": 0, "pending": 0, "failed": 1}
    assert "status='failed'" in writes[0][0]


def test_reconcile_expires_detached_children_after_six_hours_without_dropping_links() -> None:
    now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    summary = {
        "phase": "children_enqueued",
        "batch": {
            "requested": 1,
            "task_ids": ["task-1"],
            "task_links": [{"task_id": "task-1", "lane": "official", "channel_id": 1}],
        },
        "official": {"channels_failed_to_enqueue": 0},
        "kol_pool_light": {"failed_to_enqueue": 0},
        "enqueue_failures": 0,
    }
    writes: list[tuple[str, tuple[Any, ...]]] = []

    result = asyncio.run(
        daily_batch.reconcile_recent_parents(
            DurableQueue({"task-1": "queued"}),
            load=lambda: [{
                "run_id": "detached",
                "started_at": now - timedelta(hours=8),
                "updated_at": now - timedelta(hours=7),
                "summary_json": json.dumps(summary),
            }],
            write=lambda sql, params: writes.append((sql, params)) or 1,
            now=now,
        )
    )

    assert result == {"checked": 1, "reconciled": 0, "pending": 0, "failed": 1}
    assert "summary_json" not in writes[0][0]
    assert writes[0][1][1] == "orchestration_failed"
    assert writes[0][1][3] == "child_completion_lifecycle_exceeded"


def test_wait_zero_parent_summary_remains_canonical_and_reconciles_to_terminal() -> None:
    first_write: list[tuple[str, tuple[Any, ...]]] = []
    initial = {
        "status": "queued",
        "batch": {
            "batch_id": "detached",
            "requested": 1,
            "task_ids": ["task-0"],
            "task_links": [{"task_id": "task-0", "target_index": 0}],
        },
        "official": {"channels_failed_to_enqueue": 0},
        "kol_pool_light": {"failed_to_enqueue": 0},
        "enqueue_failures": 0,
        "completion_scope": "enqueue_only",
        "provider_completion": "unknown",
        "completion": _completion("queued", scope="enqueue_only"),
    }
    daily_batch.finish_parent(
        "detached",
        "queued",
        initial,
        write=lambda sql, params: first_write.append((sql, params)) or 1,
    )
    persisted = json.loads(str(first_write[0][1][2]))
    assert persisted["phase"] == "children_enqueued"
    assert (persisted["processed"], persisted["total"]) == (1, 1)

    terminal_write: list[tuple[str, tuple[Any, ...]]] = []
    result = asyncio.run(
        daily_batch.reconcile_recent_parents(
            DurableQueue({"task-0": "done"}),
            load=lambda: [{
                "run_id": "detached",
                "updated_at": datetime.now(timezone.utc),
                "summary_json": json.dumps(persisted),
            }],
            write=lambda sql, params: terminal_write.append((sql, params)) or 1,
        )
    )

    assert result == {"checked": 1, "reconciled": 1, "pending": 0, "failed": 0}
    terminal = json.loads(str(terminal_write[0][1][5]))
    assert terminal["status"] == "completed"
    assert terminal["completion_scope"] == "provider_terminal"
    assert terminal["provider_completion"] == "completed"


def test_reconcile_no_work_and_all_enqueue_failed_are_terminal_with_distinct_statuses() -> None:
    writes: list[tuple[str, tuple[Any, ...]]] = []

    def row(run_id: str, failures: int) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc),
            "summary_json": json.dumps({
                "phase": "children_enqueued",
                "batch": {"requested": failures, "task_ids": [], "task_links": []},
                "official": {"channels_failed_to_enqueue": failures},
                "kol_pool_light": {"failed_to_enqueue": 0},
                "enqueue_failures": failures,
            }),
        }

    result = asyncio.run(
        daily_batch.reconcile_recent_parents(
            DurableQueue(),
            load=lambda: [row("no-work", 0), row("all-failed", 3)],
            write=lambda sql, params: writes.append((sql, params)) or 1,
        )
    )

    assert result == {"checked": 2, "reconciled": 2, "pending": 0, "failed": 0}
    assert writes[0][1][1] == "completed"
    assert writes[1][1][1] == "failed"


def test_reconcile_failure_is_followed_by_guard_before_any_new_parent_or_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.sync import daily_sync

    events: list[str] = []

    def guard(_payload: dict[str, Any]) -> None:
        events.append("guard")
        if events.count("guard") == 2:
            raise RuntimeError("stale parent now blocks")

    async def reconcile(_queue: Any) -> dict[str, int]:
        events.append("reconcile_failed_parent")
        return {"checked": 1, "reconciled": 0, "pending": 0, "failed": 1}

    monkeypatch.setattr(daily_sync, "check_daily_sync_guard", guard)
    monkeypatch.setattr(daily_sync, "_bool", lambda value: bool(value))
    monkeypatch.setattr(daily_sync, "_kol_refresh_selector", lambda _payload: "qualified")
    monkeypatch.setattr(daily_batch, "reconcile_recent_parents", reconcile)
    monkeypatch.setattr(
        daily_batch,
        "insert_parent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("new parent inserted")),
    )
    monkeypatch.setattr(
        daily_batch,
        "queue_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("children enqueued")),
    )

    with pytest.raises(RuntimeError, match="stale parent now blocks"):
        asyncio.run(
            cron.run_job(
                "daily_incremental_sync",
                {"skip_official": True, "skip_kol": True},
                queue=DurableQueue(),
            )
        )
    assert events == ["guard", "reconcile_failed_parent", "guard"]


def test_parent_insert_failure_happens_before_child_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains import channels
    from app.domains.sync import daily_sync

    monkeypatch.setattr(daily_sync, "check_daily_sync_guard", lambda _payload: None)
    monkeypatch.setattr(daily_sync, "_bool", lambda value: bool(value))
    monkeypatch.setattr(daily_sync, "_kol_refresh_selector", lambda _payload: "qualified")
    monkeypatch.setattr(channels, "list_channels", lambda **_kwargs: {"channels": [{"id": 1}]})
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
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parent insert failed")),
    )
    monkeypatch.setattr(
        daily_batch,
        "queue_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("children enqueued")),
    )

    with pytest.raises(RuntimeError, match="parent insert failed"):
        asyncio.run(
            cron.run_job(
                "daily_incremental_sync",
                {"skip_kol": True},
                queue=DurableQueue(),
            )
        )


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


@pytest.mark.parametrize(
    ("result", "expected_action"),
    [
        (
            {
                "status": "queued",
                "batch_id": "q",
                "provider_completion": "unknown",
                "completion": {"complete": False, "completion_scope": "bounded_observation", "tasks_pending": 1},
            },
            "cron_run_accepted",
        ),
        (
            {
                "status": "completed",
                "batch_id": "n",
                "provider_completion": "not_run",
                "enqueue_failures": 0,
                "task_ids": [],
                "completion": {
                    "complete": True, "completion_scope": "no_work",
                    "provider_completion": "not_run", "tasks_total": 0,
                },
            },
            "cron_run_completed",
        ),
        (
            {
                "status": "completed",
                "batch_id": "c",
                "provider_completion": "completed",
                "task_ids": ["t1"],
                "completion": {
                    "complete": True, "completion_scope": "provider_terminal",
                    "provider_completion": "completed", "tasks_total": 1,
                    "tasks_terminal": 1, "tasks_succeeded": 1,
                },
            },
            "cron_run_completed",
        ),
        (
            {
                "status": "partial",
                "provider_completion": "partial",
                "completion": {"complete": True, "completion_scope": "provider_terminal", "tasks_failed": 1},
            },
            "cron_run_accepted",
        ),
    ],
)
def test_manual_audit_only_marks_real_terminal_or_no_work_completed(
    monkeypatch: pytest.MonkeyPatch,
    result: dict[str, Any],
    expected_action: str,
) -> None:
    audits: list[dict[str, Any]] = []

    async def run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return result

    monkeypatch.setattr(cron, "run_job", run)
    monkeypatch.setattr(cron, "_log_cron_audit", lambda **kwargs: audits.append(kwargs))
    returned = asyncio.run(
        cron.run_manual_job(
            "daily_incremental_sync",
            {"confirm": "RUN daily_incremental_sync"},
            staff={"id": 7},
            queue=DurableQueue(),
        )
    )

    assert returned is result
    assert [row["action_type"] for row in audits] == ["cron_run_requested", expected_action]
    summary = audits[-1]["metadata"]["result"]
    assert summary.get("batch_id") == result.get("batch_id")
    if result.get("completion"):
        assert "completion_scope" in summary
        assert isinstance(summary.get("tasks_total"), int) or "task_ids" not in result
