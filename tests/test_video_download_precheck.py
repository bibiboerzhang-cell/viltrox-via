"""Point 8 — download precheck.

A cheap HEAD probe sits in front of the real byte download. These tests prove:

1. A confidently-unavailable direct URL (404/410/403/401/451) is intercepted by
   the precheck, the real network download is NEVER attempted, and the surfaced
   reason routes through the worker's `_error_category` to the correct terminal
   bucket (content_unavailable / content_restricted / content_blocked).
2. Ambiguous probes (2xx, unexpected codes, transport errors, non-http urls)
   fall through to the real download — the precheck never kills a recoverable job.

All network is injected; nothing here touches the wire or the DB.
"""
from __future__ import annotations

import unittest
from unittest import mock

from app.services.media import video_download as vd
from app.services.media.video_download import (
    PRECHECK_PROCEED,
    PRECHECK_TERMINAL,
    PrecheckOutcome,
    download_direct_video_url,
    precheck_direct_video_url,
)
from app.workers.apify_jobs_worker_helpers import _error_category

DIRECT_URL = "https://cdn.example.com/v/abc123/playlist.mp4?sig=expired"


def fixed_head(code: int):
    """A HEAD fetcher that ignores input and returns a fixed status code."""

    def _fetch(url: str, headers: dict, timeout: float) -> int:
        return code

    return _fetch


class PrecheckClassificationTests(unittest.TestCase):
    def test_terminal_codes_map_to_correct_error_category(self):
        # (http_code, expected _error_category bucket)
        cases = {
            404: "content_unavailable",
            410: "content_unavailable",
            401: "content_restricted",
            403: "content_restricted",
            451: "content_blocked",
        }
        for code, expected_category in cases.items():
            with self.subTest(code=code):
                outcome = precheck_direct_video_url(DIRECT_URL, fetch=fixed_head(code))
                self.assertEqual(outcome.verdict, PRECHECK_TERMINAL)
                self.assertTrue(outcome.terminal)
                self.assertEqual(outcome.http_code, code)
                # The reason string is what the worker re-raises; it must classify
                # into the intended terminal bucket using the EXISTING category logic.
                self.assertEqual(_error_category(outcome.reason), expected_category)

    def test_2xx_and_ambiguous_codes_proceed(self):
        for code in (200, 206, 301, 302, 429, 500, 503):
            with self.subTest(code=code):
                outcome = precheck_direct_video_url(DIRECT_URL, fetch=fixed_head(code))
                self.assertEqual(outcome.verdict, PRECHECK_PROCEED)
                self.assertFalse(outcome.terminal)
                # An ambiguous probe must never be mistaken for a content_* terminal.
                self.assertNotIn(
                    _error_category(outcome.reason),
                    {"content_unavailable", "content_restricted", "content_blocked"},
                )

    def test_transport_error_proceeds_never_raises(self):
        def boom(url: str, headers: dict, timeout: float) -> int:
            raise OSError("dns down")

        outcome = precheck_direct_video_url(DIRECT_URL, fetch=boom)
        self.assertEqual(outcome.verdict, PRECHECK_PROCEED)
        self.assertFalse(outcome.terminal)
        self.assertIn("precheck_inconclusive", outcome.reason)

    def test_non_http_url_proceeds_without_probing(self):
        called = {"n": 0}

        def counting(url: str, headers: dict, timeout: float) -> int:
            called["n"] += 1
            return 200

        outcome = precheck_direct_video_url("not-a-url", fetch=counting)
        self.assertEqual(outcome.verdict, PRECHECK_PROCEED)
        self.assertEqual(called["n"], 0)


class DownloadGatedByPrecheckTests(unittest.TestCase):
    """The real download must be skipped entirely when precheck is terminal."""

    def test_terminal_precheck_blocks_real_download(self):
        # If precheck is terminal, urlopen must NEVER be called.
        with mock.patch.object(vd.urllib.request, "urlopen") as urlopen:
            result = download_direct_video_url(
                DIRECT_URL,
                "/tmp/does-not-matter",
                referer="https://www.instagram.com/p/abc/",
                precheck_fetch=fixed_head(403),
            )
        urlopen.assert_not_called()
        self.assertFalse(result["success"])
        self.assertTrue(result["precheck_terminal"])
        self.assertEqual(_error_category(result["error"]), "content_restricted")

    def test_terminal_precheck_404_blocks_and_marks_unavailable(self):
        with mock.patch.object(vd.urllib.request, "urlopen") as urlopen:
            result = download_direct_video_url(
                DIRECT_URL,
                "/tmp/does-not-matter",
                precheck_fetch=fixed_head(404),
            )
        urlopen.assert_not_called()
        self.assertTrue(result["precheck_terminal"])
        self.assertEqual(_error_category(result["error"]), "content_unavailable")

    def test_ambiguous_precheck_lets_download_proceed(self):
        # A 200 HEAD must NOT short-circuit; the real download path runs (and here
        # fails on the injected urlopen, proving control reached it).
        with mock.patch.object(
            vd.urllib.request, "urlopen", side_effect=OSError("connection reset")
        ) as urlopen:
            result = download_direct_video_url(
                DIRECT_URL,
                "/tmp/does-not-matter",
                precheck_fetch=fixed_head(200),
            )
        urlopen.assert_called_once()
        self.assertFalse(result["success"])
        self.assertFalse(result["precheck_terminal"])
        # A genuine download failure stays in the retryable "download" bucket via
        # the worker's wrapping — here we just assert it is NOT a content_* verdict.
        self.assertNotIn(
            _error_category(str(result["error"])),
            {"content_unavailable", "content_restricted", "content_blocked"},
        )

    def test_precheck_disabled_skips_probe_entirely(self):
        called = {"n": 0}

        def counting(url: str, headers: dict, timeout: float) -> int:
            called["n"] += 1
            return 403

        with mock.patch.object(
            vd.urllib.request, "urlopen", side_effect=OSError("boom")
        ):
            result = download_direct_video_url(
                DIRECT_URL,
                "/tmp/does-not-matter",
                precheck=False,
                precheck_fetch=counting,
            )
        self.assertEqual(called["n"], 0)  # probe never ran
        self.assertFalse(result["precheck_terminal"])

    def test_missing_url_returns_early_before_precheck(self):
        called = {"n": 0}

        def counting(url: str, headers: dict, timeout: float) -> int:
            called["n"] += 1
            return 404

        result = download_direct_video_url("", "/tmp/x", precheck_fetch=counting)
        self.assertEqual(called["n"], 0)
        self.assertEqual(result["error"], "direct video url missing")
        self.assertFalse(result["precheck_terminal"])


class WorkerReRaiseContractTests(unittest.TestCase):
    """Mirror the worker's call-site branch: a terminal precheck re-raises the
    BARE reason (content_* marker) rather than the 'direct_video_download_failed:'
    prefix that would otherwise win as the retryable 'download' bucket.
    """

    def test_bare_reason_beats_download_prefix(self):
        terminal_result = download_direct_video_url(
            DIRECT_URL,
            "/tmp/x",
            precheck_fetch=fixed_head(403),
        )
        # What the worker does on precheck_terminal:
        bare = str(terminal_result["error"])
        self.assertEqual(_error_category(bare), "content_restricted")
        # What it would (wrongly) do without the special-case — proves the bug we
        # avoided: the prefix drags it back into "download".
        wrapped = f"direct_video_download_failed: {bare}"
        self.assertEqual(_error_category(wrapped), "download")


if __name__ == "__main__":
    unittest.main()
