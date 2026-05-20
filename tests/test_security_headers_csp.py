from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("JWT_SECRET", "test-secret")

from app.main import _csp_value_for_request, _should_send_hsts  # noqa: E402


def _request(host: str, proto: str = "https"):
    return SimpleNamespace(
        headers={"host": host, "x-forwarded-proto": proto},
        url=SimpleNamespace(scheme="http"),
    )


class SecurityHeadersCspTests(unittest.TestCase):
    def test_public_host_csp_does_not_expose_dev_origins(self) -> None:
        csp = _csp_value_for_request(_request("viltroxtest.com"))
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn("localhost", csp)
        self.assertNotIn("127.0.0.1", csp)
        self.assertNotIn("ws://", csp)

    def test_local_host_csp_keeps_dev_origins(self) -> None:
        csp = _csp_value_for_request(_request("127.0.0.1:8001", proto="http"))
        self.assertIn("http://127.0.0.1:5173", csp)
        self.assertIn("ws://localhost:5173", csp)

    def test_hsts_is_sent_for_public_hosts_even_behind_proxy(self) -> None:
        self.assertTrue(_should_send_hsts(_request("viltroxtest.com", proto="http")))
        self.assertFalse(_should_send_hsts(_request("localhost:8001", proto="http")))


if __name__ == "__main__":
    unittest.main()
