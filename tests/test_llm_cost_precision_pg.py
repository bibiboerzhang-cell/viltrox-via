from __future__ import annotations

import json
import threading
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from app.db.connection import PostgresCompatConnection
from app.domains.costs import budget_guard
from app.platform import llm_budget_reservations as reservations
from app.platform.llm_budget_reservations import LlmBudgetBlocked


pytestmark = pytest.mark.pg
ROOT = Path(__file__).resolve().parents[1]
UP = (ROOT / "migrations/275_vkpi_llm_cost_precision.sql").read_text(
    encoding="utf-8"
)


def _create_schema(pg_dsn: str, schema: str) -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
        )
        conn.execute(
            """
            CREATE TABLE vkpi_ai_cost_ledger (
              id BIGSERIAL PRIMARY KEY,
              cron_task TEXT,
              ai_provider TEXT,
              model_name TEXT,
              cost_usd NUMERIC(10,4),
              tokens_in INT,
              tokens_out INT,
              kol_pool_id BIGINT,
              staff_id BIGINT,
              task_item_id BIGINT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              occurred_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE vkpi_provider_budget_caps (
              scope TEXT PRIMARY KEY,
              cap_usd NUMERIC(10,2),
              current_spend NUMERIC(10,4) DEFAULT 0,
              warning_at NUMERIC(3,2) DEFAULT 0.80,
              hard_stop_at NUMERIC(3,2) DEFAULT 1.00,
              reset_at TIMESTAMPTZ,
              fallback_action TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE vkpi_llm_budget_reservations (
              reservation_key TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              model_name TEXT NOT NULL,
              purpose TEXT NOT NULL DEFAULT '',
              request_hash TEXT NOT NULL,
              provider_scope TEXT NOT NULL,
              cost_scope TEXT NOT NULL DEFAULT '',
              cumulative_scopes_json TEXT NOT NULL DEFAULT '[]',
              estimated_cost_usd NUMERIC(18,6) NOT NULL,
              actual_cost_usd NUMERIC(18,6),
              state TEXT NOT NULL DEFAULT 'reserved',
              metadata_json TEXT NOT NULL DEFAULT '{}',
              reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              provider_started_at TIMESTAMPTZ,
              settled_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        conn.execute(UP)


def _compat(pg_dsn: str, schema: str) -> PostgresCompatConnection:
    import psycopg
    from psycopg import sql

    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    raw.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    raw.commit()
    return PostgresCompatConnection(raw, pool=None)


def _drop_schema(pg_dsn: str, schema: str) -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(schema)
            )
        )


def _seed_caps(conn: PostgresCompatConnection, cap: str) -> None:
    conn.execute("DELETE FROM vkpi_provider_budget_caps")
    for scope in ("monthly_total", "provider:claude", "single_call"):
        conn.execute(
            """
            INSERT INTO vkpi_provider_budget_caps
              (scope,cap_usd,current_spend,warning_at,hard_stop_at,metadata_json)
            VALUES (?,?,0,0.80,1.00,'{}')
            """,
            (scope, cap),
        )
    conn.commit()


def test_real_postgres_micro_writes_caps_and_thousand_settlements(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = f"vkpi_llm_cost_runtime_{uuid.uuid4().hex}"
    _create_schema(pg_dsn, schema)
    compat = _compat(pg_dsn, schema)
    monkeypatch.setattr(budget_guard, "get_conn", lambda: compat)
    monkeypatch.setattr(budget_guard, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(reservations, "get_conn", lambda: compat)
    monkeypatch.setattr(reservations, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(reservations, "table_exists", lambda _name: True)
    try:
        for micro in (1, 49, 50, 51, 553):
            receipt = budget_guard.record_cost(
                scope="single_call",
                cron_task="pg-micro",
                ai_provider="anthropic",
                model_name="test-model",
                cost_usd=Decimal(micro) / Decimal(1_000_000),
                update_budget_scopes=False,
            )
            assert receipt["cost_micro_usd"] == micro
            assert receipt["persisted_cost_usd"] == f"0.{micro:06d}"

        budget_guard.update_budget(
            "provider:micro-unit",
            {"cap_usd": "0.000050", "current_spend": "0.000033"},
        )
        precise_cap = compat.execute(
            "SELECT cap_usd,current_spend FROM vkpi_provider_budget_caps "
            "WHERE scope='provider:micro-unit'"
        ).fetchone()
        assert precise_cap["cap_usd"] == Decimal("0.000050")
        assert precise_cap["current_spend"] == Decimal("0.000033")

        _seed_caps(compat, "0.000050")
        first = reservations.reserve_llm_budget(
            provider="anthropic",
            model="test-model",
            purpose="pg-cap",
            prompt="fixed",
            estimated_cost_usd="0.000049",
        )
        assert first.estimated_cost_usd == 0.000049
        with pytest.raises(LlmBudgetBlocked, match="hard_stop_or_projected_cap"):
            reservations.reserve_llm_budget(
                provider="anthropic",
                model="test-model",
                purpose="pg-cap-second",
                prompt="fixed",
                estimated_cost_usd="0.000001",
            )

        compat.execute("DELETE FROM vkpi_llm_budget_reservations")
        _seed_caps(compat, "1.000000")
        scopes = json.dumps(["monthly_total", "provider:claude"])
        for index in range(1000):
            compat.execute(
                """
                INSERT INTO vkpi_llm_budget_reservations
                  (reservation_key,provider,model_name,purpose,request_hash,
                   provider_scope,cumulative_scopes_json,estimated_cost_usd,
                   state,provider_started_at)
                VALUES (?,?,?,?,?,?,?,?,'provider_started',NOW())
                """,
                (
                    f"pg-micro-{index}",
                    "anthropic",
                    "test-model",
                    "pg-thousand",
                    f"hash-{index}",
                    "provider:claude",
                    scopes,
                    "0.000001",
                ),
            )
        compat.commit()
        for index in range(1000):
            receipt = reservations.settle_llm_reservation(
                f"pg-micro-{index}",
                Decimal("0.000001"),
            )
            assert receipt["readback_verified"] is True
            assert receipt["actual_cost_micro_usd"] == 1
        totals = {
            row["scope"]: row["current_spend"]
            for row in compat.execute(
                "SELECT scope,current_spend FROM vkpi_provider_budget_caps "
                "WHERE scope IN ('monthly_total','provider:claude')"
            ).fetchall()
        }
        assert totals == {
            "monthly_total": Decimal("0.001000"),
            "provider:claude": Decimal("0.001000"),
        }
    finally:
        compat.close()
        _drop_schema(pg_dsn, schema)


def test_real_postgres_concurrent_settlement_has_no_lost_micro(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = f"vkpi_llm_cost_concurrent_{uuid.uuid4().hex}"
    _create_schema(pg_dsn, schema)
    setup = _compat(pg_dsn, schema)
    _seed_caps(setup, "1.000000")
    scopes = json.dumps(["monthly_total", "provider:claude"])
    for key in ("concurrent-a", "concurrent-b"):
        setup.execute(
            """
            INSERT INTO vkpi_llm_budget_reservations
              (reservation_key,provider,model_name,purpose,request_hash,
               provider_scope,cumulative_scopes_json,estimated_cost_usd,
               state,provider_started_at)
            VALUES (?,?,?,?,?,?,?,?,'provider_started',NOW())
            """,
            (
                key,
                "anthropic",
                "test-model",
                "pg-concurrent",
                f"hash-{key}",
                "provider:claude",
                scopes,
                "0.000001",
            ),
        )
    setup.commit()
    first = _compat(pg_dsn, schema)
    second = _compat(pg_dsn, schema)
    by_thread = {"settle-a": first, "settle-b": second}
    barrier = threading.Barrier(2)
    receipts: list[dict[str, object]] = []
    errors: list[BaseException] = []
    monkeypatch.setattr(
        reservations,
        "get_conn",
        lambda: by_thread[threading.current_thread().name],
    )
    monkeypatch.setattr(reservations, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(reservations, "table_exists", lambda _name: True)

    def settle(key: str) -> None:
        try:
            barrier.wait(timeout=5)
            receipts.append(
                reservations.settle_llm_reservation(
                    key,
                    Decimal("0.000001"),
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(
            target=settle,
            args=("concurrent-a",),
            name="settle-a",
        ),
        threading.Thread(
            target=settle,
            args=("concurrent-b",),
            name="settle-b",
        ),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert len(receipts) == 2
        assert all(receipt["readback_verified"] is True for receipt in receipts)
        row = setup.execute(
            "SELECT current_spend FROM vkpi_provider_budget_caps "
            "WHERE scope='monthly_total'"
        ).fetchone()
        assert row["current_spend"] == Decimal("0.000002")
    finally:
        first.close()
        second.close()
        setup.close()
        _drop_schema(pg_dsn, schema)
