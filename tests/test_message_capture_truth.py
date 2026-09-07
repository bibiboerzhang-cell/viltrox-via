"""Actual capture/read functions against synthetic SQLite; never send messages."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from app.domains.evidence import common, messages
from app.domains.evidence.message_truth import project_message_record
from app.domains.projects import workflow_detail_sections as sections
from app.domains.projects import workflow_evidence_project_writes as project_writes
from app.domains.recommendations import pool_action_bridge

STAFF = {"id": 7, "staff_id": 7, "role": "manager"}
EXPECTED = {
    "record_kind": "manual_capture", "transport_status": "unverified",
    "evidence_class": "manual_record", "claim_status": "descriptive_only",
    "sent": None, "replied": None, "transport_outcome_eligible": False,
}


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE vkpi_projects (id INTEGER PRIMARY KEY,kol_id INTEGER,assigned_staff_id INTEGER,
        created_by_staff_id INTEGER,platform TEXT,project_name TEXT,stage TEXT);
      CREATE TABLE kols (id INTEGER PRIMARY KEY,channel_name TEXT,channel_url TEXT);
      CREATE TABLE staff (id INTEGER PRIMARY KEY,user_id INTEGER);
      CREATE TABLE users (id INTEGER PRIMARY KEY,name TEXT,email TEXT);
      CREATE TABLE vkpi_kol_claims (id INTEGER PRIMARY KEY,kol_id INTEGER,staff_id INTEGER,status TEXT);
      CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY,linked_main_kol_id INTEGER);
      CREATE TABLE vkpi_project_kol_assignments (id INTEGER PRIMARY KEY,project_id INTEGER,kol_pool_id INTEGER,stage_status TEXT);
      CREATE TABLE vkpi_messages (id INTEGER PRIMARY KEY,project_id INTEGER,kol_id INTEGER,staff_id INTEGER,
        source TEXT,direction TEXT,sender TEXT,receiver TEXT,body TEXT,snippet TEXT,evidence_url TEXT,
        follow_up_due_at TEXT,captured_at TEXT,metadata_json TEXT,created_at TEXT);
      CREATE TABLE vkpi_message_attachments (id INTEGER PRIMARY KEY,message_id INTEGER,file_url TEXT,file_type TEXT,metadata_json TEXT,created_at TEXT);
      CREATE TABLE vkpi_content_posts (id INTEGER PRIMARY KEY,project_id INTEGER,published_at TEXT);
      CREATE TABLE vkpi_content_assets (id INTEGER PRIMARY KEY,project_id INTEGER,created_at TEXT);
      CREATE TABLE vkpi_project_terms (id INTEGER PRIMARY KEY,project_id INTEGER);
      CREATE TABLE vkpi_project_deliverables (id INTEGER PRIMARY KEY,project_id INTEGER,due_at TEXT);
      CREATE TABLE vkpi_sample_assets (id INTEGER PRIMARY KEY,project_id INTEGER,created_at TEXT);
      CREATE TABLE vkpi_shipments (id INTEGER PRIMARY KEY,project_id INTEGER,created_at TEXT);
      INSERT INTO vkpi_projects VALUES (71,9,7,7,'youtube','fixture','agreed'),(72,NULL,7,7,'youtube','multi','agreed');
      INSERT INTO kols VALUES (9,'fixture','https://example.test/9'),(10,'other','https://example.test/10');
      INSERT INTO vkpi_kol_pool VALUES (17,9),(18,10),(9,999);
      INSERT INTO vkpi_project_kol_assignments VALUES (81,71,17,'active'),(82,72,18,'active');
    """)
    for module in (messages, common, project_writes):
        monkeypatch.setattr(module, "get_conn", lambda: conn)
        if hasattr(module, "ensure_vkpi_schema"):
            monkeypatch.setattr(module, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(messages.scope, "assert_project_access", lambda *_a, **_k: None)
    monkeypatch.setattr(messages.scope, "can_view_all", lambda *_a, **_k: True)
    monkeypatch.setattr(messages.scope, "effective_staff_id", lambda *_a, **_k: None)
    monkeypatch.setattr(messages, "_actor_id", lambda _: 7)
    monkeypatch.setattr(messages.audit, "log_business_event", lambda **_: None)
    def never_send(*_a, **_k):
        pytest.fail("manual capture called an outreach send/outcome bridge")
    monkeypatch.setattr(pool_action_bridge, "bridge_message_outreach", never_send)
    yield conn
    conn.close()


def _capture(writer, body, project_id=71):
    if writer == "generic":
        return messages.create_message({"project_id": project_id, **body}, staff=STAFF)
    class ForbiddenSink:
        def record_message_outreach(self, **_kwargs):
            pytest.fail("manual project capture called the sent-outcome port")
    return project_writes.add_project_message(project_id, body, staff=STAFF, feedback_sink=ForbiddenSink())


@pytest.mark.parametrize("direction", ["outbound", "inbound", "internal_note", "draft", "unknown", "reply", ""])
@pytest.mark.parametrize("source", ["manual", "email", "provider_verified"])
def test_projection_never_promotes_client_fields_or_mutates_record(direction, source):
    raw = {
        "id": 1, "direction": direction, "source": source, "body": "fixture",
        "evidence_url": "https://example.test/provider/receipt",
        "communication_truth": {"sent": True, "transport_outcome_eligible": True},
        "metadata_json": {"communication_truth": {"sent": True}, "provider_message_id": "fake", "verified": True},
    }
    snapshot = copy.deepcopy(raw)
    projected = project_message_record(raw)
    assert projected is not raw and raw == snapshot
    expected = {**EXPECTED, "record_kind": "internal_note" if direction == "internal_note" else "manual_capture"}
    assert projected["communication_truth"] == expected
    assert projected["source"] == source and projected["metadata_json"] == raw["metadata_json"]


@pytest.mark.parametrize("writer", ["generic", "project"])
@pytest.mark.parametrize("direction", ["outbound", "inbound", "internal_note", "draft", "unknown"])
def test_actual_writes_preserve_capture_and_all_reads_project_unknown(db, writer, direction):
    body = {
        "body": "manual fixture", "source": "email", "direction": direction,
        "captured_at": "2026-09-01T12:00:00Z", "evidence_url": "https://example.test/evidence",
        "communication_truth": {"sent": True},
        "metadata": {"provider_verified": True, "communication_truth": {"replied": True}},
    }
    snapshot = copy.deepcopy(body)
    item = _capture(writer, body)
    assert body == snapshot and not db.in_transaction
    stored = dict(db.execute("SELECT * FROM vkpi_messages WHERE id=?", (item["id"],)).fetchone())
    assert stored["body"] == "manual fixture" and stored["source"] == "email"
    assert stored["direction"] == direction and stored["kol_id"] == 9
    assert json.loads(stored["metadata_json"]) == body["metadata"]
    assert "communication_truth" not in stored
    expected = {**EXPECTED, "record_kind": "internal_note" if direction == "internal_note" else "manual_capture"}
    views = [item, messages.get_message(item["id"], staff=STAFF)["message"],
             messages.list_messages(project_id=71, staff=STAFF)["messages"][0],
             sections.fetch_content_context(db, 71)["messages"][0]]
    assert all(view["communication_truth"] == expected for view in views)


@pytest.mark.parametrize("writer", ["generic", "project"])
def test_screenshot_only_capture_is_preserved(db, writer):
    item = _capture(writer, {"body": "", "evidence_url": "https://example.test/upload", "direction": "internal_note"})
    assert item["body"] == "" and item["evidence_url"]
    assert item["communication_truth"] == {**EXPECTED, "record_kind": "internal_note"}


@pytest.mark.parametrize("writer", ["generic", "project"])
@pytest.mark.parametrize("bad", [{"body": {}}, {"source": []}, {"direction": {}}, {"metadata": []},
                                 {"metadata": "{}"}, {"kol_id": True}, {"kol_id": "bad"},
                                 {"project_id": -1}, {"receiver": {}}, {"captured_at": []}])
def test_invalid_capture_fields_cannot_write(db, writer, bad):
    with pytest.raises(ValueError):
        _capture(writer, bad)
    assert db.execute("SELECT COUNT(*) FROM vkpi_messages").fetchone()[0] == 0


@pytest.mark.parametrize("writer", ["generic", "project"])
def test_explicit_kol_must_match_known_project_main_identity(db, writer):
    for wrong_id in (10, 17):
        with pytest.raises(ValueError, match="membership"):
            _capture(writer, {"body": "fixture", "kol_id": wrong_id})
    assert db.execute("SELECT COUNT(*) FROM vkpi_messages").fetchone()[0] == 0


@pytest.mark.parametrize("writer", ["generic", "project"])
def test_multi_kol_project_accepts_only_server_assigned_main_not_pool_id(db, writer):
    for wrong_id in (9, 18):
        with pytest.raises(ValueError, match="membership"):
            _capture(writer, {"body": "fixture", "kol_id": wrong_id}, project_id=72)
    item = _capture(writer, {"body": "fixture", "kol_id": 10}, project_id=72)
    assert item["kol_id"] == 10 and item["communication_truth"] == EXPECTED


@pytest.mark.parametrize("writer", ["generic", "project"])
def test_deleted_or_unresolvable_membership_cannot_write(db, writer):
    db.execute("UPDATE vkpi_project_kol_assignments SET stage_status='deleted' WHERE project_id=72")
    with pytest.raises(ValueError, match="membership"):
        _capture(writer, {"kol_id": 10}, project_id=72)
    db.execute("DROP TABLE vkpi_project_kol_assignments")
    with pytest.raises(sqlite3.OperationalError):
        _capture(writer, {"kol_id": 10}, project_id=72)
    assert db.execute("SELECT COUNT(*) FROM vkpi_messages").fetchone()[0] == 0


def test_project_path_body_conflict_and_routes_return_safe_validation_errors(db):
    from fastapi import HTTPException
    from app.api.routers import vkpi_evidence_assets, vkpi_projects
    with pytest.raises(HTTPException) as mismatch:
        vkpi_projects.add_project_message(71, {"project_id": 72, "body": "PRIVATE CONTENT"}, staff=STAFF)
    assert mismatch.value.status_code == 400 and "PRIVATE CONTENT" not in mismatch.value.detail
    with pytest.raises(HTTPException) as invalid:
        vkpi_evidence_assets.create_message({"project_id": 71, "body": {"secret": "PRIVATE CONTENT"}}, staff=STAFF)
    assert invalid.value.status_code == 400 and "PRIVATE CONTENT" not in invalid.value.detail
    assert db.execute("SELECT COUNT(*) FROM vkpi_messages").fetchone()[0] == 0


def test_legacy_json_and_forged_top_level_flags_are_overridden():
    for metadata in ('{"communication_truth":{"sent":true}}', "invalid-json", [], None):
        raw = {"direction": "outbound", "metadata_json": metadata, "communication_truth": {"sent": True}}
        assert project_message_record(raw)["communication_truth"] == EXPECTED


@pytest.mark.parametrize("writer", ["generic", "project"])
@pytest.mark.parametrize("identity", [
    {"kol_id": 10}, {"assignment_id": 83}, {"kol_pool_id": 18},
    {"metadata": {"assignment_id": 83, "kol_pool_id": 18}},
    {"kol_id": 10, "metadata": {"assignment_id": 83, "kol_pool_id": 18, "kol_id": 10}},
])
def test_primary_project_can_capture_second_member_without_silently_using_primary(db, writer, identity):
    db.execute("INSERT INTO vkpi_project_kol_assignments VALUES (83,71,18,'active')")
    body = {"body": "second member fixture", **identity}
    saved_input = copy.deepcopy(body)
    item = _capture(writer, body)
    assert body == saved_input
    assert item["kol_id"] == 10 and item["project_id"] == 71
    assert item["communication_truth"] == EXPECTED
    row = db.execute("SELECT kol_id FROM vkpi_messages WHERE id=?", (item["id"],)).fetchone()
    assert row["kol_id"] == 10


@pytest.mark.parametrize("writer", ["generic", "project"])
@pytest.mark.parametrize("identity", [
    {"metadata": {"assignment_id": 82, "kol_pool_id": 18}},
    {"metadata": {"assignment_id": 81, "kol_pool_id": 18}},
    {"kol_id": 9, "metadata": {"assignment_id": 83, "kol_pool_id": 18}},
    {"assignment_id": 81, "metadata": {"assignment_id": 83}},
    {"kol_pool_id": 17, "metadata": {"kol_pool_id": 18}},
    {"kol_id": 9, "metadata": {"kol_id": 10}},
    {"metadata": {"project_id": 72, "assignment_id": 82}},
    {"metadata": {"assignment_id": "bad"}},
    {"metadata": {"kol_pool_id": True}},
])
def test_explicit_target_conflicts_and_other_project_assignments_never_write(db, writer, identity):
    db.execute("INSERT INTO vkpi_project_kol_assignments VALUES (83,71,18,'active')")
    with pytest.raises(ValueError):
        _capture(writer, {"body": "must not attach to primary", **identity})
    assert db.execute("SELECT COUNT(*) FROM vkpi_messages").fetchone()[0] == 0


@pytest.mark.parametrize("writer", ["generic", "project"])
def test_assignment_without_main_link_never_falls_back_to_project_primary(db, writer):
    db.execute("INSERT INTO vkpi_kol_pool VALUES (19,NULL)")
    db.execute("INSERT INTO vkpi_project_kol_assignments VALUES (83,71,19,'active')")
    with pytest.raises(ValueError, match="membership"):
        _capture(writer, {"metadata": {"assignment_id": 83, "kol_pool_id": 19}})
    assert db.execute("SELECT COUNT(*) FROM vkpi_messages").fetchone()[0] == 0
