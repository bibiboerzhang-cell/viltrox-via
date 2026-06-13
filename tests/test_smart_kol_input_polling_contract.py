from __future__ import annotations

import re
import unittest
from pathlib import Path


SMART_PANEL = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "components"
    / "vkpi"
    / "v615-replica"
    / "components"
    / "SmartKolInputPanel.tsx"
)


class SmartKolInputPollingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SMART_PANEL.read_text(encoding="utf-8")

    def test_terminal_statuses_include_partial_and_failed_states(self) -> None:
        match = re.search(r"function terminalSessionStatus\(value: unknown\): boolean \{(?P<body>.*?)\n\}", self.source, re.S)
        self.assertIsNotNone(match)
        body = match.group("body") if match else ""
        for status in ("ready", "partial", "failed", "done", "blocked", "cancelled", "canceled"):
            with self.subTest(status=status):
                self.assertIn(f'"{status}"', body)

    def test_polling_stops_on_terminal_session_and_refreshes_history(self) -> None:
        self.assertIn("if (isSearchSessionTerminal(session))", self.source)
        self.assertIn("setActiveSearchSessionId(null);", self.source)
        self.assertIn('setSessionPollNotice("已找完，结果已更新");', self.source)
        self.assertIn("void refreshHistory();", self.source)

    def test_polling_has_visibility_pause_timeout_and_cleanup(self) -> None:
        self.assertIn("const maxPollMs = 12 * 60 * 1000;", self.source)
        self.assertIn('document.visibilityState === "hidden"', self.source)
        self.assertIn("window.setInterval", self.source)
        self.assertIn("}, 2500);", self.source)
        self.assertIn("window.clearInterval(timer);", self.source)

    def test_terminal_detection_reads_session_and_summary_job_statuses(self) -> None:
        match = re.search(r"function isSearchSessionTerminal\(session: VkpiKolSearchHistoryItem\): boolean \{(?P<body>.*?)\n\}", self.source, re.S)
        self.assertIsNotNone(match)
        body = match.group("body") if match else ""
        self.assertIn("terminalSessionStatus(session.status)", body)
        self.assertIn("summary.profile_batch_advance", body)
        self.assertIn("summary.smart_search_profile_advance_job", body)
        self.assertIn("terminalSessionStatus(batch.status)", body)
        self.assertIn("terminalSessionStatus(smartJob.status)", body)
        self.assertIn("terminalSessionStatus(smartJob.advance_status)", body)


if __name__ == "__main__":
    unittest.main()
