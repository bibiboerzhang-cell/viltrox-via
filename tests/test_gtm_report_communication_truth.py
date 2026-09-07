"""GTM/manual records and generated KPI report regressions, entirely offline."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.api.routers import vkpi_gtm_weights
from app.db import connection
from app.domains.market_brain import data_readiness, gtm_windows, verdict_flow
from app.domains.market_brain import weight_feedback
from app.domains.market_brain.communication_projection import (
    COMMUNICATION_FIELDS, project_communication_metrics, project_public_7d_window,
)
from app.domains.reports import pdf_renderer, report_appendices
from app.shared.vkpi_kpi_communication_truth import KPI_LABEL_SEMANTICS


@pytest.mark.parametrize("value", [True, False, 0, 4, None])
def test_legacy_window_numbers_remain_auditable_but_cannot_prove_transport(value):
    original = {key: value for key in COMMUNICATION_FIELDS}
    original.update(reply_outcome=1, reply_outcome_bridge_id=51, sample_shipped_n=2,
                    communication_evidence={"status": "verified", "sent": True})
    before = deepcopy(original)
    result = project_communication_metrics(original)
    assert all(result[key] is None for key in COMMUNICATION_FIELDS)
    assert result["recorded_communication_metrics"] == {key: value for key in COMMUNICATION_FIELDS}
    assert result["communication_evidence"]["status"] == "unknown"
    assert result["reply_outcome"] == 1 and result["reply_outcome_bridge_id"] == 51
    assert result["sample_shipped_n"] == 2
    assert project_communication_metrics(result) == result
    assert original == before


def _metrics(monkeypatch, conn):
    monkeypatch.setattr(gtm_windows, "_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(gtm_windows, "_action_bound_reply_actual", lambda *_a, **_k: {
        "reply_outcome": 1, "reply_outcome_binding": "human_reviewed_exact_action",
        "reply_outcome_receipt_id": 61})
    return gtm_windows._window_7d_metrics(conn, kol_pool_id=17, kol_id=9, project_ids=[10],
        start=datetime(2026, 8, 1, tzinfo=timezone.utc), end=datetime(2026, 8, 8, tzinfo=timezone.utc),
        action_inbox_id=41, product_sku="AF-26", channel="youtube")


@pytest.mark.parametrize("has_rows", [False, True])
def test_new_window_counts_capture_records_not_sent_or_received_events(monkeypatch, has_rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE vkpi_messages(kol_id INTEGER, project_id INTEGER, direction TEXT, captured_at TEXT)")
    if has_rows:
        conn.executemany("INSERT INTO vkpi_messages VALUES(?,?,?,?)", [
            (9, 10, "outbound", "2026-08-02T00:00:00Z"), (9, 10, "inbound", "2026-08-03T00:00:00Z"),
            (9, 10, "inbound", "2026-09-01T00:00:00Z"), (18, 20, "inbound", "2026-08-03T00:00:00Z"),
        ])
    try:
        result = _metrics(monkeypatch, conn)
    finally:
        conn.close()
    assert result["message_record_counts"] == {"outbound": int(has_rows), "inbound": int(has_rows),
                                                "status": "observed_records"}
    assert all(result[key] is None for key in COMMUNICATION_FIELDS)
    assert result["message_record_window_basis"] == "captured_at_not_sent_or_received_at"
    assert result["reply_outcome"] == 1 and result["reply_outcome_receipt_id"] == 61


def test_message_read_error_is_unknown_not_zero_and_cannot_override_action_review(monkeypatch):
    class Broken:
        def execute(self, *_a):
            raise RuntimeError("isolated fixture read failure")
    result = _metrics(monkeypatch, Broken())
    assert result["message_record_counts"] == {"outbound": None, "inbound": None, "status": "unknown"}
    assert result["reply_outcome"] == 1
    assert result["reply_rate"] is None


def _sealed_window():
    return data_readiness.seal_outcome_window_evidence({
        "schema": "vkpi_gtm_observation_window/v1", "status": "filled", "window": "7d",
        "source": gtm_windows._SOURCE_7D, "window_start": "2026-08-01T00:00:00Z",
        "window_end": "2026-08-08T00:00:00Z", "filled_at": "2026-08-09T00:00:00Z",
        "metrics": {"contacted": True, "replied": True, "outreach_sent_n": 1, "reply_n": 1,
                    "reply_rate": 1.0, "reply_outcome": 1, "reply_outcome_receipt_id": 61}})


def test_public_window_is_a_copy_and_explicitly_not_the_original_signed_payload():
    original = _sealed_window()
    before = deepcopy(original)
    result = project_public_7d_window(original)
    assert original == before
    assert original["evidence_sha256"] == data_readiness.outcome_window_evidence_sha256(original)
    assert result["evidence_sha256"] == original["evidence_sha256"]
    assert result["evidence_sha256_basis"] == "original_stored_window_not_public_projection"
    assert project_public_7d_window(result) == result


@pytest.mark.parametrize("event_matches", [True, False])
def test_list_outcomes_verifies_original_window_before_projecting_public_metrics(monkeypatch, event_matches):
    window = _sealed_window()
    stored = {"id": 1, "action_inbox_id": 901, "decision": "validated", "decided_at": "2026-08-10",
              "decided_by": 7, "window_7d": json.dumps(window), "window_14d": None, "window_28d": None}
    original = deepcopy(stored)
    calls = []
    class ReadOnly:
        def execute(self, sql, params=()):
            calls.append((sql, params))
            assert sql.lstrip().startswith("SELECT")
            if "FROM vkpi_event_ledger" in sql:
                rows = [{"actor_type": "system", "actor_id": "gtm_windows", "trace_id": "fixture",
                         "payload_json": json.dumps({"outcome_id": 1, "action_inbox_id": 901,
                             "evidence_field": "window_7d", "schema": window["schema"], "window": "7d",
                             "evidence_sha256": window["evidence_sha256"] if event_matches else "0" * 64}),
                         "provenance_json": json.dumps({"evidence_verification": "server_produced_observation_window"})}]
            elif "GROUP BY decision" in sql:
                rows = [{"decision": "validated", "n": 1}]
            elif "COUNT(*) FILTER" in sql:
                rows = [{"finalized": 1, "evidence_backed": int(event_matches)}]
            else:
                rows = [stored]
            return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0])
    monkeypatch.setattr(verdict_flow, "table_exists", lambda *_a: True)
    monkeypatch.setattr(verdict_flow, "get_conn", ReadOnly)
    monkeypatch.setattr(data_readiness, "build_learning_readiness", lambda **_k: {"claimable": True})
    result = verdict_flow.list_outcomes(decision="validated", limit=9)
    item = result["items"][0]
    assert item["evidence_backed"] is event_matches and item["claimable"] is event_matches
    assert item["window_7d"]["metrics"]["reply_rate"] is None
    assert item["window_7d"]["metrics"]["recorded_communication_metrics"]["reply_rate"] == 1.0
    assert item["window_7d"]["metrics"]["reply_outcome"] == 1
    assert calls[0][1] == ("validated", 9)
    assert stored == original


@pytest.fixture
def report_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE vkpi_kpi_ledger(id INTEGER PRIMARY KEY, ledger_date TEXT, staff_id INTEGER,
            kol_id INTEGER, project_id INTEGER, metric_key TEXT, metric_value REAL,
            source_type TEXT, source_ref TEXT, confidence TEXT, metadata_json TEXT, created_at TEXT);
        CREATE TABLE vkpi_projects(id INTEGER, project_name TEXT, project_uid TEXT, product_sku TEXT);
        CREATE TABLE kols(id INTEGER, channel_name TEXT, platform TEXT);
        CREATE TABLE staff(id INTEGER, user_id INTEGER);
        CREATE TABLE users(id INTEGER, name TEXT);
    """)
    monkeypatch.setattr(report_appendices, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(report_appendices, "get_conn", lambda: conn)
    yield conn
    conn.close()


def _insert_kpi(conn, metric, value, metadata=None, staff=7, day="2026-09-01"):
    conn.execute("INSERT INTO vkpi_kpi_ledger(ledger_date,staff_id,metric_key,metric_value,"
                 "source_type,source_ref,confidence,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                 (day, staff, metric, value, "derived_kpi", "sensitive-internal-ref", "confirmed",
                  json.dumps(metadata or {})))


@pytest.mark.parametrize("metric", ["recommendation_outreach_sent", "recommendation_reply_received",
                                    "stage_contacted", "stage_replied", "workload_score", "kpi_credit"])
@pytest.mark.parametrize("value", [0, 17])
def test_report_appendix_projects_old_aggregate_detail_and_formula_without_mutating_ledger(report_db, metric, value):
    _insert_kpi(report_db, metric, value, {"formula": "legacy reply scoring",
        "components": [{"metric_key": "recommendation_reply_received", "contribution": 17}]})
    _insert_kpi(report_db, "stage_agreed", 0)
    _insert_kpi(report_db, metric, 999, staff=8)
    _insert_kpi(report_db, metric, 999, day="2026-07-01")
    before = [dict(row) for row in report_db.execute("SELECT * FROM vkpi_kpi_ledger")]
    result = report_appendices._kpi_source_appendix("2026-09-01", "2026-09-02", scoped_staff_id=7)
    group = next(row for row in result["grouped"] if row["metric_key"] == metric)
    detail = next(row for row in result["source_rows"] if row["metric_key"] == metric)
    assert group["total_value"] is None and group["formatted_total"] == "未知"
    assert group["recorded_total_value"] == value and group["confidence"] == "unverified"
    assert detail["value"] == "未知" and detail["recorded_metric_value"] == value
    assert detail["confidence"] == "unverified" and detail["aggregation_eligible"] is False
    assert detail["component_summary"] == "" and "不计入当前积分" in detail["formula"]
    assert detail["recorded_formula"] == "legacy reply scoring"
    assert "sensitive-internal-ref" not in detail["source_ref"]
    assert next(row for row in result["grouped"] if row["metric_key"] == "stage_agreed")["formatted_total"] == "0"
    html = pdf_renderer.render_report_html({"kpi_appendix": result})
    assert "legacy reply scoring" not in html and "未知" in html
    assert [dict(row) for row in report_db.execute("SELECT * FROM vkpi_kpi_ledger")] == before


def test_report_keeps_new_valid_derived_components_operational_not_confirmed(report_db):
    _insert_kpi(report_db, "workload_score", 4, {
        "label_semantics": KPI_LABEL_SEMANTICS, "formula": "sum(metric_value * workload_weight)",
        "components": [{"metric_key": "new_kol", "metric_value": 2, "weight": 2,
                        "contribution": 4, "source_count": 2}]})
    result = report_appendices._kpi_source_appendix("2026-09-01", scoped_staff_id=7)
    assert result["source_rows"][0]["value"] == "4"
    assert result["source_rows"][0]["confidence"] == "operational"
    assert result["source_rows"][0]["aggregation_eligible"] is True
    # SQL aggregate has no component provenance; it is not silently promoted.
    assert result["grouped"][0]["total_value"] is None


@pytest.mark.parametrize("shape", ["nested", "flat", "mixed"])
@pytest.mark.parametrize("encoded", [False, True])
def test_verdict_context_projects_both_window_shapes_without_hiding_manual_results(shape, encoded):
    window = {"note": "original manual note", "reply_outcome": 1, "reply_outcome_receipt_id": 61}
    if shape != "nested":
        window.update(contacted=5, replied=2, reply_rate=0.4)
    if shape != "flat":
        window["metrics"] = {"contacted": True, "replied": False, "reply_rate": 0,
                             "reply_outcome": 0, "reply_outcome_receipt_id": 62}
    raw = {"window_7d": window, "expected_result": {"reply_rate": 0.5, "note": "human target"},
           "actual_result": {"replied": 2, "note": "human statement"},
           "window_14d": {"views": 0}, "window_28d": {"orders": 0}}
    if encoded:
        raw = {key: json.dumps(value) for key, value in raw.items()}
    before = deepcopy(raw)
    result = vkpi_gtm_weights._outcome_out(raw)
    public = result["window_7d"]
    if shape != "nested":
        assert public["contacted"] is public["replied"] is public["reply_rate"] is None
        assert public["recorded_communication_metrics"] == {"contacted": 5, "replied": 2, "reply_rate": 0.4}
    if shape != "flat":
        assert public["metrics"]["contacted"] is public["metrics"]["replied"] is None
        assert public["metrics"]["recorded_communication_metrics"] == {
            "contacted": True, "replied": False, "reply_rate": 0}
        assert public["metrics"]["reply_outcome"] == 0 and public["metrics"]["reply_outcome_receipt_id"] == 62
    assert public["reply_outcome"] == 1 and public["reply_outcome_receipt_id"] == 61
    assert public["note"] == "original manual note"
    assert result["actual_result"] == {"replied": 2, "note": "human statement"}
    assert result["expected_result"] == {"reply_rate": 0.5, "note": "human target"}
    assert result["window_14d"] == {"views": 0} and result["window_28d"] == {"orders": 0}
    assert vkpi_gtm_weights._outcome_out(result) == result
    assert raw == before


@pytest.mark.parametrize("window", [{}, None, [], {"sample_shipped_n": 0},
                                   {"reply_outcome": 1, "reply_outcome_receipt_id": 61}])
def test_public_window_leaves_empty_and_noncommunication_flat_values_unchanged(window):
    before = deepcopy(window)
    assert project_public_7d_window(window) == before
    assert window == before


_CONTEXT_STAFF = {"organization_id": 1, "organization_scope_status": "resolved"}


def _install_context_row(monkeypatch, row):
    queries = []
    class ReadOnly:
        def execute(self, sql, params):
            assert sql.lstrip().startswith("SELECT")
            queries.append((sql, params))
            return SimpleNamespace(fetchone=lambda: row)
    monkeypatch.setattr(connection, "table_exists", lambda *_a: True)
    monkeypatch.setattr(connection, "get_conn", ReadOnly)
    return queries


@pytest.mark.parametrize("id_type", ["inbox", "outcome"])
@pytest.mark.parametrize("encoded", [False, True])
def test_context_handler_previews_original_sealed_evidence_before_public_projection(monkeypatch, id_type, encoded):
    window = _sealed_window()
    row = {"id": 1, "action_inbox_id": 901, "window_7d": json.dumps(window) if encoded else window}
    before = deepcopy(row)
    queries = _install_context_row(monkeypatch, row)
    calls = []
    def preview(data, *, dry_run):
        assert data == before and dry_run is True
        raw_window = json.loads(data["window_7d"]) if encoded else data["window_7d"]
        assert raw_window["metrics"]["replied"] is True
        assert data_readiness.outcome_window_evidence_sha256(raw_window) == window["evidence_sha256"]
        calls.append("preview_original")
        return {"ok": True, "entries": [], "proof_basis": "fixture_original_verified"}
    monkeypatch.setattr(weight_feedback, "apply_weight_change", preview)
    result = vkpi_gtm_weights.get_verdict_context(901, id_type=id_type, staff=_CONTEXT_STAFF)
    assert calls == ["preview_original"] and queries[0][1] == (901,)
    assert ("action_inbox_id = ?" if id_type == "inbox" else "WHERE id = ?") in queries[0][0]
    assert result["outcome"]["window_7d"]["metrics"]["replied"] is None
    assert result["weight_preview"]["proof_basis"] == "fixture_original_verified"
    assert row == before


def test_context_preview_failure_does_not_restore_raw_communication_claims(monkeypatch):
    row = {"window_7d": {"contacted": 5, "replied": 2}, "actual_result": {"note": "human statement"}}
    before = deepcopy(row)
    _install_context_row(monkeypatch, row)
    def failed_preview(*_a, **_k):
        raise RuntimeError("fixture preview unavailable")
    monkeypatch.setattr(weight_feedback, "apply_weight_change", failed_preview)
    result = vkpi_gtm_weights.get_verdict_context(1, id_type="outcome", staff=_CONTEXT_STAFF)
    assert result["weight_preview"]["ok"] is False
    assert result["outcome"]["window_7d"]["replied"] is None
    assert result["outcome"]["actual_result"] == {"note": "human statement"}
    assert row == before


def test_context_invalid_id_type_is_still_422_without_reading(monkeypatch):
    queries = _install_context_row(monkeypatch, None)
    with pytest.raises(vkpi_gtm_weights.HTTPException) as error:
        vkpi_gtm_weights.get_verdict_context(1, id_type="wrong", staff=_CONTEXT_STAFF)
    assert error.value.status_code == 422 and queries == []


@pytest.mark.parametrize("id_type", ["inbox", "outcome"])
def test_context_missing_row_is_still_404(monkeypatch, id_type):
    queries = _install_context_row(monkeypatch, None)
    with pytest.raises(vkpi_gtm_weights.HTTPException) as error:
        vkpi_gtm_weights.get_verdict_context(1, id_type=id_type, staff=_CONTEXT_STAFF)
    assert error.value.status_code == 404 and len(queries) == 1


def test_context_scope_denial_still_precedes_every_read(monkeypatch):
    queries = _install_context_row(monkeypatch, None)
    result = vkpi_gtm_weights.get_verdict_context(1, id_type="outcome", staff={**_CONTEXT_STAFF, "organization_id": 4})
    assert result["status"] == "scope_unavailable" and queries == []
