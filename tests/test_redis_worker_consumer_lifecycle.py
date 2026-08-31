"""Redis worker consumer dispatch, provider fencing, and lease lifecycle tests."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyBudgetDecision,
    ApifyExecutionClaimBlocked,
)
from app.services.jobs import queue as queue_mod
from app.workers import redis_worker_runtime as runtime
from app.workers import worker_main
from tests.redis_job_queue_test_support import ledger_conn, queue, seed  # noqa: F401


class _LoopQueue:
    def __init__(self, status: str = "queued") -> None:
        self.status = status
        self.started_at = "old-start"
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
        return {
            "authorized": self.status in {"processing", "running"},
            "status": self.status,
        }


def _allow_provider_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_main, "acquire_provider_execution_claim", lambda *args, **kwargs: 1)
    monkeypatch.setattr(worker_main, "renew_provider_execution_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(worker_main, "finalize_provider_execution_claim", lambda *args, **kwargs: True)


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

    monkeypatch.setattr(worker_main, "process_background_job", blocked)
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

    monkeypatch.setattr(worker_main, "process_background_job", failed)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert queue.acks == []
    assert len(queue.dead) == 1


def test_only_ackable_terminal_status_is_success_acked(monkeypatch) -> None:
    queue = _LoopQueue()
    _allow_provider_claim(monkeypatch)

    async def done(*args, **kwargs):
        queue.status = "done"

    monkeypatch.setattr(worker_main, "process_background_job", done)
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
    monkeypatch.setattr(worker_main, "process_background_job", forbidden_handler)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert handler_called is False
    assert len(queue.acks) == 1
    assert queue.dead == []
    assert finalized == [("task-1", 7, "failed")]


def test_stale_retried_job_advances_to_processing_and_executes(monkeypatch) -> None:
    queue = _LoopQueue(status="retrying")
    handler_statuses: list[str] = []

    async def handler(q, raw_job):
        handler_statuses.append(q.status)
        await q.set_status(raw_job["task_id"], "done")

    _allow_provider_claim(monkeypatch)
    monkeypatch.setattr(worker_main, "process_background_job", handler)

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

    async def handler(actual_queue, raw_job):
        snapshot = await actual_queue.get_status(raw_job["task_id"])
        observed.append((snapshot["status"], snapshot["started_at"]))
        await actual_queue.set_status(raw_job["task_id"], "done", stage="done")

    _allow_provider_claim(monkeypatch)
    monkeypatch.setattr(worker_main, "process_background_job", handler)

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
    monkeypatch.setattr(worker_main, "process_background_job", forbidden_handler)

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

    monkeypatch.setattr(worker_main, "process_background_job", long_running)
    monkeypatch.setattr(worker_main, "acquire_provider_execution_claim", lambda *args, **kwargs: 1)
    monkeypatch.setattr(worker_main, "renew_provider_execution_claim", lambda *args, **kwargs: False)
    monkeypatch.setattr(worker_main, "_PROVIDER_CLAIM_RENEW_SECONDS", 0.001)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_main._consumer_loop(queue, 1))

    assert cancelled.is_set()
    assert queue.acks == []
    assert queue.dead == []
    assert queue.status == "retrying"


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
