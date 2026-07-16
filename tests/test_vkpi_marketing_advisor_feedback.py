from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies.advisor_scope import (
    require_advisor_read_scope,
    require_advisor_write_scope,
)
from app.api.routers import vkpi_marketing_advisor
from app.domains.advisor import repository
from app.domains.advisor.scope import AdvisorScope


class _SqliteCompat:
    def __init__(self, raw: sqlite3.Connection) -> None:
        self.raw = raw

    @staticmethod
    def _sql(sql: str) -> str:
        value = sql.replace("?::jsonb", "?").replace("NOW()", "CURRENT_TIMESTAMP")
        return re.sub(r"\s+FOR\s+UPDATE\b", "", value, flags=re.IGNORECASE)

    def execute(self, sql: str, params=()):
        return self.raw.execute(self._sql(sql), tuple(params or ()))

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()


_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE organizations (id INTEGER PRIMARY KEY);
CREATE TABLE staff (id INTEGER PRIMARY KEY);
CREATE TABLE vkpi_advisor_threads (
 id INTEGER PRIMARY KEY AUTOINCREMENT, thread_uid TEXT NOT NULL,
 organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'active', context_refs_json TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_message_at TEXT, archived_at TEXT, deleted_at TEXT,
 UNIQUE(organization_id, staff_id, thread_uid)
);
CREATE TABLE vkpi_advisor_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, message_uid TEXT NOT NULL,
 organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, thread_uid TEXT NOT NULL,
 role TEXT NOT NULL, content_text TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ready',
 provider_status TEXT NOT NULL DEFAULT 'not_requested', provider_reason TEXT NOT NULL DEFAULT '',
 context_refs_json TEXT NOT NULL DEFAULT '[]', provenance_json TEXT NOT NULL DEFAULT '{}',
 metadata_json TEXT NOT NULL DEFAULT '{}', client_request_id TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, deleted_at TEXT,
 UNIQUE(organization_id, staff_id, message_uid),
 FOREIGN KEY(organization_id, staff_id, thread_uid)
   REFERENCES vkpi_advisor_threads(organization_id, staff_id, thread_uid)
);
CREATE TABLE vkpi_advisor_memory_settings (
 organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'active',
 retention_days INTEGER NOT NULL DEFAULT 180, updated_by_staff_id INTEGER,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(organization_id, staff_id)
);
CREATE TABLE vkpi_advisor_memory_candidates (
 id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_uid TEXT NOT NULL,
 organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, source_message_uid TEXT,
 memory_kind TEXT NOT NULL, memory_key TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
 value_json TEXT NOT NULL DEFAULT '{}', provenance_json TEXT NOT NULL DEFAULT '{}',
 sensitivity TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'pending',
 confirmed_fact_uid TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 reviewed_at TEXT, deleted_at TEXT, UNIQUE(organization_id, staff_id, candidate_uid),
 FOREIGN KEY(organization_id, staff_id)
   REFERENCES vkpi_advisor_memory_settings(organization_id, staff_id)
);
CREATE TABLE vkpi_advisor_memory_facts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fact_uid TEXT NOT NULL,
 organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, source_candidate_uid TEXT,
 memory_kind TEXT NOT NULL, memory_key TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
 value_json TEXT NOT NULL DEFAULT '{}', provenance_json TEXT NOT NULL DEFAULT '{}',
 sensitivity TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 deleted_at TEXT, UNIQUE(organization_id, staff_id, fact_uid)
);
CREATE TABLE vkpi_advisor_memory_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL,
 actor_staff_id INTEGER, event_type TEXT NOT NULL, subject_type TEXT NOT NULL,
 subject_uid TEXT NOT NULL, before_sha256 TEXT NOT NULL DEFAULT '', after_sha256 TEXT NOT NULL DEFAULT '',
 detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE vkpi_advisor_message_feedback (
 id INTEGER PRIMARY KEY AUTOINCREMENT, feedback_uid TEXT NOT NULL,
 organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, thread_uid TEXT NOT NULL,
 message_uid TEXT NOT NULL, rating TEXT NOT NULL, correction_text TEXT NOT NULL DEFAULT '',
 propose_memory INTEGER NOT NULL DEFAULT 0, context_refs_json TEXT NOT NULL DEFAULT '[]',
 provenance_json TEXT NOT NULL DEFAULT '{}', candidate_uid TEXT,
 last_client_request_id TEXT NOT NULL DEFAULT '', payload_sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(organization_id, staff_id, feedback_uid),
 UNIQUE(organization_id, staff_id, message_uid),
 FOREIGN KEY(organization_id, staff_id, thread_uid)
   REFERENCES vkpi_advisor_threads(organization_id, staff_id, thread_uid),
 FOREIGN KEY(organization_id, staff_id, message_uid)
   REFERENCES vkpi_advisor_messages(organization_id, staff_id, message_uid),
 FOREIGN KEY(organization_id, staff_id, candidate_uid)
   REFERENCES vkpi_advisor_memory_candidates(organization_id, staff_id, candidate_uid)
);
CREATE UNIQUE INDEX uq_advisor_feedback_request_test
 ON vkpi_advisor_message_feedback(organization_id, staff_id, last_client_request_id)
 WHERE last_client_request_id <> '';
CREATE TABLE vkpi_advisor_message_feedback_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_uid TEXT NOT NULL, feedback_uid TEXT NOT NULL,
 organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, thread_uid TEXT NOT NULL,
 message_uid TEXT NOT NULL, actor_staff_id INTEGER, event_type TEXT NOT NULL,
 client_request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
 before_sha256 TEXT NOT NULL DEFAULT '', after_sha256 TEXT NOT NULL,
 detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(organization_id, staff_id, event_uid),
 UNIQUE(organization_id, staff_id, client_request_id),
 FOREIGN KEY(organization_id, staff_id, feedback_uid)
   REFERENCES vkpi_advisor_message_feedback(organization_id, staff_id, feedback_uid)
);
"""


@pytest.fixture()
def feedback_db(monkeypatch: pytest.MonkeyPatch):
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.executescript(_SCHEMA)
    raw.executemany("INSERT INTO organizations(id) VALUES (?)", [(1,), (2,)])
    raw.executemany("INSERT INTO staff(id) VALUES (?)", [(11,), (12,), (21,)])
    raw.commit()
    compat = _SqliteCompat(raw)
    monkeypatch.setattr(repository, "get_conn", lambda: compat)
    monkeypatch.setattr(
        repository,
        "table_exists",
        lambda name: name in {"vkpi_advisor_threads", "vkpi_advisor_message_feedback"},
    )
    try:
        yield compat
    finally:
        raw.close()


def _scope(org: int, staff: int) -> AdvisorScope:
    return AdvisorScope(organization_id=org, staff_id=staff, user_id=staff + 1000)


def _message(
    db: _SqliteCompat,
    scope: AdvisorScope,
    thread_uid: str,
    message_uid: str,
    role: str,
    context_refs: list[dict] | None = None,
) -> None:
    db.execute(
        "INSERT INTO vkpi_advisor_messages "
        "(message_uid, organization_id, staff_id, thread_uid, role, content_text, context_refs_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            message_uid,
            scope.organization_id,
            scope.staff_id,
            thread_uid,
            role,
            f"{role} text",
            json.dumps(context_refs or []),
        ),
    )
    db.commit()


def test_feedback_is_owner_scoped_idempotent_and_only_proposes_pending_memory(feedback_db) -> None:
    owner = _scope(1, 11)
    thread = repository.create_thread(owner, title="feedback")
    context_refs = [{
        "entity_type": "kol",
        "entity_id": "kol-42",
        "snapshot": {"label": "Creator 42", "platform": "youtube"},
        "provenance": {"source_ref": "vkpi_kol_pool:kol-42"},
    }]
    _message(
        feedback_db,
        owner,
        thread["thread_uid"],
        "assistant-1",
        "assistant",
        context_refs=context_refs,
    )

    created = repository.submit_message_feedback(
        owner,
        thread["thread_uid"],
        "assistant-1",
        rating="unhelpful",
        correction_text="Prefer overseas camera educators with verified recent activity.",
        propose_memory=True,
        context_refs=context_refs,
        provenance={"source_ref": "explicit:advisor-feedback"},
        client_request_id="feedback-1",
    )
    assert created["feedback"]["context_refs_json"][0]["entity_id"] == "kol-42"
    assert created["candidate"]["status"] == "pending"
    assert repository.get_memory(owner)["facts"] == []

    replay = repository.submit_message_feedback(
        owner,
        thread["thread_uid"],
        "assistant-1",
        rating="unhelpful",
        correction_text="Prefer overseas camera educators with verified recent activity.",
        propose_memory=True,
        context_refs=context_refs,
        provenance={"source_ref": "explicit:advisor-feedback"},
        client_request_id="feedback-1",
    )
    assert replay["idempotent_replay"] is True
    assert replay["candidate"]["candidate_uid"] == created["candidate"]["candidate_uid"]
    counts = dict(feedback_db.execute(
        "SELECT (SELECT COUNT(*) FROM vkpi_advisor_message_feedback) AS feedback_count, "
        "(SELECT COUNT(*) FROM vkpi_advisor_message_feedback_events) AS event_count, "
        "(SELECT COUNT(*) FROM vkpi_advisor_memory_candidates) AS candidate_count, "
        "(SELECT COUNT(*) FROM vkpi_advisor_memory_facts) AS fact_count"
    ).fetchone())
    assert counts == {"feedback_count": 1, "event_count": 1, "candidate_count": 1, "fact_count": 0}
    assert repository.list_messages(owner, thread["thread_uid"])[0]["feedback"]["rating"] == "unhelpful"

    for outsider in (_scope(1, 12), _scope(2, 21)):
        with pytest.raises(repository.AdvisorNotFound):
            repository.submit_message_feedback(
                outsider,
                thread["thread_uid"],
                "assistant-1",
                rating="helpful",
                client_request_id=f"outsider-{outsider.organization_id}-{outsider.staff_id}",
            )

    fact = repository.confirm_memory_candidate(owner, created["candidate"]["candidate_uid"])
    assert fact["status"] == "active"
    assert repository.get_memory(owner)["facts"][0]["fact_uid"] == fact["fact_uid"]


def test_feedback_binds_message_context_and_withdraws_pending_candidate(feedback_db) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="bound context")
    original_refs = [{
        "entity_type": "project",
        "entity_id": "project-7",
        "snapshot": {"label": "Original project"},
    }]
    _message(
        feedback_db,
        scope,
        thread["thread_uid"],
        "assistant-bound",
        "assistant",
        context_refs=original_refs,
    )

    created = repository.submit_message_feedback(
        scope,
        thread["thread_uid"],
        "assistant-bound",
        rating="unhelpful",
        correction_text="Use the verified project brief.",
        propose_memory=True,
        context_refs=[{"entity_type": "kol", "entity_id": "picker-changed"}],
        client_request_id="bound-create",
    )
    candidate_uid = created["candidate"]["candidate_uid"]
    assert created["feedback"]["context_refs_json"][0]["entity_id"] == "project-7"
    assert created["feedback"]["context_refs_json"][0]["snapshot"]["label"] == "Original project"

    withdrawn = repository.submit_message_feedback(
        scope,
        thread["thread_uid"],
        "assistant-bound",
        rating="helpful",
        context_refs=[{"entity_type": "dealer", "entity_id": "picker-changed-again"}],
        client_request_id="bound-withdraw",
    )
    assert withdrawn["feedback"]["candidate_uid"] is None
    assert withdrawn["candidate"]["status"] == "rejected"
    status = feedback_db.execute(
        "SELECT status FROM vkpi_advisor_memory_candidates WHERE candidate_uid=?",
        (candidate_uid,),
    ).fetchone()["status"]
    assert status == "rejected"


def test_feedback_rejects_user_messages_and_payload_mismatched_replays(feedback_db) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="validation")
    _message(feedback_db, scope, thread["thread_uid"], "user-1", "user")
    _message(feedback_db, scope, thread["thread_uid"], "assistant-2", "assistant")

    with pytest.raises(repository.AdvisorValidationError, match="client_request_id"):
        repository.submit_message_feedback(
            scope, thread["thread_uid"], "assistant-2", rating="helpful"
        )
    with pytest.raises(repository.AdvisorValidationError, match="assistant messages"):
        repository.submit_message_feedback(
            scope, thread["thread_uid"], "user-1", rating="unhelpful", client_request_id="bad-role"
        )
    with pytest.raises(repository.AdvisorValidationError, match="correction_text"):
        repository.submit_message_feedback(
            scope,
            thread["thread_uid"],
            "assistant-2",
            rating="unhelpful",
            propose_memory=True,
            client_request_id="missing-correction",
        )

    repository.submit_message_feedback(
        scope,
        thread["thread_uid"],
        "assistant-2",
        rating="helpful",
        client_request_id="payload-bound-feedback",
    )
    with pytest.raises(repository.AdvisorConflict, match="different feedback payload"):
        repository.submit_message_feedback(
            scope,
            thread["thread_uid"],
            "assistant-2",
            rating="unhelpful",
            client_request_id="payload-bound-feedback",
        )


def test_feedback_api_preserves_pending_confirmation_boundary(feedback_db) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="api")
    _message(feedback_db, scope, thread["thread_uid"], "assistant-api", "assistant")
    app = FastAPI()
    app.include_router(vkpi_marketing_advisor.router)
    app.dependency_overrides[require_advisor_read_scope] = lambda: scope
    app.dependency_overrides[require_advisor_write_scope] = lambda: scope
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        f"/api/admin/vkpi/marketing-advisor/threads/{thread['thread_uid']}/messages/assistant-api/feedback",
        json={
            "rating": "unhelpful",
            "correction_text": "Prefer a verified overseas creator.",
            "propose_memory": True,
            "client_request_id": "api-feedback-1",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending_confirmation"
    assert payload["candidate"]["status"] == "pending"
    assert payload["memory_active"] is False
    assert payload["training_triggered"] is False
    assert payload["weights_changed"] is False


def test_feedback_migration_and_route_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    sql = (root / "migrations" / "268_vkpi_advisor_trusted_feedback.sql").read_text()
    assert "UNIQUE (organization_id, staff_id, message_uid)" in sql
    assert "FOREIGN KEY (organization_id, staff_id, message_uid)" in sql
    assert "explicit confirmation" in sql.lower()
    paths = {getattr(route, "path", "") for route in vkpi_marketing_advisor.router.routes}
    assert "/api/admin/vkpi/marketing-advisor/threads/{thread_uid}/messages/{message_uid}/feedback" in paths
