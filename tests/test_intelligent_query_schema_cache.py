from __future__ import annotations

from typing import Any

import pytest

from app.domains.intelligent_query import repository


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _CountingConnection:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
        self.calls += 1
        if self.fail:
            raise RuntimeError("schema unavailable")
        return _Rows([{"column_name": "id"}, {"column_name": "platform"}])


def test_schema_probe_is_cached_only_inside_one_request(monkeypatch) -> None:
    monkeypatch.setattr(repository, "is_postgres_runtime", lambda: True)
    conn = _CountingConnection()

    with repository.schema_cache_scope():
        assert repository.table_columns(conn, "vkpi_kol_pool") == {"id", "platform"}
        assert repository.table_columns(conn, "vkpi_kol_pool") == {"id", "platform"}
        assert conn.calls == 1

    assert repository.table_columns(conn, "vkpi_kol_pool") == {"id", "platform"}
    assert conn.calls == 2


def test_failed_schema_probe_is_fail_closed_and_request_cached(monkeypatch) -> None:
    monkeypatch.setattr(repository, "is_postgres_runtime", lambda: True)
    conn = _CountingConnection(fail=True)

    with repository.schema_cache_scope():
        assert repository.table_columns(conn, "vkpi_projects") == set()
        assert repository.table_columns(conn, "vkpi_projects") == set()
        assert conn.calls == 1


def test_schema_probe_rejects_non_allowlisted_table_before_sql(monkeypatch) -> None:
    monkeypatch.setattr(repository, "is_postgres_runtime", lambda: True)
    conn = _CountingConnection()

    with repository.schema_cache_scope():
        with pytest.raises(ValueError, match="table is not allowlisted"):
            repository.table_columns(conn, "users")
    assert conn.calls == 0
