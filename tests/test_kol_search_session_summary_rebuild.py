from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.workers import apify_jobs_worker as worker  # noqa: E402


class _FakeCursor:
    def __init__(self, *, current_summary: dict[str, Any], items: list[dict[str, Any]]) -> None:
        self.current_summary = current_summary
        self.items = items
        self.last_sql = ""
        self.update_params: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.last_sql = sql
        if "UPDATE vkpi_kol_search_sessions" in sql:
            self.update_params = params

    def fetchone(self) -> dict[str, Any]:
        if "SELECT result_summary_json" in self.last_sql:
            return {"result_summary_json": self.current_summary}
        return {}

    def fetchall(self) -> list[dict[str, Any]]:
        if "FROM vkpi_kol_search_session_items" in self.last_sql:
            return self.items
        return []


class KolSearchSessionSummaryRebuildTests(unittest.TestCase):
    def test_rebuild_summary_derives_terminal_counts_from_items(self) -> None:
        cursor = _FakeCursor(
            current_summary={
                "query": {"query_text": "find 35mm creators"},
                "counts": {"by_status": {"queued": 2}, "ready": 0},
                "job_status": "queued",
            },
            items=[
                {
                    "id": 11,
                    "item_type": "url_video",
                    "status": "ready",
                    "stage": "analysis",
                    "rank": 1,
                    "score": 0.91,
                    "kol_pool_id": 5662,
                    "evidence_id": 1614,
                    "job_id": 760,
                    "source_url": "https://www.youtube.com/watch?v=abc",
                    "payload_json": {
                        "job_status": "done",
                        "job_last_error": "",
                        "analysis": {"cache_id": 271, "status": "ready"},
                    },
                    "updated_at": datetime(2026, 6, 10, tzinfo=timezone.utc),
                },
                {
                    "id": 12,
                    "item_type": "candidate",
                    "status": "failed",
                    "stage": "profile",
                    "rank": 2,
                    "score": 0.42,
                    "kol_pool_id": None,
                    "evidence_id": None,
                    "job_id": 761,
                    "source_url": "https://www.youtube.com/@missing",
                    "payload_json": {"job_status": "failed", "job_last_error": "provider pressure"},
                    "updated_at": "2026-06-10T01:00:00+00:00",
                },
            ],
        )

        worker._rebuild_search_session_summary(cursor, session_id=47, session_status="partial")

        self.assertIsNotNone(cursor.update_params)
        assert cursor.update_params is not None
        status, raw_summary, session_id = cursor.update_params
        summary = json.loads(raw_summary)

        self.assertEqual(status, "partial")
        self.assertEqual(session_id, 47)
        self.assertEqual(summary["query"], {"query_text": "find 35mm creators"})
        self.assertEqual(summary["item_status"], "ready")
        self.assertEqual(summary["job_status"], "done")
        self.assertEqual(summary["job_last_error"], "")
        self.assertEqual(summary["analysis"], {"cache_id": 271, "status": "ready"})
        self.assertEqual(summary["items_written"], 2)
        self.assertIn("terminal_synced_at", summary)
        self.assertEqual(summary["counts"]["by_status"], {"ready": 1, "failed": 1})
        self.assertEqual(summary["counts"]["ready"], 1)
        self.assertEqual(summary["counts"]["errors"], 1)
        self.assertEqual(summary["counts"]["executed"], 2)
        self.assertEqual(summary["phase"], "partial")
        self.assertEqual(
            summary["progress"],
            {
                "base": 2,
                "total": 2,
                "profile_ready": 1,
                "profile_failed": 1,
                "profile_completed": 2,
                "profile_succeeded": 1,
                "profile_remaining": 0,
                "complete_ready": 1,
                "complete_partial": 0,
                "base_complete": True,
                "requested_tasks_terminal": True,
                "full_analysis_execution_complete": False,
                "full_analysis_observable": False,
                "full_analysis_complete": False,
                "decision_eligible": False,
                "required_tasks_complete": True,
                "complete": True,
            },
        )

    def test_rebuild_running_summary_has_progress_without_terminal_timestamp(self) -> None:
        cursor = _FakeCursor(
            current_summary={"phase": "base", "progress": {"base": 1, "total": 2}},
            items=[
                {
                    "id": 11,
                    "item_type": "recall_candidate",
                    "status": "partial",
                    "stage": "profile",
                    "payload_json": {"job_status": "done"},
                }
            ],
        )

        worker._rebuild_search_session_summary(cursor, session_id=47, session_status="running")

        assert cursor.update_params is not None
        status, raw_summary, _session_id = cursor.update_params
        summary = json.loads(raw_summary)
        self.assertEqual(status, "running")
        self.assertEqual(summary["phase"], "profile")
        self.assertEqual(summary["progress"]["base"], 1)
        self.assertEqual(summary["progress"]["total"], 2)
        self.assertEqual(summary["progress"]["profile_failed"], 0)
        self.assertEqual(summary["progress"]["profile_completed"], 1)
        self.assertEqual(summary["progress"]["profile_remaining"], 1)
        self.assertEqual(summary["progress"]["complete_ready"], 0)
        self.assertEqual(summary["progress"]["complete_partial"], 1)
        self.assertTrue(summary["progress"]["base_complete"] is False)
        self.assertTrue(summary["progress"]["requested_tasks_terminal"] is False)
        self.assertTrue(summary["progress"]["full_analysis_complete"] is False)
        self.assertTrue(summary["progress"]["decision_eligible"] is False)
        self.assertNotIn("terminal_synced_at", summary)

    def test_session_status_from_items_keeps_running_until_queue_drains(self) -> None:
        self.assertEqual(worker._search_session_status_from_items([{"status": "ready"}]), "ready")
        self.assertEqual(worker._search_session_status_from_items([{"status": "queued"}]), "running")
        self.assertEqual(worker._search_session_status_from_items([{"status": "already_queued"}]), "running")
        self.assertEqual(worker._search_session_status_from_items([{"status": "ready"}, {"status": "failed"}]), "partial")
        self.assertEqual(worker._search_session_status_from_items([]), "ready")


if __name__ == "__main__":
    unittest.main()
