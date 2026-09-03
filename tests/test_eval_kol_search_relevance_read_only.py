"""eval CLI 只读连接守卫(T 车道 2026-09-02:export -> database_read_only_guard_failed)。

真因:脚本靠 ``PGOPTIONS default_transaction_read_only=on``,但共享池对每条连接
``raw_conn.read_only=False`` → psycopg 发 ``BEGIN READ WRITE`` 压过默认值。
修法:评测脚本自己开一条 ``read_only=True`` 的 psycopg 连接并绑成作用域连接,
召回代码里的 ``get_conn()`` 全部落到它,共享池一次都不借。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from app.db import connection


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_kol_search_relevance.py"


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("vkpi_eval_kol_search_relevance_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _RawCursor:
    def __init__(self, raw: "_RawConn") -> None:
        self.raw = raw
        self.description = [("transaction_read_only",)]
        self._row: tuple[Any, ...] | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: Any = None):
        self.raw.executed.append(sql)
        # psycopg only opens a READ ONLY transaction when the flag is set.
        self._row = ("on" if self.raw.read_only else "off",)
        return self

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []

    def close(self) -> None:
        return None


class _RawConn:
    def __init__(self) -> None:
        self.read_only: bool | None = None
        self.executed: list[str] = []
        self.closed = False
        self.rollbacks = 0

    def cursor(self, *_args, **_kwargs) -> _RawCursor:
        return _RawCursor(self)

    def rollback(self) -> None:
        self.rollbacks += 1

    def commit(self) -> None:
        raise AssertionError("evaluator must never commit")

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_psycopg(monkeypatch):
    import psycopg

    opened: list[dict[str, Any]] = []
    raw = _RawConn()

    def _connect(conninfo: str, **kwargs: Any) -> _RawConn:
        opened.append({"conninfo": conninfo, **kwargs})
        return raw

    monkeypatch.setattr(psycopg, "connect", _connect)
    return raw, opened


def test_open_read_only_conn_owns_a_standalone_read_only_psycopg_connection(cli, fake_psycopg) -> None:
    raw, opened = fake_psycopg
    conn = cli._open_read_only_conn("postgresql://postgres@127.0.0.1:54329/viltrox2")
    assert isinstance(conn, connection.PostgresCompatConnection)
    assert conn._pool is None, "the evaluator must not lease from the shared pool"
    assert raw.read_only is True
    assert len(opened) == 1 and opened[0]["autocommit"] is False
    assert "default_transaction_read_only=on" in opened[0]["options"]


def test_export_binds_read_only_conn_so_recall_get_conn_bypasses_pool(cli, fake_psycopg, monkeypatch) -> None:
    raw, _opened = fake_psycopg
    seen: dict[str, Any] = {}

    def _boom():
        raise AssertionError("shared pool must not be touched by the evaluator")

    monkeypatch.setattr(connection, "_get_pg_pool", _boom)
    monkeypatch.setattr(cli, "_configure_read_only_runtime", lambda _url: None)
    monkeypatch.setattr(cli, "_dataset_snapshot_id", lambda conn: "local-db-sha256:test")
    monkeypatch.setattr(cli, "_source_code_version", lambda: "source-sha256:test")

    def _build(search, *, code_version, dataset_snapshot_id):
        seen["get_conn_is_bound"] = connection.get_conn() is seen["bound"]
        seen["search"] = search
        return {"candidate_export_complete": True, "code_version": code_version, "dataset_snapshot_id": dataset_snapshot_id}

    monkeypatch.setattr(cli.EVALUATOR, "build_candidate_manifest", _build)
    original_open = cli._open_read_only_conn

    def _open(url: str):
        seen["bound"] = original_open(url)
        return seen["bound"]

    monkeypatch.setattr(cli, "_open_read_only_conn", _open)

    manifest = cli._export_manifest("postgresql://postgres@127.0.0.1:54329/viltrox2")

    assert manifest["candidate_export_complete"] is True
    assert seen["get_conn_is_bound"] is True
    assert any("SHOW transaction_read_only" in sql for sql in raw.executed)
    assert raw.closed is True, "the standalone connection is closed after export"
    assert connection._scoped_conn.get() is None, "scope token is reset after export"


def test_export_fails_closed_when_transaction_is_not_read_only(cli, fake_psycopg, monkeypatch) -> None:
    raw, _opened = fake_psycopg
    monkeypatch.setattr(cli, "_configure_read_only_runtime", lambda _url: None)
    monkeypatch.setattr(cli.EVALUATOR, "build_candidate_manifest", lambda *_a, **_kw: pytest.fail("must not export"))
    original_open = cli._open_read_only_conn

    def _open(url: str):
        conn = original_open(url)
        raw.read_only = False  # simulate the pool-style override the guard exists to catch
        return conn

    monkeypatch.setattr(cli, "_open_read_only_conn", _open)

    with pytest.raises(RuntimeError, match="database_read_only_guard_failed"):
        cli._export_manifest("postgresql://postgres@127.0.0.1:54329/viltrox2")
    assert raw.closed is True
    assert connection._scoped_conn.get() is None


def test_export_cli_still_refuses_non_loopback_urls(cli) -> None:
    with pytest.raises(ValueError, match="loopback_postgresql_url_required"):
        cli._validate_loopback_database_url("postgresql://postgres@10.0.0.9:5432/viltrox2")
