"""Real PG payout accrual races, with synthetic orders and no payment adapter."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.db.connection import PostgresCompatConnection
from app.services.commerce import payouts

pytestmark = pytest.mark.pg
_OPTIONS = '-c statement_timeout=15000 -c lock_timeout=5000'
_CYCLE = 'synthetic-cycle-only'
_SCHEMA = """
CREATE TABLE users(id BIGINT PRIMARY KEY,email TEXT);
CREATE TABLE payout_cycles(id TEXT PRIMARY KEY,start_date TIMESTAMPTZ,end_date TIMESTAMPTZ,
 status TEXT,processed_by BIGINT REFERENCES users(id),processed_at TIMESTAMPTZ);
CREATE TABLE orders(id BIGINT PRIMARY KEY,attribution_user_id BIGINT REFERENCES users(id),
 currency TEXT,placed_at TIMESTAMPTZ,status TEXT,subtotal_cents INTEGER,commission_cents INTEGER);
CREATE TABLE payouts(id BIGSERIAL PRIMARY KEY,cycle_id TEXT REFERENCES payout_cycles(id),
 user_id BIGINT REFERENCES users(id),amount_cents INTEGER,currency TEXT,order_ids_json TEXT,
 gmv_cents INTEGER,order_count INTEGER,method TEXT,method_details TEXT,status TEXT,
 hold_reason TEXT,paid_tx_id TEXT,paid_at TIMESTAMPTZ,failed_at TIMESTAMPTZ,failed_reason TEXT,
 UNIQUE(cycle_id,user_id));
INSERT INTO users VALUES(7,'manager@example.invalid'),(9,'creator@example.invalid');
INSERT INTO payout_cycles(id,start_date,end_date,status)
 VALUES('synthetic-cycle-only','2026-09-01T00:00:00Z','2026-09-30T23:59:59Z','active');
INSERT INTO orders VALUES
 (101,9,'usd','2026-09-07T00:00:00Z','paid',4000,500),
 (102,9,' USD ','2026-09-08T00:00:00Z','paid',6000,750),
 (103,9,'USD','2026-09-08T00:00:00Z','pending',9999,9999),
 (104,9,'USD','2026-08-08T00:00:00Z','paid',9999,9999);
"""


class _PausedCommitConnection(PostgresCompatConnection):
    def __init__(self, raw, before_commit=None):
        super().__init__(raw, pool=None)
        self.before_commit = before_commit

    def commit(self):
        if self.before_commit is not None:
            callback, self.before_commit = self.before_commit, None
            callback()
        return super().commit()


@pytest.fixture
def payout_pg(pg_dsn, monkeypatch):
    schema = 'vkpi_payout_test_' + uuid4().hex
    local = threading.local()
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5, options=_OPTIONS) as admin:
        try:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            admin.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
            admin.execute(_SCHEMA)

            def connect(*, before_commit=None):
                raw = psycopg.connect(pg_dsn, connect_timeout=5, options=_OPTIONS)
                raw.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
                raw.commit()
                return _PausedCommitConnection(raw, before_commit)

            def forbidden_dispatch(*_args, **_kwargs):
                pytest.fail('DB-only payout tests must never reach the dispatch adapter')

            monkeypatch.setattr(payouts, 'get_conn', lambda: local.conn)
            monkeypatch.setattr(payouts, '_dispatch_payout', forbidden_dispatch)
            # Do not mock runtime detection, SQL, accrual, processing, or commits.
            assert payouts.is_postgres_runtime()
            yield SimpleNamespace(admin=admin, local=local, connect=connect)
        finally:
            admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def _run(fixture, state, *, process=False, before_commit=None):
    conn = fixture.connect(before_commit=before_commit)
    fixture.local.conn = conn
    state['pid'] = conn._raw.info.backend_pid
    state['started'].set()
    try:
        return payouts.process_cycle(_CYCLE, 7) if process else payouts.accrue_cycle(_CYCLE)
    finally:
        conn.close()
        state['finished'].set()


def _state():
    return {'started': threading.Event(), 'finished': threading.Event()}


def _wait_for_lock(fixture, state):
    assert state['started'].wait(3), 'test worker did not connect'
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        row = fixture.admin.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s', (state['pid'],)).fetchone()
        if row and row[0] == 'Lock':
            assert not state['finished'].is_set()
            return
        assert not state['finished'].is_set(), 'writer finished before held-lock observation'
        time.sleep(0.01)
    pytest.fail('No real PostgreSQL lock wait was observed')


def _assert_pending(fixture):
    rows = fixture.admin.execute(
        'SELECT user_id,amount_cents,gmv_cents,order_count,currency,status,order_ids_json,paid_tx_id FROM payouts'
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][:6] == (9, 1250, 10000, 2, 'USD', 'pending')
    assert set(json.loads(rows[0][6])) == {101, 102} and rows[0][7] is None
    assert fixture.admin.execute('SELECT status,processed_at FROM payout_cycles').fetchone() == ('active', None)


def test_concurrent_accrual_waits_and_creates_one_pending_payout(payout_pg):
    fixture = payout_pg
    blocker = fixture.connect()
    blocker.execute('SELECT id FROM payout_cycles WHERE id=? FOR NO KEY UPDATE', (_CYCLE,))
    states = [_state(), _state()]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_run, fixture, state) for state in states]
        try:
            for state in states:
                _wait_for_lock(fixture, state)
            assert fixture.admin.execute('SELECT COUNT(*) FROM payouts').fetchone()[0] == 0
        finally:
            blocker.commit()
            blocker.close()
        results = [future.result(timeout=8) for future in futures]
    assert sorted(result['accrued_count'] for result in results) == [0, 1]
    _assert_pending(fixture)


def test_processing_waits_for_accrual_and_cannot_complete_pending_payout(payout_pg):
    fixture = payout_pg
    accrual_ready, release_accrual = threading.Event(), threading.Event()
    accrual, processing = _state(), _state()

    def pause_accrual_commit():
        accrual_ready.set()
        assert release_accrual.wait(5), 'test did not release its owned accrual transaction'

    with ThreadPoolExecutor(max_workers=2) as executor:
        accrued = executor.submit(_run, fixture, accrual, before_commit=pause_accrual_commit)
        try:
            assert accrual_ready.wait(3), 'accrual did not reach commit'
            processed = executor.submit(_run, fixture, processing, process=True)
            _wait_for_lock(fixture, processing)
            assert fixture.admin.execute('SELECT COUNT(*) FROM payouts').fetchone()[0] == 0
        finally:
            release_accrual.set()
        assert accrued.result(timeout=8)['accrued_count'] == 1
        result = processed.result(timeout=8)
    assert result['status'] == 'partial' and result['cycle_complete'] is False
    assert result['processed_count'] == 0 and result['unresolved_count'] == 1
    assert result['provider_calls_performed'] is False and result['unknown_count'] == 0
    _assert_pending(fixture)
