from __future__ import annotations

import threading
from uuid import uuid4

import pytest

from app.domains.costs import budget_guard
from app.domains.costs.budget_window_roll import roll_budget_window


class _Compat:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, statement, params=()):
        return self.conn.execute(statement.replace("?", "%s"), params)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()


class _PausingCompat(_Compat):
    def __init__(self, conn, *, select_reached: threading.Event, resume: threading.Event):
        super().__init__(conn)
        self.select_reached = select_reached
        self.resume = resume

    def execute(self, statement, params=()):
        if statement.startswith("SELECT * FROM vkpi_provider_budget_caps WHERE scope="):
            self.select_reached.set()
            assert self.resume.wait(timeout=5)
        return super().execute(statement, params)


@pytest.mark.pg
def test_roll_lock_serializes_reset_before_concurrent_cost_increment(pg_dsn: str) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    schema = f"vkpi_budget_roll_{uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True)
    first = psycopg.connect(pg_dsn, row_factory=dict_row)
    second = psycopg.connect(pg_dsn, row_factory=dict_row)
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for conn in (first, second):
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.commit()
        first.execute(
            """
            CREATE TABLE vkpi_provider_budget_caps (
                scope TEXT PRIMARY KEY, current_spend NUMERIC, reset_at TIMESTAMPTZ
            )
            """
        )
        first.execute(
            "INSERT INTO vkpi_provider_budget_caps VALUES "
            "('cron:probe', 5, CURRENT_TIMESTAMP - INTERVAL '1 day')"
        )
        first.commit()

        adapter = _Compat(first)
        row = adapter.execute(
            "SELECT * FROM vkpi_provider_budget_caps WHERE scope=?", ("cron:probe",)
        ).fetchone()
        roll_budget_window(
            adapter, dict(row), postgres=True, release_fenced=False, commit=False, strict=True
        )

        completed = threading.Event()

        def add_cost():
            second.execute(
                "UPDATE vkpi_provider_budget_caps "
                "SET current_spend=current_spend + 1 WHERE scope='cron:probe'"
            )
            second.commit()
            completed.set()

        thread = threading.Thread(target=add_cost, daemon=True)
        thread.start()
        assert completed.wait(0.2) is False
        first.commit()
        thread.join(timeout=5)
        assert completed.is_set()
        final = first.execute(
            "SELECT current_spend FROM vkpi_provider_budget_caps WHERE scope='cron:probe'"
        ).fetchone()
        assert float(final["current_spend"]) == 1.0
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


@pytest.mark.pg
def test_record_cost_concurrent_scope_delete_fails_and_rolls_back_ledger(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    schema = f"vkpi_budget_delete_{uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True)
    first = psycopg.connect(pg_dsn, row_factory=dict_row)
    second = psycopg.connect(pg_dsn, row_factory=dict_row)
    select_reached = threading.Event()
    resume = threading.Event()
    errors: list[BaseException] = []
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for conn in (first, second):
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.commit()
        first.execute(
            """
            CREATE TABLE vkpi_provider_budget_caps (
                scope TEXT PRIMARY KEY, cap_usd NUMERIC, current_spend NUMERIC,
                warning_at NUMERIC, hard_stop_at NUMERIC, reset_at TIMESTAMPTZ,
                fallback_action TEXT, metadata_json TEXT
            );
            CREATE TABLE vkpi_ai_cost_ledger (
                id BIGSERIAL PRIMARY KEY, cron_task TEXT, ai_provider TEXT,
                model_name TEXT, cost_usd NUMERIC, tokens_in INTEGER,
                tokens_out INTEGER, kol_pool_id BIGINT, staff_id BIGINT,
                task_item_id BIGINT, metadata_json TEXT, occurred_at TIMESTAMPTZ
            );
            INSERT INTO vkpi_provider_budget_caps VALUES
                ('cron:delete_probe', 2, 0, 0.8, 1,
                 CURRENT_TIMESTAMP + INTERVAL '1 day', 'deny', '{}');
            """
        )
        first.commit()
        adapter = _PausingCompat(
            first, select_reached=select_reached, resume=resume
        )
        monkeypatch.setattr(budget_guard, "get_conn", lambda: adapter)
        monkeypatch.setattr(budget_guard, "ensure_budget_schema", lambda: None)
        monkeypatch.setattr(budget_guard, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(budget_guard, "release_validation_active", lambda: False)

        def record() -> None:
            try:
                budget_guard.record_cost(
                    scope="cron:delete_probe",
                    cron_task="cron:delete_probe",
                    ai_provider="gemini",
                    model_name="delete-probe",
                    cost_usd="0.01",
                    optional_scopes=["cron:delete_probe"],
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        thread = threading.Thread(target=record, daemon=True)
        thread.start()
        assert select_reached.wait(timeout=5)
        second.execute(
            "DELETE FROM vkpi_provider_budget_caps WHERE scope='cron:delete_probe'"
        )
        second.commit()
        resume.set()
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert len(errors) == 1
        assert "budget_scope_missing_during_cost_record" in str(errors[0])
        assert first.execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"] == 0
    finally:
        resume.set()
        first.rollback()
        second.rollback()
        first.close()
        second.close()
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()
