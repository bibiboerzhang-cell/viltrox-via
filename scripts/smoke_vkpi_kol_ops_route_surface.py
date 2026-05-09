#!/usr/bin/env python3
"""Lock the KOL Ops router surface after D-series refactors.

This smoke is intentionally structural. It prevents future file-splitting work
from accidentally dropping or double-prefixing public KOL Ops endpoints.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


EXPECTED_ROUTE_COUNT = 31

EXPECTED_PATHS = {
    "/api/admin/kol/kols",
    "/api/admin/kol/search/platform",
    "/api/admin/kol/tools/analyze-url",
    "/api/admin/kol/candidates",
    "/api/admin/kol/dashboard/staff-performance",
    "/api/admin/kol/dashboard/staff-activity",
    "/api/admin/kol/dashboard/cross-filter",
    "/api/admin/kol/kols/{kol_id}/outreach",
    "/api/admin/kol/kols/{kol_id}/campaigns",
    "/api/admin/kol/campaigns/{campaign_id}",
    "/api/admin/kol/campaigns/{campaign_id}/content",
    "/api/admin/kol/content",
    "/api/admin/kol/content/{content_id}",
    "/api/admin/kol/content/{content_id}/score",
    "/api/admin/kol/content/{content_id}/analyze-url",
    "/api/admin/kol/content/{content_id}/attribution",
    "/api/admin/kol/content/{content_id}/attribute",
    "/api/admin/kol/kols/{kol_id}/ai-suggestions",
}

EXPECTED_MODULES = {
    "app.api.routers.kol_ops",
    "app.api.routers.kol_ops_schema",
    "app.api.routers.kol_ops_helpers",
    "app.api.routers.kol_ops_dashboard",
    "app.api.routers.kol_ops_content",
}


def main() -> int:
    import importlib

    loaded_modules = {}
    for module_name in sorted(EXPECTED_MODULES):
        loaded_modules[module_name] = importlib.import_module(module_name).__file__

    from app.api.routers.kol_ops import router

    paths = sorted(getattr(route, "path", "") for route in router.routes)
    missing = sorted(EXPECTED_PATHS.difference(paths))
    duplicate_dashboard_paths = [path for path in paths if "/dashboard/dashboard/" in path]

    ok = (
        len(paths) == EXPECTED_ROUTE_COUNT
        and not missing
        and not duplicate_dashboard_paths
    )
    payload = {
        "ok": ok,
        "marker": "VKPI_KOL_OPS_ROUTE_SURFACE_OK" if ok else "VKPI_KOL_OPS_ROUTE_SURFACE_FAIL",
        "route_count": len(paths),
        "expected_route_count": EXPECTED_ROUTE_COUNT,
        "missing": missing,
        "duplicate_dashboard_paths": duplicate_dashboard_paths,
        "modules": loaded_modules,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
