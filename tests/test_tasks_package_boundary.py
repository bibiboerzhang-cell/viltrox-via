from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tasks_package_import_is_inert_in_fresh_process() -> None:
    script = """
import sys
import app.domains.tasks as tasks
assert 'app.domains.tasks.enqueue' not in sys.modules
assert 'enqueue' not in vars(tasks)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_direct_enqueue_adapter_import_remains_supported() -> None:
    import app.domains.tasks.enqueue as task_enqueue

    assert callable(task_enqueue.enqueue_vkpi_task)
