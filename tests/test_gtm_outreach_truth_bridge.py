"""Hermetic security contracts for Action -> Project -> Outreach truth."""
from __future__ import annotations

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routers import vkpi_gtm_verdicts
from app.db import connection
from app.domains.actions import approval_evidence
from app.domains.market_brain import (
    outreach_reply_truth,
    outreach_truth_bridge,
    prediction_truth,
)

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/277_vkpi_action_outreach_truth_bridge.sql"
DOWN = ROOT / "migrations/277_vkpi_action_outreach_truth_bridge_down.sql"
START = "2026-08-11T01:02:03+00:00"
END = "2026-08-18T01:02:03+00:00"
APPROVED = "2026-08-11T02:00:00+00:00"
SERVER_NOW = datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)
MANAGER = {
    "id": 7, "role": "manager", "organization_id": 1,
    "organization_scope_status": "resolved",
}


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE vkpi_action_inbox (
          id INTEGER PRIMARY KEY, dedupe_key TEXT, category TEXT, title TEXT,
          detail TEXT, priority TEXT, entity_type TEXT, entity_id TEXT,
          suggested_endpoint TEXT, estimated_cost_cents INTEGER,
          writes_business_data INTEGER, uses_llm INTEGER, requires_approval INTEGER,
          owner_staff_id INTEGER, reason TEXT, payload_json TEXT, touches_v6_fit INTEGER,
          expected_gain TEXT, risk_level TEXT, evidence_refs_json TEXT,
          verification_plan_json TEXT, affected_tables_json TEXT,
          approval_reason TEXT, status TEXT, approved_by_staff_id INTEGER,
          approved_at TEXT, approval_snapshot_sha256 TEXT
        );
        CREATE TABLE vkpi_prediction_runs (
          organization_id TEXT, run_id TEXT, task_type TEXT, product_sku TEXT,
          channel TEXT, horizon_days INTEGER, input_summary TEXT, prediction TEXT,
          p10 REAL, p50 REAL, p90 REAL, created_at TEXT,
          UNIQUE(organization_id, run_id)
        );
        CREATE TABLE vkpi_kol_pool (
          id INTEGER PRIMARY KEY, platform TEXT, linked_main_kol_id INTEGER
        );
        CREATE TABLE vkpi_projects (
          id INTEGER PRIMARY KEY, kol_id INTEGER, product_sku TEXT,
          platform TEXT, stage_status TEXT
        );
        CREATE TABLE vkpi_messages (
          id INTEGER PRIMARY KEY, project_id INTEGER, kol_id INTEGER,
          source TEXT, direction TEXT, body TEXT, snippet TEXT, evidence_url TEXT,
          captured_at TEXT, created_at TEXT,
          metadata_json TEXT DEFAULT '{}'
        );
        CREATE TABLE vkpi_action_outreach_truth_bridges (
          id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER NOT NULL,
          action_inbox_id INTEGER NOT NULL, prediction_organization_id TEXT NOT NULL,
          prediction_run_id TEXT NOT NULL, project_id INTEGER NOT NULL,
          kol_pool_id INTEGER NOT NULL, kol_id INTEGER NOT NULL,
          product_sku TEXT NOT NULL, channel TEXT NOT NULL,
          first_outbound_message_id INTEGER NOT NULL, first_outbound_at TEXT NOT NULL,
          first_outbound_created_at TEXT NOT NULL, observation_start_at TEXT NOT NULL,
          observation_end_at TEXT NOT NULL, action_approved_at TEXT NOT NULL,
          approval_snapshot_sha256 TEXT NOT NULL, actor_staff_id INTEGER NOT NULL,
          correlation_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
          binding_fingerprint TEXT NOT NULL, verified_at TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(organization_id, action_inbox_id),
          UNIQUE(organization_id, correlation_id),
          UNIQUE(prediction_organization_id, prediction_run_id)
        );
        CREATE TABLE vkpi_action_outreach_reply_truth_receipts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER NOT NULL,
          binding_id INTEGER NOT NULL, outcome TEXT NOT NULL,
          inbound_message_id INTEGER, inbound_captured_at TEXT, inbound_created_at TEXT,
          first_outbound_at TEXT NOT NULL, observation_end_at TEXT NOT NULL,
          candidate_observed_at TEXT NOT NULL, verified_at TEXT NOT NULL,
          actor_staff_id INTEGER NOT NULL,
          correlation_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
          binding_fingerprint TEXT NOT NULL, review_candidate_sha256 TEXT NOT NULL,
          review_candidate_json TEXT NOT NULL,
          receipt_fingerprint TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(organization_id, binding_id), UNIQUE(organization_id, correlation_id)
        );
        CREATE TABLE vkpi_event_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER NOT NULL,
          event_type TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
          actor_type TEXT NOT NULL, actor_id TEXT NOT NULL, source TEXT NOT NULL,
          payload_json TEXT NOT NULL, trace_id TEXT NOT NULL, confidence REAL,
          provenance_json TEXT NOT NULL, occurred_at TEXT DEFAULT CURRENT_TIMESTAMP,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX uq_test_binding_event
          ON vkpi_event_ledger(organization_id,entity_type,entity_id,source)
          WHERE event_type='action_outreach_bound';
        CREATE UNIQUE INDEX uq_test_reply_event
          ON vkpi_event_ledger(organization_id,entity_type,entity_id,source)
          WHERE event_type='action_outreach_reply_verified';
        """
    )
    conn.commit()


def _contract(action_id: int, *, start: str = START) -> dict[str, Any]:
    return prediction_truth.build_registered_gtm_evaluation_contract(
        "kol_outreach_reply_outcome_7d",
        target_action_inbox_id=action_id,
        observation_start_at=start,
    )


def _seed_action(
    conn: sqlite3.Connection,
    action_id: int,
    *,
    status: str = "approved",
    approved_at: str = APPROVED,
    valid_approval: bool = True,
) -> None:
    row: dict[str, Any] = {
        "id": action_id, "dedupe_key": f"gtm:{action_id}", "category": "gtm_bet",
        "title": "outreach", "detail": "contact creator", "priority": "normal",
        "entity_type": "kol", "entity_id": "17", "suggested_endpoint": "/gtm",
        "estimated_cost_cents": 0, "writes_business_data": 1, "uses_llm": 0,
        "requires_approval": 1, "owner_staff_id": 7, "reason": "registered bet",
        "payload_json": "{}", "touches_v6_fit": 0, "expected_gain": "reply",
        "risk_level": "low", "evidence_refs_json": "[]",
        "verification_plan_json": "[]", "affected_tables_json": "[]",
        "approval_reason": "approved for test", "status": status,
        "approved_by_staff_id": 7, "approved_at": approved_at,
    }
    snapshot_hash = approval_evidence._hash(row, row["approval_reason"])
    row["approval_snapshot_sha256"] = snapshot_hash if valid_approval else "0" * 64
    columns = approval_evidence.APPROVAL_CONTRACT_COLUMNS.split(",")
    conn.execute(
        f"INSERT INTO vkpi_action_inbox ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        tuple(row.get(column) for column in columns),
    )
    if valid_approval:
        conn.execute(
            """
            INSERT INTO vkpi_event_ledger (
              organization_id,event_type,entity_type,entity_id,actor_type,actor_id,
              source,payload_json,trace_id,provenance_json
            ) VALUES (1,'action_approved','action',?,'staff','7',?, '{}',?,?)
            """,
            (
                str(action_id), approval_evidence._EVENT_SOURCE, f"approval-{action_id}",
                json.dumps({"approval_snapshot_sha256": snapshot_hash}),
            ),
        )


def _seed_run(
    conn: sqlite3.Connection,
    action_id: int,
    *,
    run_start: str = START,
    contract_start: str = START,
    p10: Any = 0.05,
    p50: Any = 0.10,
    p90: Any = 0.20,
) -> None:
    prediction = {
        "metric_key": "reply_outcome", "unit": "ratio", "value": p50,
        "p10": p10, "p50": p50, "p90": p90, "kol_pool_id": 17,
    }
    conn.execute(
        "INSERT INTO vkpi_prediction_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "viltrox", f"gtmact_{action_id}_kol_outreach_reply_outcome_7d",
            "kol_outreach_reply_probability", "AF-26", "youtube", 7,
            json.dumps({"evaluation_contract": _contract(action_id, start=contract_start)}),
            json.dumps(prediction), p10, p50, p90, run_start,
        ),
    )


def _message(
    conn: sqlite3.Connection,
    message_id: int,
    project_id: int,
    direction: str,
    captured_at: str,
    *,
    kol_id: int = 9,
    created_at: str | None = None,
    source: str = "manual",
    metadata: dict[str, Any] | None = None,
    body: str | None = None,
    snippet: str = "",
    evidence_url: str = "",
) -> None:
    conn.execute(
        "INSERT INTO vkpi_messages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            message_id, project_id, kol_id, source, direction,
            body if body is not None else (
                "Thanks, I am interested." if direction == "inbound"
                else "Hello, would you like to collaborate?"
            ),
            snippet,
            evidence_url, captured_at,
            created_at or captured_at, json.dumps(metadata or {}),
        ),
    )


def _seed(
    conn: sqlite3.Connection,
    *,
    action_id: int = 41,
    project_id: int = 10,
    status: str = "approved",
    valid_approval: bool = True,
    approved_at: str = APPROVED,
    project_kol_id: int = 9,
    project_sku: str = "AF-26",
    project_channel: str = "youtube",
    pool_channel: str = "yt",
    run_start: str = START,
    contract_start: str = START,
    with_outbound: bool = True,
) -> None:
    _seed_action(
        conn, action_id, status=status, approved_at=approved_at,
        valid_approval=valid_approval,
    )
    conn.execute("INSERT OR IGNORE INTO vkpi_kol_pool VALUES (17,?,9)", (pool_channel,))
    conn.execute(
        "INSERT INTO vkpi_projects VALUES (?,?,?,?,?)",
        (project_id, project_kol_id, project_sku, project_channel, "active"),
    )
    _seed_run(
        conn, action_id, run_start=run_start, contract_start=contract_start,
    )
    if with_outbound:
        _message(
            conn, 100, project_id, "outbound", "2026-08-12T00:00:00+00:00",
            created_at="2026-08-12T00:01:00+00:00",
            metadata={"claimed_action_id": action_id},
        )
    conn.commit()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _schema(conn)
    monkeypatch.setattr(outreach_truth_bridge, "table_exists", lambda _name: True)
    monkeypatch.setattr(outreach_reply_truth, "table_exists", lambda _name: True)
    monkeypatch.setattr(outreach_truth_bridge, "_server_now", lambda _conn: SERVER_NOW)
    return conn


def _bind(
    conn: sqlite3.Connection | None,
    *,
    action_id: int = 41,
    correlation: str = "outreach-bind-0001",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return outreach_truth_bridge.create_outreach_binding(
        action_id, correlation_id=correlation, staff=staff or MANAGER, _connection=conn,
    )


def _verify(
    conn: sqlite3.Connection | None,
    binding_id: int,
    *,
    outcome: str = "replied",
    correlation: str = "outreach-reply-0001",
    staff: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review = candidate or outreach_reply_truth.get_reply_review_candidate(
        binding_id, outcome=outcome, staff=staff or MANAGER, _connection=conn,
    )
    if not review.get("ok"):
        return review
    return outreach_reply_truth.verify_reply(
        binding_id, outcome=outcome, correlation_id=correlation,
        expected_candidate_sha256=str(review["candidate_sha256"]),
        candidate_observed_at=str(review["candidate_observed_at"]),
        staff=staff or MANAGER, _connection=conn,
    )


def _actual(conn: sqlite3.Connection, **changes: Any) -> dict[str, Any]:
    values = {
        "action_inbox_id": 41, "kol_pool_id": 17, "kol_id": 9,
        "product_sku": "AF-26", "channel": "youtube",
        "start": datetime.fromisoformat(START), "end": datetime.fromisoformat(END),
    }
    values.update(changes)
    return outreach_truth_bridge.resolve_reply_actual(conn, **values)


def test_manager_binding_reverifies_approval_and_server_selects_project(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    _message(db, 99, 10, "outbound", "2026-08-10T00:00:00+00:00")
    db.commit()

    result = _bind(db)

    assert result["ok"] is True and result["project_id"] == 10
    bridge = dict(db.execute("SELECT * FROM vkpi_action_outreach_truth_bridges").fetchone())
    assert bridge["first_outbound_message_id"] == 100
    assert bridge["action_approved_at"] == APPROVED
    assert bridge["first_outbound_created_at"] == "2026-08-12T00:01:00+00:00"
    event = dict(db.execute(
        "SELECT * FROM vkpi_event_ledger WHERE event_type='action_outreach_bound'"
    ).fetchone())
    provenance = json.loads(event["provenance_json"])
    assert provenance["approval_snapshot_reverified"] is True
    assert provenance["client_project_or_message_id_used"] is False


def test_raw_client_message_never_actual_then_verified_receipt_proves_one(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    _message(
        db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00",
        created_at="2026-08-13T00:01:00+00:00",
        metadata={"action_inbox_id": 41, "reply_outcome": 1},
    )
    db.commit()
    binding = _bind(db)
    assert binding["ok"] is True
    assert _actual(db)["reply_outcome"] is None

    receipt = _verify(db, binding["id"])
    actual = _actual(db)

    assert receipt["ok"] is True and receipt["inbound_message_id"] == 101
    assert actual["reply_outcome"] == 1
    assert actual["reply_outcome_correlated_inbound_message_ids"] == [101]
    assert actual["reply_outcome_project_id"] == 10


def test_reply_review_candidate_is_redacted_hash_bound_and_detects_toctou(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    _message(
        db, 102, 10, "inbound", "2026-08-14T00:00:00+00:00",
        created_at="2026-08-14T00:01:00+00:00",
        source="email", metadata={"password": "do-not-return"},
        body="Yes, please send the collaboration details.", snippet="Interested in AF-26",
        evidence_url="https://evidence.example/path?token=super-secret-token",
    )
    db.commit()
    binding = _bind(db)
    candidate = outreach_reply_truth.get_reply_review_candidate(
        binding["id"], outcome="replied", staff=MANAGER, _connection=db,
    )
    assert candidate["ok"] is True and candidate["candidate"]["eligible"] is True
    resolved = candidate["candidate"]["resolved_inbound"]
    assert resolved["source_class"] == "email"
    assert resolved["review_content"]["body_excerpt"].startswith("Yes, please")
    assert resolved["review_content"]["evidence_host"] == "evidence.example"
    assert resolved["review_content"]["raw_evidence_url_returned"] is False
    canonical = candidate["candidate_canonical_json"].lower()
    assert "super-secret" not in canonical and "password" not in canonical

    # A newly visible earlier reply changes the exact first-inbound candidate;
    # the manager must review a new hash rather than blind-sign stale state.
    _message(
        db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00",
        created_at="2026-08-13T00:01:00+00:00",
    )
    db.commit()
    changed = _verify(db, binding["id"], candidate=candidate)
    assert changed["reason"] == "outreach_reply_candidate_changed"

    missing = outreach_reply_truth.verify_reply(
        binding["id"], outcome="replied", correlation_id="outreach-reply-0002",
        expected_candidate_sha256="", candidate_observed_at="", staff=MANAGER,
        _connection=db,
    )
    assert missing["reason"] == "outreach_reply_candidate_required"


def test_reply_content_change_invalidates_review_hash_and_secrets_stay_redacted(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    _message(
        db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00",
        body="authorization: Bearer very-secret-token", snippet="reply",
        evidence_url="https://example.com/item?sig=private",
    )
    db.commit()
    binding = _bind(db)
    candidate = outreach_reply_truth.get_reply_review_candidate(
        binding["id"], outcome="replied", staff=MANAGER, _connection=db,
    )
    canonical = candidate["candidate_canonical_json"].lower()
    assert "very-secret-token" not in canonical and "sig=private" not in canonical
    assert "[redacted]" in canonical
    db.execute("UPDATE vkpi_messages SET body='different reply' WHERE id=101")
    db.commit()
    assert _verify(db, binding["id"], candidate=candidate)["reason"] == (
        "outreach_reply_candidate_changed"
    )


def test_empty_inbound_is_visible_but_not_eligible_for_positive_truth(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    _message(
        db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00",
        body="", snippet="", evidence_url="https://example.com/evidence",
    )
    db.commit()
    binding = _bind(db)
    candidate = outreach_reply_truth.get_reply_review_candidate(
        binding["id"], outcome="replied", staff=MANAGER, _connection=db,
    )
    assert candidate["candidate"]["resolved_inbound"]["review_content"][
        "reviewable_content"
    ] is False
    assert candidate["candidate"]["eligibility_reason"] == "inbound_content_unreviewable"
    assert _verify(db, binding["id"], candidate=candidate)["reason"] == (
        "outreach_inbound_content_unreviewable"
    )


def test_late_arriving_earlier_outbound_invalidates_review_candidate(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    binding = _bind(db)
    # Still after approval, but earlier than the outbound the manager bound.
    _message(
        db, 99, 10, "outbound", "2026-08-11T03:00:00+00:00",
        created_at="2026-08-11T03:01:00+00:00",
    )
    _message(db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00")
    db.commit()
    candidate = outreach_reply_truth.get_reply_review_candidate(
        binding["id"], outcome="replied", staff=MANAGER, _connection=db,
    )
    assert candidate["ok"] is True
    assert candidate["candidate"]["binding_first_outbound_still_exact"] is False
    assert candidate["candidate"]["eligible"] is False
    assert _verify(db, binding["id"], candidate=candidate)["reason"] == (
        "outreach_reply_candidate_changed"
    )


def test_post_review_clock_invalid_outbound_candidate_forces_new_review(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    _message(db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00")
    db.commit()
    binding = _bind(db)
    candidate = outreach_reply_truth.get_reply_review_candidate(
        binding["id"], outcome="replied", staff=MANAGER, _connection=db,
    )
    _message(
        db, 99, 10, "outbound", "2026-08-11T03:00:00+00:00",
        created_at="2026-08-19T02:00:00+00:00",
    )
    db.commit()
    assert _verify(db, binding["id"], candidate=candidate)["reason"] == (
        "outreach_reply_candidate_changed"
    )


@pytest.mark.parametrize(
    "message",
    [
        (101, 10, "inbound", "2026-08-11T12:00:00+00:00", "2026-08-11T12:01:00+00:00"),
        (101, 11, "inbound", "2026-08-13T00:00:00+00:00", "2026-08-13T00:01:00+00:00"),
        (101, 10, "inbound", "2026-08-20T00:00:00+00:00", "2026-08-20T00:01:00+00:00"),
        # Backfilled after the frozen end: captured is forged in-window but
        # server-created time is outside, so it is not eligible evidence.
        (101, 10, "inbound", "2026-08-13T00:00:00+00:00", "2026-08-19T00:00:00+00:00"),
    ],
)
def test_wrong_project_time_or_backfill_cannot_prove_reply(
    db: sqlite3.Connection, message: tuple[Any, ...],
) -> None:
    _seed(db)
    if message[1] == 11:
        db.execute("INSERT INTO vkpi_projects VALUES (11,9,'AF-35','youtube','active')")
    _message(db, message[0], message[1], message[2], message[3], created_at=message[4])
    db.commit()
    binding = _bind(db)
    assert binding["ok"] is True
    assert _verify(db, binding["id"])["reason"] == "outreach_verified_inbound_not_observed"
    assert _actual(db)["reply_outcome"] is None


def test_no_reply_requires_closed_server_clock_and_immutable_receipt(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(db)
    binding = _bind(db)
    monkeypatch.setattr(
        outreach_truth_bridge, "_server_now",
        lambda _conn: datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    assert _verify(db, binding["id"], outcome="no_reply")["reason"] == (
        "outreach_no_reply_window_open"
    )
    monkeypatch.setattr(outreach_truth_bridge, "_server_now", lambda _conn: SERVER_NOW)
    receipt = _verify(db, binding["id"], outcome="no_reply")
    assert receipt["ok"] is True
    assert _actual(db)["reply_outcome"] == 0


def test_no_reply_candidate_rejects_backdated_inbound_inserted_after_review(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    binding = _bind(db)
    candidate = outreach_reply_truth.get_reply_review_candidate(
        binding["id"], outcome="no_reply", staff=MANAGER, _connection=db,
    )
    assert candidate["candidate"]["eligible"] is True
    _message(
        db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00",
        created_at="2026-08-13T00:01:00+00:00", body="late imported reply",
    )
    db.commit()
    assert _verify(
        db, binding["id"], outcome="no_reply", candidate=candidate,
    )["reason"] == "outreach_reply_candidate_changed"


def test_preapproval_or_postwindow_created_outbound_is_rejected(
    db: sqlite3.Connection,
) -> None:
    _seed(db, with_outbound=False)
    _message(
        db, 100, 10, "outbound", "2026-08-11T01:30:00+00:00",
        created_at="2026-08-11T01:31:00+00:00",
    )
    db.commit()
    assert _bind(db)["reason"] == "outreach_outbound_precedes_approval"

    db.execute("DELETE FROM vkpi_messages")
    _message(
        db, 100, 10, "outbound", "2026-08-12T00:00:00+00:00",
        created_at="2026-08-19T00:00:00+00:00",
    )
    db.commit()
    assert _bind(db)["reason"] == "outreach_outbound_evidence_unverified"


def test_preapproval_first_outbound_cannot_be_hidden_by_later_valid_send(
    db: sqlite3.Connection,
) -> None:
    _seed(db, with_outbound=False)
    _message(db, 99, 10, "outbound", "2026-08-11T01:30:00+00:00")
    _message(db, 100, 10, "outbound", "2026-08-12T00:00:00+00:00")
    db.commit()
    assert _bind(db)["reason"] == "outreach_outbound_precedes_approval"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"project_kol_id": 8}, "outreach_project_scope_not_found"),
        ({"project_sku": "AF-35"}, "outreach_project_scope_not_found"),
        ({"project_channel": "instagram"}, "outreach_project_scope_not_found"),
        ({"pool_channel": "instagram"}, "outreach_kol_channel_mismatch"),
        ({"status": "suggested"}, "outreach_action_not_approved_gtm_bet"),
        ({"valid_approval": False}, "outreach_action_approval_proof_invalid"),
        ({"run_start": "2026-08-11T01:02:04+00:00"}, "outreach_prediction_contract_invalid"),
    ],
)
def test_binding_fails_closed_on_scope_approval_or_contract_drift(
    db: sqlite3.Connection, changes: dict[str, Any], reason: str,
) -> None:
    _seed(db, **changes)
    assert _bind(db) == {"ok": False, "reason": reason}
    assert db.execute("SELECT COUNT(*) FROM vkpi_action_outreach_truth_bridges").fetchone()[0] == 0


def test_manager_cannot_choose_favorable_project_when_scope_is_ambiguous(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    db.execute("INSERT INTO vkpi_projects VALUES (11,9,'AF-26','youtube','active')")
    _message(db, 101, 11, "outbound", "2026-08-12T01:00:00+00:00")
    _message(db, 102, 11, "inbound", "2026-08-13T01:00:00+00:00")
    db.commit()
    assert _bind(db)["reason"] == "outreach_project_ambiguous"
    with pytest.raises(ValidationError):
        vkpi_gtm_verdicts.OutreachTruthBindingBody(
            project_id=11, correlation_id="outreach-bind-0001",
        )


def test_two_exact_projects_are_ambiguous_even_when_only_one_has_outbound(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    db.execute("INSERT INTO vkpi_projects VALUES (11,9,'AF-26','youtube','active')")
    db.commit()
    assert _bind(db)["reason"] == "outreach_project_ambiguous"


def test_exact_replay_and_same_key_different_actor_conflict(db: sqlite3.Connection) -> None:
    _seed(db)
    first = _bind(db)
    replay = _bind(db)
    actor_conflict = _bind(db, staff={**MANAGER, "id": 8})
    other_key = _bind(db, correlation="outreach-bind-0002")
    assert first["ok"] is True and first["idempotent"] is False
    assert replay["ok"] is True and replay["idempotent"] is True
    assert actor_conflict["reason"] == "outreach_binding_correlation_conflict"
    assert other_key["reason"] == "outreach_action_already_bound"


def test_reply_receipt_exact_replay_and_conflicting_keys(db: sqlite3.Connection) -> None:
    _seed(db)
    binding = _bind(db)
    _message(db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00")
    db.commit()
    candidate = outreach_reply_truth.get_reply_review_candidate(
        binding["id"], outcome="replied", staff=MANAGER, _connection=db,
    )
    first = _verify(db, binding["id"], candidate=candidate)
    replay = _verify(db, binding["id"], candidate=candidate)
    actor_conflict = _verify(
        db, binding["id"], candidate=candidate, staff={**MANAGER, "id": 8},
    )
    other_key = _verify(
        db, binding["id"], candidate=candidate, correlation="outreach-reply-0002",
    )
    assert first["ok"] is True and first["idempotent"] is False
    assert replay["ok"] is True and replay["idempotent"] is True
    assert actor_conflict["reason"] == "outreach_reply_correlation_conflict"
    assert other_key["reason"] == "outreach_reply_already_verified"


def test_binding_status_recovers_id_and_verified_reply_without_message_content(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    binding = _bind(db)
    pending = outreach_truth_bridge.get_outreach_binding_status(
        41, staff=MANAGER, _connection=db,
    )
    assert pending["ok"] is True
    assert pending["status"] == "bound_pending_reply_verification"
    assert pending["binding"]["id"] == binding["id"]
    assert "first_outbound_message_id" not in pending["binding"]

    _message(db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00")
    db.commit()
    assert _verify(db, binding["id"])["ok"] is True
    verified = outreach_truth_bridge.get_outreach_binding_status(
        41, staff=MANAGER, _connection=db,
    )
    assert verified["status"] == "reply_verified"
    assert verified["reply_verification"]["outcome"] == "replied"
    assert verified["reply_verification"]["review_candidate"]["eligible"] is True
    assert len(verified["reply_verification"]["review_candidate_sha256"]) == 64
    canonical = verified["reply_verification"]["review_candidate_canonical_json"]
    assert canonical == outreach_reply_truth.review_contract.canonical_review_json(
        verified["reply_verification"]["review_candidate"],
    )
    assert outreach_reply_truth.review_contract.review_snapshot_sha256(
        json.loads(canonical)
    ) == verified["reply_verification"]["review_candidate_sha256"]
    rendered = json.dumps(verified)
    assert "metadata_json" not in rendered and "?sig=" not in rendered


def test_receipt_persists_redacted_candidate_and_read_recomputes_it(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    binding = _bind(db)
    _message(
        db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00",
        body="authorization: Bearer never-store-this", snippet="interested",
        evidence_url="https://evidence.example/item?sig=never-store-this",
    )
    db.commit()
    assert _verify(db, binding["id"])["ok"] is True
    receipt = dict(db.execute(
        "SELECT * FROM vkpi_action_outreach_reply_truth_receipts"
    ).fetchone())
    stored = json.loads(receipt["review_candidate_json"])
    assert stored["schema"] == "vkpi_action_outreach_reply_review_candidate/v1"
    assert "never-store-this" not in receipt["review_candidate_json"]
    assert outreach_reply_truth.verified_receipt_for_binding(
        db, dict(db.execute("SELECT * FROM vkpi_action_outreach_truth_bridges").fetchone()),
    ) is not None

    stored["project_id"] = 999
    db.execute(
        "UPDATE vkpi_action_outreach_reply_truth_receipts SET review_candidate_json=?",
        (json.dumps(stored),),
    )
    db.commit()
    assert _actual(db)["reply_outcome"] is None


def test_bridge_and_receipt_event_failures_roll_back_rows(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(db)
    original = outreach_truth_bridge.event_ledger.insert_required
    monkeypatch.setattr(
        outreach_truth_bridge.event_ledger, "insert_required",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event unavailable")),
    )
    assert _bind(db)["reason"] == "outreach_binding_write_failed"
    assert db.execute("SELECT COUNT(*) FROM vkpi_action_outreach_truth_bridges").fetchone()[0] == 0

    monkeypatch.setattr(outreach_truth_bridge.event_ledger, "insert_required", original)
    binding = _bind(db)
    _message(db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00")
    db.commit()
    monkeypatch.setattr(
        outreach_reply_truth.event_ledger, "insert_required",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event unavailable")),
    )
    assert _verify(db, binding["id"])["reason"] == "outreach_reply_write_failed"
    assert db.execute(
        "SELECT COUNT(*) FROM vkpi_action_outreach_reply_truth_receipts"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("damage", ["binding_hash", "binding_event", "receipt_hash", "receipt_event"])
def test_read_path_recomputes_both_hashes_and_exact_events(
    db: sqlite3.Connection, damage: str,
) -> None:
    _seed(db)
    binding = _bind(db)
    _message(db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00")
    db.commit()
    assert _verify(db, binding["id"])["ok"] is True
    if damage == "binding_hash":
        db.execute("UPDATE vkpi_action_outreach_truth_bridges SET binding_fingerprint=?", ("0" * 64,))
    elif damage == "binding_event":
        db.execute("UPDATE vkpi_event_ledger SET payload_json='{}' WHERE event_type='action_outreach_bound'")
    elif damage == "receipt_hash":
        db.execute("UPDATE vkpi_action_outreach_reply_truth_receipts SET receipt_fingerprint=?", ("0" * 64,))
    else:
        db.execute("DELETE FROM vkpi_event_ledger WHERE event_type='action_outreach_reply_verified'")
    db.commit()
    actual = _actual(db)
    assert actual["reply_outcome"] is None
    assert "invalid" in actual["reply_outcome_binding"] or "unverified" in actual["reply_outcome_binding"]


def test_domain_and_routes_are_manager_org1_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        outreach_truth_bridge, "table_exists", lambda _name: calls.append(("db",)) or True,
    )
    assert _bind(None, staff={**MANAGER, "role": "employee"})["reason"] == (
        "outreach_binding_scope_unavailable"
    )
    assert _bind(None, staff={**MANAGER, "organization_id": 2})["reason"] == (
        "outreach_binding_scope_unavailable"
    )
    assert calls == []
    monkeypatch.setattr(
        outreach_reply_truth, "table_exists", lambda _name: calls.append(("reply-db",)) or True,
    )
    candidate_denied = outreach_reply_truth.get_reply_review_candidate(
        1, outcome="replied", staff={**MANAGER, "organization_id": 2}, _connection=None,
    )
    assert candidate_denied["reason"] == "outreach_reply_scope_unavailable"
    status_denied = outreach_truth_bridge.get_outreach_binding_status(
        41, staff={**MANAGER, "organization_id": 2}, _connection=None,
    )
    assert status_denied["reason"] == "outreach_binding_scope_unavailable"
    assert calls == []

    def fake(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return {"ok": True, "id": 1}

    monkeypatch.setattr(outreach_truth_bridge, "create_outreach_binding", fake)
    body = vkpi_gtm_verdicts.OutreachTruthBindingBody(correlation_id="outreach-bind-0001")
    assert vkpi_gtm_verdicts.bind_action_outreach_truth(41, body, staff=MANAGER)["ok"] is True
    assert calls[-1][0] == (41,)
    monkeypatch.setattr(outreach_truth_bridge, "get_outreach_binding_status", fake)
    assert vkpi_gtm_verdicts.get_action_outreach_binding_status(
        41, staff=MANAGER,
    )["ok"] is True
    assert calls[-1][0] == (41,)
    with pytest.raises(HTTPException) as denied:
        vkpi_gtm_verdicts.bind_action_outreach_truth(
            41, body, staff={**MANAGER, "organization_id": 2},
        )
    assert denied.value.status_code == 403


def test_concurrent_exact_replay_inserts_one_bridge_and_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "bridge.sqlite"
    seed = sqlite3.connect(path)
    seed.row_factory = sqlite3.Row
    _schema(seed)
    _seed(seed)
    seed.close()
    monkeypatch.setattr(outreach_truth_bridge, "table_exists", lambda _name: True)
    monkeypatch.setattr(outreach_truth_bridge, "_server_now", lambda _conn: SERVER_NOW)

    def run() -> dict[str, Any]:
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            return _bind(conn)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: run(), range(2)))
    assert all(result["ok"] for result in results)
    assert sorted(result["idempotent"] for result in results) == [False, True]
    check = sqlite3.connect(path)
    assert check.execute("SELECT COUNT(*) FROM vkpi_action_outreach_truth_bridges").fetchone()[0] == 1
    assert check.execute(
        "SELECT COUNT(*) FROM vkpi_event_ledger WHERE event_type='action_outreach_bound'"
    ).fetchone()[0] == 1


def test_due_coverage_keeps_invalid_and_unbound_runs_in_denominator(
    db: sqlite3.Connection,
) -> None:
    _seed(db)
    binding = _bind(db)
    _message(db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00")
    db.commit()
    assert _verify(db, binding["id"])["ok"] is True
    for action_id in range(42, 131):
        _seed_run(db, action_id)
    # One additional due invalid probability must be counted, not continued away.
    _seed_run(db, 131, p10=0.2, p50=2.0, p90=2.1)
    db.commit()

    coverage = outreach_truth_bridge.outreach_prediction_coverage(
        db, now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert coverage["registered_due"] == 91
    assert coverage["verified_bound"] == 1 and coverage["verified_actual"] == 1
    assert coverage["invalid_due_contract"] == 1
    assert coverage["unbound"] == 90
    assert coverage["claimable"] is False
    assert coverage["claim_level"] == "descriptive_only"


def test_manager_receipts_never_claim_without_provider_completeness(
    monkeypatch: pytest.MonkeyPatch, db: sqlite3.Connection,
) -> None:
    _seed(db)
    binding = _bind(db)
    _message(db, 101, 10, "inbound", "2026-08-13T00:00:00+00:00")
    db.commit()
    assert _verify(db, binding["id"])["ok"] is True
    monkeypatch.setattr(outreach_truth_bridge, "MIN_CLAIMABLE_ACTUALS", 1)
    monkeypatch.setattr(outreach_truth_bridge, "MIN_CLAIMABLE_COVERAGE", 1.0)

    coverage = outreach_truth_bridge.outreach_prediction_coverage(
        db, now=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert coverage["manager_attested_sample_ready"] is True
    assert coverage["provider_completeness_verified"] is False
    assert coverage["evidence_class"] == "manager_attested_mutable_message_snapshot"
    assert coverage["claimable"] is False
    assert coverage["claim_blockers"] == ["provider_sync_completeness_receipt_missing"]


def test_postgres_scope_lock_orders_fk_parents_before_message_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class LockCursor:
        def __init__(self, row: dict[str, Any] | None = None) -> None:
            self.row = row

        def fetchone(self) -> dict[str, Any] | None:
            return self.row

        def fetchall(self) -> list[Any]:
            return []

    class LockConn:
        def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> Any:
            calls.append(" ".join(sql.split()))
            if "FROM vkpi_kol_pool" in sql:
                return LockCursor({"id": 17, "platform": "youtube", "linked_main_kol_id": 9})
            return LockCursor()

    monkeypatch.setattr(outreach_truth_bridge, "is_postgres_runtime", lambda: True)
    pool = outreach_truth_bridge._lock_pool_kol_message_scope(
        LockConn(), kol_pool_id=17, project_ids=[10],
    )
    assert pool is not None and pool["linked_main_kol_id"] == 9
    assert "FROM vkpi_kol_pool" in calls[0] and calls[0].endswith("FOR UPDATE")
    assert calls[1] == "SELECT id FROM kols WHERE id=? FOR UPDATE"
    assert "FROM vkpi_projects" in calls[2] and calls[2].endswith("FOR UPDATE")
    assert "FROM vkpi_messages" in calls[3] and calls[3].endswith("FOR UPDATE")
    assert all("SET TRANSACTION" not in sql for sql in calls)


def test_migration_277_is_runner_owned_immutable_and_reversible() -> None:
    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")
    assert re.search(r"(?mi)^\s*(?:BEGIN|COMMIT)\s*;", up) is None
    for token in (
        "uq_vkpi_outreach_truth_action", "uq_vkpi_outreach_truth_correlation",
        "uq_vkpi_outreach_truth_prediction", "first_outbound_created_at <= observation_end_at",
        "verified_at >= observation_end_at", "action_outreach_bound",
        "review_candidate_json", "octet_length(review_candidate_json::TEXT) <= 65536",
        "action_outreach_reply_verified",
        "BEFORE UPDATE OR DELETE ON vkpi_action_outreach_truth_bridges",
        "BEFORE UPDATE OR DELETE ON vkpi_action_outreach_reply_truth_receipts",
        "BEFORE UPDATE OR DELETE ON vkpi_event_ledger",
    ):
        assert token in up
    assert "DROP TABLE IF EXISTS vkpi_action_outreach_reply_truth_receipts" in down
    assert "DROP TABLE IF EXISTS vkpi_action_outreach_truth_bridges" in down
    assert "277_vkpi_action_outreach_truth_bridge.sql" in down
    names = connection._discover_postgres_migrations()
    assert names.index("276_vkpi_prediction_runs_immutable.sql") < names.index(UP.name)
