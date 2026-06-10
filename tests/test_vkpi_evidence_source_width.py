from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol.video_evidence_sources import (  # noqa: E402
    EVIDENCE_SOURCE_MAX_LEN,
    profile_crawl_source,
    validate_source_value,
)


class VkpiEvidenceSourceWidthTests(unittest.TestCase):
    def test_profile_crawl_sources_fit_guarded_width(self) -> None:
        self.assertEqual(EVIDENCE_SOURCE_MAX_LEN, 40)
        for platform in ("youtube", "instagram", "tiktok"):
            source = profile_crawl_source(platform)
            with self.subTest(platform=platform, source=source):
                self.assertLessEqual(len(source), EVIDENCE_SOURCE_MAX_LEN)
                self.assertEqual(validate_source_value("scrape_source", source), source)

    def test_source_guard_fails_loudly_before_database_truncation(self) -> None:
        too_long = "x" * (EVIDENCE_SOURCE_MAX_LEN + 1)
        with self.assertRaisesRegex(ValueError, "scrape_source exceeds varchar\\(40\\)"):
            validate_source_value("scrape_source", too_long)

    def test_migration_widens_scrape_and_metrics_sources(self) -> None:
        migration = (ROOT / "migrations" / "104_vkpi_evidence_source_width.sql").read_text()
        self.assertIn("ALTER COLUMN scrape_source TYPE VARCHAR(40)", migration)
        self.assertIn("ALTER COLUMN metrics_source TYPE VARCHAR(40)", migration)


if __name__ == "__main__":
    unittest.main()
