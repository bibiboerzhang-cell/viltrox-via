"""KOL profile aggregates preserve historical audit values, never eligible send points."""
from copy import deepcopy
import sqlite3

import pytest

from app.domains.kol import profile_assembly
from app.shared.vkpi_kpi_communication_truth import project_kpi_source_row


@pytest.mark.parametrize("metric", ["recommendation_outreach_sent", "recommendation_reply_received",
                                   "stage_contacted", "stage_replied", "workload_score", "kpi_credit"])
@pytest.mark.parametrize("project_first", [True, False])
def test_profile_aggregation_retains_recorded_total_but_never_rehabilitates_old_points(metric, project_first):
    rows = [{"metric_key": metric, "metric_value": 5, "confidence": "confirmed"},
            {"metric_key": metric, "metric_value": 2, "confidence": "confirmed"}]
    before = deepcopy(rows)
    source = [project_kpi_source_row(row) for row in rows] if project_first else rows
    item = profile_assembly.build_kpi_summary(source)[0]
    assert item["total_value"] is None
    assert item["aggregation_eligible"] is False
    assert item["recorded_total_value"] == 7
    assert rows == before


def test_noncommunication_zero_remains_a_known_zero_in_profile_summary():
    result = profile_assembly.build_kpi_summary([{"metric_key": "views", "metric_value": 0}])
    assert result[0]["total_value"] == 0


def test_profile_applies_same_projection_to_ledger_summary_and_timeline(monkeypatch):
    from app.domains.kol import profile_detail

    raw = {"id": 5, "metric_key": "recommendation_reply_received", "metric_value": 19,
           "confidence": "confirmed", "ledger_date": "2026-09-01"}
    before = deepcopy(raw)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE kols(id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO kols VALUES(9)")
    monkeypatch.setattr(profile_detail, "get_conn", lambda: conn)
    monkeypatch.setattr(profile_detail, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(profile_detail, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(profile_detail.claim_access, "assert_kol_access", lambda *a, **k: None)
    monkeypatch.setattr(profile_detail.scope, "can_view_all", lambda *a, **k: True)
    monkeypatch.setattr(profile_detail.scope, "actor_staff_id", lambda *a: 3)
    monkeypatch.setattr(profile_detail.profile_scope, "project_staff_filter", lambda *a: ("", []))
    monkeypatch.setattr(profile_detail, "_row_or_empty", lambda *a, **k: {})
    monkeypatch.setattr(profile_detail, "_rows_or_empty", lambda sql, *a: [raw] if "FROM vkpi_kpi_ledger" in sql else [])
    try:
        result = profile_detail.profile(9, staff={"id": 3})
    finally:
        conn.close()
    assert result["kpi_ledger"][0]["metric_value"] is None
    assert result["kpi_ledger"][0]["recorded_metric_value"] == 19
    assert result["kpi_summary"][0]["total_value"] is None
    assert result["kpi_summary"][0]["recorded_total_value"] == 19
    timeline = next(item["data"] for item in result["activity_timeline"] if item["type"] == "kpi")
    assert timeline["metric_value"] is None
    assert timeline["aggregation_eligible"] is False
    assert raw == before
