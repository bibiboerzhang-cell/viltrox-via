"""Explicit database startup orchestration.

The connection module owns low-level connection and pool primitives.  This
module owns the one-way startup sequence so those primitives never import the
migration, repository, or service layers that already depend on them.
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger
from app.db import connection as db_connection
from app.db.connection import (
    _bootstrap_default_admin,
    _get_pg_pool,
    _resolve_db_startup_mode,
    _reset_db_startup_status,
    _run_db_startup_stage,
    _run_postgres_migrations,
    _update_db_startup_status,
    _utc_timestamp,
    get_db_startup_status,
    is_postgres_runtime,
)


logger = get_logger(__name__)

_DB_STARTUP_MODE_ENV = "VKPI_DB_STARTUP_MODE"
_DB_STARTUP_MODE_MIGRATIONS_ONLY = "migrations-only"


def _load_sqlite_migrations() -> Any:
    from app.db import migrations as sqlite_migrations

    return sqlite_migrations


def _bootstrap_sqlite_runtime() -> None:
    _load_sqlite_migrations().init_db()


def _load_runtime_seeders() -> tuple[Callable[[], Any], Callable[[], Any]]:
    from app.db.repositories.users import backfill_user_social_verified_flags
    from app.services.runtime_seed import ensure_runtime_seed_data

    return ensure_runtime_seed_data, backfill_user_social_verified_flags


def _run_runtime_seeders() -> None:
    ensure_runtime_seed_data, backfill_user_social_verified_flags = _load_runtime_seeders()
    try:
        ensure_runtime_seed_data()
        backfill_user_social_verified_flags()
    finally:
        local_conn = getattr(db_connection._db_local, "conn", None)
        if local_conn is not None:
            try:
                local_conn.close()
            except Exception as exc:
                logger.debug("runtime seeder local connection close skipped: %s", exc)
            db_connection._db_local.conn = None


async def init_db_runtime(*, skip_non_migration_writes: bool = False) -> None:
    """Initialize schema and optional bootstrap data in the established order."""

    mode = _resolve_db_startup_mode()
    backend = "postgres" if is_postgres_runtime() else "sqlite"
    _reset_db_startup_status(mode=mode, backend=backend)
    logger.info("db.startup.mode_selected | mode=%s | backend=%s", mode, backend)

    if not is_postgres_runtime():
        if skip_non_migration_writes:
            _update_db_startup_status(
                state="failed",
                failed_stage="mode_validation",
                error_type="RuntimeError",
            )
            raise RuntimeError(
                "Release-validation startup requires Postgres because the SQLite "
                "schema bootstrap includes non-migration writes"
            )
        if mode == _DB_STARTUP_MODE_MIGRATIONS_ONLY:
            _update_db_startup_status(
                state="failed",
                failed_stage="mode_validation",
                error_type="RuntimeError",
            )
            raise RuntimeError(
                f"{_DB_STARTUP_MODE_ENV}={_DB_STARTUP_MODE_MIGRATIONS_ONLY!r} requires the Postgres runtime; "
                "the SQLite schema bootstrap also performs non-migration default-admin writes"
            )
        await _run_db_startup_stage("schema_migrations", _bootstrap_sqlite_runtime)
        _update_db_startup_status(default_admin_bootstrap="included_in_sqlite_bootstrap")
        await _run_db_startup_stage("runtime_seeders", _run_runtime_seeders)
        _update_db_startup_status(
            state="completed",
            non_migration_startup_writes="executed",
            completed_at=_utc_timestamp(),
        )
        return

    _get_pg_pool()
    await _run_db_startup_stage("schema_migrations", _run_postgres_migrations)

    if mode == _DB_STARTUP_MODE_MIGRATIONS_ONLY or skip_non_migration_writes:
        skip_status = (
            "skipped_release_validation"
            if skip_non_migration_writes
            else "skipped_explicitly"
        )
        _update_db_startup_status(
            state="completed",
            default_admin_bootstrap=skip_status,
            runtime_seeders=skip_status,
            non_migration_startup_writes=skip_status,
            completed_at=_utc_timestamp(),
        )
        logger.warning(
            "db.startup.non_migration_writes_skipped | mode=%s | release_validation=%s | "
            "default_admin_bootstrap=skipped | runtime_seeders=skipped",
            mode,
            bool(skip_non_migration_writes),
        )
        return

    await _run_db_startup_stage("default_admin_bootstrap", _bootstrap_default_admin)
    await _run_db_startup_stage("runtime_seeders", _run_runtime_seeders)
    _update_db_startup_status(
        state="completed",
        non_migration_startup_writes="executed",
        completed_at=_utc_timestamp(),
    )


async def start_db_actor() -> None:
    if is_postgres_runtime():
        await init_db_runtime()
        logger.info("Postgres pooled runtime initialized")
        return
    logger.info("Single-writer DB actor started (sqlite)")


__all__ = ["get_db_startup_status", "init_db_runtime", "start_db_actor"]
