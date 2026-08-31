"""Import compatibility contract for the system package's lazy runtime module."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"


def _run_probe(source: str, tmp_path: Path) -> dict[str, object]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.fspath(tmp_path),
        "TMPDIR": os.fspath(tmp_path),
        "PYTHONPATH": os.fspath(BACKEND_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "VKPI_SKIP_DOTENV": "1",
        "ENVIRONMENT": "test",
        "JWT_SECRET": "system-package-import-contract",
        "DB_RUNTIME_BACKEND": "sqlite",
    }
    completed = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_provider_health_cold_import_does_not_eager_load_runtime(tmp_path: Path) -> None:
    payload = _run_probe(
        """
import json
import sys
import app.services.system as package
from app.services.system import provider_health

print(json.dumps({
    "provider_identity": (
        provider_health is sys.modules["app.services.system.provider_health"]
    ),
    "runtime_loaded": "app.services.system.runtime" in sys.modules,
    "runtime_exported": "runtime" in package.__all__,
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload == {
        "provider_identity": True,
        "runtime_loaded": False,
        "runtime_exported": True,
    }


def test_system_package_cold_import_has_no_eager_service_graph(tmp_path: Path) -> None:
    payload = _run_probe(
        """
import json
import sys
import app.services.system as package

names = ["integrations", "runtime", "staff", "trust_admin"]
print(json.dumps({
    "loaded": [name for name in names if f"app.services.system.{name}" in sys.modules],
    "exports": list(package.__all__),
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload == {
        "loaded": [],
        "exports": ["integrations", "runtime", "staff", "trust_admin"],
    }


def test_system_package_attribute_access_remains_compatible(tmp_path: Path) -> None:
    payload = _run_probe(
        """
import json
import sys
import app.services.system as package
staff = package.staff
print(json.dumps({
    "identity": staff is sys.modules["app.services.system.staff"],
    "runtime_loaded": "app.services.system.runtime" in sys.modules,
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload == {"identity": True, "runtime_loaded": False}


def test_explicit_runtime_import_is_compatible_in_either_order(tmp_path: Path) -> None:
    probe_template = """
import importlib
import json
import sys
import app.services.system as package
{ordered_imports}
from app.services.system import provider_health as provider_again
from app.services.system import runtime as runtime_again

print(json.dumps({{
    "provider_identity": (
        provider_health is provider_again
        and provider_again is sys.modules["app.services.system.provider_health"]
    ),
    "runtime_identity": (
        runtime is runtime_again
        and runtime_again is importlib.import_module("app.services.system.runtime")
        and package.runtime is runtime_again
    ),
    "runtime_exported": "runtime" in package.__all__,
}}, separators=(",", ":")))
"""
    provider_then_runtime = _run_probe(
        probe_template.format(
            ordered_imports=(
                "from app.services.system import provider_health\n"
                "from app.services.system import runtime"
            )
        ),
        tmp_path,
    )
    runtime_then_provider = _run_probe(
        probe_template.format(
            ordered_imports=(
                "from app.services.system import runtime\n"
                "from app.services.system import provider_health"
            )
        ),
        tmp_path,
    )

    expected = {
        "provider_identity": True,
        "runtime_identity": True,
        "runtime_exported": True,
    }
    assert provider_then_runtime == expected
    assert runtime_then_provider == expected
