"""Cold-import and public-export contracts for the cache package."""
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
        "JWT_SECRET": "cache-package-import-contract",
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


def test_cache_package_cold_import_does_not_initialize_backend(tmp_path: Path) -> None:
    payload = _probe(
        """
import json
import sys
import app.services.cache as cache
print(json.dumps({
    "backend_loaded": "app.services.cache.memory_cache" in sys.modules,
    "exports": list(cache.__all__),
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload["backend_loaded"] is False
    assert payload["exports"] == [
        "cache_get",
        "cache_get_or_build",
        "cache_set",
        "cache_delete",
        "cache_clear",
        "cache_invalidate_admin",
        "cached",
        "get_cache_stats",
    ]


def test_cache_exports_resolve_to_backend_functions(tmp_path: Path) -> None:
    payload = _probe(
        """
import json
import sys
import app.services.cache as cache
from app.services.cache import cache_clear, cache_get
backend = sys.modules["app.services.cache.memory_cache"]
print(json.dumps({
    "clear_identity": cache_clear is backend.cache_clear is cache.cache_clear,
    "get_identity": cache_get is backend.cache_get is cache.cache_get,
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload == {"clear_identity": True, "get_identity": True}


def test_cache_package_reads_reloaded_backend_function(tmp_path: Path) -> None:
    payload = _probe(
        """
import importlib
import json
import app.services.cache as cache
from app.services.cache import memory_cache
before = cache.cache_get
importlib.reload(memory_cache)
print(json.dumps({
    "fresh_identity": cache.cache_get is memory_cache.cache_get,
    "changed": before is not cache.cache_get,
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload == {"fresh_identity": True, "changed": True}
