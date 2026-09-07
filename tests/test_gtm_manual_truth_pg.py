"""Actual GTM review transactions in disposable PG, not transport acceptance."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.db.connection import PostgresCompatConnection
from app.domains.market_brain import outreach_reply_truth, outreach_truth_bridge
from test_gtm_outreach_truth_bridge import MANAGER, _actual, _bind, _message, _seed, _verify

pytestmark = pytest.mark.pg
_OPTIONS = '-c statement_timeout=15000 -c lock_timeout=2000'
_UP = Path(__file__).resolve().parents[1] / 'migrations/277_vkpi_action_outreach_truth_bridge.sql'
_SCHEMA = """
CREATE TABLE kols(id BIGINT PRIMARY KEY);
CREATE TABLE vkpi_action_inbox (
 id BIGINT PRIMARY KEY,dedupe_key TEXT,category TEXT,title TEXT,detail TEXT,priority TEXT,
 entity_type TEXT,entity_id TEXT,suggested_endpoint TEXT,estimated_cost_cents INTEGER,
 writes_business_data INTEGER,uses_llm INTEGER,requires_approval INTEGER,owner_staff_id BIGINT,
 reason TEXT,payload_json TEXT,touches_v6_fit INTEGER,expected_gain TEXT,risk_level TEXT,
 evidence_refs_json TEXT,verification_plan_json TEXT,affected_tables_json TEXT,approval_reason TEXT,
 status TEXT,approved_by_staff_id BIGINT,approved_at TEXT,approval_snapshot_sha256 TEXT);
CREATE TABLE vkpi_prediction_runs (
 organization_id TEXT,run_id TEXT,task_type TEXT,product_sku TEXT,channel TEXT,horizon_days INTEGER,
 input_summary TEXT,prediction TEXT,p10 DOUBLE PRECISION,p50 DOUBLE PRECISION,p90 DOUBLE PRECISION,
 created_at TEXT,PRIMARY KEY(organization_id,run_id));
CREATE TABLE vkpi_kol_pool(id BIGINT PRIMARY KEY,platform TEXT,linked_main_kol_id BIGINT REFERENCES kols(id));
CREATE TABLE vkpi_projects(id BIGINT PRIMARY KEY,kol_id BIGINT REFERENCES kols(id),
 product_sku TEXT,platform TEXT,stage_status TEXT);
CREATE TABLE vkpi_messages(id BIGINT PRIMARY KEY,project_id BIGINT REFERENCES vkpi_projects(id),
 kol_id BIGINT REFERENCES kols(id),source TEXT,direction TEXT,body TEXT,snippet TEXT,evidence_url TEXT,
 captured_at TIMESTAMPTZ,created_at TIMESTAMPTZ,metadata_json JSONB DEFAULT '{}'::jsonb);
CREATE TABLE vkpi_event_ledger(id BIGSERIAL PRIMARY KEY,organization_id BIGINT NOT NULL,
 event_type TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,actor_type TEXT NOT NULL,
 actor_id TEXT NOT NULL,source TEXT NOT NULL,payload_json JSONB NOT NULL,trace_id TEXT NOT NULL,
 confidence DOUBLE PRECISION,provenance_json JSONB NOT NULL,
 occurred_at TIMESTAMPTZ DEFAULT NOW(),created_at TIMESTAMPTZ DEFAULT NOW());
INSERT INTO kols VALUES(9);
"""


@pytest.fixture
def manual_pg(pg_dsn, monkeypatch):
    schema = 'vkpi_manual_truth_test_' + uuid4().hex
    raw = None
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5, options=_OPTIONS) as admin:
        try:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            raw = psycopg.connect(pg_dsn, connect_timeout=5, options=_OPTIONS)
            raw.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
            raw.execute(_SCHEMA)
            raw.execute(_UP.read_text(encoding='utf-8'))
            raw.commit()
            conn = PostgresCompatConnection(raw, pool=None)

            def table_exists(name):
                row = conn.execute("SELECT to_regclass(current_schema() || '.' || ?) AS relation", (name,)).fetchone()
                return bool(row and row['relation'])

            for module in (outreach_truth_bridge, outreach_reply_truth):
                monkeypatch.setattr(module, 'table_exists', table_exists)
                monkeypatch.setattr(module, 'get_conn', lambda: conn)
            # Clock, PG runtime branch, approval hashes, events and SQL are not mocked.
            yield conn, admin, schema
        finally:
            if raw is not None:
                raw.rollback()
                raw.close()
            admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def _counts(observer, schema):
    return tuple(observer.execute(sql.SQL('SELECT COUNT(*) FROM {}.{}').format(
        sql.Identifier(schema), sql.Identifier(table),
    )).fetchone()[0] for table in (
        'vkpi_action_outreach_truth_bridges', 'vkpi_action_outreach_reply_truth_receipts',
    ))


def _reject_event(conn, event_type):
    # A real server constraint fails AFTER the business insert, proving rollback.
    assert event_type in {'action_outreach_bound', 'action_outreach_reply_verified'}
    conn.execute('ALTER TABLE vkpi_event_ledger ADD CONSTRAINT synthetic_event_failure '
                 f"CHECK(event_type <> '{event_type}')")
    conn.commit()


def _allow_events(conn):
    conn.execute('ALTER TABLE vkpi_event_ledger DROP CONSTRAINT synthetic_event_failure')
    conn.commit()


def test_domain_receipts_replay_and_event_rollback_stay_manager_attested(manual_pg):
    conn, observer, schema = manual_pg
    _seed(conn)
    _message(conn, 101, 10, 'inbound', '2026-08-13T00:00:00+00:00',
             source='provider_verified', body='synthetic manager review sample',
             metadata={'provider_verified': True, 'receipt_id': 'client-claim'})
    conn.commit()
    assert _actual(conn)['reply_outcome'] is None
    _reject_event(conn, 'action_outreach_bound')
    assert _bind(conn)['reason'] == 'outreach_binding_write_failed'
    assert _counts(observer, schema) == (0, 0)
    _allow_events(conn)
    bound = _bind(conn)
    assert bound['ok'] is True and bound['idempotent'] is False
    assert _bind(conn)['idempotent'] is True
    assert _counts(observer, schema) == (1, 0)
    assert _actual(conn)['reply_outcome'] is None

    _reject_event(conn, 'action_outreach_reply_verified')
    assert _verify(conn, bound['id'])['reason'] == 'outreach_reply_write_failed'
    assert _counts(observer, schema) == (1, 0)
    _allow_events(conn)
    candidate = outreach_reply_truth.get_reply_review_candidate(
        bound['id'], outcome='replied', staff=MANAGER, _connection=conn,
    )
    assert candidate['ok'] is True
    verified = _verify(conn, bound['id'], candidate=candidate)
    assert verified['ok'] is True and verified['idempotent'] is False
    assert _verify(conn, bound['id'], candidate=candidate)['idempotent'] is True
    assert _counts(observer, schema) == (1, 1)
    actual = _actual(conn)
    assert actual['reply_outcome'] == 1
    assert actual['reply_outcome_binding'] == 'manager_verified_action_project_outreach_receipt/v1'
    coverage = outreach_truth_bridge.outreach_prediction_coverage(conn)
    assert coverage['verified_actual'] == 1 and coverage['claimable'] is False
    assert coverage['provider_completeness_verified'] is False
    assert coverage['evidence_class'] == 'manager_attested_mutable_message_snapshot'
    assert coverage['claim_level'] == 'descriptive_only'
    events = observer.execute(sql.SQL(
        'SELECT event_type,COUNT(*) FROM {}.vkpi_event_ledger GROUP BY event_type'
    ).format(sql.Identifier(schema))).fetchall()
    assert dict(events) == {'action_approved': 1, 'action_outreach_bound': 1, 'action_outreach_reply_verified': 1}
    stored = observer.execute(sql.SQL(
        'SELECT review_candidate_json FROM {}.vkpi_action_outreach_reply_truth_receipts'
    ).format(sql.Identifier(schema))).fetchone()[0]
    assert isinstance(stored, dict) and stored['schema'] == 'vkpi_action_outreach_reply_review_candidate/v1'
    assert 'client-claim' not in json.dumps(stored)
