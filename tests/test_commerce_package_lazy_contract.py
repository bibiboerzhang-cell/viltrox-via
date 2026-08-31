"""Cold-import compatibility contract for the commerce package."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"


def test_commerce_package_attribute_is_lazy_and_compatible(tmp_path: Path) -> None:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.fspath(tmp_path),
        "TMPDIR": os.fspath(tmp_path),
        "PYTHONPATH": os.fspath(BACKEND_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "VKPI_SKIP_DOTENV": "1",
        "ENVIRONMENT": "test",
        "JWT_SECRET": "commerce-package-import-contract",
        "DB_RUNTIME_BACKEND": "sqlite",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            """
import json
import sys
import app.services.commerce as commerce
cold = "app.services.commerce.orders" not in sys.modules
orders = commerce.orders
print(json.dumps({
    "cold": cold,
    "identity": orders is sys.modules["app.services.commerce.orders"],
}, separators=(",", ":")))
""",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "cold": True,
        "identity": True,
    }
