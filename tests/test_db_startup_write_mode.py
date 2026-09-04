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

from app.db import connection, startup  # noqa: E402
import app.main as main  # noqa: E402


def _postgres_startup_spies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(startup, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(startup, "_get_pg_pool", lambda: calls.append("pool"))
    monkeypatch.setattr(startup, "_run_postgres_migrations", lambda: calls.append("migrations"))
    monkeypatch.setattr(startup, "_bootstrap_default_admin", lambda: calls.append("default_admin"))
    monkeypatch.setattr(startup, "_run_runtime_seeders", lambda: calls.append("runtime_seeders"))
    return calls


def test_default_mode_preserves_full_postgres_startup_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VKPI_DB_STARTUP_MODE", raising=False)
    calls = _postgres_startup_spies(monkeypatch)

    asyncio.run(startup.init_db_runtime())

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
        startup.logger,
        "warning",
        lambda message, *args, **kwargs: warnings.append(message % args if args else message),
    )

    asyncio.run(startup.init_db_runtime())

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


def test_release_validation_startup_skips_bootstrap_and_seed_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VKPI_DB_STARTUP_MODE", raising=False)
    calls = _postgres_startup_spies(monkeypatch)

    asyncio.run(startup.init_db_runtime(skip_non_migration_writes=True))

    assert calls == ["pool", "migrations"]
    status = connection.get_db_startup_status()
    assert status["mode"] == "full"
    assert status["state"] == "completed"
    assert status["schema_migrations"] == "completed"
    assert status["default_admin_bootstrap"] == "skipped_release_validation"
    assert status["runtime_seeders"] == "skipped_release_validation"
    assert status["non_migration_startup_writes"] == "skipped_release_validation"


def test_release_validation_startup_rejects_mixed_sqlite_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VKPI_DB_STARTUP_MODE", raising=False)
    monkeypatch.setattr(startup, "is_postgres_runtime", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(
        startup,
        "_bootstrap_sqlite_runtime",
        lambda: calls.append("sqlite_bootstrap"),
    )
    monkeypatch.setattr(
        startup,
        "_run_runtime_seeders",
        lambda: calls.append("runtime_seeders"),
    )

    with pytest.raises(RuntimeError, match="Release-validation startup requires Postgres"):
        asyncio.run(startup.init_db_runtime(skip_non_migration_writes=True))

    assert calls == []
    status = connection.get_db_startup_status()
    assert status["state"] == "failed"
    assert status["failed_stage"] == "mode_validation"


def test_unknown_mode_fails_before_pool_or_any_database_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_DB_STARTUP_MODE", "migration-only-typo")
    calls = _postgres_startup_spies(monkeypatch)

    with pytest.raises(RuntimeError, match="Unsupported VKPI_DB_STARTUP_MODE"):
        asyncio.run(startup.init_db_runtime())

    assert calls == []


def test_default_mode_preserves_sqlite_bootstrap_and_seeder_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VKPI_DB_STARTUP_MODE", raising=False)
    monkeypatch.setattr(startup, "is_postgres_runtime", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(startup, "_bootstrap_sqlite_runtime", lambda: calls.append("sqlite_bootstrap"))
    monkeypatch.setattr(startup, "_run_runtime_seeders", lambda: calls.append("runtime_seeders"))

    asyncio.run(startup.init_db_runtime())

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
    monkeypatch.setattr(startup, "is_postgres_runtime", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(startup, "_bootstrap_sqlite_runtime", lambda: calls.append("sqlite_bootstrap"))
    monkeypatch.setattr(startup, "_run_runtime_seeders", lambda: calls.append("runtime_seeders"))

    with pytest.raises(RuntimeError, match="SQLite schema bootstrap"):
        asyncio.run(startup.init_db_runtime())

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
        asyncio.run(startup.init_db_runtime())

    assert calls == []


def test_migration_runner_rejects_full_startup_before_pool_or_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_DB_STARTUP_MODE", "full")
    monkeypatch.setattr(connection, "APP_ROLE", "migration-runner")
    calls = _postgres_startup_spies(monkeypatch)

    with pytest.raises(RuntimeError, match="requires VKPI_DB_STARTUP_MODE='migrations-only'"):
        asyncio.run(startup.init_db_runtime())

    assert calls == []


def test_startup_status_is_exposed_in_health_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {
        "mode": "migrations-only",
        "state": "completed",
        "schema_migrations": "completed",
        "non_migration_startup_writes": "skipped_explicitly",
    }
    monkeypatch.setattr(main, "get_db_startup_status", lambda: expected)
    monkeypatch.setattr(
        main,
        "_trust_db_migration_max",
        lambda: {
            "max": "244_test.sql",
            "set_complete": True,
            "set_exact": True,
            "applied_count": 244,
            "expected_count": 244,
            "missing_count": 0,
            "unexpected_count": 0,
            "set_sha256": "a" * 64,
        },
    )
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
    assert trust["db_migration_complete"] is True
    assert trust["db_migration_missing_count"] == 0


def test_health_migration_identity_detects_a_hole_below_the_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = "309_vkpi_dsar_public_intake.sql"
    applied = [
        {"version_key": name}
        for name in connection._POSTGRES_MIGRATION_SEQUENCE
        if name != missing
    ]

    class Result:
        def fetchall(self):
            return applied

    class Conn:
        def execute(self, _sql: str):
            return Result()

    monkeypatch.setattr(connection, "get_conn", lambda: Conn())

    identity = main._trust_db_migration_max()

    assert identity is not None
    assert identity["max"] == "310_vkpi_kol_search_refresh_scheduler.sql"
    assert identity["set_complete"] is False
    assert identity["set_exact"] is False
    assert identity["missing_count"] == 1
    assert identity["unexpected_count"] == 0


def test_health_migration_identity_allows_a_forward_compatible_superset_on_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied = [
        {"version_key": name} for name in connection._POSTGRES_MIGRATION_SEQUENCE
    ] + [{"version_key": "311_future_forward_compatible.sql"}]

    class Result:
        def fetchall(self):
            return applied

    class Conn:
        def execute(self, _sql: str):
            return Result()

    monkeypatch.setattr(connection, "get_conn", lambda: Conn())

    identity = main._trust_db_migration_max()

    assert identity is not None
    assert identity["set_complete"] is True
    assert identity["set_exact"] is False
    assert identity["missing_count"] == 0
    assert identity["unexpected_count"] == 1
