"""Synthetic read-model regressions; never read or clean a business database."""
from __future__ import annotations

import sqlite3

import pytest

from app.domains.recommendations import evidence, feedback_backlog


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE vkpi_kol_recommendations (
        id INTEGER PRIMARY KEY, launch_id INTEGER, run_id INTEGER, kol_pool_id INTEGER,
        linked_main_kol_id INTEGER, platform TEXT, handle TEXT, display_name TEXT,
        rank INTEGER, score REAL, status TEXT, created_at TEXT);
      CREATE TABLE vkpi_recommendation_outcomes (
        id INTEGER PRIMARY KEY, recommendation_id INTEGER, was_shortlisted INTEGER,
        was_rejected INTEGER, was_claimed INTEGER, project_created INTEGER,
        outreach_sent INTEGER, reply_received INTEGER, agreement_reached INTEGER,
        content_published INTEGER, order_attributed INTEGER, attributed_clicks INTEGER,
        attributed_orders INTEGER, attributed_gmv_cents INTEGER, attributed_cost_cents INTEGER,
        computed_roi REAL, first_action_at TEXT, outcome_finalized_at TEXT, model_version TEXT);
      INSERT INTO vkpi_kol_recommendations(id,run_id,platform,created_at) VALUES(1,7,'youtube','2026-01-01');
      INSERT INTO vkpi_recommendation_outcomes(id,recommendation_id,was_shortlisted,outreach_sent,reply_received,first_action_at)
        VALUES(11,1,1,1,1,'2026-01-02');
    """)
    monkeypatch.setattr(evidence, "get_conn", lambda: conn)
    monkeypatch.setattr(evidence, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(evidence, "_postgres_selected", lambda: False)
    yield conn
    conn.close()


@pytest.mark.parametrize("run_id", [None, 7, 999])
def test_summary_never_counts_raw_transport_bits_even_for_empty_selection(db, run_id):
    before = tuple(db.execute("SELECT * FROM vkpi_recommendation_outcomes").fetchone())
    result = evidence.recommendation_outcome_summary(run_id=run_id)
    for key in ("outreach_sent", "reply_received"):
        assert result["totals"][key] is None
        assert result["conversion"][key] is None
    assert result["communication_evidence"]["status"] == "unknown"
    if run_id != 999:
        assert result["totals"]["shortlisted"] == 1
        assert result["source_rows"][0]["reply_received"] is None
        assert result["source_rows"][0]["first_action_at"] is None
    assert tuple(db.execute("SELECT * FROM vkpi_recommendation_outcomes").fetchone()) == before


@pytest.mark.parametrize("legacy", [True, False, None])
def test_backlog_does_not_propose_positive_feedback_for_legacy_transport(legacy):
    row = {"outreach_sent": legacy, "reply_received": legacy,
           "first_action_at": "2026-01-02", "recommendation_status": "recommended"}
    result = feedback_backlog._outcome_status(row)
    assert result["flags"] == [] and result["has_outcome"] is False
    assert result["first_action_at"] == ""
    assert result["communication_evidence"]["sent"] is None
    assert feedback_backlog._suggestion(row, result)["suggested_action"] == "needs_human_review"


def test_backlog_keeps_non_transport_operational_preference():
    row = {"was_shortlisted": 1, "outreach_sent": 1}
    result = feedback_backlog._outcome_status(row)
    assert result["flags"] == ["shortlisted"]
    assert feedback_backlog._suggestion(row, result)["suggested_action"] == "capture_shortlist_feedback"


def test_evidence_messages_project_manual_truth_and_exclude_explicit_other_recommendation(monkeypatch):
    def rows(sql, params=()):
        if "FROM vkpi_projects" in sql:
            return [
                {"id": 71, "kol_id": 9, "metadata_json": '{"recommendation_id":1}'},
                {"id": 72, "kol_id": 9, "metadata_json": '{"recommendation_id":12}'},
                {"id": 73, "kol_id": 9, "metadata_json": '{"recommendation_id":true}'},
            ]
        if "FROM vkpi_messages" in sql:
            assert params == (71,)
            return [{"id": 41, "direction": "outbound", "source": "provider",
                     "communication_truth": {"sent": True}, "body": "synthetic"}]
        return []
    monkeypatch.setattr(evidence, "_safe_rows", rows)
    result = evidence._project_evidence_rows(1, 9)
    assert [row["id"] for row in result["projects"]] == [71]
    assert result["messages"][0]["body"] == "synthetic"
    assert result["messages"][0]["communication_truth"]["sent"] is None


def test_explicit_recommendation_mismatch_never_uses_same_author_fallback():
    assert not evidence._matches_evidence_project({"kol_id": 9, "metadata_json": '{"recommendation_id":12}'}, 1, 9)
    assert evidence._matches_evidence_project({"kol_id": 9, "metadata_json": '{}'}, 1, 9)


@pytest.mark.parametrize("is_manager", [True, False])
def test_profile_and_timeline_project_manual_communications_without_mutating_source(monkeypatch, is_manager):
    from app.domains.kol import profile_detail

    raw_message = {"id": 1, "body": "synthetic", "direction": "inbound", "source": "provider",
                   "communication_truth": {"sent": True, "replied": True}}
    raw_outcome = {"id": 2, "outreach_sent": 1, "reply_received": 1,
                   "first_action_at": "2026-01-02", "recommended_at": "2026-01-01"}
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE kols(id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO kols VALUES(9)")
    monkeypatch.setattr(profile_detail, "get_conn", lambda: conn)
    monkeypatch.setattr(profile_detail, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(profile_detail, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(profile_detail.claim_access, "assert_kol_access", lambda *a, **k: None)
    monkeypatch.setattr(profile_detail.scope, "can_view_all", lambda *a, **k: is_manager)
    monkeypatch.setattr(profile_detail.scope, "actor_staff_id", lambda *a: 3)
    monkeypatch.setattr(profile_detail.profile_scope, "project_staff_filter", lambda *a: ("", []))
    monkeypatch.setattr(profile_detail, "_row_or_empty", lambda *a, **k: {})

    def rows(sql, params=()):
        if "FROM vkpi_messages" in sql:
            return [raw_message]
        if "FROM vkpi_recommendation_outcomes" in sql:
            return [raw_outcome]
        return []

    monkeypatch.setattr(profile_detail, "_rows_or_empty", rows)
    try:
        result = profile_detail.profile(9, staff={"id": 3})
    finally:
        conn.close()
    message = result["messages"][0]
    assert message["communication_truth"]["sent"] is None
    assert message["communication_truth"]["replied"] is None
    timeline_message = next(item["data"] for item in result["activity_timeline"] if item["type"] == "message")
    assert timeline_message["communication_truth"] == message["communication_truth"]
    if is_manager:
        assert result["recommendation_outcomes"][0]["reply_received"] is None
        timeline_outcome = next(item["data"] for item in result["activity_timeline"] if item["type"] == "recommendation_outcome")
        assert timeline_outcome["outreach_sent"] is None
        assert timeline_outcome["first_action_at"] is None
    else:
        assert result["recommendation_outcomes"] == []
    assert raw_message["communication_truth"]["replied"] is True
    assert raw_outcome["reply_received"] == 1
    assert raw_outcome["first_action_at"] == "2026-01-02"
