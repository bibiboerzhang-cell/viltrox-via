"""Real PG capture contracts in an owned scratch schema; no transport adapter.

Access is a fixed manager fixture, and audit is a spy, not an audit integration
claim. SQL, RETURNING, FK/CHECK failures and transaction readback are real PG.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.db.connection import PostgresCompatConnection
from app.domains.evidence import common, messages
from app.domains.projects import workflow_detail_sections as sections
from app.domains.projects import workflow_evidence_project_writes as project_writes
from app.domains.recommendations import pool_action_bridge
from test_message_capture_truth import EXPECTED, STAFF, _capture

pytestmark = pytest.mark.pg
_OPTIONS = '-c statement_timeout=15000 -c lock_timeout=2000'
_SCHEMA = """
CREATE TABLE kols (id BIGINT PRIMARY KEY, channel_name TEXT, channel_url TEXT);
CREATE TABLE vkpi_projects (id BIGINT PRIMARY KEY, kol_id BIGINT REFERENCES kols(id),
 assigned_staff_id BIGINT, created_by_staff_id BIGINT, platform TEXT, project_name TEXT, stage TEXT);
CREATE TABLE staff (id BIGINT PRIMARY KEY,user_id BIGINT);
CREATE TABLE users (id BIGINT PRIMARY KEY,name TEXT,email TEXT);
CREATE TABLE vkpi_kol_claims (id BIGINT PRIMARY KEY,kol_id BIGINT,staff_id BIGINT,status TEXT);
CREATE TABLE vkpi_kol_pool (id BIGINT PRIMARY KEY,linked_main_kol_id BIGINT REFERENCES kols(id));
CREATE TABLE vkpi_project_kol_assignments (id BIGINT PRIMARY KEY,
 project_id BIGINT REFERENCES vkpi_projects(id),kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id),stage_status TEXT);
CREATE TABLE vkpi_messages (id BIGSERIAL PRIMARY KEY,project_id BIGINT REFERENCES vkpi_projects(id),
 kol_id BIGINT REFERENCES kols(id),staff_id BIGINT,source TEXT,direction TEXT,sender TEXT,receiver TEXT,
 body TEXT CHECK(body <> 'synthetic-check-rejection'),snippet TEXT,evidence_url TEXT,
 follow_up_due_at TIMESTAMPTZ,captured_at TIMESTAMPTZ,metadata_json JSONB,created_at TIMESTAMPTZ);
CREATE TABLE vkpi_message_attachments (id BIGINT PRIMARY KEY,message_id BIGINT,file_url TEXT,
 file_type TEXT,metadata_json JSONB,created_at TIMESTAMPTZ);
CREATE TABLE vkpi_content_posts (id BIGINT PRIMARY KEY,project_id BIGINT,published_at TIMESTAMPTZ);
CREATE TABLE vkpi_content_assets (id BIGINT PRIMARY KEY,project_id BIGINT,created_at TIMESTAMPTZ);
CREATE TABLE vkpi_project_terms (id BIGINT PRIMARY KEY,project_id BIGINT);
CREATE TABLE vkpi_project_deliverables (id BIGINT PRIMARY KEY,project_id BIGINT,due_at TIMESTAMPTZ);
CREATE TABLE vkpi_sample_assets (id BIGINT PRIMARY KEY,project_id BIGINT,created_at TIMESTAMPTZ);
CREATE TABLE vkpi_shipments (id BIGINT PRIMARY KEY,project_id BIGINT,created_at TIMESTAMPTZ);
INSERT INTO kols VALUES (9,'primary','https://example.invalid/9'),(10,'second','https://example.invalid/10');
INSERT INTO vkpi_projects VALUES (71,9,7,7,'youtube','fixture','agreed'),(72,NULL,7,7,'youtube','other','agreed');
INSERT INTO vkpi_kol_pool VALUES (17,9),(18,10);
INSERT INTO vkpi_project_kol_assignments VALUES (81,71,17,'active'),(82,72,18,'active'),(83,71,18,'active');
"""


@pytest.fixture
def capture_pg(pg_dsn, monkeypatch):
    schema = 'vkpi_capture_test_' + uuid4().hex
    raw = None
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5, options=_OPTIONS) as admin:
        try:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            raw = psycopg.connect(pg_dsn, connect_timeout=5, options=_OPTIONS)
            raw.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
            raw.execute(_SCHEMA)
            raw.commit()
            conn = PostgresCompatConnection(raw, pool=None)
            audits = []
            for module in (messages, common, project_writes):
                monkeypatch.setattr(module, 'get_conn', lambda: conn)
                if hasattr(module, 'ensure_vkpi_schema'):
                    monkeypatch.setattr(module, 'ensure_vkpi_schema', lambda: None)
            monkeypatch.setattr(messages.scope, 'assert_project_access', lambda *_a, **_k: None)
            monkeypatch.setattr(messages.scope, 'can_view_all', lambda *_a, **_k: True)
            monkeypatch.setattr(messages.scope, 'effective_staff_id', lambda *_a, **_k: None)
            monkeypatch.setattr(messages, '_actor_id', lambda _: 7)
            monkeypatch.setattr(messages.audit, 'log_business_event', lambda **kwargs: audits.append(kwargs))

            def never_send(*_a, **_k):
                pytest.fail('manual capture invoked a transport/outcome bridge')

            monkeypatch.setattr(pool_action_bridge, 'bridge_message_outreach', never_send)
            yield SimpleNamespace(conn=conn, observer=admin, schema=schema, audits=audits)
        finally:
            if raw is not None:
                raw.rollback()
                raw.close()
            admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def _observed_rows(fixture):
    return fixture.observer.execute(sql.SQL(
        'SELECT id,kol_id,metadata_json,body FROM {}.vkpi_messages ORDER BY id'
    ).format(sql.Identifier(fixture.schema))).fetchall()


def test_both_message_writers_commit_correct_member_and_project_unknown(capture_pg):
    fixture = capture_pg
    for writer in ('generic', 'project'):
        body = {'body': 'synthetic capture ' + writer, 'source': 'provider_verified', 'direction': 'inbound',
                'metadata': {'assignment_id': 83, 'kol_pool_id': 18, 'provider_message_id': 'untrusted',
                             'communication_truth': {'sent': True, 'replied': True}},
                'communication_truth': {'transport_outcome_eligible': True}}
        original = copy.deepcopy(body)
        item = _capture(writer, body)
        assert body == original and item['kol_id'] == 10 and item['project_id'] == 71
        assert item['communication_truth'] == EXPECTED
        observed = _observed_rows(fixture)
        assert observed[-1][0] == item['id'] and observed[-1][1] == 10
        assert observed[-1][2] == body['metadata']  # Raw psycopg JSONB, not a mocked/dialect substitute.
        assert observed[-1][3] == body['body']
        views = [messages.get_message(item['id'], staff=STAFF)['message'],
                 *messages.list_messages(project_id=71, staff=STAFF)['messages'],
                 *sections.fetch_content_context(fixture.conn, 71)['messages']]
        assert all(view['communication_truth'] == EXPECTED for view in views)
    assert len(_observed_rows(fixture)) == len(fixture.audits) == 2


def test_bad_identity_and_database_constraint_do_not_leave_partial_capture(capture_pg):
    fixture = capture_pg
    for writer in ('generic', 'project'):
        for bad in ({'metadata': {'assignment_id': 82, 'kol_pool_id': 18}},
                    {'kol_id': 9, 'metadata': {'assignment_id': 83}}, {'metadata': []}, {'body': {}}):
            with pytest.raises(ValueError):
                _capture(writer, bad)
        assert _observed_rows(fixture) == [] and fixture.audits == []
        with pytest.raises(psycopg.errors.CheckViolation):
            _capture(writer, {'body': 'synthetic-check-rejection'})
        assert fixture.conn._raw.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert _observed_rows(fixture) == [] and fixture.audits == []
    # Failure did not poison the next real transaction, and no earlier partial row survived.
    result = _capture('project', {'body': 'recovered capture', 'kol_id': 10})
    assert len(_observed_rows(fixture)) == len(fixture.audits) == 1
    assert result['kol_id'] == 10 and result['communication_truth'] == EXPECTED
