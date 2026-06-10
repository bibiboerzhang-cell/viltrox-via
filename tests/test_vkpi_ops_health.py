from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vkpi_ops_health as health  # noqa: E402


class VkpiOpsHealthTests(unittest.TestCase):
    def test_worker_heartbeat_warns_before_stale_and_dangers_on_stale(self) -> None:
        warning = health.worker_heartbeat_section(
            {"running_count": 1, "stale_running_count": 0, "max_running_age_seconds": 1000},
            stale_minutes=20,
        )
        danger = health.worker_heartbeat_section(
            {"running_count": 1, "stale_running_count": 1, "max_running_age_seconds": 1300},
            stale_minutes=20,
        )

        self.assertEqual(warning["status"], "warning")
        self.assertEqual(danger["status"], "danger")
        self.assertEqual(danger["stale_after_seconds"], 1200)

    def test_failure_rate_splits_provider_pressure_from_permanent_failures(self) -> None:
        section = health.failure_rate_section(
            {
                "done_count": 8,
                "failed_count": 2,
                "blocked_count": 0,
                "provider_pressure_count": 2,
                "provider_pressure_oldest_age_seconds": 120,
            },
            [{"category": "provider_pressure", "count": 2}],
            warning_rate=0.15,
            danger_rate=0.35,
        )

        self.assertEqual(section["status"], "ok")
        self.assertEqual(section["failure_rate"], 0.2)
        self.assertEqual(section["pipeline_failure_rate"], 0.0)
        self.assertEqual(section["pipeline_failure_count"], 0)
        self.assertEqual(section["provider_pressure_count"], 2)
        self.assertEqual(section["provider_pressure_status"], "info")
        self.assertEqual(section["by_error_category"][0]["category"], "provider_pressure")

    def test_failure_rate_warns_on_real_pipeline_failures_and_ignores_content_unavailable(self) -> None:
        section = health.failure_rate_section(
            {
                "done_count": 8,
                "failed_count": 2,
                "blocked_count": 0,
                "provider_pressure_count": 0,
                "provider_pressure_oldest_age_seconds": 0,
            },
            [
                {"category": "download", "count": 1},
                {"category": "content_unavailable", "count": 1},
            ],
            warning_rate=0.09,
            danger_rate=0.35,
        )

        self.assertEqual(section["status"], "warning")
        self.assertEqual(section["failure_rate"], 0.2)
        self.assertEqual(section["pipeline_failure_rate"], 0.1)
        self.assertEqual(section["pipeline_failure_count"], 1)
        self.assertEqual(section["content_unavailable_count"], 1)

    def test_provider_pressure_becomes_warning_only_when_sustained_and_dominant(self) -> None:
        section = health.failure_rate_section(
            {
                "done_count": 2,
                "failed_count": 8,
                "blocked_count": 0,
                "provider_pressure_count": 8,
                "provider_pressure_oldest_age_seconds": 7201,
            },
            [{"category": "provider_pressure", "count": 8}],
            warning_rate=0.15,
            danger_rate=0.35,
        )

        self.assertEqual(section["status"], "warning")
        self.assertEqual(section["pipeline_failure_rate"], 0.0)
        self.assertEqual(section["provider_pressure_status"], "warning")
        self.assertEqual(section["provider_pressure_share"], 0.8)

    def test_cost_daily_reports_visible_total_without_writing(self) -> None:
        section = health.cost_daily_section(
            {"ai_ledger_cost_usd": "1.25", "llm_calls_cost_usd": "0.40", "analysis_cache_cost_usd": "2.75"},
            warning_usd=10,
            danger_usd=30,
        )

        self.assertEqual(section["status"], "ok")
        self.assertEqual(section["visible_total_usd"], 4.0)
        self.assertEqual(section["llm_calls_cost_usd"], 0.4)

    def test_build_payload_is_read_only_and_overall_status_uses_worst_section(self) -> None:
        payload = health.build_payload(
            worker_row={"running_count": 0, "stale_running_count": 0, "max_running_age_seconds": 0},
            failure_row={
                "done_count": 5,
                "failed_count": 5,
                "blocked_count": 0,
                "provider_pressure_count": 4,
                "provider_pressure_oldest_age_seconds": 60,
            },
            error_categories=[{"category": "provider_pressure", "count": 4}, {"category": "download", "count": 1}],
            cost_row={"ai_ledger_cost_usd": 0, "llm_calls_cost_usd": 0, "analysis_cache_cost_usd": 0},
            hours=24,
            stale_minutes=20,
            failure_warning_rate=0.15,
            failure_danger_rate=0.35,
            cost_warning_usd=10,
            cost_danger_usd=30,
        )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["sections"]["failure_rate"]["pipeline_failure_rate"], 0.1)
        self.assertFalse(payload["diagnostics"]["provider_calls"])
        self.assertFalse(payload["diagnostics"]["write_db"])
        self.assertFalse(payload["diagnostics"]["deploy"])
        self.assertIn("apify_jobs", payload["diagnostics"]["tables_read"])


if __name__ == "__main__":
    unittest.main()
