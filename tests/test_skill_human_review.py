"""Hermetic truth gates for staff review of Marketing Brain skill runs."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.marketing_brain import skill_reviews
from app.domains.platform import event_ledger
from app.domains.platform import review_contract


STAFF = {"id": 17, "organization_id": 1, "organization_scope_status": "resolved"}
EVIDENCE = [
    {
        "source": "db_record",
        "reference": "vkpi_skill_runs:1",
        "type": "skill_output",
        "observed_at": "2026-08-11T00:00:00Z",
    }
]
DEFAULT_OUTPUT = {
    "recommendations": [{"handle": "lens-reviewer", "fit_reason": "AF 26mm creator"}],
    "rationale": "one evidence-backed candidate",
}
DEFAULT_OUTPUT_HASH = review_contract.review_snapshot_sha256(DEFAULT_OUTPUT)
DEFAULT_INPUT = {"product": "AF 26mm", "market": "US"}
DEFAULT_INPUT_HASH = review_contract.review_snapshot_sha256(DEFAULT_INPUT)


def _db(*, skill_name: str = "creator_match", output: dict | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_skill_runs (
            id INTEGER PRIMARY KEY,
            skill_name TEXT NOT NULL,
            skill_version TEXT NOT NULL,
            input_schema TEXT NOT NULL,
            model_used TEXT,
            prompt_version TEXT,
            accepted INTEGER,
            human_score REAL,
            business_result TEXT,
            output TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
        """INSERT INTO vkpi_skill_runs(
               id,skill_name,skill_version,input_schema,model_used,prompt_version,output
           ) VALUES (1,?,'v1',?,'rule','p1',?)""",
        (
            skill_name,
            json.dumps(DEFAULT_INPUT),
            json.dumps(output if output is not None else DEFAULT_OUTPUT),
        ),
    )
    conn.commit()
    return conn


def _wire(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(skill_reviews, "get_conn", lambda: conn)
    monkeypatch.setattr(skill_reviews, "table_exists", lambda name: True)
    monkeypatch.setattr(skill_reviews, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(event_ledger, "is_postgres_runtime", lambda: False)


def _review(**overrides):
    payload = {
        "run_id": 1,
        "staff": STAFF,
        "accepted": True,
        "human_score": 4.5,
        "business_result": "可直接用于 KOL 首轮筛选",
        "evidence": EVIDENCE,
        "correlation_id": "skill-review-0001",
        "expected_input_sha256": DEFAULT_INPUT_HASH,
        "expected_output_sha256": DEFAULT_OUTPUT_HASH,
    }
    payload.update(overrides)
    run_id = payload.pop("run_id")
    return skill_reviews.review_skill_run(run_id, **payload)


def test_skill_review_commits_structured_evidence_and_exact_event(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)

    result = _review()

    assert result == {
        "ok": True,
        "run_id": 1,
        "event_id": 1,
        "accepted": True,
        "human_score": 4.5,
        "idempotent": False,
    }
    run = dict(conn.execute("SELECT * FROM vkpi_skill_runs WHERE id=1").fetchone())
    assert run["accepted"] == 1
    assert run["human_score"] == 4.5
    event = dict(conn.execute("SELECT * FROM vkpi_event_ledger").fetchone())
    assert event["event_type"] == "skill_run_accepted"
    assert event["actor_type"] == "staff"
    assert event["actor_id"] == "17"
    assert event["organization_id"] == 1
    assert json.loads(event["payload_json"])["evidence"] == EVIDENCE
    assert json.loads(event["provenance_json"])["kind"] == "human_review"


def test_skill_review_candidate_is_redacted_and_hash_bound(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    result = skill_reviews.get_skill_review_candidate(1)
    assert result["ok"] is True
    assert result["input_snapshot"] == {"market": "US", "product": "AF 26mm"}
    assert result["input_sha256"] == DEFAULT_INPUT_HASH
    assert result["output_snapshot"] == DEFAULT_OUTPUT
    assert result["output_snapshot_json"] == review_contract.canonical_review_json(DEFAULT_OUTPUT)
    assert result["output_sha256"] == DEFAULT_OUTPUT_HASH


def test_skill_review_rejects_changed_candidate(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _review(expected_output_sha256="f" * 64) == {
        "ok": False,
        "reason": "skill_review_candidate_changed",
    }


def test_skill_review_idempotency_compares_the_full_payload(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _review()["ok"] is True

    same = _review()
    conflict = _review(evidence=[{"source": "ledger", "reference": "other:99", "type": "receipt"}])

    assert same["idempotent"] is True
    assert conflict == {"ok": False, "reason": "review_correlation_conflict"}
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"staff": {"id": 17, "organization_id": 2, "organization_scope_status": "resolved"}},
            "review_scope_unavailable",
        ),
        ({"evidence": [{"source": "unknown", "reference": "record:1"}]}, "review_evidence_required"),
        (
            {"evidence": [{"source": "url", "reference": "Authorization: Bearer private"}]},
            "review_evidence_required",
        ),
    ],
)
def test_skill_review_rejects_cross_tenant_or_unstructured_evidence(monkeypatch, overrides, reason):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _review(**overrides) == {"ok": False, "reason": reason}
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("skill_name", "output"),
    [
        ("test.creator_match", {"status": "ok"}),
        ("creator_match", {"status": "error", "reason": "provider timeout"}),
        ("creator_match", {"status": "missing_data"}),
        ("creator_match", {}),
    ],
)
def test_skill_review_rejects_nonproduction_or_nonreviewable_runs(
    monkeypatch, skill_name, output
):
    conn = _db(skill_name=skill_name, output=output)
    _wire(monkeypatch, conn)
    result = _review()
    assert result["ok"] is False
    assert result["reason"] in {"nonproduction_skill_run", "skill_run_output_not_reviewable"}


def test_creator_match_empty_run_is_not_learning_eligible(monkeypatch):
    empty = {"recommendations": [], "rationale": "honest empty"}
    conn = _db(output=empty)
    _wire(monkeypatch, conn)
    result = _review(
        expected_output_sha256=review_contract.review_snapshot_sha256(empty),
    )
    assert result == {"ok": False, "reason": "skill_run_output_not_reviewable"}


def test_skill_review_rolls_back_run_when_event_write_fails(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    monkeypatch.setattr(
        event_ledger,
        "insert_required",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("event unavailable")),
    )

    assert _review() == {"ok": False, "reason": "skill_review_failed"}
    run = dict(conn.execute("SELECT * FROM vkpi_skill_runs WHERE id=1").fetchone())
    assert run["accepted"] is None
    assert run["human_score"] is None
    assert run["business_result"] is None
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 0
