from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.db import connection as db_connection
from app.domains.data_quality import checks as data_quality_checks
from app.domains.data_quality import common as data_quality_common


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations" / "274_vkpi_data_quality_actions.sql"
DOWN = ROOT / "migrations" / "274_vkpi_data_quality_actions_down.sql"

LEGACY_POSTGRES_SCHEMA = """
CREATE TABLE vkpi_data_quality_actions (
  id BIGSERIAL PRIMARY KEY,
  issue_id TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT DEFAULT '',
  staff_id BIGINT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def _unexpected_connection() -> Any:
    raise AssertionError("PostgreSQL schema helpers must not open a connection")


class _EmptyCursor:
    def fetchall(self) -> list[Any]:
        return []

    def fetchone(self) -> None:
        return None


class _ReadOnlyConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(
        self,
        sql: str,
        _params: tuple[Any, ...] = (),
    ) -> _EmptyCursor:
        normalized = " ".join(sql.split()).upper()
        assert normalized.startswith(("SELECT ", "WITH "))
        self.statements.append(normalized)
        return _EmptyCursor()

    def commit(self) -> None:
        raise AssertionError("GET data-quality must not commit")


def test_migration_274_is_runner_owned_and_validates_the_full_contract() -> None:
    up = _normalized(UP)

    assert "begin;" not in up
    assert "commit;" not in up
    assert "create table if not exists vkpi_data_quality_actions" in up
    assert "expected seven canonical columns" in up
    assert "incompatible column contract" in up
    assert "incompatible defaults" in up
    assert "incompatible constraints" in up
    assert "create index if not exists idx_vkpi_data_quality_actions_issue" in up
    assert "incompatible index" in up


def test_migration_274_down_preserves_the_ledger_and_removes_only_its_receipt() -> None:
    down = _normalized(DOWN)

    assert "drop table" not in down
    assert "truncate" not in down
    assert "delete from vkpi_data_quality_actions" not in down
    assert "delete from schema_migrations" in down
    assert "where version_key = '274_vkpi_data_quality_actions.sql'" in down


def test_migration_274_is_discovered_but_its_down_file_is_not() -> None:
    assert "274_vkpi_data_quality_actions.sql" in db_connection._POSTGRES_MIGRATION_SEQUENCE
    assert "274_vkpi_data_quality_actions_down.sql" not in db_connection._POSTGRES_MIGRATION_SEQUENCE


def test_postgres_schema_helper_is_a_connection_free_noop(monkeypatch) -> None:
    monkeypatch.setattr(data_quality_common, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(data_quality_common, "get_conn", _unexpected_connection)

    data_quality_common.ensure_data_quality_schema()


def test_sqlite_schema_helper_still_creates_the_compatibility_schema(
    monkeypatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        monkeypatch.setattr(data_quality_common, "is_postgres_runtime", lambda: False)
        monkeypatch.setattr(data_quality_common, "get_conn", lambda: conn)

        data_quality_common.ensure_data_quality_schema()
        columns = conn.execute(
            "PRAGMA table_info(vkpi_data_quality_actions)"
        ).fetchall()
        indexes = conn.execute(
            "PRAGMA index_list(vkpi_data_quality_actions)"
        ).fetchall()

        assert [row[1] for row in columns] == [
            "id",
            "issue_id",
            "action",
            "reason",
            "staff_id",
            "metadata_json",
            "created_at",
        ]
        assert any(
            row[1] == "idx_vkpi_data_quality_actions_issue" for row in indexes
        )
    finally:
        conn.close()


def test_data_quality_get_path_never_bootstraps_the_action_schema(
    monkeypatch,
) -> None:
    conn = _ReadOnlyConnection()
    monkeypatch.setattr(data_quality_checks, "get_conn", lambda: conn)
    monkeypatch.setattr(data_quality_checks, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(
        data_quality_checks,
        "ensure_vkpi_lineage_schema",
        lambda: None,
    )
    monkeypatch.setattr(
        data_quality_checks,
        "ensure_vkpi_reconciliation_schema",
        lambda: None,
    )
    monkeypatch.setattr(
        data_quality_common,
        "ensure_data_quality_schema",
        lambda: (_ for _ in ()).throw(
            AssertionError("GET must not bootstrap the action schema")
        ),
    )

    result = data_quality_checks.list_issues(
        limit=1,
        staff={"id": 1, "role": "admin"},
    )

    assert isinstance(result["issues"], list)
    assert conn.statements


def _create_scratch_schema(conn: Any) -> str:
    from psycopg import sql

    schema = f"vkpi_migration_274_{uuid.uuid4().hex}"
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    conn.execute(
        sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema))
    )
    conn.execute(
        "CREATE TABLE schema_migrations ("
        "version_key TEXT PRIMARY KEY, "
        "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
        ")"
    )
    return schema


def _drop_scratch_schema(conn: Any, schema: str) -> None:
    from psycopg import sql

    conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.pg
def test_migration_274_up_safe_down_and_reapply_on_real_postgres(
    pg_dsn: str,
) -> None:
    import psycopg

    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        schema = _create_scratch_schema(conn)
        try:
            with conn.transaction():
                conn.execute(up)
                conn.execute(
                    "INSERT INTO schema_migrations(version_key) "
                    "VALUES ('274_vkpi_data_quality_actions.sql')"
                )
            conn.execute(
                "INSERT INTO vkpi_data_quality_actions "
                "(issue_id, action, reason, staff_id, metadata_json) "
                "VALUES ('sentinel', 'resolve', 'preserve me', 7, '{}')"
            )

            conn.execute(down)

            assert conn.execute(
                "SELECT to_regclass('vkpi_data_quality_actions') IS NOT NULL"
            ).fetchone()[0] is True
            assert conn.execute(
                "SELECT count(*) FROM vkpi_data_quality_actions "
                "WHERE issue_id='sentinel'"
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT count(*) FROM schema_migrations "
                "WHERE version_key='274_vkpi_data_quality_actions.sql'"
            ).fetchone()[0] == 0

            conn.execute(up)
            assert conn.execute(
                "SELECT count(*) FROM vkpi_data_quality_actions "
                "WHERE issue_id='sentinel'"
            ).fetchone()[0] == 1
        finally:
            _drop_scratch_schema(conn, schema)


@pytest.mark.pg
def test_migration_274_adopts_a_matching_legacy_table_without_data_loss(
    pg_dsn: str,
) -> None:
    import psycopg

    up = UP.read_text(encoding="utf-8")
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        schema = _create_scratch_schema(conn)
        try:
            conn.execute(LEGACY_POSTGRES_SCHEMA)
            conn.execute(
                "CREATE INDEX idx_vkpi_data_quality_actions_issue "
                "ON vkpi_data_quality_actions(issue_id, created_at DESC)"
            )
            conn.execute(
                "INSERT INTO vkpi_data_quality_actions "
                "(issue_id, action, metadata_json) "
                "VALUES ('legacy', 'ignore', '{}')"
            )

            conn.execute(up)

            assert conn.execute(
                "SELECT count(*) FROM vkpi_data_quality_actions "
                "WHERE issue_id='legacy'"
            ).fetchone()[0] == 1
        finally:
            _drop_scratch_schema(conn, schema)


@pytest.mark.pg
@pytest.mark.parametrize(
    "incompatible_sql, expected_error",
    [
        (
            LEGACY_POSTGRES_SCHEMA.replace(
                "metadata_json TEXT",
                "metadata_json JSONB",
            ),
            "incompatible column contract",
        ),
        (
            LEGACY_POSTGRES_SCHEMA
            + "; CREATE INDEX idx_vkpi_data_quality_actions_issue "
            "ON vkpi_data_quality_actions(created_at, issue_id)",
            "incompatible index",
        ),
        (
            LEGACY_POSTGRES_SCHEMA
            + "; CREATE INDEX idx_vkpi_data_quality_actions_issue "
            "ON vkpi_data_quality_actions(issue_id, created_at DESC) "
            "WHERE issue_id <> ''",
            "incompatible index",
        ),
        (
            LEGACY_POSTGRES_SCHEMA.replace(
                "issue_id TEXT NOT NULL",
                "issue_id TEXT NOT NULL UNIQUE",
            ),
            "incompatible constraints",
        ),
        (
            LEGACY_POSTGRES_SCHEMA
            + "; CREATE SEQUENCE wrong_data_quality_id_seq"
            + "; ALTER TABLE vkpi_data_quality_actions "
            "ALTER COLUMN id SET DEFAULT nextval('wrong_data_quality_id_seq')",
            "incompatible defaults",
        ),
    ],
    ids=[
        "wrong-column-type",
        "wrong-index-definition",
        "partial-index",
        "extra-unique-constraint",
        "wrong-default-sequence",
    ],
)
def test_migration_274_fails_closed_for_incompatible_existing_schema(
    pg_dsn: str,
    incompatible_sql: str,
    expected_error: str,
) -> None:
    import psycopg

    up = UP.read_text(encoding="utf-8")
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        schema = _create_scratch_schema(conn)
        try:
            conn.execute(incompatible_sql)

            with pytest.raises(psycopg.Error, match=expected_error):
                with conn.transaction():
                    conn.execute(up)
                    conn.execute(
                        "INSERT INTO schema_migrations(version_key) "
                        "VALUES ('274_vkpi_data_quality_actions.sql')"
                    )

            assert conn.execute(
                "SELECT count(*) FROM schema_migrations "
                "WHERE version_key='274_vkpi_data_quality_actions.sql'"
            ).fetchone()[0] == 0
        finally:
            _drop_scratch_schema(conn, schema)
