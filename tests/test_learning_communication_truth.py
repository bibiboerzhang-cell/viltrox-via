"""Read-only weekly/snapshot projections must not score legacy communication bits."""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from app.domains.learning import learning_loop, weekly_scorecard
from app.domains.agents import prediction_ledger
from app.domains.recommendations.communication_evidence import LABEL_SEMANTICS


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE vkpi_recommendation_feedback (
        id INTEGER PRIMARY KEY, recommendation_id INTEGER, feedback_type TEXT, created_at TEXT
      );
      CREATE TABLE vkpi_recommendation_outcomes (
        id INTEGER PRIMARY KEY, recommendation_id INTEGER, kol_pool_id INTEGER,
        recommended_at TEXT, first_action_at TEXT, outcome_finalized_at TEXT,
        was_shortlisted INTEGER DEFAULT 0, shortlisted_at TEXT,
        was_rejected INTEGER DEFAULT 0, rejected_at TEXT,
        was_claimed INTEGER DEFAULT 0, claimed_at TEXT, project_created INTEGER DEFAULT 0,
        outreach_sent INTEGER DEFAULT 0, outreach_sent_at TEXT,
        reply_received INTEGER DEFAULT 0, reply_at TEXT,
        agreement_reached INTEGER DEFAULT 0, agreement_at TEXT,
        content_published INTEGER DEFAULT 0, content_published_at TEXT,
        order_attributed INTEGER DEFAULT 0, first_order_at TEXT, metadata_json TEXT
      );
    """)
    monkeypatch.setattr(learning_loop, "get_conn", lambda: conn)
    monkeypatch.setattr(weekly_scorecard, "_utcnow", lambda: datetime(2026, 9, 7, tzinfo=timezone.utc))
    try:
        yield conn
    finally:
        conn.close()


def _seed(conn, **fields):
    values = {"id": 1, "recommendation_id": 71, "kol_pool_id": 11,
              "recommended_at": "2026-09-01T00:00:00Z", **fields}
    conn.execute(
        "INSERT INTO vkpi_recommendation_outcomes (" + ",".join(values) + ") VALUES ("
        + ",".join("?" for _ in values) + ")", tuple(values.values()),
    )
    conn.commit()


def _weekly(conn):
    pending = []
    result = weekly_scorecard._weekly_kol_recommend(conn, weekly_scorecard._week_axis(2), pending)
    return result, pending


@pytest.mark.parametrize("sent,replied", [(1, 0), (0, 1), (1, 1), (0, 0)])
def test_only_legacy_communication_is_pending_not_a_positive_label(db, sent, replied):
    _seed(db, outreach_sent=sent, reply_received=replied, outreach_sent_at="2026-09-01T00:00:00Z",
          reply_at="2026-09-02T00:00:00Z", metadata_json='{"verified":true,"provider_message_id":"client"}')
    before = dict(db.execute("SELECT * FROM vkpi_recommendation_outcomes").fetchone())
    group, pending = _weekly(db)
    assert group["status"] == "pending" and group["pending_count"] == 1
    assert group["judged_total_all_time"] == group["in_range_judged"] == 0
    assert group["in_range_hit_rate"] is None and len(pending) == 1
    assert group["communication_evidence"]["status"] == "unknown"
    assert group["label_semantics"] == LABEL_SEMANTICS
    assert dict(db.execute("SELECT * FROM vkpi_recommendation_outcomes").fetchone()) == before


@pytest.mark.parametrize("flag,timestamp", [
    ("was_shortlisted", "shortlisted_at"), ("was_claimed", "claimed_at"),
    ("agreement_reached", "agreement_at"), ("content_published", "content_published_at"),
    ("order_attributed", "first_order_at"),
])
def test_other_operational_nodes_remain_positive_without_using_old_reply_time(db, flag, timestamp):
    _seed(db, **{flag: 1, timestamp: "2026-09-07T00:00:00Z"},
          outreach_sent=1, reply_received=1, reply_at="2026-09-01T00:00:00Z")
    group, pending = _weekly(db)
    assert group["in_range_judged"] == group["in_range_hits"] == 1 and pending == []
    assert group["weekly"][0]["judged"] == 0
    assert group["weekly"][1]["judged"] == group["weekly"][1]["hits"] == 1


def test_legacy_reply_does_not_override_an_explicit_rejection(db):
    _seed(db, was_rejected=1, rejected_at="2026-09-07T00:00:00Z", reply_received=1)
    group, pending = _weekly(db)
    assert group["in_range_judged"] == 1 and group["in_range_hits"] == 0
    assert group["in_range_hit_rate"] == 0.0 and pending == []


@pytest.mark.parametrize("feedback,positive", [("shortlist", True), ("claim", True), ("contact", False)])
def test_manual_preferences_keep_their_existing_non_transport_label(db, feedback, positive):
    _seed(db, outreach_sent=1)
    db.execute("INSERT INTO vkpi_recommendation_feedback VALUES (1,71,?,'2026-09-07T00:00:00Z')", (feedback,))
    group, pending = _weekly(db)
    assert group["in_range_hits"] == int(positive)
    assert group["pending_count"] == int(not positive)
    assert len(pending) == int(not positive)


@pytest.mark.parametrize("legacy_value", [0, 1, None])
def test_snapshot_communication_counts_are_unknown_not_sums_or_zero(db, legacy_value):
    _seed(db, was_shortlisted=1, was_claimed=1, project_created=1,
          outreach_sent=legacy_value, reply_received=legacy_value)
    counts = learning_loop._outcome_counts()
    assert counts["total"] == counts["shortlisted"] == counts["claimed"] == counts["project_created"] == 1
    assert counts["rejected"] == counts["content_published"] == counts["order_attributed"] == 0
    assert counts["outreach_sent"] is counts["reply_received"] is None
    assert counts["communication_evidence"]["status"] == "unknown"
    assert counts["label_semantics"] == LABEL_SEMANTICS


def test_empty_snapshot_does_not_claim_confirmed_zero_transfers(db):
    counts = learning_loop._outcome_counts()
    assert counts["total"] == 0
    assert counts["outreach_sent"] is counts["reply_received"] is None


def test_unavailable_snapshot_still_reports_unknown_communication(db):
    db.execute("DROP TABLE vkpi_recommendation_outcomes")
    counts = learning_loop._outcome_counts()
    assert counts["outreach_sent"] is counts["reply_received"] is None
    assert counts["communication_evidence"]["status"] == "unknown"
    assert "total" not in counts


def test_formatted_snapshot_exposes_unknown_without_zero_coercion(db, monkeypatch):
    _seed(db, outreach_sent=1, reply_received=1)
    monkeypatch.setattr(learning_loop, "_count", lambda _: 0)
    monkeypatch.setattr(learning_loop, "_group_counts", lambda *_: {})
    result = learning_loop.build_learning_snapshot()
    assert result["provider_calls"] is result["write_db"] is False
    assert "recommendation_outcomes.outreach_sent=unknown" in result["markdown"]
    assert "recommendation_outcomes.reply_received=unknown" in result["markdown"]
    assert "label_semantics=" + LABEL_SEMANTICS in result["markdown"]
    assert result["recommendation_outcomes"]["communication_evidence"]["sent"] is None


@pytest.mark.parametrize("sent,replied", [(1, 0), (0, 1), (1, 1), (0, 0)])
def test_prediction_ledger_cannot_adopt_raw_communication_as_hit(db, sent, replied):
    _seed(db, outreach_sent=sent, reply_received=replied)
    group = prediction_ledger._collect_kol_recommend(db, window=20)
    assert group["status"] == "pending" and group["sample_count"] == 0
    assert group["hit_rate"] is None and group["basis"]["pending_count"] == 1
    assert group["label_semantics"] == LABEL_SEMANTICS
    assert group["communication_evidence"]["sent"] is group["communication_evidence"]["replied"] is None


@pytest.mark.parametrize("flag,hit", [("was_shortlisted", 1.0), ("was_claimed", 1.0), ("was_rejected", 0.0)])
def test_prediction_ledger_keeps_operator_outcomes_despite_legacy_reply(db, flag, hit):
    _seed(db, **{flag: 1}, outreach_sent=1, reply_received=1)
    group = prediction_ledger._collect_kol_recommend(db, window=20)
    assert group["sample_count"] == 1 and group["hit_rate"] == hit
    assert group["basis"]["pending_count"] == 0
