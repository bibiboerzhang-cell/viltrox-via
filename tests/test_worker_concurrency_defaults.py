"""Hermetic startup configuration checks: no business DB or worker starts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.workers import redis_worker_runtime


def test_clean_environment_defaults_pass_the_same_runtime_guard(tmp_path) -> None:
    env = dict(os.environ)
    for key in ("WORKER_ASYNC_CONSUMERS", "WORKER_SERVICE_PROCESSES", "VKPI_REDIS_WORKER_MAX_CONSUMERS"):
        env.pop(key, None)
    env.update({
        "VKPI_SKIP_DOTENV": "1", "ENVIRONMENT": "test", "APP_ROLE": "admin-web",
        "VKPI_DB_STARTUP_MODE": "full", "VKPI_RUNTIME_DATA_DIR": str(tmp_path / "data"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    code = (
        "import json; from app.core import config; "
        "from app.workers.redis_worker_runtime import redis_worker_concurrency; "
        "print(json.dumps({'configured':config.WORKER_ASYNC_CONSUMERS,"
        "'admitted':redis_worker_concurrency(config.WORKER_ASYNC_CONSUMERS),"
        "'fleet':config.WORKER_CONFIGURED_CONCURRENCY}))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], env=env, cwd=tmp_path,
        capture_output=True, text=True, check=True, timeout=15,
    )
    assert json.loads(result.stdout.splitlines()[-1]) == {"configured": 2, "admitted": 2, "fleet": 4}


@pytest.mark.parametrize("override", [None, "invalid", "2"])
def test_default_or_invalid_hard_max_never_expands_admission(monkeypatch, override) -> None:
    if override is None:
        monkeypatch.delenv("VKPI_REDIS_WORKER_MAX_CONSUMERS", raising=False)
    else:
        monkeypatch.setenv("VKPI_REDIS_WORKER_MAX_CONSUMERS", override)
    assert redis_worker_runtime.redis_worker_concurrency(2) == 2
    with pytest.raises(RuntimeError, match="exceeds reviewed hard max 2"):
        redis_worker_runtime.redis_worker_concurrency(3)


def test_explicit_reviewed_expansion_keeps_absolute_ceiling(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_REDIS_WORKER_MAX_CONSUMERS", "100")
    assert redis_worker_runtime.redis_worker_concurrency(4) == 4
    with pytest.raises(RuntimeError, match="exceeds reviewed hard max 4"):
        redis_worker_runtime.redis_worker_concurrency(5)
