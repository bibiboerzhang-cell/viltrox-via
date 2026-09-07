"""Hermetic communication truth: records are not transport receipts."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

import pytest

from app.domains.recommendations import outcome_sync, outcomes, rerank_fit, rerank_shadow, training_export
from app.domains.recommendations.communication_evidence import LABEL_SEMANTICS_VERSION, project_outcome_communications
from test_recommendation_outcomes_learning import learning_db, _seed_recommendation


@pytest.mark.parametrize("node", ["outreach_sent", "reply_received"])
def test_untrusted_context_cannot_record_transport_without_any_database_write(monkeypatch, node):
    monkeypatch.setattr(outcomes, "ensure_vkpi_product_industry_schema", lambda: pytest.fail("no schema writes"))
    monkeypatch.setattr(outcomes, "get_conn", lambda: pytest.fail("no record writes"))
    fake = {"provider_verified": True, "source": "provider", "receipt_id": 999}
    assert outcomes.record_if_missing(1, node, context=fake) is False
    result = outcomes.record(1, node, context=fake)
    assert result["recorded"] is False
    assert result["communication_evidence"]["sent"] is None


@pytest.mark.parametrize("raw", [
    {"outreach_sent": True}, {"reply_received": True},
    {"outreach_sent": True, "reply_received": False}, {}, None,
])
def test_legacy_communication_or_silence_never_labels_transport_success_or_failure(raw):
    assert rerank_fit.label_for_outcome(
        raw, recommended_at="2025-01-01T00:00:00Z", now=datetime(2026, 9, 7, tzinfo=timezone.utc),
    ) == (None, [])


def test_refresh_cannot_repromote_raw_message_dates():
    context = {"kol_id": 7, "linked_kol_source": "existing"}
    projects = {"ids": [1], "rows": [], "stage_map": {}, "first_project": None}
    evidence = {"message": {"first_message_at": "2026-01-01", "first_outbound_at": "2026-01-01",
                            "first_inbound_at": "2026-01-02"},
                "agreement": None, "content": None, "click": None, "sales": None, "cost": None}
    result = outcomes._summarize_refresh(context, projects, evidence, None)
    assert result["first_outreach"] is None
    assert result["first_reply"] is None
    assert result["aggregates"]["outreach_sent"] is None
    assert result["aggregates"]["reply_received"] is None


def test_training_export_masks_old_communication_bits_even_if_finalized():
    result = training_export._dataset_record(
        {"outreach_sent": True, "reply_received": False, "outcome_finalized_at": "2026-01-01"},
        {}, {"recommendation_id": 1, "launch_id": None, "kol_pool_id": 7, "as_of": "2025-12-01"},
    )
    assert result["outcome"]["outreach_sent"] is None
    assert result["outcome"]["reply_received"] is None
    assert result["label_semantics"] == "operational_preference_not_transport_success"
    assert result["training_label"]["eligible"] is False
    assert result["training_label"]["value"] is None


@pytest.mark.parametrize("raw, expected", [
    ({"reply_received": True}, None), ({}, None),
    ({"was_shortlisted": True, "outreach_sent": True}, 1),
    ({"was_rejected": True, "reply_received": False}, 0),
    ({"order_attributed": True}, 1),
])
def test_export_operational_label_never_uses_transport_or_silence(raw, expected):
    record = training_export._dataset_record(
        raw, {}, {"recommendation_id": 1, "launch_id": None, "kol_pool_id": 7, "as_of": "2025-01-01"},
    )
    assert record["training_label"]["value"] == expected
    assert record["training_label"]["eligible"] is (expected is not None)
    assert record["training_label"]["label_semantics_version"] == LABEL_SEMANTICS_VERSION


def test_legacy_read_and_refresh_early_return_mask_transport_without_rewriting_history(learning_db):
    conn = learning_db
    rec_id = _seed_recommendation(conn, kol_pool_id=70)
    outcomes.ensure_outcome(rec_id)
    conn.execute(
        "UPDATE vkpi_recommendation_outcomes SET outreach_sent=1, reply_received=1, "
        "outreach_sent_at=?, reply_at=?, first_action_at=? WHERE recommendation_id=?",
        ("2026-01-01", "2026-01-02", "2026-01-01", rec_id),
    )
    conn.commit()
    for result in (outcomes.get_outcome(rec_id), outcomes.refresh_business_outcome(rec_id)):
        row = result["outcome"]
        assert row["outreach_sent"] is None and row["reply_received"] is None
        assert row["first_action_at"] is None and row["outreach_sent_at"] is None
        assert row["communication_evidence"]["transport_outcome_eligible"] is False
    stored = dict(conn.execute("SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,)).fetchone())
    assert stored["outreach_sent"] == 1 and stored["reply_received"] == 1
    assert stored["first_action_at"] == "2026-01-01"  # Projection only; historical rows are not silently rewritten.


@pytest.mark.parametrize("flag, stamp, expected", [
    (True, "2026-01-03T00:00:00Z", "2026-01-03T00:00:00Z"),
    ("t", "2026-01-03", "2026-01-03"), (False, "2026-01-03", None),
    (True, "invalid-time", None), (True, None, None),
])
def test_first_action_uses_noncommunication_flag_and_valid_time_only(flag, stamp, expected):
    raw = {"outreach_sent": True, "first_action_at": "2026-01-01",
           "was_claimed": flag, "claimed_at": stamp}
    assert project_outcome_communications(raw)["first_action_at"] == expected
    assert raw["first_action_at"] == "2026-01-01"


@pytest.mark.parametrize("direction", ["inbound", "outbound", "reply", "unknown"])
def test_daily_message_and_touch_sync_do_not_manufacture_transport(learning_db, direction):
    conn = learning_db
    rec_id = _seed_recommendation(conn, kol_pool_id=71, created_at="2026-01-01T00:00:00Z")
    outcomes.ensure_outcome(rec_id)
    conn.execute("UPDATE vkpi_kol_pool SET linked_main_kol_id=700 WHERE id=71")
    conn.execute("INSERT INTO vkpi_messages(kol_id, direction, captured_at, created_at) VALUES (?,?,?,?)",
                 (700, direction, "2026-02-01T00:00:00Z", "2026-02-01T00:00:00Z"))
    conn.execute("INSERT INTO vkpi_kol_pool_touches(kol_pool_id,touched_at,created_at) VALUES (?,?,?)",
                 (71, "2026-02-01T00:00:00Z", "2026-02-01T00:00:00Z"))
    conn.commit()
    for sync in (outcome_sync.sync_message_outcomes, outcome_sync.sync_touch_outcomes):
        result = sync()
        assert result["scanned"] == 1 and result["changed"] == 0
        assert result["no_recommendation"] == 0
    raw = dict(conn.execute("SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,)).fetchone())
    assert not raw["outreach_sent"] and not raw["reply_received"] and raw["first_action_at"] is None


def test_past_event_never_falls_back_to_future_recommendation(learning_db):
    rec_id = _seed_recommendation(learning_db, kol_pool_id=72, created_at="2026-03-01T00:00:00Z")
    assert outcome_sync._latest_recommendation_for_pool(learning_db, 72, not_after="2026-02-01T00:00:00Z") == 0
    assert outcome_sync._latest_recommendation_for_pool(learning_db, 72, not_after="2026-03-01T00:00:00Z") == rec_id
    assert outcome_sync._latest_recommendation_for_pool(learning_db, 72) == rec_id


def test_project_json_prefix_and_explicit_mismatch_cannot_steal_attribution():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE vkpi_projects(id INTEGER, stage TEXT, stage_status TEXT, created_at TEXT, "
                 "updated_at TEXT, metadata_json TEXT, kol_id INTEGER, source_type TEXT, product_sku TEXT)")
    fixtures = [{"recommendation_id": 1}, {"recommendation_id": 12}, {"recommendation_id": "1"},
                {"recommendation_id": True}, {}, {"nested": {"recommendation_id": 1}}, []]
    try:
        for index, metadata in enumerate(fixtures, 1):
            conn.execute("INSERT INTO vkpi_projects VALUES(?, 'agreed','active','2026-02-01','2026-02-02',?,7,'product_recommendation','SKU')",
                         (index, json.dumps(metadata)))
        context = {"rec_id": 1, "kol_id": 7, "launch_sku": "SKU", "recommended_at": "2026-01-01"}
        linked = outcomes._load_refresh_projects(conn, context)
        assert linked["ids"] == [1, 5, 6]  # Missing top-level id retains the existing explicit KOL/SKU fallback.
        assert outcomes._load_refresh_projects(conn, {**context, "kol_id": 0})["ids"] == [1]
    finally:
        conn.close()


@pytest.mark.parametrize("old_label, flags, expected", [
    (1, {"outreach_sent": 1}, None), (1, {"reply_received": 1}, None),
    (0, {"outreach_sent": 1}, None), (0, {}, None),
    (0, {"was_claimed": 1}, 1), (None, {"was_shortlisted": 1}, 1),
    (1, {"was_rejected": 1}, 0),
])
def test_training_and_holdout_recompute_eligibility_not_old_labels(learning_db, old_label, flags, expected):
    conn = learning_db
    rec_id = _seed_recommendation(conn, kol_pool_id=73)
    outcomes.ensure_outcome(rec_id)
    for key, value in flags.items():
        conn.execute(f"UPDATE vkpi_recommendation_outcomes SET {key}=? WHERE recommendation_id=?", (value, rec_id))
    assert rerank_shadow.write_snapshot(
        recommendation_id=rec_id, run_id=None, kol_pool_id=73, launch_id=None, staff_id=None,
        engine="product_analysis", arm="off", vector={"base_score_norm": 0.5},
        base_score=50, adjustment=0, applied=False, reason_codes=[], model_version="legacy",
    )
    conn.execute(f"UPDATE {rerank_shadow.SNAPSHOT_TABLE} SET outcome_label=? WHERE recommendation_id=?", (old_label, rec_id))
    conn.commit()
    for reader in (rerank_fit._load_training_rows, rerank_fit._load_holdout_rows):
        rows = reader()
        assert [row["label"] for row in rows] == ([] if expected is None else [expected])
    stored = conn.execute(f"SELECT outcome_label FROM {rerank_shadow.SNAPSHOT_TABLE} WHERE recommendation_id=?", (rec_id,)).fetchone()
    assert stored["outcome_label"] == old_label
    if expected is None:
        result = rerank_fit.label_snapshots()
        assert result["pending"] == 1 and result["labeled"] == 0
        assert rerank_fit._load_training_rows() == []  # Unknown continue cannot revive a persisted legacy label.


@pytest.mark.parametrize("metrics, accepted", [
    ({}, False), ({"label_semantics_version": "legacy"}, False), ([], False),
    ({"label_semantics_version": LABEL_SEMANTICS_VERSION}, True),
])
def test_model_reader_quarantines_legacy_weights_without_rewriting_history(learning_db, metrics, accepted):
    assert rerank_shadow.tables_ready()
    result = rerank_fit._store_model(
        version="synthetic-fixture", sample_count=60, positive_count=30, negative_count=30,
        activated=True, rule="fixture-only", weights={"coef": {"base_score_norm": 1}},
        metrics={"label_semantics_version": "client-forgery"}, reason_codes=[],
    )
    assert result["metrics"]["label_semantics_version"] == LABEL_SEMANTICS_VERSION
    learning_db.execute(f"UPDATE {rerank_shadow.MODEL_TABLE} SET metrics=?", (json.dumps(metrics),))
    learning_db.commit()
    model = rerank_shadow.load_active_model()
    assert (model is not None) is accepted
    if not accepted:
        assert rerank_shadow.adjustment_for(model, {"base_score_norm": 1}) == (0.0, [])
    row = dict(learning_db.execute(f"SELECT * FROM {rerank_shadow.MODEL_TABLE}").fetchone())
    assert row["activated"] == 1 and json.loads(row["metrics"]) == metrics
