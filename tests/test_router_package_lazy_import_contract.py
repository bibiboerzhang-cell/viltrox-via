from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"

# Exact public submodule surface that the former package-level eager import
# populated.  Keep this characterization independent from __all__ so a future
# accidental removal cannot make the test agree with itself.
LEGACY_EAGER_MODULES = (
    "activities",
    "auth",
    "admin",
    "audit",
    "creator",
    "uploads",
    "sse",
    "leaderboard",
    "platform_ingest",
    "media",
    "via",
    "student_identity",
    "vkpi",
    "vkpi_attribution_metrics",
    "vkpi_comment_intelligence",
    "vkpi_audit",
    "vkpi_comments",
    "vkpi_costs",
    "vkpi_dashboard_staff",
    "vkpi_data_quality",
    "vkpi_evidence_assets",
    "vkpi_feedback",
    "vkpi_firewall",
    "vkpi_industry_automation",
    "vkpi_kol_links",
    "vkpi_kol_pool",
    "vkpi_memory",
    "vkpi_operations",
    "vkpi_pillars",
    "vkpi_product_analysis",
    "vkpi_projects",
    "vkpi_reconciliation",
    "vkpi_reports",
    "vkpi_settings",
    "vkpi_sentiment",
    "vkpi_sync",
    "vkpi_weekly_reports",
    "vkpi_workflow_assets",
)

# main.py imports these router modules directly even though they were absent
# from the old package-level eager list.  They exercise Python's implicit
# package-submodule import compatibility as well.
MAIN_ONLY_MODULES = (
    "jobs",
    "ops",
    "verify",
    "vkpi_kol_portal",
    "commerce",
    "deepsight",
    "insights",
    "intelligence",
    "intelligence_admin",
    "system_admin",
    "account_scanner",
    "brand_analysis",
    "kol_ops",
)

EXPECTED_REGISTRY_COUNT = 122
EXPECTED_REGISTRY_SHA256 = (
    "8f063fe552cf7af26c180cb1c4588f1a734a708e6a12f8f40f7699392f62a008"
)
EXPECTED_CURRENT_ROUTE_COUNT_WITHOUT_FRONTEND_ASSETS = 1273
EXPECTED_CURRENT_ROUTE_SHA256_WITHOUT_FRONTEND_ASSETS = (
    "123366f13170f3da0e506f62ed50905ae2bd1d7946ebde93e126c310386401be"
)
EXPECTED_CURRENT_ROUTE_COUNT_WITH_FRONTEND_ASSETS = 1274
EXPECTED_CURRENT_ROUTE_SHA256_WITH_FRONTEND_ASSETS = (
    "5ba080afc94871cf0fc78af7ac8e33fd891b3db9338ee71ef4b927043af16615"
)


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    """Small explicit environment: no dotenv, provider key, DB URL or proxy."""

    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.fspath(tmp_path),
        "TMPDIR": os.fspath(tmp_path),
        "PYTHONPATH": os.fspath(BACKEND_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "VKPI_SKIP_DOTENV": "1",
        "VKPI_RUNTIME_DATA_DIR": os.fspath(tmp_path / "runtime-data"),
        "ENVIRONMENT": "test",
        "V2_PRODUCTION_MODE": "0",
        "APP_ROLE": "all",
        "JWT_SECRET": "router-package-contract-test",
        "DB_RUNTIME_BACKEND": "sqlite",
        "ENABLE_LOCAL_ORCHESTRATOR": "0",
        "ENABLE_BROWSER": "0",
        "ENABLE_SCHEDULER": "0",
        "ENABLE_UPLOAD_CLEANUP": "0",
    }


def _run_probe(code: str, tmp_path: Path) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT,
        env=_isolated_env(tmp_path),
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _network_guard_source() -> str:
    return """
import sys

def _deny_network(event, args):
    if event in {"socket.bind", "socket.connect", "socket.getaddrinfo"}:
        raise AssertionError(f"network access forbidden during router import: {event}")

sys.addaudithook(_deny_network)
"""


def _route_probe_source(*, legacy_eager_first: bool) -> str:
    eager_import = ""
    if legacy_eager_first:
        eager_import = (
            "from app.api.routers import " + ", ".join(LEGACY_EAGER_MODULES)
        )
    return (
        _network_guard_source()
        + f"\n{eager_import}\n"
        + """
import hashlib
import json
from app.api.routers import ADMIN_ROUTER_MODULES
from app.main import app

signature = []
for route in app.routes:
    methods = getattr(route, "methods", None)
    signature.append([
        type(route).__name__,
        getattr(route, "path", None),
        sorted(methods) if methods is not None else None,
        getattr(route, "name", None),
    ])
canonical = json.dumps(signature, ensure_ascii=False, separators=(",", ":"))
registry = json.dumps(
    list(ADMIN_ROUTER_MODULES), ensure_ascii=False, separators=(",", ":")
)
print(json.dumps({
    "signature": signature,
    "route_count": len(signature),
    "route_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
    "registry_count": len(ADMIN_ROUTER_MODULES),
    "registry_sha256": hashlib.sha256(registry.encode()).hexdigest(),
}, ensure_ascii=False, separators=(",", ":")))
"""
    )


def test_importing_router_package_does_not_eager_load_submodules(tmp_path: Path) -> None:
    payload = _run_probe(
        _network_guard_source()
        + """
import json
import sys
import app.api.routers as routers

loaded = sorted(
    name for name in sys.modules
    if name.startswith("app.api.routers.") and name.count(".") == 3
)
print(json.dumps({
    "loaded": loaded,
    "all": routers.__all__,
    "registry_count": len(routers.ADMIN_ROUTER_MODULES),
}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload == {
        "loaded": [],
        "all": [*LEGACY_EAGER_MODULES, "ADMIN_ROUTER_MODULES"],
        "registry_count": EXPECTED_REGISTRY_COUNT,
    }


def test_explicit_package_submodule_imports_remain_compatible(tmp_path: Path) -> None:
    names = (*LEGACY_EAGER_MODULES, *MAIN_ONLY_MODULES)
    import_source = "from app.api.routers import " + ", ".join(names)
    payload = _run_probe(
        _network_guard_source()
        + f"""
import json
import sys
{import_source}

names = {list(names)!r}
missing = [name for name in names if f"app.api.routers.{{name}}" not in sys.modules]
wrong_identity = [
    name for name in names
    if globals()[name] is not sys.modules[f"app.api.routers.{{name}}"]
]
print(json.dumps({{"missing": missing, "wrong_identity": wrong_identity}}, separators=(",", ":")))
""",
        tmp_path,
    )

    assert payload == {"missing": [], "wrong_identity": []}


def test_main_route_signature_matches_legacy_eager_import_order(tmp_path: Path) -> None:
    legacy = _run_probe(_route_probe_source(legacy_eager_first=True), tmp_path)
    lazy = _run_probe(_route_probe_source(legacy_eager_first=False), tmp_path)
    has_frontend_assets = (ROOT / "frontend" / "dist" / "assets").is_dir()
    expected_route_count = (
        EXPECTED_CURRENT_ROUTE_COUNT_WITH_FRONTEND_ASSETS
        if has_frontend_assets
        else EXPECTED_CURRENT_ROUTE_COUNT_WITHOUT_FRONTEND_ASSETS
    )
    expected_route_sha256 = (
        EXPECTED_CURRENT_ROUTE_SHA256_WITH_FRONTEND_ASSETS
        if has_frontend_assets
        else EXPECTED_CURRENT_ROUTE_SHA256_WITHOUT_FRONTEND_ASSETS
    )

    assert lazy["signature"] == legacy["signature"]
    assert lazy["route_count"] == legacy["route_count"] == expected_route_count
    assert lazy["route_sha256"] == legacy["route_sha256"] == expected_route_sha256
    assert lazy["registry_count"] == legacy["registry_count"] == EXPECTED_REGISTRY_COUNT
    assert lazy["registry_sha256"] == legacy["registry_sha256"] == EXPECTED_REGISTRY_SHA256
