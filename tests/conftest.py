"""Shared pytest fixtures — real Postgres test layer.

The bulk of the suite fakes the database with in-memory sqlite, so the
sqlite->postgres compat/translation layer (``?`` placeholders, JSONB, ON
CONFLICT, ``FOR UPDATE SKIP LOCKED``, the 154-line dialect rewriter) has never
actually been exercised against a live Postgres. This conftest adds a minimal
real-PG fixture set behind the ``pg`` marker.

Isolation: each ``pg`` test runs against the live database inside its own
connection whose transaction is rolled back on teardown, so nothing is
committed. Tests that need cross-connection visibility (e.g. row locking)
manage a uniquely named scratch table and drop it in a ``finally``.

Availability: when no DSN is configured or Postgres is unreachable, every
``pg``-marked test is skipped (never failed) so a missing local database does
not turn CI red.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = _REPO_ROOT / ".env"
_DSN_KEYS = ("DATABASE_URL", "LOCAL_DATABASE_URL")


def _read_env_dsn() -> str:
    """Resolve the Postgres DSN from the live environment, then the repo .env."""
    for key in _DSN_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    if _ENV_PATH.exists():
        for raw in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() in _DSN_KEYS:
                val = val.strip().strip('"').strip("'")
                if val:
                    return val
    return ""


def _probe_pg(dsn: str) -> tuple[bool, str]:
    """Return (available, reason). Reason is only meaningful when unavailable."""
    if not dsn:
        return False, "no DATABASE_URL/LOCAL_DATABASE_URL configured"
    try:
        import psycopg
    except ImportError as exc:
        return False, f"psycopg not installed: {exc}"
    try:
        conn = psycopg.connect(dsn, connect_timeout=5)
        conn.close()
    except psycopg.Error as exc:
        return False, f"Postgres unreachable: {exc}"
    return True, ""


_PG_STATUS: tuple[bool, str] | None = None


def _pg_status() -> tuple[bool, str]:
    global _PG_STATUS
    if _PG_STATUS is None:
        _PG_STATUS = _probe_pg(_read_env_dsn())
    return _PG_STATUS


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip ``pg``-marked tests when the real database is not reachable."""
    if item.get_closest_marker("pg") is None:
        return
    available, reason = _pg_status()
    if not available:
        pytest.skip(f"pg marker: {reason}")


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """Session-scoped DSN for the real Postgres test database."""
    available, reason = _pg_status()
    if not available:
        pytest.skip(f"pg fixture: {reason}")
    return _read_env_dsn()


@pytest.fixture()
def pg_conn(pg_dsn: str) -> Iterator[Any]:
    """A raw psycopg connection scoped to one test, rolled back on teardown."""
    import psycopg

    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def pg_compat(pg_dsn: str) -> Iterator[Any]:
    """A PostgresCompatConnection over a dedicated raw connection.

    This is the exact wrapper production traffic flows through, so SQL run here
    passes through the real ``?``->``%s`` / dialect translation. The underlying
    transaction is rolled back and closed on teardown for isolation.
    """
    import psycopg

    from app.db.connection import PostgresCompatConnection

    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    raw.autocommit = False
    conn = PostgresCompatConnection(raw, pool=None)
    try:
        yield conn
    finally:
        conn.close()
