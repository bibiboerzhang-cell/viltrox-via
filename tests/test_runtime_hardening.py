from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.security import _user_cache_key, make_token, verify_token  # noqa: E402
from app.services.creator_program import _table_exists  # noqa: E402
from app.services.rewards.points import calculate_submission_points  # noqa: E402


class RuntimeHardeningTests(unittest.TestCase):
    def test_make_token_roundtrip(self) -> None:
        token = make_token(42, "creator")
        payload = verify_token(token)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["uid"], 42)
        self.assertEqual(payload["role"], "creator")

    def test_user_cache_key_is_scoped_by_user(self) -> None:
        first = _user_cache_key(7, "token-a")
        second = _user_cache_key(7, "token-a")
        third = _user_cache_key(8, "token-a")
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertTrue(first.startswith("auth:user:7:"))

    def test_table_exists_rejects_blank_name(self) -> None:
        self.assertFalse(_table_exists(""))
        self.assertFalse(_table_exists("   "))

    def test_submission_points_match_campaign_score_scale(self) -> None:
        self.assertEqual(calculate_submission_points(252), 252)
        self.assertEqual(calculate_submission_points(105), 105)
        self.assertEqual(calculate_submission_points(9), 10)
        self.assertEqual(calculate_submission_points(0), 0)


if __name__ == "__main__":
    unittest.main()
