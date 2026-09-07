"""Operator feedback is not transport evidence; hermetic in-memory regression."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.recommendations import actions, pool_action_bridge


@pytest.fixture
def feedback_store(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE vkpi_kol_recommendations (id INTEGER PRIMARY KEY, kol_pool_id INTEGER);
        CREATE TABLE vkpi_recommendation_feedback (
            id INTEGER PRIMARY KEY, recommendation_id INTEGER, feedback_type TEXT,
            note TEXT, created_by_staff_id INTEGER, created_at TEXT, metadata_json TEXT
        );
        INSERT INTO vkpi_kol_recommendations VALUES (71, 11);
    """)
    monkeypatch.setattr(actions, "get_conn", lambda: conn)
    monkeypatch.setattr(pool_action_bridge, "get_conn", lambda: conn)
    outcomes = []
    monkeypatch.setattr(
        actions.outcome_collector, "record_if_missing",
        lambda rec_id, node, **kwargs: outcomes.append((rec_id, node, kwargs)) or True,
    )
    try:
        yield conn, outcomes
    finally:
        conn.close()


@pytest.mark.parametrize("action", ["contact", "touch", "outreach", " CONTACT "])
@pytest.mark.parametrize("payload", [
    {},
    {"source": "provider", "direction": "outbound", "verified": True,
     "outreach_sent": True, "reply_received": True, "provider_message_id": "untrusted-client-value"},
])
def test_operator_contact_feedback_persists_without_transport_fact(feedback_store, action, payload):
    conn, outcomes = feedback_store
    first = actions.record_pool_action_feedback(11, action, staff={"id": 7}, note="manual contact", payload=payload)
    repeated = actions.record_pool_action_feedback(11, action, staff={"id": 7}, note="manual contact", payload=payload)
    rows = conn.execute("SELECT * FROM vkpi_recommendation_feedback").fetchall()
    assert len(rows) == 1
    assert dict(rows[0]) | {"metadata_json": None, "created_at": None} == {
        "id": 1, "recommendation_id": 71, "feedback_type": "contact", "note": "manual contact",
        "created_by_staff_id": 7, "created_at": None, "metadata_json": None,
    }
    assert json.loads(rows[0]["metadata_json"]) == {
        "source_action": "contact", "kol_pool_id": 11, "pool_action": action.strip().lower(), **payload,
    }
    assert first["linked"] is True and first["feedback_inserted"] is True
    assert repeated["linked"] is True and repeated["feedback_inserted"] is False
    assert first["outcome_node"] == repeated["outcome_node"] == ""
    assert first["outcome_changed"] is repeated["outcome_changed"] is False
    assert outcomes == []
    assert conn.in_transaction is False


@pytest.mark.parametrize("action,feedback_type,outcome_node", [
    ("favorite", "shortlist", "shortlisted"), ("shortlist", "shortlist", "shortlisted"),
    ("promote", "claim", "claimed"), ("claim", "claim", "claimed"),
    ("reject", "reject", "rejected"), ("snooze", "snooze", ""),
    ("unfavorite", "snooze", ""),
])
def test_non_transport_preference_semantics_are_unchanged(feedback_store, action, feedback_type, outcome_node):
    conn, outcomes = feedback_store
    result = pool_action_bridge.bridge_pool_action(11, action, staff={"id": 7}, source="unit")
    assert result["linked"] is True and result["feedback_inserted"] is True
    assert result["feedback_type"] == feedback_type
    assert result["outcome_node"] == outcome_node
    assert [(rec_id, node) for rec_id, node, _ in outcomes] == ([(71, outcome_node)] if outcome_node else [])
    assert conn.execute("SELECT COUNT(*) FROM vkpi_recommendation_feedback").fetchone()[0] == 1


@pytest.mark.parametrize("action", ["contact", "touch", "outreach"])
def test_real_operator_bridge_keeps_feedback_but_not_transport_outcomes(feedback_store, action):
    conn, outcomes = feedback_store
    result = pool_action_bridge.bridge_pool_action(
        11, action, staff={"id": 7}, source="project_assignment",
        payload={"project_id": 3, "channel": "project_assignment", "verified": True},
    )
    assert result["feedback_type"] == "contact"
    assert result["outcome_node"] == "" and result["outcome_changed"] is False
    assert conn.execute("SELECT feedback_type FROM vkpi_recommendation_feedback").fetchone()[0] == "contact"
    assert outcomes == []
