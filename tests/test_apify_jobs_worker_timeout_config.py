from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"


def _read_worker_timeout(env_value: str) -> int:
    env = os.environ.copy()
    env["APIFY_WORKER_GEMINI_CALL_TIMEOUT_SEC"] = env_value
    env["PYTHONPATH"] = f"{BACKEND_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.workers import apify_jobs_worker as w; print(w.GEMINI_CALL_TIMEOUT_SECONDS)",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return int(result.stdout.strip())


class ApifyJobsWorkerTimeoutConfigTests(unittest.TestCase):
    def test_gemini_timeout_reads_runtime_env(self) -> None:
        self.assertEqual(_read_worker_timeout("900"), 900)

    def test_gemini_timeout_has_floor(self) -> None:
        self.assertEqual(_read_worker_timeout("5"), 30)


if __name__ == "__main__":
    unittest.main()
