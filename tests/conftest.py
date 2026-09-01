"""Shared pytest fixtures — hermetic by default, real Postgres by opt-in.

The bulk of the suite fakes the database with in-memory sqlite, so the
sqlite->postgres compat/translation layer (``?`` placeholders, JSONB, ON
CONFLICT, ``FOR UPDATE SKIP LOCKED``, the 154-line dialect rewriter) has never
actually been exercised against a live Postgres. This conftest adds a minimal
real-PG fixture set behind the ``pg`` marker.

Isolation: each ``pg`` test runs against the live database inside its own
connection whose transaction is rolled back on teardown, so nothing is
committed. Tests that need cross-connection visibility (e.g. row locking)
manage a uniquely named scratch table and drop it in a ``finally``.

Safety: ordinary pytest runs never read the repository ``.env`` and never use
the repository ``submissions.db``.  They force a temporary SQLite path and
disable Redis before application modules are imported.  The separate
``pytest -m pg`` lane may resolve a separately captured disposable-database
DSN only after opting in with ``VKPI_PYTEST_ALLOW_LIVE_SERVICES=1``; without
that flag every ``pg``-marked test is skipped.  Redis, browser, scheduler and
other external workers remain disabled in both lanes.
"""
from __future__ import annotations

import os
import json
import re
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_DSN_KEYS = ("DATABASE_URL", "LOCAL_DATABASE_URL")
_LIVE_SERVICE_OPT_IN = os.environ.get("VKPI_PYTEST_ALLOW_LIVE_SERVICES", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_CAPTURED_LIVE_DSN = next(
    (
        os.environ.get(key, "").strip()
        for key in _DSN_KEYS
        if os.environ.get(key, "").strip()
    ),
    "",
)
_HERMETIC_RUNTIME_DIR: tempfile.TemporaryDirectory[str] | None = None


# This executes while pytest loads conftest, before it imports test modules (and
# therefore before app.core.config can read the repository .env).  The ordinary
# suite remains fully hermetic.  The explicit live-service lane is different:
# it is run as ``pytest -m pg`` against a disposable PostgreSQL database and
# must let the production application connection layer use that same DSN.
# Keeping the app on SQLite in that lane made the so-called real-PG endpoint
# smoke silently exercise the wrong dialect and hid hundreds of skipped checks.
_HERMETIC_RUNTIME_DIR = tempfile.TemporaryDirectory(prefix="vkpi-pytest-")
_test_db_path = Path(_HERMETIC_RUNTIME_DIR.name) / "vkpi-tests.db"
_common_test_env = {
    "VKPI_SKIP_DOTENV": "1",
    "ENVIRONMENT": "test",
    "V2_PRODUCTION_MODE": "0",
    "DATABASE_POOL_URL": "",
    "DB_USE_PGBOUNCER": "0",
    "REDIS_URL": "",
    "ENABLE_LOCAL_ORCHESTRATOR": "0",
    "ENABLE_BROWSER": "0",
    "ENABLE_SCHEDULER": "0",
    "ENABLE_UPLOAD_CLEANUP": "0",
    "VKPI_ASYNC_ENABLED": "0",
}
if _LIVE_SERVICE_OPT_IN and _CAPTURED_LIVE_DSN:
    _common_test_env.update(
        {
            "VKPI_PYTEST_HERMETIC": "0",
            "DB_RUNTIME_BACKEND": "postgres",
            "DB_TARGET_BACKEND": "postgres",
            "DATABASE_URL": _CAPTURED_LIVE_DSN,
            "LOCAL_DATABASE_URL": _CAPTURED_LIVE_DSN,
            "POSTGRES_POOL_MIN_SIZE": "1",
            "POSTGRES_POOL_MAX_SIZE": "4",
        }
    )
else:
    _common_test_env.update(
        {
            "VKPI_PYTEST_HERMETIC": "1",
            "DB_RUNTIME_BACKEND": "sqlite",
            "DB_TARGET_BACKEND": "sqlite",
            "DB_PATH": str(_test_db_path),
            "DATABASE_URL": "",
            "LOCAL_DATABASE_URL": "",
        }
    )
os.environ.update(_common_test_env)
# 密闭纪律:操作员放行开关(runtime/local_operator_env.sh 注入)绝不能漂进测试进程,
# 否则「就绪门默认 fail-closed」类断言会被本机运行环境污染;要测放行的用 monkeypatch.setenv。
os.environ.pop("VKPI_LLM_READINESS_OPERATOR_ACK", None)


def _read_env_dsn() -> str:
    """Resolve a fixture-only Postgres DSN without mutating app runtime env."""
    if not _LIVE_SERVICE_OPT_IN:
        return ""
    # Never fall back to the repository .env here.  A developer must pass the
    # disposable DSN explicitly in the process environment; otherwise an
    # opt-in typo could point destructive/scratch-table tests at business data.
    return _CAPTURED_LIVE_DSN


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
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database()")
                database_name = str((cur.fetchone() or [""])[0] or "")
            if not re.search(r"(?:^|[_-])(test|tests|ci|integration|disposable|scratch)(?:[_-]|$)", database_name, re.I):
                return False, (
                    "refusing non-disposable PostgreSQL database "
                    f"{database_name!r}; use a name containing test/ci/integration/disposable/scratch"
                )
        finally:
            conn.close()
    except psycopg.Error as exc:
        return False, f"Postgres unreachable: {exc}"
    return True, ""


_PG_STATUS: tuple[bool, str] | None = None


def _pg_status() -> tuple[bool, str]:
    global _PG_STATUS
    if _PG_STATUS is None:
        if not _LIVE_SERVICE_OPT_IN:
            _PG_STATUS = (
                False,
                "live services disabled; set VKPI_PYTEST_ALLOW_LIVE_SERVICES=1 to run pg tests",
            )
        else:
            _PG_STATUS = _probe_pg(_read_env_dsn())
    return _PG_STATUS


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Enforce explicit platform and live-service test boundaries."""
    if item.get_closest_marker("darwin_controller") is not None and sys.platform != "darwin":
        pytest.skip("darwin_controller marker: requires the reviewed macOS controller runtime")
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


@pytest.fixture(scope="session")
def pg_test_identities(pg_dsn: str) -> Iterator[dict[str, int]]:
    """Provide the two employee identities used by the real-PG security/race lane.

    Historical tests assumed staff 7676/7677 already existed in the developer's
    business database.  A disposable CI database correctly has no such rows,
    which turned the lane red for fixture foreign-key failures instead of the
    product invariant under test.  Seed only the dedicated IDs, remember exactly
    which rows this fixture created, and never update a pre-existing identity.
    """

    import psycopg

    users = ((7952, "a"), (7953, "b"))
    staff = ((7676, 7952), (7677, 7953))
    permissions = json.dumps(
        {
            "vkpi": "write",
            "kol_ops": "read",
            "system": "none",
            "system.api_keys": "none",
            "system.usage": "none",
            "system.models": "none",
            "system.restart": "none",
            "system.members": "none",
        },
        separators=(",", ":"),
    )
    created_users: list[int] = []
    created_staff: list[int] = []
    conn = psycopg.connect(pg_dsn, connect_timeout=5)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for user_id, suffix in users:
                cur.execute(
                    """
                    INSERT INTO users (
                        id, email, password_hash, name, status, role, email_verified
                    ) VALUES (%s, %s, '!vkpi-pg-test-only!', %s, 'active', 'creator', 1)
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """,
                    (user_id, f"vkpi-pg-test-{suffix}@example.invalid", f"PG Test {suffix.upper()}"),
                )
                row = cur.fetchone()
                if row:
                    created_users.append(int(row[0]))
            for staff_id, user_id in staff:
                cur.execute(
                    """
                    INSERT INTO staff (
                        id, user_id, role, permissions_json, active, is_owner, accepted_at
                    ) VALUES (%s, %s, 'employee', %s, 1, 0, NOW())
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """,
                    (staff_id, user_id, permissions),
                )
                row = cur.fetchone()
                if row:
                    created_staff.append(int(row[0]))
        yield {"a_uid": 7952, "a_sid": 7676, "b_uid": 7953, "b_sid": 7677}
    finally:
        try:
            with conn.cursor() as cur:
                if created_staff:
                    cur.execute("DELETE FROM staff WHERE id = ANY(%s)", (created_staff,))
                if created_users:
                    cur.execute("DELETE FROM users WHERE id = ANY(%s)", (created_users,))
        finally:
            conn.close()


_ACTION_TEST_SCHEMA = """
CREATE TABLE vkpi_action_inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'low',
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    suggested_endpoint TEXT NOT NULL DEFAULT '',
    estimated_cost_cents INTEGER NOT NULL DEFAULT 0,
    writes_business_data INTEGER NOT NULL DEFAULT 0,
    uses_llm INTEGER NOT NULL DEFAULT 0,
    requires_approval INTEGER NOT NULL DEFAULT 1,
    owner_staff_id INTEGER,
    reason TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    expected_gain TEXT NOT NULL DEFAULT '',
    risk_level TEXT NOT NULL DEFAULT 'low',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    result_checklist_json TEXT NOT NULL DEFAULT '{}',
    approval_reason TEXT,
    approved_by_staff_id INTEGER,
    approved_at TEXT,
    approval_snapshot_sha256 TEXT,
    verification_plan_json TEXT NOT NULL DEFAULT '[]',
    affected_tables_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'suggested',
    touches_v6_fit INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE vkpi_action_execution_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id INTEGER,
    category TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL DEFAULT '',
    actor_staff_id INTEGER,
    mode TEXT NOT NULL,
    outcome TEXT NOT NULL,
    endpoint TEXT NOT NULL DEFAULT '',
    cost_cents INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(action_id) REFERENCES vkpi_action_inbox(id) ON DELETE CASCADE
);

CREATE TABLE vkpi_event_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL DEFAULT 1,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    actor_type TEXT NOT NULL DEFAULT 'system',
    actor_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    trace_id TEXT NOT NULL DEFAULT '',
    confidence REAL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX uq_test_action_required_approval_event
ON vkpi_event_ledger(organization_id,entity_type,entity_id,source)
WHERE event_type='action_approved' AND source='action_inbox.required_approval';
"""


@pytest.fixture()
def hermetic_action_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Provide the Action Inbox tests a fresh SQLite file, never submissions.db.

    Production uses Postgres for this domain.  The domain keeps a SQLite branch
    for deterministic state-machine tests; each test gets its own file so the
    concurrent reconciliation case can use multiple real connections safely.
    """
    from app.db import connection as db_connection

    db_connection.close_db_runtime_sync()
    db_path = (tmp_path / "actions.db").resolve()
    production_path = (_REPO_ROOT / "submissions.db").resolve()
    assert db_path != production_path
    monkeypatch.setattr(db_connection, "DB_PATH", db_path)
    monkeypatch.setattr(db_connection, "DB_RUNTIME_BACKEND", "sqlite")
    monkeypatch.setattr(db_connection, "DB_RUNTIME_URL", "")

    setup = sqlite3.connect(str(db_path))
    try:
        setup.executescript(_ACTION_TEST_SCHEMA)
        setup.commit()
    finally:
        setup.close()

    try:
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
