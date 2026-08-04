from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_auth_contract as auth  # noqa: E402


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = iter(rows)
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str) -> None:
        self.queries.append(" ".join(query.split()))

    def fetchone(self):
        return next(self.rows)


class _Connection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.cursor_value = _Cursor(rows)

    def cursor(self) -> _Cursor:
        return self.cursor_value


def test_postgres_admin_lookup_is_bound_to_public_schema_and_real_database() -> None:
    connection = _Connection(
        [("on",), ("pg_catalog, public",), ("viltrox2",), (True,), (7, "admin")]
    )

    assert auth.select_acceptance_pg_admin(
        connection,
        expected_database="viltrox2",
    ) == (7, "admin")

    joined = " ".join(connection.cursor_value.queries)
    for required in (
        "SHOW transaction_read_only",
        "SHOW search_path",
        "SELECT pg_catalog.current_database()",
        "pg_catalog.to_regclass('public.vkpi_kol_search_sessions')",
        "FROM public.users AS u",
        "LEFT JOIN public.staff AS s",
        "FROM public.vkpi_kol_search_sessions AS ks",
    ):
        assert required in joined
    assert "COALESCE(" in joined
    assert "pg_catalog.COALESCE" not in joined


@pytest.mark.pg
def test_real_postgres_acceptance_query_parses_and_plans(pg_conn: object) -> None:
    # The pg lane is opt-in and transaction-rolled-back by conftest. EXPLAIN
    # forces PostgreSQL itself to parse, resolve and type-check the exact query
    # without selecting or mutating business rows.
    with pg_conn.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(
            "EXPLAIN "
            + auth.PG_ACCEPTANCE_ADMIN_QUERY
            + " ORDER BY COALESCE(s.is_owner,0) DESC,u.id LIMIT 1"
        )
        assert cursor.fetchone()


@pytest.mark.parametrize(
    "rows",
    [
        [("on",), ('"$user", public',)],
        [("on",), ("pg_catalog, public",), ("shadow",)],
    ],
)
def test_postgres_admin_lookup_rejects_shadow_identity_before_table_reads(
    rows: list[tuple[object, ...]],
) -> None:
    connection = _Connection(rows)

    with pytest.raises(RuntimeError):
        auth.select_acceptance_pg_admin(
            connection,
            expected_database="viltrox2",
        )

    assert not any(
        "FROM public.users" in query for query in connection.cursor_value.queries
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://127.0.0.1/viltrox2?dbname=shadow",
        "postgresql://127.0.0.1/viltrox2?%64bname=shadow",
        "postgresql://127.0.0.1/viltrox2?service=shadow",
        "postgresql://db.internal/viltrox2",
    ],
)
def test_local_database_contract_rejects_identity_overrides(url: str) -> None:
    with pytest.raises(RuntimeError):
        auth.validated_local_database(url, ROOT / "submissions.db", ROOT)


def test_sqlite_admin_lookup_remains_schema_agnostic() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE users(id INTEGER PRIMARY KEY, status TEXT, role TEXT);
        CREATE TABLE staff(id INTEGER PRIMARY KEY, user_id INTEGER, role TEXT,
                           active INTEGER, is_owner INTEGER);
        INSERT INTO users(id,status,role) VALUES(1,'approved','admin');
        INSERT INTO staff(id,user_id,role,active,is_owner)
        VALUES(1,1,'admin',1,1);
        """
    )

    assert auth.select_acceptance_sqlite_admin(connection) == (1, "admin")
