"""Real-PostgreSQL concurrency contract for smart-search draft serialization."""
from __future__ import annotations

import json
import re
import threading
import uuid
from typing import Any

import pytest


pytestmark = pytest.mark.pg


def test_owner_session_row_lock_serializes_draft_reuse(pg_dsn: str) -> None:
    """A contender cannot pass the owner row until the first draft commits."""
    import psycopg
    from psycopg import sql

    from app.db.connection import PostgresCompatConnection
    from app.domains.projects import workflow_projects

    schema = f"vkpi_draft_lock_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    raw_first = None
    first = None
    contender: threading.Thread | None = None
    contender_started = threading.Event()
    contender_acquired = threading.Event()
    contender_finished = threading.Event()
    observed: dict[str, Any] = {}
    errors: list[BaseException] = []
    first_released = False

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

        raw_first = psycopg.connect(pg_dsn, connect_timeout=5)
        raw_first.autocommit = False
        with raw_first.cursor() as cur:
            cur.execute(
                sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema))
            )
        first = PostgresCompatConnection(raw_first, pool=None)
        first.execute(
            """
            CREATE TABLE vkpi_kol_search_sessions (
                id BIGINT PRIMARY KEY,
                created_by BIGINT NOT NULL
            )
            """
        )
        first.execute(
            """
            CREATE TABLE vkpi_projects (
                id BIGSERIAL PRIMARY KEY,
                source_type TEXT NOT NULL,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        first.execute(
            "INSERT INTO vkpi_kol_search_sessions (id, created_by) VALUES (?, ?)",
            (44, 7),
        )
        first.commit()

        workflow_projects._lock_owned_search_session_for_draft(
            first,
            session_id=44,
            owner_id=7,
        )
        first.execute(
            """
            INSERT INTO vkpi_projects (source_type, metadata_json)
            VALUES ('smart_search', ?::jsonb)
            """,
            ('{"search_session_id": 44}',),
        )

        def _contend() -> None:
            raw_second = None
            second = None
            try:
                raw_second = psycopg.connect(pg_dsn, connect_timeout=5)
                raw_second.autocommit = False
                with raw_second.cursor() as cur:
                    cur.execute(
                        sql.SQL("SET search_path TO {}, pg_catalog").format(
                            sql.Identifier(schema)
                        )
                    )
                second = PostgresCompatConnection(raw_second, pool=None)
                contender_started.set()
                workflow_projects._lock_owned_search_session_for_draft(
                    second,
                    session_id=44,
                    owner_id=7,
                )
                contender_acquired.set()
                row = second.execute(
                    """
                    SELECT id, metadata_json
                    FROM vkpi_projects
                    WHERE source_type='smart_search'
                      AND metadata_json->>'search_session_id'=?
                    """,
                    ("44",),
                ).fetchone()
                observed.update(dict(row) if row else {})
                second.commit()
            except BaseException as exc:  # surfaced in the main test thread
                errors.append(exc)
            finally:
                contender_finished.set()
                if second is not None:
                    second.close()
                elif raw_second is not None:
                    raw_second.close()

        contender = threading.Thread(target=_contend, name="vkpi-draft-lock-contender")
        contender.start()
        assert contender_started.wait(3), "second PostgreSQL connection did not start"

        # The second connection has reached SELECT ... FOR UPDATE but cannot
        # observe/reuse the uncommitted project while the first owns the row.
        assert not contender_acquired.wait(0.35), "second request bypassed the owner row lock"

        first.commit()
        first_released = True

        assert contender_finished.wait(5), "second request did not resume after first commit"
        contender.join(timeout=1)
        assert not contender.is_alive()
        assert errors == []
        assert contender_acquired.is_set()
        assert int(observed.get("id") or 0) > 0
        metadata = observed.get("metadata_json")
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        assert metadata == {"search_session_id": 44}
    finally:
        # Any assertion before the explicit commit must still release the row
        # so the contender and schema cleanup cannot hang.
        if first is not None:
            if not first_released:
                try:
                    first.rollback()
                except Exception:
                    pass
            first.close()
        elif raw_first is not None:
            raw_first.close()
        if contender is not None and contender.is_alive():
            contender.join(timeout=5)
        try:
            with admin.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        finally:
            admin.close()
