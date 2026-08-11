from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from app.db import connection


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/275_vkpi_llm_cost_precision.sql"
DOWN = ROOT / "migrations/275_vkpi_llm_cost_precision_down.sql"


def test_migration_275_is_latest_and_runner_owned() -> None:
    sequence = connection._discover_postgres_migrations()
    assert UP.name in sequence
    assert sequence.index(UP.name) < sequence.index("276_vkpi_prediction_runs_immutable.sql")
    up = UP.read_text(encoding="utf-8")
    assert connection._FORWARD_TRANSACTION_CONTROL_RE.search(up) is None
    assert "NUMERIC(18, 6)" in up
    assert "vkpi_ai_cost_ledger" in up
    assert "vkpi_provider_budget_caps" in up


def test_migration_275_down_refuses_silent_precision_loss() -> None:
    down = DOWN.read_text(encoding="utf-8").lower()
    assert "raise exception" in down
    assert "round(cost_usd, 4)" in down
    assert "round(cap_usd, 2)" in down
    assert "round(current_spend, 4)" in down
    assert "using cost_usd::numeric(10, 4)" in down
    assert "using cap_usd::numeric(10, 2)" in down
    assert "using current_spend::numeric(10, 4)" in down


@pytest.mark.pg
def test_migration_275_up_and_fail_closed_down_on_real_postgres(
    pg_dsn: str,
) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_llm_cost_precision_{uuid.uuid4().hex}"
    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL("SET search_path TO {}, public").format(
                    sql.Identifier(schema)
                )
            )
            conn.execute(
                """
                CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
                INSERT INTO schema_migrations
                VALUES ('275_vkpi_llm_cost_precision.sql');
                CREATE TABLE vkpi_ai_cost_ledger (
                  id BIGSERIAL PRIMARY KEY,
                  cost_usd NUMERIC(10,4)
                );
                CREATE TABLE vkpi_provider_budget_caps (
                  scope TEXT PRIMARY KEY,
                  cap_usd NUMERIC(10,2),
                  current_spend NUMERIC(10,4)
                );
                INSERT INTO vkpi_ai_cost_ledger(cost_usd) VALUES (1.2500);
                INSERT INTO vkpi_provider_budget_caps
                  (scope,cap_usd,current_spend)
                VALUES ('monthly_total',10.00,1.2500);
                """
            )
            conn.execute(up)
            shapes = {
                (row[0], row[1]): (row[2], row[3])
                for row in conn.execute(
                    """
                    SELECT table_name,column_name,numeric_precision,numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema=current_schema()
                      AND (
                        (table_name='vkpi_ai_cost_ledger'
                         AND column_name='cost_usd')
                        OR
                        (table_name='vkpi_provider_budget_caps'
                         AND column_name IN ('cap_usd','current_spend'))
                      )
                    """
                ).fetchall()
            }
            assert shapes == {
                ("vkpi_ai_cost_ledger", "cost_usd"): (18, 6),
                ("vkpi_provider_budget_caps", "cap_usd"): (18, 6),
                ("vkpi_provider_budget_caps", "current_spend"): (18, 6),
            }

            conn.execute(
                "INSERT INTO vkpi_ai_cost_ledger(cost_usd) VALUES (0.000033)"
            )
            conn.execute(
                "UPDATE vkpi_provider_budget_caps "
                "SET cap_usd=0.000050,current_spend=0.000033 "
                "WHERE scope='monthly_total'"
            )
            assert conn.execute(
                "SELECT cost_usd FROM vkpi_ai_cost_ledger ORDER BY id DESC LIMIT 1"
            ).fetchone()[0] == Decimal("0.000033")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(down)

            conn.execute(
                "DELETE FROM vkpi_ai_cost_ledger WHERE cost_usd=0.000033"
            )
            conn.execute(
                "UPDATE vkpi_provider_budget_caps "
                "SET cap_usd=10.00,current_spend=1.2500 "
                "WHERE scope='monthly_total'"
            )
            conn.execute(down)
            narrowed = {
                (row[0], row[1]): (row[2], row[3])
                for row in conn.execute(
                    """
                    SELECT table_name,column_name,numeric_precision,numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema=current_schema()
                      AND (
                        (table_name='vkpi_ai_cost_ledger'
                         AND column_name='cost_usd')
                        OR
                        (table_name='vkpi_provider_budget_caps'
                         AND column_name IN ('cap_usd','current_spend'))
                      )
                    """
                ).fetchall()
            }
            assert narrowed == {
                ("vkpi_ai_cost_ledger", "cost_usd"): (10, 4),
                ("vkpi_provider_budget_caps", "cap_usd"): (10, 2),
                ("vkpi_provider_budget_caps", "current_spend"): (10, 4),
            }
            assert conn.execute(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version_key='275_vkpi_llm_cost_precision.sql'"
            ).fetchone()[0] == 0
        finally:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
