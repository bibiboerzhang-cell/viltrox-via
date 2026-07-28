from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.comments import collector, intelligence, sentiment  # noqa: E402
from app.domains.content import pillars  # noqa: E402


SCHEMA_HELPERS = (
    (collector, collector.ensure_vkpi_comments_schema),
    (sentiment, sentiment.ensure_vkpi_sentiment_schema),
    (pillars, pillars.ensure_vkpi_pillar_schema),
    (intelligence, intelligence.ensure_vkpi_comment_intelligence_schema),
)


class CommentsRuntimeSchemaBoundaryTests(unittest.TestCase):
    def test_postgres_helpers_never_open_a_connection_or_issue_runtime_ddl(self) -> None:
        for module, helper in SCHEMA_HELPERS:
            with self.subTest(helper=helper.__name__):
                with (
                    patch.object(module, "is_postgres_runtime", return_value=True),
                    patch.object(module, "get_conn") as get_conn,
                ):
                    helper()
                get_conn.assert_not_called()

    def test_sqlite_fallback_bootstrap_remains_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            for module, helper in SCHEMA_HELPERS:
                with (
                    patch.object(module, "is_postgres_runtime", return_value=False),
                    patch.object(module, "get_conn", return_value=conn),
                ):
                    helper()
                    helper()

            tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertTrue(
                {
                    "vkpi_comments",
                    "vkpi_comments_collection_runs",
                    "vkpi_sentiment_results",
                    "vkpi_pillars",
                    "vkpi_post_pillars",
                    "vkpi_comment_intelligence_runs",
                }.issubset(tables)
            )
            seeded = conn.execute("SELECT COUNT(*) AS n FROM vkpi_pillars").fetchone()
            self.assertEqual(int(seeded["n"]), len(pillars.PILLAR_SEEDS))
        finally:
            conn.close()

    def test_postgres_migrations_own_every_runtime_schema(self) -> None:
        migration_049 = (ROOT / "migrations" / "049_vkpi_comments.sql").read_text()
        migration_051 = (ROOT / "migrations" / "051_vkpi_sentiment.sql").read_text()
        migration_052 = (ROOT / "migrations" / "052_vkpi_pillars.sql").read_text()
        migration_054 = (
            ROOT / "migrations" / "054_vkpi_comment_intelligence_runs.sql"
        ).read_text()
        migration_208 = (ROOT / "migrations" / "208_vkpi_raw_extraction_columns.sql").read_text()

        self.assertIn("CREATE TABLE IF NOT EXISTS vkpi_comments", migration_049)
        self.assertIn("vkpi_comments_collection_runs", migration_049)
        self.assertIn("CREATE TABLE IF NOT EXISTS vkpi_sentiment_results", migration_051)
        self.assertIn("CREATE TABLE IF NOT EXISTS vkpi_pillars", migration_052)
        self.assertIn("CREATE TABLE IF NOT EXISTS vkpi_post_pillars", migration_052)
        self.assertIn("vkpi_comment_intelligence_runs", migration_054)
        self.assertIn(
            "ALTER TABLE vkpi_comments ADD COLUMN IF NOT EXISTS author_avatar_url",
            migration_208,
        )


if __name__ == "__main__":
    unittest.main()
