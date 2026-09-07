"""PG BOOLEAN/TIMESTAMPTZ communication boundary, never a provider test."""
from __future__ import annotations

from app.domains.recommendations import outcome_sync, outcomes, rerank_fit, rerank_shadow
from test_recommendation_outcomes_pg import _outcome, _seed, scratch, truthy  # noqa: F401
import pytest

pytestmark = pytest.mark.pg


def test_record_sync_refresh_and_legacy_reads_never_claim_transport(scratch):
    conn = scratch
    conn.execute("SET statement_timeout='15000ms'")
    conn.execute("SET lock_timeout='2000ms'")
    conn.execute('INSERT INTO kols(id,channel_name,platform) VALUES(9,?,?)', ('fixture', 'youtube'))
    pool_id, rec_id = _seed(conn, handle='manual_truth', linked_main_kol_id=9)
    conn.execute('UPDATE vkpi_kol_pool SET linked_main_kol_id=9 WHERE id=?', (pool_id,))
    conn.execute('CREATE TABLE vkpi_messages(id BIGSERIAL PRIMARY KEY,project_id BIGINT,kol_id BIGINT, '
                 'direction TEXT,captured_at TIMESTAMPTZ,created_at TIMESTAMPTZ,metadata_json JSONB)')
    for direction in ('outbound', 'inbound'):
        conn.execute('INSERT INTO vkpi_messages(kol_id,direction,captured_at,created_at,metadata_json) '
                     'VALUES(9,?,?,?,?::jsonb)',
                     (direction, '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z',
                      '{"provider_verified":true,"receipt_id":"untrusted"}'))
    conn.execute('INSERT INTO vkpi_kol_pool_touches(kol_pool_id,channel,touched_at) VALUES(?,?,?)',
                 (pool_id, 'email', '2026-07-02T00:00:00Z'))
    conn.commit()
    for node in ('outreach_sent', 'reply_received'):
        assert outcomes.record(rec_id, node, context={'provider_verified': True})['recorded'] is False
        assert outcomes.record_if_missing(rec_id, node, context={'receipt_id': 'fake'}) is False
    assert _outcome(conn, rec_id) == {}
    outcomes.ensure_outcome(rec_id)
    sync = outcome_sync.sync_action_outcomes()
    assert sync['messages']['scanned'] == 2 and sync['messages']['changed'] == 0
    assert sync['touches']['scanned'] == 1 and sync['touches']['changed'] == 0
    assert not any(value.get('status') == 'failed' for value in sync.values() if isinstance(value, dict))
    raw = _outcome(conn, rec_id)
    assert not truthy(raw['outreach_sent']) and not truthy(raw['reply_received'])
    assert raw['first_action_at'] is None
    assert outcome_sync._latest_recommendation_for_pool(conn, pool_id, not_after='2026-06-01T00:00:00Z') == 0

    # History remains untouched in storage, but cannot leak through reads or training.
    conn.execute('UPDATE vkpi_recommendation_outcomes SET outreach_sent=TRUE,reply_received=TRUE, '
                 'outreach_sent_at=?,reply_at=?,first_action_at=? WHERE recommendation_id=?',
                 ('2026-07-02T00:00:00Z', '2026-07-03T00:00:00Z', '2026-07-02T00:00:00Z', rec_id))
    conn.commit()
    for result in (outcomes.get_outcome(rec_id), outcomes.refresh_business_outcome(rec_id)):
        row = result['outcome']
        assert row['outreach_sent'] is row['reply_received'] is row['first_action_at'] is None
        assert row['outreach_sent_at'] is row['reply_at'] is None
        assert row['communication_evidence']['transport_outcome_eligible'] is False
    legacy = _outcome(conn, rec_id)
    assert truthy(legacy['outreach_sent']) and truthy(legacy['reply_received'])
    assert legacy['first_action_at'] is not None
    assert rerank_shadow.write_snapshot(
        recommendation_id=rec_id, run_id=None, kol_pool_id=pool_id, launch_id=None, staff_id=None,
        engine='product_analysis', arm='off', vector={'base_score_norm': 0.5}, base_score=50,
        adjustment=0, applied=False, reason_codes=[], model_version='legacy',
    )
    conn.execute(f'UPDATE {rerank_shadow.SNAPSHOT_TABLE} SET outcome_label=1 WHERE recommendation_id=?', (rec_id,))
    conn.commit()
    assert rerank_fit._load_training_rows() == [] and rerank_fit._load_holdout_rows() == []
    assert rerank_fit.label_snapshots()['pending'] == 1
    assert rerank_fit._load_training_rows() == []

    # A genuine non-communication claim exercises the full refresh UPDATE path.
    conn.execute("INSERT INTO vkpi_kol_claims(kol_id,staff_id,claimed_at) VALUES(9,7,NOW()+INTERVAL '1 second')")
    conn.commit()
    refreshed = outcomes.refresh_business_outcome(rec_id)
    assert truthy(refreshed['outcome']['was_claimed'])
    assert refreshed['outcome']['outreach_sent'] is refreshed['outcome']['reply_received'] is None
    assert refreshed['aggregates']['outreach_sent'] is refreshed['aggregates']['reply_received'] is None
    assert refreshed['outcome']['first_action_at'] != legacy['first_action_at']
    final = _outcome(conn, rec_id)
    assert final['outreach_sent'] == legacy['outreach_sent'] and final['reply_at'] == legacy['reply_at']
