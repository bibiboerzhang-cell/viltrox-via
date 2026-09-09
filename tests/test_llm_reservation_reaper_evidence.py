"""Offline reaper evidence/state-fence regressions; no application database."""
from __future__ import annotations

import sqlite3
import uuid
from decimal import Decimal

import pytest

from app.platform import llm_budget_reservations as reservations


NOW = "2035-01-03 00:00:00"
OLD = "2035-01-01 00:00:00"
SCOPE = "cron:reaper_safety_test"


class ClockedConnection:
    """Translate only PostgreSQL clock arithmetic, retaining the real WHERE."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, parameters=()):
        sql = sql.replace(
            "NOW() - (? * INTERVAL '1 hour')",
            f"datetime('{NOW}', '-' || ? || ' hours')",
        ).replace("NOW()", f"'{NOW}'")
        return self.raw.execute(sql, parameters)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()


@pytest.fixture
def isolated_budget(monkeypatch):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript("""
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
        raw.execute(
            "INSERT INTO vkpi_provider_budget_caps (scope,cap_usd,reset_at) VALUES (?,1,?)",
            (scope, "2099-01-01T00:00:00Z"),
        )
    raw.commit()
    conn = ClockedConnection(raw)
    monkeypatch.setattr(reservations, "get_conn", lambda: conn)
    monkeypatch.setattr(reservations, "table_exists", lambda _name: True)
    monkeypatch.setattr(reservations, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(reservations, "_utcnow", lambda: NOW)
    monkeypatch.setattr(reservations, "_last_reap_monotonic", float("-inf"))
    monkeypatch.delenv("VKPI_LLM_RESERVATION_TTL_HOURS", raising=False)
    yield conn
    raw.close()


def insert_reservation(conn, state="reserved", **overrides):
    row = {
        "reservation_key": "synthetic-reservation", "provider": "openai",
        "model_name": "synthetic-model", "purpose": "reaper-test",
        "request_hash": "synthetic-hash", "provider_scope": "provider:openai",
        "cost_scope": SCOPE, "cumulative_scopes_json": "[]",
        "estimated_cost_usd": 0.6, "actual_cost_usd": None, "state": state,
        "metadata_json": "{}", "reserved_at": OLD, "provider_started_at": None,
        "settled_at": None, "updated_at": OLD,
        **overrides,
    }
    conn.execute(
        "INSERT INTO vkpi_llm_budget_reservations (" + ",".join(row)
        + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()),
    )
    conn.commit()


def rows(conn):
    return [dict(row) for row in conn.execute(
        "SELECT * FROM vkpi_llm_budget_reservations ORDER BY reservation_key"
    ).fetchall()]


def allowance(conn, scope):
    return reservations._open_reserved_for_scope(
        conn, scope, provider_scope="provider:openai", cost_scope=SCOPE,
    )


@pytest.mark.parametrize("state", ["provider_started", "unknown"])
@pytest.mark.parametrize("started_at", [None, OLD])
def test_old_uncertain_spend_is_never_reaped_and_still_consumes_all_scopes(
    isolated_budget, state, started_at,
):
    conn = isolated_budget
    insert_reservation(conn, state, provider_started_at=started_at)
    before = rows(conn)
    assert reservations.reap_stale_reservations(ttl_hours=24) == 0
    assert rows(conn) == before
    for scope in ("monthly_total", "provider:openai", SCOPE):
        assert allowance(conn, scope) == Decimal("0.600000")


@pytest.mark.parametrize("state", ["provider_started", "unknown"])
def test_normal_reservation_entry_cannot_free_old_uncertain_allowance(
    isolated_budget, state,
):
    conn = isolated_budget
    insert_reservation(conn, state, provider_started_at=OLD)
    before = rows(conn)
    with pytest.raises(reservations.LlmBudgetBlocked, match="hard_stop_or_projected_cap"):
        reservations.reserve_llm_budget(
            provider="openai", model="synthetic-model", purpose="reaper-test",
            prompt="synthetic offline request", estimated_cost_usd="0.5",
            cost_scope=SCOPE, require_cost_scope=True,
        )
    assert rows(conn) == before
    assert all(row["current_spend"] == 0 for row in conn.execute(
        "SELECT current_spend FROM vkpi_provider_budget_caps"
    ))


@pytest.mark.parametrize("evidence", [
    {"provider_started_at": OLD}, {"settled_at": OLD},
    {"actual_cost_usd": 0}, {"actual_cost_usd": 0.01},
])
def test_reserved_with_contradictory_start_or_settlement_evidence_is_retained(
    isolated_budget, evidence,
):
    conn = isolated_budget
    insert_reservation(conn, **evidence)
    before = rows(conn)
    assert reservations.reap_stale_reservations(ttl_hours=24) == 0
    assert rows(conn) == before


def test_only_old_unstarted_reserved_can_expire_and_cannot_then_start(isolated_budget):
    conn = isolated_budget
    insert_reservation(conn)
    insert_reservation(conn, reservation_key="fresh", reserved_at=NOW, updated_at=NOW)
    assert reservations.reap_stale_reservations(ttl_hours=24) == 1
    actual = {row["reservation_key"]: row for row in rows(conn)}
    assert actual["synthetic-reservation"]["state"] == "expired"
    assert actual["synthetic-reservation"]["actual_cost_usd"] is None
    assert actual["synthetic-reservation"]["settled_at"] is None
    assert actual["fresh"]["state"] == "reserved"
    assert allowance(conn, "monthly_total") == Decimal("0.600000")
    assert reservations.reap_stale_reservations(ttl_hours=24) == 0
    with pytest.raises(reservations.LlmBudgetBlocked, match="reservation_not_startable"):
        reservations.mark_llm_provider_started("synthetic-reservation")


def test_committed_provider_start_wins_over_later_reaper(isolated_budget):
    conn = isolated_budget
    insert_reservation(conn)
    reservations.mark_llm_provider_started("synthetic-reservation")
    before = rows(conn)
    assert before[0]["provider_started_at"] == NOW
    assert reservations.reap_stale_reservations(ttl_hours=24) == 0
    assert rows(conn) == before


@pytest.mark.parametrize("state", ["settled", "released", "blocked", "expired"])
def test_reaper_never_rewrites_historical_terminal_rows(isolated_budget, state):
    conn = isolated_budget
    insert_reservation(conn, state)
    before = rows(conn)
    assert reservations.reap_stale_reservations(ttl_hours=24) == 0
    assert rows(conn) == before


@pytest.mark.pg
def test_native_postgres_reaper_preserves_started_unknown_and_conflicting_evidence(
    pg_dsn, monkeypatch,
):
    """Native SQL batch regression, not a concurrent-provider execution test."""
    import psycopg
    from psycopg import sql

    from app.db.connection import PostgresCompatConnection

    schema = f"vkpi_llm_reaper_{uuid.uuid4().hex}"
    with psycopg.connect(pg_dsn, connect_timeout=5) as raw:
        raw.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        raw.execute("SET statement_timeout='5s'")
        raw.execute("SET lock_timeout='1s'")
        raw.commit()
        try:
            raw.execute("""
                CREATE TABLE vkpi_llm_budget_reservations (
                    reservation_key TEXT PRIMARY KEY, provider TEXT, model_name TEXT,
                    purpose TEXT, request_hash TEXT, provider_scope TEXT, cost_scope TEXT,
                    cumulative_scopes_json TEXT, estimated_cost_usd NUMERIC(18,6),
                    actual_cost_usd NUMERIC(18,6), state TEXT, metadata_json TEXT,
                    reserved_at TIMESTAMPTZ, provider_started_at TIMESTAMPTZ,
                    settled_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
                )
            """)
            raw.commit()
            conn = PostgresCompatConnection(raw, pool=None)
            monkeypatch.setattr(reservations, "get_conn", lambda: conn)
            monkeypatch.setattr(reservations, "is_postgres_runtime", lambda: True)
            old, recent = raw.execute("SELECT NOW()-INTERVAL '48 hours',NOW()").fetchone()
            fixtures = [
                ("old-reserved", "reserved", {}),
                ("old-unknown", "unknown", {"provider_started_at": old}),
                ("old-started", "provider_started", {"provider_started_at": old}),
                ("conflicting-started", "reserved", {"provider_started_at": old}),
                ("conflicting-settled", "reserved", {"settled_at": old}),
                ("conflicting-zero", "reserved", {"actual_cost_usd": Decimal("0")}),
                ("conflicting-cost", "reserved", {"actual_cost_usd": Decimal("0.01")}),
                ("fresh-reserved", "reserved", {"reserved_at": recent}),
            ]
            for key, state, evidence in fixtures:
                insert_reservation(conn, state, **{
                    "reservation_key": key, "reserved_at": old, "updated_at": old,
                    "estimated_cost_usd": Decimal("0.600000"), **evidence,
                })
            before = {row["reservation_key"]: row for row in rows(conn)}
            assert reservations.reap_stale_reservations(ttl_hours=24) == 1
            after = {row["reservation_key"]: row for row in rows(conn)}
            expected_expired = {**before["old-reserved"], "state": "expired",
                                "updated_at": after["old-reserved"]["updated_at"]}
            assert after["old-reserved"] == expected_expired
            updated = raw.execute(
                "SELECT updated_at FROM vkpi_llm_budget_reservations WHERE reservation_key=%s",
                ("old-reserved",),
            ).fetchone()[0]
            assert updated >= recent
            for key in before.keys() - {"old-reserved"}:
                assert after[key] == before[key]
            for scope in ("monthly_total", "provider:openai", SCOPE):
                assert allowance(conn, scope) == Decimal("4.200000")
            assert reservations.reap_stale_reservations(ttl_hours=24) == 0
        finally:
            raw.rollback()
            raw.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            raw.commit()
