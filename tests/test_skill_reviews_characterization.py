"""Characterization tests for marketing_brain/skill_reviews.review_skill_run.

锁行为用:校验错误的优先级顺序、哈希绑定、幂等/冲突判定、非生产 run 拒审、
rowcount 竞态与回滚时机,降复杂度刀(CC 54 → ≤10)改完必须原样绿。
Marketing Brain 学习链 L3 红线:不碰 provider/LLM,纯本地 sqlite。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.marketing_brain import skill_reviews
from app.domains.platform import event_ledger, review_contract

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


def _db(
    *,
    skill_name: str = "creator_match",
    output: dict | None = None,
    accepted=None,
    human_score=None,
    business_result=None,
) -> sqlite3.Connection:
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
               id,skill_name,skill_version,input_schema,model_used,prompt_version,
               accepted,human_score,business_result,output
           ) VALUES (1,?,'v1',?,'rule','p1',?,?,?,?)""",
        (
            skill_name,
            json.dumps(DEFAULT_INPUT),
            accepted,
            human_score,
            business_result,
            json.dumps(output if output is not None else DEFAULT_OUTPUT),
        ),
    )
    conn.commit()
    return conn


def _wire(monkeypatch: pytest.MonkeyPatch, conn) -> None:
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


# ── 校验顺序与拒绝口径(错误优先级逐字锁定)────────────────────────────


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"run_id": "abc"}, "invalid_review_identity_or_score"),
        ({"human_score": "not-a-number"}, "invalid_review_identity_or_score"),
        ({"run_id": 0}, "invalid_review_identity_or_score"),
        ({"human_score": 5.5}, "human_score_out_of_range"),
        ({"human_score": -0.1}, "human_score_out_of_range"),
        ({"business_result": ""}, "business_result_required"),
        ({"evidence": []}, "review_evidence_required"),
        ({"correlation_id": "short"}, "review_correlation_required"),
        ({"expected_output_sha256": "zz"}, "review_candidate_required"),
        ({"expected_input_sha256": "Z" * 64}, "review_candidate_required"),
        ({"staff": None}, "review_scope_unavailable"),
    ],
)
def test_review_rejects_invalid_requests(monkeypatch, overrides, reason):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _review(**overrides) == {"ok": False, "reason": reason}
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 0


def test_review_error_precedence_rid_before_hash_before_tables(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    # rid<=0 优先于坏哈希
    assert _review(run_id=0, expected_output_sha256="bad")["reason"] == (
        "invalid_review_identity_or_score"
    )
    # 坏哈希优先于缺表
    monkeypatch.setattr(skill_reviews, "table_exists", lambda name: False)
    assert _review(expected_output_sha256="bad")["reason"] == "review_candidate_required"
    # 哈希合法才轮到缺表
    assert _review()["reason"] == "review_ledger_unavailable"


def test_review_accepts_uppercase_hash_via_normalization(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)

    result = _review(
        expected_input_sha256=DEFAULT_INPUT_HASH.upper(),
        expected_output_sha256=DEFAULT_OUTPUT_HASH.upper(),
    )

    assert result == {
        "ok": True,
        "run_id": 1,
        "event_id": 1,
        "accepted": True,
        "human_score": 4.5,
        "idempotent": False,
    }


def test_review_run_not_found(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _review(run_id=999) == {"ok": False, "reason": "skill_run_not_found"}


def test_review_rejects_input_hash_mismatch(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _review(expected_input_sha256="a" * 64) == {
        "ok": False,
        "reason": "skill_review_candidate_changed",
    }


@pytest.mark.parametrize("business_result", ["pytest", "demo", "dry_run", "smoke"])
def test_review_rejects_nonproduction_business_result_marker(monkeypatch, business_result):
    conn = _db(business_result=business_result)
    _wire(monkeypatch, conn)
    # 非生产标记优先于「已评审」判定。
    assert _review() == {"ok": False, "reason": "nonproduction_skill_run"}


def test_review_rejects_already_reviewed_columns_without_event(monkeypatch):
    conn = _db(accepted=0, human_score=2.0)
    _wire(monkeypatch, conn)
    assert _review() == {"ok": False, "reason": "skill_run_already_reviewed"}


def test_review_correlation_conflict_on_actor_mismatch(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _review()["ok"] is True

    other_staff = {"id": 99, "organization_id": 1, "organization_scope_status": "resolved"}
    conflict = _review(staff=other_staff)

    assert conflict == {"ok": False, "reason": "review_correlation_conflict"}
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 1


def test_review_idempotent_replay_returns_existing_event(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _review()["ok"] is True

    replay = _review()

    assert replay == {
        "ok": True,
        "run_id": 1,
        "event_id": 1,
        "accepted": True,
        "human_score": 4.5,
        "idempotent": True,
    }
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 1


class _RowcountZeroConn:
    """Proxy that reports rowcount=0 for the review UPDATE (simulated lost race)."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cursor = self._conn.execute(sql, params)
        if sql.lstrip().upper().startswith("UPDATE"):
            class _Zero:
                rowcount = 0

            return _Zero()
        return cursor

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_review_update_race_returns_state_changed_and_rolls_back(monkeypatch):
    raw = _db()
    conn = _RowcountZeroConn(raw)
    _wire(monkeypatch, conn)

    assert _review() == {"ok": False, "reason": "skill_review_state_changed"}
    run = dict(raw.execute("SELECT * FROM vkpi_skill_runs WHERE id=1").fetchone())
    assert run["accepted"] is None
    assert run["human_score"] is None
    assert run["business_result"] is None
    assert raw.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 0


def test_review_success_event_payload_and_provenance_exact(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)

    result = _review(accepted=False, human_score=1.5, business_result="首轮筛选质量不足")

    assert result["ok"] is True
    assert result["accepted"] is False
    event = dict(conn.execute("SELECT * FROM vkpi_event_ledger").fetchone())
    assert event["event_type"] == "skill_run_rejected"
    payload = json.loads(event["payload_json"])
    assert payload["accepted"] is False
    assert payload["human_score"] == 1.5
    assert payload["business_result"] == "首轮筛选质量不足"
    assert payload["evidence"] == EVIDENCE
    assert payload["correlation_id"] == "skill-review-0001"
    assert payload["output_sha256"] == DEFAULT_OUTPUT_HASH
    assert payload["input_sha256"] == DEFAULT_INPUT_HASH
    provenance = json.loads(event["provenance_json"])
    assert provenance == {
        "kind": "human_review",
        "source": "skill_studio",
        "evidence_count": 1,
        "evidence_verification": "staff_attestation_bound_to_skill_run",
        "server_bound_run_id": 1,
        "server_bound_output_sha256": DEFAULT_OUTPUT_HASH,
        "server_bound_input_sha256": DEFAULT_INPUT_HASH,
        "skill_version": "v1",
        "model_used": "rule",
        "prompt_version": "p1",
        "review_eligibility": "usable_production_output",
    }
    run = dict(conn.execute("SELECT * FROM vkpi_skill_runs WHERE id=1").fetchone())
    assert run["accepted"] == 0
    assert run["human_score"] == 1.5
    assert run["business_result"] == "首轮筛选质量不足"


def test_review_unexpected_exception_maps_to_failed(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    monkeypatch.setattr(
        skill_reviews,
        "_review_events",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ledger read broke")),
    )
    assert _review() == {"ok": False, "reason": "skill_review_failed"}


# ── _usable_production_output:五个 Skill 的服务器侧成功合约 ─────────────


@pytest.mark.parametrize(
    ("skill_name", "output", "usable"),
    [
        ("creator_match", DEFAULT_OUTPUT, True),
        ("creator_match", {"recommendations": []}, False),
        ("brief_generate", {"ok": True, "brief": {"hook": "钩子", "deliverables": ["v1"]}}, True),
        ("brief_generate", {"ok": True, "brief": {"hook": " ", "deliverables": ["v1"]}}, False),
        ("brief_generate", {"ok": False, "brief": {"hook": "钩子", "deliverables": ["v1"]}}, False),
        (
            "content_score",
            {"status": "ok", "source": {"target_id": "t1"}, "summary": "评过了"},
            True,
        ),
        ("content_score", {"status": "ok", "source": {}, "summary": "评过了"}, False),
        ("roi_review", {"status": "ready", "missing_data": False, "roi": {"value": 1}}, True),
        ("roi_review", {"status": "ready", "missing_data": True, "roi": {"value": 1}}, False),
        (
            "campaign_plan",
            {"status": "ok", "plan": {"timeline": ["w1"], "creator_mix": ["a"]}},
            True,
        ),
        ("campaign_plan", {"status": "ok", "plan": {"timeline": [], "creator_mix": ["a"]}}, False),
        ("unknown_skill", {"status": "ok"}, False),
    ],
)
def test_usable_production_output_contract(skill_name, output, usable):
    assert skill_reviews._usable_production_output(skill_name, output) is usable
