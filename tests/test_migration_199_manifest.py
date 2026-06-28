"""Smoke test: migration 199_vkpi_skill_runs is correctly registered in the runner manifest.

Context: 199_vkpi_skill_runs.sql was hand-applied but never added to
_POSTGRES_MIGRATION_SEQUENCE, so a fresh DB would not build the table and
/health migration_max stayed at 198. This test guards that 199 is in the
manifest, the file exists, is contiguous after 198, contains no ASCII '?'
(compat placeholder trap), and is idempotent (CREATE ... IF NOT EXISTS).
"""
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
MIGRATION_NAME = "199_vkpi_skill_runs.sql"


class Migration199ManifestTests(unittest.TestCase):
    def _sequence(self):
        from app.db import connection

        return connection._POSTGRES_MIGRATION_SEQUENCE

    def test_199_registered_in_manifest(self):
        self.assertIn(MIGRATION_NAME, self._sequence())

    def test_199_file_exists_and_name_matches(self):
        path = MIGRATIONS_DIR / MIGRATION_NAME
        self.assertTrue(path.exists(), f"missing migration file: {path}")
        # Runner does `_MIGRATIONS_DIR / migration_name` then .exists(); name must match exactly.
        self.assertEqual(path.name, MIGRATION_NAME)

    def test_199_contiguous_after_198(self):
        seq = self._sequence()
        idx_198 = seq.index("198_vkpi_metric_value_provenance.sql")
        idx_199 = seq.index(MIGRATION_NAME)
        self.assertEqual(idx_199, idx_198 + 1, "199 must follow 198 in the manifest")

    def test_199_no_ascii_question_mark(self):
        text = (MIGRATIONS_DIR / MIGRATION_NAME).read_text(encoding="utf-8")
        self.assertNotIn("?", text, "ASCII '?' detonates the compat placeholder layer on apply")

    def test_199_is_idempotent(self):
        text = (MIGRATIONS_DIR / MIGRATION_NAME).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS vkpi_skill_runs", text)
        self.assertIn("CREATE INDEX IF NOT EXISTS", text)

    def test_every_manifest_entry_has_a_file(self):
        # The runner raises RuntimeError on a missing file; guard the whole tail.
        for name in self._sequence():
            self.assertTrue(
                (MIGRATIONS_DIR / name).exists(),
                f"manifest references nonexistent migration: {name}",
            )


if __name__ == "__main__":
    unittest.main()
