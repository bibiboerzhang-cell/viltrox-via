from __future__ import annotations

import sqlite3
from typing import Any

from app.api.routers import vkpi_tasks
from app.domains.tasks import enqueue as task_enqueue


class _Result:
    def __init__(self, *, one: Any = None, many: list[Any] | None = None) -> None:
        self._one = one
        self._many = list(many or [])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _RecordingConnection:
    def __init__(self, *, one: Any = None, many: list[Any] | None = None) -> None:
        self.one = one
        self.many = list(many or [])
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params=()):
        normalized_params = tuple(params or ())
        self.calls.append((sql, normalized_params))
        # psycopg treats a bare percent sign in SQL text as a placeholder.
        # The LIKE wildcard must therefore travel as a bound value, not as
        # ``LIKE 'vkpi_%'`` in the SQL string.
        assert "LIKE 'vkpi_%'" not in sql
        return _Result(one=self.one, many=self.many)


def test_task_detail_binds_vkpi_like_pattern_for_postgres(monkeypatch) -> None:
    row = {
        "task_id": "task-1",
        "job_type": "vkpi_official_channel_sync",
        "status": "done",
    }
    conn = _RecordingConnection(one=row)
    monkeypatch.setattr(task_enqueue, "ensure_vkpi_task_schema", lambda: None)
    monkeypatch.setattr(task_enqueue, "get_conn", lambda: conn)

    assert task_enqueue._task_row("task-1") == row
    sql, params = conn.calls[-1]
    assert "job_type LIKE ?" in sql
    assert params == ("task-1", "vkpi_%")


def test_task_list_binds_vkpi_like_pattern_for_postgres(monkeypatch) -> None:
    conn = _RecordingConnection(many=[])
    monkeypatch.setattr(task_enqueue, "ensure_vkpi_task_schema", lambda: None)
    monkeypatch.setattr(task_enqueue, "get_conn", lambda: conn)
    monkeypatch.setattr(task_enqueue, "_can_manage_all", lambda _staff: True)

    assert task_enqueue.list_tasks(status="running", limit=25, staff={"role": "admin"}) == {"tasks": []}
    sql, params = conn.calls[-1]
    assert "job_type LIKE ?" in sql
    assert params == ("vkpi_%", "running", 25)


def test_postgres_task_schema_guard_is_runtime_ddl_free(monkeypatch) -> None:
    calls = 0

    def fail_if_connected():
        nonlocal calls
        calls += 1
        raise AssertionError("PostgreSQL request path must not run schema SQL")

    monkeypatch.setattr(task_enqueue, "_SCHEMA_READY", False)
    monkeypatch.setattr(task_enqueue, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(task_enqueue, "get_conn", fail_if_connected)

    task_enqueue.ensure_vkpi_task_schema()
    task_enqueue.ensure_vkpi_task_schema()

    assert calls == 0
    assert task_enqueue._SCHEMA_READY is True


def test_sqlite_task_schema_guard_remains_idempotent(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE job_execution_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL DEFAULT 'queued'
        )
        """
    )
    monkeypatch.setattr(task_enqueue, "_SCHEMA_READY", False)
    monkeypatch.setattr(task_enqueue, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(task_enqueue, "get_conn", lambda: conn)

    task_enqueue.ensure_vkpi_task_schema()
    task_enqueue.ensure_vkpi_task_schema()

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(job_execution_ledger)")}
    assert {
        "priority",
        "lock_key",
        "timeout_seconds",
        "heartbeat_at",
        "cancel_requested_at",
        "estimated_cost",
        "actual_cost",
    }.issubset(columns)
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vkpi_async_task_items'"
    ).fetchone()
    index_names = {
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "uniq_job_active_lock" in index_names
    assert "idx_vkpi_task_items_task" in index_names
    conn.close()


def test_compact_task_list_uses_narrow_projection_and_contract(monkeypatch) -> None:
    row = {
        "task_id": "task-compact",
        "job_type": "vkpi_video_cache",
        "status": "done",
        "summary": "缓存完成",
        "stage": "complete",
        "error_message": "",
        "extra_json": '{"progress_pct":100,"progress_text":"完成"}',
        "result_json": '{"summary":"已缓存","cached_url":"https://media.example/video.mp4","secret":"omit"}',
        "payload_json": '{"large":"omit"}',
        "created_at": "2026-08-04T00:00:00Z",
        "started_at": "2026-08-04T00:00:01Z",
        "finished_at": "2026-08-04T00:00:02Z",
    }
    conn = _RecordingConnection(many=[row])
    monkeypatch.setattr(task_enqueue, "ensure_vkpi_task_schema", lambda: None)
    monkeypatch.setattr(task_enqueue, "get_conn", lambda: conn)
    monkeypatch.setattr(task_enqueue, "_can_manage_all", lambda _staff: True)

    response = task_enqueue.list_tasks(limit=10, compact=True, staff={"role": "admin"})

    sql, params = conn.calls[-1]
    assert "SELECT *" not in sql
    assert "payload_json" not in sql
    assert "stats_json" not in sql
    assert "extra_json" in sql
    assert "result_json" in sql
    assert params == ("vkpi_%", 10)
    task = response["tasks"][0]
    assert task["progress_pct"] == 100
    assert task["progress_text"] == "完成"
    assert task["result"] == {
        "summary": "已缓存",
        "cached_url": "https://media.example/video.mp4",
    }
    assert "payload" not in task
    assert "items" not in task
    assert "estimated_cost" not in task
    assert "actual_cost" not in task
    assert "cancel_requested_at" not in task
    assert "large" not in str(task)
    assert "secret" not in str(task)


def test_full_task_list_contract_remains_unchanged(monkeypatch) -> None:
    row = {
        "task_id": "task-full",
        "job_type": "vkpi_ai_analysis",
        "status": "done",
        "payload_json": '{"scope":"full"}',
        "result_json": '{"details":{"score":7}}',
        "extra_json": "{}",
    }
    conn = _RecordingConnection(many=[row])
    monkeypatch.setattr(task_enqueue, "ensure_vkpi_task_schema", lambda: None)
    monkeypatch.setattr(task_enqueue, "get_conn", lambda: conn)
    monkeypatch.setattr(task_enqueue, "_can_manage_all", lambda _staff: True)

    response = task_enqueue.list_tasks(limit=1, compact=False, staff={"role": "admin"})

    sql, _params = conn.calls[-1]
    assert "SELECT *" in sql
    task = response["tasks"][0]
    assert task["payload"] == {"scope": "full"}
    assert task["result"] == {"details": {"score": 7}}
    assert task["items"] == []
    assert "estimated_cost" in task
    assert "actual_cost" in task
    assert "cancel_requested_at" in task


def test_task_list_route_forwards_compact_flag(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_list_tasks(**kwargs):
        captured.update(kwargs)
        return {"tasks": []}

    monkeypatch.setattr(task_enqueue, "list_tasks", fake_list_tasks)

    assert vkpi_tasks.list_vkpi_tasks(
        status="",
        task_type="",
        user_id=None,
        limit=25,
        compact=True,
        staff={"role": "admin"},
    ) == {"tasks": []}
    assert captured["compact"] is True
    assert captured["limit"] == 25
