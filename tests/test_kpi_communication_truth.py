"""Offline communication KPI contracts; no historical ledger mutation or provider I/O."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.domains.staff import decision_staff, kpi_ledger, kpi_rollup
from app.shared import vkpi_decision_common, vkpi_kpi_evidence
from app.shared.vkpi_kpi_communication_truth import (
    COMMUNICATION_METRICS, KPI_LABEL_SEMANTICS, kpi_score_eligibility,
    project_kpi_metric_summary, project_kpi_source_row,
)
from test_kpi_daily_rollup_characterization import RollupHarness


def _derived(metric="workload_score", value=4):
    return {"metric_key": metric, "metric_value": value, "confidence": "confirmed",
            "source_type": "derived_kpi", "metadata": {
                "label_semantics": KPI_LABEL_SEMANTICS,
                "components": [{"metric_key": "new_kol", "metric_value": 2,
                                "weight": 2, "contribution": 4, "source_count": 2}],
                "net_contribution_cents": 5000, "net_contribution_bonus": 0.5,
            }}


@pytest.mark.parametrize("metric", sorted(COMMUNICATION_METRICS) + ["workload_score", "kpi_credit"])
@pytest.mark.parametrize("value", [0, 8, None])
def test_old_communication_and_unexplained_derived_rows_are_unknown_and_idempotent(metric, value):
    raw = {"metric_key": metric, "metric_value": value, "confidence": "confirmed",
           "business_truth_status": "provider_verified", "metadata": {"verified": True}}
    before = deepcopy(raw)
    first = project_kpi_source_row(raw)
    assert first["metric_value"] is None
    assert first["recorded_metric_value"] == value
    assert first["recorded_confidence"] == "confirmed"
    assert first["confidence"] == first["business_truth_status"] == "unverified"
    assert first["aggregation_eligible"] is False
    assert first["communication_evidence"]["sent"] is None
    assert project_kpi_source_row(first) == first
    assert raw == before


@pytest.mark.parametrize("metric,value", [("workload_score", 4), ("kpi_credit", 4.5)])
def test_new_math_checked_derived_score_is_operational_not_provider_verified(metric, value):
    raw = _derived(metric, value)
    raw["business_truth_status"] = "provider_verified"
    raw["metadata_json"] = json.dumps(raw.pop("metadata"))
    first = project_kpi_source_row(raw)
    assert first["aggregation_eligible"] is True
    assert first["metric_value"] == value
    assert first["confidence"] == first["business_truth_status"] == "operational"
    assert first["claim_status"] == "descriptive_only"
    assert first["recorded_confidence"] == "confirmed"
    assert project_kpi_source_row(first) == first


@pytest.mark.parametrize("change", ["marker_only", "communication", "weight", "math", "unknown_metric",
                                    "duplicate", "bool_count", "missing_count", "nan", "bad_json"])
def test_marker_and_arbitrary_components_cannot_certify_derived_scores(change):
    raw = _derived()
    components = raw["metadata"]["components"]
    if change == "marker_only":
        raw["metadata"].pop("components")
    elif change == "communication":
        components[0]["metric_key"] = "stage_replied"
    elif change == "weight":
        components[0].update(weight=200, contribution=400)
        raw["metric_value"] = 400
    elif change == "math":
        components[0]["contribution"] = 3
    elif change == "unknown_metric":
        components[0]["metric_key"] = "invented_success"
    elif change == "duplicate":
        components.append(deepcopy(components[0]))
        raw["metric_value"] = 8
    elif change == "bool_count":
        components[0]["source_count"] = True
    elif change == "missing_count":
        components[0].pop("source_count")
    elif change == "nan":
        components[0]["metric_value"] = float("nan")
    else:
        raw["metadata_json"] = "{broken"
    assert kpi_score_eligibility(raw) is False


@pytest.mark.parametrize("metric", sorted(COMMUNICATION_METRICS))
def test_even_well_formed_metadata_does_not_enable_communication_points(metric):
    raw = _derived(metric)
    raw["metadata"].update(source="provider", verified=True, receipt_id="user-supplied")
    assert kpi_score_eligibility(raw) is False


@pytest.mark.parametrize("value", [0, 7, None])
def test_aggregate_without_components_preserves_raw_total_but_not_eligible_total(value):
    raw = {"metric_key": "workload_score", "total_value": value, "confidence": "confirmed"}
    result = project_kpi_metric_summary(raw)
    assert result["total_value"] is None
    assert result["recorded_total_value"] == value
    assert project_kpi_metric_summary(result) == result


def test_noncommunication_known_zero_is_not_changed():
    raw = {"metric_key": "stage_agreed", "metric_value": 0, "confidence": "confirmed"}
    assert project_kpi_source_row(raw) == raw
    assert project_kpi_metric_summary({"metric_key": "stage_agreed", "total_value": 0})["total_value"] == 0


@pytest.fixture
def ledger_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE vkpi_kpi_ledger (
            id INTEGER PRIMARY KEY, ledger_date TEXT, staff_id INTEGER, kol_id INTEGER,
            project_id INTEGER, metric_key TEXT, metric_value REAL, source_type TEXT,
            source_ref TEXT, confidence TEXT, metadata_json TEXT, created_at TEXT);
    """)
    yield conn
    conn.close()


def _upsert(conn, metric="workload_score", value=4, metadata=None):
    return kpi_ledger._upsert_entry(conn, ledger_date="2026-09-01", staff_id=7,
        metric_key=metric, metric_value=value, source_type="derived_kpi",
        source_ref="same-source", metadata=metadata)


@pytest.mark.parametrize("metric", sorted(COMMUNICATION_METRICS))
def test_communication_writer_does_not_even_query_or_write(metric):
    class NoDB:
        def execute(self, *_a):
            raise AssertionError("communication must not write or read points")
    assert _upsert(NoDB(), metric, metadata={"verified": True}) == "skipped_unverified"


def test_legacy_derived_points_are_not_debited_when_daily_rollup_is_repeated(ledger_db):
    ledger_db.execute("INSERT INTO vkpi_kpi_ledger VALUES(1,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-09-01", 7, None, None, "workload_score", 90, "derived_kpi", "same-source",
         "confirmed", '{"formula":"legacy"}', "2026-09-01"))
    before = dict(ledger_db.execute("SELECT * FROM vkpi_kpi_ledger").fetchone())
    assert _upsert(ledger_db, metadata=_derived()["metadata"]) == "skipped_unverified"
    assert dict(ledger_db.execute("SELECT * FROM vkpi_kpi_ledger").fetchone()) == before


def test_new_noncommunication_rollup_is_idempotent_and_has_operational_provenance(ledger_db):
    assert _upsert(ledger_db, metadata=_derived()["metadata"]) == "inserted"
    assert _upsert(ledger_db, metadata=_derived()["metadata"]) == "updated"
    rows = ledger_db.execute("SELECT * FROM vkpi_kpi_ledger").fetchall()
    assert len(rows) == 1
    assert project_kpi_source_row(dict(rows[0]))["metric_value_status"] == "operational"
    assert _upsert(ledger_db, "stage_agreed", 1) == "inserted"


def test_daily_rollup_keeps_manual_stages_but_never_turns_them_into_send_reply_points(monkeypatch):
    class CommunicationHarness(RollupHarness):
        def _rows(self, label):
            rows = super()._rows(label)
            if label == "stage_events":
                rows += [{"id": 91 + i, "staff_id": 7, "to_stage": stage, "event_type": "manual"}
                         for i, stage in enumerate(("contacted", "replied"))]
            return rows
    harness = CommunicationHarness()
    harness.install(monkeypatch)
    result = kpi_ledger.generate_daily_rollup("2026-09-01", staff_id=7)
    assert result["skipped_unverified"] == 2
    assert not COMMUNICATION_METRICS.intersection(result["metric_counts"])
    assert result["metric_counts"]["stage_agreed"] == 1
    assert not {"recommendation_outreach_sent", "recommendation_reply_received"}.intersection(
        label for label, _sql, _args in harness.queries)
    derived = [row for row in harness.store.values() if row["metric_key"] in {"workload_score", "kpi_credit"}]
    assert len(derived) == 2
    assert all(kpi_score_eligibility(row) for row in derived)
    assert harness.events[-3][0] == "commit"


def test_old_communication_rows_cannot_reenter_new_components_even_with_legacy_weights():
    rows = [{"staff_id": 7, "metric_key": "stage_replied", "metric_value": 50},
            {"staff_id": 7, "metric_key": "recommendation_reply_received", "metric_value": 100},
            {"staff_id": 7, "metric_key": "new_kol", "metric_value": 2}]
    deps = replace(kpi_ledger._rollup_dependencies(), ledger_source_query=lambda *_a: rows,
                   workload_weights={**kpi_ledger.WORKLOAD_WEIGHTS, "stage_replied": 2,
                                     "recommendation_reply_received": 2})
    ctx = kpi_rollup.RollupContext(deps, None, "2026-09-01", 7, "now", {}, defaultdict(int))
    scores, components = kpi_rollup._collect_staff_scores(ctx)
    assert scores[7]["workload"] == 4
    assert set(components[7]) == {"new_kol"}


def test_skipped_historical_derived_write_is_not_reported_as_a_new_metric():
    deps = replace(kpi_ledger._rollup_dependencies(), upsert_entry=lambda *_a, **_k: "skipped_unverified")
    ctx = kpi_rollup.RollupContext(deps, None, "day", 7, "now", {}, defaultdict(int))
    ctx.upsert(metric_key="workload_score")
    assert ctx.status_counts == {"skipped_unverified": 1}
    assert dict(ctx.metric_counts) == {}


def test_evidence_context_never_repromotes_raw_outcome_reply(monkeypatch):
    outcome = {"id": 8, "recommendation_id": 2, "reply_received": 1, "was_claimed": 1}
    monkeypatch.setattr(vkpi_kpi_evidence, "_row", lambda *_a: dict(outcome))
    result = vkpi_kpi_evidence.enrich_kpi_source_row(None, {
        "metric_key": "recommendation_reply_received", "metric_value": 1,
        "confidence": "confirmed", "metadata": {"outcome_id": 8}})
    observed = result["source_context"]["recommendation_outcome"]
    assert observed["reply_received"] is None
    assert observed["was_claimed"] == 1
    assert result["metric_value"] is None
    assert outcome["reply_received"] == 1


def test_list_entries_retains_staff_scope_and_unknown_projection(monkeypatch):
    raw = {"metric_key": "stage_replied", "metric_value": 0, "confidence": "confirmed"}
    queries = []
    class ReadOnly:
        def execute(self, sql, params):
            queries.append((sql, params))
            return SimpleNamespace(fetchall=lambda: [dict(raw)])
    monkeypatch.setattr(kpi_ledger, "get_conn", ReadOnly)
    monkeypatch.setattr(kpi_ledger, "ensure_vkpi_schema", lambda: None)
    result = kpi_ledger.list_entries(12, staff_id=99, staff={"id": 7, "role": "staff"})
    assert queries[0][1] == (7, 12)
    row = result["entries"][0]
    assert row["metric_value"] is None and row["recorded_metric_value"] == 0
    assert row["recorded_confidence"] == "confirmed"
    assert "未核验" in row["metric_label"]


def test_staff_breakdown_has_same_truth_for_aggregate_and_individual_row(monkeypatch):
    queries = []
    def rows(_conn, sql, params):
        queries.append(params)
        if "GROUP BY metric_key" in sql:
            return [{"metric_key": "stage_replied", "total_value": 3, "confidence": "confirmed"}]
        return [{"metric_key": "stage_replied", "metric_value": 3, "confidence": "confirmed"}]
    monkeypatch.setattr(vkpi_decision_common, "_safe_rows", rows)
    result = vkpi_decision_common._staff_kpi_breakdown(None, 7, start="START", limit=9)
    assert queries == [(7, "START"), (7, "START", 9)]
    assert result["grouped"][0]["total_value"] is None
    assert result["grouped"][0]["recorded_total_value"] == 3
    assert result["source_rows"][0]["metric_value"] is None
    assert result["source_rows"][0]["recorded_metric_value"] == 3


def test_empty_staff_profile_does_not_invent_zero_points(monkeypatch):
    monkeypatch.setattr(decision_staff, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(decision_staff, "get_conn", lambda: None)
    monkeypatch.setattr(decision_staff, "staff_directory", lambda: {"staff": []})
    monkeypatch.setattr(decision_staff, "staff_kpi", lambda *_a, **_k: {"rows": []})
    monkeypatch.setattr(decision_staff, "_safe_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(decision_staff, "_staff_kpi_breakdown", lambda *_a, **_k: {})
    result = decision_staff.staff_profile(7, staff={"id": 7, "role": "staff"})
    assert result["summary"]["workload_score"] is None
    assert result["summary"]["kpi_credit"] is None
    assert result["summary"]["operational_workload_score"] is None
    assert result["summary"]["label_semantics"] == KPI_LABEL_SEMANTICS
