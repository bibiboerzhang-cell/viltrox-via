from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts import vkpi_engineering_health_graph as graph_tools  # noqa: E402


RING_MODULE_PATHS = {
    "app.db.connection": "backend/app/db/connection.py",
    "app.db.migrations": "backend/app/db/migrations.py",
    "app.db.migrations_activities": "backend/app/db/migrations_activities.py",
    "app.db.migrations_v5": "backend/app/db/migrations_v5.py",
    "app.db.repositories.student_identity": "backend/app/db/repositories/student_identity.py",
    "app.db.repositories.users": "backend/app/db/repositories/users.py",
    "app.services.runtime_seed": "backend/app/services/runtime_seed.py",
    "app.services.student_identity_defaults": "backend/app/services/student_identity_defaults.py",
    "app.services.trust": "backend/app/services/trust.py",
}
LEGACY_CONNECTION_OUTGOING = {
    "app.db.migrations",
    "app.db.repositories.users",
    "app.services.runtime_seed",
}
DIRECT_CALLER_PATHS = (
    "backend/app/main.py",
    "scripts/ops/staging_db_clone.py",
    "scripts/smoke_auth_social_student.py",
    "scripts/smoke_upload_audit_video_factory.py",
    "scripts/smoke_via_runtime.py",
)


def _ring_graph() -> dict[str, set[str]]:
    trees = {
        path: ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
        for path in RING_MODULE_PATHS.values()
    }
    return graph_tools.build_backend_import_graph(trees).graph


def test_actual_graph_removes_connection_orchestration_edges_and_counterfactual_recreates_ring() -> None:
    graph = _ring_graph()
    ring = set(RING_MODULE_PATHS)

    assert not (graph["app.db.connection"] & LEGACY_CONNECTION_OUTGOING)
    actual_cycles = {
        frozenset(component)
        for component in graph_tools.strongly_connected_components(graph)
        if graph_tools.is_cycle(component, graph)
    }
    assert frozenset(ring) not in actual_cycles

    counterfactual = {module: set(targets) for module, targets in graph.items()}
    counterfactual["app.db.connection"].update(LEGACY_CONNECTION_OUTGOING)
    counterfactual_cycles = {
        frozenset(component)
        for component in graph_tools.strongly_connected_components(counterfactual)
        if graph_tools.is_cycle(component, counterfactual)
    }
    assert frozenset(ring) in counterfactual_cycles


def test_importing_startup_is_write_free_and_does_not_eagerly_load_ring_workers() -> None:
    code = """
import sys
import app.db.startup
forbidden = {
    'app.db.migrations',
    'app.db.repositories.users',
    'app.services.runtime_seed',
    'app.services.student_identity_defaults',
    'app.services.trust',
}
loaded = sorted(forbidden.intersection(sys.modules))
if loaded:
    raise AssertionError(loaded)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env.setdefault("JWT_SECRET", "test-secret")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_sqlite_migrations_has_no_implicit_connection_or_write() -> None:
    code = """
import importlib
from app.db import connection

calls = []
def forbidden_get_conn():
    calls.append('get_conn')
    raise AssertionError('migration import attempted a database write')

connection.get_conn = forbidden_get_conn
importlib.import_module('app.db.migrations')
if calls:
    raise AssertionError(calls)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env.setdefault("JWT_SECRET", "test-secret")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_all_init_db_runtime_callers_use_the_startup_coordinator() -> None:
    for relative_path in DIRECT_CALLER_PATHS:
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"), filename=relative_path)
        connection_imports: set[str] = set()
        startup_imports: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            names = {alias.name for alias in node.names}
            if node.module == "app.db.connection":
                connection_imports.update(names)
            elif node.module == "app.db.startup":
                startup_imports.update(names)
        assert "init_db_runtime" not in connection_imports, relative_path
        assert "init_db_runtime" in startup_imports, relative_path


def test_sqlite_bootstrap_calls_explicit_migrator_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import startup

    calls: list[str] = []
    fake_migrations = type("FakeMigrations", (), {"init_db": lambda self: calls.append("init_db")})()
    monkeypatch.setattr(startup, "_load_sqlite_migrations", lambda: fake_migrations)

    startup._bootstrap_sqlite_runtime()

    assert calls == ["init_db"]


def test_runtime_seeder_failure_is_reraised_after_thread_local_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import connection, startup

    class SeederFailure(RuntimeError):
        pass

    class FakeConnection:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    conn = FakeConnection()
    calls: list[str] = []

    def fail_seed() -> None:
        calls.append("runtime_seed")
        raise SeederFailure("seed failed")

    def forbidden_backfill() -> None:
        calls.append("backfill")

    monkeypatch.setattr(
        startup,
        "_load_runtime_seeders",
        lambda: (fail_seed, forbidden_backfill),
    )
    connection._db_local.conn = conn
    try:
        with pytest.raises(SeederFailure, match="seed failed"):
            startup._run_runtime_seeders()
    finally:
        connection._db_local.conn = None

    assert calls == ["runtime_seed"]
    assert conn.close_calls == 1


def test_startup_stage_failure_keeps_exception_type_and_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import connection, startup

    class MigrationFailure(RuntimeError):
        pass

    monkeypatch.delenv("VKPI_DB_STARTUP_MODE", raising=False)
    monkeypatch.setattr(startup, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(startup, "_get_pg_pool", lambda: object())
    monkeypatch.setattr(
        startup,
        "_run_postgres_migrations",
        lambda: (_ for _ in ()).throw(MigrationFailure("migration failed")),
    )
    monkeypatch.setattr(
        startup,
        "_bootstrap_default_admin",
        lambda: pytest.fail("default admin ran after migration failure"),
    )
    monkeypatch.setattr(
        startup,
        "_run_runtime_seeders",
        lambda: pytest.fail("runtime seeders ran after migration failure"),
    )

    with pytest.raises(MigrationFailure, match="migration failed"):
        import asyncio

        asyncio.run(startup.init_db_runtime())

    status = connection.get_db_startup_status()
    assert status["state"] == "failed"
    assert status["failed_stage"] == "schema_migrations"
    assert status["schema_migrations"] == "failed"
    assert status["error_type"] == "MigrationFailure"
