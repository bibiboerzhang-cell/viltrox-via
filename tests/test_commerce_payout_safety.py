"""Payout safety uses only an in-memory synthetic database; no payment provider."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from app.services.commerce import payouts


@pytest.fixture
def payout_db(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE payout_cycles (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, processed_by INTEGER,
            processed_at TEXT, start_date TEXT, end_date TEXT, label TEXT
        );
        CREATE TABLE payouts (
            id INTEGER PRIMARY KEY, cycle_id TEXT NOT NULL, user_id INTEGER,
            amount_cents INTEGER NOT NULL, currency TEXT, method TEXT,
            status TEXT NOT NULL, paid_at TEXT, paid_tx_id TEXT,
            failed_at TEXT, failed_reason TEXT, hold_reason TEXT,
            approved_at TEXT, approved_by INTEGER, gmv_cents INTEGER,
            order_count INTEGER, order_ids_json TEXT, method_details TEXT
        );
        CREATE TABLE payout_disputes (
            id INTEGER PRIMARY KEY, payout_id INTEGER, status TEXT,
            resolved_by INTEGER, resolved_at TEXT, resolution_note TEXT
        );
        INSERT INTO payout_cycles (id, status) VALUES ('synthetic-cycle', 'active');
        INSERT INTO payouts (id, cycle_id, user_id, amount_cents, currency, method, status)
        VALUES (1, 'synthetic-cycle', 101, 1250, 'USD', 'paypal', 'approved');
    """)
    conn.commit()
    monkeypatch.setattr(payouts, "get_conn", lambda: conn)
    yield conn
    conn.close()


def _payout(conn):
    return dict(conn.execute("SELECT * FROM payouts WHERE id=1").fetchone())


@pytest.mark.parametrize("method", ["paypal", "bank", "unsupported"])
def test_unconnected_provider_never_marks_paid_or_completes_cycle(payout_db, method):
    payout_db.execute("UPDATE payouts SET method=?", (method,))
    payout_db.commit()

    result = payouts.process_cycle("synthetic-cycle", 7)

    assert result["processed_count"] == 0
    assert result["status"] == "blocked"
    assert result["provider_calls_performed"] is False
    assert result["cycle_complete"] is False
    assert _payout(payout_db)["status"] == "approved"
    assert _payout(payout_db)["paid_tx_id"] is None
    assert _payout(payout_db)["paid_at"] is None
    cycle = payout_db.execute("SELECT * FROM payout_cycles").fetchone()
    assert cycle["status"] == "active"
    assert cycle["processed_at"] is None


@pytest.mark.parametrize("state", ["paid", "paying", "payment_unknown", "failed"])
@pytest.mark.parametrize("operation", ["hold", "release", "adjust"])
def test_manual_mutations_cannot_reopen_unsettled_or_terminal_payment(payout_db, state, operation):
    payout_db.execute("UPDATE payouts SET status=?", (state,))
    payout_db.commit()
    before = _payout(payout_db)
    actions = {
        "hold": lambda: payouts.hold_one(1, "synthetic reason", 7),
        "release": lambda: payouts.release_one(1, 7),
        "adjust": lambda: payouts.adjust_one(1, 500, "synthetic correction", 7),
    }

    with pytest.raises(ValueError):
        actions[operation]()

    assert _payout(payout_db) == before


def _receipt(payout, **changes):
    receipt = payouts.PayoutDispatchReceipt(
        payout_id=payout["id"], method=payout["method"], amount_cents=payout["amount_cents"],
        currency=payout["currency"], status="paid", transaction_id="synthetic-confirmed-123",
    )
    return replace(receipt, **changes)


@pytest.mark.parametrize("receipt", [None, "", "pp_stub_1_123", "bank_stub_1", "some-tx", {},
                                     {"status": "paid", "transaction_id": "some-tx"}])
def test_untyped_or_invalid_receipts_stay_unknown(payout_db, monkeypatch, receipt):
    calls = []
    monkeypatch.setattr(payouts, "_dispatch_payout", lambda p: calls.append(p) or receipt)
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert len(calls) == 1
    assert result["status"] == "unknown"
    assert result["processed_count"] == 0
    assert result["provider_calls_performed"] is None
    assert result["cycle_complete"] is False
    assert _payout(payout_db)["status"] == "payment_unknown"
    assert _payout(payout_db)["paid_tx_id"] is None
    assert _payout(payout_db)["paid_at"] is None
    with pytest.raises(ValueError):
        payouts.process_cycle("synthetic-cycle", 7)
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"payout_id": 2}, {"payout_id": True}, {"amount_cents": 1251}, {"amount_cents": True},
    {"currency": "EUR"}, {"method": "bank"}, {"status": "accepted"}, {"status": "queued"},
    {"transaction_id": "pp_stub_1"}, {"transaction_id": "BANK_STUB_1"},
    {"transaction_id": ""}, {"transaction_id": " x "}, {"transaction_id": "x\ny"},
    {"transaction_id": None}, {"transaction_id": "x" * 201},
])
def test_receipt_must_match_exact_claimed_transfer(payout_db, monkeypatch, changes):
    monkeypatch.setattr(payouts, "_dispatch_payout", lambda p: _receipt(p, **changes))
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert result["processed_count"] == 0
    assert result["status"] == "unknown"
    assert _payout(payout_db)["status"] == "payment_unknown"


@pytest.mark.parametrize("status", ["paid", "failed"])
def test_only_confirmed_server_receipt_is_terminal(payout_db, monkeypatch, status):
    calls = []
    monkeypatch.setattr(payouts, "_dispatch_payout", lambda p: calls.append(p) or _receipt(p, status=status))
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert result["status"] == "processed"
    assert result["cycle_complete"] is True
    assert result["provider_calls_performed"] is True
    assert result["processed_count"] == int(status == "paid")
    assert _payout(payout_db)["status"] == status
    assert bool(_payout(payout_db)["paid_at"]) is (status == "paid")
    assert bool(_payout(payout_db)["failed_at"]) is (status == "failed")
    with pytest.raises(ValueError):
        payouts.process_cycle("synthetic-cycle", 7)
    assert len(calls) == 1


def test_unexpected_provider_failure_is_unknown_and_no_sensitive_error_echo(payout_db, monkeypatch):
    def fail(_p):
        raise RuntimeError("synthetic-private-bank-account")

    monkeypatch.setattr(payouts, "_dispatch_payout", fail)
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert result["status"] == "unknown"
    assert result["failed"] == []
    assert _payout(payout_db)["failed_at"] is None
    assert "synthetic-private" not in str(result)
    assert "synthetic-private" not in str(_payout(payout_db))


@pytest.mark.parametrize("state", ["pending", "held", "paying", "payment_unknown", "invalid_legacy"])
def test_every_unresolved_state_prevents_cycle_completion(payout_db, monkeypatch, state):
    payout_db.execute("UPDATE payouts SET status=?", (state,))
    payout_db.commit()
    monkeypatch.setattr(payouts, "_dispatch_payout", lambda _p: pytest.fail("must not dispatch"))
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert result["cycle_complete"] is False
    assert result["processed_count"] == 0
    assert result["unresolved_count"] == 1
    assert payout_db.execute("SELECT processed_at FROM payout_cycles").fetchone()[0] is None


@pytest.mark.parametrize("state", ["paid", "paying", "payment_unknown", "failed"])
def test_dispute_overturn_cannot_reopen_existing_payment(payout_db, state):
    payout_db.execute("UPDATE payouts SET status=?", (state,))
    payout_db.execute("INSERT INTO payout_disputes (id,payout_id,status) VALUES (8,1,'open')")
    payout_db.commit()
    before = _payout(payout_db)
    payouts.resolve_dispute(8, "overturn", "synthetic note", 7)
    assert _payout(payout_db) == before


class _BeforeClaimConnection:
    def __init__(self, conn, callback):
        self.conn, self.callback = conn, callback

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def execute(self, sql, params=()):
        if "UPDATE payouts SET status='paying'" in sql and self.callback:
            callback, self.callback = self.callback, None
            callback()
        return self.conn.execute(sql, params)


def test_claim_uses_latest_reapproved_amount_not_stale_query_snapshot(payout_db, monkeypatch):
    observed = []

    def change_approval():
        payouts.hold_one(1, "change amount", 8)
        payouts.adjust_one(1, 2500, "new amount", 8)
        payouts.approve_one(1, 8)

    wrapped = _BeforeClaimConnection(payout_db, change_approval)
    monkeypatch.setattr(payouts, "get_conn", lambda: wrapped)
    monkeypatch.setattr(payouts, "_dispatch_payout", lambda p: observed.append(p) or _receipt(p))
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert result["processed_count"] == 1
    assert observed[0]["amount_cents"] == 2500
    assert observed[0]["approved_by"] == 8
    assert observed[0]["status"] == "paying"


def test_lost_claim_does_not_dispatch(payout_db, monkeypatch):
    wrapped = _BeforeClaimConnection(payout_db, lambda: payouts.hold_one(1, "hold before claim", 8))
    monkeypatch.setattr(payouts, "get_conn", lambda: wrapped)
    monkeypatch.setattr(payouts, "_dispatch_payout", lambda _p: pytest.fail("lost claim"))
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert result["processed_count"] == 0
    assert result["cycle_complete"] is False
    assert _payout(payout_db)["status"] == "held"


def test_parallel_cycle_request_cannot_dispatch_twice(payout_db, monkeypatch):
    calls = []

    def dispatch(p):
        calls.append(p)
        with pytest.raises(ValueError, match="cannot process"):
            payouts.process_cycle("synthetic-cycle", 8)
        return _receipt(p)

    monkeypatch.setattr(payouts, "_dispatch_payout", dispatch)
    assert payouts.process_cycle("synthetic-cycle", 7)["processed_count"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("state", ["processing", "processed", "invalid_legacy"])
def test_accrual_cannot_add_payable_rows_to_closed_cycle(payout_db, state):
    payout_db.execute("UPDATE payout_cycles SET status=?", (state,))
    payout_db.commit()
    before = _payout(payout_db)
    # No orders table exists: blocked cycles must not even query order data.
    with pytest.raises(ValueError, match="not open"):
        payouts.accrue_cycle("synthetic-cycle")
    assert _payout(payout_db) == before
    assert payout_db.in_transaction is False


def test_process_http_exposes_blocked_state_and_audit_not_fake_success(payout_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routers import commerce

    audits = []
    monkeypatch.setattr(commerce, "record_admin_action", lambda **kwargs: audits.append(kwargs))
    app = FastAPI()
    app.include_router(commerce.router)
    app.dependency_overrides[commerce.require_admin] = lambda: {"id": 7}
    with TestClient(app) as client:
        response = client.post("/api/admin/payouts/cycle/synthetic-cycle/process", json={
            "status": "paid", "transaction_id": "client-forged-receipt",
        })
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "blocked"
    assert result["provider_calls_performed"] is False
    assert audits[0]["detail"]["status"] == "blocked"
    assert audits[0]["detail"]["cycle_complete"] is False
    assert audits[0]["detail"]["processed_count"] == 0


def test_conflicting_manual_route_returns_409_without_audit(payout_db, monkeypatch):
    from fastapi import HTTPException
    from app.api.routers import commerce

    payout_db.execute("UPDATE payouts SET status='paid'")
    payout_db.commit()
    monkeypatch.setattr(commerce, "record_admin_action", lambda **_kw: pytest.fail("no successful action"))
    with pytest.raises(HTTPException) as error:
        commerce.release_payout(1, None, {"id": 7})
    assert error.value.status_code == 409
    assert _payout(payout_db)["status"] == "paid"


@pytest.mark.parametrize("transaction", ["pp_stub_1_123", "bank_stub_1", " PP_STUB_1 "])
def test_legacy_placeholder_is_unverified_without_rewriting_or_repaying(payout_db, monkeypatch, transaction):
    payout_db.execute("UPDATE payouts SET status='paid', paid_tx_id=?, paid_at='synthetic-date'", (transaction,))
    payout_db.commit()
    before = _payout(payout_db)
    monkeypatch.setattr(payouts, "_dispatch_payout", lambda _p: pytest.fail("never repay legacy"))
    projection = payouts.user_history(101)[0]
    assert projection["status"] == "payment_unknown"
    assert projection["stored_status"] == "paid"
    assert projection["payment_confirmed"] is None
    counts = payouts._cycle_counts("synthetic-cycle")
    assert counts["paid_cents"] == 0
    assert counts["unverified_cents"] == 1250
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert result["cycle_complete"] is False
    assert result["blocked_legacy_receipt_count"] == 1
    assert result["processed_count"] == 0
    assert result["provider_calls_performed"] is None
    assert _payout(payout_db) == before


def test_ordinary_legacy_transaction_is_not_reclassified_by_guess(payout_db):
    payout_db.execute("UPDATE payouts SET status='paid', paid_tx_id='ordinary-existing-reference'")
    payout_db.commit()
    assert payouts.user_history(101)[0]["status"] == "paid"
    assert payouts._cycle_counts("synthetic-cycle")["paid_cents"] == 1250


@pytest.mark.parametrize("currency", [None, "", "ZZZ", "not-a-currency", 12])
def test_missing_or_invalid_currency_is_blocked_before_adapter(payout_db, monkeypatch, currency):
    payout_db.execute("UPDATE payouts SET currency=?", (currency,))
    payout_db.commit()
    assert payouts._confirmed_receipt(_receipt(_payout(payout_db), currency="USD"), _payout(payout_db)) is False
    monkeypatch.setattr(payouts, "_dispatch_payout", lambda _p: pytest.fail("invalid currency must not dispatch"))
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert result["status"] == "blocked"
    assert result["provider_calls_performed"] is False
    assert _payout(payout_db)["status"] == "approved"


def _seed_accrual(conn, orders):
    conn.executescript("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, attribution_user_id INTEGER, currency TEXT,
            subtotal_cents INTEGER, commission_cents INTEGER, placed_at TEXT, status TEXT
        );
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);
        INSERT INTO users VALUES (102, 'synthetic@example.invalid'), (103, 'synthetic-2@example.invalid');
        UPDATE payout_cycles SET start_date='2026-09-01', end_date='2026-09-30';
    """)
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, 1000, ?, '2026-09-07', 'paid')", orders)
    conn.commit()


@pytest.mark.parametrize("bad_currency", ["EUR", None, "", "ZZZ"])
def test_accrual_rejects_mixed_or_unknown_units_without_partial_writes(payout_db, bad_currency):
    _seed_accrual(payout_db, [(1, 103, "USD", 300), (2, 102, "USD", 100), (3, 102, bad_currency, 200)])
    before = [dict(row) for row in payout_db.execute("SELECT * FROM payouts")]
    with pytest.raises(ValueError):
        payouts.accrue_cycle("synthetic-cycle")
    assert [dict(row) for row in payout_db.execute("SELECT * FROM payouts")] == before
    assert payout_db.in_transaction is False


def test_single_currency_accrual_keeps_explicit_euro_instead_of_usd_default(payout_db):
    _seed_accrual(payout_db, [(1, 102, "EUR", 100), (2, 102, "eur", 200)])
    assert payouts.accrue_cycle("synthetic-cycle")["accrued_count"] == 1
    row = payout_db.execute("SELECT * FROM payouts WHERE user_id=102").fetchone()
    assert row["currency"] == "EUR"
    assert row["amount_cents"] == 300
    assert sorted(json.loads(row["order_ids_json"])) == [1, 2]
    counts = payouts._cycle_counts("synthetic-cycle")
    assert counts["aggregation_status"] == "mixed_currency"
    assert counts["currency"] is None
    for key in ("approved_cents", "pending_cents", "held_cents", "paid_cents", "unverified_cents"):
        assert counts[key] is None
    assert counts["creator_count"] == 2


@pytest.mark.parametrize("currency", [None, "", "ZZZ"])
def test_unknown_currency_totals_are_unknown_not_zero_or_usd(payout_db, currency):
    payout_db.execute("UPDATE payouts SET currency=?", (currency,))
    payout_db.commit()
    counts = payouts._cycle_counts("synthetic-cycle")
    assert counts["aggregation_status"] == "unknown_currency"
    assert counts["currency"] is None
    assert counts["approved_cents"] is None


def test_repeated_unconnected_requests_remain_not_called_and_unpaid(payout_db):
    first = payouts.process_cycle("synthetic-cycle", 7)
    second = payouts.process_cycle("synthetic-cycle", 7)
    assert first["status"] == second["status"] == "blocked"
    assert first["provider_calls_performed"] is second["provider_calls_performed"] is False
    assert _payout(payout_db)["paid_at"] is None


def test_cancelled_dispatch_preserves_durable_claim_and_prevents_retry(payout_db, monkeypatch):
    import asyncio

    calls = []

    def cancelled(p):
        calls.append(p)
        raise asyncio.CancelledError()

    monkeypatch.setattr(payouts, "_dispatch_payout", cancelled)
    with pytest.raises(asyncio.CancelledError):
        payouts.process_cycle("synthetic-cycle", 7)
    assert _payout(payout_db)["status"] == "paying"
    assert _payout(payout_db)["paid_tx_id"] is None
    with pytest.raises(ValueError):
        payouts.process_cycle("synthetic-cycle", 7)
    assert len(calls) == 1


def test_adapter_cannot_mutate_the_receipt_contract_it_is_checked_against(payout_db, monkeypatch):
    def changed(p):
        p["amount_cents"] = 99999
        return _receipt(p)

    monkeypatch.setattr(payouts, "_dispatch_payout", changed)
    result = payouts.process_cycle("synthetic-cycle", 7)
    assert result["status"] == "unknown"
    assert result["processed_count"] == 0
    assert _payout(payout_db)["amount_cents"] == 1250


def test_uncommitted_settlement_is_not_reported_paid_or_replayed(payout_db, monkeypatch):
    class FailSettlementCommit(_BeforeClaimConnection):
        commits = 0

        def commit(self):
            self.commits += 1
            if self.commits == 3:
                raise sqlite3.OperationalError("synthetic settlement failure")
            self.conn.commit()

    wrapped = FailSettlementCommit(payout_db, None)
    calls = []
    monkeypatch.setattr(payouts, "get_conn", lambda: wrapped)
    monkeypatch.setattr(payouts, "_dispatch_payout", lambda p: calls.append(p) or _receipt(p))
    with pytest.raises(sqlite3.OperationalError):
        payouts.process_cycle("synthetic-cycle", 7)
    assert _payout(payout_db)["status"] == "paying"
    assert _payout(payout_db)["paid_tx_id"] is None
    assert payout_db.in_transaction is False
    with pytest.raises(ValueError):
        payouts.process_cycle("synthetic-cycle", 7)
    assert len(calls) == 1
