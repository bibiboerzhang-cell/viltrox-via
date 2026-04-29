from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.jobs import queue as queue_mod  # noqa: E402


SCHEMA = """
CREATE TABLE job_execution_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL DEFAULT 'audit_submission',
    submission_id INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT DEFAULT '{}',
    retry_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_message TEXT,
    summary TEXT,
    detection_status TEXT,
    result_path TEXT,
    result_json TEXT DEFAULT '{}',
    stats_json TEXT DEFAULT '{}',
    stage TEXT,
    stream_id TEXT,
    consumer_name TEXT,
    extra_json TEXT DEFAULT '{}'
);
"""


class QueueStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "queue.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.queue = queue_mod.RedisJobQueue.__new__(queue_mod.RedisJobQueue)
        self.get_conn_patch = patch.object(queue_mod, "get_conn", return_value=self.conn)
        self.get_conn_patch.start()

    def tearDown(self) -> None:
        self.get_conn_patch.stop()
        self.conn.close()
        self.tmp.cleanup()

    def seed(self, task_id: str, status: str) -> None:
        self.conn.execute(
            """
            INSERT INTO job_execution_ledger (
                task_id, job_type, submission_id, user_id, status, payload_json,
                retry_count, created_at, updated_at, stage, extra_json
            )
            VALUES (?, 'audit_submission', 1, 7, ?, '{}', 0, '2026-04-28T00:00:00Z', '2026-04-28T00:00:00Z', 'analyze', '{}')
            """,
            (task_id, status),
        )
        self.conn.commit()

    def status_of(self, task_id: str) -> str:
        row = self.conn.execute("SELECT status FROM job_execution_ledger WHERE task_id=?", (task_id,)).fetchone()
        assert row is not None
        return str(row["status"])

    def test_done_cannot_revert_to_processing(self) -> None:
        self.seed("task-done", "done")
        snapshot = self.queue._update_job_ledger("task-done", "processing")
        self.assertEqual(self.status_of("task-done"), "done")
        self.assertTrue(snapshot and snapshot.get("_stale_status_ignored"))

    def test_failed_cannot_revert_to_queued(self) -> None:
        self.seed("task-failed", "failed")
        snapshot = self.queue._update_job_ledger("task-failed", "queued")
        self.assertEqual(self.status_of("task-failed"), "failed")
        self.assertTrue(snapshot and snapshot.get("_stale_status_ignored"))

    def test_prefilter_rejected_cannot_revert_to_retrying(self) -> None:
        self.seed("task-prefilter", "prefilter_rejected")
        snapshot = self.queue._update_job_ledger("task-prefilter", "retrying")
        self.assertEqual(self.status_of("task-prefilter"), "prefilter_rejected")
        self.assertTrue(snapshot and snapshot.get("_stale_status_ignored"))

    def test_terminal_metadata_can_be_filled_once(self) -> None:
        self.seed("task-meta", "done")
        self.queue._update_job_ledger("task-meta", "processing", stream_id="stream-1", consumer_name="worker-1")
        row = self.conn.execute(
            "SELECT status, stream_id, consumer_name FROM job_execution_ledger WHERE task_id=?",
            ("task-meta",),
        ).fetchone()
        assert row is not None
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["stream_id"], "stream-1")
        self.assertEqual(row["consumer_name"], "worker-1")

        self.queue._update_job_ledger("task-meta", "queued", stream_id="stream-2", consumer_name="worker-2")
        row = self.conn.execute(
            "SELECT status, stream_id, consumer_name FROM job_execution_ledger WHERE task_id=?",
            ("task-meta",),
        ).fetchone()
        assert row is not None
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["stream_id"], "stream-1")
        self.assertEqual(row["consumer_name"], "worker-1")

    def test_normal_transition_still_updates(self) -> None:
        self.seed("task-normal", "queued")
        snapshot = self.queue._update_job_ledger("task-normal", "processing", stage="analysis")
        self.assertEqual(self.status_of("task-normal"), "processing")
        self.assertFalse(snapshot and snapshot.get("_stale_status_ignored"))

    def test_unknown_task_returns_none(self) -> None:
        self.assertIsNone(self.queue._update_job_ledger("missing", "done"))


if __name__ == "__main__":
    unittest.main()
