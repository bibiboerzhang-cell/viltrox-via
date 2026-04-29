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

from app.db.repositories import assets as assets_mod  # noqa: E402


SCHEMA = """
CREATE TABLE submission_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL DEFAULT 0,
    asset_role TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    mime_type TEXT DEFAULT '',
    size_bytes INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    checksum TEXT DEFAULT '',
    deleted_at TEXT,
    deleted_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""


class PendingAssetCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "assets.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.get_conn_patch = patch.object(assets_mod, "get_conn", return_value=self.conn)
        self.runtime_patch = patch.object(assets_mod, "is_postgres_runtime", return_value=False)
        self.get_conn_patch.start()
        self.runtime_patch.start()

    def tearDown(self) -> None:
        self.runtime_patch.stop()
        self.get_conn_patch.stop()
        self.conn.close()
        self.tmp.cleanup()

    def seed_asset(self, storage_key: str, created_at: str, *, submission_id: int = 0, role: str = "uploaded_video_pending") -> int:
        cur = self.conn.execute(
            """
            INSERT INTO submission_assets (
                submission_id, asset_role, storage_key, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (submission_id, role, storage_key, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def test_cleanup_soft_deletes_only_old_unbound_pending_assets(self) -> None:
        stale_id = self.seed_asset("uploads/stale.mp4", "2000-01-01T00:00:00Z")
        fresh_id = self.seed_asset("uploads/fresh.mp4", "2999-04-29T08:00:00Z")
        bound_id = self.seed_asset("uploads/bound.mp4", "2000-01-01T00:00:00Z", submission_id=12, role="uploaded_video")

        result = assets_mod.cleanup_stale_pending_assets(max_age_minutes=30)

        self.assertEqual(result["deleted"], 1)
        stale = self.conn.execute("SELECT deleted_at, deleted_reason FROM submission_assets WHERE id=?", (stale_id,)).fetchone()
        fresh = self.conn.execute("SELECT deleted_at FROM submission_assets WHERE id=?", (fresh_id,)).fetchone()
        bound = self.conn.execute("SELECT deleted_at FROM submission_assets WHERE id=?", (bound_id,)).fetchone()
        assert stale is not None and fresh is not None and bound is not None
        self.assertTrue(stale["deleted_at"])
        self.assertEqual(stale["deleted_reason"], "pending_unbound_older_than_30m")
        self.assertIsNone(fresh["deleted_at"])
        self.assertIsNone(bound["deleted_at"])

    def test_attach_ignores_soft_deleted_pending_asset(self) -> None:
        stale_id = self.seed_asset("uploads/stale.mp4", "2000-01-01T00:00:00Z")
        self.conn.execute(
            "UPDATE submission_assets SET deleted_at='2026-04-29T09:00:00Z', deleted_reason='pending_unbound_older_than_30m' WHERE id=?",
            (stale_id,),
        )
        self.conn.commit()

        asset = assets_mod.attach_uploaded_asset_to_submission(
            submission_id=44,
            asset_id=stale_id,
        )

        self.assertIsNone(asset)
        row = self.conn.execute("SELECT submission_id, asset_role FROM submission_assets WHERE id=?", (stale_id,)).fetchone()
        assert row is not None
        self.assertEqual(row["submission_id"], 0)
        self.assertEqual(row["asset_role"], "uploaded_video_pending")

    def test_attach_by_storage_key_ignores_soft_deleted_matches(self) -> None:
        deleted_id = self.seed_asset("uploads/deleted.mp4", "2000-01-01T00:00:00Z")
        active_id = self.seed_asset("uploads/active.mp4", "2000-01-01T00:00:00Z")
        self.conn.execute(
            "UPDATE submission_assets SET deleted_at='2026-04-29T09:00:00Z', deleted_reason='pending_unbound_older_than_30m' WHERE id=?",
            (deleted_id,),
        )
        self.conn.commit()

        missing = assets_mod.attach_uploaded_asset_to_submission(
            submission_id=45,
            r2_key="uploads/deleted.mp4",
        )
        attached = assets_mod.attach_uploaded_asset_to_submission(
            submission_id=46,
            r2_key="uploads/active.mp4",
        )

        self.assertIsNone(missing)
        self.assertIsNotNone(attached)
        assert attached is not None
        self.assertEqual(attached["id"], active_id)
        self.assertEqual(attached["submission_id"], 46)


if __name__ == "__main__":
    unittest.main()
