from __future__ import annotations

from typing import Any

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
