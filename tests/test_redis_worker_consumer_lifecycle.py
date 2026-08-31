"""Redis worker consumer dispatch, provider fencing, and lease lifecycle tests."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyBudgetDecision,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
)
from app.services.jobs import queue as queue_mod
from app.workers import redis_worker_runtime as runtime
from app.workers import job_subprocess
from app.workers import worker_main
from tests.redis_job_queue_test_support import ledger_conn, queue, seed  # noqa: F401


class _LoopQueue:
    def __init__(self, status: str = "queued", timeout_seconds: float = 300) -> None:
        self.status = status
        self.timeout_seconds = timeout_seconds
        self.started_at = "old-start"
        self.deadline_started_at = datetime.now(timezone.utc).isoformat()
        self.pop_count = 0
        self.acks: list[dict] = []
        self.dead: list[tuple[dict, str]] = []

    async def pop_job(self, **kwargs):
        self.pop_count += 1
        if self.pop_count == 1:
            if self.status == "queued":
                self.status = "processing"
            return {"task_id": "task-1", "job_type": "vkpi_official_channel_sync", "payload": {}, "_stream_id": "1-0"}
        raise asyncio.CancelledError

    async def get_status(self, task_id: str):
        return {"status": self.status}

    async def ack(self, raw_job: dict):
        self.acks.append(raw_job)

    async def move_to_dead_letter(self, raw_job: dict, reason: str):
        self.dead.append((raw_job, reason))
        self.status = "failed"

    async def set_status(self, task_id: str, status: str, **kwargs):
        self.status = status

    async def authorize_provider_dispatch(self, task_id: str, stream_id: str):
        if self.status == "retrying":
            self.status = "processing"
            self.started_at = "reset-after-provider-claim"
            self.deadline_started_at = datetime.now(timezone.utc).isoformat()
        return {
            "authorized": self.status in {"processing", "running"},
            "status": self.status,
            "timeout_seconds": self.timeout_seconds,
            "started_at": self.deadline_started_at,
        }


def _allow_provider_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_main, "acquire_provider_execution_claim", lambda *args, **kwargs: 1)
    monkeypatch.setattr(worker_main, "renew_provider_execution_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(worker_main, "finalize_provider_execution_claim", lambda *args, **kwargs: True)


def test_handler_deadline_deducts_elapsed_ledger_time() -> None:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    remaining = worker_main._handler_deadline_seconds(
        {
            "timeout_seconds": 300,
            "started_at": (now - timedelta(seconds=275)).isoformat(),
        },
        now=now,
    )

    assert remaining == pytest.approx(25.0)


def test_handler_deadline_rejects_unparseable_ledger_anchor() -> None:
    with pytest.raises(
        worker_main.WorkerHandlerProcessError,
        match="parseable ledger started_at",
    ):
        worker_main._handler_deadline_seconds(
            {"timeout_seconds": 300, "started_at": "not-a-timestamp"}
        )


def test_isolated_handler_cleanup_failure_does_not_mask_success_or_failure(
    monkeypatch,
) -> None:
    class CleanupFailureQueue:
        async def close(self) -> None:
            raise RuntimeError("queue cleanup failed")

    queue = CleanupFailureQueue()

    monkeypatch.setattr(job_subprocess, "RedisJobQueue", lambda _url: queue)

    async def db_cleanup_failure() -> None:
        raise RuntimeError("database cleanup failed")

    monkeypatch.setattr(job_subprocess, "close_db_runtime", db_cleanup_failure)

    async def success(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(job_subprocess, "process_background_job", success)
    asyncio.run(job_subprocess._run({"task_id": "cleanup-success"}, 1))

    async def handler_failure(*_args, **_kwargs) -> None:
        raise ValueError("primary handler failure")

    monkeypatch.setattr(job_subprocess, "process_background_job", handler_failure)
    with pytest.raises(ValueError, match="primary handler failure"):
        asyncio.run(job_subprocess._run({"task_id": "cleanup-failure"}, 2))


@pytest.mark.parametrize(
    ("exit_code", "error_type"),
    [
        (0, None),
        (worker_main.EXIT_BUDGET_BLOCKED, worker_main.WorkerIsolatedBudgetBlocked),
        (worker_main.EXIT_PROVIDER_REPLAY_BLOCKED, ApifyProviderReplayBlocked),
        (worker_main.EXIT_EXECUTION_CLAIM_BLOCKED, ApifyExecutionClaimBlocked),
        (1, worker_main.WorkerHandlerProcessError),
    ],
)
def test_handler_subprocess_inherits_logs_and_maps_typed_exit(
    monkeypatch,
    exit_code: int,
    error_type: type[BaseException] | None,
) -> None:
    launches: list[dict[str, Any]] = []

    class FakeProcess:
        returncode = exit_code

        async def communicate(self, request: bytes):
            assert b'"fence_token": 7' in request
            return None, None

    async def fake_exec(*_args, **kwargs):
        launches.append(dict(kwargs))
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    invocation = worker_main._run_handler(
        object(), {"task_id": "isolated-exit-map"}, 7
    )
    if error_type is None:
        asyncio.run(invocation)
    else:
        with pytest.raises(error_type):
            asyncio.run(invocation)

    assert len(launches) == 1
    assert launches[0]["stdin"] is asyncio.subprocess.PIPE
    assert "stdout" not in launches[0]
    assert "stderr" not in launches[0]


def test_budget_block_never_uses_success_ack_and_goes_to_dlq(monkeypatch) -> None:
    queue = _LoopQueue()
    _allow_provider_claim(monkeypatch)

    async def blocked(*args, **kwargs):
        raise ApifyBudgetBlocked(
            ApifyBudgetDecision(
                allowed=False,
                scope="provider:apify",
                estimated_cost_usd=0.001,
                reason="hard_stop_or_projected_cap",
                operation="official_sync",
                actor_id="vendor/actor",
                platform="instagram",
                source="test",
            )
        )

    monkeypatch.setattr(worker_main, "_run_handler", blocked)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert queue.acks == []
    assert len(queue.dead) == 1
    assert "budget_blocked" in queue.dead[0][1]


def test_handler_failed_return_is_dlq_not_success_ack(monkeypatch) -> None:
    queue = _LoopQueue()
    _allow_provider_claim(monkeypatch)

    async def failed(*args, **kwargs):
        queue.status = "failed"

    monkeypatch.setattr(worker_main, "_run_handler", failed)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert queue.acks == []
    assert len(queue.dead) == 1


def test_only_ackable_terminal_status_is_success_acked(monkeypatch) -> None:
    queue = _LoopQueue()
    _allow_provider_claim(monkeypatch)

    async def done(*args, **kwargs):
        queue.status = "done"

    monkeypatch.setattr(worker_main, "_run_handler", done)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert len(queue.acks) == 1
    assert queue.dead == []


def test_terminal_after_provider_claim_blocks_handler_dispatch(monkeypatch) -> None:
    queue = _LoopQueue(status="processing")
    handler_called = False
    finalized: list[tuple[str, int, str]] = []

    def acquire(*args, **kwargs):
        queue.status = "failed"
        return 7

    def finalize(task_id: str, fence: int, state: str):
        finalized.append((task_id, fence, state))
        return True

    async def forbidden_handler(*args, **kwargs):
        nonlocal handler_called
        handler_called = True

    monkeypatch.setattr(worker_main, "acquire_provider_execution_claim", acquire)
    monkeypatch.setattr(worker_main, "finalize_provider_execution_claim", finalize)
    monkeypatch.setattr(worker_main, "_run_handler", forbidden_handler)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert handler_called is False
    assert len(queue.acks) == 1
    assert queue.dead == []
    assert finalized == [("task-1", 7, "failed")]


def test_stale_retried_job_advances_to_processing_and_executes(monkeypatch) -> None:
    queue = _LoopQueue(status="retrying")
    handler_statuses: list[str] = []

    async def handler(q, raw_job, _fence):
        handler_statuses.append(q.status)
        await q.set_status(raw_job["task_id"], "done")

    _allow_provider_claim(monkeypatch)
    monkeypatch.setattr(worker_main, "_run_handler", handler)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert handler_statuses == ["processing"]
    assert queue.started_at == "reset-after-provider-claim"
    assert len(queue.acks) == 1
    assert queue.dead == []


def test_real_stale_reclaim_advances_through_authorize_and_executes(
    queue,
    ledger_conn,
    monkeypatch,
) -> None:
    old_started_at = "2000-01-01T00:00:00Z"
    task_id = "stale-real-dispatch"
    stream_id = "21-0"
    fields = {
        "task_id": task_id,
        "job_type": "vkpi_test",
        "submission_id": "0",
        "payload_json": "{}",
    }
    seed(
        ledger_conn,
        task_id,
        status="processing",
        stream_id="old-consumer-stream",
        started_at=old_started_at,
    )
    queue._client.xpending_result = [
        {
            "message_id": stream_id,
            "time_since_delivered": queue_mod.REDIS_JOB_CLAIM_IDLE_MS + 1,
        }
    ]
    queue._client.xrange_result = [(stream_id, fields)]
    queue._client.xclaim_result = [(stream_id, fields)]
    monkeypatch.setattr(
        queue,
        "_provider_execution_claim_is_live",
        lambda candidate: False,
    )
    reads = 0

    async def read_then_stop(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 1:
            return []
        raise asyncio.CancelledError

    queue._client.xreadgroup = read_then_stop
    observed: list[tuple[str, str]] = []

    async def handler(actual_queue, raw_job, _fence):
        snapshot = await actual_queue.get_status(raw_job["task_id"])
        observed.append((snapshot["status"], snapshot["started_at"]))
        await actual_queue.set_status(raw_job["task_id"], "done", stage="done")

    _allow_provider_claim(monkeypatch)
    monkeypatch.setattr(worker_main, "_run_handler", handler)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert len(observed) == 1
    assert observed[0][0] == "processing"
    assert observed[0][1] != old_started_at
    assert queue._client.acked == [stream_id]
    row = ledger_conn.execute(
        "SELECT status, started_at FROM job_execution_ledger WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "done"
    assert row["started_at"] == observed[0][1]


def test_real_stale_reclaim_terminal_race_blocks_handler(
    queue,
    ledger_conn,
    monkeypatch,
) -> None:
    task_id = "stale-terminal-race"
    stream_id = "22-0"
    fields = {
        "task_id": task_id,
        "job_type": "vkpi_test",
        "submission_id": "0",
        "payload_json": "{}",
    }
    seed(
        ledger_conn,
        task_id,
        status="processing",
        stream_id="old-consumer-stream",
        started_at="2000-01-01T00:00:00Z",
    )
    queue._client.xpending_result = [
        {
            "message_id": stream_id,
            "time_since_delivered": queue_mod.REDIS_JOB_CLAIM_IDLE_MS + 1,
        }
    ]
    queue._client.xrange_result = [(stream_id, fields)]
    queue._client.xclaim_result = [(stream_id, fields)]
    monkeypatch.setattr(
        queue,
        "_provider_execution_claim_is_live",
        lambda candidate: False,
    )
    reads = 0

    async def read_then_stop(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 1:
            return []
        raise asyncio.CancelledError

    queue._client.xreadgroup = read_then_stop
    handler_called = False
    finalized: list[tuple[str, int, str]] = []

    def terminalize_after_claim(candidate, *args, **kwargs):
        snapshot = queue._update_job_ledger(candidate, "failed", stage="failed")
        assert snapshot and snapshot["status"] == "failed"
        return 9

    def finalize(candidate: str, fence: int, state: str):
        finalized.append((candidate, fence, state))
        return True

    async def forbidden_handler(*args, **kwargs):
        nonlocal handler_called
        handler_called = True

    monkeypatch.setattr(
        worker_main,
        "acquire_provider_execution_claim",
        terminalize_after_claim,
    )
    monkeypatch.setattr(worker_main, "finalize_provider_execution_claim", finalize)
    monkeypatch.setattr(worker_main, "_run_handler", forbidden_handler)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert handler_called is False
    assert queue._client.acked == [stream_id]
    assert finalized == [(task_id, 9, "failed")]
    row = ledger_conn.execute(
        "SELECT status FROM job_execution_ledger WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "failed"


def test_live_provider_claim_is_left_pending_without_dlq_or_ack(monkeypatch) -> None:
    queue = _LoopQueue()

    def blocked(*args, **kwargs):
        raise ApifyExecutionClaimBlocked("another consumer owns a live lease")

    monkeypatch.setattr(worker_main, "acquire_provider_execution_claim", blocked)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert queue.acks == []
    assert queue.dead == []
    assert queue.status == "retrying"


def test_lost_provider_lease_cancels_handler_and_leaves_message_pending(monkeypatch) -> None:
    queue = _LoopQueue()
    cancelled = asyncio.Event()

    async def long_running(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(worker_main, "_run_handler", long_running)
    monkeypatch.setattr(worker_main, "acquire_provider_execution_claim", lambda *args, **kwargs: 1)
    monkeypatch.setattr(worker_main, "renew_provider_execution_claim", lambda *args, **kwargs: False)
    monkeypatch.setattr(worker_main, "_PROVIDER_CLAIM_RENEW_SECONDS", 0.001)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert cancelled.is_set()
    assert queue.acks == []
    assert queue.dead == []
    assert queue.status == "retrying"


def test_lost_provider_lease_reaps_child_before_queue_state_change(
    monkeypatch,
    tmp_path,
) -> None:
    processes: list[asyncio.subprocess.Process] = []
    state_events: list[str] = []
    started_path = tmp_path / "claim-loss-started"
    late_write_path = tmp_path / "claim-loss-late-write"
    child_code = f"""
import time
from pathlib import Path
Path({str(started_path)!r}).write_text('started', encoding='utf-8')
time.sleep(0.8)
Path({str(late_write_path)!r}).write_text('late', encoding='utf-8')
"""

    class ClaimLossQueue(_LoopQueue):
        async def get_status(self, task_id: str):
            assert processes and processes[0].returncode is not None
            assert not worker_main._handler_process_group_exists(processes[0].pid)
            state_events.append("get_status_after_reap")
            return {"status": self.status}

        async def set_status(self, task_id: str, status: str, **kwargs):
            assert processes and processes[0].returncode is not None
            assert not worker_main._handler_process_group_exists(processes[0].pid)
            state_events.append("set_status_after_reap")
            self.status = status

    queue = ClaimLossQueue()

    async def isolated_handler(*_args, **_kwargs):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            child_code,
            stdin=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        processes.append(process)
        await worker_main._communicate_handler_process(process, b"")

    def lose_claim(*_args, **_kwargs):
        deadline = time.monotonic() + 2
        while not started_path.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert started_path.exists()
        return False

    monkeypatch.setattr(worker_main, "_run_handler", isolated_handler)
    monkeypatch.setattr(
        worker_main, "acquire_provider_execution_claim", lambda *args, **kwargs: 1
    )
    monkeypatch.setattr(worker_main, "renew_provider_execution_claim", lose_claim)
    monkeypatch.setattr(worker_main, "_PROVIDER_CLAIM_RENEW_SECONDS", 0.001)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert state_events == ["get_status_after_reap", "set_status_after_reap"]
    assert queue.acks == []
    assert queue.dead == []
    assert queue.status == "retrying"
    time.sleep(0.8)
    assert not late_write_path.exists()


def test_handler_deadline_kills_real_to_thread_process_before_late_write(
    monkeypatch,
    tmp_path,
) -> None:
    queue = _LoopQueue(timeout_seconds=0.2)
    finalized: list[tuple[str, int, str]] = []
    processes: list[asyncio.subprocess.Process] = []
    started_path = tmp_path / "started"
    late_write_path = tmp_path / "late-write"
    child_code = f"""
import asyncio
import time
from pathlib import Path

def blocking_provider():
    Path({str(started_path)!r}).write_text('started', encoding='utf-8')
    time.sleep(0.8)
    Path({str(late_write_path)!r}).write_text('late', encoding='utf-8')

asyncio.run(asyncio.to_thread(blocking_provider))
"""

    async def isolated_sync_handler(*_args, **_kwargs):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            child_code,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        processes.append(process)
        await worker_main._communicate_handler_process(process, b"")

    monkeypatch.setattr(worker_main, "_run_handler", isolated_sync_handler)
    monkeypatch.setattr(worker_main, "acquire_provider_execution_claim", lambda *args, **kwargs: 11)
    monkeypatch.setattr(worker_main, "renew_provider_execution_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        worker_main,
        "finalize_provider_execution_claim",
        lambda task_id, fence, state: finalized.append((task_id, fence, state)) or True,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert started_path.exists()
    assert len(processes) == 1
    assert processes[0].returncode is not None
    assert finalized == [("task-1", 11, "unknown")]
    assert len(queue.dead) == 1
    assert "handler_timeout_no_retry" in queue.dead[0][1]
    assert "provider outcome is unknown" in queue.dead[0][1]
    assert queue.status == "failed"
    # Past the blocking function's original completion time, the killed
    # process cannot resume its to_thread callback and write late state.
    time.sleep(0.8)
    assert not late_write_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="process-group contract is POSIX-only")
def test_handler_deadline_kills_sigterm_ignoring_grandchild_process_group(
    monkeypatch,
    tmp_path,
) -> None:
    queue = _LoopQueue(timeout_seconds=0.5)
    processes: list[asyncio.subprocess.Process] = []
    grandchild_ready = tmp_path / "grandchild-ready"
    grandchild_late = tmp_path / "grandchild-late-write"
    grandchild_pid = tmp_path / "grandchild-pid"
    grandchild_code = f"""
import os
import signal
import time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path({str(grandchild_ready)!r}).write_text('ready', encoding='utf-8')
time.sleep(1.2)
Path({str(grandchild_late)!r}).write_text('late', encoding='utf-8')
time.sleep(10)
"""
    leader_code = f"""
import subprocess
import sys
import time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', {grandchild_code!r}])
Path({str(grandchild_pid)!r}).write_text(str(child.pid), encoding='utf-8')
while True:
    time.sleep(1)
"""

    async def process_tree_handler(*_args, **_kwargs):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            leader_code,
            stdin=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        processes.append(process)
        await worker_main._communicate_handler_process(process, b"")

    monkeypatch.setattr(worker_main, "_run_handler", process_tree_handler)
    monkeypatch.setattr(
        worker_main, "acquire_provider_execution_claim", lambda *args, **kwargs: 13
    )
    monkeypatch.setattr(
        worker_main, "renew_provider_execution_claim", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(worker_main, "finalize_provider_execution_claim", lambda *a, **k: True)
    monkeypatch.setattr(worker_main, "_HANDLER_TERMINATE_GRACE_SECONDS", 0.15)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert grandchild_ready.exists()
    assert grandchild_pid.exists()
    assert processes and processes[0].returncode is not None
    pgid = processes[0].pid
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)
    time.sleep(1.2)
    assert not grandchild_late.exists()


def test_local_stale_backlog_blocks_before_any_redis_claim(monkeypatch) -> None:
    class Conn:
        def execute(self, sql, params):
            assert "job_execution_ledger" in sql
            return self

        def fetchone(self):
            return {"n": 18, "oldest": "2026-05-19T00:00:00Z"}

    monkeypatch.setattr(runtime, "table_exists", lambda name: True)
    monkeypatch.setattr(runtime, "get_conn", lambda: Conn())
    monkeypatch.setenv("VKPI_REDIS_WORKER_ALLOW_STALE_BACKLOG", "0")

    with pytest.raises(RuntimeError, match="18 active jobs"):
        runtime.stale_backlog_preflight()


def test_to_thread_preflight_and_heartbeats_release_every_postgres_lease(monkeypatch) -> None:
    """Executor thread reuse must not retain one pool lease per thread."""

    from app.db import connection as db_connection

    identity = runtime.RedisWorkerIdentity(
        worker_name="redis-worker-main",
        pid=4321,
        worker_git_sha="a" * 40,
        boot_nonce_sha256="b" * 64,
        started_at="2026-07-14T22:00:00Z",
    )
    build_barrier = threading.Barrier(4)
    built: list[FakeConn] = []

    class Cursor:
        def __init__(self, row=None) -> None:
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __init__(self) -> None:
            self.closed = False
            self.commits = 0

        def execute(self, sql, params=None):
            statement = " ".join(str(sql).split())
            if "to_regclass" in statement:
                return Cursor({"regclass": "public.test_table"})
            if "COUNT(*) AS n" in statement:
                return Cursor({"n": 0, "oldest": None})
            if "INSERT INTO vkpi_worker_heartbeat" in statement:
                return Cursor()
            raise AssertionError(statement)

        def commit(self) -> None:
            self.commits += 1

        def close(self) -> None:
            self.closed = True

    def build(*, release_validation_guard: bool):
        assert release_validation_guard is False
        conn = FakeConn()
        built.append(conn)
        build_barrier.wait(timeout=5)
        return conn

    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(db_connection, "_build_postgres_conn", build)

    def invoke(index: int):
        if index % 2:
            runtime.upsert_redis_worker_heartbeat(
                identity,
                {
                    "redis_ready": True,
                    "redis_stream_key": "vkpi:jobs",
                    "redis_group_name": "vkpi-workers",
                    "redis_consumer_count": 2,
                },
                interval_seconds=15,
            )
        else:
            assert runtime.stale_backlog_preflight()["pass"] is True
        return getattr(db_connection._db_local, "conn", None)

    async def exercise() -> list[object | None]:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=4))
        return await asyncio.gather(*(asyncio.to_thread(invoke, index) for index in range(24)))

    thread_locals = asyncio.run(exercise())

    assert len(built) == 24
    assert all(conn.closed for conn in built)
    assert sum(conn.commits for conn in built) == 12
    assert thread_locals == [None] * 24
