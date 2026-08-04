from __future__ import annotations

import asyncio
import inspect
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyBudgetDecision,
    ApifyExecutionClaimBlocked,
)
from app import main as main_mod
from app.services.jobs import queue as queue_mod
from app.workers import redis_worker_health
from app.workers import redis_worker_runtime as runtime
from app.workers import worker_main


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import verify_redis_worker_health as redis_gate  # noqa: E402


class _LoopQueue:
    def __init__(self, status: str = "queued") -> None:
        self.status = status
        self.pop_count = 0
        self.acks: list[dict] = []
        self.dead: list[tuple[dict, str]] = []

    async def pop_job(self, **kwargs):
        self.pop_count += 1
        if self.pop_count == 1:
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


def test_redis_worker_concurrency_fails_closed_above_reviewed_limit(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_REDIS_WORKER_MAX_CONSUMERS", "2")
    assert runtime.redis_worker_concurrency(2) == 2
    with pytest.raises(RuntimeError, match="exceeds reviewed hard max"):
        runtime.redis_worker_concurrency(15)


def test_worker_health_like_wildcards_are_bound_for_postgres(monkeypatch) -> None:
    now = datetime.now(timezone.utc)

    class Cursor:
        def fetchall(self):
            return [
                {
                    "worker_name": "apify-worker-interactive-test",
                    "latest": now,
                    "pid": 123,
                    "worker_git_sha": "a" * 40,
                    "boot_nonce_sha256": "b" * 64,
                    "started_at": now - timedelta(seconds=30),
                }
            ]

    class Conn:
        def execute(self, sql, params=None):
            assert "worker_name NOT LIKE ?" in str(sql)
            assert params == ("redis-worker-%",)
            return Cursor()

    import app.db.connection as db_connection

    monkeypatch.setattr(db_connection, "table_exists", lambda name: name == "vkpi_worker_heartbeat")
    monkeypatch.setattr(db_connection, "get_conn", lambda: Conn())
    monkeypatch.setenv("APIFY_WORKER_EXPECTED_INSTANCES", "1")
    monkeypatch.setattr(main_mod, "APP_GIT_SHA", "a" * 40)

    report = main_mod._trust_worker()
    assert report["worker_online"] is True
    assert report["worker_fleet"]["online_count"] == 1


def test_redis_health_like_wildcard_is_bound_for_postgres(monkeypatch) -> None:
    now = datetime.now(timezone.utc)

    class Cursor:
        def fetchall(self):
            return [
                {
                    "worker_name": "redis-worker-main",
                    "last_heartbeat_at": now,
                    "pid": 456,
                    "worker_git_sha": "c" * 40,
                    "boot_nonce_sha256": "d" * 64,
                    "started_at": now - timedelta(seconds=45),
                    "redis_ready": True,
                    "redis_readiness_at": now,
                    "redis_stream_key": "v2-local:jobs:stream",
                    "redis_group_name": "viltrox-2.0-local-workers",
                    "redis_consumer_count": 2,
                    "redis_ready_sequence": 3,
                    "redis_heartbeat_interval_seconds": 15,
                    "redis_readiness_error_code": "",
                }
            ]

    class Conn:
        def execute(self, sql, params=None):
            assert "worker_name LIKE ?" in str(sql)
            assert params == ("redis-worker-%",)
            return Cursor()

    monkeypatch.setattr(redis_worker_health, "table_exists", lambda name: name == "vkpi_worker_heartbeat")
    monkeypatch.setattr(redis_worker_health, "get_conn", lambda: Conn())
    monkeypatch.setenv("VKPI_REDIS_WORKER_EXPECTED_INSTANCES", "1")

    report = redis_worker_health.redis_worker_fleet_health("c" * 40)
    assert report["online"] is True
    assert report["online_count"] == 1


def test_local_worker_cluster_detaches_from_launcher_process_group() -> None:
    source = (ROOT / "scripts" / "start_worker_cluster.sh").read_text(encoding="utf-8")
    assert "os.setsid()" in source
    assert "os.execvp" in source
    assert '[[ -z "${PYTHON_BIN:-}" && -x "$ROOT/.venv/bin/python" ]]' in source
    assert 'PYTHON_BIN="$ROOT/.venv/bin/python"' in source
    assert 'heartbeat_name="${VKPI_REDIS_WORKER_HEARTBEAT_NAME:-redis-worker-main}"' in source
    assert 'export VKPI_REDIS_WORKER_HEARTBEAT_NAME="$heartbeat_name"' in source
    assert 'PROFILE_CONSUMERS="${WORKER_ASYNC_CONSUMERS:-2}"' in source
    assert 'REDIS_CONSUMER_HARD_MAX="${VKPI_REDIS_WORKER_MAX_CONSUMERS:-2}"' in source
    assert "WORKER_ASYNC_CONSUMERS > REDIS_CONSUMER_HARD_MAX" in source


def test_redis_worker_db_preflight_is_read_only_and_does_not_run_startup_writes(monkeypatch) -> None:
    executed: list[str] = []

    class Cursor:
        def fetchone(self):
            return {"ok": 1}

    class Conn:
        def execute(self, sql, params=None):
            executed.append(" ".join(str(sql).split()))
            return Cursor()

    class SyncScope:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(runtime, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(runtime, "db_connection_sync_scope", lambda: SyncScope())
    monkeypatch.setattr(runtime, "get_conn", lambda: Conn())

    report = runtime.redis_worker_db_preflight()

    assert report["pass"] is True
    assert report["read_only"] is True
    assert report["migrations_run"] is False
    assert report["seeders_run"] is False
    assert len(executed) == 1 + len(runtime._REQUIRED_RUNTIME_COLUMNS)
    assert all(statement.startswith("SELECT ") for statement in executed)
    source = inspect.getsource(worker_main._worker_loop)
    assert "init_db_runtime" not in source
    assert source.index("redis_worker_db_preflight") < source.index("upsert_redis_worker_heartbeat")
    assert source.index("redis_worker_db_preflight") < source.index("RedisJobQueue")


def test_redis_worker_db_preflight_rejects_missing_schema(monkeypatch) -> None:
    class Cursor:
        def fetchone(self):
            return {"ok": 1}

    class Conn:
        def execute(self, sql, params=None):
            if "vkpi_worker_heartbeat" in str(sql):
                raise RuntimeError("missing required column")
            return Cursor()

    class SyncScope:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(runtime, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(runtime, "db_connection_sync_scope", lambda: SyncScope())
    monkeypatch.setattr(runtime, "get_conn", lambda: Conn())

    with pytest.raises(RuntimeError, match="database preflight failed"):
        runtime.redis_worker_db_preflight()


def test_redis_worker_db_preflight_rejects_non_postgres_runtime(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "is_postgres_runtime", lambda: False)
    with pytest.raises(RuntimeError, match="requires the Postgres runtime"):
        runtime.redis_worker_db_preflight()


def test_active_lock_idempotency_returns_existing_task_without_xadd(monkeypatch) -> None:
    queue = queue_mod.RedisJobQueue.__new__(queue_mod.RedisJobQueue)

    class Client:
        def __init__(self) -> None:
            self.xadds = 0

        async def xadd(self, *args, **kwargs):
            self.xadds += 1
            return "1-0"

    client = Client()
    queue._client = client

    async def ready():
        return None

    queue._ensure_ready = ready
    queue._find_active_lock_job = lambda lock_key: "existing-task"

    @asynccontextmanager
    async def scope():
        yield

    monkeypatch.setattr(queue_mod, "db_connection_scope", scope)
    task_id = asyncio.run(queue.enqueue("vkpi_test", {"x": 1}, lock_key="stable-lock"))

    assert task_id == "existing-task"
    assert client.xadds == 0


def test_worker_readiness_registers_consumers_without_claim_or_read() -> None:
    queue = queue_mod.RedisJobQueue.__new__(queue_mod.RedisJobQueue)
    queue._group = "vkpi-workers"
    queue._ready = True

    class Client:
        def __init__(self) -> None:
            self.created: list[str] = []

        async def ping(self):
            return True

        async def xinfo_groups(self, stream):
            return [{"name": "vkpi-workers"}]

        async def xgroup_createconsumer(self, stream, group, name):
            self.created.append(name)

        async def xinfo_consumers(self, stream, group):
            return [{"name": name} for name in self.created]

        async def xreadgroup(self, *args, **kwargs):
            raise AssertionError("readiness must not consume")

        async def xclaim(self, *args, **kwargs):
            raise AssertionError("readiness must not claim")

    queue._client = Client()
    report = asyncio.run(queue.worker_readiness(["redis-worker-main-slot-1", "redis-worker-main-slot-2"]))
    assert report == {
        "redis_ready": True,
        "redis_stream_key": queue_mod.REDIS_JOB_STREAM_KEY,
        "redis_group_name": "vkpi-workers",
        "redis_consumer_count": 2,
    }


def test_stale_pending_message_is_reclaimed_once_and_marked_retrying(monkeypatch) -> None:
    queue = queue_mod.RedisJobQueue.__new__(queue_mod.RedisJobQueue)
    queue._group = "test-group"

    class Client:
        async def xpending_range(self, *args, **kwargs):
            return [{"message_id": "7-0", "time_since_delivered": queue_mod.REDIS_JOB_CLAIM_IDLE_MS + 1}]

        async def xrange(self, *args, **kwargs):
            return [("7-0", {"task_id": "task-7", "job_type": "vkpi_test", "submission_id": "0", "payload_json": "{}"})]

        async def xclaim(self, *args, **kwargs):
            return [("7-0", {"task_id": "task-7", "job_type": "vkpi_test", "submission_id": "0", "payload_json": "{}"})]

        async def xack(self, *args, **kwargs):
            raise AssertionError("active stale message must not be acknowledged before processing")

    queue._client = Client()
    monkeypatch.setattr(queue, "_provider_execution_claim_is_live", lambda task_id: False)
    state = {"status": "queued", "retry_count": 0}

    async def get_status(task_id: str):
        return dict(state)

    async def set_status(task_id: str, status: str, **extra):
        state.update(status=status, retry_count=int(extra.get("retry_count") or state["retry_count"]))

    queue.get_status = get_status
    queue.set_status = set_status

    @asynccontextmanager
    async def scope():
        yield

    monkeypatch.setattr(queue_mod, "db_connection_scope", scope)
    claimed = asyncio.run(queue._claim_stale("redis-worker-test", count=1))

    assert [job["task_id"] for job in claimed] == ["task-7"]
    assert claimed[0]["_consumer_name"] == "redis-worker-test"
    assert state == {"status": "retrying", "retry_count": 1}


def test_stale_pending_message_with_live_provider_fence_is_not_xclaimed(monkeypatch) -> None:
    queue = queue_mod.RedisJobQueue.__new__(queue_mod.RedisJobQueue)
    queue._group = "test-group"

    class Client:
        async def xpending_range(self, *args, **kwargs):
            return [{"message_id": "8-0", "time_since_delivered": queue_mod.REDIS_JOB_CLAIM_IDLE_MS + 1}]

        async def xrange(self, *args, **kwargs):
            return [("8-0", {"task_id": "task-live", "job_type": "intel_scan_matrix", "payload_json": "{}"})]

        async def xclaim(self, *args, **kwargs):
            raise AssertionError("live provider work must never be XCLAIMed")

        async def xack(self, *args, **kwargs):
            raise AssertionError("live provider work must never be XACKed")

    queue._client = Client()
    queue.get_status = lambda task_id: asyncio.sleep(0, result={"status": "retrying"})
    monkeypatch.setattr(queue, "_provider_execution_claim_is_live", lambda task_id: True)

    @asynccontextmanager
    async def scope():
        yield

    monkeypatch.setattr(queue_mod, "db_connection_scope", scope)
    claimed = asyncio.run(queue._claim_stale("redis-worker-test", count=1))
    assert claimed == []


def test_periodic_redis_readiness_failure_revokes_ready_then_raises(monkeypatch) -> None:
    identity = runtime.RedisWorkerIdentity(
        worker_name="redis-worker-main",
        pid=4321,
        worker_git_sha="a" * 40,
        boot_nonce_sha256="b" * 64,
        started_at="2026-07-14T22:00:00Z",
    )
    writes: list[dict] = []

    def heartbeat(identity, readiness, **kwargs):
        writes.append(dict(readiness))

    async def broken_readiness():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(runtime, "redis_worker_heartbeat_interval", lambda: 0.001)
    monkeypatch.setattr(runtime, "upsert_redis_worker_heartbeat", heartbeat)
    with pytest.raises(RuntimeError, match="periodic readiness failed"):
        asyncio.run(
            runtime.redis_worker_heartbeat_loop(
                identity,
                asyncio.Event(),
                broken_readiness,
            )
        )
    assert writes == [{"redis_ready": False}]


def test_worker_supervisor_exits_nonzero_after_redis_readiness_loss(monkeypatch) -> None:
    identity = runtime.RedisWorkerIdentity(
        worker_name="redis-worker-main",
        pid=4321,
        worker_git_sha="a" * 40,
        boot_nonce_sha256="b" * 64,
        started_at="2026-07-14T22:00:00Z",
    )
    writes: list[dict] = []

    class Queue:
        def __init__(self, url: str) -> None:
            self.probes = 0

        async def worker_readiness(self, names):
            self.probes += 1
            if self.probes > 1:
                raise ConnectionError("redis lost")
            return {
                "redis_ready": True,
                "redis_stream_key": "vkpi:jobs",
                "redis_group_name": "vkpi-workers",
                "redis_consumer_count": len(names),
            }

        async def close(self):
            return None

    def heartbeat(identity, readiness, **kwargs):
        writes.append(dict(readiness))

    async def consumer(*args, **kwargs):
        await asyncio.Event().wait()

    async def close_db():
        return None

    monkeypatch.setattr(worker_main, "REDIS_URL", "redis://unit")
    monkeypatch.setattr(worker_main, "ENABLE_BROWSER", False)
    monkeypatch.setattr(worker_main, "RedisJobQueue", Queue)
    monkeypatch.setattr(worker_main, "redis_worker_db_preflight", lambda: {"pass": True})
    monkeypatch.setattr(worker_main, "build_redis_worker_identity", lambda: identity)
    monkeypatch.setattr(worker_main, "redis_worker_concurrency", lambda requested: 1)
    monkeypatch.setattr(worker_main, "stale_backlog_preflight", lambda: {"stale_active_count": 0})
    monkeypatch.setattr(worker_main, "redis_worker_heartbeat_interval", lambda: 0.001)
    monkeypatch.setattr(worker_main, "upsert_redis_worker_heartbeat", heartbeat)
    monkeypatch.setattr(worker_main, "_consumer_loop", consumer)
    monkeypatch.setattr(worker_main, "close_db_runtime", close_db)
    monkeypatch.setattr(runtime, "redis_worker_heartbeat_interval", lambda: 0.001)
    monkeypatch.setattr(runtime, "upsert_redis_worker_heartbeat", heartbeat)

    with pytest.raises(RuntimeError, match="periodic readiness failed"):
        asyncio.run(worker_main._worker_loop())
    assert writes[0]["redis_ready"] is True
    assert writes[-1] == {"redis_ready": False}


def test_job_timeout_is_terminal_before_late_handler_can_revert(monkeypatch, tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "queue.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE job_execution_ledger (
          id INTEGER PRIMARY KEY, task_id TEXT UNIQUE, job_type TEXT, submission_id INTEGER,
          user_id INTEGER, status TEXT, payload_json TEXT, retry_count INTEGER,
          created_at TEXT, updated_at TEXT, started_at TEXT, finished_at TEXT,
          timeout_seconds INTEGER, error_message TEXT, summary TEXT, detection_status TEXT,
          result_path TEXT, result_json TEXT, stats_json TEXT, stage TEXT,
          stream_id TEXT, consumer_name TEXT, extra_json TEXT
        );
        CREATE TABLE vkpi_async_task_items (task_id TEXT, status TEXT, error TEXT, updated_at TEXT);
        """
    )
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO job_execution_ledger VALUES (1,'task-timeout','vkpi_test',0,0,'queued','{}',0,?,?,NULL,NULL,1,'','','','','{}','{}','ingest',NULL,NULL,'{}')",
        (old, old),
    )
    conn.execute("INSERT INTO vkpi_async_task_items VALUES ('task-timeout','pending','',?)", (old,))
    conn.commit()
    queue = queue_mod.RedisJobQueue.__new__(queue_mod.RedisJobQueue)
    monkeypatch.setattr(queue_mod, "get_conn", lambda: conn)

    assert queue._mark_timed_out_jobs() == 1
    assert conn.execute("SELECT status FROM job_execution_ledger").fetchone()["status"] == "timeout"
    snapshot = queue._update_job_ledger("task-timeout", "done")
    assert snapshot["_stale_status_ignored"] is True
    assert conn.execute("SELECT status FROM job_execution_ledger").fetchone()["status"] == "timeout"
    conn.close()


def _health_payload(head: str) -> dict:
    return {
        "trust": {
            "redis_worker_fleet": {
                "online": True,
                "online_count": 1,
                "expected_count": 1,
                "unique_names": True,
                "unique_pids": True,
                "all_worker_sha_aligned": True,
                "all_redis_ready": True,
                "workers": [
                    {
                        "worker_name": "redis-worker-main",
                        "pid": 4321,
                        "worker_sha": head,
                        "boot_nonce_sha256": "b" * 64,
                        "started_at": "2026-07-14T21:59:00Z",
                        "heartbeat": "2026-07-14T22:00:00Z",
                        "redis_ready": True,
                        "redis_readiness_at": "2026-07-14T22:00:00Z",
                        "redis_stream_key": "vkpi:jobs",
                        "redis_group_name": "vkpi-workers",
                        "redis_consumer_count": 2,
                        "redis_ready_sequence": 3,
                        "redis_heartbeat_interval_seconds": 15,
                        "online": True,
                    }
                ],
            },
            "worker_fleet": {"workers": [{"worker_name": "apify-worker-interactive"}]},
        }
    }


def test_release_gate_accepts_separate_fresh_redis_worker_identity() -> None:
    head = "a" * 40
    report = redis_gate.validate_redis_worker_health(
        _health_payload(head),
        expected_head=head,
        expected_count=1,
        now=datetime(2026, 7, 14, 22, 1, tzinfo=timezone.utc),
    )
    assert report["pass"] is True


def test_release_gate_rejects_redis_identity_polluting_apify_fleet() -> None:
    head = "a" * 40
    payload = _health_payload(head)
    payload["trust"]["worker_fleet"]["workers"].append({"worker_name": "redis-worker-main"})
    report = redis_gate.validate_redis_worker_health(
        payload,
        expected_head=head,
        now=datetime(2026, 7, 14, 22, 1, tzinfo=timezone.utc),
    )
    assert report["pass"] is False
    assert "redis worker identity polluted the Apify worker fleet" in report["errors"]


def test_release_gate_binds_two_cycles_to_systemd_main_pid() -> None:
    head = "a" * 40
    accepted = redis_gate.validate_redis_worker_health(
        _health_payload(head),
        expected_head=head,
        expected_main_pid=4321,
        min_ready_sequence=3,
        now=datetime(2026, 7, 14, 22, 1, tzinfo=timezone.utc),
    )
    rejected = redis_gate.validate_redis_worker_health(
        _health_payload(head),
        expected_head=head,
        expected_main_pid=9999,
        min_ready_sequence=3,
        now=datetime(2026, 7, 14, 22, 1, tzinfo=timezone.utc),
    )
    assert accepted["pass"] is True
    assert rejected["pass"] is False
    assert "redis worker PID does not match systemd MainPID" in rejected["errors"]


def test_release_gate_rejects_early_boot_snapshot_until_two_cycles_complete() -> None:
    head = "a" * 40
    payload = _health_payload(head)
    worker = payload["trust"]["redis_worker_fleet"]["workers"][0]
    worker["started_at"] = "2026-07-14T22:00:00Z"
    worker["heartbeat"] = "2026-07-14T22:00:05Z"
    worker["redis_readiness_at"] = "2026-07-14T22:00:05Z"
    worker["redis_ready_sequence"] = 1
    not_before = datetime(2026, 7, 14, 22, 0, tzinfo=timezone.utc)

    early = redis_gate.validate_redis_worker_health(
        payload,
        expected_head=head,
        expected_main_pid=4321,
        min_ready_sequence=3,
        worker_not_before=not_before,
        now=datetime(2026, 7, 14, 22, 0, 5, tzinfo=timezone.utc),
    )

    assert early["pass"] is False
    assert "redis worker has not sustained readiness for two heartbeat cycles" in early["errors"]
    assert "redis worker boot has not survived two heartbeat cycles" in early["errors"]

    worker["heartbeat"] = "2026-07-14T22:00:30Z"
    worker["redis_readiness_at"] = "2026-07-14T22:00:30Z"
    worker["redis_ready_sequence"] = 3
    mature = redis_gate.validate_redis_worker_health(
        payload,
        expected_head=head,
        expected_main_pid=4321,
        min_ready_sequence=3,
        worker_not_before=not_before,
        now=datetime(2026, 7, 14, 22, 0, 30, tzinfo=timezone.utc),
    )

    assert mature["pass"] is True


def test_systemd_unit_is_non_root_conservative_and_stale_safe() -> None:
    unit = (ROOT / "scripts/ops/systemd/vkpi-redis-worker.service").read_text(encoding="utf-8")
    assert "User=viltrox" in unit
    assert "Group=viltrox" in unit
    assert "WORKER_ASYNC_CONSUMERS=2" in unit
    assert "VKPI_REDIS_WORKER_ALLOW_STALE_BACKLOG=0" in unit
    assert "POSTGRES_POOL_MIN_SIZE=2" in unit.split("ExecStart=", 1)[1]
    assert "POSTGRES_POOL_MAX_SIZE=16" in unit.split("ExecStart=", 1)[1]
    assert "POSTGRES_POOL_TIMEOUT_SEC=30" in unit.split("ExecStart=", 1)[1]
    assert "VKPI_REDIS_WORKER_HEARTBEAT_NAME=redis-worker-main" in unit
    assert "Environment=VKPI_JOB_RESULTS_DIR=/opt/viltrox-2.0/runtime/job-results" in unit
    assert "--job-results-dir /opt/viltrox-2.0/runtime/job-results" in unit
    assert "ReadWritePaths=/opt/viltrox-2.0/runtime/job-results" in unit
    assert "-m app.workers.worker_main" in unit
    assert "User=root" not in unit
    assert "WantedBy=multi-user.target" in unit


def test_deploy_enables_unit_and_binds_release_gate_to_systemd_main_pid() -> None:
    deploy = (ROOT / "scripts/ops/deploy_local_to_cloud.sh").read_text(encoding="utf-8")
    assert "VKPI_JOB_RESULTS_DIR='${REMOTE_ROOT}/runtime/job-results'" in deploy
    assert "--job-results-dir '${REMOTE_ROOT}/runtime/job-results'" in deploy
    start = deploy.index("sudo systemctl enable --now '${STAGING_REDIS_WORKER_SERVICE}'")
    main_pid = deploy.index("systemctl show --property MainPID --value", start)
    wait_gate = deploy.index("--expected-main-pid '${REDIS_WORKER_MAIN_PID}'", main_pid)
    final_gate = deploy.index('--expected-main-pid "${REDIS_WORKER_MAIN_PID}"', wait_gate)
    assert start < main_pid < wait_gate < final_gate
    assert deploy.count("--min-ready-sequence 3") >= 2
    for required in (
        "present:active:enabled:unmasked",
        "present:inactive:disabled:unmasked",
        "present:inactive:disabled:masked",
        "absent:inactive:disabled:unmasked",
        "sudo systemctl mask '${STAGING_REDIS_WORKER_SERVICE}'",
        "sudo systemctl disable --now '${STAGING_REDIS_WORKER_SERVICE}'",
        "sudo systemctl enable '${STAGING_REDIS_WORKER_SERVICE}'",
        "sudo systemctl disable '${STAGING_REDIS_WORKER_SERVICE}'",
        "sudo systemctl start '${STAGING_REDIS_WORKER_SERVICE}'",
        "sudo systemctl stop '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}",
        "rollback did not restore the exact Redis worker unit state",
    ):
        assert required in deploy

    rollback = deploy.split("attempt_automatic_rollback()", 1)[1].split(
        "cleanup_post_deploy_evidence()", 1
    )[0]
    stop_all_at = rollback.index("sudo systemctl stop '${SERVICE_NAME}'")
    disable_at = rollback.index("sudo systemctl disable --now", stop_all_at)
    restore_at = rollback.index("atomic_release_layout.py' restore")
    assert stop_all_at < disable_at < restore_at

    rollback_not_before_at = rollback.index(
        'rollback_redis_not_before="$(ssh "${SSH_TARGET}" "date -u',
        restore_at,
    )
    rollback_start_at = rollback.index(
        "sudo systemctl start '${STAGING_REDIS_WORKER_SERVICE}'",
        rollback_not_before_at,
    )
    rollback_main_pid_at = rollback.index(
        "systemctl show --property MainPID --value",
        rollback_start_at,
    )
    rollback_poll_at = rollback.index("for attempt in $(seq 1 90); do", rollback_main_pid_at)
    rollback_fresh_health_at = rollback.index(
        'rollback_candidate_health="$(ssh "${SSH_TARGET}"',
        rollback_poll_at,
    )
    rollback_wait_pid_at = rollback.index(
        '--expected-main-pid "${rollback_redis_main_pid}"',
        rollback_fresh_health_at,
    )
    rollback_promote_at = rollback.index(
        'rollback_health="${rollback_candidate_health}"',
        rollback_wait_pid_at,
    )
    rollback_runtime_gate_at = rollback.index(
        '"${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/verify_runtime_health.py"',
        rollback_promote_at,
    )
    rollback_final_refresh_at = rollback.index(
        'rollback_candidate_health="$(ssh "${SSH_TARGET}"',
        rollback_runtime_gate_at,
    )
    rollback_final_pid_at = rollback.index(
        '--expected-main-pid "${rollback_redis_main_pid}"',
        rollback_final_refresh_at,
    )
    rollback_final_promote_at = rollback.index(
        'rollback_health="${rollback_candidate_health}"',
        rollback_final_pid_at,
    )
    restore_sync_at = rollback.index("restore_remote_sync_unit_state", rollback_final_promote_at)
    assert (
        restore_at
        < rollback_not_before_at
        < rollback_start_at
        < rollback_main_pid_at
        < rollback_poll_at
        < rollback_fresh_health_at
        < rollback_wait_pid_at
        < rollback_promote_at
        < rollback_runtime_gate_at
        < rollback_final_refresh_at
        < rollback_final_pid_at
        < rollback_final_promote_at
        < restore_sync_at
    )
    assert rollback.count('rollback_candidate_health="$(ssh "${SSH_TARGET}"') == 2
    assert rollback.count('--expected-main-pid "${rollback_redis_main_pid}"') == 2
    assert rollback.count("--min-ready-sequence 3") == 2
