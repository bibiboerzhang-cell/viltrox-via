"""Unit tests for the YouTube availability probe (no network)."""
from __future__ import annotations

import unittest

from app.services.scraping.availability import (
    ProbeResult,
    extract_video_id,
    probe_many,
    probe_video_availability,
)


def fake_fetch(code: int, body: str = ""):
    def _fetch(url: str, timeout: float):
        return code, body

    return _fetch


class ExtractVideoIdTests(unittest.TestCase):
    def test_common_url_shapes(self):
        cases = {
            "https://www.youtube.com/watch?v=4vgEYL5LFbw": "4vgEYL5LFbw",
            "https://www.youtube.com/watch?feature=shared&v=hDtQEs-npGw": "hDtQEs-npGw",
            "https://youtu.be/NUasmeh6uo8": "NUasmeh6uo8",
            "https://www.youtube.com/shorts/8gBAL4WHRAE": "8gBAL4WHRAE",
            "https://www.youtube.com/embed/4vgEYL5LFbw": "4vgEYL5LFbw",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(extract_video_id(url), expected)

    def test_non_youtube_url_yields_empty(self):
        self.assertEqual(extract_video_id("https://vimeo.com/12345"), "")


class ProbeClassificationTests(unittest.TestCase):
    def test_200_is_available_with_title(self):
        result = probe_video_availability(
            "https://youtu.be/4vgEYL5LFbw",
            fetch=fake_fetch(200, '{"title": "Understanding Anamorphic"}'),
        )
        self.assertEqual(result.status, "available")
        self.assertEqual(result.title, "Understanding Anamorphic")
        self.assertFalse(result.terminal)

    def test_404_and_410_are_terminal_unavailable(self):
        for code in (404, 410):
            with self.subTest(code=code):
                result = probe_video_availability(
                    "https://youtu.be/4vgEYL5LFbw",
                    fetch=fake_fetch(code),
                )
                self.assertEqual(result.status, "unavailable")
                self.assertTrue(result.terminal)

    def test_401_403_are_restricted_not_terminal(self):
        for code in (401, 403):
            with self.subTest(code=code):
                result = probe_video_availability(
                    "https://youtu.be/4vgEYL5LFbw",
                    fetch=fake_fetch(code),
                )
                self.assertEqual(result.status, "restricted")
                self.assertFalse(result.terminal)

    def test_unexpected_code_is_unknown(self):
        result = probe_video_availability(
            "https://youtu.be/4vgEYL5LFbw",
            fetch=fake_fetch(503),
        )
        self.assertEqual(result.status, "unknown")
        self.assertFalse(result.terminal)

    def test_transport_error_is_unknown_never_raises(self):
        def boom(url: str, timeout: float):
            raise OSError("dns down")

        result = probe_video_availability("https://youtu.be/4vgEYL5LFbw", fetch=boom)
        self.assertEqual(result.status, "unknown")
        self.assertIn("transport_error", result.reason)

    def test_bad_url_is_unknown_without_fetch(self):
        called = {"n": 0}

        def counting(url: str, timeout: float):
            called["n"] += 1
            return 200, "{}"

        result = probe_video_availability("not a url", fetch=counting)
        self.assertEqual(result.status, "unknown")
        self.assertEqual(called["n"], 0)


class ProbeManyTests(unittest.TestCase):
    def test_batch_paces_between_calls(self):
        sleeps: list[float] = []
        results = probe_many(
            ["https://youtu.be/aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb"],
            qps=2.0,
            fetch=fake_fetch(200, "{}"),
            sleeper=sleeps.append,
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(sleeps, [0.5])
        self.assertTrue(all(isinstance(r, ProbeResult) for r in results))
