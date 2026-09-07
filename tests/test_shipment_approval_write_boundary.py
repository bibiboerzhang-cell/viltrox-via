"""Synthetic SQLite only: actual shipment writers, no business DB or providers."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.projects import shipment_approval as approval
from app.domains.projects import shipment_write_guard as guard
from app.domains.projects import workflow_evidence as assignments
from app.domains.projects import workflow_evidence_project_writes as project_writes
from app.domains.projects import workflow_projects as projects
from app.domains.evidence import shipments

STAFF = {"id": 7, "role": "manager", "staff_id": 7}


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.create_function("NOW", 0, lambda: "2026-09-07T12:00:00Z")
    conn.executescript("""
      CREATE TABLE vkpi_projects (
        id INTEGER PRIMARY KEY, kol_id INTEGER, project_name TEXT DEFAULT 'fixture',
        product_sku TEXT DEFAULT 'fixture', product_name TEXT DEFAULT 'fixture',
        stage TEXT DEFAULT 'agreed', stage_status TEXT DEFAULT 'active',
        sample_status TEXT DEFAULT '', tracking_number TEXT DEFAULT '',
        metadata_json TEXT DEFAULT '{}', assigned_staff_id INTEGER,
        closed_at TEXT, last_activity_at TEXT, updated_at TEXT);
      CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, linked_main_kol_id INTEGER);
      CREATE TABLE vkpi_project_kol_assignments (
        id INTEGER PRIMARY KEY, project_id INTEGER, kol_pool_id INTEGER,
        stage TEXT DEFAULT 'agreed', stage_status TEXT DEFAULT 'active',
        metadata_json TEXT DEFAULT '{}', tracking_number TEXT DEFAULT '',
        is_placeholder_tracking INTEGER DEFAULT 0, source_ref TEXT, updated_at TEXT);
      CREATE TABLE vkpi_shipment_approvals (
        id INTEGER PRIMARY KEY, project_id INTEGER, kol_pool_id INTEGER,
        status TEXT, requested_by INTEGER, approved_by INTEGER, approved_at TEXT,
        reason TEXT DEFAULT '', created_at TEXT DEFAULT 'now', updated_at TEXT,
        UNIQUE(project_id, kol_pool_id));
      CREATE TABLE vkpi_sample_assets (
        id INTEGER PRIMARY KEY, project_id INTEGER, kol_id INTEGER,
        product_sku TEXT, product_name TEXT, serial_number TEXT, sample_cost_cents INTEGER,
        currency TEXT, return_required INTEGER, status TEXT, shipped_at TEXT,
        received_at TEXT, note TEXT, metadata_json TEXT, created_at TEXT, updated_at TEXT);
      CREATE TABLE vkpi_shipments (
        id INTEGER PRIMARY KEY, project_id INTEGER, assignment_id INTEGER, sample_asset_id INTEGER,
        carrier TEXT, tracking_number TEXT, status TEXT, shipping_cost_cents INTEGER,
        currency TEXT, shipped_at TEXT, delivered_at TEXT, evidence_url TEXT,
        note TEXT, metadata_json TEXT, created_at TEXT, updated_at TEXT);
      CREATE TABLE vkpi_project_stage_events (
        id INTEGER PRIMARY KEY, project_id INTEGER, from_stage TEXT, to_stage TEXT,
        event_type TEXT, actor_staff_id INTEGER, note TEXT, source_ref_type TEXT,
        source_ref_id TEXT, effective_at TEXT, metadata_json TEXT, created_at TEXT);
      INSERT INTO vkpi_projects(id,kol_id) VALUES (71,9),(72,NULL);
      INSERT INTO vkpi_kol_pool VALUES (17,9),(9,999),(18,10);
      INSERT INTO vkpi_project_kol_assignments(id,project_id,kol_pool_id) VALUES (81,71,17),(82,72,18);
      INSERT INTO vkpi_shipments(id,project_id,assignment_id,tracking_number,status,metadata_json)
        VALUES (61,71,81,'','planned','{"assignment_id":81,"kol_pool_id":17}');
    """)
    for module in (approval, assignments, project_writes, projects, shipments):
        monkeypatch.setattr(module, "get_conn", lambda: conn)
        if hasattr(module, "ensure_vkpi_schema"):
            monkeypatch.setattr(module, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(approval, "table_exists", lambda _: True)
    monkeypatch.setattr(approval, "_actor", lambda _: 7)
    monkeypatch.setattr(approval.scope, "can_view_all", lambda *_a, **_k: True)
    monkeypatch.setattr(approval.scope, "assert_project_access", lambda *_a, **_k: None)
    monkeypatch.setattr(assignments.audit, "log_business_event", lambda **_: None)
    monkeypatch.setattr(projects, "_log_project_audit", lambda **_: None)
    monkeypatch.setattr(projects, "_validate_transition", lambda *_a: None)
    from app.domains import costs
    from app.domains.platform import event_ledger
    monkeypatch.setattr(costs, "record_shipped_product_cost", lambda *_a, **_k: {"status": "fixture"})
    monkeypatch.setattr(event_ledger, "emit", lambda *_a, **_k: None)
    monkeypatch.setattr(shipments, "_assert_shipment_access", lambda sid, *_a, **_k: dict(conn.execute("SELECT * FROM vkpi_shipments WHERE id=?", (sid,)).fetchone()))
    yield conn
    conn.close()


def _approve(db, pool=17, status="approved"):
    db.execute("INSERT INTO vkpi_shipment_approvals(project_id,kol_pool_id,status,approved_by,approved_at) VALUES(71,?,?,7,'now')", (pool, status))
    db.commit()


WRITERS = {
    "project_new": lambda: project_writes.add_project_shipment(71, {"tracking_number": "NEW"}, staff=STAFF),
    "evidence_new": lambda: shipments.create_shipment({"project_id": 71, "tracking_number": "NEW"}, staff=STAFF),
    "assignment_shipping": lambda: assignments.update_project_kol_shipping(71, 81, {"tracking_number": "NEW"}, staff=STAFF),
    "assignment_stage": lambda: assignments.advance_project_kol_assignment(71, 81, {"to_stage": "device_sent"}, staff=STAFF),
    "project_stage": lambda: projects.transition_project(71, {"to_stage": "shipped"}, staff=STAFF),
    "project_tracking": lambda: projects.update_project(71, {"tracking_number": "NEW"}, staff=STAFF),
    "project_sample": lambda: projects.update_project(71, {"sample_status": "shipped"}, staff=STAFF),
    "shipment_update": lambda: shipments.update_shipment(61, {"status": "shipped", "tracking_number": "NEW"}, staff=STAFF),
}


def _snapshot(db):
    return tuple(tuple(tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")) for table in (
        "vkpi_projects", "vkpi_project_kol_assignments", "vkpi_shipments", "vkpi_sample_assets",
    ))


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("state", ["missing", "pending", "rejected", "wrong_identity", "missing_table"])
def test_unapproved_actual_writers_never_write(db, writer, state):
    if state == "missing_table":
        db.execute("DROP TABLE vkpi_shipment_approvals")
    elif state != "missing":
        _approve(db, pool=9 if state == "wrong_identity" else 17, status="approved" if state == "wrong_identity" else state)
    before = _snapshot(db)
    with pytest.raises(approval.ShipmentNotApproved):
        WRITERS[writer]()
    assert _snapshot(db) == before


@pytest.mark.parametrize("writer", WRITERS)
def test_correct_pool_approval_allows_actual_writers(db, writer):
    _approve(db)
    before = _snapshot(db)
    WRITERS[writer]()
    assert _snapshot(db) != before


def test_approval_request_and_decide_use_project_membership(db):
    assert approval.request_approval(71, 17, staff=STAFF)["status"] == "pending"
    assert approval.approve(71, 17, staff=STAFF)["ok"] is True
    assert approval.request_approval(71, 17, staff=STAFF)["status"] == "approved"
    project_writes.add_project_shipment(71, {"tracking_number": "NEW"}, staff=STAFF)
    assert db.execute("SELECT kol_id FROM vkpi_sample_assets").fetchone()[0] == 9
    with pytest.raises(approval.ShipmentNotApproved):
        approval.request_approval(71, 9, staff=STAFF)


def test_wrong_explicit_pool_and_assignment_cannot_borrow_approval(db):
    _approve(db)
    for body in ({"kol_pool_id": 9}, {"assignment_id": 82}, {"kol_pool_id": 18, "assignment_id": 81}):
        with pytest.raises(approval.ShipmentNotApproved):
            project_writes.add_project_shipment(71, {"tracking_number": "NEW", **body}, staff=STAFF)
    assert db.execute("SELECT COUNT(*) FROM vkpi_sample_assets").fetchone()[0] == 0


def test_missing_or_ambiguous_subject_blocks(db):
    _approve(db)
    db.execute("INSERT INTO vkpi_kol_pool VALUES(19,9)")
    with pytest.raises(approval.ShipmentNotApproved, match="ambiguous"):
        project_writes.add_project_shipment(71, {}, staff=STAFF)
    db.execute("UPDATE vkpi_projects SET kol_id=NULL WHERE id=71")
    db.execute("DELETE FROM vkpi_project_kol_assignments WHERE project_id=71")
    with pytest.raises(approval.ShipmentNotApproved, match="missing_or_ambiguous"):
        project_writes.add_project_shipment(71, {}, staff=STAFF)


def test_lookup_error_fails_closed_without_business_writes(db, monkeypatch):
    _approve(db)
    before = _snapshot(db)
    def unavailable(*_a, **_k):
        raise sqlite3.OperationalError("fixture database unavailable")
    monkeypatch.setattr(guard, "resolve_subject", unavailable)
    with pytest.raises(approval.ShipmentNotApproved) as caught:
        project_writes.add_project_shipment(71, {}, staff=STAFF)
    assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
    assert _snapshot(db) == before


def test_same_server_tracking_receipt_progress_remains_available(db):
    db.execute("UPDATE vkpi_shipments SET status='shipped',tracking_number='OLD',shipped_at='before' WHERE id=61")
    updated = shipments.mark_shipment_received(61, {"evidence_url": "https://example.test/receipt", "metadata": {"kol_pool_id": 9}}, staff=STAFF)
    assert updated["status"] == "delivered"
    assert json.loads(updated["metadata_json"])["kol_pool_id"] == 17
    with pytest.raises(approval.ShipmentNotApproved):
        shipments.update_shipment(61, {"tracking_number": "REPLACEMENT", "receipt_only": True}, staff=STAFF)


def test_assignment_same_tracking_updates_do_not_require_new_approval(db):
    db.execute("UPDATE vkpi_project_kol_assignments SET stage='shipped',tracking_number='OLD' WHERE id=81")
    assignments.update_project_kol_shipping(71, 81, {"tracking_number": "OLD", "carrier": "fixture"}, staff=STAFF)
    with pytest.raises(approval.ShipmentNotApproved):
        assignments.update_project_kol_shipping(71, 81, {"tracking_number": "OTHER"}, staff=STAFF)


@pytest.mark.parametrize("body", [{"stage": "shipped"}, {"sample_status": "shipped"}, {"tracking_number": "NEW"}, {"stage": "delivered"}])
def test_create_cannot_start_as_shipment(db, body):
    before = _snapshot(db)
    with pytest.raises(approval.ShipmentNotApproved, match="create_draft"):
        projects.create_project({"project_name": "fixture new", **body}, staff=STAFF)
    assert _snapshot(db) == before


def test_draft_preparation_still_allowed():
    guard.reject_dispatched_creation({"stage": "discovery", "sample_status": "not_required"})


def test_approval_routes_report_invalid_subject(db):
    from app.api.routers import vkpi_projects_fulfillment as routes
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        routes.request_shipping_approval(71, 9, {}, staff=STAFF)
    assert caught.value.status_code == 409


def test_exact_project_shipment_replay_reuses_same_sample_and_receipt(db):
    _approve(db)
    first = project_writes.add_project_shipment(71, {"tracking_number": " SAME "}, staff=STAFF)
    second = project_writes.add_project_shipment(71, {"tracking_number": "SAME"}, staff=STAFF)
    assert first["id"] == second["id"] and second["receipt_reused"] is True
    assert db.execute("SELECT COUNT(*) FROM vkpi_sample_assets").fetchone()[0] == 1


def test_tracking_for_other_creator_never_reassigned(db):
    _approve(db)
    db.execute("UPDATE vkpi_shipments SET tracking_number='OTHER',assignment_id=82,metadata_json='{\"kol_pool_id\":18}' WHERE id=61")
    before = _snapshot(db)
    with pytest.raises(approval.ShipmentNotApproved, match="tracking_identity_conflict"):
        assignments.update_project_kol_shipping(71, 81, {"tracking_number": "OTHER"}, staff=STAFF)
    assert _snapshot(db) == before


@pytest.mark.parametrize("with_update", [True, False])
def test_real_committing_note_audit_runs_only_after_project_transaction(db, monkeypatch, with_update):
    from app.domains.audit import service as audit_service
    from app.domains.projects.workflow_projects_occupancy import _log_project_audit
    db.execute("CREATE TABLE vkpi_business_audit_logs (id INTEGER PRIMARY KEY, staff_id INTEGER, action_type TEXT, target_type TEXT, target_id TEXT, detail TEXT, metadata_json TEXT, created_at TEXT)")
    _approve(db)
    monkeypatch.setattr(projects, "_log_project_audit", _log_project_audit)
    monkeypatch.setattr(assignments.audit, "log_business_event", audit_service.log_business_event)
    monkeypatch.setattr(audit_service, "get_conn", lambda: db)
    monkeypatch.setattr(audit_service, "ensure_vkpi_audit_schema", lambda: None)
    monkeypatch.setattr(audit_service, "_release_validation_fenced", lambda: False)
    sql = []
    db.set_trace_callback(lambda statement: sql.append(statement.strip()))
    db.execute("BEGIN")
    body = {"note": "synthetic note", **({"tracking_number": "NEW"} if with_update else {})}
    result = projects.update_project(71, body, staff=STAFF)
    first_audit = next(i for i, statement in enumerate(sql) if statement.startswith("INSERT INTO vkpi_business_audit_logs"))
    first_commit = sql.index("COMMIT")
    assert first_commit < first_audit
    if with_update:
        project_update = next(i for i, statement in enumerate(sql) if statement.startswith("UPDATE vkpi_projects SET"))
        assert project_update < first_commit
        assert result["status"] == "updated"
    else:
        assert result["status"] == "unchanged"
    assert not db.in_transaction
    assert db.execute("SELECT COUNT(*) FROM vkpi_business_audit_logs WHERE action_type='project_update_note'").fetchone()[0] == 1


@pytest.mark.parametrize("writer", ["assignment_shipping", "assignment_stage"])
def test_locked_assignment_changed_project_cannot_write(db, monkeypatch, writer):
    _approve(db)
    stale = dict(db.execute("SELECT * FROM vkpi_project_kol_assignments WHERE id=81").fetchone())
    db.execute("UPDATE vkpi_project_kol_assignments SET project_id=72 WHERE id=81")
    before = _snapshot(db)
    monkeypatch.setattr(assignments, "_assignment_row", lambda *_a, **_k: stale)
    with pytest.raises(approval.ShipmentNotApproved, match="identity_mismatch"):
        WRITERS[writer]()
    assert _snapshot(db) == before


@pytest.mark.parametrize("body", [{"kol_pool_id": -1}, {"assignment_id": -1}])
def test_negative_identity_is_not_treated_as_infer_from_project(db, body):
    _approve(db)
    with pytest.raises(approval.ShipmentNotApproved, match="invalid_identity"):
        project_writes.add_project_shipment(71, {"tracking_number": "NEW", **body}, staff=STAFF)


class _RollbackOnSqlErrorConnection(guard.PostgresCompatConnection):
    """Only a SQLite-backed simulation of the PG wrapper's rollback-on-error contract."""
    def __init__(self, db, *, fail_metadata=False):
        self.db, self.fail_metadata, self.events = db, fail_metadata, []

    def execute(self, statement, params=()):
        self.events.append(statement.strip())
        try:
            if "pg_attribute" in statement:
                if self.fail_metadata:
                    raise sqlite3.OperationalError("synthetic metadata failure")
                return self.db.execute("SELECT 1 AS present")
            if statement == "SELECT assignment_id FROM vkpi_shipments WHERE 1=0":
                raise sqlite3.OperationalError("unsafe missing-column probe")
            sql = statement.replace(" FOR NO KEY UPDATE", "").replace(" FOR SHARE", "")
            return self.db.execute(sql, params)
        except Exception:
            self.events.append("ROLLBACK")
            self.db.rollback()
            raise

    def commit(self):
        self.events.append("COMMIT")
        self.db.commit()


@pytest.mark.parametrize("fail_metadata", [False, True])
def test_assignment_writer_never_continues_after_metadata_query_rollback(db, monkeypatch, fail_metadata):
    _approve(db)
    conn = _RollbackOnSqlErrorConnection(db, fail_metadata=fail_metadata)
    monkeypatch.setattr(assignments, "get_conn", lambda: conn)
    before = _snapshot(db)
    if fail_metadata:
        with pytest.raises(sqlite3.OperationalError, match="synthetic metadata failure"):
            WRITERS["assignment_shipping"]()
        assert _snapshot(db) == before
        assert conn.events[-1] == "ROLLBACK"
        assert not any(statement.startswith("INSERT INTO vkpi_shipments") for statement in conn.events)
    else:
        WRITERS["assignment_shipping"]()
        assert _snapshot(db) != before and "ROLLBACK" not in conn.events
    assert any("pg_attribute" in statement for statement in conn.events)
    assert "SELECT assignment_id FROM vkpi_shipments WHERE 1=0" not in conn.events


def test_missing_assignment_column_uses_metadata_without_failed_probe(db):
    _approve(db)
    db.execute("ALTER TABLE vkpi_shipments DROP COLUMN assignment_id")
    db.commit()
    sql = []
    db.set_trace_callback(lambda statement: sql.append(statement.strip()))
    WRITERS["assignment_shipping"]()
    row = db.execute("SELECT * FROM vkpi_shipments WHERE tracking_number='NEW'").fetchone()
    assert row["status"] == "shipped"
    assert json.loads(row["metadata_json"])["assignment_id"] == 81
    assert db.execute("SELECT tracking_number FROM vkpi_project_kol_assignments WHERE id=81").fetchone()[0] == "NEW"
    assert "PRAGMA table_info(vkpi_shipments)" in sql
    assert "SELECT assignment_id FROM vkpi_shipments WHERE 1=0" not in sql


def test_approval_decision_without_pending_row_commits_only_after_fallback_insert(db):
    sql = []
    db.set_trace_callback(lambda statement: sql.append(statement.strip()))
    assert approval.approve(71, 17, staff=STAFF)["status"] == "approved"
    update_index = next(i for i, statement in enumerate(sql) if statement.startswith("UPDATE vkpi_shipment_approvals"))
    insert_index = next(i for i, statement in enumerate(sql) if statement.startswith("INSERT INTO vkpi_shipment_approvals"))
    assert update_index < insert_index < sql.index("COMMIT")


@pytest.mark.parametrize("writer", WRITERS)
def test_database_failure_in_approval_read_blocks_every_writer(db, monkeypatch, writer):
    _approve(db)
    before = _snapshot(db)
    original = approval.assert_shippable
    class BrokenApprovalRead:
        def execute(self, *_a, **_k):
            raise sqlite3.OperationalError("fixture query failure")
    monkeypatch.setattr(approval, "assert_shippable", lambda pid, kid, **kw: original(pid, kid, staff=kw.get("staff"), conn=BrokenApprovalRead()))
    with pytest.raises(approval.ShipmentNotApproved) as caught:
        WRITERS[writer]()
    assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
    assert _snapshot(db) == before
