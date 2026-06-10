from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.workers import apify_jobs_worker as worker  # noqa: E402
from app.services.ai.analyzers import gemini_video  # noqa: E402


class ApifyJobsProviderRetryTests(unittest.TestCase):
    def test_provider_pressure_errors_are_classified_retryable(self) -> None:
        samples = [
            "429 RESOURCE_EXHAUSTED quota exceeded",
            "RuntimeError: Gemini video analysis failed: 503 UNAVAILABLE high demand",
            str(gemini_video.ProviderPressureExhausted("provider_pressure(all models tried): 503 UNAVAILABLE")),
            "500 internal server error",
            "service unavailable: temporarily overloaded",
        ]

        for message in samples:
            with self.subTest(message=message):
                self.assertEqual(worker._error_category(message), "provider_pressure")

    def test_final_v1_model_chain_has_stable_fallback(self) -> None:
        self.assertEqual(
            gemini_video.final_v1_gemini_models(""),
            ["gemini-3-flash-preview", "gemini-2.5-flash"],
        )

    def test_permanent_media_errors_do_not_become_provider_pressure(self) -> None:
        cases = {
            "media_resolve_failed: no playable video": "media_resolve",
            "direct_video_download_failed: yt-dlp exited 1": "download",
            "invalid_video_url: not_video": "permanent",
            "gemini_call_timeout": "timeout",
        }

        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(worker._error_category(message), expected)

    def test_provider_retry_backoff_is_exponential_and_capped(self) -> None:
        with (
            patch.object(worker, "PROVIDER_RETRY_BASE_SECONDS", 60),
            patch.object(worker, "PROVIDER_RETRY_MAX_DELAY_SECONDS", 960),
            patch.object(worker, "PROVIDER_RETRY_JITTER_RATIO", 0),
        ):
            self.assertEqual(worker._provider_retry_delay_seconds(1), 60)
            self.assertEqual(worker._provider_retry_delay_seconds(2), 240)
            self.assertEqual(worker._provider_retry_delay_seconds(3), 960)
            self.assertEqual(worker._provider_retry_delay_seconds(4), 960)

    def test_provider_retry_jitter_never_exceeds_cap(self) -> None:
        with (
            patch.object(worker, "PROVIDER_RETRY_BASE_SECONDS", 60),
            patch.object(worker, "PROVIDER_RETRY_MAX_DELAY_SECONDS", 960),
            patch.object(worker, "PROVIDER_RETRY_JITTER_RATIO", 0.2),
            patch.object(worker.random, "uniform", return_value=9999),
        ):
            self.assertEqual(worker._provider_retry_delay_seconds(4), 960)


if __name__ == "__main__":
    unittest.main()
