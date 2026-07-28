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

from app.domains.market import ai_today, competitor_radar  # noqa: E402


SCHEMA_HELPERS = (
    (ai_today, ai_today._ensure_schema, "vkpi_ai_today_hot"),
    (competitor_radar, competitor_radar._ensure_schema, "vkpi_competitor_radar"),
)


class MarketRuntimeSchemaBoundaryTests(unittest.TestCase):
    def test_postgres_helpers_never_open_a_connection_or_issue_runtime_ddl(self) -> None:
        for module, helper, _table in SCHEMA_HELPERS:
            with self.subTest(helper=helper.__name__):
                with (
                    patch.object(module, "is_postgres_runtime", return_value=True),
                    patch.object(module, "get_conn") as get_conn,
                ):
                    helper()
                get_conn.assert_not_called()

    def test_sqlite_fixture_bootstrap_remains_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            for module, helper, table in SCHEMA_HELPERS:
                with (
                    patch.object(module, "is_postgres_runtime", return_value=False),
                    patch.object(module, "get_conn", return_value=conn),
                ):
                    helper()
                    helper()
                self.assertIsNotNone(
                    conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                )
        finally:
            conn.close()

    def test_postgres_migrations_own_market_snapshot_tables(self) -> None:
        migration_150 = (ROOT / "migrations" / "150_vkpi_ai_today_hot.sql").read_text()
        migration_152 = (ROOT / "migrations" / "152_vkpi_competitor_radar.sql").read_text()
        self.assertIn("CREATE TABLE IF NOT EXISTS vkpi_ai_today_hot", migration_150)
        self.assertIn("CREATE TABLE IF NOT EXISTS vkpi_competitor_radar", migration_152)


if __name__ == "__main__":
    unittest.main()
