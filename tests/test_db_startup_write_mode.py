from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("JWT_SECRET", "test-secret")

from app.db import connection  # noqa: E402
import app.main as main  # noqa: E402


def _postgres_startup_spies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(connection, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(connection, "_get_pg_pool", lambda: calls.append("pool"))
    monkeypatch.setattr(connection, "_run_postgres_migrations", lambda: calls.append("migrations"))
    monkeypatch.setattr(connection, "_bootstrap_default_admin", lambda: calls.append("default_admin"))
    monkeypatch.setattr(connection, "_run_runtime_seeders", lambda: calls.append("runtime_seeders"))
    return calls


def test_default_mode_preserves_full_postgres_startup_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VKPI_DB_STARTUP_MODE", raising=False)
    calls = _postgres_startup_spies(monkeypatch)

    asyncio.run(connection.init_db_runtime())

    assert calls == ["pool", "migrations", "default_admin", "runtime_seeders"]
    status = connection.get_db_startup_status()
    assert {
        key: status[key]
        for key in (
            "mode",
            "backend",
            "state",
            "schema_migrations",
            "default_admin_bootstrap",
            "runtime_seeders",
            "non_migration_startup_writes",
            "failed_stage",
            "error_type",
        )
    } == {
        "mode": "full",
        "backend": "postgres",
        "state": "completed",
        "schema_migrations": "completed",
        "default_admin_bootstrap": "completed",
        "runtime_seeders": "completed",
        "non_migration_startup_writes": "executed",
        "failed_stage": None,
        "error_type": None,
    }
    assert status["started_at"]
    assert status["completed_at"]


def test_migrations_only_mode_skips_only_non_migration_startup_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_DB_STARTUP_MODE", "migrations-only")
    monkeypatch.setattr(connection, "APP_ROLE", "migration-runner")
    calls = _postgres_startup_spies(monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr(
        connection.logger,
        "warning",
        lambda message, *args, **kwargs: warnings.append(message % args if args else message),
    )

    asyncio.run(connection.init_db_runtime())

    assert calls == ["pool", "migrations"]
    status = connection.get_db_startup_status()
    assert status["mode"] == "migrations-only"
    assert status["state"] == "completed"
    assert status["schema_migrations"] == "completed"
    assert status["default_admin_bootstrap"] == "skipped_explicitly"
    assert status["runtime_seeders"] == "skipped_explicitly"
    assert status["non_migration_startup_writes"] == "skipped_explicitly"
    assert status["failed_stage"] is None
    assert any("db.startup.non_migration_writes_skipped" in line for line in warnings)


def test_unknown_mode_fails_before_pool_or_any_database_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_DB_STARTUP_MODE", "migration-only-typo")
    calls = _postgres_startup_spies(monkeypatch)

    with pytest.raises(RuntimeError, match="Unsupported VKPI_DB_STARTUP_MODE"):
        asyncio.run(connection.init_db_runtime())

    assert calls == []


def test_default_mode_preserves_sqlite_bootstrap_and_seeder_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VKPI_DB_STARTUP_MODE", raising=False)
    monkeypatch.setattr(connection, "is_postgres_runtime", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(connection, "_bootstrap_sqlite_runtime", lambda: calls.append("sqlite_bootstrap"))
    monkeypatch.setattr(connection, "_run_runtime_seeders", lambda: calls.append("runtime_seeders"))

    asyncio.run(connection.init_db_runtime())

    assert calls == ["sqlite_bootstrap", "runtime_seeders"]
    status = connection.get_db_startup_status()
    assert status["mode"] == "full"
    assert status["state"] == "completed"
    assert status["default_admin_bootstrap"] == "included_in_sqlite_bootstrap"
    assert status["non_migration_startup_writes"] == "executed"


def test_migrations_only_rejects_sqlite_before_its_mixed_bootstrap_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_DB_STARTUP_MODE", "migrations-only")
    monkeypatch.setattr(connection, "APP_ROLE", "migration-runner")
    monkeypatch.setattr(connection, "is_postgres_runtime", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(connection, "_bootstrap_sqlite_runtime", lambda: calls.append("sqlite_bootstrap"))
    monkeypatch.setattr(connection, "_run_runtime_seeders", lambda: calls.append("runtime_seeders"))

    with pytest.raises(RuntimeError, match="SQLite schema bootstrap"):
        asyncio.run(connection.init_db_runtime())

    assert calls == []
    status = connection.get_db_startup_status()
    assert status["state"] == "failed"
    assert status["failed_stage"] == "mode_validation"
    assert status["schema_migrations"] == "not_started"


def test_migrations_only_rejects_non_runner_role_before_pool_or_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_DB_STARTUP_MODE", "migrations-only")
    monkeypatch.setattr(connection, "APP_ROLE", "admin-web")
    calls = _postgres_startup_spies(monkeypatch)

    with pytest.raises(RuntimeError, match="requires APP_ROLE='migration-runner'"):
        asyncio.run(connection.init_db_runtime())

    assert calls == []


def test_migration_runner_rejects_full_startup_before_pool_or_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_DB_STARTUP_MODE", "full")
    monkeypatch.setattr(connection, "APP_ROLE", "migration-runner")
    calls = _postgres_startup_spies(monkeypatch)

    with pytest.raises(RuntimeError, match="requires VKPI_DB_STARTUP_MODE='migrations-only'"):
        asyncio.run(connection.init_db_runtime())

    assert calls == []


def test_startup_status_is_exposed_in_health_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {
        "mode": "migrations-only",
        "state": "completed",
        "schema_migrations": "completed",
        "non_migration_startup_writes": "skipped_explicitly",
    }
    monkeypatch.setattr(main, "get_db_startup_status", lambda: expected)
    monkeypatch.setattr(main, "_trust_db_migration_max", lambda: "244_test.sql")
    monkeypatch.setattr(main, "_trust_worker", lambda: {"worker_heartbeat": None, "worker_online": None})
    monkeypatch.setattr(main, "_trust_scheduler", lambda: "not_configured")
    monkeypatch.setattr(
        main,
        "_trust_worker_sha",
        lambda: {"worker_sha": "test", "worker_sha_source": "test"},
    )

    trust = main._runtime_trust()

    assert trust["db_startup"] == expected
    assert trust["db_migration_max"] == "244_test.sql"
