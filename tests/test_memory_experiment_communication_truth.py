"""Read-only, synthetic SQLite contracts; no provider or business database."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.experiments import scoring
from app.domains.memory import provenance


@pytest.fixture()
def evidence_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE vkpi_recommendation_feature_snapshot (
            id INTEGER PRIMARY KEY, recommendation_id INTEGER, arm TEXT,
            rerank_applied BOOLEAN, outcome_label INTEGER, created_at TEXT
        );
        CREATE TABLE vkpi_recommendation_outcomes (
            recommendation_id INTEGER PRIMARY KEY, kol_pool_id INTEGER,
            was_shortlisted BOOLEAN, was_claimed BOOLEAN, was_rejected BOOLEAN,
            project_created BOOLEAN, outreach_sent BOOLEAN, reply_received BOOLEAN,
            content_published BOOLEAN, agreement_reached BOOLEAN, order_attributed BOOLEAN
        );
    """)

    def exists(name):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    monkeypatch.setattr(scoring, "get_conn", lambda: conn)
    monkeypatch.setattr(provenance, "get_conn", lambda: conn)
    monkeypatch.setattr(provenance, "table_exists", exists)
    monkeypatch.setattr("app.db.connection.table_exists", exists)
    from app.domains.kol import roi_aggregate

    monkeypatch.setattr(roi_aggregate, "get_kol_roi_summary", lambda *a, **k: {"status": "no_projects", "total_projects": 0})
    yield conn
    conn.close()


def _seed(conn, rec_id, *, old_label=None, arm="control", **flags):
    conn.execute("INSERT INTO vkpi_recommendation_feature_snapshot VALUES(?,?,?,?,?,?)",
                 (rec_id, rec_id, arm, arm == "treatment", old_label, scoring._utcnow()))
    if flags:
        keys = list(flags)
        conn.execute(f"INSERT INTO vkpi_recommendation_outcomes (recommendation_id, kol_pool_id, {','.join(keys)}) "
                     f"VALUES (?,?,{','.join('?' for _ in keys)})", (rec_id, 7, *flags.values()))
    conn.commit()


def test_memory_does_not_cite_legacy_sent_as_a_transport_fact(evidence_db):
    _seed(evidence_db, 1, old_label=1, outreach_sent=True, reply_received=True, was_claimed=True)
    _seed(evidence_db, 2, old_label=0, outreach_sent=True, reply_received=False)
    before = evidence_db.total_changes
    result = provenance.get_kol_provenance(7)
    counts = result["provenance"]["outcomes"]
    assert counts["outreach_sent"] is None and counts["reply_received"] is None
    assert counts["records"] == 2 and counts["claimed"] == 1
    assert counts["communication_evidence"]["status"] == "unknown"
    assert counts["communication_evidence"]["transport_outcome_eligible"] is False
    assert not any(citation["type"] in {"sent", "reply", "outreach"} for citation in result["citations"])
    assert evidence_db.total_changes == before


@pytest.mark.parametrize("old_label", [None, 0, 1])
def test_arm_summary_cannot_promote_old_transport_labels_or_silence(evidence_db, old_label):
    _seed(evidence_db, 1, old_label=old_label, outreach_sent=True, reply_received=False)
    _seed(evidence_db, 2, old_label=old_label, reply_received=True)
    _seed(evidence_db, 3, old_label=old_label)  # Missing outcome is unknown, never a negative example.
    before = evidence_db.total_changes
    summary = scoring.rerank_arm_summary()
    assert summary["arms"]["control"] == {
        "snapshots": 3, "applied": 0, "labeled": 0, "positives": 0, "positive_rate": None,
    }
    assert summary["pending_by_arm"]["control"] == 3
    assert summary["claim_status"] == "descriptive_only"
    assert summary["algorithm_effect_status"] == "not_evaluated"
    assert evidence_db.total_changes == before


@pytest.mark.parametrize("old_label", [None, 0, 1])
@pytest.mark.parametrize("flag, positive", [
    ("was_shortlisted", 1), ("was_claimed", 1), ("project_created", 1),
    ("agreement_reached", 1), ("content_published", 1), ("order_attributed", 1),
    ("was_rejected", 0),
])
def test_current_noncommunication_preferences_survive_stale_labels(evidence_db, old_label, flag, positive):
    _seed(evidence_db, 1, arm="treatment", old_label=old_label, **{flag: True})
    _seed(evidence_db, 2, arm="treatment", old_label=1, reply_received=True)
    before = evidence_db.total_changes
    summary = scoring.rerank_arm_summary()
    assert summary["arms"]["treatment"] == {
        "snapshots": 2, "applied": 2, "labeled": 1, "positives": positive, "positive_rate": float(positive),
    }
    assert summary["pending_by_arm"]["treatment"] == 1
    assert summary["label_semantics"] == "operational_preference_not_transport_success"
    assert summary["label_semantics_version"] == "operational_preference_no_transport_v1"
    assert evidence_db.total_changes == before
    stored = evidence_db.execute("SELECT outcome_label FROM vkpi_recommendation_feature_snapshot WHERE id=1").fetchone()
    assert stored["outcome_label"] == old_label


def test_grouped_sql_counts_duplicate_flags_and_time_window_without_old_label(evidence_db):
    for index in range(1, 11):
        _seed(evidence_db, index, old_label=0, was_claimed=True)
    _seed(evidence_db, 11, old_label=1, was_rejected=True)
    _seed(evidence_db, 12, old_label=1)
    _seed(evidence_db, 13, old_label=1, was_claimed=True)
    evidence_db.execute("UPDATE vkpi_recommendation_feature_snapshot SET created_at='2000-01-01T00:00:00Z' WHERE id=13")
    evidence_db.commit()
    statements = []
    evidence_db.set_trace_callback(statements.append)
    summary = scoring.rerank_arm_summary(days=7)
    assert summary["arms"]["control"] == {
        "snapshots": 12, "applied": 0, "labeled": 11, "positives": 10, "positive_rate": 0.9091,
    }
    assert summary["pending_by_arm"]["control"] == 1
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert "outcome_label" not in " ".join(statements)
    assert "GROUP BY s.arm, o.was_shortlisted" in " ".join(statements)


@pytest.mark.parametrize("flag", [False, 0, "f", "false", None])
def test_memory_and_summary_do_not_convert_false_operational_values_to_positive(evidence_db, flag):
    _seed(evidence_db, 1, old_label=1, was_claimed=flag, outreach_sent=True)
    assert provenance.get_kol_provenance(7)["provenance"]["outcomes"]["claimed"] == 0
    assert scoring.rerank_arm_summary()["arms"]["control"]["positive_rate"] is None


def test_missing_outcome_table_keeps_snapshot_counts_and_marks_labels_pending(evidence_db):
    _seed(evidence_db, 1, old_label=1, arm="treatment")
    evidence_db.execute("DROP TABLE vkpi_recommendation_outcomes")  # Test-owned in-memory table only.
    before = evidence_db.total_changes
    summary = scoring.rerank_arm_summary()
    assert summary["status"] == "outcome_table_missing"
    assert summary["arms"]["treatment"]["snapshots"] == 1
    assert summary["arms"]["treatment"]["applied"] == 1
    assert summary["arms"]["treatment"]["labeled"] == 0
    assert summary["arms"]["treatment"]["positive_rate"] is None
    assert summary["pending_by_arm"]["treatment"] == 1
    assert evidence_db.total_changes == before


def test_empty_and_missing_snapshot_return_no_rate_or_effect_claim(evidence_db):
    result = scoring.rerank_arm_summary()
    assert result["status"] == "no_snapshots_in_window"
    assert result["arms"] == {} and result["pending_by_arm"] == {}
    evidence_db.execute("DROP TABLE vkpi_recommendation_feature_snapshot")
    missing = scoring.rerank_arm_summary()
    assert missing["status"] == "snapshot_table_missing"
    assert missing["algorithm_effect_status"] == "not_evaluated"
    assert missing["communication_evidence"]["sent"] is None


def test_http_projection_and_compat_service_preserve_explained_null(evidence_db):
    from fastapi.encoders import jsonable_encoder
    from app.api.routers import vkpi_agents
    from app.services.vkpi import ab_experiments

    _seed(evidence_db, 1, old_label=1, outreach_sent=True)
    response = vkpi_agents.kol_provenance(7, staff={"id": 9, "role": "manager"})
    body = json.loads(json.dumps(jsonable_encoder(response)))
    counts = body["provenance"]["outcomes"]
    assert counts["outreach_sent"] is None and counts["reply_received"] is None
    assert counts["communication_evidence"]["reason"] == "provider_receipt_unavailable"
    summary = json.loads(json.dumps(ab_experiments.rerank_arm_summary()))
    assert summary["arms"]["control"]["positive_rate"] is None
    assert summary["pending_by_arm"]["control"] == 1
    assert summary["provider_calls"] is False and summary["provider_calls_scope"] == "this_read_only_summary"
    assert summary["write_db"] is False
