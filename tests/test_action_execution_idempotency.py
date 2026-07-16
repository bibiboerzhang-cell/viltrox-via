from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None, rowcount: int = 0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class _ClaimConnection:
    def __init__(self):
        self.status = "approved"
        self._lock = threading.Lock()
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params=()):
        if "SET status = 'executing'" in sql:
            with self._lock:
                if self.status == "approved":
                    self.status = "executing"
                    return _Cursor({"id": int(params[0]), "status": self.status}, 1)
                return _Cursor(None, 0)
        if "SELECT" in sql and "vkpi_action_inbox" in sql:
            return _Cursor(
                {
                    "id": int(params[0]),
                    "status": self.status,
                    "owner_staff_id": None,
                    "payload_json": {},
                    "evidence_refs_json": [],
                    "result_checklist_json": {},
                    "verification_plan_json": {},
                    "affected_tables_json": [],
                },
                1,
            )
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _action(action_id: int = 901) -> dict[str, Any]:
    return {
        "id": action_id,
        "status": "approved",
        "category": "idempotency_probe",
        "entity_type": "",
        "entity_id": "",
        "touches_v6_fit": False,
        "estimated_cost_cents": 0,
        "uses_llm": False,
        "writes_business_data": True,
        "suggested_endpoint": "POST /probe",
        "payload_json": {},
        "affected_tables_json": [],
    }


def test_claim_action_execution_compare_and_set_allows_one_winner(monkeypatch):
    from app.domains.actions import inbox

    conn = _ClaimConnection()
    monkeypatch.setattr(inbox, "get_conn", lambda: conn)
    monkeypatch.setattr(inbox, "table_exists", lambda name: True)

    staff = {"id": 11, "role": "manager"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: inbox.claim_action_execution(901, staff), range(2)))

    assert sum(bool(row["ok"]) for row in results) == 1
    loser = next(row for row in results if not row["ok"])
    assert loser["reason"] == "execution_already_claimed"
    assert loser["status"] == "executing"
    assert conn.status == "executing"


def test_concurrent_execute_calls_handler_side_effect_once(monkeypatch):
    from app.domains.actions import executors

    action = _action()
    claim_state = {"status": "approved"}
    claim_lock = threading.Lock()
    both_at_claim = threading.Barrier(2)
    side_effect_calls = 0
    side_effect_lock = threading.Lock()

    monkeypatch.setattr(executors.inbox, "get_action", lambda aid, staff=None: dict(action))
    monkeypatch.setattr(
        executors.validators,
        "validate_action",
        lambda row: {"ok": True, "reason": "", "checks": {}},
    )

    def _claim(aid, staff=None):
        both_at_claim.wait(timeout=2)
        with claim_lock:
            if claim_state["status"] != "approved":
                return {"ok": False, "reason": "execution_already_claimed", "status": claim_state["status"]}
            claim_state["status"] = "executing"
            return {"ok": True, "status": "executing", "action_id": aid}

    def _handler(row, staff=None):
        nonlocal side_effect_calls
        with side_effect_lock:
            side_effect_calls += 1
        time.sleep(0.03)
        return {"outcome": "success", "reason": "", "detail": {"external_write": True}}

    def _finalize(**kwargs):
        with claim_lock:
            assert claim_state["status"] == "executing"
            claim_state["status"] = "executed"
        return {"ok": True, "status": "executed", "ledger_id": 77}

    monkeypatch.setattr(executors.inbox, "claim_action_execution", _claim)
    monkeypatch.setitem(executors._DISPATCH, "idempotency_probe", _handler)
    monkeypatch.setattr(executors, "_finalize_claimed_execution", _finalize)
    monkeypatch.setattr(executors, "_snapshot_table_counts", lambda tables: {})
    monkeypatch.setattr(executors, "_record_execution_feedback", lambda *a, **k: None)
    monkeypatch.setattr(executors, "_record_outcome_eval", lambda *a, **k: None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: executors.execute_action(901, {"id": 11}), range(2)))

    assert side_effect_calls == 1
    assert claim_state["status"] == "executed"
    assert sum(row["outcome"] == "success" for row in results) == 1
    loser = next(row for row in results if row["outcome"] == "skipped")
    assert loser["reason"] == "execution_already_claimed"


def test_handler_exception_finalizes_claim_as_failed(monkeypatch):
    from app.domains.actions import executors

    action = _action(902)
    finalized: list[dict[str, Any]] = []
    monkeypatch.setattr(executors.inbox, "get_action", lambda aid, staff=None: dict(action))
    monkeypatch.setattr(
        executors.inbox,
        "claim_action_execution",
        lambda aid, staff=None: {"ok": True, "status": "executing", "action_id": aid},
    )
    monkeypatch.setattr(executors.validators, "validate_action", lambda row: {"ok": True, "checks": {}})
    monkeypatch.setitem(
        executors._DISPATCH,
        "idempotency_probe",
        lambda row, staff=None: (_ for _ in ()).throw(RuntimeError("side effect failed")),
    )
    monkeypatch.setattr(executors, "_snapshot_table_counts", lambda tables: {})

    def _finalize(**kwargs):
        finalized.append(kwargs)
        return {"ok": True, "status": "failed", "ledger_id": 88}

    monkeypatch.setattr(executors, "_finalize_claimed_execution", _finalize)
    result = executors.execute_action(902, {"id": 11})

    assert result["ok"] is False
    assert result["outcome"] == "failed"
    assert result["reason"] == "exception"
    assert len(finalized) == 1
    assert finalized[0]["outcome"] == "failed"
    assert finalized[0]["checklist"]["outcome"] == "failed"


def test_invalid_handler_result_finalizes_claim_as_failed(monkeypatch):
    from app.domains.actions import executors

    action = _action(905)
    finalized: list[dict[str, Any]] = []
    monkeypatch.setattr(executors.inbox, "get_action", lambda aid, staff=None: dict(action))
    monkeypatch.setattr(
        executors.inbox,
        "claim_action_execution",
        lambda aid, staff=None: {"ok": True, "status": "executing", "action_id": aid},
    )
    monkeypatch.setattr(executors.validators, "validate_action", lambda row: {"ok": True, "checks": {}})
    monkeypatch.setitem(executors._DISPATCH, "idempotency_probe", lambda row, staff=None: None)
    monkeypatch.setattr(executors, "_snapshot_table_counts", lambda tables: {})

    def _finalize(**kwargs):
        finalized.append(kwargs)
        return {"ok": True, "status": "failed", "ledger_id": 89}

    monkeypatch.setattr(executors, "_finalize_claimed_execution", _finalize)
    result = executors.execute_action(905, {"id": 11})

    assert result["ok"] is False
    assert result["reason"] == "invalid_handler_result"
    assert finalized[0]["outcome"] == "failed"


class _FinalizeConnection:
    def __init__(self, *, update_rowcount: int = 1):
        self.update_rowcount = update_rowcount
        self.calls: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params=()):
        self.calls.append("ledger" if "INSERT INTO vkpi_action_execution_ledger" in sql else "status")
        if "INSERT INTO vkpi_action_execution_ledger" in sql:
            return _Cursor({"id": 321}, 1)
        return _Cursor(None, self.update_rowcount)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_finalize_commits_ledger_checklist_and_status_as_one_transaction(monkeypatch):
    from app.domains.actions import executors

    conn = _FinalizeConnection()
    monkeypatch.setattr(executors, "get_conn", lambda: conn)
    monkeypatch.setattr(executors, "table_exists", lambda name: True)
    result = executors._finalize_claimed_execution(
        action=_action(903),
        action_id=903,
        staff={"id": 11},
        outcome="success",
        detail={"external_write": True},
        checklist={"outcome": "success"},
    )

    assert result == {"ok": True, "status": "executed", "ledger_id": 321}
    assert conn.calls == ["ledger", "status"]
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_finalize_claim_loss_rolls_back_ledger(monkeypatch):
    from app.domains.actions import executors

    conn = _FinalizeConnection(update_rowcount=0)
    monkeypatch.setattr(executors, "get_conn", lambda: conn)
    monkeypatch.setattr(executors, "table_exists", lambda name: True)
    result = executors._finalize_claimed_execution(
        action=_action(904),
        action_id=904,
        staff={"id": 11},
        outcome="success",
        detail={},
        checklist={"outcome": "success"},
    )

    assert result["ok"] is False
    assert result["reason"] == "execution_claim_lost"
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_finalize_without_ledger_fails_closed_and_keeps_claim(monkeypatch):
    from app.domains.actions import executors

    conn = _FinalizeConnection()
    monkeypatch.setattr(executors, "get_conn", lambda: conn)
    monkeypatch.setattr(executors, "table_exists", lambda name: False)
    result = executors._finalize_claimed_execution(
        action=_action(906),
        action_id=906,
        staff={"id": 11},
        outcome="success",
        detail={},
        checklist={"outcome": "success"},
    )

    assert result["ok"] is False
    assert result["reason"] == "execution_finalize_failed"
    assert result["status"] == "executing"
    assert conn.calls == []
    assert conn.commits == 0
    assert conn.rollbacks == 1
