from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.ai.retry import call_ai_with_retry  # noqa: E402


class AiRetryTests(unittest.TestCase):
    def test_retries_transient_failure(self) -> None:
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("temporary")
            return "ok"

        with patch("app.services.ai.retry.time.sleep", return_value=None):
            self.assertEqual(call_ai_with_retry("unit.flaky", flaky, base_delay_sec=0), "ok")
        self.assertEqual(calls["n"], 2)

    def test_raises_after_attempts_exhausted(self) -> None:
        with patch("app.services.ai.retry.time.sleep", return_value=None):
            with self.assertRaises(RuntimeError):
                call_ai_with_retry("unit.fail", lambda: (_ for _ in ()).throw(RuntimeError("boom")), attempts=2, base_delay_sec=0)

    def test_attempt_aware_callback_receives_exact_progress(self) -> None:
        seen = []

        def attempt_fn(attempt, total):
            seen.append((attempt, total))
            if attempt < total:
                raise RuntimeError("retry")
            return "ok"

        self.assertEqual(
            call_ai_with_retry(
                "unit.progress",
                lambda: "legacy-should-not-run",
                attempts=3,
                base_delay_sec=0,
                attempt_fn=attempt_fn,
            ),
            "ok",
        )
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])


if __name__ == "__main__":
    unittest.main()
