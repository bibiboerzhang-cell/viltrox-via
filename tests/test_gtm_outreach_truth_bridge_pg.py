"""Disposable PostgreSQL contracts for outreach truth immutability and locks."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


pytestmark = pytest.mark.pg

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/277_vkpi_action_outreach_truth_bridge.sql"
DOWN = ROOT / "migrations/277_vkpi_action_outreach_truth_bridge_down.sql"


def _schema_name() -> str:
    return f"vkpi_outreach_test_{uuid4().hex}"


def _set_search_path(conn: psycopg.Connection, schema: str) -> None:
    conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))


def _drop_schema(pg_dsn: str, schema: str) -> None:
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_migration_277_real_pg_up_down_fk_and_immutable_triggers(pg_dsn: str) -> None:
    schema = _schema_name()
    try:
        with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            _set_search_path(conn, schema)
            conn.execute("CREATE TABLE vkpi_action_inbox (id BIGINT PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE vkpi_prediction_runs (organization_id TEXT, run_id TEXT, "
                "PRIMARY KEY (organization_id, run_id))"
            )
            conn.execute("CREATE TABLE kols (id BIGINT PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE vkpi_kol_pool (id BIGINT PRIMARY KEY, "
                "linked_main_kol_id BIGINT REFERENCES kols(id), platform TEXT)"
            )
            conn.execute(
                "CREATE TABLE vkpi_projects (id BIGINT PRIMARY KEY, "
                "kol_id BIGINT REFERENCES kols(id))"
            )
            conn.execute(
                "CREATE TABLE vkpi_messages (id BIGINT PRIMARY KEY, "
                "project_id BIGINT REFERENCES vkpi_projects(id), "
                "kol_id BIGINT REFERENCES kols(id))"
            )
            conn.execute(
                "CREATE TABLE vkpi_event_ledger (id BIGSERIAL PRIMARY KEY, "
                "organization_id BIGINT, event_type TEXT, entity_type TEXT, "
                "entity_id TEXT, source TEXT)"
            )
            conn.execute("CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY)")
            conn.execute(UP.read_text(encoding="utf-8"))

            conn.execute("INSERT INTO vkpi_action_inbox VALUES (41)")
            conn.execute("INSERT INTO vkpi_prediction_runs VALUES ('viltrox','run-41')")
            conn.execute("INSERT INTO kols VALUES (9)")
            conn.execute("INSERT INTO vkpi_kol_pool VALUES (17,9,'youtube')")
            conn.execute("INSERT INTO vkpi_projects VALUES (10,9)")
            conn.execute("INSERT INTO vkpi_messages VALUES (100,10,9)")
            conn.execute(
                """
                INSERT INTO vkpi_action_outreach_truth_bridges (
                  organization_id, action_inbox_id, prediction_organization_id,
                  prediction_run_id, project_id, kol_pool_id, kol_id, product_sku,
                  channel, first_outbound_message_id, first_outbound_at,
                  observation_start_at, observation_end_at, action_approved_at,
                  approval_snapshot_sha256, first_outbound_created_at, actor_staff_id,
                  correlation_id, request_fingerprint, binding_fingerprint, verified_at
                ) VALUES (
                  1,41,'viltrox','run-41',10,17,9,'AF-26','youtube',100,
                  '2026-08-12T00:00:00Z','2026-08-11T00:00:00Z',
                  '2026-08-18T00:00:00Z','2026-08-11T01:00:00Z',
                  repeat('a',64),'2026-08-12T00:01:00Z',7,'binding-0001',
                  repeat('b',64),repeat('c',64),'2026-08-19T00:00:00Z'
                )
                """
            )
            candidate = (
                '{"schema":"vkpi_action_outreach_reply_review_candidate/v1"}'
            )
            conn.execute(
                """
                INSERT INTO vkpi_action_outreach_reply_truth_receipts (
                  organization_id,binding_id,outcome,inbound_message_id,
                  inbound_captured_at,inbound_created_at,first_outbound_at,
                  observation_end_at,candidate_observed_at,verified_at,actor_staff_id,
                  correlation_id,request_fingerprint,binding_fingerprint,
                  review_candidate_sha256,review_candidate_json,receipt_fingerprint
                ) VALUES (
                  1,1,'no_reply',NULL,NULL,NULL,'2026-08-12T00:00:00Z',
                  '2026-08-18T00:00:00Z','2026-08-19T00:00:00Z',
                  '2026-08-19T00:00:01Z',7,'reply-0001',repeat('d',64),
                  repeat('c',64),repeat('e',64),%s::jsonb,repeat('f',64)
                )
                """,
                (candidate,),
            )
            conn.execute(
                "INSERT INTO vkpi_event_ledger "
                "(organization_id,event_type,entity_type,entity_id,source) "
                "VALUES (1,'action_outreach_bound','action_outreach_bridge','1','test')"
            )

            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(
                    "UPDATE vkpi_action_outreach_truth_bridges SET channel='instagram' WHERE id=1"
                )
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(
                    "DELETE FROM vkpi_action_outreach_reply_truth_receipts WHERE id=1"
                )
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(
                    "UPDATE vkpi_event_ledger SET source='changed' "
                    "WHERE event_type='action_outreach_bound'"
                )
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                conn.execute("DELETE FROM vkpi_projects WHERE id=10")

            conn.execute(DOWN.read_text(encoding="utf-8"))
            assert conn.execute(
                "SELECT to_regclass(current_schema() || '.vkpi_action_outreach_truth_bridges')"
            ).fetchone()[0] is None
            assert conn.execute(
                "SELECT to_regclass(current_schema() || "
                "'.vkpi_action_outreach_reply_truth_receipts')"
            ).fetchone()[0] is None
    finally:
        _drop_schema(pg_dsn, schema)


def test_real_pg_parent_locks_block_pool_project_and_message_phantoms(
    pg_dsn: str,
) -> None:
    schema = _schema_name()
    first = second = None
    try:
        with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            _set_search_path(admin, schema)
            admin.execute("CREATE TABLE kols (id BIGINT PRIMARY KEY)")
            admin.execute(
                "CREATE TABLE vkpi_kol_pool (id BIGINT PRIMARY KEY, platform TEXT, "
                "linked_main_kol_id BIGINT REFERENCES kols(id))"
            )
            admin.execute(
                "CREATE TABLE vkpi_projects (id BIGINT PRIMARY KEY, "
                "kol_id BIGINT REFERENCES kols(id))"
            )
            admin.execute(
                "CREATE TABLE vkpi_messages (id BIGINT PRIMARY KEY, "
                "project_id BIGINT REFERENCES vkpi_projects(id), "
                "kol_id BIGINT REFERENCES kols(id))"
            )
            admin.execute("INSERT INTO kols VALUES (9)")
            admin.execute("INSERT INTO vkpi_kol_pool VALUES (17,'youtube',9)")
            admin.execute("INSERT INTO vkpi_projects VALUES (10,9)")
            admin.execute("INSERT INTO vkpi_messages VALUES (100,10,9)")

        first = psycopg.connect(pg_dsn, connect_timeout=5)
        second = psycopg.connect(pg_dsn, connect_timeout=5)
        _set_search_path(first, schema)
        _set_search_path(second, schema)
        first.execute("SELECT id FROM vkpi_kol_pool WHERE id=17 FOR UPDATE").fetchall()
        first.execute("SELECT id FROM kols WHERE id=9 FOR UPDATE").fetchall()
        first.execute("SELECT id FROM vkpi_projects WHERE id=10 FOR UPDATE").fetchall()
        first.execute("SELECT id FROM vkpi_messages WHERE project_id=10 FOR UPDATE").fetchall()

        second.execute("SET LOCAL lock_timeout='250ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            second.execute("UPDATE vkpi_kol_pool SET platform='instagram' WHERE id=17")
        second.rollback()
        _set_search_path(second, schema)
        second.execute("SET LOCAL lock_timeout='250ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            second.execute("INSERT INTO vkpi_projects VALUES (11,9)")
        second.rollback()
        _set_search_path(second, schema)
        second.execute("SET LOCAL lock_timeout='250ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            second.execute("INSERT INTO vkpi_messages VALUES (101,10,9)")
        second.rollback()

        first.commit()
        _set_search_path(second, schema)
        second.execute("INSERT INTO vkpi_messages VALUES (101,10,9)")
        second.commit()
    finally:
        if second is not None:
            second.rollback()
            second.close()
        if first is not None:
            first.rollback()
            first.close()
        _drop_schema(pg_dsn, schema)
