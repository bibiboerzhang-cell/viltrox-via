from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_intelligent, vkpi_marketing_advisor
from app.domains.advisor import intelligent_bridge, repository, service
from app.domains.advisor.scope import AdvisorScope, AdvisorScopeError, advisor_scope_from_staff


class _SqliteCompat:
    """Tiny test adapter for the repository's Postgres-compatible SQL subset."""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self.raw = raw

    @staticmethod
    def _sql(sql: str) -> str:
        value = sql.replace("?::jsonb", "?").replace("NOW()", "CURRENT_TIMESTAMP")
        value = re.sub(r"\s+FOR\s+UPDATE\b", "", value, flags=re.IGNORECASE)
        return value

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
CREATE UNIQUE INDEX uq_advisor_message_request_test
 ON vkpi_advisor_messages(organization_id, staff_id, thread_uid, client_request_id)
 WHERE client_request_id <> '' AND role='user' AND deleted_at IS NULL;
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
 deleted_at TEXT, UNIQUE(organization_id, staff_id, fact_uid),
 FOREIGN KEY(organization_id, staff_id)
   REFERENCES vkpi_advisor_memory_settings(organization_id, staff_id)
);
CREATE UNIQUE INDEX uq_advisor_memory_fact_key_test
 ON vkpi_advisor_memory_facts(organization_id, staff_id, memory_key) WHERE deleted_at IS NULL;
CREATE TABLE vkpi_advisor_action_drafts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, draft_uid TEXT NOT NULL,
 organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, thread_uid TEXT NOT NULL,
 source_message_uid TEXT NOT NULL, action_type TEXT NOT NULL, target_type TEXT NOT NULL DEFAULT '',
 target_id TEXT NOT NULL DEFAULT '', estimated_cost_cents INTEGER NOT NULL DEFAULT 0,
 writes_business_data INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL DEFAULT '{}',
 provenance_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'draft',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, cancelled_at TEXT,
 UNIQUE(organization_id, staff_id, draft_uid)
);
CREATE TABLE vkpi_advisor_memory_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL,
 actor_staff_id INTEGER, event_type TEXT NOT NULL, subject_type TEXT NOT NULL,
 subject_uid TEXT NOT NULL, before_sha256 TEXT NOT NULL DEFAULT '', after_sha256 TEXT NOT NULL DEFAULT '',
 detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE vkpi_advisor_turn_claims (
 organization_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, thread_uid TEXT NOT NULL,
 client_request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL, claim_token_sha256 TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'claimed', provider_attempted BOOLEAN NOT NULL DEFAULT FALSE,
 provider_binding TEXT NOT NULL DEFAULT '', failure_code TEXT NOT NULL DEFAULT '',
 result_user_message_uid TEXT, result_assistant_message_uid TEXT,
 claimed_at TEXT NOT NULL, lease_expires_at TEXT NOT NULL, provider_started_at TEXT,
 completed_at TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(organization_id, staff_id, thread_uid, client_request_id),
 FOREIGN KEY(organization_id, staff_id, thread_uid)
   REFERENCES vkpi_advisor_threads(organization_id, staff_id, thread_uid)
);
"""


@pytest.fixture()
def advisor_db(monkeypatch: pytest.MonkeyPatch):
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.executescript(_SCHEMA)
    raw.executemany("INSERT INTO organizations(id) VALUES (?)", [(1,), (2,)])
    raw.executemany("INSERT INTO staff(id) VALUES (?)", [(11,), (12,), (21,), (22,)])
    raw.commit()
    compat = _SqliteCompat(raw)
    monkeypatch.setattr(repository, "get_conn", lambda: compat)
    monkeypatch.setattr(
        repository,
        "table_exists",
        lambda name: name in {"vkpi_advisor_threads", "vkpi_advisor_turn_claims"},
    )
    try:
        yield compat
    finally:
        raw.close()


def _scope(org: int, staff: int) -> AdvisorScope:
    return AdvisorScope(organization_id=org, staff_id=staff, user_id=staff + 1000)


def test_advisor_scope_is_fail_closed_for_missing_or_ambiguous_org() -> None:
    resolved = advisor_scope_from_staff(
        {
            "id": 11,
            "user_id": 1011,
            "organization_id": 1,
            "organization_scope_status": "resolved",
        }
    )
    assert resolved == _scope(1, 11)

    for status in ("", "ambiguous", "membership_missing", "lookup_failed"):
        with pytest.raises(AdvisorScopeError):
            advisor_scope_from_staff(
                {
                    "id": 11,
                    "user_id": 1011,
                    "organization_id": 1,
                    "organization_scope_status": status,
                }
            )


def test_threads_and_messages_are_isolated_between_users_and_orgs(advisor_db) -> None:
    user_a = _scope(1, 11)
    user_b = _scope(1, 12)
    org_b = _scope(2, 21)
    thread_a = repository.create_thread(user_a, title="A private")
    thread_b = repository.create_thread(user_b, title="B private")
    thread_org_b = repository.create_thread(org_b, title="Org B private")

    assert [item["thread_uid"] for item in repository.list_threads(user_a)] == [thread_a["thread_uid"]]
    assert [item["thread_uid"] for item in repository.list_threads(user_b)] == [thread_b["thread_uid"]]
    assert [item["thread_uid"] for item in repository.list_threads(org_b)] == [thread_org_b["thread_uid"]]
    with pytest.raises(repository.AdvisorNotFound):
        repository.get_thread(user_b, thread_a["thread_uid"])
    with pytest.raises(repository.AdvisorNotFound):
        repository.get_thread(org_b, thread_a["thread_uid"])

    service.create_message_turn(user_a, thread_a["thread_uid"], content="Private A")
    assert len(repository.list_messages(user_a, thread_a["thread_uid"])) == 2
    with pytest.raises(repository.AdvisorNotFound):
        repository.list_messages(user_b, thread_a["thread_uid"])


def test_message_limit_returns_latest_window_in_chronological_order(advisor_db) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="Long conversation")
    thread_uid = thread["thread_uid"]

    for turn in range(1, 5):
        service.create_message_turn(
            scope,
            thread_uid,
            content=f"question-{turn}",
            client_request_id=f"latest-window-{turn}",
        )

    all_messages = repository.list_messages(scope, thread_uid, limit=500)
    assert len(all_messages) == 8

    latest_three = repository.list_messages(scope, thread_uid, limit=3)
    assert [item["id"] for item in latest_three] == [
        item["id"] for item in all_messages[-3:]
    ]
    assert [item["id"] for item in latest_three] == sorted(
        item["id"] for item in latest_three
    )
    assert latest_three[-1]["role"] == "assistant"

    latest_one = repository.list_messages(scope, thread_uid, limit=1)
    assert [item["id"] for item in latest_one] == [all_messages[-1]["id"]]


def test_message_limit_is_bounded_before_it_reaches_sql() -> None:
    assert repository._positive_limit(-10, default=100, maximum=500) == 1
    assert repository._positive_limit(501, default=100, maximum=500) == 500
    assert repository._positive_limit("invalid", default=100, maximum=500) == 100


def test_message_degrades_without_provider_and_actions_stay_draft(advisor_db) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(
        scope,
        title="KOL consultation",
        context_refs=[
            {
                "entity_type": "kol",
                "entity_id": "1364",
                "snapshot": {"label": "creator", "platform": "youtube", "extra": "drop"},
                "provenance": {"source_ref": "vkpi_kol_pool:1364"},
            }
        ],
    )
    out = service.create_message_turn(
        scope,
        thread["thread_uid"],
        content="Send an outreach and book a paid collaboration",
        client_request_id="req-1",
        requested_actions=[
            {
                "action_type": "incur_cost",
                "target_type": "kol",
                "target_id": "1364",
                "estimated_cost_cents": 50000,
                "payload": {"brief": "draft only"},
            }
        ],
    )

    assert out["status"] == "degraded"
    assert out["provider"]["provider_called"] is False
    assert out["reason"] == "advisor_external_ai_not_requested"
    assert out["provider"]["reason"] == "advisor_external_ai_operator_disabled"
    assert out["messages"][1]["provider_status"] == "not_requested"
    assert out["draft_actions"][0]["status"] == "draft"
    assert out["draft_actions"][0]["estimated_cost_cents"] == 50000
    assert out["draft_actions"][0]["writes_business_data"] is False
    assert set(thread["context_refs_json"][0]["snapshot"]) == {"label", "platform", "observed_at"}

    replay = service.create_message_turn(
        scope,
        thread["thread_uid"],
        content="Send an outreach and book a paid collaboration",
        client_request_id="req-1",
        requested_actions=[
            {
                "action_type": "incur_cost",
                "target_type": "kol",
                "target_id": "1364",
                "estimated_cost_cents": 50000,
                "payload": {"brief": "draft only"},
            }
        ],
    )
    assert replay["idempotent_replay"] is True
    assert len(repository.list_messages(scope, thread["thread_uid"])) == 2
    assert len(repository.list_action_drafts(scope)) == 1


def test_memory_requires_explicit_confirmation_and_remains_owner_scoped(advisor_db) -> None:
    owner = _scope(1, 11)
    other = _scope(1, 12)
    candidate = repository.create_memory_candidate(
        owner,
        memory_kind="preference",
        memory_key="output.language",
        summary="Answer in English",
        value={"language": "en"},
        provenance={"source_ref": "explicit:user-setting"},
    )
    snapshot = repository.get_memory(owner)
    assert snapshot["facts"] == []
    assert snapshot["candidates"][0]["status"] == "pending"
    with pytest.raises(repository.AdvisorNotFound):
        repository.confirm_memory_candidate(other, candidate["candidate_uid"])

    fact = repository.confirm_memory_candidate(owner, candidate["candidate_uid"])
    assert fact["status"] == "active"
    assert repository.get_memory(owner)["facts"][0]["memory_key"] == "output.language"
    assert repository.get_memory(other)["facts"] == []

    paused_fact = repository.update_memory_fact(owner, fact["fact_uid"], status="paused")
    assert paused_fact["status"] == "paused"
    edited_fact = repository.update_memory_fact(
        owner,
        fact["fact_uid"],
        summary="Answer in concise English",
    )
    assert edited_fact["summary"] == "Answer in concise English"
    assert edited_fact["version"] == 3

    pending_before_pause = repository.create_memory_candidate(
        owner,
        memory_kind="constraint",
        memory_key="approval.required",
        summary="Require approval",
        value={"required": True},
        provenance={"source_ref": "explicit:user-setting"},
    )
    settings = repository.update_memory_settings(owner, state="paused", retention_days=30)
    assert settings["state"] == "paused"
    with pytest.raises(repository.AdvisorConflict, match="personal memory is paused"):
        repository.confirm_memory_candidate(owner, pending_before_pause["candidate_uid"])
    with pytest.raises(repository.AdvisorConflict):
        repository.create_memory_candidate(
            owner,
            memory_kind="constraint",
            memory_key="budget.limit",
            summary="Do not exceed budget",
            value={"cents": 100},
            provenance={},
        )
    deleted = repository.delete_memory_fact(owner, fact["fact_uid"])
    assert deleted["status"] == "deleted"
    assert repository.get_memory(owner)["facts"] == []


def test_memory_retention_window_filters_without_physical_delete(advisor_db) -> None:
    owner = _scope(1, 11)
    confirmed_candidate = repository.create_memory_candidate(
        owner,
        memory_kind="preference",
        memory_key="old.confirmed",
        summary="Old confirmed memory",
        value={"old": True},
        provenance={"source_ref": "explicit:user-setting"},
    )
    fact = repository.confirm_memory_candidate(owner, confirmed_candidate["candidate_uid"])
    pending_candidate = repository.create_memory_candidate(
        owner,
        memory_kind="constraint",
        memory_key="old.pending",
        summary="Old pending memory",
        value={"old": True},
        provenance={"source_ref": "explicit:user-setting"},
    )
    repository.update_memory_settings(owner, state="active", retention_days=30)
    advisor_db.execute(
        "UPDATE vkpi_advisor_memory_candidates SET created_at='2000-01-01 00:00:00' "
        "WHERE organization_id=? AND staff_id=?",
        (owner.organization_id, owner.staff_id),
    )
    advisor_db.execute(
        "UPDATE vkpi_advisor_memory_facts SET updated_at='2000-01-01 00:00:00' "
        "WHERE organization_id=? AND staff_id=?",
        (owner.organization_id, owner.staff_id),
    )
    advisor_db.commit()

    snapshot = repository.get_memory(owner)

    assert snapshot["candidates"] == []
    assert snapshot["facts"] == []
    assert snapshot["retention_policy"] == {
        "mode": "read_window",
        "retention_days": 30,
        "cutoff_at": snapshot["retention_policy"]["cutoff_at"],
        "candidate_clock": "created_at",
        "fact_clock": "updated_at",
        "expired_rows_returned": False,
        "physical_delete_performed": False,
    }
    assert advisor_db.execute(
        "SELECT COUNT(*) FROM vkpi_advisor_memory_candidates WHERE organization_id=? AND staff_id=?",
        (owner.organization_id, owner.staff_id),
    ).fetchone()[0] == 2
    assert advisor_db.execute(
        "SELECT COUNT(*) FROM vkpi_advisor_memory_facts WHERE organization_id=? AND staff_id=?",
        (owner.organization_id, owner.staff_id),
    ).fetchone()[0] == 1
    with pytest.raises(repository.AdvisorNotFound, match="outside retention window"):
        repository.confirm_memory_candidate(owner, pending_candidate["candidate_uid"])
    assert fact["fact_uid"]


def test_readiness_and_message_route_return_non_500_degraded_contract(advisor_db) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="readiness")
    ready = vkpi_marketing_advisor.advisor_readiness(scope)
    assert ready == service.readiness()
    assert ready["status"] == "degraded"
    assert ready["provider_called"] is False
    assert ready["budget_authorized"] is False
    assert ready["knowledge_bridge_ready"] is True
    assert ready["knowledge_bridge_reason"] == ""
    assert ready["knowledge_bridge_mode"] == "advisor_owner_scope_v1"
    assert ready["core_status"] == "ready"
    assert ready["external_ai_status"] == "blocked"
    assert ready["ai_off_path_ready"] is True
    assert ready["external_ai_ready"] is False
    assert ready["capabilities"]["local_context_recall"] == {
        "ready": True,
        "provider_required": False,
        "provider_calls_allowed": False,
    }
    assert ready["capabilities"]["model_generated_advice"] == {
        "ready": False,
        "provider_required": True,
        "reason": "advisor_external_model_generation_blocked",
    }
    assert ready["capabilities"]["business_actions"]["execution_allowed"] is False
    assert ready["provider_connectivity"]["provider_called"] is False
    assert ready["provider_connectivity"]["exact_model_evidence"] is False
    assert ready["exact_model_evidence"]["production_ready"] is False
    assert ready["provider_called"] is False

    response = vkpi_marketing_advisor.create_message(
        thread["thread_uid"],
        vkpi_marketing_advisor.MessageCreateBody(content="How should I proceed?"),
        scope,
    )
    assert response["status"] == "degraded"
    assert response["messages"][1]["status"] == "degraded"


def test_http_message_endpoint_persists_and_returns_200_degraded(advisor_db) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.dependencies.advisor_scope import (
        require_advisor_read_scope,
        require_advisor_write_scope,
    )

    scope = _scope(1, 11)
    app = FastAPI()
    app.include_router(vkpi_marketing_advisor.router)
    app.dependency_overrides[require_advisor_read_scope] = lambda: scope
    app.dependency_overrides[require_advisor_write_scope] = lambda: scope
    client = TestClient(app, raise_server_exceptions=False)

    created = client.post(
        "/api/admin/vkpi/marketing-advisor/threads",
        json={"title": "HTTP contract"},
    )
    assert created.status_code == 200
    thread_uid = created.json()["thread"]["thread_uid"]
    response = client.post(
        f"/api/admin/vkpi/marketing-advisor/threads/{thread_uid}/messages",
        json={
            "content": "Draft a paid outreach",
            "client_request_id": "http-1",
            "requested_actions": [
                {
                    "action_type": "incur_cost",
                    "target_type": "kol",
                    "target_id": "1364",
                    "estimated_cost_cents": 25000,
                }
            ],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["provider"]["provider_called"] is False
    assert payload["draft_actions"][0]["status"] == "draft"
    assert client.get(
        f"/api/admin/vkpi/marketing-advisor/threads/{thread_uid}/messages"
    ).json()["count"] == 2

    candidate_response = client.post(
        "/api/admin/vkpi/marketing-advisor/memory/candidates",
        json={
            "memory_kind": "constraint",
            "memory_key": "approval.required",
            "summary": "Require approval",
            "value": {"required": True},
            "provenance": {"source_ref": "explicit:user-setting"},
        },
    )
    assert candidate_response.status_code == 200
    candidate_uid = candidate_response.json()["candidate"]["candidate_uid"]
    paused_response = client.patch(
        "/api/admin/vkpi/marketing-advisor/memory/settings",
        json={"state": "paused", "retention_days": 180},
    )
    assert paused_response.status_code == 200
    confirm_response = client.post(
        f"/api/admin/vkpi/marketing-advisor/memory/candidates/{candidate_uid}/confirm",
        json={},
    )
    assert confirm_response.status_code == 409
    assert confirm_response.json()["detail"]["code"] == "advisor_conflict"


def test_default_intelligent_bridge_is_owner_scoped_without_search_or_cost(advisor_db) -> None:
    result = intelligent_bridge.answer("find creators", _scope(1, 11))
    assert result["status"] == "ready"
    assert result["mode"] == "advisor_owner_scope_v1"
    assert result["reason"] == ""
    assert result["evidence"] == []
    assert result["scope_enforced"] is True
    assert intelligent_bridge.readiness()["cost_incurred"] is False
    assert intelligent_bridge.readiness()["legacy_global_search_used"] is False


def test_server_side_intelligent_bridge_is_persisted_without_client_assistant_text(
    advisor_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="bridge")
    monkeypatch.setattr(
        intelligent_bridge,
        "answer",
        lambda question, actual_scope, **_kwargs: {
            "answer": "Scoped existing-search answer",
            "status": "ready",
            "mode": "search",
            "reason": "",
            "evidence": [{"evidence_id": "ev_test_1", "kind": "owner_scope"}],
            "navigation_actions": [{"label": "KOL Pool", "route": "kol-pool"}],
        },
    )

    response = service.create_message_turn(
        scope,
        thread["thread_uid"],
        content="Find a creator",
        client_request_id="bridge-1",
    )

    assert response["status"] == "degraded"  # model provider remains fail-closed
    assert response["provider"]["provider_called"] is False
    assert response["knowledge_bridge"] == {"status": "ready", "mode": "search", "reason": ""}
    assistant = response["messages"][1]
    assert assistant["content_text"] == "Scoped existing-search answer"
    assert assistant["provenance_json"]["bridge"] == "advisor_owner_scope_v1"
    assert assistant["provenance_json"]["evidence"] == [
        {"evidence_id": "ev_test_1", "kind": "owner_scope"}
    ]
    assert assistant["metadata_json"]["navigation_actions"][0]["route"] == "kol-pool"


def test_owner_scoped_bridge_excludes_other_users_and_non_normal_memory(advisor_db) -> None:
    owner = _scope(1, 11)
    other = _scope(1, 12)
    thread = repository.create_thread(
        owner,
        title="owner context",
        context_refs=[
            {
                "entity_type": "kol",
                "entity_id": "owner-kol",
                "snapshot": {"label": "Owner creator", "platform": "youtube"},
            }
        ],
    )
    repository.create_degraded_turn(
        owner,
        thread["thread_uid"],
        content_text="owner-only prior message",
        context_refs=[],
        requested_actions=[],
    )

    owner_candidate = repository.create_memory_candidate(
        owner,
        memory_kind="preference",
        memory_key="advisor.language",
        summary="Owner prefers concise English advice",
        value={},
        provenance={"source_ref": "explicit:owner"},
    )
    repository.confirm_memory_candidate(owner, owner_candidate["candidate_uid"])
    restricted_candidate = repository.create_memory_candidate(
        owner,
        memory_kind="constraint",
        memory_key="private.restricted",
        summary="restricted-owner-secret",
        value={},
        provenance={"source_ref": "explicit:owner"},
        sensitivity="restricted",
    )
    repository.confirm_memory_candidate(owner, restricted_candidate["candidate_uid"])
    other_candidate = repository.create_memory_candidate(
        other,
        memory_kind="constraint",
        memory_key="other.secret",
        summary="other-user-secret",
        value={},
        provenance={"source_ref": "explicit:other"},
    )
    repository.confirm_memory_candidate(other, other_candidate["candidate_uid"])

    result = intelligent_bridge.answer(
        "English creator advice",
        owner,
        thread_uid=thread["thread_uid"],
    )
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["status"] == "ready"
    assert result["scope_enforced"] is True
    assert "Owner prefers concise English advice" in serialized
    assert "owner-only prior message" in serialized
    assert "Owner creator" in serialized
    assert "restricted-owner-secret" not in serialized
    assert "other-user-secret" not in serialized
    context_items = [item for item in result["evidence"] if item.get("kind") == "context_ref"]
    assert context_items[0]["external_share_allowed"] is False
    assert context_items[0]["verification_status"] == "unverified_entity_reference"
    assert all(
        item.get("external_share_allowed") is True
        for item in result["evidence"]
        if item.get("kind") != "context_ref"
    )


def test_current_turn_context_overrides_stale_thread_defaults(advisor_db) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(
        scope,
        title="turn refs",
        context_refs=[{
            "entity_type": "kol",
            "entity_id": "stale-kol",
            "snapshot": {"label": "Stale creator"},
        }],
    )
    result = intelligent_bridge.answer(
        "Use this project",
        scope,
        thread_uid=thread["thread_uid"],
        context_refs=[{
            "entity_type": "project",
            "entity_id": "current-project",
            "snapshot": {"label": "Current project"},
        }],
    )
    serialized = json.dumps(result, ensure_ascii=False)
    assert "Current project" in serialized
    assert "Stale creator" not in serialized
    current = next(item for item in result["evidence"] if item.get("kind") == "context_ref")
    assert current["external_share_allowed"] is False


def _allowed_provider_plan() -> dict:
    return {
        "provider_calls_allowed": True,
        "provider_gate_reason": "provider_calls_allowed",
        "providers": [
            {
                "provider": "openai",
                "binding": "openai/gpt-5.6-luna",
                "binding_gate_reason": "ready",
                "budget_allowed": True,
                "estimated_cost_usd": 0.001,
            }
        ],
    }


def _blocked_provider_plan() -> dict:
    return {
        "provider_calls_allowed": False,
        "provider_gate_reason": "model_binding_blocked",
        "providers": [
            {
                "provider": "openai",
                "binding": "openai/gpt-5.6-luna",
                "binding_gate_reason": "readiness_not_production_ready",
                "budget_allowed": True,
                "estimated_cost_usd": 0.001,
            }
        ],
    }


def test_provider_path_is_exact_budgeted_opt_in_and_idempotent(
    advisor_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="live provider")
    monkeypatch.setenv("VKPI_ADVISOR_EXTERNAL_AI_ENABLED", "1")
    monkeypatch.setattr(service, "_provider_preflight", lambda _prompt: _allowed_provider_plan())
    calls: list[dict] = []

    def invoke_json(prompt: str, **kwargs):
        calls.append({"prompt": prompt, "kwargs": kwargs})
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "json": {"answer": "Scoped answer", "evidence_ids": [], "confidence": 0.8},
            "latency_ms": 123,
            "cost_micro_usd": 321,
            "provider_attempts": 1,
        }

    monkeypatch.setattr(service.llm_gateway, "invoke_json", invoke_json)

    first = service.create_message_turn(
        scope,
        thread["thread_uid"],
        content="Give scoped advice",
        client_request_id="provider-once",
        allow_external_ai=True,
    )
    replay = service.create_message_turn(
        scope,
        thread["thread_uid"],
        content="Give scoped advice",
        client_request_id="provider-once",
        allow_external_ai=True,
    )

    assert first["status"] == "ok"
    assert first["provider"]["provider_called"] is True
    assert first["messages"][1]["content_text"] == "Scoped answer"
    assert first["messages"][1]["provider_status"] == "ready"
    assert replay["idempotent_replay"] is True
    assert replay["messages"][1]["content_text"] == "Scoped answer"
    assert len(calls) == 1
    assert calls[0]["kwargs"]["model_override"] == "gpt-5.6-luna"
    assert calls[0]["kwargs"]["model_fallbacks"] == ()
    assert calls[0]["kwargs"]["cost_tag"] == "cron:marketing_advisor"
    assert calls[0]["kwargs"]["require_configured_budget"] is True
    assert calls[0]["kwargs"]["max_provider_attempts"] == 1
    claim = advisor_db.execute(
        "SELECT * FROM vkpi_advisor_turn_claims WHERE organization_id=? AND staff_id=? "
        "AND thread_uid=? AND client_request_id=?",
        (1, 11, thread["thread_uid"], "provider-once"),
    ).fetchone()
    assert claim["state"] == "completed"
    assert bool(claim["provider_attempted"]) is True
    assert claim["claim_token_sha256"] and len(claim["claim_token_sha256"]) == 64
    assert "Give scoped advice" not in repr(dict(claim))
    assert "Scoped answer" not in repr(dict(claim))

    with pytest.raises(repository.AdvisorConflict, match="different content"):
        service.create_message_turn(
            scope,
            thread["thread_uid"],
            content="Different payload",
            client_request_id="provider-once",
            allow_external_ai=True,
        )


def test_provider_gate_blocks_before_http_and_persists_honest_bridge(
    advisor_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="blocked provider")
    monkeypatch.setenv("VKPI_ADVISOR_EXTERNAL_AI_ENABLED", "1")
    monkeypatch.setattr(service, "_provider_preflight", lambda _prompt: _blocked_provider_plan())
    monkeypatch.setattr(
        service.llm_gateway,
        "invoke_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be called")
        ),
    )

    result = service.create_message_turn(
        scope,
        thread["thread_uid"],
        content="Give advice",
        client_request_id="blocked-before-http",
        allow_external_ai=True,
    )

    assert result["status"] == "degraded"
    assert result["reason"] == "advisor_exact_model_not_production_ready"
    assert result["provider"]["provider_called"] is False
    assert result["knowledge_bridge"]["status"] == "ready"
    claim = advisor_db.execute(
        "SELECT state, provider_attempted FROM vkpi_advisor_turn_claims WHERE client_request_id=?",
        ("blocked-before-http",),
    ).fetchone()
    assert claim["state"] == "completed"
    assert bool(claim["provider_attempted"]) is False


def test_uncertain_provider_outcome_is_never_auto_replayed(
    advisor_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="unknown outcome")
    monkeypatch.setenv("VKPI_ADVISOR_EXTERNAL_AI_ENABLED", "1")
    monkeypatch.setattr(service, "_provider_preflight", lambda _prompt: _allowed_provider_plan())
    calls = {"count": 0}

    def explode(*_args, **_kwargs):
        calls["count"] += 1
        raise RuntimeError("transport state unknown")

    monkeypatch.setattr(service.llm_gateway, "invoke_json", explode)
    first = service.create_message_turn(
        scope,
        thread["thread_uid"],
        content="One attempt only",
        client_request_id="unknown-once",
        allow_external_ai=True,
    )
    second = service.create_message_turn(
        scope,
        thread["thread_uid"],
        content="One attempt only",
        client_request_id="unknown-once",
        allow_external_ai=True,
    )

    assert first["status"] == "blocked"
    assert first["claim_state"] == "outcome_unknown"
    assert first["retryable"] is False
    assert second["status"] == "blocked"
    assert second["claim_state"] == "outcome_unknown"
    assert calls["count"] == 1


def test_staged_sse_contract_is_honest_and_persisted(
    advisor_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.dependencies.advisor_scope import require_advisor_write_scope

    scope = _scope(1, 11)
    thread = repository.create_thread(scope, title="SSE")
    monkeypatch.setattr(service, "_provider_preflight", lambda _prompt: _blocked_provider_plan())
    app = FastAPI()
    app.include_router(vkpi_marketing_advisor.router)
    app.dependency_overrides[require_advisor_write_scope] = lambda: scope
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        f"/api/admin/vkpi/marketing-advisor/threads/{thread['thread_uid']}/messages/stream",
        json={"content": "stream this", "client_request_id": "sse-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert "event: accepted" in response.text
    assert '"provider_streaming":false' in response.text
    assert "event: final" in response.text
    assert '"claim_state":"completed"' in response.text


def test_intelligent_cache_is_scoped_by_org_staff_and_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    with vkpi_intelligent._ASK_CACHE_LOCK:
        vkpi_intelligent._ASK_CACHE.clear()
    calls = {"count": 0}

    def intent(question: str):
        calls["count"] += 1
        return vkpi_intelligent._answer(answer=f"answer-{calls['count']}", mode="intent")

    monkeypatch.setattr(vkpi_intelligent, "_try_intent", intent)
    user_a = {
        "id": 11,
        "role": "admin",
        "organization_id": 1,
        "organization_scope_status": "resolved",
    }
    user_b = {
        "id": 12,
        "role": "admin",
        "organization_id": 1,
        "organization_scope_status": "resolved",
    }
    same_staff_other_org = {
        "id": 11,
        "role": "admin",
        "organization_id": 2,
        "organization_scope_status": "resolved",
    }

    first = vkpi_intelligent.intelligent_ask({"question": "same", "thread_id": "t1"}, staff=user_a)
    repeat = vkpi_intelligent.intelligent_ask({"question": "same", "thread_id": "t1"}, staff=user_a)
    other_thread = vkpi_intelligent.intelligent_ask({"question": "same", "thread_id": "t2"}, staff=user_a)
    other_user = vkpi_intelligent.intelligent_ask({"question": "same", "thread_id": "t1"}, staff=user_b)
    other_org = vkpi_intelligent.intelligent_ask(
        {"question": "same", "thread_id": "t1"},
        staff=same_staff_other_org,
    )

    assert first["cached"] is False
    assert repeat["cached"] is True
    assert other_thread["cached"] is False
    assert other_user["cached"] is False
    assert other_org["cached"] is False
    assert calls["count"] == 4


def test_unresolved_intelligent_scope_never_seeds_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    with vkpi_intelligent._ASK_CACHE_LOCK:
        vkpi_intelligent._ASK_CACHE.clear()
    calls = {"count": 0}

    def intent(question: str):
        calls["count"] += 1
        return vkpi_intelligent._answer(answer="uncached", mode="intent")

    monkeypatch.setattr(vkpi_intelligent, "_try_intent", intent)
    unresolved = {
        "id": 11,
        "role": "admin",
        "organization_scope_status": "ambiguous",
    }
    for _ in range(2):
        with pytest.raises(HTTPException) as exc_info:
            vkpi_intelligent.intelligent_ask({"question": "same", "thread_id": "t1"}, staff=unresolved)
        assert exc_info.value.status_code == 403
    assert calls["count"] == 0
    assert vkpi_intelligent._ASK_CACHE == {}


def test_migration_contract_has_composite_scope_and_draft_only_actions() -> None:
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "250_vkpi_marketing_advisor_memory.sql").read_text()
    assert "FOREIGN KEY (organization_id, staff_id, thread_uid)" in sql
    assert "UNIQUE (organization_id, staff_id, thread_uid)" in sql
    assert "UNIQUE (organization_id, staff_id, candidate_uid)" in sql
    assert "status IN ('draft','cancelled')" in sql
    assert "approved" not in sql.lower()
    claim_sql = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "252_vkpi_advisor_turn_claims.sql"
    ).read_text()
    assert "PRIMARY KEY (organization_id, staff_id, thread_uid, client_request_id)" in claim_sql
    assert "provider_attempted" in claim_sql
    assert "outcome_unknown" in claim_sql
    assert "prompt_text" not in claim_sql.lower()
    assert "response_text" not in claim_sql.lower()
    assert "payload_json" not in claim_sql.lower()
    assert '"vkpi_marketing_advisor"' in (
        Path(__file__).resolve().parents[1] / "backend" / "app" / "api" / "routers" / "__init__.py"
    ).read_text()


def test_marketing_advisor_routes_are_mounted() -> None:
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/admin/vkpi/marketing-advisor/readiness" in paths
    assert "/api/admin/vkpi/marketing-advisor/threads/{thread_uid}/messages" in paths
    assert "/api/admin/vkpi/marketing-advisor/threads/{thread_uid}/messages/stream" in paths
    assert "/api/admin/vkpi/marketing-advisor/memory/candidates/{candidate_uid}/confirm" in paths
