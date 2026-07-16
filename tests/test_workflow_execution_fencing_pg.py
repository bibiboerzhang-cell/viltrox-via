"""Optional real-PostgreSQL contracts for workflow claim and migration 265."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

import pytest

from app.domains.platform import workflow_repository


pytestmark = pytest.mark.pg
ROOT = Path(__file__).resolve().parents[1]
UP = (ROOT / "migrations/265_vkpi_workflow_execution_fencing.sql").read_text(encoding="utf-8")
DOWN = (ROOT / "migrations/265_vkpi_workflow_execution_fencing_down.sql").read_text(encoding="utf-8")


def _base_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
        CREATE TABLE vkpi_workflow_runs (
          id BIGSERIAL PRIMARY KEY,
          organization_id BIGINT NOT NULL DEFAULT 1,
          workflow_name TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          input_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          current_step INTEGER NOT NULL DEFAULT 0,
          entity_type TEXT NOT NULL DEFAULT '',
          entity_id TEXT NOT NULL DEFAULT '',
          trace_id TEXT NOT NULL DEFAULT '',
          last_error TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE vkpi_workflow_steps (
          id BIGSERIAL PRIMARY KEY,
          run_id BIGINT NOT NULL,
          step_index INTEGER NOT NULL,
          step_name TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending',
          output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          error TEXT NOT NULL DEFAULT '',
          started_at TIMESTAMPTZ,
          finished_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE TABLE vkpi_workflow_checkpoints (
          id BIGSERIAL PRIMARY KEY,
          run_id BIGINT NOT NULL,
          step_index INTEGER NOT NULL,
          state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def _compat(pg_dsn: str, schema: str):
    import psycopg
    from psycopg import sql

    from app.db.connection import PostgresCompatConnection

    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    raw.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    raw.commit()
    return PostgresCompatConnection(raw, pool=None)


def test_migration_265_up_and_down_on_real_postgres(pg_dsn: str) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_workflow_migration_{uuid.uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            _base_schema(conn)
            conn.execute(UP)
            columns = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name='vkpi_workflow_runs'"
                ).fetchall()
            }
            assert {
                "lease_owner",
                "lease_token_hash",
                "fence_token",
                "lease_expires_at",
                "heartbeat_at",
                "attempt_no",
                "row_version",
            } <= columns
            assert conn.execute(
                "SELECT COUNT(*) FROM pg_indexes WHERE schemaname=current_schema() "
                "AND indexname IN ('uq_vkpi_workflow_step_once','uq_vkpi_workflow_checkpoint_once')"
            ).fetchone()[0] == 2
            conn.execute(DOWN)
            assert conn.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='vkpi_workflow_runs' "
                "AND column_name='lease_token_hash'"
            ).fetchone()[0] == 0
        finally:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_real_postgres_concurrent_workflow_claim_has_one_winner(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_workflow_claim_{uuid.uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as setup:
        setup.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        setup.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        _base_schema(setup)
        setup.execute(UP)
        run_id = setup.execute(
            "INSERT INTO vkpi_workflow_runs (workflow_name, input_json) "
            "VALUES ('concurrent', '{}'::jsonb) RETURNING id"
        ).fetchone()[0]

    first = _compat(pg_dsn, schema)
    second = _compat(pg_dsn, schema)
    by_thread = {"claim-first": first, "claim-second": second}
    barrier = threading.Barrier(2)
    outcomes: list[dict[str, object]] = []
    errors: list[BaseException] = []
    monkeypatch.setattr(
        workflow_repository,
        "get_conn",
        lambda: by_thread[threading.current_thread().name],
    )
    monkeypatch.setattr(workflow_repository, "table_exists", lambda _name: True)

    def claim() -> None:
        try:
            barrier.wait(timeout=5)
            outcomes.append(
                workflow_repository.claim_run(
                    int(run_id),
                    threading.current_thread().name,
                    lease_seconds=60,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=claim, name="claim-first"),
        threading.Thread(target=claim, name="claim-second"),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(str(item["status"]) for item in outcomes) == ["acquired", "in_progress"]
        winner = next(item for item in outcomes if item["status"] == "acquired")["claim"]
        assert winner.fence_token == 1
    finally:
        first.close()
        second.close()
        with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as cleanup:
            cleanup.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_real_postgres_recovery_scan_selects_only_resumable_runs(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_workflow_recovery_{uuid.uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as setup:
        setup.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        setup.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        _base_schema(setup)
        setup.execute(UP)
        failed_id = setup.execute(
            "INSERT INTO vkpi_workflow_runs "
            "(workflow_name, status, input_json, updated_at) "
            "VALUES ('agent_cycle', 'failed', '{}'::jsonb, NOW()-INTERVAL '5 minutes') "
            "RETURNING id"
        ).fetchone()[0]
        expired_id = setup.execute(
            "INSERT INTO vkpi_workflow_runs "
            "(workflow_name, status, input_json, lease_owner, lease_token_hash, "
            "lease_expires_at, updated_at) VALUES "
            "('fulfillment_sweep', 'running', '{}'::jsonb, 'dead-worker', "
            "repeat('a',64), NOW()-INTERVAL '1 minute', NOW()-INTERVAL '5 minutes') "
            "RETURNING id"
        ).fetchone()[0]
        live_id = setup.execute(
            "INSERT INTO vkpi_workflow_runs "
            "(workflow_name, status, input_json, lease_owner, lease_token_hash, "
            "lease_expires_at, updated_at) VALUES "
            "('agent_cycle', 'running', '{}'::jsonb, 'live-worker', "
            "repeat('b',64), NOW()+INTERVAL '10 minutes', NOW()-INTERVAL '5 minutes') "
            "RETURNING id"
        ).fetchone()[0]

    compat = _compat(pg_dsn, schema)
    monkeypatch.setattr(workflow_repository, "get_conn", lambda: compat)
    monkeypatch.setattr(workflow_repository, "table_exists", lambda _name: True)
    try:
        candidates = workflow_repository.list_recoverable_runs(
            limit=10,
            minimum_age_seconds=0,
        )
        selected = {int(row["id"]) for row in candidates}

        assert selected == {int(failed_id), int(expired_id)}
        assert int(live_id) not in selected
    finally:
        compat.close()
        with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as cleanup:
            cleanup.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
