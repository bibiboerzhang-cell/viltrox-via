from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _local_admin_capture_python(tmp_path: Path) -> Path:
    helper = ROOT / "scripts/ops/derive_local_pgbouncer_url.py"
    fake_python = tmp_path / "capture-local-pgbouncer.sh"
    fake_python.write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = '-B' ] && [ \"$2\" = '{helper}' ]; then\n"
        f"  exec '{sys.executable}' \"$@\"\n"
        "fi\n"
        f"exec '{sys.executable}' - <<'PY'\n"
        "import os\n"
        "from urllib.parse import urlsplit\n"
        "direct = urlsplit(os.environ.get('DATABASE_URL', ''))\n"
        "pool = urlsplit(os.environ.get('DATABASE_POOL_URL', ''))\n"
        "facts = [\n"
        "    os.environ.get('DB_USE_PGBOUNCER', ''),\n"
        "    str(pool.hostname or ''),\n"
        "    str(pool.port or ''),\n"
        "    pool.path.lstrip('/'),\n"
        "    str(pool.username == direct.username and pool.password == direct.password),\n"
        "    str(direct.port or ''),\n"
        "    str('VKPI_LOCAL_WEB_PGBOUNCER' in os.environ),\n"
        "]\n"
        "print('|'.join(facts))\n"
        "PY\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o700)
    return fake_python


def _isolated_start_admin_env(tmp_path: Path, fake_python: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "DATABASE_POOL_URL",
        "DB_USE_PGBOUNCER",
        "ENV_FILE",
        "VKPI_SYSTEMD_ADMIN_WEB_CONTRACT",
    ):
        env.pop(name, None)
    env.update(
        {
            "ADMIN_DAEMON": "0",
            "ENVIRONMENT": "local",
            "LOCAL_DATABASE_URL": "postgresql://local_user:do-not-print@localhost:54329/viltrox2?application_name=vkpi",
            "LOCAL_ENV_FILE": str(tmp_path / "missing.env"),
            "LOCAL_REDIS_URL": "redis://127.0.0.1:6380/0",
            "LOCAL_RUNTIME_FORCE_STACK": "1",
            "PYTHON_BIN": str(fake_python),
            "RUNTIME_ENV_QUIET": "1",
        }
    )
    return env


def test_local_admin_explicitly_routes_web_database_through_pgbouncer(tmp_path: Path) -> None:
    fake_python = _local_admin_capture_python(tmp_path)
    env = _isolated_start_admin_env(tmp_path, fake_python)
    env["VKPI_LOCAL_WEB_PGBOUNCER"] = "1"

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1|127.0.0.1|6432|viltrox2|True|54329|True"
    assert "do-not-print" not in result.stdout
    assert "do-not-print" not in result.stderr
    assert "postgresql://" not in result.stderr


def test_local_admin_pgbouncer_default_remains_direct(tmp_path: Path) -> None:
    fake_python = _local_admin_capture_python(tmp_path)
    env = _isolated_start_admin_env(tmp_path, fake_python)

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "||||False|54329|False"


def test_local_admin_adopts_an_existing_valid_pool_url(tmp_path: Path) -> None:
    fake_python = _local_admin_capture_python(tmp_path)
    env = _isolated_start_admin_env(tmp_path, fake_python)
    env.update(
        {
            "DATABASE_POOL_URL": "postgresql://pool_user:pool-secret@127.0.0.1:6432/viltrox2?application_name=vkpi",
            "VKPI_LOCAL_WEB_PGBOUNCER": "1",
        }
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1|127.0.0.1|6432|viltrox2|False|54329|True"
    assert "pool-secret" not in result.stdout
    assert "pool-secret" not in result.stderr


def test_local_pgbouncer_switch_cannot_override_systemd_production_pool(tmp_path: Path) -> None:
    fake_python = _local_admin_capture_python(tmp_path)
    env = _isolated_start_admin_env(tmp_path, fake_python)
    env.update(
        {
            "ADMIN_PASSWORD": "synthetic-systemd-production-admin-password",
            "DATABASE_POOL_URL": "postgresql://pool_user:pool-secret@127.0.0.1:6432/vkpi",
            "DATABASE_URL": "postgresql://cloud_user:db-secret@db.internal:5432/vkpi",
            "DB_USE_PGBOUNCER": "1",
            "ENVIRONMENT": "production",
            "JWT_SECRET": "synthetic-systemd-production-jwt-secret",
            "REDIS_URL": "redis://:redis-secret@redis.internal:6379/0",
            "VKPI_LOCAL_WEB_PGBOUNCER": "1",
            "VKPI_SYSTEMD_ADMIN_WEB_CONTRACT": "1",
        }
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1|127.0.0.1|6432|vkpi|False|5432|False"
    for sensitive in ("pool-secret", "db-secret", "redis-secret", "postgresql://", "redis://"):
        assert sensitive not in result.stderr


def test_local_pgbouncer_invalid_source_fails_without_leaking_credentials(tmp_path: Path) -> None:
    fake_python = _local_admin_capture_python(tmp_path)
    env = _isolated_start_admin_env(tmp_path, fake_python)
    env.update(
        {
            "LOCAL_DATABASE_URL": "postgresql://local_user:never-print@db.internal:54329/viltrox2",
            "VKPI_LOCAL_WEB_PGBOUNCER": "1",
        }
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "local PgBouncer configuration is invalid" in result.stderr
    assert "never-print" not in result.stdout
    assert "never-print" not in result.stderr
    assert "db.internal" not in result.stderr


def test_local_pgbouncer_rejects_a_different_database_identity_without_leakage(tmp_path: Path) -> None:
    fake_python = _local_admin_capture_python(tmp_path)
    env = _isolated_start_admin_env(tmp_path, fake_python)
    env.update(
        {
            "DATABASE_POOL_URL": "postgresql://pool_user:identity-secret@127.0.0.1:6432/other_database",
            "VKPI_LOCAL_WEB_PGBOUNCER": "1",
        }
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "local PgBouncer configuration is invalid" in result.stderr
    assert "identity-secret" not in result.stdout
    assert "identity-secret" not in result.stderr
    assert "other_database" not in result.stderr
