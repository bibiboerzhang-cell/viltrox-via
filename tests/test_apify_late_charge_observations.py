"""Synthetic charge observations and SQLite ledgers; no live DB or HTTP."""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domains.costs.apify_cost_reconciliation import observed_charge, reconcile_run_observation

NOW = datetime(2026, 9, 6, 15, tzinfo=timezone.utc)
RUN = "run_fixture"


def seed(conn):
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE vkpi_ai_cost_ledger (
          id INTEGER PRIMARY KEY, ai_provider TEXT, cost_usd REAL,
          metadata_json TEXT, occurred_at TEXT, cron_task TEXT, model_name TEXT,
          tokens_in INTEGER, tokens_out INTEGER);
        CREATE TABLE vkpi_apify_budget_reservations (
          reservation_key TEXT PRIMARY KEY, apify_run_id TEXT UNIQUE, state TEXT,
          actual_cost_usd REAL, metadata_json TEXT, settled_at TEXT, updated_at TEXT,
          provider_started_at TEXT, reserved_at TEXT);
        CREATE TABLE vkpi_provider_budget_caps (
          scope TEXT PRIMARY KEY, current_spend REAL, cap_usd REAL, reset_at TEXT);
        INSERT INTO vkpi_provider_budget_caps VALUES
          ('provider:apify',1.0026,10,'2026-10-01T00:00:00Z'),
          ('monthly_total',2.0026,50,'2026-10-01T00:00:00Z'),
          ('cron:kol_live_query_eval',0.02,0.000001,'2026-09-07T00:00:00Z');
    """)
    meta = {"apify_run_id": RUN, "budget_reservation_key": "reserved_fixture",
            "estimated": False, "pricing_basis": "usage_settled", "reconciled": True}
    conn.execute("INSERT INTO vkpi_ai_cost_ledger(id,ai_provider,cost_usd,metadata_json,occurred_at) VALUES (1,'apify',0.0026,?,'2026-09-05T15:00:00Z')", (json.dumps(meta),))
    conn.execute("INSERT INTO vkpi_apify_budget_reservations VALUES (?,?, 'settled',0.0026,?, ?, ?, ?, ?)",
                 ("reserved_fixture", RUN, '{"preserve":"user metadata"}', *(["2026-09-05T15:00:00Z"] * 4)))
    conn.commit()


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    seed(db)
    yield db
    db.close()


def observation(cost=.026, **extra):
    return {"id": RUN, "status": "SUCCEEDED", "usageTotalUsd": cost, **extra}


def apply(conn, cost=.026, **kwargs):
    return reconcile_run_observation(conn, observation(cost), postgres=False, now=NOW, **kwargs)


def snapshot(conn):
    return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in ("vkpi_ai_cost_ledger", "vkpi_apify_budget_reservations", "vkpi_provider_budget_caps")}


def test_positive_early_usage_and_undocumented_final_field_are_not_final(conn):
    assert observed_charge(observation(.0026, finalTotalChargeUsd=99)) == Decimal("0.002600")
    result = apply(conn)
    assert result["cost_state"] == "provisional" and result["provider_cost_final"] is False


def test_late_charge_updates_all_three_books_exactly_once_and_preserves_other_usage(conn):
    original_caps = snapshot(conn)["vkpi_provider_budget_caps"]
    result = apply(conn)
    assert result["reconciled"] is True and result["accounting_verified"] is True
    assert result["delta_usd"] == .0234 and result["cost_usd"] == .026
    rows = snapshot(conn)
    assert rows["vkpi_ai_cost_ledger"][0]["cost_usd"] == pytest.approx(.026)
    assert rows["vkpi_apify_budget_reservations"][0]["actual_cost_usd"] == pytest.approx(.026)
    assert json.loads(rows["vkpi_apify_budget_reservations"][0]["metadata_json"])["preserve"] == "user metadata"
    assert rows["vkpi_provider_budget_caps"][0]["current_spend"] == pytest.approx(1.026)
    assert rows["vkpi_provider_budget_caps"][1]["current_spend"] == pytest.approx(2.026)
    assert rows["vkpi_provider_budget_caps"][2] == original_caps[2]  # No yesterday-to-today cron charge.
    assert all(after["cap_usd"] == before["cap_usd"] and after["reset_at"] == before["reset_at"]
               for before, after in zip(original_caps, rows["vkpi_provider_budget_caps"]))
    replay = apply(conn)
    assert replay["delta_usd"] == 0
    assert snapshot(conn) == rows


def test_later_higher_charge_not_frozen_by_prior_reconciled_flag(conn):
    apply(conn)
    later = apply(conn, .03)
    assert later["delta_usd"] == .004
    assert conn.execute("SELECT current_spend FROM vkpi_provider_budget_caps WHERE scope='provider:apify'").fetchone()[0] == pytest.approx(1.03)


def test_stable_window_means_repeated_observation_not_provider_final(conn):
    apply(conn)
    result = reconcile_run_observation(conn, observation(), postgres=False, now=NOW + timedelta(seconds=300))
    assert result["observation_state"] == "stable_observation"
    assert result["provider_cost_final"] is False and result["delta_usd"] == 0


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), True, "0.026"])
def test_unknown_charge_never_settles_or_mutates(conn, value):
    before = snapshot(conn)
    result = apply(conn, value)
    assert result == {"reconciled": False, "reason": "charge_observation_unconfirmed"}
    assert snapshot(conn) == before


def test_running_is_not_terminal_cost_evidence(conn):
    before = snapshot(conn)
    result = reconcile_run_observation(conn, observation(status="RUNNING"), postgres=False, now=NOW)
    assert result["reconciled"] is False and snapshot(conn) == before


@pytest.mark.parametrize("sql,reason", [
    ("DELETE FROM vkpi_provider_budget_caps WHERE scope='monthly_total'", "budget_scope_missing"),
    ("UPDATE vkpi_provider_budget_caps SET reset_at=NULL WHERE scope='provider:apify'", "budget_window_requires_review"),
    ("UPDATE vkpi_provider_budget_caps SET reset_at='2026-11-01T00:00:00Z'", "budget_window_requires_review"),
    ("UPDATE vkpi_ai_cost_ledger SET occurred_at='2026-08-31T23:59:00Z'", "budget_window_requires_review"),
    ("UPDATE vkpi_apify_budget_reservations SET actual_cost_usd=0.003", "reservation_ledger_cost_drift_requires_review"),
    ("DELETE FROM vkpi_apify_budget_reservations", "reservation_run_not_unique"),
    ("UPDATE vkpi_apify_budget_reservations SET state='reserved'", "reservation_state_unconfirmed"),
    ("INSERT INTO vkpi_ai_cost_ledger SELECT 2,ai_provider,cost_usd,metadata_json,occurred_at,cron_task,model_name,tokens_in,tokens_out FROM vkpi_ai_cost_ledger", "ledger_run_not_unique"),
    ("UPDATE vkpi_provider_budget_caps SET current_spend=0 WHERE scope='provider:apify'", "budget_baseline_missing_requires_review"),
])
def test_ambiguous_or_cross_window_evidence_fails_without_partial_updates(conn, sql, reason):
    conn.execute(sql)
    conn.commit()
    before = snapshot(conn)
    result = apply(conn)
    assert result["reason"] == reason
    assert snapshot(conn) == before


def test_out_of_order_lower_observation_cannot_decrement_budget(conn):
    apply(conn)
    before = snapshot(conn)
    result = apply(conn, .0026)
    assert result["reason"] == "cost_decrease_requires_review"
    assert snapshot(conn) == before


def test_unknown_reserved_estimate_can_later_be_accounted_without_double_charging_estimate(conn):
    meta = json.loads(conn.execute("SELECT metadata_json FROM vkpi_ai_cost_ledger").fetchone()[0])
    meta.update(estimated=True, cost_budget_accounted=False)
    conn.execute("UPDATE vkpi_ai_cost_ledger SET cost_usd=0.04,metadata_json=?", (json.dumps(meta),))
    conn.execute("UPDATE vkpi_apify_budget_reservations SET state='unknown',actual_cost_usd=NULL")
    conn.execute("UPDATE vkpi_provider_budget_caps SET current_spend=current_spend-0.0026 WHERE scope IN ('provider:apify','monthly_total')")
    conn.commit()
    result = apply(conn)
    assert result["delta_usd"] == .026
    assert conn.execute("SELECT current_spend FROM vkpi_provider_budget_caps WHERE scope='provider:apify'").fetchone()[0] == pytest.approx(1.026)


class FaultConnection:
    def __init__(self, raw, target):
        self.raw, self.target = raw, target

    def execute(self, sql, params=()):
        if sql.startswith(self.target):
            raise RuntimeError("injected write failure")
        return self.raw.execute(sql, params)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()


@pytest.mark.parametrize("target", [
    "UPDATE vkpi_provider_budget_caps", "UPDATE vkpi_apify_budget_reservations",
    "UPDATE vkpi_ai_cost_ledger",
])
def test_each_write_failure_rolls_back_all_books(conn, target):
    before = snapshot(conn)
    with pytest.raises(RuntimeError, match="injected"):
        apply(FaultConnection(conn, target))
    assert snapshot(conn) == before


def test_lost_commit_ack_is_replay_safe(conn):
    class LostAck(FaultConnection):
        def commit(self):
            self.raw.commit()
            raise RuntimeError("commit acknowledgement lost")
    with pytest.raises(RuntimeError, match="acknowledgement"):
        apply(LostAck(conn, "NO SQL MATCH"))
    assert apply(conn)["delta_usd"] == 0
    assert conn.execute("SELECT current_spend FROM vkpi_provider_budget_caps WHERE scope='provider:apify'").fetchone()[0] == pytest.approx(1.026)


def test_two_concurrent_observers_apply_only_one_delta(tmp_path):
    path = tmp_path / "isolated.sqlite"
    initial = sqlite3.connect(path)
    seed(initial)
    initial.close()

    def task():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            return apply(connection)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: task(), range(2)))
    assert sorted(result["delta_usd"] for result in results) == [0, .0234]


def prepare_initial(conn):
    conn.execute("DELETE FROM vkpi_ai_cost_ledger")
    conn.execute("UPDATE vkpi_apify_budget_reservations SET actual_cost_usd=NULL,state='provider_started',settled_at=NULL")
    conn.execute("UPDATE vkpi_provider_budget_caps SET current_spend=current_spend-0.0026 WHERE scope IN ('provider:apify','monthly_total')")
    conn.commit()


def test_first_record_and_replay_are_atomic_and_keep_compatible_receipt(conn):
    prepare_initial(conn)
    result = apply(conn, initial_entry={"actor_id": "streamers/youtube-scraper"})
    assert result["ledger_inserted"] is True and result["delta_usd"] == .026
    assert result["cost_micro_usd"] == 26000 and result["persisted_cost_usd"] == "0.026000"
    second = apply(conn, initial_entry={"actor_id": "streamers/youtube-scraper"})
    assert second["ledger_inserted"] is False and second["delta_usd"] == 0
    assert conn.execute("SELECT COUNT(*) FROM vkpi_ai_cost_ledger").fetchone()[0] == 1


@pytest.mark.parametrize("target", ["INSERT INTO vkpi_ai_cost_ledger", "UPDATE vkpi_ai_cost_ledger"])
def test_first_detail_write_failure_does_not_settle_or_charge_budget(conn, target):
    prepare_initial(conn)
    before = snapshot(conn)
    with pytest.raises(RuntimeError, match="injected"):
        apply(FaultConnection(conn, target), initial_entry={"actor_id": "streamers/youtube-scraper"})
    assert snapshot(conn) == before


@pytest.fixture
def known_run_job(conn, monkeypatch):
    from app.domains.costs import budget_guard
    import httpx

    requests = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 15.0, "follow_redirects": False}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            assert url == f"https://api.apify.com/v2/actor-runs/{RUN}"
            assert kwargs == {"headers": {"Authorization": "Bearer fake-token"}}
            return type("Response", (), {"status_code": 200, "json": lambda _: {"data": observation()}})()

    monkeypatch.setattr(budget_guard, "ensure_budget_schema", lambda: None)
    monkeypatch.setattr(budget_guard, "get_conn", lambda: conn)
    monkeypatch.setattr(budget_guard, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(budget_guard, "datetime", Clock)
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("APIFY_TOKEN", "fake-token")
    return budget_guard, requests


def test_reconcile_job_fetches_only_known_run_and_does_not_freeze_old_reconciled_flag(known_run_job):
    budget_guard, requests = known_run_job
    first = budget_guard.reconcile_apify_costs()
    second = budget_guard.reconcile_apify_costs()
    assert first["delta_usd"] == .0234 and first["reconciled"] == 1
    assert second["delta_usd"] == 0 and second["checked"] == 1
    assert first["provider_cost_final"] is False and len(requests) == 2


@pytest.mark.parametrize("metadata,failed", [
    ("{", 1), ("[]", 1), ("null", 1), ("true", 1), ("0", 1), ('"scalar"', 1),
    ("{}", 0), ('{"apify_run_id":""}', 0),
])
def test_reconcile_job_skips_malformed_metadata_and_continues_known_run(conn, known_run_job, metadata, failed):
    budget_guard, requests = known_run_job
    conn.execute(
        "INSERT INTO vkpi_ai_cost_ledger(id,ai_provider,cost_usd,metadata_json,occurred_at) "
        "VALUES (2,'apify',0,?,'2026-09-05T15:00:00Z')", (metadata,),
    )
    conn.commit()
    # DESC visits the malformed row first. The mock client exposes GET only;
    # any Actor start or extra run request must fail instead of reaching HTTP.
    before_bad = dict(conn.execute("SELECT * FROM vkpi_ai_cost_ledger WHERE id=2").fetchone())
    result = budget_guard.reconcile_apify_costs()
    assert result["failed"] == failed and result["checked"] == result["reconciled"] == 1
    assert result["delta_usd"] == .0234 and result["provider_cost_final"] is False
    assert len(requests) == 1
    assert dict(conn.execute("SELECT * FROM vkpi_ai_cost_ledger WHERE id=2").fetchone()) == before_bad
    good = conn.execute("SELECT cost_usd,metadata_json FROM vkpi_ai_cost_ledger WHERE id=1").fetchone()
    assert good["cost_usd"] == pytest.approx(.026)
    assert json.loads(good["metadata_json"])["cost_state"] == "provisional"


def test_reconcile_job_all_malformed_rows_reports_failures_without_requests(conn, known_run_job):
    budget_guard, requests = known_run_job
    conn.execute("UPDATE vkpi_ai_cost_ledger SET metadata_json='null' WHERE id=1")
    conn.execute(
        "INSERT INTO vkpi_ai_cost_ledger(id,ai_provider,cost_usd,metadata_json,occurred_at) "
        "VALUES (2,'apify',0,'{','2026-09-05T15:00:00Z')",
    )
    conn.commit()
    before = snapshot(conn)
    result = budget_guard.reconcile_apify_costs()
    assert result["failed"] == 2 and result["checked"] == result["reconciled"] == 0
    assert result["delta_usd"] == 0 and result["provider_cost_final"] is False
    assert requests == [] and snapshot(conn) == before


def test_postgres_path_locks_reservation_detail_and_both_budget_rows(conn):
    statements = []

    class FakePostgres(FaultConnection):
        def execute(self, sql, params=()):
            statements.append(sql)
            return self.raw.execute(sql.replace(" FOR UPDATE NOWAIT", ""),
                                    tuple(str(value) if isinstance(value, Decimal) else value for value in params))

    result = reconcile_run_observation(FakePostgres(conn, ""), observation(), postgres=True, now=NOW)
    assert result["reconciled"] is True
    locks = [sql for sql in statements if sql.endswith(" FOR UPDATE NOWAIT")]
    assert len(locks) == 4
    assert "vkpi_apify_budget_reservations" in locks[0]
    assert "vkpi_ai_cost_ledger" in locks[1]
    assert all("vkpi_provider_budget_caps" in sql for sql in locks[2:])


@pytest.mark.parametrize("sqlstate", ["55P03", "40P01"])
@pytest.mark.parametrize("shape", ["direct", "diag", "cause", "context"])
def test_lock_contention_rolls_back_all_books_and_retries_only_existing_reconciliation(conn, sqlstate, shape):
    underlying = RuntimeError("opaque database detail")
    if shape == "diag":
        underlying.diag = SimpleNamespace(sqlstate=sqlstate)
    else:
        underlying.sqlstate = sqlstate
    error = underlying
    if shape in {"cause", "context"}:
        error = RuntimeError("wrapper contains private connection detail")
        setattr(error, "__cause__" if shape == "cause" else "__context__", underlying)

    class BusyConnection(FaultConnection):
        rollbacks = 0

        def execute(self, sql, params=()):
            # Fail after both budget increments and the reservation update,
            # demonstrating rollback is not merely a pre-write early return.
            if sql.startswith("UPDATE vkpi_ai_cost_ledger"):
                raise error
            return self.raw.execute(sql.replace(" FOR UPDATE NOWAIT", ""),
                                    tuple(str(value) if isinstance(value, Decimal) else value for value in params))

        def rollback(self):
            self.rollbacks += 1
            self.raw.rollback()

    before = snapshot(conn)
    wrapper = BusyConnection(conn, "")
    result = reconcile_run_observation(wrapper, observation(), postgres=True, now=NOW)
    assert result == {"reconciled": False, "recorded": False, "reason": "reconciliation_busy",
                      "apify_run_id": RUN, "sqlstate": sqlstate,
                      "next_action": "retry_existing_run_reconciliation",
                      "automatic_provider_retry_allowed": False}
    assert wrapper.rollbacks == 1 and snapshot(conn) == before
    assert "private" not in json.dumps(result) and "opaque" not in json.dumps(result)
    # An independently scheduled observation can retry the same run; there is
    # no Actor start, network call, or retry loop anywhere in this module.
    assert apply(conn)["delta_usd"] == .0234


def test_unknown_database_error_is_not_reported_as_retryable_busy(conn):
    error = RuntimeError("unclassified database failure")
    error.sqlstate = "23503"

    class BadConnection(FaultConnection):
        def execute(self, sql, params=()):
            if sql.startswith("UPDATE vkpi_ai_cost_ledger"):
                raise error
            return self.raw.execute(sql, params)

    before = snapshot(conn)
    with pytest.raises(RuntimeError, match="unclassified") as raised:
        apply(BadConnection(conn, ""))
    assert raised.value is error and snapshot(conn) == before


def test_busy_detection_does_not_override_outer_sqlstate_or_loop_on_causes():
    from app.domains.costs.apify_cost_reconciliation import _busy_sqlstate
    inner = RuntimeError("inner lock")
    inner.sqlstate = "55P03"
    outer = RuntimeError("outer non-lock error")
    outer.sqlstate, outer.__cause__ = "23503", inner
    assert _busy_sqlstate(outer) is None
    cycle = RuntimeError("cyclic wrapper")
    cycle.__cause__ = cycle
    assert _busy_sqlstate(cycle) is None
