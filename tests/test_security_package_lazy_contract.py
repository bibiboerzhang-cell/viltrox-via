"""Cold-import and public-export contracts for the security package."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"


def _probe(source: str, tmp_path: Path) -> dict[str, object]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.fspath(tmp_path),
        "TMPDIR": os.fspath(tmp_path),
        "PYTHONPATH": os.fspath(BACKEND_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "VKPI_SKIP_DOTENV": "1",
        "ENVIRONMENT": "test",
        "JWT_SECRET": "security-package-import-contract",
        "DB_RUNTIME_BACKEND": "sqlite",
    }
    completed = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_security_package_cold_import_is_a_leaf(tmp_path: Path) -> None:
    payload = _probe(
        """
import json
import sys
import app.services.security as security
print(json.dumps({
    "rate_limiter_loaded": "app.services.security.rate_limiter" in sys.modules,
    "exports": list(security.__all__),
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload["rate_limiter_loaded"] is False
    assert payload["exports"] == [
        "rate_limit",
        "check_rate_limit",
        "get_client_ip",
        "get_rate_limit_stats",
        "cleanup_old_buckets",
    ]


def test_security_exports_resolve_to_rate_limiter(tmp_path: Path) -> None:
    payload = _probe(
        """
import json
import sys
import app.services.security as security
from app.services.security import check_rate_limit, rate_limit
backend = sys.modules["app.services.security.rate_limiter"]
print(json.dumps({
    "check_identity": check_rate_limit is backend.check_rate_limit is security.check_rate_limit,
    "decorator_identity": rate_limit is backend.rate_limit is security.rate_limit,
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload == {"check_identity": True, "decorator_identity": True}


def test_security_package_reads_reloaded_backend_function(tmp_path: Path) -> None:
    payload = _probe(
        """
import importlib
import json
import app.services.security as security
from app.services.security import rate_limiter
before = security.check_rate_limit
importlib.reload(rate_limiter)
print(json.dumps({
    "fresh_identity": security.check_rate_limit is rate_limiter.check_rate_limit,
    "changed": before is not security.check_rate_limit,
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload == {"fresh_identity": True, "changed": True}
