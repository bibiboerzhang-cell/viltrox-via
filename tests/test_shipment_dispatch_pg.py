"""Synthetic shipment-record races on owned PG; no carrier or real dispatch.

The production record writer and rejection function execute real SQL. Manager
authorization and audit are fixed test doubles, not a production auth audit.
"""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.db.connection import PostgresCompatConnection
from app.domains.projects import shipment_approval, workflow_evidence_project_writes as writes

pytestmark = pytest.mark.pg
_OPTIONS = '-c statement_timeout=15000 -c lock_timeout=5000'
_STAFF = {'id': 7, 'staff_id': 7, 'role': 'manager'}
_SCHEMA = """
CREATE TABLE kols(id BIGINT PRIMARY KEY);
CREATE TABLE vkpi_projects(id BIGINT PRIMARY KEY,kol_id BIGINT REFERENCES kols(id),product_sku TEXT,product_name TEXT);
CREATE TABLE vkpi_kol_pool(id BIGINT PRIMARY KEY,linked_main_kol_id BIGINT REFERENCES kols(id));
CREATE TABLE vkpi_project_kol_assignments(id BIGINT PRIMARY KEY,project_id BIGINT REFERENCES vkpi_projects(id),
 kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id));
CREATE TABLE vkpi_shipment_approvals(id BIGSERIAL PRIMARY KEY,project_id BIGINT,kol_pool_id BIGINT,
 status TEXT,requested_by BIGINT,approved_by BIGINT,approved_at TIMESTAMPTZ,reason TEXT DEFAULT '',
 created_at TIMESTAMPTZ DEFAULT NOW(),updated_at TIMESTAMPTZ DEFAULT NOW(),UNIQUE(project_id,kol_pool_id));
CREATE TABLE vkpi_sample_assets(id BIGSERIAL PRIMARY KEY,project_id BIGINT REFERENCES vkpi_projects(id),
 kol_id BIGINT REFERENCES kols(id),product_sku TEXT,product_name TEXT,serial_number TEXT,sample_cost_cents BIGINT,
 currency TEXT,return_required BOOLEAN,status TEXT,shipped_at TIMESTAMPTZ,received_at TIMESTAMPTZ,
 note TEXT,metadata_json JSONB,created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ);
CREATE TABLE vkpi_shipments(id BIGSERIAL PRIMARY KEY,project_id BIGINT REFERENCES vkpi_projects(id),
 assignment_id BIGINT REFERENCES vkpi_project_kol_assignments(id),sample_asset_id BIGINT REFERENCES vkpi_sample_assets(id),
 carrier TEXT,tracking_number TEXT,status TEXT,shipping_cost_cents BIGINT,currency TEXT,shipped_at TIMESTAMPTZ,
 delivered_at TIMESTAMPTZ,evidence_url TEXT,note TEXT,metadata_json JSONB,created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ);
INSERT INTO kols VALUES(9);
INSERT INTO vkpi_projects VALUES(71,9,'SYNTHETIC-SKU','Synthetic Sample');
INSERT INTO vkpi_kol_pool VALUES(17,9);
INSERT INTO vkpi_project_kol_assignments VALUES(81,71,17);
INSERT INTO vkpi_shipment_approvals(project_id,kol_pool_id,status,approved_by,approved_at)
 VALUES(71,17,'approved',7,NOW());
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
def dispatch_pg(pg_dsn, monkeypatch):
    schema = 'vkpi_dispatch_test_' + uuid4().hex
    local = threading.local()
    audits = []
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

            def table_exists(name):
                row = local.conn.execute("SELECT to_regclass(current_schema() || '.' || ?) AS relation", (name,)).fetchone()
                return bool(row and row['relation'])

            for module in (writes, shipment_approval):
                monkeypatch.setattr(module, 'get_conn', lambda: local.conn)
            monkeypatch.setattr(writes, 'ensure_vkpi_schema', lambda: None)
            monkeypatch.setattr(shipment_approval, 'table_exists', table_exists)
            monkeypatch.setattr(shipment_approval.scope, 'assert_project_access', lambda *_a, **_k: None)
            monkeypatch.setattr(shipment_approval.scope, 'can_view_all', lambda *_a, **_k: True)
            monkeypatch.setattr(writes.audit, 'log_business_event', lambda **kwargs: audits.append(kwargs))
            yield SimpleNamespace(admin=admin, schema=schema, local=local, connect=connect, audits=audits)
        finally:
            admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def _run(fixture, state, *, reject=False, before_commit=None):
    conn = fixture.connect(before_commit=before_commit)
    fixture.local.conn = conn
    state['pid'] = conn._raw.info.backend_pid
    state['started'].set()
    try:
        if reject:
            return shipment_approval.reject(71, 17, staff=_STAFF, reason='Synthetic revocation')
        return writes.add_project_shipment(71, {'assignment_id': 81, 'tracking_number': 'SYNTHETIC-TRACK-ONLY'}, staff=_STAFF)
    finally:
        conn.close()  # Real rollback releases locks on validation errors.
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
        assert not state['finished'].is_set(), 'writer finished before the held-lock observation'
        time.sleep(0.01)
    pytest.fail('No real PostgreSQL lock wait was observed')


def _counts(fixture):
    return tuple(fixture.admin.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
                 for table in ('vkpi_sample_assets', 'vkpi_shipments'))


def test_same_tracking_concurrent_records_wait_and_reuse_one_receipt(dispatch_pg):
    fixture = dispatch_pg
    blocker = fixture.connect()
    blocker.execute('SELECT id FROM vkpi_projects WHERE id=71 FOR NO KEY UPDATE')
    states = [_state(), _state()]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_run, fixture, state) for state in states]
        try:
            for state in states:
                _wait_for_lock(fixture, state)
            assert _counts(fixture) == (0, 0)
        finally:
            blocker.commit()
            blocker.close()
        results = [future.result(timeout=8) for future in futures]
    assert results[0]['id'] == results[1]['id']
    assert sum(bool(item.get('receipt_reused')) for item in results) == 1
    assert _counts(fixture) == (1, 1) and len(fixture.audits) == 1
    sample = fixture.admin.execute('SELECT kol_id FROM vkpi_sample_assets').fetchone()
    assert sample[0] == 9  # Main author, never pool 17.


def test_concurrent_rejection_commits_before_dispatch_and_blocks_all_records(dispatch_pg):
    fixture = dispatch_pg
    rejection_ready, release_rejection = threading.Event(), threading.Event()
    rejection, dispatch = _state(), _state()

    def pause_rejection_commit():
        rejection_ready.set()
        assert release_rejection.wait(5), 'test did not release its owned rejection transaction'

    with ThreadPoolExecutor(max_workers=2) as executor:
        rejected = executor.submit(_run, fixture, rejection, reject=True, before_commit=pause_rejection_commit)
        try:
            assert rejection_ready.wait(3)
            dispatched = executor.submit(_run, fixture, dispatch)
            _wait_for_lock(fixture, dispatch)
            assert _counts(fixture) == (0, 0)
        finally:
            release_rejection.set()
        assert rejected.result(timeout=8)['status'] == 'rejected'
        with pytest.raises(shipment_approval.ShipmentNotApproved, match='rejected'):
            dispatched.result(timeout=8)
    assert _counts(fixture) == (0, 0) and fixture.audits == []
    assert fixture.admin.execute('SELECT status FROM vkpi_shipment_approvals').fetchone()[0] == 'rejected'
