from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_default_pytest_runtime_is_hermetic_and_uses_temp_sqlite() -> None:
    from app.core import config
    from app.db import connection

    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert os.environ.get("VKPI_PYTEST_HERMETIC") == "1"
    assert os.environ.get("VKPI_SKIP_DOTENV") == "1"
    assert config.ENVIRONMENT == "test"
    assert config.DB_RUNTIME_BACKEND == "sqlite"
    assert config.DB_RUNTIME_URL == ""
    assert config.REDIS_URL == ""
    assert config.DB_PATH != repository_db
    assert connection.DB_PATH != repository_db
    assert connection.is_postgres_runtime() is False

    conn = connection.get_conn()
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        main_path = Path(str(rows[0][2])).resolve()
        assert main_path == config.DB_PATH
        assert main_path != repository_db
    finally:
        connection.close_db_runtime_sync()


def test_hermetic_runtime_refuses_postgres_pool_even_if_called_directly() -> None:
    from app.db import connection

    with pytest.raises(RuntimeError, match="forbids PostgreSQL"):
        connection._get_pg_pool()


def test_live_service_opt_in_never_changes_application_runtime() -> None:
    """Opt-in is fixture authority, never authority for app config/get_conn."""
    from app.core import config
    from app.db import connection

    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert os.environ.get("VKPI_PYTEST_HERMETIC") == "1"
    assert config.DB_RUNTIME_BACKEND == "sqlite"
    assert config.DB_RUNTIME_URL == ""
    assert config.REDIS_URL == ""
    assert config.DB_PATH != repository_db
    assert connection.is_postgres_runtime() is False
    with pytest.raises(RuntimeError, match="forbids PostgreSQL"):
        connection._get_pg_pool()


def test_pg_fixture_dsn_is_separate_from_application_runtime() -> None:
    """When opted in, the fixture may see only the pre-sandbox DSN."""
    import conftest

    if conftest._LIVE_SERVICE_OPT_IN:
        resolved = conftest._read_env_dsn()
        if conftest._CAPTURED_LIVE_DSN:
            assert resolved == conftest._CAPTURED_LIVE_DSN
        else:
            assert conftest._ENV_PATH.exists() or resolved == ""
        assert os.environ.get("DATABASE_URL") == ""
        assert os.environ.get("LOCAL_DATABASE_URL") == ""
    else:
        assert conftest._read_env_dsn() == ""
