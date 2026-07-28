from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "scripts" / "start_apify_worker_pool.sh"
STOP = ROOT / "scripts" / "stop_apify_worker_pool.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _pool_sandbox(tmp_path: Path, script: Path) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    runtime = root / "runtime"
    bin_dir = root / "test-bin"
    scripts.mkdir(parents=True)
    (runtime / "logs").mkdir(parents=True)
    bin_dir.mkdir()
    target = scripts / script.name
    shutil.copy2(script, target)
    _write_executable(
        scripts / "runtime_env.sh",
        "#!/usr/bin/env bash\nexport DATABASE_URL='postgresql://stub/test'\n",
    )
    calls = root / "calls.log"
    env = dict(os.environ)
    env.pop("APIFY_WORKER_POOL_BULK_COUNT", None)
    env.pop("APIFY_WORKER_BURST_TIER", None)
    env["VKPI_TEST_CALLS"] = str(calls)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return target, calls, env


def test_pool_scripts_default_to_fifteen_bulk_lanes_and_reject_sixteen() -> None:
    start = START.read_text(encoding="utf-8")
    stop = STOP.read_text(encoding="utf-8")

    assert 'APIFY_WORKER_POOL_BULK_COUNT:-15' in start
    assert 'APIFY_WORKER_POOL_BULK_COUNT:-15' in stop
    assert "1..15" in start
    assert "^([1-9]|1[0-5])$" in start
    assert "^([1-9]|1[0-5])$" in stop


def test_start_pool_default_launches_one_interactive_then_fifteen_bulk(
    tmp_path: Path,
) -> None:
    script, calls, env = _pool_sandbox(tmp_path, START)
    scripts = script.parent
    _write_executable(
        scripts / "start_worker_lane.sh",
        '#!/usr/bin/env bash\nprintf "start:%s\\n" "$1" >> "$VKPI_TEST_CALLS"\n',
    )
    _write_executable(
        scripts.parent / "test-bin" / "sleep",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    python_stub = scripts.parent / "test-bin" / "python-stub"
    _write_executable(
        python_stub,
        "#!/usr/bin/env bash\nexit 0\n",
    )
    env["PYTHON_BIN"] = str(python_stub)

    result = subprocess.run(
        ["bash", str(script)],
        cwd=script.parent.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "start:interactive",
        *(f"start:bulk{index}" for index in range(1, 16)),
    ]
    assert "apify worker pool ready: interactive bulk1" in result.stdout
    assert "bulk15 (burst tier 1)" in result.stdout


def test_stop_pool_default_covers_fifteen_bulk_then_interactive(
    tmp_path: Path,
) -> None:
    script, calls, env = _pool_sandbox(tmp_path, STOP)
    root = script.parent.parent
    _write_executable(
        script.parent / "stop_worker.sh",
        (
            "#!/usr/bin/env bash\n"
            'printf "stop:%s\\n" "$(basename "$PIDFILE" .pid)" '
            '>> "$VKPI_TEST_CALLS"\n'
        ),
    )
    _write_executable(
        root / "test-bin" / "psql",
        "#!/usr/bin/env bash\nprintf '0\\n'\n",
    )
    for index, lane in enumerate(
        ["interactive", *(f"bulk{number}" for number in range(1, 16))],
        start=1,
    ):
        (root / "runtime" / f"worker-{lane}.pid").write_text(
            str(1000 + index),
            encoding="utf-8",
        )

    result = subprocess.run(
        ["bash", str(script)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        *(f"stop:worker-bulk{index}" for index in range(15, 0, -1)),
        "stop:worker-interactive",
    ]
    assert result.stdout.rstrip().endswith("apify worker pool stopped")


@pytest.mark.parametrize("script", [START, STOP], ids=["start", "stop"])
def test_pool_scripts_fail_closed_above_fifteen(
    tmp_path: Path,
    script: Path,
) -> None:
    sandboxed, _calls, env = _pool_sandbox(tmp_path, script)
    env["APIFY_WORKER_POOL_BULK_COUNT"] = "16"

    result = subprocess.run(
        ["bash", str(sandboxed)],
        cwd=sandboxed.parent.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
