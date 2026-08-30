"""Compatibility contract for the intelligence package's lazy facade."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"

EXPECTED_EXPORTS = {
    "fetch_bh_viltrox_products": "app.services.intelligence.bh_scraper",
    "fetch_bh_product_reviews": "app.services.intelligence.bh_scraper",
    "fetch_bh_reviews": "app.services.intelligence.bh_scraper",
    "normalize_bh_product": "app.services.intelligence.bh_scraper",
    "normalize_bh_review": "app.services.intelligence.bh_scraper",
    "save_bh_snapshot": "app.services.intelligence.bh_repository",
    "get_latest_bh_products": "app.services.intelligence.bh_repository",
    "get_bh_summary": "app.services.intelligence.bh_repository",
    "get_bh_price_history": "app.services.intelligence.bh_repository",
    "get_bh_reviews_summary": "app.services.intelligence.bh_repository",
    "get_bh_top_rated": "app.services.intelligence.bh_repository",
    "select_bh_review_targets": "app.services.intelligence.bh_repository",
    "upsert_bh_reviews": "app.services.intelligence.bh_repository",
    "build_viltrox_overview": "app.services.intelligence.viltrox_matrix",
    "reset_viltrox_official_roster": "app.services.intelligence.viltrox_matrix",
    "scan_viltrox_official_matrix_now": "app.services.intelligence.viltrox_matrix",
}


def test_cold_package_import_loads_no_intelligence_children(tmp_path: Path) -> None:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.fspath(tmp_path),
        "TMPDIR": os.fspath(tmp_path),
        "PYTHONPATH": os.fspath(BACKEND_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "VKPI_SKIP_DOTENV": "1",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import json,sys; import app.services.intelligence as package; "
            "print(json.dumps({'all': package.__all__, 'children': sorted("
            "name for name in sys.modules if name.startswith('app.services.intelligence.')"
            ")}))",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload == {"all": list(EXPECTED_EXPORTS), "children": []}


def test_legacy_exports_and_explicit_submodule_import_keep_identity() -> None:
    package = importlib.import_module("app.services.intelligence")
    assert package.__all__ == list(EXPECTED_EXPORTS)
    for name, module_name in EXPECTED_EXPORTS.items():
        assert getattr(package, name) is getattr(importlib.import_module(module_name), name)

    from app.services.intelligence import account_scan_service

    assert account_scan_service is importlib.import_module(
        "app.services.intelligence.account_scan_service"
    )
