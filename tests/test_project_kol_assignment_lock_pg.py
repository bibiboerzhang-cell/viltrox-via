"""Real-PostgreSQL concurrency contract for cross-project KOL occupancy."""
from __future__ import annotations

import re
import threading
import uuid
from typing import Any

import pytest


pytestmark = pytest.mark.pg


_SCHEMA_SQL = """
CREATE TABLE users (
    id BIGINT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE staff (
    id BIGINT PRIMARY KEY,
    user_id BIGINT REFERENCES users(id)
);
CREATE TABLE vkpi_projects (
    id BIGINT PRIMARY KEY,
    stage TEXT NOT NULL DEFAULT 'discovery',
    stage_status TEXT NOT NULL DEFAULT 'active',
    restricted BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE vkpi_kol_pool (
    id BIGINT PRIMARY KEY,
    linked_main_kol_id BIGINT,
    display_name TEXT NOT NULL DEFAULT '',
    handle TEXT NOT NULL DEFAULT ''
);
CREATE TABLE vkpi_kol_claims (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL,
    staff_id BIGINT NOT NULL,
    project_id BIGINT,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE vkpi_project_kol_assignments (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id),
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id),
    stage TEXT NOT NULL,
    stage_status TEXT NOT NULL,
    assigned_staff_id BIGINT,
    source TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, kol_pool_id)
);
CREATE TABLE vkpi_kol_pool_touches (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id),
    staff_id BIGINT,
    channel TEXT NOT NULL,
    project_id BIGINT,
    note TEXT NOT NULL DEFAULT '',
    touched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kol_pool_id, channel, project_id)
);
CREATE TABLE vkpi_kol_pool_favorites (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id),
    staff_id BIGINT NOT NULL,
    note TEXT,
    UNIQUE (kol_pool_id, staff_id)
);
"""


def test_pool_row_lock_blocks_cross_staff_double_assignment_and_preserves_admin_force(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The waiter must re-read occupancy after the first assignment commits."""
    import psycopg
    from psycopg import sql

    from app.db.connection import PostgresCompatConnection
    from app.domains.memory import agent_memory_writer
    from app.domains.projects import workflow_projects_kols as workflow

    schema = f"vkpi_kol_assignment_lock_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    seed_raw = None
    seed = None
    threads: list[threading.Thread] = []
    release_first = threading.Event()
    first_locked = threading.Event()
    second_started = threading.Event()
    second_attempting_lock = threading.Event()
    second_acquired = threading.Event()
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, BaseException] = {}
    local = threading.local()

    try:
        with admin.cursor() as cur:
            cur.execute("SELECT current_database()")
            database_name = str((cur.fetchone() or [""])[0] or "")
            assert re.search(
                r"(?:^|[_-])(test|tests|ci|integration|disposable|scratch)(?:[_-]|$)",
                database_name,
                re.I,
            ), f"refusing non-disposable database {database_name!r}"
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        seed_raw = psycopg.connect(pg_dsn, connect_timeout=5)
        seed_raw.autocommit = False
        with seed_raw.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
        seed = PostgresCompatConnection(seed_raw, pool=None)
        for statement in [part.strip() for part in _SCHEMA_SQL.split(";") if part.strip()]:
            seed.execute(statement)
        seed.execute("INSERT INTO users (id, name) VALUES (1001, 'First Owner'), (1002, 'Second Owner'), (1003, 'Admin')")
        seed.execute("INSERT INTO staff (id, user_id) VALUES (101, 1001), (202, 1002), (303, 1003)")
        seed.execute("INSERT INTO vkpi_projects (id) VALUES (11), (22)")
        seed.execute(
            "INSERT INTO vkpi_kol_pool (id, display_name, handle) VALUES (77, 'Locked Creator', '@locked_creator')"
        )
        seed.commit()
        seed.close()
        seed = None
        seed_raw = None

        monkeypatch.setattr(workflow, "ensure_vkpi_schema", lambda: None)
        monkeypatch.setattr(workflow.scope, "assert_project_access", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(workflow, "get_conn", lambda: local.conn)
        monkeypatch.setattr(workflow, "_log_project_audit", lambda **_kwargs: None)
        monkeypatch.setattr(agent_memory_writer, "record_kol_signal", lambda *_args, **_kwargs: None)

        original_locked_occupancy = workflow._locked_pool_claim_occupancy

        def _coordinated_occupancy(conn, pool_ids):
            name = threading.current_thread().name
            if name == "vkpi-kol-owner-second":
                second_attempting_lock.set()
            occupancy = original_locked_occupancy(conn, pool_ids)
            if name == "vkpi-kol-owner-first":
                first_locked.set()
                if not release_first.wait(5):
                    raise AssertionError("test did not release first assignment transaction")
            elif name == "vkpi-kol-owner-second":
                second_acquired.set()
            return occupancy

        monkeypatch.setattr(workflow, "_locked_pool_claim_occupancy", _coordinated_occupancy)

        def _run_assignment(name: str, project_id: int, staff_id: int) -> None:
            raw = None
            conn = None
            try:
                raw = psycopg.connect(pg_dsn, connect_timeout=5)
                raw.autocommit = False
                with raw.cursor() as cur:
                    cur.execute(
                        sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema))
                    )
                conn = PostgresCompatConnection(raw, pool=None)
                local.conn = conn
                if name == "second":
                    second_started.set()
                results[name] = workflow.add_project_kols(
                    project_id,
                    {"kol_pool_ids": [77]},
                    staff={"id": staff_id, "role": "staff"},
                )
            except BaseException as exc:  # surfaced in the main test thread
                errors[name] = exc
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
            finally:
                if conn is not None:
                    conn.close()
                elif raw is not None:
                    raw.close()

        first_thread = threading.Thread(
            target=_run_assignment,
            args=("first", 11, 101),
            name="vkpi-kol-owner-first",
        )
        threads.append(first_thread)
        first_thread.start()
        assert first_locked.wait(3), "first assignment did not acquire the KOL parent-row lock"

        second_thread = threading.Thread(
            target=_run_assignment,
            args=("second", 22, 202),
            name="vkpi-kol-owner-second",
        )
        threads.append(second_thread)
        second_thread.start()
        assert second_started.wait(3), "second PostgreSQL connection did not start"
        assert second_attempting_lock.wait(3), "second assignment did not reach the KOL row lock"
        assert not second_acquired.wait(0.35), "second assignment bypassed the KOL parent-row lock"

        release_first.set()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        assert results["first"]["inserted"] == 1
        assert "first" not in errors
        assert second_acquired.is_set()
        assert "second" in errors
        assert isinstance(errors["second"], ValueError)
        assert "First Owner" in str(errors["second"])
        assert "second" not in results

        # The deliberate manager override remains available and auditable.
        force_raw = psycopg.connect(pg_dsn, connect_timeout=5)
        force_raw.autocommit = False
        with force_raw.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
        force_conn = PostgresCompatConnection(force_raw, pool=None)
        local.conn = force_conn
        try:
            forced = workflow.add_project_kols(
                22,
                {"kol_pool_ids": [77], "force": True},
                staff={"id": 303, "role": "manager"},
            )
            assert forced["inserted"] == 1
            assert forced["forced_claim_conflicts"][0]["occupied_by_staff_id"] == 101
            assignments = force_conn.execute(
                "SELECT project_id, assigned_staff_id FROM vkpi_project_kol_assignments ORDER BY project_id"
            ).fetchall()
            assert [(row["project_id"], row["assigned_staff_id"]) for row in assignments] == [
                (11, 101),
                (22, 303),
            ]
        finally:
            force_conn.close()
    finally:
        release_first.set()
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=5)
        if seed is not None:
            seed.close()
        elif seed_raw is not None:
            seed_raw.close()
        try:
            with admin.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        finally:
            admin.close()
