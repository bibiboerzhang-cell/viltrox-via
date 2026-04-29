import os
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class AppImportSmokeTests(unittest.TestCase):
    def test_main_app_imports(self):
        os.environ.setdefault("JWT_SECRET", "test-secret")
        from app.main import app

        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
