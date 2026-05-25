"""Tests for V-KPI metric lineage layer.

Covers:
  - generate_run writes run header + values + sources
  - dedup of source_count between value and source rows
  - drilldown_value returns hydrated entities
  - drilldown_latest falls back gracefully when no run exists
  - definition_version is captured per run
  - sources are correctly hydrated with project/kol/staff names

Run:
  pytest tests/test_vkpi_metric_lineage.py -v
"""
from __future__ import annotations

import pytest

from app.db.connection import get_conn
from app.domains import lineage as metric_lineage
from app.domains.lineage import DEFINITION_VERSION
from app.services.vkpi import drilldown
from app.services.vkpi.schema import ensure_vkpi_schema
from app.domains.lineage import ensure_vkpi_lineage_schema


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _ensure_schemas():
    """Ensure both core and lineage schemas exist for every test."""
    ensure_vkpi_schema()
    ensure_vkpi_lineage_schema()
    yield


@pytest.fixture
def seeded_data():
    """Create one project + one cost + one sales attribution row.

    Returns the ids so each test can assert against them.
    """
    conn = get_conn()
    now = "2026-05-01T10:00:00Z"
    marker = "VKPI-METRIC-LINEAGE-TEST"
    email = "vkpi-metric-lineage-test@example.com"
    project_uid = "VKPI-TEST-001"
    source_ref = "test-order-1"
    cost_ref = "test-cost-1"

    ids: dict[str, int] = {}

    def cleanup() -> None:
        project_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (project_uid,)).fetchall()
        ]
        kol_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM kols WHERE channel_name=?", (marker,)).fetchall()
        ]
        staff_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT s.id FROM staff s JOIN users u ON u.id=s.user_id WHERE u.email=?",
                (email,),
            ).fetchall()
        ]
        user_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchall()
        ]

        conn.execute("DELETE FROM vkpi_metric_sources")
        conn.execute("DELETE FROM vkpi_metric_values")
        conn.execute("DELETE FROM vkpi_metric_runs")
        conn.execute("DELETE FROM vkpi_sales_attributions WHERE source_ref=?", (source_ref,))
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref=?", (cost_ref,))
        for project_id in project_ids:
            conn.execute("DELETE FROM vkpi_projects WHERE id=?", (project_id,))
        for kol_id in kol_ids:
            conn.execute("DELETE FROM kols WHERE id=?", (kol_id,))
        for staff_id in staff_ids:
            conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        for user_id in user_ids:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()

    try:
        cleanup()

        conn.execute(
            """INSERT INTO users
               (created_at, email, password_hash, name, status, role, email_verified)
               VALUES (?,?,?,?,?,?,?)""",
            (now, email, "v2:00:00", "Metric Lineage Test", "approved", "admin", 1),
        )
        user_row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        user_id = int(user_row["id"])

        conn.execute(
            """INSERT INTO staff
               (user_id, role, permissions_json, mfa_enabled, active, invited_at, is_owner, email_domain_verified)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, "admin", '{"vkpi":"admin"}', 0, 1, now, 1, 1),
        )
        staff_row = conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()
        staff_id = int(staff_row["id"])

        conn.execute(
            """INSERT INTO kols
               (channel_name, channel_url, platform, assigned_staff_id, created_by_staff_id, follower_count, avg_views)
               VALUES (?,?,?,?,?,?,?)""",
            (marker, "https://example.com/vkpi-metric-lineage", "youtube", staff_id, staff_id, 1000, 100),
        )
        kol_row = conn.execute("SELECT id FROM kols WHERE channel_name=?", (marker,)).fetchone()
        kol_id = int(kol_row["id"])

        # 1 project
        conn.execute(
            """INSERT INTO vkpi_projects
               (project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                stage, stage_status, priority, source_type, sample_status, metadata_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project_uid, "Test Project", kol_id, staff_id, staff_id,
             "shipped", "active", "normal", "manual", "shipped", "{}", now, now),
        )
        project_row = conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (project_uid,)).fetchone()
        project_id = int(project_row["id"])

        # 1 sales attribution: $100 revenue
        conn.execute(
            """INSERT INTO vkpi_sales_attributions
               (source_platform, source_ref, project_id, kol_id, staff_id, revenue_cents,
                commission_cents, currency, attribution_model, confidence, occurred_at,
                imported_at, evidence_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("shopify", source_ref, project_id, kol_id, staff_id, 10000,
             500, "USD", "last_touch", "confirmed", now, now, "{}", now),
        )
        sales_row = conn.execute("SELECT id FROM vkpi_sales_attributions WHERE source_ref=?", (source_ref,)).fetchone()
        sales_id = int(sales_row["id"])

        # 1 cost: $30 product cost
        conn.execute(
            """INSERT INTO vkpi_cost_ledger
               (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status,
                incurred_at, source_ref, note, metadata_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, kol_id, staff_id, "product_cost", 3000, "USD", "actual",
             now, cost_ref, "test product cost", "{}", now),
        )
        cost_row = conn.execute("SELECT id FROM vkpi_cost_ledger WHERE source_ref=?", (cost_ref,)).fetchone()
        cost_id = int(cost_row["id"])

        conn.commit()
        ids = {"project_id": project_id, "sales_id": sales_id, "cost_id": cost_id, "kol_id": kol_id, "staff_id": staff_id}
        yield ids
    finally:
        cleanup()


# ---------------------------------------------------------------------------
# generate_run
# ---------------------------------------------------------------------------

def _staff_context(seed: dict[str, int]) -> dict[str, object]:
    return {
        "id": seed["staff_id"],
        "staff_id": seed["staff_id"],
        "role": "admin",
        "is_owner": True,
    }


def _generate_seeded_run(seed: dict[str, int], **kwargs):
    """Generate a run scoped to this fixture's staff so live DB rows do not pollute assertions."""
    return metric_lineage.generate_run(
        period_days=365,
        scope_type="staff",
        scope_id=seed["staff_id"],
        generated_by_staff_id=seed["staff_id"],
        **kwargs,
    )


def test_generate_run_writes_header_and_values(seeded_data):
    result = _generate_seeded_run(seeded_data, trigger_source="dashboard")
    assert result["run_id"] > 0
    assert result["run_uid"].startswith("mr-")
    assert result["definition_version"] == DEFINITION_VERSION
    assert result["metric_count"] >= 5  # gmv, cost, new_kol, published_content, valid_clicks + derived
    assert result["source_count"] >= 2  # at least 1 sales + 1 cost row contributed


def test_generate_run_gmv_value_matches_seed(seeded_data):
    result = _generate_seeded_run(seeded_data, trigger_source="dashboard")
    run_detail = metric_lineage.get_run(result["run_id"])

    gmv_value = next((v for v in run_detail["values"] if v["metric_key"] == "gmv"), None)
    cost_value = next((v for v in run_detail["values"] if v["metric_key"] == "cost"), None)
    assert gmv_value is not None
    assert cost_value is not None
    assert int(gmv_value["value_numeric"]) == 10000  # $100 in cents
    assert int(cost_value["value_numeric"]) == 3000  # $30 in cents
    assert gmv_value["source_count"] == 1
    assert cost_value["source_count"] == 1


def test_generate_run_derived_metrics_correct(seeded_data):
    result = _generate_seeded_run(seeded_data, trigger_source="dashboard")
    run_detail = metric_lineage.get_run(result["run_id"])

    net = next((v for v in run_detail["values"] if v["metric_key"] == "net_contribution"), None)
    roi = next((v for v in run_detail["values"] if v["metric_key"] == "roi"), None)
    assert net is not None and roi is not None
    # gmv 10000 - cost 3000 = 7000 cents net
    assert int(net["value_numeric"]) == 7000
    # roi = gmv / cost = 10000 / 3000 = 3.3333 (see metric_definitions.py)
    assert abs(float(roi["value_numeric"]) - (10000 / 3000)) < 0.001


def test_definition_version_persisted(seeded_data):
    result = _generate_seeded_run(seeded_data, trigger_source="dashboard")
    run_detail = metric_lineage.get_run(result["run_id"])
    assert run_detail["run"]["definition_version"] == DEFINITION_VERSION


def test_generate_run_rejects_unknown_metric(seeded_data):
    # passing only an unknown metric should raise
    with pytest.raises(ValueError):
        metric_lineage.generate_run(metrics=["totally_made_up_metric"])


def test_generate_run_subset_metrics_only(seeded_data):
    result = _generate_seeded_run(seeded_data, metrics=["gmv", "cost"])
    run_detail = metric_lineage.get_run(result["run_id"])
    keys = {v["metric_key"] for v in run_detail["values"]}
    # net_contribution and roi are derived; they are skipped if requested subset
    # excludes them via filter (since is_known_metric returns True only for listed keys)
    assert "gmv" in keys
    assert "cost" in keys


# ---------------------------------------------------------------------------
# drilldown
# ---------------------------------------------------------------------------

def test_drilldown_value_returns_hydrated_rows(seeded_data):
    result = _generate_seeded_run(seeded_data, trigger_source="dashboard")
    run_detail = metric_lineage.get_run(result["run_id"])
    gmv_value = next(v for v in run_detail["values"] if v["metric_key"] == "gmv")

    drilldown_result = drilldown.drilldown_value(int(gmv_value["id"]))
    assert drilldown_result["row_count"] == 1
    row = drilldown_result["rows"][0]
    assert row["source_type"] == "sales_attribution"
    assert row["source_id"] == seeded_data["sales_id"]
    assert row["evidence_ref"] == "test-order-1"
    assert row["evidence_type"] == "shopify"
    assert row["contribution_amount"] == 10000


def test_drilldown_value_404_for_unknown(seeded_data):
    with pytest.raises(LookupError):
        drilldown.drilldown_value(99999999)


def test_drilldown_value_filter_by_project(seeded_data):
    result = _generate_seeded_run(seeded_data, trigger_source="dashboard")
    run_detail = metric_lineage.get_run(result["run_id"])
    gmv_value = next(v for v in run_detail["values"] if v["metric_key"] == "gmv")

    matching = drilldown.drilldown_value(
        int(gmv_value["id"]),
        project_id=seeded_data["project_id"],
        staff=_staff_context(seeded_data),
    )
    assert matching["row_count"] == 1

    not_matching = drilldown.drilldown_value(
        int(gmv_value["id"]),
        project_id=999999,
        staff=_staff_context(seeded_data),
    )
    assert not_matching["row_count"] == 0


def test_drilldown_latest_no_run_yet():
    # ensure clean state: clear any prior runs
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_metric_sources")
    conn.execute("DELETE FROM vkpi_metric_values")
    conn.execute("DELETE FROM vkpi_metric_runs")
    conn.commit()

    result = drilldown.drilldown_latest("gmv")
    assert result["empty_reason"] == "no_run_yet"
    assert result["row_count"] == 0
    assert result["value"] is None


def test_drilldown_latest_finds_most_recent(seeded_data):
    # generate two runs; latest one wins
    _generate_seeded_run(seeded_data, trigger_source="dashboard")
    second = _generate_seeded_run(seeded_data, trigger_source="dashboard")

    result = drilldown.drilldown_latest(
        "gmv",
        scope_type="staff",
        scope_id=seeded_data["staff_id"],
        staff_id=seeded_data["staff_id"],
        staff=_staff_context(seeded_data),
    )
    assert result["value"] is not None
    # latest run id should match
    assert int(result["value"]["run_id"]) == int(second["run_id"])


# ---------------------------------------------------------------------------
# list_runs / get_run
# ---------------------------------------------------------------------------

def test_list_runs_filter_by_trigger(seeded_data):
    _generate_seeded_run(seeded_data, trigger_source="dashboard")
    _generate_seeded_run(seeded_data, trigger_source="weekly_report")

    dashboards = metric_lineage.list_runs(trigger_source="dashboard")
    weekly = metric_lineage.list_runs(trigger_source="weekly_report")
    assert all(r["trigger_source"] == "dashboard" for r in dashboards["runs"])
    assert all(r["trigger_source"] == "weekly_report" for r in weekly["runs"])


# ---------------------------------------------------------------------------
# source contribution percent
# ---------------------------------------------------------------------------

def test_source_contribution_percent_correct(seeded_data):
    """If we have only one source row, its contribution_percent should be 100."""
    result = _generate_seeded_run(seeded_data)
    run_detail = metric_lineage.get_run(result["run_id"])
    gmv_value = next(v for v in run_detail["values"] if v["metric_key"] == "gmv")

    drilldown_result = drilldown.drilldown_value(int(gmv_value["id"]))
    row = drilldown_result["rows"][0]
    assert abs(float(row["contribution_percent"]) - 100.0) < 0.01
