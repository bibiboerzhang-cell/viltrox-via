"""RedisJobQueue 拆分前行为锁(characterization)。

class-LOC 棘轮拆刀(queue.py::RedisJobQueue 782 行 → 门面 + claim/heartbeat/maintenance
三协作对象)之前,先把可观察行为逐条钉死:

- enqueue:台账列(priority/lock_key/timeout_seconds/留痕)、去重(lock_key +
  audit_submission)、插入失败 rollback + 赢家回读、XADD 字段、queued 事件;
- pop_job:XREADGROUP 命中即 processing(带 stream_id/consumer),终态竞态 ack+None,
  空转回落 _claim_stale;
- _claim_stale:processing 未超 timeout 窗不许抢,终态消息只 ack 不抢;
- timeout sweep:只清理已有真实 started_at 的 processing/running,排队/重试等待
  不消耗执行 timeout,且重试被重新 claim 时重置执行起点;
- move_to_dead_letter:死信流 + failed 终态 + ack;
- set_status:终态回退被吞时零事件;
- subscribe_task_events:空消息心跳 + finally 退订;
- worker_readiness:重名/空名拒绝;
- 模块级 monkeypatch 面(get_conn/db_connection_scope/常量/工具)必须留在 queue 模块。

拆分后本文件必须原样全绿——任何断言变化都意味着行为漂移。
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.db.connection import PostgresCompatConnection
from app.services.jobs import (
    queue as queue_mod,
    queue_claim,
    queue_enqueue,
    queue_heartbeat,
    queue_maintenance,
)
from app.services.jobs.queue_runtime import queue_facade


SCHEMA = """
CREATE TABLE job_execution_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL DEFAULT 'audit_submission',
    submission_id INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT DEFAULT '{}',
    retry_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_message TEXT,
    summary TEXT,
    detection_status TEXT,
    result_path TEXT,
    result_json TEXT DEFAULT '{}',
    stats_json TEXT DEFAULT '{}',
    stage TEXT,
    stream_id TEXT,
    consumer_name TEXT,
    extra_json TEXT DEFAULT '{}',
    priority INTEGER,
    lock_key TEXT,
    timeout_seconds INTEGER,
    triggered_by_staff_id INTEGER,
    task_chain_json TEXT
);
CREATE TABLE vkpi_async_task_items (task_id TEXT, status TEXT, error TEXT, updated_at TEXT);
CREATE TABLE vkpi_provider_execution_claims (
    task_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL
);
"""


def test_queue_collaborators_share_live_facade_without_reverse_import():
    assert queue_facade() is queue_mod
    for collaborator in (
        queue_claim,
        queue_enqueue,
        queue_heartbeat,
        queue_maintenance,
    ):
        assert collaborator._qm() is queue_mod
        source = Path(collaborator.__file__).read_text(encoding="utf-8")
        assert "from app.services.jobs import queue as queue_module" not in source


def test_queue_runtime_reload_keeps_existing_collaborators_bound():
    from app.services.jobs import queue_runtime

    importlib.reload(queue_runtime)
    assert queue_runtime.queue_facade() is queue_mod
    assert queue_claim._qm() is queue_mod
    assert queue_enqueue._qm() is queue_mod


class FakeRedis:
    def __init__(self) -> None:
        self.xadds: list[tuple[str, dict]] = []
        self.published: list[tuple[str, dict]] = []
        self.acked: list[str] = []
        self.deleted: list[str] = []
        self.xreadgroup_result: list = []
        self.xpending_result: list = []
        self.xrange_result: list = []
        self.xclaim_result: list = []
        self.xclaim_forbidden = False

    async def xadd(self, stream, fields):
        self.xadds.append((stream, dict(fields)))
        return f"{len(self.xadds)}-0"

    async def publish(self, channel, message):
        self.published.append((channel, json.loads(message)))

    async def xack(self, stream, group, message_id):
        self.acked.append(str(message_id))

    async def xdel(self, stream, message_id):
        self.deleted.append(str(message_id))
        return 1

    async def xreadgroup(self, group, consumer, streams, count=1, block=0):
        return self.xreadgroup_result

    async def xpending_range(self, stream, group, min, max, count, idle):
        return self.xpending_result

    async def xrange(self, stream, min, max, count=1):
        return self.xrange_result

    async def xclaim(self, stream, group, consumer, min_idle_time, message_ids):
        if self.xclaim_forbidden:
            raise AssertionError("xclaim must not be called in this scenario")
        return self.xclaim_result

    async def xlen(self, stream):
        return 4

    async def xinfo_groups(self, stream):
        return [{"name": "test-group"}]


@pytest.fixture()
def ledger_conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "ledger.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


@pytest.fixture()
def queue(ledger_conn, monkeypatch):
    q = queue_mod.RedisJobQueue.__new__(queue_mod.RedisJobQueue)
    q._client = FakeRedis()
    q._group = "test-group"
    q._ready = True
    monkeypatch.setattr(queue_mod, "get_conn", lambda: ledger_conn)

    @asynccontextmanager
    async def scope():
        yield

    monkeypatch.setattr(queue_mod, "db_connection_scope", scope)
    return q


def seed(conn, task_id, *, status="queued", job_type="vkpi_test", submission_id=0, user_id=7, **cols) -> None:
    base = {
        "task_id": task_id,
        "job_type": job_type,
        "submission_id": submission_id,
        "user_id": user_id,
        "status": status,
        "payload_json": "{}",
        "retry_count": 0,
        "created_at": "2026-04-28T00:00:00Z",
        "updated_at": "2026-04-28T00:00:00Z",
        "stage": "ingest",
        "extra_json": "{}",
    }
    base.update(cols)
    names = ", ".join(base)
    marks = ", ".join("?" for _ in base)
    conn.execute(f"INSERT INTO job_execution_ledger ({names}) VALUES ({marks})", tuple(base.values()))
    conn.commit()


# ---------------------------------------------------------------- enqueue


def test_enqueue_writes_ledger_stream_and_queued_event(queue, ledger_conn):
    task_id = asyncio.run(
        queue.enqueue(
            "vkpi_test",
            {"user_id": 7, "staff_id": 3, "reason": "unit", "category": "c", "endpoint": "/e"},
            priority=2,
            lock_key="lock-1",
            timeout_seconds=120,
        )
    )
    row = ledger_conn.execute("SELECT * FROM job_execution_ledger WHERE task_id=?", (task_id,)).fetchone()
    assert row["job_type"] == "vkpi_test"
    assert row["status"] == "queued"
    assert row["priority"] == 2
    assert row["lock_key"] == "lock-1"
    assert row["timeout_seconds"] == 120
    assert row["triggered_by_staff_id"] == 3
    assert json.loads(row["task_chain_json"]) == {
        "job_type": "vkpi_test",
        "reason": "unit",
        "category": "c",
        "endpoint": "/e",
    }
    assert row["stream_id"] == "1-0"

    stream, fields = queue._client.xadds[0]
    assert stream == queue_mod.REDIS_JOB_STREAM_KEY
    assert fields["task_id"] == task_id
    assert fields["job_type"] == "vkpi_test"
    assert fields["user_id"] == "7"
    assert json.loads(fields["payload_json"])["staff_id"] == 3

    channels = [channel for channel, _ in queue._client.published]
    assert f"{queue_mod.REDIS_JOB_EVENT_PREFIX}:task:{task_id}" in channels
    assert f"{queue_mod.REDIS_JOB_EVENT_PREFIX}:user:7" in channels
    event = queue._client.published[0][1]
    assert event["event_type"] == "queued"
    assert event["status"] == "queued"
    assert event["task_id"] == task_id


def test_enqueue_without_optional_columns_leaves_them_null(queue, ledger_conn):
    task_id = asyncio.run(queue.enqueue("vkpi_test", {"user_id": 1}))
    row = ledger_conn.execute("SELECT * FROM job_execution_ledger WHERE task_id=?", (task_id,)).fetchone()
    assert row["priority"] is None
    assert row["lock_key"] is None
    assert row["timeout_seconds"] is None


def test_enqueue_audit_submission_returns_existing_active_task(queue, ledger_conn):
    seed(ledger_conn, "existing", status="processing", job_type="audit_submission", submission_id=11)
    task_id = asyncio.run(queue.enqueue("audit_submission", {"user_id": 1}, submission_id=11))
    assert task_id == "existing"
    assert queue._client.xadds == []


def test_enqueue_lock_key_dedupes_against_active_job(queue, ledger_conn):
    seed(ledger_conn, "held", status="running", lock_key="stable")
    task_id = asyncio.run(queue.enqueue("vkpi_test", {"user_id": 1}, lock_key="stable"))
    assert task_id == "held"
    assert queue._client.xadds == []


def test_enqueue_lock_key_insert_race_rolls_back_and_returns_winner(queue):
    calls = {"rollback": 0}

    def boom(job):
        raise RuntimeError("unique violation")

    found = iter([None, "winner-task"])
    queue._insert_job_ledger = boom
    queue._find_active_lock_job = lambda lock_key: next(found)
    queue._rollback_job_ledger_insert = lambda: calls.__setitem__("rollback", calls["rollback"] + 1)

    task_id = asyncio.run(queue.enqueue("vkpi_test", {"user_id": 1}, lock_key="k"))
    assert task_id == "winner-task"
    assert calls["rollback"] == 1
    assert queue._client.xadds == []


def test_enqueue_insert_failure_without_lock_key_raises(queue):
    def boom(job):
        raise RuntimeError("insert failed")

    queue._insert_job_ledger = boom
    with pytest.raises(RuntimeError, match="insert failed"):
        asyncio.run(queue.enqueue("vkpi_test", {"user_id": 1}))
    assert queue._client.xadds == []


# ---------------------------------------------------------------- pop_job


def test_pop_job_marks_processing_with_stream_metadata(queue, ledger_conn):
    seed(ledger_conn, "task-p")
    queue._client.xreadgroup_result = [
        (queue_mod.REDIS_JOB_STREAM_KEY, [("5-0", {"task_id": "task-p", "job_type": "vkpi_test", "submission_id": "0", "payload_json": '{"a": 1}'})])
    ]
    raw = asyncio.run(queue.pop_job("worker-a", timeout=1))
    assert raw["task_id"] == "task-p"
    assert raw["_stream_id"] == "5-0"
    assert raw["_consumer_name"] == "worker-a"
    assert raw["payload"] == {"a": 1}
    row = ledger_conn.execute("SELECT status, stream_id, consumer_name FROM job_execution_ledger WHERE task_id=?", ("task-p",)).fetchone()
    assert row["status"] == "processing"
    assert row["stream_id"] == "5-0"
    assert row["consumer_name"] == "worker-a"


def test_pop_job_terminal_race_acks_and_returns_none(queue, ledger_conn):
    seed(ledger_conn, "task-done", status="done")
    queue._client.xreadgroup_result = [
        (queue_mod.REDIS_JOB_STREAM_KEY, [("6-0", {"task_id": "task-done", "job_type": "vkpi_test", "submission_id": "0", "payload_json": "{}"})])
    ]
    raw = asyncio.run(queue.pop_job("worker-a", timeout=1))
    assert raw is None
    assert queue._client.acked == ["6-0"]
    assert ledger_conn.execute("SELECT status FROM job_execution_ledger WHERE task_id=?", ("task-done",)).fetchone()["status"] == "done"


def test_pop_job_falls_back_to_claim_stale_when_stream_is_empty(queue):
    queue._client.xreadgroup_result = []
    seen = {}

    async def fake_claim(consumer_name, count=5):
        seen.update(consumer_name=consumer_name, count=count)
        return [{"task_id": "stale-task"}]

    queue._claim_stale = fake_claim
    raw = asyncio.run(queue.pop_job("worker-b", timeout=1))
    assert raw == {"task_id": "stale-task"}
    assert seen == {"consumer_name": "worker-b", "count": 1}


# ---------------------------------------------------------------- _claim_stale


def test_claim_stale_leaves_processing_job_inside_timeout_window(queue, monkeypatch):
    queue._client.xpending_result = [{"message_id": "9-0", "time_since_delivered": queue_mod.REDIS_JOB_CLAIM_IDLE_MS + 1}]
    queue._client.xrange_result = [("9-0", {"task_id": "task-live", "job_type": "vkpi_test", "submission_id": "0", "payload_json": "{}"})]
    queue._client.xclaim_forbidden = True
    monkeypatch.setattr(queue, "_provider_execution_claim_is_live", lambda task_id: False)

    async def get_status(task_id):
        return {
            "status": "processing",
            "started_at": queue_mod._utcnow(),
            "timeout_seconds": "3600",
        }

    queue.get_status = get_status
    claimed = asyncio.run(queue._claim_stale("worker-c", count=1))
    assert claimed == []
    assert queue._client.acked == []


def test_claim_stale_acks_terminal_message_without_claiming(queue, monkeypatch):
    queue._client.xpending_result = [{"message_id": "10-0", "time_since_delivered": queue_mod.REDIS_JOB_CLAIM_IDLE_MS + 1}]
    queue._client.xrange_result = [("10-0", {"task_id": "task-final", "job_type": "vkpi_test", "submission_id": "0", "payload_json": "{}"})]
    queue._client.xclaim_forbidden = True
    monkeypatch.setattr(queue, "_provider_execution_claim_is_live", lambda task_id: False)

    async def get_status(task_id):
        return {"status": "done"}

    queue.get_status = get_status
    claimed = asyncio.run(queue._claim_stale("worker-c", count=1))
    assert claimed == []
    assert queue._client.acked == ["10-0"]


# ---------------------------------------------------------------- dead letter / set_status


def test_move_to_dead_letter_writes_dead_stream_failed_status_and_acks(queue, ledger_conn):
    seed(ledger_conn, "task-d")
    raw = {"_stream_id": "7-0", "task_id": "task-d", "job_type": "vkpi_test", "payload": {"x": 1}}
    asyncio.run(queue.move_to_dead_letter(raw, "budget_blocked: nope"))

    stream, fields = queue._client.xadds[0]
    assert stream == queue_mod.REDIS_JOB_DEAD_STREAM_KEY
    assert fields["task_id"] == "task-d"
    assert fields["reason"] == "budget_blocked: nope"
    assert json.loads(fields["payload_json"]) == {"x": 1}

    row = ledger_conn.execute("SELECT status, error_message, stage FROM job_execution_ledger WHERE task_id=?", ("task-d",)).fetchone()
    assert row["status"] == "failed"
    assert row["error_message"] == "budget_blocked: nope"
    assert row["stage"] == "dead_letter"
    assert queue._client.acked == ["7-0"]

    events = [event for _, event in queue._client.published]
    assert events and events[0]["event_type"] == "failed"


def test_set_status_on_terminal_row_is_ignored_and_publishes_nothing(queue, ledger_conn):
    seed(ledger_conn, "task-t", status="done")
    asyncio.run(queue.set_status("task-t", "processing"))
    assert ledger_conn.execute("SELECT status FROM job_execution_ledger WHERE task_id=?", ("task-t",)).fetchone()["status"] == "done"
    assert queue._client.published == []


def test_set_status_done_publishes_result_ready(queue, ledger_conn):
    seed(ledger_conn, "task-r", status="processing")
    asyncio.run(queue.set_status("task-r", "done", summary="ok"))
    event = queue._client.published[0][1]
    assert event["event_type"] == "result_ready"
    assert event["status"] == "done"
    assert event["summary"] == "ok"


# ---------------------------------------------------------------- events / readiness / stats


def test_subscribe_task_events_yields_heartbeat_then_message_and_cleans_up(queue):
    class PubSub:
        def __init__(self) -> None:
            self.subscribed: list[str] = []
            self.unsubscribed: list[str] = []
            self.closed = False
            self.messages = [None, {"data": json.dumps({"event_type": "x"})}]

        async def subscribe(self, channel):
            self.subscribed.append(channel)

        async def get_message(self, ignore_subscribe_messages=True, timeout=5.0):
            return self.messages.pop(0)

        async def unsubscribe(self, channel):
            self.unsubscribed.append(channel)

        async def aclose(self):
            self.closed = True

    pubsub = PubSub()
    queue._client.pubsub = lambda: pubsub

    async def run():
        gen = queue.subscribe_task_events("t-1")
        first = await gen.__anext__()
        second = await gen.__anext__()
        await gen.aclose()
        return first, second

    first, second = asyncio.run(run())
    assert first["event_type"] == "heartbeat"
    assert first["task_id"] == "t-1"
    assert second == {"event_type": "x"}
    expected_channel = f"{queue_mod.REDIS_JOB_EVENT_PREFIX}:task:t-1"
    assert pubsub.subscribed == [expected_channel]
    assert pubsub.unsubscribed == [expected_channel]
    assert pubsub.closed is True


def test_worker_readiness_rejects_duplicate_and_empty_names(queue):
    with pytest.raises(RuntimeError, match="unique consumer names"):
        asyncio.run(queue.worker_readiness(["a", "a"]))
    with pytest.raises(RuntimeError, match="unique consumer names"):
        asyncio.run(queue.worker_readiness(["", "  "]))


def test_ledger_queue_summary_counts_waiting_processing_failed_completed(queue, ledger_conn):
    seed(ledger_conn, "w-1", status="queued", created_at="2026-04-28T00:00:00Z")
    seed(ledger_conn, "p-1", status="processing")
    seed(ledger_conn, "f-1", status="failed")
    seed(
        ledger_conn,
        "d-1",
        status="done",
        started_at="2026-04-28T00:00:00Z",
        finished_at="2026-04-28T00:00:30Z",
    )
    summary = queue._ledger_queue_summary()
    assert summary["waiting"] == 1
    assert summary["processing"] == 1
    assert summary["failed"] == 1
    assert summary["completed_recent_sample"] == 1
    assert summary["avg_duration_seconds"] == 30.0
    assert summary["oldest_waiting_age_seconds"] > 0
    assert summary["configured_concurrency"] == queue_mod.WORKER_CONFIGURED_CONCURRENCY
    assert summary["by_job_type"]["vkpi_test"] == {"waiting": 1, "processing": 1, "failed": 1, "completed": 1}


def test_runtime_stats_reports_backend_depth_and_summary(queue):
    stats = asyncio.run(queue.runtime_stats())
    assert stats["backend"] == "redis-stream"
    assert stats["stream_key"] == queue_mod.REDIS_JOB_STREAM_KEY
    assert stats["group"] == "test-group"
    assert stats["queue_depth"] == 4
    assert stats["groups"] == [{"name": "test-group"}]
    assert stats["dead_letter_stream"] == queue_mod.REDIS_JOB_DEAD_STREAM_KEY
    assert isinstance(stats["summary"], dict)


# ---------------------------------------------------------------- module surface


def test_queue_module_keeps_monkeypatch_surface():
    for name in (
        "BaseJobQueue",
        "InProcessJobQueue",
        "RedisJobQueue",
        "VideoJobInput",
        "build_job_queue",
        "get_conn",
        "db_connection_scope",
        "redis_from_url",
        "logger",
        "TaskStatus",
        "TERMINAL_JOB_STATUSES",
        "_decode_json",
        "_normalize_payload",
        "_parse_ts",
        "_seconds_between",
        "_utcnow",
        "REDIS_JOB_STREAM_KEY",
        "REDIS_JOB_DEAD_STREAM_KEY",
        "REDIS_JOB_EVENT_PREFIX",
        "REDIS_JOB_GROUP",
        "REDIS_JOB_CLAIM_IDLE_MS",
        "REDIS_JOB_BLOCK_MS",
        "REDIS_URL",
        "IS_PRODUCTION",
        "WORKER_CONFIGURED_CONCURRENCY",
    ):
        assert hasattr(queue_mod, name), name


def test_build_job_queue_branches(monkeypatch):
    monkeypatch.setattr(queue_mod, "REDIS_URL", "")
    monkeypatch.setattr(queue_mod, "IS_PRODUCTION", True)
    with pytest.raises(RuntimeError, match="requires REDIS_URL"):
        queue_mod.build_job_queue()
    monkeypatch.setattr(queue_mod, "IS_PRODUCTION", False)
    assert queue_mod.build_job_queue() is None
    built = queue_mod.build_job_queue(object())
    assert isinstance(built, queue_mod.InProcessJobQueue)
