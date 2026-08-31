from __future__ import annotations

import re
import unittest
from pathlib import Path


_COMPONENTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "frontend" / "src" / "components" / "vkpi" / "cockpit" / "components"
)
# 瘦身重构把 SmartKolInputPanel 的纯函数/派生器/子组件拆到多个 sibling
# (.helpers.ts / .Sections.tsx / .derivers.ts …,terminalSessionStatus / isSearchSessionTerminal
# 等定义会随拆分迁移)。契约测试 glob 全部 SmartKolInputPanel* 源拼成一份,验定义+调用契约不破——
# 无论以后再怎么拆,只要这些名字与调用仍在该组件家族内即通过。
SMART_PANEL_SIBLINGS = sorted(_COMPONENTS_DIR.glob("SmartKolInputPanel*.ts")) + sorted(
    _COMPONENTS_DIR.glob("SmartKolInputPanel*.tsx")
)
POLLING_SOURCE = (_COMPONENTS_DIR / "SmartKolInputPanel.polling.ts").read_text(
    encoding="utf-8"
)


class SmartKolInputPollingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = "\n".join(p.read_text(encoding="utf-8") for p in SMART_PANEL_SIBLINGS)

    def test_terminal_statuses_include_partial_and_failed_states(self) -> None:
        match = re.search(r"(?:export )?function terminalSessionStatus\(value: unknown\): boolean \{(?P<body>.*?)\n\}", self.source, re.S)
        self.assertIsNotNone(match)
        body = match.group("body") if match else ""
        for status in ("ready", "partial", "failed", "done", "blocked", "cancelled", "canceled"):
            with self.subTest(status=status):
                self.assertIn(f'"{status}"', body)

    def test_polling_waits_for_required_tasks_or_terminal_grace_and_refreshes_history(self) -> None:
        self.assertIn("const progress = searchSessionProgress(session);", self.source)
        self.assertIn("if (progress.requiredTasksComplete)", self.source)
        self.assertIn("Date.now() - terminalSince >= 30000", self.source)
        self.assertNotIn("haveDiscovery || graceUsedUp", self.source)
        # Polling completion must stop only the transport loop.  The displayed
        # session remains bound so terminal results can still be approved.
        self.assertIn("setPollingSearchSessionId(null);", self.source)
        self.assertNotIn("setActiveSearchSessionId(null);", self.source)
        self.assertIn("结果已更新", self.source)
        self.assertIn("void refreshHistory();", self.source)

    def test_polling_merges_sparse_snapshots_per_kol_and_reports_stage_progress(self) -> None:
        self.assertIn("mergeKolSearchSessionSnapshots(prev, session)", self.source)
        self.assertIn("mergeKolRecallSnapshots(prev, polledRecall)", self.source)
        self.assertIn("基础结果 ${progress.basicVisible}/${progress.target}", self.source)
        self.assertIn("档案补全 ${progress.profileReady}/${progress.target}", self.source)
        self.assertIn("完整分析 ${progress.deepReady}/${progress.target}", self.source)

    def test_polling_has_visibility_pause_timeout_and_cleanup(self) -> None:
        # Scope this contract to the polling hook itself.  Searching every
        # SmartKol sibling let unrelated setInterval calls make the old fixed
        # cadence contract pass after polling moved to adaptive setTimeout.
        self.assertIn("const maxPollMs = 12 * 60 * 1000;", POLLING_SOURCE)
        self.assertIn("let stopped = false;", POLLING_SOURCE)
        self.assertIn("let inFlight = false;", POLLING_SOURCE)
        self.assertIn(
            "if (cancelled || stopped || inFlight) return;",
            POLLING_SOURCE,
        )
        self.assertIn("inFlight = true;", POLLING_SOURCE)
        self.assertIn("inFlight = false;", POLLING_SOURCE)
        self.assertIn(
            "SESSION_POLL_BACKOFF_MS = [2500, 2500, 5000, 5000, 10000]",
            POLLING_SOURCE,
        )
        self.assertIn(
            "window.setTimeout(tick, sessionPollDelayMs(idleSteps))",
            POLLING_SOURCE,
        )
        self.assertIn("idleSteps += 1;", POLLING_SOURCE)
        self.assertIn("idleSteps = 0;", POLLING_SOURCE)
        self.assertIn('document.visibilityState === "hidden"', POLLING_SOURCE)
        self.assertIn("window.clearTimeout(timer);", POLLING_SOURCE)
        self.assertIn(
            'document.addEventListener("visibilitychange", onVisibilityChange);',
            POLLING_SOURCE,
        )
        self.assertIn(
            'document.removeEventListener("visibilitychange", onVisibilityChange);',
            POLLING_SOURCE,
        )
        self.assertNotIn("window.setInterval", POLLING_SOURCE)

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
