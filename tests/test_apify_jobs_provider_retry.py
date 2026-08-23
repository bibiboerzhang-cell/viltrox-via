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

    def test_final_v1_model_chain_defaults_to_primary_then_lite(self) -> None:
        # 优化波 B·C4:默认链 = 主力 → 裁判同款 lite(core/gemini_models 字面契约);
        # 只在提供方压力时换节;worker 仍通过 payload 钉单精确模型(见下)。
        self.assertEqual(gemini_video.final_v1_gemini_models(""), ["gemini-3.6-flash", "gemini-3.5-flash-lite"])
        self.assertEqual(worker.FINAL_V1_GEMINI_MODELS, ["gemini-3.6-flash"])

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

    def test_redacts_url_userinfo_in_worker_errors(self) -> None:
        raw = (
            "yt-dlp --proxy http://sp7y5uz7ho:AJmCw2Secret@gate.decodo.com:10001 "
            "https://www.youtube.com/watch?v=abc"
        )

        redacted = worker._redact_sensitive_text(raw)

        self.assertIn("http://***@gate.decodo.com:10001", redacted)
        self.assertNotIn("sp7y5uz7ho", redacted)
        self.assertNotIn("AJmCw2Secret", redacted)
        self.assertIn("yt-dlp", redacted)

    def test_redacts_key_value_and_authorization_tokens(self) -> None:
        raw = (
            "proxy=http://user:pass@proxy.local token=tok123 API_KEY=api-secret "
            "client_secret=client-secret authorization=Bearer abc.def.ghi Bearer xyz.123"
        )

        redacted = worker._redact_sensitive_text(raw)

        self.assertIn("proxy=***", redacted)
        self.assertIn("token=***", redacted)
        self.assertIn("API_KEY=***", redacted)
        self.assertIn("client_secret=***", redacted)
        self.assertIn("authorization=***", redacted)
        self.assertIn("Bearer ***", redacted)
        self.assertNotIn("user:pass", redacted)
        self.assertNotIn("tok123", redacted)
        self.assertNotIn("api-secret", redacted)
        self.assertNotIn("client-secret", redacted)
        self.assertNotIn("abc.def.ghi", redacted)
        self.assertNotIn("xyz.123", redacted)


if __name__ == "__main__":
    unittest.main()
