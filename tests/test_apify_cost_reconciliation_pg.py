"""Real PostgreSQL three-book accounting; synthetic schemas, no provider I/O."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.db.connection import PostgresCompatConnection
from app.domains.costs.apify_cost_reconciliation import reconcile_run_observation


pytestmark = pytest.mark.pg
NOW = datetime(2026, 9, 6, 15, tzinfo=timezone.utc)
RUN = "synthetic_run_only"
KEY = "synthetic_reservation_only"
TABLES = ("vkpi_ai_cost_ledger", "vkpi_apify_budget_reservations", "vkpi_provider_budget_caps")


@pytest.fixture
def accounting(pg_dsn):
    import psycopg
    from psycopg import sql

    schema = "vkpi_apify_cost_test_" + uuid4().hex
    options = "-c statement_timeout=10000 -c lock_timeout=2000"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5, options=options)
    connections = []

    def connect():
        raw = psycopg.connect(pg_dsn, connect_timeout=5, options=options)
        raw.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
        raw.commit()
        assert raw.autocommit is False
        wrapped = PostgresCompatConnection(raw, pool=None)
        connections.append(wrapped)
        return wrapped

    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        setup = connect()
        setup.execute("""
            CREATE TABLE vkpi_ai_cost_ledger (
                id BIGSERIAL PRIMARY KEY, ai_provider TEXT, cost_usd NUMERIC(18,6),
                metadata_json TEXT, occurred_at TIMESTAMPTZ, cron_task TEXT,
                model_name TEXT, tokens_in INTEGER, tokens_out INTEGER);
            CREATE TABLE vkpi_apify_budget_reservations (
                reservation_key TEXT PRIMARY KEY, apify_run_id TEXT UNIQUE, state TEXT,
                actual_cost_usd NUMERIC(18,6), metadata_json TEXT, settled_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ, provider_started_at TIMESTAMPTZ, reserved_at TIMESTAMPTZ);
            CREATE TABLE vkpi_provider_budget_caps (
                scope TEXT PRIMARY KEY, current_spend NUMERIC(18,6), cap_usd NUMERIC(18,6),
                reset_at TIMESTAMPTZ);
            INSERT INTO vkpi_provider_budget_caps VALUES
                ('provider:apify',1,10,'2026-10-01T00:00:00Z'),
                ('monthly_total',2,50,'2026-10-01T00:00:00Z'),
                ('cron:kol_live_query_eval',0.02,0.000001,'2026-09-07T00:00:00Z');
        """)
        setup.execute(
            "INSERT INTO vkpi_apify_budget_reservations VALUES "
            "(?,?,'provider_started',NULL,?,NULL,?,?,?)",
            (KEY, RUN, '{"preserve":"synthetic fixture"}', NOW, NOW, NOW),
        )
        setup.commit()
        yield setup, connect
    finally:
        for conn in connections:
            conn.close()
        try:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        finally:
            admin.close()


def apply(conn, amount="0.0026", *, first=False):
    return reconcile_run_observation(
        conn, {"id": RUN, "status": "SUCCEEDED", "usageTotalUsd": Decimal(amount),
               "_vkpi_budget_reservation_key": KEY},
        postgres=True, now=NOW,
        initial_entry={"actor_id": "synthetic/no-provider-call"} if first else None,
    )


def snapshot(conn):
    return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]
            for table in TABLES}


def assert_accounted(conn, amount):
    rows = snapshot(conn)
    charge = Decimal(amount)
    assert len(rows[TABLES[0]]) == len(rows[TABLES[1]]) == 1
    detail, reservation = rows[TABLES[0]][0], rows[TABLES[1]][0]
    assert Decimal(str(detail["cost_usd"])) == Decimal(str(reservation["actual_cost_usd"])) == charge
    assert reservation["state"] == "settled"
    assert json.loads(detail["metadata_json"])["cost_state"] == "provisional"
    assert json.loads(detail["metadata_json"])["provider_cost_final"] is False
    assert json.loads(reservation["metadata_json"])["preserve"] == "synthetic fixture"
    caps = {row["scope"]: row for row in rows[TABLES[2]]}
    assert Decimal(str(caps["provider:apify"]["current_spend"])) == 1 + charge
    assert Decimal(str(caps["monthly_total"]["current_spend"])) == 2 + charge
    assert Decimal(str(caps["cron:kol_live_query_eval"]["current_spend"])) == Decimal("0.02")
    assert Decimal(str(caps["cron:kol_live_query_eval"]["cap_usd"])) == Decimal("0.000001")


def test_first_record_late_charge_and_replay_are_atomic(accounting):
    conn, _ = accounting
    first = apply(conn, first=True)
    assert first["ledger_inserted"] is True and first["ledger_id"] > 0
    assert first["accounting_verified"] is True and first["provider_cost_final"] is False
    assert first["delta_usd"] == .0026
    assert_accounted(conn, "0.0026")
    late = apply(conn, "0.026")
    assert late["delta_usd"] == .0234 and late["ledger_id"] == first["ledger_id"]
    assert_accounted(conn, "0.026")
    before = snapshot(conn)
    assert apply(conn, "0.026")["delta_usd"] == 0
    assert snapshot(conn) == before


def test_concurrent_run_observers_apply_one_delta(accounting):
    setup, connect = accounting
    apply(setup, first=True)
    first, second = connect(), connect()
    barrier = threading.Barrier(2)
    receipts, errors = [], []

    def observe(conn):
        try:
            barrier.wait(timeout=5)
            receipts.append(apply(conn, "0.026"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=observe, args=(conn,), daemon=True) for conn in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=12)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == [] and len(receipts) == 2
    for receipt in receipts:
        if not receipt.get("reconciled"):
            assert receipt["reason"] == "reconciliation_busy" and receipt["sqlstate"] == "55P03"
            assert receipt["automatic_provider_retry_allowed"] is False
    assert sum(Decimal(str(row.get("delta_usd", 0))) for row in receipts) == Decimal("0.0234")
    assert apply(first, "0.026")["delta_usd"] == apply(second, "0.026")["delta_usd"] == 0
    assert_accounted(setup, "0.026")


def test_nowait_cap_lock_rolls_back_initial_detail_then_existing_run_retries(accounting):
    setup, connect = accounting
    held, contender = connect(), connect()
    before = snapshot(setup)
    setup.commit()
    held.execute("SELECT scope FROM vkpi_provider_budget_caps WHERE scope='monthly_total' FOR UPDATE")
    result = apply(contender, first=True)
    assert result["reason"] == "reconciliation_busy" and result["sqlstate"] == "55P03"
    assert result["next_action"] == "retry_existing_run_reconciliation"
    assert result["automatic_provider_retry_allowed"] is False
    # INSERT ... RETURNING ran before the final cap lock; the real transaction
    # must remove that insert and leave reservation and every cap untouched.
    assert snapshot(setup) == before
    held.rollback()
    after = apply(contender, first=True)
    assert after["ledger_inserted"] is True and after["delta_usd"] == .0026
    assert_accounted(setup, "0.0026")


def test_real_sql_failure_rolls_back_three_books_and_preserves_sqlstate(accounting):
    import psycopg

    conn, _ = accounting
    apply(conn, first=True)
    conn.execute("ALTER TABLE vkpi_ai_cost_ledger ADD CONSTRAINT fixture_ceiling CHECK(cost_usd < 0.02)")
    conn.commit()
    before = snapshot(conn)
    with pytest.raises(psycopg.errors.CheckViolation) as error:
        apply(conn, "0.026")
    assert error.value.sqlstate == "23514"
    assert snapshot(conn) == before
    conn.execute("ALTER TABLE vkpi_ai_cost_ledger DROP CONSTRAINT fixture_ceiling")
    conn.commit()
    assert apply(conn, "0.026")["delta_usd"] == .0234
    assert_accounted(conn, "0.026")
