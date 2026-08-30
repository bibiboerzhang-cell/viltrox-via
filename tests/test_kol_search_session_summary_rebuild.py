from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.workers import apify_jobs_worker as worker  # noqa: E402


class _FakeCursor:
    def __init__(self, *, current_summary: Any, items: list[dict[str, Any]]) -> None:
        self.current_summary = current_summary
        self.items = items
        self.last_sql = ""
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.update_params: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.last_sql = sql
        self.calls.append((" ".join(str(sql).split()), tuple(params)))
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


class _FrozenDatetime:
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return datetime(2026, 8, 29, 12, 34, 56, tzinfo=tz)


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

    def test_rebuild_sql_order_scalar_summary_empty_items_and_none_return(self) -> None:
        from app.domains.kol import search_session_job_analysis

        cursor = _FakeCursor(current_summary=["legacy-scalar"], items=[])
        with patch.object(search_session_job_analysis, "datetime", _FrozenDatetime):
            result = worker._rebuild_search_session_summary(
                cursor,
                session_id=47,
                session_status="failed",
            )

        self.assertIsNone(result)
        self.assertEqual(len(cursor.calls), 3)
        self.assertTrue(cursor.calls[0][0].startswith("SELECT result_summary_json"))
        self.assertEqual(cursor.calls[0][1], (47,))
        self.assertIn("ORDER BY rank NULLS LAST, id", cursor.calls[1][0])
        self.assertEqual(cursor.calls[1][1], (47,))
        self.assertTrue(cursor.calls[2][0].startswith("UPDATE vkpi_kol_search_sessions"))
        assert cursor.update_params is not None
        status, raw_summary, session_id = cursor.update_params
        summary = json.loads(raw_summary)
        self.assertEqual((status, session_id), ("failed", 47))
        self.assertEqual(summary["phase"], "partial")
        self.assertEqual(summary["counts"]["by_status"], {})
        self.assertEqual(summary["items_written"], 0)
        self.assertEqual(summary["terminal_synced_at"], "2026-08-29T12:34:56+00:00")

    def test_rebuild_prefers_later_url_item_and_decodes_json_payload(self) -> None:
        cursor = _FakeCursor(
            current_summary={"query": {"query_text": "lens creator"}},
            items=[
                {
                    "id": 1,
                    "item_type": "candidate",
                    "status": "partial",
                    "stage": "profile",
                    "payload_json": {"job_status": "blocked"},
                },
                {
                    "id": 2,
                    "item_type": "url_video",
                    "status": "ready",
                    "stage": "summary",
                    "payload_json": json.dumps(
                        {
                            "job_status": "done",
                            "job_last_error": "",
                            "analysis": {"cache_id": 91, "status": "ready"},
                            "profile_execute": {"status": "ready"},
                        }
                    ),
                },
            ],
        )

        worker._rebuild_search_session_summary(
            cursor,
            session_id=48,
            session_status="ready",
        )

        assert cursor.update_params is not None
        summary = json.loads(cursor.update_params[1])
        self.assertEqual(summary["query"], {"query_text": "lens creator"})
        self.assertEqual(summary["item_status"], "ready")
        self.assertEqual(summary["job_status"], "done")
        self.assertEqual(summary["job_last_error"], "")
        self.assertEqual(summary["analysis"], {"cache_id": 91, "status": "ready"})

    def test_worker_rebuild_symbol_is_the_domain_facade(self) -> None:
        from app.domains.kol import search_session_job_sync
        from app.workers import apify_jobs_worker_session

        self.assertIs(
            apify_jobs_worker_session._rebuild_search_session_summary,
            search_session_job_sync.rebuild_search_session_summary,
        )

    def test_session_status_from_items_keeps_running_until_queue_drains(self) -> None:
        self.assertEqual(worker._search_session_status_from_items([{"status": "ready"}]), "ready")
        self.assertEqual(worker._search_session_status_from_items([{"status": "queued"}]), "running")
        self.assertEqual(worker._search_session_status_from_items([{"status": "already_queued"}]), "running")
        self.assertEqual(worker._search_session_status_from_items([{"status": "ready"}, {"status": "failed"}]), "partial")
        self.assertEqual(worker._search_session_status_from_items([]), "ready")


if __name__ == "__main__":
    unittest.main()
