from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.db import connection
from app.domains.intelligence.marketing_brain_activity_evidence import (
    activity_evidence_contracts,
)
from app.domains.intelligence import marketing_brain_scorecard as scorecard
from app.domains.platform import evals, event_ledger


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/280_vkpi_eval_evidence_contract.sql"
DOWN = ROOT / "migrations/280_vkpi_eval_evidence_contract_down.sql"


def _sqlite_evidence_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_eval_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          suite TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          total INTEGER NOT NULL DEFAULT 0,
          passed INTEGER NOT NULL DEFAULT 0,
          summary_json TEXT NOT NULL DEFAULT '{}',
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          finished_at TEXT
        );
        CREATE TABLE vkpi_eval_results (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id INTEGER NOT NULL,
          case_name TEXT NOT NULL,
          passed INTEGER NOT NULL DEFAULT 0,
          score REAL,
          detail TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE vkpi_event_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          organization_id INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          entity_type TEXT NOT NULL DEFAULT '',
          entity_id TEXT NOT NULL DEFAULT '',
          actor_type TEXT NOT NULL DEFAULT 'system',
          actor_id TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          trace_id TEXT NOT NULL DEFAULT '',
          confidence REAL,
          provenance_json TEXT NOT NULL DEFAULT '{}',
          occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    return conn


def _patch_writer(
    monkeypatch: pytest.MonkeyPatch,
    conn: sqlite3.Connection,
    cases: list[evals.Case],
) -> None:
    monkeypatch.setattr(evals, "_BUILTIN", cases)
    monkeypatch.setattr(evals, "table_exists", lambda _table: True)
    monkeypatch.setattr(evals, "get_conn", lambda: conn)
    monkeypatch.setattr(evals, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(event_ledger, "is_postgres_runtime", lambda: False)


def _case(passed: bool, score: float, detail: str):
    def run() -> tuple[bool, float, str]:
        return passed, score, detail

    return run


def _one(conn: sqlite3.Connection, sql: str) -> dict:
    row = conn.execute(sql).fetchone()
    assert row is not None
    return dict(row)


def test_writer_persists_server_bound_event_and_hash_in_one_transaction(monkeypatch):
    conn = _sqlite_evidence_conn()
    _patch_writer(
        monkeypatch,
        conn,
        [
            ("case_b", _case(True, 0.8, "second")),
            ("case_a", _case(True, 1.0, "first")),
        ],
    )

    result = evals.run_builtin_suite("core_v1")

    assert result["run_id"] == 1
    assert result["evidence_status"] == "server_bound"
    assert result["passed"] == result["total"] == 2
    run = _one(conn, "SELECT * FROM vkpi_eval_runs")
    summary = json.loads(run["summary_json"])
    event = _one(conn, "SELECT * FROM vkpi_event_ledger")
    payload = json.loads(event["payload_json"])
    provenance = json.loads(event["provenance_json"])
    assert run["status"] == "done"
    assert conn.execute("SELECT COUNT(*) FROM vkpi_eval_results").fetchone()[0] == 2
    assert summary["organization_id"] == 1
    assert summary["server_bound_run_id"] == 1
    assert summary["result_set_sha256"] == result["result_set_sha256"]
    assert payload["result_set_sha256"] == summary["result_set_sha256"]
    assert provenance["result_set_sha256"] == summary["result_set_sha256"]
    assert event["organization_id"] == 1
    assert event["actor_id"] == "run_builtin_suite"
    assert event["source"] == "platform.evals"
    assert event["trace_id"]


def test_failed_suite_persists_receipt_but_is_excluded_from_score_contract(monkeypatch):
    conn = _sqlite_evidence_conn()
    _patch_writer(
        monkeypatch,
        conn,
        [
            ("green", _case(True, 1.0, "ok")),
            ("red", _case(False, 0.0, "regression")),
        ],
    )

    result = evals.run_builtin_suite("core_v1")

    assert result["run_id"] == 1
    assert result["evidence_status"] == "server_bound"
    assert result["passed"] == 1 and result["total"] == 2
    assert _one(conn, "SELECT status, total, passed FROM vkpi_eval_runs") == {
        "status": "done",
        "total": 2,
        "passed": 1,
    }
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 1
    score_sql = activity_evidence_contracts()["eval"].where_sql
    assert "total > 0 AND total = passed" in score_sql
    assert "eval_result.passed IS NOT TRUE" in score_sql


def test_required_event_failure_rolls_back_run_results_and_event(monkeypatch):
    conn = _sqlite_evidence_conn()
    _patch_writer(monkeypatch, conn, [("green", _case(True, 1.0, "ok"))])

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("event insert unavailable")

    monkeypatch.setattr(event_ledger, "insert_required", fail_event)
    result = evals.run_builtin_suite("core_v1")

    assert result["run_id"] is None
    assert result["evidence_status"] == "not_persisted"
    assert result["result_set_sha256"] is None
    for table in ("vkpi_eval_runs", "vkpi_eval_results", "vkpi_event_ledger"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_hash_is_order_stable_and_sensitive_to_result_content():
    first = [
        {"case_name": "a", "passed": True, "score": 1.0, "detail": "ok"},
        {"case_name": "b", "passed": False, "score": 0.0, "detail": "no"},
    ]
    reverse = list(reversed(first))
    changed = [dict(row) for row in first]
    changed[1]["detail"] = "different"

    assert evals._result_set_sha256("core_v1", first) == evals._result_set_sha256(
        "core_v1", reverse,
    )
    assert evals._result_set_sha256("core_v1", first) != evals._result_set_sha256(
        "core_v1", changed,
    )


def test_writer_rejects_non_org1_scope_before_running_cases(monkeypatch):
    called = False

    def should_not_run():
        nonlocal called
        called = True
        return True, 1.0, "unexpected"

    monkeypatch.setattr(evals, "_BUILTIN", [("case", should_not_run)])
    with pytest.raises(ValueError, match="organization_scope"):
        evals.run_builtin_suite("core_v1", organization_id=2)
    assert called is False


def test_activity_contract_queries_are_psycopg_percent_safe(monkeypatch):
    class Cursor:
        def fetchone(self):
            return {"n": 0}

    class PercentSafeConn:
        def execute(self, sql, params=()):
            assert "%" not in sql
            assert params == ()
            return Cursor()

    monkeypatch.setattr(scorecard, "table_exists", lambda _table: True)
    monkeypatch.setattr(scorecard, "get_conn", lambda: PercentSafeConn())
    for contract in activity_evidence_contracts().values():
        assert scorecard._recent_distinct_count(
            contract.table,
            contract.unit_sql,
            contract.timestamp_column,
            where=contract.where_sql,
        ) == 0


def test_migration_280_is_runner_owned_and_freezes_all_terminal_evidence():
    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")

    assert connection._FORWARD_TRANSACTION_CONTROL_RE.search(up) is None
    assert "NEW.passed < 0" in up and "NEW.passed > NEW.total" in up
    assert "passed_count <> NEW.passed" in up
    assert "failed_count <> NEW.total - NEW.passed" in up
    assert "NEW.passed <> NEW.total" not in up
    assert "result_set_sha256" in up
    assert "ev.payload_json->>'result_set_sha256'" in up
    assert "ev.provenance_json->>'result_set_sha256'" in up
    assert "OLD.status = 'done'" in up
    assert "completed eval result evidence is immutable" in up
    assert "TG_OP = 'UPDATE' AND NEW.event_type = 'eval_suite_completed'" in up
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_eval_suite_completed_event" in up
    assert "280_vkpi_eval_evidence_contract.sql" in down


@pytest.mark.pg
def test_migration_280_writer_scorecard_and_tamper_contract_on_real_pg(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_eval_evidence_{uuid.uuid4().hex}"
    raw = None
    try:
        raw = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
        raw.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        raw.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        raw.execute(
            """
            CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
            CREATE TABLE vkpi_eval_runs (
              id BIGSERIAL PRIMARY KEY, suite TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'running', total INTEGER NOT NULL DEFAULT 0,
              passed INTEGER NOT NULL DEFAULT 0,
              summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ
            );
            CREATE TABLE vkpi_eval_results (
              id BIGSERIAL PRIMARY KEY, run_id BIGINT NOT NULL,
              case_name TEXT NOT NULL, passed BOOLEAN NOT NULL DEFAULT FALSE,
              score DOUBLE PRECISION, detail TEXT NOT NULL DEFAULT '',
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE vkpi_event_ledger (
              id BIGSERIAL PRIMARY KEY, organization_id BIGINT NOT NULL,
              event_type TEXT NOT NULL, entity_type TEXT NOT NULL DEFAULT '',
              entity_id TEXT NOT NULL DEFAULT '', actor_type TEXT NOT NULL DEFAULT 'system',
              actor_id TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
              payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              trace_id TEXT NOT NULL DEFAULT '', confidence DOUBLE PRECISION,
              provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        raw.execute(UP.read_text(encoding="utf-8"))
        raw.autocommit = False
        compat = connection.PostgresCompatConnection(raw, pool=None)
        monkeypatch.setattr(evals, "table_exists", lambda _table: True)
        monkeypatch.setattr(evals, "get_conn", lambda: compat)
        monkeypatch.setattr(evals, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(event_ledger, "is_postgres_runtime", lambda: True)

        monkeypatch.setattr(evals, "_BUILTIN", [("green", _case(True, 1.0, "ok"))])
        assert evals.run_builtin_suite("core_v1")["run_id"] == 1
        assert evals.run_builtin_suite("core_v1")["run_id"] == 2
        contract = activity_evidence_contracts()["eval"]
        counted = compat.execute(
            f"SELECT COUNT(DISTINCT {contract.unit_sql}) AS n "
            f"FROM {contract.table} WHERE {contract.where_sql}"
        ).fetchone()
        assert dict(counted or {}).get("n") == 1

        monkeypatch.setattr(evals, "_BUILTIN", [("red", _case(False, 0.0, "bad"))])
        failed = evals.run_builtin_suite("regression_v1")
        assert failed["run_id"] == 3 and failed["passed"] == 0
        counted = compat.execute(
            f"SELECT COUNT(DISTINCT {contract.unit_sql}) AS n "
            f"FROM {contract.table} WHERE {contract.where_sql}"
        ).fetchone()
        assert dict(counted or {}).get("n") == 1

        for statement in (
            "UPDATE vkpi_eval_runs SET summary_json='{}'::jsonb WHERE id=1",
            "UPDATE vkpi_eval_results SET detail='tampered' WHERE run_id=1",
            "UPDATE vkpi_event_ledger SET payload_json='{}'::jsonb "
            "WHERE event_type='eval_suite_completed' AND entity_id='1'",
        ):
            with pytest.raises(psycopg.Error):
                compat.execute(statement)
            compat.rollback()
        assert compat.execute(
            "SELECT COUNT(*) AS n FROM vkpi_eval_runs WHERE status='done'"
        ).fetchone()["n"] == 3
    finally:
        if raw is not None:
            raw.rollback()
            raw.close()
        with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )
