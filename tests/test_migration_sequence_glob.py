"""Cut 2 — the Postgres migration manifest is derived from migrations/ via glob.

_POSTGRES_MIGRATION_SEQUENCE used to be a hand-maintained ~226-line tuple; a
newly-applied NNN_*.sql that was forgotten in the tuple silently vanished from a
fresh-DB rebuild (see test_migration_199_manifest). It is now
sorted(migrations/*.sql) minus *_down.sql minus an explicit pre-baseline/rollback
exclusion set. These tests guard that the derivation stays faithful to the
historical apply order and cannot silently drop or reorder a migration.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.db import connection

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


class MigrationSequenceGlobTests(unittest.TestCase):
    def _seq(self):
        return connection._POSTGRES_MIGRATION_SEQUENCE

    def test_sequence_matches_discovery_function(self):
        # The module-level constant must equal a fresh discovery run (deterministic).
        self.assertEqual(self._seq(), connection._discover_postgres_migrations())

    def test_sequence_is_a_sorted_unique_tuple(self):
        seq = self._seq()
        self.assertIsInstance(seq, tuple)
        self.assertEqual(list(seq), sorted(seq), "apply order must be filename-sorted")
        self.assertEqual(len(seq), len(set(seq)), "no duplicate migrations")

    def test_first_entry_is_the_postgres_baseline(self):
        # main.py reports [-1] as the latest; [0] must stay the baseline that
        # everything else layers on top of.
        self.assertEqual(self._seq()[0], "003_postgres_baseline.sql")

    def test_every_entry_exists_and_is_not_a_down_script(self):
        for name in self._seq():
            self.assertTrue(
                (MIGRATIONS_DIR / name).exists(),
                f"manifest references nonexistent migration: {name}",
            )
            self.assertFalse(
                name.endswith("_down.sql"),
                f"rollback script must not be in the forward manifest: {name}",
            )

    def test_derivation_equals_glob_minus_down_minus_exclude(self):
        expected = sorted(
            path.name
            for path in MIGRATIONS_DIR.glob("*.sql")
            if not path.name.endswith("_down.sql")
            and path.name not in connection._MIGRATION_EXCLUDE
        )
        self.assertEqual(list(self._seq()), expected)

    def test_excluded_files_exist_on_disk_but_are_not_registered(self):
        # They must be present (so exclusion is deliberate) yet absent from the
        # forward path — these are pre-baseline / rollback artifacts.
        seq = set(self._seq())
        for name in connection._MIGRATION_EXCLUDE:
            self.assertTrue(
                (MIGRATIONS_DIR / name).exists(),
                f"excluded migration is missing from disk: {name}",
            )
            self.assertNotIn(name, seq)

    def test_runner_owned_forward_migrations_have_no_transaction_control(self):
        for name in self._seq():
            match = re.match(r"^(\d{3})", name)
            if (
                match is None
                or int(match.group(1))
                < connection._RUNNER_OWNED_TRANSACTION_MIN_VERSION
            ):
                continue
            sql = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
            self.assertIsNone(
                connection._FORWARD_TRANSACTION_CONTROL_RE.search(sql),
                f"{name} must leave BEGIN/COMMIT to _run_postgres_migrations",
            )

    def test_discovery_fails_closed_on_runner_owned_transaction_control(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "234_bad.sql").write_text(
                "CREATE TABLE audit_bad(id INT);\nCOMMIT;\n",
                encoding="utf-8",
            )
            with patch.object(connection, "_MIGRATIONS_DIR", directory):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Forward migration contains transaction control",
                ):
                    connection._discover_postgres_migrations()


if __name__ == "__main__":
    unittest.main()
