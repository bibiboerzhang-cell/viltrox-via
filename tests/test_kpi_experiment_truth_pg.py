"""KPI/experiment grouped SQL against a test-owned PostgreSQL schema.

No business schemas, suppliers, payment, shipping or model activation. Reader
checks use READ ONLY transactions and the real compatibility/parameter layer.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.db import connection
from app.domains import business_truth
from app.domains.experiments import scoring
from app.domains.staff import decision_staff_kpi, kpi_ledger, kpi_rollup
from app.shared import vkpi_decision_common
from app.shared.vkpi_kpi_communication_truth import KPI_LABEL_SEMANTICS, project_kpi_source_row

pytestmark = pytest.mark.pg
_OPTIONS = '-c statement_timeout=15000 -c lock_timeout=2000'
_DAY = '2026-09-01'
_SCHEMA = """
CREATE TABLE kols(id BIGINT PRIMARY KEY,channel_name TEXT,platform TEXT);
CREATE TABLE vkpi_projects(id BIGINT PRIMARY KEY,project_name TEXT,product_sku TEXT);
CREATE TABLE vkpi_kpi_ledger(
 id BIGSERIAL PRIMARY KEY,ledger_date DATE NOT NULL,staff_id BIGINT,kol_id BIGINT,
 project_id BIGINT,metric_key TEXT NOT NULL,metric_value NUMERIC(18,4) NOT NULL DEFAULT 0,
 source_type TEXT NOT NULL DEFAULT 'system',source_ref TEXT,confidence TEXT NOT NULL DEFAULT 'confirmed',
 metadata_json TEXT NOT NULL DEFAULT '{}',created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(ledger_date,metric_key,source_ref));
CREATE TABLE vkpi_recommendation_feature_snapshot(
 id BIGSERIAL PRIMARY KEY,recommendation_id BIGINT,arm TEXT,
 rerank_applied BOOLEAN,outcome_label INTEGER,created_at TIMESTAMPTZ NOT NULL);
CREATE TABLE vkpi_recommendation_outcomes(
 recommendation_id BIGINT PRIMARY KEY,kol_pool_id BIGINT,
 was_shortlisted BOOLEAN,was_claimed BOOLEAN,was_rejected BOOLEAN,project_created BOOLEAN,
 outreach_sent BOOLEAN,reply_received BOOLEAN,content_published BOOLEAN,
 agreement_reached BOOLEAN,order_attributed BOOLEAN);
"""


@pytest.fixture
def truth_pg(pg_dsn, monkeypatch):
    schema = 'vkpi_kpi_experiment_test_' + uuid4().hex
    raw = None
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5, options=_OPTIONS) as admin:
        try:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            raw = psycopg.connect(pg_dsn, connect_timeout=5, options=_OPTIONS)
            raw.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
            raw.execute(_SCHEMA)
            raw.commit()
            conn = connection.PostgresCompatConnection(raw, pool=None)
            queries = []

            def strict_rows(actual_conn, statement, params=()):
                assert actual_conn is conn
                queries.append((statement, params))
                return [dict(row) for row in conn.execute(statement, params).fetchall()]

            def table_exists(name):
                result = conn.execute("SELECT to_regclass(current_schema() || '.' || ?) AS relation", (name,)).fetchone()
                return bool(result and result['relation'])

            monkeypatch.setattr(vkpi_decision_common, '_safe_rows', strict_rows)
            monkeypatch.setattr(kpi_ledger, 'get_conn', lambda: conn)
            monkeypatch.setattr(scoring, 'get_conn', lambda: conn)
            monkeypatch.setattr(connection, 'table_exists', table_exists)
            yield SimpleNamespace(conn=conn, observer=admin, schema=schema,
                                  queries=queries, strict_rows=strict_rows)
        finally:
            if raw is not None:
                raw.rollback()
                raw.close()
            admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def _ledger(fixture, key, value, source, *, staff=7, day=_DAY, confidence='confirmed', metadata=None):
    fixture.conn.execute(
        'INSERT INTO vkpi_kpi_ledger(ledger_date,staff_id,metric_key,metric_value,source_type,source_ref,confidence,metadata_json) '
        'VALUES(?,?,?,?,?,?,?,?)', (day, staff, key, value, 'synthetic_test', source, confidence, json.dumps(metadata or {})),
    )


def _read_only(fixture):
    fixture.conn.commit()
    fixture.conn.execute('SET TRANSACTION READ ONLY')
    assert fixture.conn.execute('SHOW transaction_read_only').fetchone()[0] == 'on'


def _stored(fixture, table):
    assert table in {'vkpi_kpi_ledger', 'vkpi_recommendation_feature_snapshot', 'vkpi_recommendation_outcomes'}
    return fixture.observer.execute(sql.SQL('SELECT * FROM {}.{} ORDER BY 1').format(
        sql.Identifier(fixture.schema), sql.Identifier(table),
    )).fetchall()


def _derived_metadata():
    return {'label_semantics': KPI_LABEL_SEMANTICS, 'components': [
        {'metric_key': 'new_kol', 'metric_value': 2, 'weight': 2, 'contribution': 4, 'source_count': 2},
    ]}


def test_kpi_grouped_numeric_date_scope_preserves_recorded_and_unknown(truth_pg):
    fixture = truth_pg
    _ledger(fixture, 'stage_replied', '2.1250', 'reply:one')
    _ledger(fixture, 'stage_replied', '1.8750', 'reply:two')
    _ledger(fixture, 'stage_replied', '999', 'reply:other-staff', staff=8)
    _ledger(fixture, 'stage_replied', '888', 'reply:old', day='2000-01-01')
    _ledger(fixture, 'stage_replied', '777', 'reply:stale', confidence='stale')
    _ledger(fixture, 'stage_agreed', '0', 'agreement:zero')
    _ledger(fixture, 'workload_score', '90.1250', 'legacy:workload')
    _read_only(fixture)
    before = _stored(fixture, 'vkpi_kpi_ledger')
    result = vkpi_decision_common._staff_kpi_breakdown(fixture.conn, 7, start=_DAY, limit=20)
    grouped = {row['metric_key']: row for row in result['grouped']}
    assert grouped['stage_replied']['total_value'] is None
    assert grouped['stage_replied']['recorded_total_value'] == 4
    assert grouped['stage_replied']['source_count'] == 2
    assert grouped['workload_score']['total_value'] is None
    assert grouped['workload_score']['recorded_total_value'] == 90.125
    assert grouped['stage_agreed']['total_value'] == 0
    source = result['source_rows']
    assert len(source) == 4 and all(row['staff_id'] == 7 for row in source)
    assert {row['source_ref'] for row in source} == {'reply:one', 'reply:two', 'agreement:zero', 'legacy:workload'}
    assert all(row['metric_value'] is None for row in source if row['metric_key'] != 'stage_agreed')
    assert sum(row['recorded_metric_value'] for row in source if row['metric_key'] == 'stage_replied') == 4
    assert any('GROUP BY metric_key' in query for query, _params in fixture.queries)
    assert _stored(fixture, 'vkpi_kpi_ledger') == before


def test_staff_grouped_ledger_cannot_restore_legacy_credit(truth_pg):
    fixture = truth_pg
    _ledger(fixture, 'workload_score', 90, 'legacy:workload')
    _ledger(fixture, 'workload_score', 4, 'new:workload', metadata=_derived_metadata())
    _ledger(fixture, 'kpi_credit', 999, 'legacy:credit')
    _ledger(fixture, 'recommendation_reply_received', 2, 'legacy:reply')
    _ledger(fixture, 'recommendation_project_created', 1, 'project:one')
    _ledger(fixture, 'kpi_credit', 9999, 'other:credit', staff=8)
    _read_only(fixture)
    before = _stored(fixture, 'vkpi_kpi_ledger')
    rows = {7: decision_staff_kpi._base_staff_row({'staff_name': 'Synthetic'}, 7)}
    decision_staff_kpi._collect_ledger_metrics(
        rows, conn=fixture.conn, start=_DAY, staff_id=7, safe_rows=fixture.strict_rows,
        current_kpi_ledger_sql=business_truth.current_kpi_ledger_sql,
    )
    assert rows[7]['recorded_ledger_workload_score'] == 94
    assert rows[7]['recorded_kpi_credit'] == 999
    assert rows[7]['ledger_workload_score'] is rows[7]['kpi_credit'] is None
    # Stage counts remain audit-only; existing non-communication weights survive.
    rows[7].update(contacted=3, replied=2, agreed=1)
    result = decision_staff_kpi._finalize_rows(rows)[0]
    assert result['recorded_stage_counts'] == {'contacted': 3, 'replied': 2}
    assert result['operational_workload_score'] == 4
    assert result['legacy_workload_score'] == 11
    assert result['workload_score'] is result['kpi_credit'] is result['contacted'] is result['replied'] is None
    assert result['recommendation_projects'] == 1
    assert _stored(fixture, 'vkpi_kpi_ledger') == before


def test_kpi_upsert_replay_retains_old_derived_and_excludes_transport(truth_pg):
    fixture = truth_pg
    _ledger(fixture, 'workload_score', 90, 'same-legacy')
    _ledger(fixture, 'new_kol', 2, 'known-source')
    _ledger(fixture, 'recommendation_reply_received', 200, 'legacy-reply')
    fixture.conn.commit()
    before = _stored(fixture, 'vkpi_kpi_ledger')
    common = {'ledger_date': _DAY, 'staff_id': 7, 'source_type': 'derived_kpi'}
    for metric in ('stage_contacted', 'stage_replied', 'recommendation_outreach_sent', 'recommendation_reply_received'):
        assert kpi_ledger._upsert_entry(fixture.conn, metric_key=metric, metric_value=1,
                                       source_ref='rejected:' + metric, **common) == 'skipped_unverified'
    assert kpi_ledger._upsert_entry(fixture.conn, metric_key='workload_score', metric_value=4,
                                   source_ref='same-legacy', metadata=_derived_metadata(), **common) == 'skipped_unverified'
    fixture.conn.commit()
    assert _stored(fixture, 'vkpi_kpi_ledger') == before
    for expected in ('inserted', 'updated'):
        assert kpi_ledger._upsert_entry(fixture.conn, metric_key='workload_score', metric_value=4,
                                       source_ref='current', metadata=_derived_metadata(), **common) == expected
        fixture.conn.commit()
    row = fixture.conn.execute("SELECT * FROM vkpi_kpi_ledger WHERE source_ref='current'").fetchone()
    assert project_kpi_source_row(dict(row))['metric_value_status'] == 'operational'
    deps = kpi_ledger._rollup_dependencies()
    ctx = kpi_rollup.RollupContext(deps, fixture.conn, _DAY, 7, 'now', {}, defaultdict(int))
    scores, components = kpi_rollup._collect_staff_scores(ctx)
    assert scores[7]['workload'] == 4 and set(components[7]) == {'new_kol'}
    assert len(_stored(fixture, 'vkpi_kpi_ledger')) == len(before) + 1


def _snapshot(fixture, rec_id, *, arm='control', old_label=1, applied=False, days_old=0, flags=None):
    now = datetime.now(timezone.utc) - timedelta(days=days_old)
    fixture.conn.execute('INSERT INTO vkpi_recommendation_feature_snapshot '
                         '(recommendation_id,arm,rerank_applied,outcome_label,created_at) VALUES(?,?,?,?,?)',
                         (rec_id, arm, applied, old_label, now))
    if flags is not None:
        allowed = {'was_shortlisted', 'was_claimed', 'was_rejected', 'project_created', 'outreach_sent',
                   'reply_received', 'content_published', 'agreement_reached', 'order_attributed'}
        assert set(flags) <= allowed
        names = list(flags)
        fixture.conn.execute(f"INSERT INTO vkpi_recommendation_outcomes(recommendation_id,{','.join(names)}) "
                             f"VALUES(?{',?' * len(names)})", (rec_id, *flags.values()))


def _assert_no_effect_claim(summary):
    assert summary['claim_status'] == 'descriptive_only'
    assert summary['algorithm_effect_status'] == 'not_evaluated'
    assert summary['write_db'] is summary['provider_calls'] is False
    assert summary['communication_evidence']['sent'] is summary['communication_evidence']['replied'] is None


def test_experiment_boolean_groups_recompute_labels_and_keep_pending(truth_pg):
    fixture = truth_pg
    for rec_id in range(1, 11):
        _snapshot(fixture, rec_id, old_label=0, flags={'was_claimed': True})
    _snapshot(fixture, 11, flags={'was_rejected': True})
    _snapshot(fixture, 12, flags={'outreach_sent': True, 'reply_received': True})
    _snapshot(fixture, 13)  # Missing outcome row, not negative.
    _snapshot(fixture, 14, flags={'was_claimed': False, 'was_rejected': None})
    _snapshot(fixture, 15, days_old=8, flags={'was_claimed': True})
    _snapshot(fixture, 16, arm='treatment', applied=True, flags={'was_shortlisted': True, 'was_rejected': True})
    _snapshot(fixture, 17, arm='treatment', applied=None, flags={'reply_received': True})
    _read_only(fixture)
    before = [_stored(fixture, table) for table in ('vkpi_recommendation_feature_snapshot', 'vkpi_recommendation_outcomes')]
    summary = scoring.rerank_arm_summary(days=7)
    assert summary['arms']['control'] == {'snapshots': 14, 'applied': 0, 'labeled': 11, 'positives': 10, 'positive_rate': 0.9091}
    assert summary['arms']['treatment'] == {'snapshots': 2, 'applied': 1, 'labeled': 1, 'positives': 1, 'positive_rate': 1.0}
    assert summary['pending_by_arm'] == {'control': 3, 'treatment': 1}
    assert summary['label_semantics_version'] == 'operational_preference_no_transport_v1'
    _assert_no_effect_claim(summary)
    assert [_stored(fixture, table) for table in ('vkpi_recommendation_feature_snapshot', 'vkpi_recommendation_outcomes')] == before


def test_experiment_missing_outcomes_table_preserves_unknown_denominator(truth_pg):
    fixture = truth_pg
    _snapshot(fixture, 1, arm='treatment', applied=True)
    _snapshot(fixture, 2, arm='treatment', applied=False)
    fixture.conn.execute('DROP TABLE vkpi_recommendation_outcomes')  # This test owns its schema.
    _read_only(fixture)
    before = _stored(fixture, 'vkpi_recommendation_feature_snapshot')
    summary = scoring.rerank_arm_summary(days=7)
    assert summary['status'] == 'outcome_table_missing'
    assert summary['arms']['treatment'] == {'snapshots': 2, 'applied': 1, 'labeled': 0, 'positives': 0, 'positive_rate': None}
    assert summary['pending_by_arm'] == {'treatment': 2}
    _assert_no_effect_claim(summary)
    assert _stored(fixture, 'vkpi_recommendation_feature_snapshot') == before


def test_experiment_empty_window_and_missing_snapshot_do_not_invent_zero_rate(truth_pg):
    fixture = truth_pg
    _snapshot(fixture, 1, days_old=31, flags={'was_claimed': True})
    _read_only(fixture)
    summary = scoring.rerank_arm_summary(days=7)
    assert summary['status'] == 'no_snapshots_in_window'
    assert summary['arms'] == summary['pending_by_arm'] == {}
    _assert_no_effect_claim(summary)
    fixture.conn.rollback()
    fixture.conn.execute('DROP TABLE vkpi_recommendation_feature_snapshot')
    _read_only(fixture)
    missing = scoring.rerank_arm_summary()
    assert missing['status'] == 'snapshot_table_missing' and missing['arms'] == {}
    _assert_no_effect_claim(missing)
