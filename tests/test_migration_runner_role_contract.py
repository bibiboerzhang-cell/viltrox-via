from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _config_env(**overrides: str) -> dict[str, str]:
    env = {
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(BACKEND),
        "PYTHONDONTWRITEBYTECODE": "1",
        "VKPI_SKIP_DOTENV": "1",
        "ENVIRONMENT": "production",
        "V2_PRODUCTION_MODE": "1",
        "JWT_SECRET": "hermetic-migration-runner-secret",
        "APP_ROLE": "migration-runner",
        "VKPI_DB_STARTUP_MODE": "migrations-only",
        "DB_RUNTIME_BACKEND": "postgres",
        "DATABASE_URL": "postgresql://runner:secret@127.0.0.1:9/vkpi_release",
        "DATABASE_POOL_URL": "",
        "DB_USE_PGBOUNCER": "0",
        "REDIS_URL": "",
        "ENABLE_LOCAL_ORCHESTRATOR": "0",
        "ENABLE_BROWSER": "0",
        "ENABLE_SCHEDULER": "0",
        "ENABLE_UPLOAD_CLEANUP": "0",
    }
    env.update(overrides)
    return env


def _import_config(*, cwd: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    script = """
import json
from app.core import config
print(json.dumps({
    "app_role": config.APP_ROLE,
    "startup_mode": config.DB_STARTUP_MODE,
    "backend": config.DB_RUNTIME_BACKEND,
    "side_effects": [
        config.ENABLE_LOCAL_ORCHESTRATOR,
        config.ENABLE_BROWSER,
        config.ENABLE_SCHEDULER,
        config.ENABLE_UPLOAD_CLEANUP,
    ],
}))
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd,
        env=_config_env(**overrides),
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_migration_runner_import_is_read_only_and_side_effect_free(
    tmp_path: Path,
) -> None:
    release = tmp_path / "read-only-release"
    release.mkdir()
    release.chmod(0o555)
    try:
        result = _import_config(cwd=release)
    finally:
        release.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "app_role": "migration-runner",
        "startup_mode": "migrations-only",
        "backend": "postgres",
        "side_effects": [False, False, False, False],
    }
    assert not (release / "uploads").exists()
    assert not (release / "frames").exists()
    assert not (release / "creator_profiles").exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"VKPI_DB_STARTUP_MODE": "full"}, "requires VKPI_DB_STARTUP_MODE"),
        ({"APP_ROLE": "admin-web"}, "requires APP_ROLE='migration-runner'"),
        ({"DB_RUNTIME_BACKEND": "sqlite"}, "requires the PostgreSQL runtime"),
        ({"DATABASE_URL": ""}, "requires the PostgreSQL runtime"),
        ({"ENABLE_LOCAL_ORCHESTRATOR": "1"}, "forbids application side effects"),
        ({"ENABLE_BROWSER": "1"}, "forbids application side effects"),
        ({"ENABLE_SCHEDULER": "1"}, "forbids application side effects"),
        ({"ENABLE_UPLOAD_CLEANUP": "1"}, "forbids application side effects"),
    ],
)
def test_migration_runner_config_rejects_unsafe_role_intent_and_side_effects(
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    result = _import_config(cwd=tmp_path, **overrides)

    assert result.returncode != 0
    assert message in result.stderr
    assert not (tmp_path / "uploads").exists()
    assert not (tmp_path / "frames").exists()
    assert not (tmp_path / "creator_profiles").exists()


def test_migration_runner_cannot_enter_web_worker_or_scheduler_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as main
    from app.services.scheduler import jobs
    from app.workers import worker_main

    async def scenario() -> None:
        monkeypatch.setattr(main, "APP_ROLE", "migration-runner")
        with pytest.raises(RuntimeError, match="cannot serve web traffic"):
            async with main.lifespan(main.app):
                pass

        monkeypatch.setattr(worker_main, "APP_ROLE", "migration-runner")
        with pytest.raises(RuntimeError, match="cannot start the Redis worker"):
            await worker_main._worker_loop()

        monkeypatch.setattr(jobs, "APP_ROLE", "migration-runner")
        with pytest.raises(RuntimeError, match="cannot start scheduler or provider jobs"):
            await jobs.start_scheduler()

    asyncio.run(scenario())
