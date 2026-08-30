from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "scripts" / "start_redis_local.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_redis_start_timeout_reaps_a_stuck_bootstrap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    binaries = repo / "bin"
    data = repo / "runtime" / "data" / "redis"
    logs = repo / "runtime" / "logs"
    scripts.mkdir(parents=True)
    binaries.mkdir()
    data.mkdir(parents=True)
    logs.mkdir(parents=True)
    shutil.copy2(START, scripts / START.name)

    pid_record = repo / "bootstrap.pid"
    _write_executable(
        scripts / "runtime_env.sh",
        "\n".join(
            (
                "#!/usr/bin/env bash",
                f'export REDIS_BIN_DIR="{binaries}"',
                'export REDIS_HOST="127.0.0.1"',
                'export REDIS_PORT="6399"',
                f'export REDIS_DATA_DIR="{data}"',
                f'export REDIS_LOG_FILE="{logs / "redis.log"}"',
                f'export REDIS_PID_FILE="{data / "redis.pid"}"',
                f'export REDIS_CONF_FILE="{data / "redis.conf"}"',
                'export LOCAL_REDIS_URL="redis://127.0.0.1:6399/0"',
            )
        )
        + "\n",
    )
    _write_executable(
        binaries / "redis-server",
        f'#!/usr/bin/env bash\nprintf "%s" "$$" > "{pid_record}"\nexec sleep 60\n',
    )
    _write_executable(binaries / "redis-cli", "#!/usr/bin/env bash\nexit 1\n")

    started = time.monotonic()
    result = subprocess.run(
        ["bash", str(scripts / START.name)],
        cwd=repo,
        env={**os.environ, "VKPI_REDIS_START_TIMEOUT_SECONDS": "1"},
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 1
    assert "failed to become ready within 1s" in result.stderr
    assert elapsed < 4
    bootstrap_pid = int(pid_record.read_text(encoding="utf-8"))
    probe = subprocess.run(
        ["/bin/ps", "-p", str(bootstrap_pid), "-o", "pid="],
        text=True,
        capture_output=True,
        check=False,
    )
    assert not probe.stdout.strip()


def test_redis_start_timeout_is_bounded_and_supervisor_recovery_is_opt_in() -> None:
    start_source = START.read_text(encoding="utf-8")
    supervisor = (ROOT / "scripts" / "ops" / "local_stack_supervisor.sh").read_text(
        encoding="utf-8"
    )

    assert "VKPI_REDIS_START_TIMEOUT_SECONDS:-15" in start_source
    assert '"$REDIS_START_TIMEOUT_SECONDS" -gt 120' in start_source
    assert 'kill "$redis_bootstrap_pid"' in start_source
    assert "VKPI_SUPERVISOR_REDIS_AUTORECOVER:-0" in supervisor
