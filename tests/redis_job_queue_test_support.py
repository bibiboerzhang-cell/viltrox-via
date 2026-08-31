"""Shared hermetic ledger/Redis fixtures for Redis job queue recovery tests."""
from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager

import pytest

from app.services.jobs import queue as queue_mod


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
    instance = queue_mod.RedisJobQueue.__new__(queue_mod.RedisJobQueue)
    instance._client = FakeRedis()
    instance._group = "test-group"
    instance._ready = True
    monkeypatch.setattr(queue_mod, "get_conn", lambda: ledger_conn)

    @asynccontextmanager
    async def scope():
        yield

    monkeypatch.setattr(queue_mod, "db_connection_scope", scope)
    return instance


def seed(
    conn,
    task_id,
    *,
    status="queued",
    job_type="vkpi_test",
    submission_id=0,
    user_id=7,
    **cols,
) -> None:
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
    conn.execute(
        f"INSERT INTO job_execution_ledger ({names}) VALUES ({marks})",
        tuple(base.values()),
    )
    conn.commit()
