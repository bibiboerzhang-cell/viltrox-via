"""Hermetic truth gates for manager verification of executed Action results."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.actions import reviews
from app.domains.platform import event_ledger
from app.domains.platform import review_contract


STAFF = {"id": 23, "organization_id": 1, "organization_scope_status": "resolved"}
EVIDENCE = [{"source": "receipt", "reference": "action-ledger:101", "type": "execution"}]
EXECUTION_DETAIL = {"result_checklist": {"outcome": "success", "rows_written": 1}}
EXECUTION_DETAIL_HASH = review_contract.review_snapshot_sha256(EXECUTION_DETAIL)
EXECUTION_CREATED_AT = "2026-08-11T00:00:00Z"
ACTION_CANDIDATE = {
    "action_id": 7,
    "execution_ledger_id": 101,
    "execution_created_at": EXECUTION_CREATED_AT,
    "endpoint": "/execute",
    "outcome": "success",
    "detail_json": EXECUTION_DETAIL,
    "detail_sha256": EXECUTION_DETAIL_HASH,
    "tool_run_ids": [201],
    "verification_plan": ["核对执行回执"],
}
ACTION_CANDIDATE_HASH = review_contract.review_snapshot_sha256(ACTION_CANDIDATE)


def _db(*, execution_mode: str = "executed", tool_receipts: int = 1) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_action_inbox (
            id INTEGER PRIMARY KEY,
            category TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            suggested_endpoint TEXT NOT NULL,
            status TEXT NOT NULL,
            verification_plan_json TEXT NOT NULL,
            result_checklist_json TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE vkpi_action_execution_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id INTEGER,
            category TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,
            actor_staff_id INTEGER,
            mode TEXT NOT NULL,
            outcome TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            cost_cents INTEGER NOT NULL,
            error TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE vkpi_agent_tool_run (
            id INTEGER PRIMARY KEY,
            inputs_json TEXT NOT NULL,
            output_ref TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE vkpi_event_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            confidence REAL,
            provenance_json TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO vkpi_action_inbox VALUES "
        "(7,'event_followup','event:7','/execute','executed','[\"核对执行回执\"]','{}',NULL)"
    )
    conn.execute(
        """INSERT INTO vkpi_action_execution_ledger
           (id,action_id,category,dedupe_key,mode,outcome,endpoint,cost_cents,error,detail_json,created_at)
           VALUES (101,7,'event_followup','event:7',?,'success','/execute',0,'',?,?)""",
        (execution_mode, json.dumps(EXECUTION_DETAIL), EXECUTION_CREATED_AT),
    )
    for offset in range(tool_receipts):
        conn.execute(
            "INSERT INTO vkpi_agent_tool_run VALUES (?,?,?,'executed')",
            (
                201 + offset,
                json.dumps({"execution_ledger_id": 101, "execution_effect": "acknowledgement"}),
                "action:7",
            ),
        )
    conn.commit()
    return conn


def _wire(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(reviews, "get_conn", lambda: conn)
    monkeypatch.setattr(reviews, "table_exists", lambda name: True)
    monkeypatch.setattr(reviews, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(event_ledger, "is_postgres_runtime", lambda: False)


def _verify(**overrides):
    payload = {
        "action_id": 7,
        "staff": STAFF,
        "decision": "accepted",
        "reason": "回执与项目状态一致",
        "evidence": EVIDENCE,
        "correlation_id": "action-review-0001",
        "expected_execution_ledger_id": 101,
        "expected_detail_sha256": EXECUTION_DETAIL_HASH,
        "expected_candidate_sha256": ACTION_CANDIDATE_HASH,
    }
    payload.update(overrides)
    action_id = payload.pop("action_id")
    staff = payload.pop("staff")
    return reviews.verify_action_result(action_id, staff, **payload)


def test_action_verification_commits_action_audit_and_tool_truth_atomically(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)

    result = _verify()

    assert result["ok"] is True
    assert result["tool_run_ids"] == [201]
    checklist = json.loads(
        conn.execute("SELECT result_checklist_json FROM vkpi_action_inbox WHERE id=7").fetchone()[0]
    )
    assert checklist["human_verification"]["decision"] == "accepted"
    audit = dict(
        conn.execute(
            "SELECT * FROM vkpi_action_execution_ledger WHERE endpoint='manual:verify-result'"
        ).fetchone()
    )
    detail = json.loads(audit["detail_json"])
    assert detail["evidence"] == EVIDENCE
    assert detail["execution_ledger_id"] == 101
    assert detail["tool_run_ids"] == [201]
    assert [
        row[0]
        for row in conn.execute("SELECT event_type FROM vkpi_event_ledger ORDER BY id").fetchall()
    ] == ["action_result_accepted", "agent_tool_run_accepted"]


def test_action_review_candidate_is_redacted_and_hash_bound(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    result = reviews.get_action_review_candidate(7)
    assert result["ok"] is True
    assert result["execution_ledger_id"] == 101
    assert result["detail_json"] == EXECUTION_DETAIL
    assert result["detail_json_canonical"] == review_contract.canonical_review_json(EXECUTION_DETAIL)
    assert result["detail_sha256"] == EXECUTION_DETAIL_HASH
    assert result["verification_plan"] == ["核对执行回执"]
    assert json.loads(result["candidate_canonical_json"]) == ACTION_CANDIDATE
    assert result["candidate_sha256"] == ACTION_CANDIDATE_HASH


def test_action_verification_rejects_changed_candidate(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _verify(expected_detail_sha256="a" * 64) == {
        "ok": False,
        "reason": "verification_candidate_changed",
    }
    assert _verify(expected_candidate_sha256="b" * 64) == {
        "ok": False,
        "reason": "verification_candidate_changed",
    }


def test_action_verification_idempotency_compares_reason_and_evidence(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    first = _verify()
    assert first["ok"] is True

    same = _verify()
    changed = _verify(reason="另一个结论")

    assert same["idempotent"] is True
    assert same["ledger_id"] == first["ledger_id"]
    assert same["tool_run_ids"] == [201]
    assert changed == {"ok": False, "reason": "verification_correlation_conflict"}
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_action_execution_ledger WHERE endpoint='manual:verify-result'"
    ).fetchone()[0] == 1


def test_action_verification_rejects_dry_run_as_business_execution(monkeypatch):
    conn = _db(execution_mode="dry_run")
    _wire(monkeypatch, conn)

    assert _verify() == {"ok": False, "reason": "successful_execution_receipt_required"}
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 0


def test_action_verification_rejects_ambiguous_tool_receipts(monkeypatch):
    conn = _db(tool_receipts=2)
    _wire(monkeypatch, conn)

    assert _verify() == {"ok": False, "reason": "ambiguous_agent_tool_run_receipts"}
    checklist = json.loads(
        conn.execute("SELECT result_checklist_json FROM vkpi_action_inbox WHERE id=7").fetchone()[0]
    )
    assert "human_verification" not in checklist


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"staff": {"id": 23, "organization_id": 9, "organization_scope_status": "resolved"}},
            "verification_scope_unavailable",
        ),
        ({"evidence": [{"source": "free_text", "reference": "trust me"}]}, "verification_evidence_required"),
    ],
)
def test_action_verification_rejects_cross_tenant_or_unstructured_evidence(
    monkeypatch, overrides, reason
):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _verify(**overrides) == {"ok": False, "reason": reason}


def test_action_verification_rolls_back_everything_when_tool_event_write_fails(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    original = event_ledger.insert_required
    calls = 0

    def fail_second_event(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("tool event unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(event_ledger, "insert_required", fail_second_event)

    assert _verify() == {"ok": False, "reason": "action_result_verification_failed"}
    assert json.loads(
        conn.execute("SELECT result_checklist_json FROM vkpi_action_inbox WHERE id=7").fetchone()[0]
    ) == {}
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_action_execution_ledger WHERE endpoint='manual:verify-result'"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 0
