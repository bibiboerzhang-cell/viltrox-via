from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import _admin_permission_for_request  # noqa: E402


class AdminRbacMappingTests(unittest.TestCase):
    def test_intel_nested_student_routes_use_student_tab(self) -> None:
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/student/overview", "GET"),
            ("student", "read", False),
        )
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/student/schools", "POST"),
            ("student", "write", False),
        )

    def test_intel_nested_via_routes_use_via_tab(self) -> None:
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/via/control-overview", "GET"),
            ("via", "read", False),
        )
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/via/proposals/key/apply", "POST"),
            ("via", "write", False),
        )

    def test_intel_nested_system_routes_use_runtime_tab(self) -> None:
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/system/cache", "GET"),
            ("runtime", "read", False),
        )
        self.assertEqual(
            _admin_permission_for_request("/api/admin/intel/system/cache/clear", "POST"),
            ("runtime", "write", False),
        )

    def test_market_and_brand_intelligence_routes_use_analytics_tab(self) -> None:
        self.assertEqual(
            _admin_permission_for_request("/api/intelligence/market/gaps", "GET"),
            ("analytics", "read", False),
        )
        self.assertEqual(
            _admin_permission_for_request("/api/intelligence/brand/insights/generate", "POST"),
            ("analytics", "write", False),
        )

    def test_trust_user_moderation_routes_use_command_tab(self) -> None:
        for suffix in ("block", "unblock", "flag", "clear-flag", "adjust-score"):
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    _admin_permission_for_request(f"/api/admin/users/42/{suffix}", "POST"),
                    ("command", "write", False),
                )


if __name__ == "__main__":
    unittest.main()
