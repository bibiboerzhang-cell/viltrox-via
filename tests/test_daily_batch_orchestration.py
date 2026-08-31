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

from app.domains.sync import cron, daily_batch  # noqa: E402


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
