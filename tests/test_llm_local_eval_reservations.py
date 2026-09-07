"""Offline local-evaluation reservation and temporary closed-cap contracts."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

import pytest

from app.platform import llm_budget_reservations as reservations


SCOPE = "cron:kol_live_query_eval"


@pytest.fixture
def isolated_budget(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE vkpi_provider_budget_caps (
            scope TEXT PRIMARY KEY, cap_usd REAL, current_spend REAL DEFAULT 0,
            warning_at REAL DEFAULT 0.8, hard_stop_at REAL DEFAULT 1,
            reset_at TEXT, fallback_action TEXT DEFAULT '', metadata_json TEXT DEFAULT '{}'
        );
        CREATE TABLE vkpi_llm_budget_reservations (
            reservation_key TEXT PRIMARY KEY, provider TEXT, model_name TEXT,
            purpose TEXT, request_hash TEXT, provider_scope TEXT, cost_scope TEXT,
            cumulative_scopes_json TEXT, estimated_cost_usd REAL, actual_cost_usd REAL,
            state TEXT, metadata_json TEXT, reserved_at TEXT, provider_started_at TEXT,
            settled_at TEXT, updated_at TEXT
        );
    """)
    for scope in ("monthly_total", "single_call", "provider:openai", SCOPE):
        conn.execute(
            "INSERT INTO vkpi_provider_budget_caps (scope,cap_usd,reset_at) VALUES (?,?,?)",
            (scope, 0.10 if scope == SCOPE else 1.0, "2099-01-01T00:00:00Z"),
        )
    conn.commit()
    monkeypatch.setattr(reservations, "IS_PRODUCTION", False)
    monkeypatch.setattr(reservations, "get_conn", lambda: conn)
    monkeypatch.setattr(reservations, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(reservations, "table_exists", lambda _name: True)
    reaper_calls = []
    monkeypatch.setattr(reservations, "_maybe_reap_stale_reservations", lambda: reaper_calls.append(True))
    yield conn, reaper_calls
    conn.close()


def _reserve(**overrides):
    arguments = {
        "provider": "openai", "model": "gpt-5.6-luna", "purpose": "kol_live_query_eval",
        "prompt": "synthetic evaluation request", "estimated_cost_usd": "0.01",
        "cost_scope": SCOPE, "require_cost_scope": True, "local_evaluation_no_reap": True,
    }
    return reservations.reserve_llm_budget(**{**arguments, **overrides})


def _reservation_rows(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM vkpi_llm_budget_reservations ORDER BY reservation_key")]


def test_local_evaluation_skips_reaper_and_preserves_other_scope_unknown(isolated_budget):
    conn, reaper_calls = isolated_budget
    conn.execute("""INSERT INTO vkpi_llm_budget_reservations
        (reservation_key, provider_scope, cost_scope, estimated_cost_usd, state, reserved_at)
        VALUES ('old-unknown','provider:claude','cron:other',0.01,'unknown','2000-01-01T00:00:00Z')""")
    conn.commit()
    before = _reservation_rows(conn)[0]
    result = _reserve(metadata={"execution_class": "production", "claim_status": "verified"})
    rows = {row["reservation_key"]: row for row in _reservation_rows(conn)}
    assert reaper_calls == []
    assert rows["old-unknown"] == before
    assert set(result.cumulative_scopes) == {"monthly_total", "provider:openai", SCOPE}
    metadata = json.loads(rows[result.reservation_key]["metadata_json"])
    assert metadata["execution_class"] == "local_evaluation"
    assert metadata["claim_status"] == "descriptive_only"
    assert metadata["request_content_recorded"] is False


@pytest.mark.parametrize("options", [{}, {"local_evaluation_no_reap": False}])
def test_ordinary_reservations_keep_existing_reaper_behavior(isolated_budget, options):
    _, reaper_calls = isolated_budget
    reservations.reserve_llm_budget(provider="openai", model="gpt-5.6-luna", purpose="ordinary",
                                    prompt="safe", estimated_cost_usd="0.01", **options)
    assert reaper_calls == [True]


@pytest.mark.parametrize("override,reason", [
    ({"cost_scope": "cron:other"}, "local_evaluation_budget_scope_required"),
    ({"cost_scope": ""}, "local_evaluation_budget_scope_required"),
    ({"require_cost_scope": False}, "local_evaluation_budget_scope_required"),
    ({"require_cost_scope": 1}, "local_evaluation_budget_scope_required"),
    ({"local_evaluation_no_reap": "true"}, "local_evaluation_no_reap_flag_invalid"),
    ({"local_evaluation_no_reap": 1}, "local_evaluation_no_reap_flag_invalid"),
])
def test_invalid_local_option_fails_before_database_or_reaper(monkeypatch, override, reason):
    monkeypatch.setattr(reservations, "IS_PRODUCTION", False)
    monkeypatch.setattr(reservations, "_ensure_schema", lambda: pytest.fail("must not access DB"))
    monkeypatch.setattr(reservations, "_maybe_reap_stale_reservations", lambda: pytest.fail("must not reap"))
    with pytest.raises(reservations.LlmBudgetBlocked, match=reason):
        _reserve(**override)


def test_local_no_reap_is_forbidden_in_production(monkeypatch):
    monkeypatch.setattr(reservations, "IS_PRODUCTION", True)
    monkeypatch.setattr(reservations, "_ensure_schema", lambda: pytest.fail("must not access DB"))
    with pytest.raises(reservations.LlmBudgetBlocked, match="local_evaluation_forbidden_in_production"):
        _reserve()


@pytest.mark.parametrize("scope", [SCOPE, "monthly_total", "single_call", "provider:openai"])
def test_no_reap_never_bypasses_missing_required_scopes(isolated_budget, scope):
    conn, reaper_calls = isolated_budget
    conn.execute("DELETE FROM vkpi_provider_budget_caps WHERE scope=?", (scope,))
    conn.commit()
    with pytest.raises(reservations.LlmBudgetBlocked, match="budget_scope_not_configured") as caught:
        _reserve()
    assert caught.value.scope == scope
    assert _reservation_rows(conn) == []
    assert reaper_calls == []


def test_no_reap_keeps_unknown_estimates_reserved_and_unreleasable(isolated_budget):
    conn, reaper_calls = isolated_budget
    first = _reserve(estimated_cost_usd="0.06")
    reservations.mark_llm_provider_started(first.reservation_key)
    assert reservations.mark_llm_provider_unknown(first.reservation_key) is True
    assert reservations.release_llm_reservation(first.reservation_key) is False
    assert reservations.settle_llm_reservation(first.reservation_key, "0") == {
        "settled": False, "reason": "provider_outcome_not_confirmed"}
    with pytest.raises(reservations.LlmBudgetBlocked, match="hard_stop_or_projected_cap") as caught:
        _reserve(estimated_cost_usd="0.04")
    assert caught.value.scope == SCOPE
    assert _reservation_rows(conn)[0]["state"] == "unknown"
    assert reaper_calls == []


@pytest.mark.parametrize("scope", ["monthly_total", "single_call", "provider:openai", SCOPE])
def test_no_reap_keeps_every_configured_cap(isolated_budget, scope):
    conn, _ = isolated_budget
    conn.execute("UPDATE vkpi_provider_budget_caps SET cap_usd=0.005 WHERE scope=?", (scope,))
    conn.commit()
    with pytest.raises(reservations.LlmBudgetBlocked, match="hard_stop_or_projected_cap") as caught:
        _reserve()
    assert caught.value.scope == scope
    assert _reservation_rows(conn) == []


def test_local_evaluation_settles_cumulative_scopes_exactly_once(isolated_budget):
    conn, _ = isolated_budget
    result = _reserve()
    reservations.mark_llm_provider_started(result.reservation_key)
    settlement = reservations.settle_llm_reservation(result.reservation_key, "0.000033")
    assert settlement["readback_verified"] is True
    assert settlement["scope_deltas_micro_usd"] == {
        "monthly_total": 33, "provider:openai": 33, SCOPE: 33}
    assert reservations.settle_llm_reservation(result.reservation_key, "0.000033")["reason"] == "already_settled"
    assert _reservation_rows(conn)[0]["state"] == "settled"


@pytest.mark.parametrize("estimate", ["0", "0.0000001", "-0.01"])
def test_no_reap_still_requires_positive_micro_usd_estimate(isolated_budget, estimate):
    conn, _ = isolated_budget
    with pytest.raises(reservations.LlmBudgetBlocked, match="estimate_"):
        _reserve(estimated_cost_usd=estimate)
    assert _reservation_rows(conn) == []


@pytest.mark.parametrize("reset_at", ["2099-01-01T00:00:00Z", "2000-01-01T00:00:00Z"])
def test_one_micro_closed_cap_blocks_smallest_legal_estimate_even_after_daily_roll(isolated_budget, reset_at):
    conn, _ = isolated_budget
    conn.execute("UPDATE vkpi_provider_budget_caps SET cap_usd=0.000001,hard_stop_at=1,reset_at=? WHERE scope=?", (reset_at, SCOPE))
    conn.commit()
    with pytest.raises(reservations.LlmBudgetBlocked, match="hard_stop_or_projected_cap") as caught:
        _reserve(estimated_cost_usd=Decimal("0.000001"))
    assert caught.value.scope == SCOPE
    assert _reservation_rows(conn) == []


@pytest.mark.parametrize("reset_at", ["2099-01-01T00:00:00Z", "2000-01-01T00:00:00Z"])
def test_temporary_closed_cap_update_preserves_spend_and_unknown_rows(isolated_budget, reset_at):
    conn, _ = isolated_budget
    first = _reserve()
    reservations.mark_llm_provider_started(first.reservation_key)
    reservations.mark_llm_provider_unknown(first.reservation_key)
    conn.execute("UPDATE vkpi_provider_budget_caps SET current_spend=0.000033,reset_at=? WHERE scope=?", (reset_at, SCOPE))
    conn.commit()
    unknown_before = _reservation_rows(conn)
    core_before = [dict(row) for row in conn.execute("SELECT * FROM vkpi_provider_budget_caps WHERE scope<>? ORDER BY scope", (SCOPE,))]
    # Closing uses a scoped UPDATE, not get_budget_status/update_budget: their
    # return path may roll an expired window and zero historical stored spend.
    cursor = conn.execute(
        "UPDATE vkpi_provider_budget_caps SET cap_usd=?,hard_stop_at=? WHERE scope=?",
        ("0.000001", 1, SCOPE),
    )
    assert cursor.rowcount == 1
    conn.commit()
    row = conn.execute("SELECT cap_usd,current_spend,reset_at FROM vkpi_provider_budget_caps WHERE scope=?", (SCOPE,)).fetchone()
    assert Decimal(str(row["cap_usd"])) == Decimal("0.000001")
    assert Decimal(str(row["current_spend"])) == Decimal("0.000033")
    assert row["reset_at"] == reset_at
    assert _reservation_rows(conn) == unknown_before
    assert [dict(row) for row in conn.execute("SELECT * FROM vkpi_provider_budget_caps WHERE scope<>? ORDER BY scope", (SCOPE,))] == core_before
    with pytest.raises(reservations.LlmBudgetBlocked, match="hard_stop_or_projected_cap"):
        _reserve(estimated_cost_usd="0.000001")
