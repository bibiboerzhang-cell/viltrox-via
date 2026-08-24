"""Migration 297 repairs the already-applied-296 Advisor budget gap."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
UP = (ROOT / "migrations/297_vkpi_marketing_advisor_budget.sql").read_text(encoding="utf-8")
DOWN = (ROOT / "migrations/297_vkpi_marketing_advisor_budget_down.sql").read_text(encoding="utf-8")


def test_migration_297_is_dedicated_idempotent_and_conservatively_reversible() -> None:
    normalized_up = " ".join(UP.lower().split())
    normalized_down = " ".join(DOWN.lower().split())
    assert "'cron:marketing_advisor'" in UP
    assert "2.00" in UP
    assert "'fallback_to_evidence_only'" in UP
    assert '"seeded_by":"migration_297"' in UP
    assert '"window":"daily"' in UP
    assert "on conflict (scope) do nothing" in normalized_up

    assert "current_spend = 0" in normalized_down
    assert "vkpi_297_try_parse_jsonb(metadata_json)" in normalized_down
    assert "jsonb_typeof(parsed) <> 'object'" in normalized_down
    assert "when invalid_text_representation" in normalized_down
    assert "metadata_json::jsonb" not in normalized_down
    assert "drop function pg_temp.vkpi_297_try_parse_jsonb(text)" in normalized_down
    assert "reset_at =" in normalized_down
    assert "297_vkpi_marketing_advisor_budget.sql" in DOWN


@pytest.mark.pg
def test_migration_297_real_postgres_repairs_old_296_and_preserves_operator_row(pg_dsn: str) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_budget_297_{uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.execute(
                """
                CREATE TABLE vkpi_provider_budget_caps (
                    scope TEXT PRIMARY KEY,
                    cap_usd NUMERIC(10,2),
                    current_spend NUMERIC(10,4) DEFAULT 0,
                    warning_at NUMERIC(10,2) DEFAULT 0.80,
                    hard_stop_at NUMERIC(10,2) DEFAULT 1.00,
                    reset_at TIMESTAMPTZ,
                    fallback_action TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
                INSERT INTO schema_migrations(version_key)
                VALUES ('296_vkpi_budget_scope_registry.sql'),
                       ('297_vkpi_marketing_advisor_budget.sql');
                """
            )

            conn.execute(UP)
            repaired = conn.execute(
                "SELECT * FROM vkpi_provider_budget_caps WHERE scope='cron:marketing_advisor'"
            ).fetchone()
            assert repaired is not None
            assert float(repaired[1]) == 2.0
            assert '"seeded_by":"migration_297"' in repaired[7]

            conn.execute(UP)
            assert conn.execute(
                "SELECT COUNT(*) FROM vkpi_provider_budget_caps WHERE scope='cron:marketing_advisor'"
            ).fetchone()[0] == 1

            conn.execute(DOWN)
            assert conn.execute(
                "SELECT COUNT(*) FROM vkpi_provider_budget_caps WHERE scope='cron:marketing_advisor'"
            ).fetchone()[0] == 0

            conn.execute(
                """
                INSERT INTO vkpi_provider_budget_caps
                    (scope, cap_usd, current_spend, warning_at, hard_stop_at,
                     reset_at, fallback_action, metadata_json)
                VALUES
                    ('cron:marketing_advisor', 9.00, 1.25, 0.70, 0.95,
                     NOW() + INTERVAL '9 days', 'operator_policy',
                     '{"seeded_by":"operator","window":"daily"}')
                """
            )
            conn.execute(UP)
            conn.execute(DOWN)
            operator = conn.execute(
                "SELECT cap_usd,current_spend,fallback_action,metadata_json "
                "FROM vkpi_provider_budget_caps WHERE scope='cron:marketing_advisor'"
            ).fetchone()
            assert operator is not None
            assert float(operator[0]) == 9.0
            assert float(operator[1]) == 1.25
            assert operator[2] == "operator_policy"
            assert '"seeded_by":"operator"' in operator[3]

            exact_seed_with_operator_edit = (
                '{"seeded_by":"migration_297","tier":"advisor",'
                '"window":"daily","cost_tag":"cron:marketing_advisor",'
                '"operator_note":"keep"}'
            )
            for edited_metadata in (
                "{malformed-json",
                "[]",
                exact_seed_with_operator_edit,
            ):
                conn.execute(
                    "DELETE FROM vkpi_provider_budget_caps "
                    "WHERE scope='cron:marketing_advisor'"
                )
                conn.execute(
                    """
                    INSERT INTO vkpi_provider_budget_caps
                        (scope, cap_usd, current_spend, warning_at, hard_stop_at,
                         reset_at, fallback_action, metadata_json)
                    VALUES
                        ('cron:marketing_advisor', 2.00, 0, 0.80, 1.00,
                         (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                          + INTERVAL '1 day') AT TIME ZONE 'UTC',
                         'fallback_to_evidence_only', %s)
                    """,
                    (edited_metadata,),
                )
                conn.execute(DOWN)
                retained = conn.execute(
                    "SELECT metadata_json FROM vkpi_provider_budget_caps "
                    "WHERE scope='cron:marketing_advisor'"
                ).fetchone()
                assert retained is not None
                assert retained[0] == edited_metadata
        finally:
            conn.execute("SET search_path TO public")
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
