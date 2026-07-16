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

from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.domains import lineage as metric_lineage
from app.domains.lineage import DEFINITION_VERSION
from app.domains.lineage.definitions import METRICS
import app.domains.lineage.drilldown as drilldown
import app.domains.lineage.schema as lineage_schema
import app.platform.db.schema as vkpi_schema
from app.services.vkpi.schema import ensure_vkpi_schema
from app.domains.lineage import ensure_vkpi_lineage_schema
from app.domains.costs.ledger import _ensure_cost_ledger_columns


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _lineage_test_db(tmp_path_factory: pytest.TempPathFactory):
    """Give this module a private, minimally seeded SQLite database.

    The lineage tests exercise real SQL and schema guards, but must not inherit
    either the repository ``submissions.db`` or tables left behind by another
    test module.  Only the three identity/KOL tables not owned by the V-KPI
    schema guard are declared here; the production schema guards build every
    lineage and metric source table under test.
    """
    db_path = (tmp_path_factory.mktemp("metric-lineage") / "lineage.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db

    old_db_path = db_connection.DB_PATH
    old_runtime_backend = db_connection.DB_RUNTIME_BACKEND
    old_runtime_url = db_connection.DB_RUNTIME_URL
    old_vkpi_ready = vkpi_schema._SCHEMA_READY
    old_lineage_ready = lineage_schema._SCHEMA_READY

    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    vkpi_schema._SCHEMA_READY = False
    lineage_schema._SCHEMA_READY = False

    conn = get_conn()
    try:
        actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
        assert actual_path == db_path
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                email TEXT UNIQUE,
                password_hash TEXT,
                name TEXT,
                status TEXT DEFAULT 'pending',
                role TEXT DEFAULT 'creator',
                email_verified INTEGER DEFAULT 0
            );
            CREATE TABLE staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                role TEXT NOT NULL DEFAULT 'readonly',
                permissions_json TEXT NOT NULL DEFAULT '{}',
                mfa_enabled INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                invited_at TEXT,
                is_owner INTEGER NOT NULL DEFAULT 0,
                email_domain_verified INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE kols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_name TEXT NOT NULL,
                channel_url TEXT,
                platform TEXT NOT NULL,
                assigned_staff_id INTEGER,
                created_by_staff_id INTEGER,
                follower_count INTEGER DEFAULT 0,
                avg_views INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        ensure_vkpi_schema()
        _ensure_cost_ledger_columns()
        ensure_vkpi_lineage_schema()
        conn.commit()
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url
        vkpi_schema._SCHEMA_READY = old_vkpi_ready
        lineage_schema._SCHEMA_READY = old_lineage_ready


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
    snapshot_ref = "test-shopify-snapshot-1"

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
        conn.execute("DELETE FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?", (snapshot_ref,))
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

        # 1 signed-provider-equivalent snapshot + attribution: $100 revenue.
        conn.execute(
            """
            INSERT INTO vkpi_shopify_order_snapshots (
                shopify_order_id, financial_status, provider_auth_mode,
                provider_verified_at, raw_payload_hash, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (snapshot_ref, "paid", "shopify-hmac", now, "sha256:test-signed-payload", now, now),
        )
        snapshot_id = int(
            conn.execute(
                "SELECT id FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?",
                (snapshot_ref,),
            ).fetchone()["id"]
        )
        conn.execute(
            """INSERT INTO vkpi_sales_attributions
               (source_platform, source_ref, project_id, kol_id, staff_id, shopify_order_snapshot_id, revenue_cents,
                commission_cents, currency, attribution_model, confidence, occurred_at,
                imported_at, evidence_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("shopify", source_ref, project_id, kol_id, staff_id, snapshot_id, 10000,
             500, "USD", "last_touch", "confirmed", now, now, "{}", now),
        )
        sales_row = conn.execute("SELECT id FROM vkpi_sales_attributions WHERE source_ref=?", (source_ref,)).fetchone()
        sales_id = int(sales_row["id"])

        # 1 cost: $30 product cost
        conn.execute(
            """INSERT INTO vkpi_cost_ledger
               (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status,
                incurred_at, source_ref, note, metadata_json, created_at, approved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, kol_id, staff_id, "product_cost", 3000, "USD", "actual",
             now, cost_ref, "test product cost", "{}", now, now),
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


def test_financial_run_without_canonical_sources_persists_unknown_not_zero(seeded_data):
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_sales_attributions WHERE id=?", (seeded_data["sales_id"],))
    conn.execute("DELETE FROM vkpi_cost_ledger WHERE id=?", (seeded_data["cost_id"],))
    conn.commit()

    result = _generate_seeded_run(
        seeded_data,
        trigger_source="dashboard",
        metrics=["gmv", "cost", "net_contribution", "roi"],
    )
    values = {
        row["metric_key"]: row
        for row in metric_lineage.get_run(result["run_id"])["values"]
    }

    for key in ("gmv", "cost", "net_contribution", "roi"):
        assert values[key]["value_numeric"] is None
        assert values[key]["data_status"] == "awaiting_source"
        assert float(values[key]["confidence"]) == 0.0
        assert bool(values[key]["is_partial"]) is True
    assert values["gmv"]["source_count"] == 0
    assert values["cost"]["source_count"] == 0
    assert values["net_contribution"]["source_count"] == 0
    assert values["roi"]["source_count"] == 0


def test_roi_with_real_zero_cost_is_unavailable_not_synthetic_zero(seeded_data):
    conn = get_conn()
    conn.execute(
        "UPDATE vkpi_cost_ledger SET amount_cents=0 WHERE id=?",
        (seeded_data["cost_id"],),
    )
    conn.commit()

    result = _generate_seeded_run(
        seeded_data,
        trigger_source="dashboard",
        metrics=["gmv", "cost", "roi"],
    )
    values = {
        row["metric_key"]: row
        for row in metric_lineage.get_run(result["run_id"])["values"]
    }

    assert values["cost"]["value_numeric"] == 0
    assert values["cost"]["data_status"] == "real"
    assert values["roi"]["value_numeric"] is None
    assert values["roi"]["data_status"] == "unavailable"
    assert values["roi"]["source_count"] == 0
    assert bool(values["roi"]["is_partial"]) is True


def test_v4_financial_definition_metadata_matches_canonical_truth_contract() -> None:
    assert DEFINITION_VERSION == "v4"
    assert "shopify-hmac" in METRICS["gmv"]["formula"]
    assert "provider_verified_at" in METRICS["gmv"]["formula"]
    assert "status='actual'" in METRICS["cost"]["formula"]
    assert "approved_at IS NOT NULL" in METRICS["cost"]["formula"]
    assert "never synthetic zero" in METRICS["roi"]["formula"]


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


def test_drilldown_unavailable_metric_retains_audit_count_but_hides_sources(seeded_data):
    result = _generate_seeded_run(seeded_data, trigger_source="dashboard")
    run_detail = metric_lineage.get_run(result["run_id"])
    gmv_value = next(v for v in run_detail["values"] if v["metric_key"] == "gmv")
    conn = get_conn()
    conn.execute(
        """
        UPDATE vkpi_metric_values
        SET value_numeric=NULL, data_status='unavailable', confidence=0, is_partial=1
        WHERE id=?
        """,
        (int(gmv_value["id"]),),
    )
    conn.commit()

    drilldown_result = drilldown.drilldown_value(int(gmv_value["id"]))

    assert drilldown_result["value"]["value_numeric"] is None
    assert drilldown_result["value"]["data_status"] == "unavailable"
    assert drilldown_result["value"]["source_count"] == 0
    assert drilldown_result["value"]["retained_source_count"] == 1
    assert drilldown_result["rows"] == []
    assert drilldown_result["row_count"] == 0
    assert drilldown_result["empty_reason"] == "metric_unavailable"


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
