"""Approval receipt, claim, and manual-completion truth gates."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.domains.actions import approval_evidence
from app.domains.platform import event_ledger

MANAGER = {
    "id": 7, "role": "manager", "organization_id": 1,
    "organization_scope_status": "resolved",
}
OTHER_MANAGER = {
    "id": 9, "role": "manager", "organization_id": 1,
    "organization_scope_status": "resolved",
}
OWNER = {
    "id": 8, "role": "employee", "organization_id": 1,
    "organization_scope_status": "resolved",
}


def _db(*, status: str = "suggested", endpoint: str = "", writes: int = 0) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_action_inbox (
          id INTEGER PRIMARY KEY, dedupe_key TEXT, category TEXT, title TEXT, detail TEXT,
          priority TEXT, entity_type TEXT, entity_id TEXT, suggested_endpoint TEXT,
          estimated_cost_cents INTEGER, writes_business_data INTEGER, uses_llm INTEGER,
          requires_approval INTEGER, owner_staff_id INTEGER, reason TEXT, payload_json TEXT,
          touches_v6_fit INTEGER, expected_gain TEXT, risk_level TEXT,
          evidence_refs_json TEXT, verification_plan_json TEXT, affected_tables_json TEXT,
          result_checklist_json TEXT, approval_reason TEXT, status TEXT,
          approved_by_staff_id INTEGER, approved_at TEXT, approval_snapshot_sha256 TEXT,
          created_at TEXT, updated_at TEXT
        );
        CREATE TABLE vkpi_event_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT, organization_id INTEGER, event_type TEXT,
          entity_type TEXT, entity_id TEXT, actor_type TEXT, actor_id TEXT, source TEXT,
          payload_json TEXT, trace_id TEXT, confidence REAL, provenance_json TEXT,
          occurred_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX uq_required_approval_test
          ON vkpi_event_ledger(organization_id,entity_type,entity_id,source)
          WHERE event_type='action_approved' AND source='action_inbox.required_approval';
        CREATE TABLE vkpi_action_execution_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT, action_id INTEGER, category TEXT,
          dedupe_key TEXT, actor_staff_id INTEGER, mode TEXT, outcome TEXT, endpoint TEXT,
          cost_cents INTEGER, error TEXT, detail_json TEXT, created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO vkpi_action_inbox VALUES "
        "(1,'approval:test:1','gtm_bet','Approve bet','detail','low','bet','1',?,"
        "0,?,0,1,8,'reason','{}',0,'gain','low','[]','[\"verify\"]','[]','{}',"
        "NULL,?,NULL,NULL,NULL,'now','now')",
        (endpoint, writes, status),
    )
    conn.commit()
    return conn


def _patch(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(approval_evidence, "get_conn", lambda: conn)
    monkeypatch.setattr(approval_evidence, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(approval_evidence, "table_exists", lambda _name: True)
    monkeypatch.setattr(event_ledger, "is_postgres_runtime", lambda: False)


def test_approval_row_and_required_event_commit_atomically_and_replay_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    _patch(monkeypatch, conn)

    approved = approval_evidence.approve_action(1, MANAGER, reason="checked")
    replay = approval_evidence.approve_action(1, MANAGER, reason="checked")
    conflict = approval_evidence.approve_action(1, OTHER_MANAGER, reason="checked")

    assert approved["ok"] is True and approved["idempotent"] is False
    assert len(approved["approval_snapshot_sha256"]) == 64
    assert replay["ok"] is True and replay["idempotent"] is True
    assert conflict == {"ok": False, "reason": "approval_replay_conflict", "action_id": 1}
    row = dict(conn.execute(
        "SELECT status,approved_by_staff_id,approved_at,approval_snapshot_sha256 "
        "FROM vkpi_action_inbox WHERE id=1"
    ).fetchone())
    assert row["status"] == "approved"
    assert row["approved_by_staff_id"] == 7 and row["approved_at"]
    assert row["approval_snapshot_sha256"] == approved["approval_snapshot_sha256"]
    event = dict(conn.execute("SELECT * FROM vkpi_event_ledger").fetchone())
    provenance = json.loads(event["provenance_json"])
    assert event["source"] == "action_inbox.required_approval"
    assert provenance["approval_snapshot_sha256"] == approved["approval_snapshot_sha256"]


def test_required_event_failure_rolls_back_approval_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    _patch(monkeypatch, conn)
    monkeypatch.setattr(
        event_ledger, "insert_required", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("event unavailable")
        ),
    )

    result = approval_evidence.approve_action(1, MANAGER, reason="checked")

    assert result["reason"] == "approval_persist_failed"
    row = conn.execute(
        "SELECT status,approved_at,approval_snapshot_sha256 FROM vkpi_action_inbox WHERE id=1"
    ).fetchone()
    assert tuple(row) == ("suggested", None, None)
    assert conn.execute("SELECT COUNT(*) FROM vkpi_event_ledger").fetchone()[0] == 0


def test_approval_reason_and_manual_note_reject_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    _patch(monkeypatch, conn)
    rejected = approval_evidence.approve_action(
        1, MANAGER, reason="token%253Dsk-supersecret123456",
    )
    assert rejected["reason"] == "approval_reason_invalid"
    assert conn.execute("SELECT status FROM vkpi_action_inbox").fetchone()[0] == "suggested"
    assert approval_evidence.approve_action(1, MANAGER, reason="checked")["ok"] is True
    note = approval_evidence.mark_done_action(
        1, OWNER, note="https://example.test/cb?x=1&token=sk-supersecret123456",
    )
    assert note["reason"] == "manual_note_invalid"
    assert conn.execute("SELECT COUNT(*) FROM vkpi_action_execution_ledger").fetchone()[0] == 0


def test_other_organization_manager_is_rejected_before_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def bomb() -> sqlite3.Connection:
        nonlocal calls
        calls += 1
        raise AssertionError("org2 actor must fail before DB")

    monkeypatch.setattr(approval_evidence, "get_conn", bomb)
    org2 = {
        "id": 22, "role": "manager", "organization_id": 2,
        "organization_scope_status": "resolved",
    }
    assert approval_evidence.approve_action(1, org2, reason="checked")["reason"] == (
        "not_found_or_out_of_scope"
    )
    assert approval_evidence.claim_action_execution(1, org2)["reason"] == (
        "not_found_or_out_of_scope"
    )
    assert approval_evidence.mark_done_action(1, org2, note="done")["reason"] == (
        "not_found_or_out_of_scope"
    )
    assert calls == 0


def test_claim_rehashes_contract_and_rejects_post_approval_tamper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    _patch(monkeypatch, conn)
    assert approval_evidence.approve_action(1, MANAGER, reason="checked")["ok"] is True
    conn.execute("UPDATE vkpi_action_inbox SET entity_id='forged' WHERE id=1")
    conn.commit()

    claim = approval_evidence.claim_action_execution(1, MANAGER)

    assert claim["reason"] == "approval_snapshot_mismatch"
    assert conn.execute("SELECT status FROM vkpi_action_inbox WHERE id=1").fetchone()[0] == "approved"


def test_legacy_approved_row_can_only_be_upgraded_by_atomic_manager_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db(status="approved")
    _patch(monkeypatch, conn)

    upgraded = approval_evidence.approve_action(1, MANAGER, reason="checked")

    assert upgraded["ok"] is True and upgraded["upgraded_legacy_approval"] is True
    assert approval_evidence.verified_approval_snapshot(
        conn, dict(conn.execute(f"SELECT {approval_evidence.APPROVAL_CONTRACT_COLUMNS} "
                                "FROM vkpi_action_inbox WHERE id=1").fetchone()),
    ) is True


def test_resolved_org1_owner_can_claim_manager_approved_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    _patch(monkeypatch, conn)
    assert approval_evidence.approve_action(1, MANAGER, reason="checked")["ok"] is True

    claimed = approval_evidence.claim_action_execution(1, OWNER)

    assert claimed == {"ok": True, "status": "executing", "action_id": 1}


def test_concurrent_claim_has_exactly_one_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    source = _db()
    _patch(monkeypatch, source)
    assert approval_evidence.approve_action(1, MANAGER, reason="checked")["ok"] is True
    db_path = tmp_path / "approval-claim.sqlite"
    target = sqlite3.connect(db_path)
    source.backup(target)
    target.close()
    source.close()

    local = threading.local()
    opened: list[sqlite3.Connection] = []
    opened_lock = threading.Lock()

    def get_thread_conn() -> sqlite3.Connection:
        conn = getattr(local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            local.conn = conn
            with opened_lock:
                opened.append(conn)
        return conn

    monkeypatch.setattr(approval_evidence, "get_conn", get_thread_conn)
    monkeypatch.setattr(approval_evidence, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(approval_evidence, "table_exists", lambda _name: True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: approval_evidence.claim_action_execution(1, MANAGER), range(2),
            ))
    finally:
        for conn in opened:
            conn.close()

    assert sum(result["ok"] for result in results) == 1
    loser = next(result for result in results if not result["ok"])
    assert loser["reason"] == "execution_already_claimed"
    check = sqlite3.connect(db_path)
    assert check.execute("SELECT status FROM vkpi_action_inbox WHERE id=1").fetchone()[0] == "executing"
    check.close()


def test_mark_done_requires_verified_approval_and_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    _patch(monkeypatch, conn)
    assert approval_evidence.approve_action(1, MANAGER, reason="checked")["ok"] is True

    done = approval_evidence.mark_done_action(1, OWNER, note="completed outside")

    assert done["ok"] is True and done["status"] == "executed"
    ledger = dict(conn.execute("SELECT * FROM vkpi_action_execution_ledger").fetchone())
    assert ledger["endpoint"] == "manual:mark-done"
    detail = json.loads(ledger["detail_json"])
    assert len(detail["approval_snapshot_sha256"]) == 64


def test_fake_approved_or_business_write_cannot_use_mark_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _db(status="approved")
    _patch(monkeypatch, fake)
    assert approval_evidence.mark_done_action(1, OWNER, note="fake")["reason"] == (
        "approval_snapshot_mismatch"
    )
    assert fake.execute("SELECT COUNT(*) FROM vkpi_action_execution_ledger").fetchone()[0] == 0

    business = _db(endpoint="local-action:any", writes=1)
    _patch(monkeypatch, business)
    assert approval_evidence.approve_action(1, MANAGER, reason="checked")["ok"] is True
    blocked = approval_evidence.mark_done_action(1, OWNER, note="bypass")
    assert blocked["reason"] == "manual_execution_not_allowed"
    assert business.execute("SELECT status FROM vkpi_action_inbox").fetchone()[0] == "approved"


def test_migration_278_freezes_approval_event_action_contract_and_terminal_receipt() -> None:
    root = Path(__file__).resolve().parents[1]
    up_path = root / "migrations/278_vkpi_action_approval_evidence.sql"
    down_path = root / "migrations/278_vkpi_action_approval_evidence_down.sql"
    sql = up_path.read_text(encoding="utf-8")
    down = down_path.read_text(encoding="utf-8")
    assert "approved_by_staff_id" in sql
    assert "approved_at" in sql
    assert "approval_snapshot_sha256" in sql
    assert "trg_vkpi_required_action_approval_event_immutable" in sql
    assert "BEFORE UPDATE OR DELETE ON vkpi_event_ledger" in sql
    assert "NEW.event_type = 'action_approved'" in sql
    assert "trg_vkpi_approved_action_contract_immutable" in sql
    assert "OLD.status IN ('approved', 'executing', 'executed', 'failed')" in sql
    assert "legacy approved Action must be evidence-sealed before transition" in sql
    assert "trg_vkpi_terminal_agent_tool_run_immutable" in sql
    assert "OLD.status IN ('executed', 'failed', 'skipped')" in sql
    assert "NEW.inputs_json IS DISTINCT FROM OLD.inputs_json" in sql
    assert "source_shipment_id" in sql
    assert "uq_vkpi_observation_window_source_shipment" in sql
    assert "REFERENCES staff(id) ON DELETE RESTRICT" in sql
    assert "BEGIN;" not in sql.upper() and "COMMIT;" not in sql.upper()
    assert "278_vkpi_action_approval_evidence.sql" in down
    # Reverse dependency order: triggers/functions before their indexes/columns.
    assert down.index("trg_vkpi_approved_action_contract_immutable") < down.index(
        "DROP COLUMN IF EXISTS approval_snapshot_sha256"
    )
    assert down.index("trg_vkpi_sourced_observation_window_identity_immutable") < down.index(
        "DROP COLUMN IF EXISTS source_shipment_id"
    )


@pytest.mark.pg
def test_migration_278_up_freezes_receipts_and_down_removes_guards(pg_dsn: str) -> None:
    import psycopg
    from psycopg import errors, sql

    root = Path(__file__).resolve().parents[1]
    up = (root / "migrations/278_vkpi_action_approval_evidence.sql").read_text(encoding="utf-8")
    down = (root / "migrations/278_vkpi_action_approval_evidence_down.sql").read_text(
        encoding="utf-8"
    )
    schema = f"vkpi_approval_278_{uuid.uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            conn.execute(
                """
                CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
                INSERT INTO schema_migrations VALUES ('278_vkpi_action_approval_evidence.sql');
                CREATE TABLE staff (id BIGINT PRIMARY KEY);
                INSERT INTO staff VALUES (7);
                CREATE TABLE vkpi_shipments (id BIGINT PRIMARY KEY);
                INSERT INTO vkpi_shipments VALUES (61);
                CREATE TABLE vkpi_action_inbox (
                  id BIGINT PRIMARY KEY, dedupe_key TEXT, category TEXT, title TEXT,
                  detail TEXT, priority TEXT, entity_type TEXT, entity_id TEXT,
                  suggested_endpoint TEXT, estimated_cost_cents BIGINT,
                  writes_business_data BOOLEAN, uses_llm BOOLEAN, requires_approval BOOLEAN,
                  owner_staff_id BIGINT, reason TEXT, payload_json JSONB,
                  touches_v6_fit BOOLEAN, expected_gain TEXT, risk_level TEXT,
                  evidence_refs_json JSONB, verification_plan_json JSONB,
                  affected_tables_json JSONB, approval_reason TEXT, status TEXT
                );
                INSERT INTO vkpi_action_inbox VALUES (
                  1,'plan:1:step:0','orchestrated_step','approved title','detail','medium',
                  'project','71','local-action:project_observation',0,TRUE,FALSE,TRUE,7,
                  'reason','{}',FALSE,'gain','low','[]','[]','[]','legacy','approved'
                );
                CREATE TABLE vkpi_project_content_observation_windows (
                  id BIGINT PRIMARY KEY, project_id BIGINT, assignment_id BIGINT,
                  kol_pool_id BIGINT, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
                  status TEXT
                );
                CREATE TABLE vkpi_event_ledger (
                  id BIGINT PRIMARY KEY, organization_id BIGINT, event_type TEXT,
                  entity_type TEXT, entity_id TEXT, source TEXT
                );
                CREATE TABLE vkpi_agent_tool_run (
                  id BIGINT PRIMARY KEY, plan_id BIGINT, tool_id TEXT, step_index INTEGER,
                  inputs_json JSONB, output_ref TEXT, cost_cents BIGINT, status TEXT,
                  error TEXT, executed_at TIMESTAMPTZ
                );
                """
            )
            conn.execute(up)
            conn.execute(
                "UPDATE vkpi_action_inbox SET approval_reason='checked',"
                "approved_by_staff_id=7,approved_at=NOW(),"
                "approval_snapshot_sha256=repeat('a',64) WHERE id=1"
            )
            conn.execute(
                """
                INSERT INTO vkpi_event_ledger VALUES (
                  1,1,'action_approved','action','1','action_inbox.required_approval'
                );
                INSERT INTO vkpi_event_ledger VALUES (2,1,'other','action','2','other');
                INSERT INTO vkpi_agent_tool_run VALUES (
                  1,1,'check_project_observation',0,'{}','ledger:1',0,'executed','',NOW()
                );
                INSERT INTO vkpi_agent_tool_run VALUES (
                  2,1,'check_project_observation',0,'{}','',0,'planned','',NULL
                );
                INSERT INTO vkpi_project_content_observation_windows VALUES (
                  1,71,81,91,NOW(),NOW() + INTERVAL '30 days','pending',61
                );
                """
            )
            with pytest.raises(errors.RaiseException):
                conn.execute("UPDATE vkpi_action_inbox SET title='forged' WHERE id=1")
            with pytest.raises(errors.RaiseException):
                conn.execute("DELETE FROM vkpi_event_ledger WHERE id=1")
            with pytest.raises(errors.RaiseException):
                conn.execute(
                    "UPDATE vkpi_event_ledger SET event_type='action_approved',"
                    "source='action_inbox.required_approval' WHERE id=2"
                )
            with pytest.raises(errors.RaiseException):
                conn.execute("UPDATE vkpi_agent_tool_run SET inputs_json='{}' WHERE id=1")
            with pytest.raises(errors.RaiseException):
                conn.execute("UPDATE vkpi_agent_tool_run SET inputs_json='{\"forged\":true}' WHERE id=2")
            with pytest.raises(errors.RaiseException):
                conn.execute(
                    "UPDATE vkpi_project_content_observation_windows SET project_id=72 WHERE id=1"
                )
            conn.execute("UPDATE vkpi_project_content_observation_windows SET status='closed' WHERE id=1")

            conn.execute(down)
            conn.execute("UPDATE vkpi_action_inbox SET title='after-down' WHERE id=1")
            conn.execute("DELETE FROM vkpi_event_ledger WHERE id=1")
            conn.execute("UPDATE vkpi_agent_tool_run SET inputs_json='{}' WHERE id=1")
            conn.execute("DELETE FROM vkpi_project_content_observation_windows WHERE id=1")
            columns = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='vkpi_action_inbox'"
            ).fetchall()
            assert "approval_snapshot_sha256" not in {row[0] for row in columns}
        finally:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
