#!/usr/bin/env python3
"""Read-only V-KPI role/scope audit.

P4.2 is not a permissions rewrite. This script gives us a repeatable gate for
the current state:
- every admin V-KPI router endpoint must have an auth/permission dependency;
- service functions that accept staff and read scoped business tables are
  listed when they do not visibly call the scope helpers.

The service scan is intentionally advisory because SQL can be built through
shared helpers. Endpoint guard failures are hard failures.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROUTER_DIR = ROOT / "backend" / "app" / "api" / "routers"
SERVICE_DIR = ROOT / "backend" / "app" / "services" / "vkpi"

ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
ADMIN_PREFIX_MARKERS = {"/api/admin/vkpi", "admin_router_prefix("}
GUARD_MARKERS = (
    "require_tab(",
    "require_permission(",
    "get_current_user",
    "require_admin",
    "Depends(require_tab",
    "Depends(require_permission",
)
SCOPE_MARKERS = (
    "scope.",
    "ScopeDenied",
    "assert_",
    "effective_staff_id",
    "scope_context",
    "project_filter",
    "link_filter",
    "row_staff_filter",
    "staff_filter",
    "_require_manager_staff",
    "_is_manager_staff",
)
SENSITIVE_TABLES = (
    "kols",
    "vkpi_projects",
    "vkpi_links",
    "vkpi_messages",
    "vkpi_content_posts",
    "vkpi_shipments",
    "vkpi_deliverables",
    "vkpi_kol_pool",
)


@dataclass
class EndpointFinding:
    file: str
    function: str
    methods: list[str]
    paths: list[str]
    admin_endpoint: bool
    guarded: bool
    has_staff_param: bool
    uses_scope_helper: bool
    calls_service_with_staff: bool


@dataclass
class ServiceWarning:
    file: str
    function: str
    tables: list[str]
    reason: str


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        if not _unparse(node.value.func).endswith("APIRouter"):
            continue
        targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if not targets:
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg != "prefix":
                continue
            if isinstance(kw.value, ast.Constant):
                prefix = str(kw.value.value or "")
            else:
                prefix = _unparse(kw.value)
        for target in targets:
            prefixes[target] = prefix
    return prefixes


def _route_from_decorator(dec: ast.AST, prefixes: dict[str, str]) -> tuple[str, str, bool] | None:
    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
        return None
    method = dec.func.attr
    if method not in ROUTE_METHODS:
        return None
    owner = dec.func.value
    if not isinstance(owner, ast.Name):
        return None
    router_name = owner.id
    prefix = prefixes.get(router_name, "")
    route_path = ""
    if dec.args and isinstance(dec.args[0], ast.Constant):
        route_path = str(dec.args[0].value or "")
    full_path = f"{prefix.rstrip('/')}/{route_path.lstrip('/')}" if route_path else prefix
    admin_endpoint = any(marker in prefix for marker in ADMIN_PREFIX_MARKERS)
    return method.upper(), full_path or route_path, admin_endpoint


def _function_source(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def _has_arg(node: ast.FunctionDef | ast.AsyncFunctionDef, names: set[str]) -> bool:
    args = list(node.args.args) + list(node.args.kwonlyargs)
    return any(arg.arg in names for arg in args)


def scan_routers() -> dict[str, Any]:
    endpoints: list[EndpointFinding] = []
    for path in sorted(ROUTER_DIR.glob("vkpi*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        prefixes = _router_prefixes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            routes = [_route_from_decorator(dec, prefixes) for dec in node.decorator_list]
            routes = [route for route in routes if route is not None]
            if not routes:
                continue
            src = _function_source(source, node)
            endpoints.append(
                EndpointFinding(
                    file=str(path.relative_to(ROOT)),
                    function=node.name,
                    methods=sorted({route[0] for route in routes}),
                    paths=[route[1] for route in routes],
                    admin_endpoint=any(route[2] for route in routes),
                    guarded=any(marker in src for marker in GUARD_MARKERS),
                    has_staff_param=_has_arg(node, {"staff", "current_staff", "user", "current_user"}),
                    uses_scope_helper=any(marker in src for marker in SCOPE_MARKERS),
                    calls_service_with_staff=bool(re.search(r"\bstaff\s*=", src)),
                )
            )
    admin_endpoints = [item for item in endpoints if item.admin_endpoint]
    unguarded = [item for item in admin_endpoints if not item.guarded]
    no_staff_context = [item for item in admin_endpoints if item.guarded and not item.has_staff_param]
    return {
        "routers_scanned": len(list(ROUTER_DIR.glob("vkpi*.py"))),
        "total_route_handlers": len(endpoints),
        "admin_route_handlers": len(admin_endpoints),
        "guarded_admin_route_handlers": len([item for item in admin_endpoints if item.guarded]),
        "unguarded_admin_endpoints": [asdict(item) for item in unguarded],
        "admin_endpoints_without_staff_param": [asdict(item) for item in no_staff_context],
        "endpoints": [asdict(item) for item in endpoints],
    }


def _function_nodes(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def scan_services() -> dict[str, Any]:
    warnings: list[ServiceWarning] = []
    files = sorted(SERVICE_DIR.glob("*.py"))
    for path in files:
        if path.name in {"scope.py", "__init__.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in _function_nodes(tree):
            if not _has_arg(node, {"staff", "current_staff"}):
                continue
            src = _function_source(source, node)
            if "SELECT" not in src.upper():
                continue
            tables = sorted({table for table in SENSITIVE_TABLES if re.search(rf"\b{re.escape(table)}\b", src)})
            if not tables:
                continue
            if any(marker in src for marker in SCOPE_MARKERS):
                continue
            warnings.append(
                ServiceWarning(
                    file=str(path.relative_to(ROOT)),
                    function=node.name,
                    tables=tables,
                    reason="staff-aware SELECT touches scoped table but no visible scope helper was found",
                )
            )
    return {
        "services_scanned": len(files),
        "advisory_scope_warnings": [asdict(item) for item in warnings],
        "advisory_scope_warning_count": len(warnings),
    }


def run_audit() -> dict[str, Any]:
    routers = scan_routers()
    services = scan_services()
    ok = not routers["unguarded_admin_endpoints"]
    return {
        "marker": "VKPI_SCOPE_AUDIT",
        "ok": ok,
        "summary": {
            "routers_scanned": routers["routers_scanned"],
            "admin_route_handlers": routers["admin_route_handlers"],
            "unguarded_admin_endpoint_count": len(routers["unguarded_admin_endpoints"]),
            "admin_endpoints_without_staff_param_count": len(routers["admin_endpoints_without_staff_param"]),
            "services_scanned": services["services_scanned"],
            "advisory_scope_warning_count": services["advisory_scope_warning_count"],
        },
        "routers": routers,
        "services": services,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit V-KPI admin endpoint guards and advisory service scope usage.")
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    parser.add_argument("--fail-on-advisory", action="store_true", help="Return non-zero when advisory service warnings exist.")
    args = parser.parse_args()

    report = run_audit()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("VKPI_SCOPE_AUDIT", json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
        if report["routers"]["unguarded_admin_endpoints"]:
            print("UNGUARDED_ADMIN_ENDPOINTS", json.dumps(report["routers"]["unguarded_admin_endpoints"], ensure_ascii=False, sort_keys=True))
        if report["services"]["advisory_scope_warnings"]:
            print("ADVISORY_SCOPE_WARNINGS", json.dumps(report["services"]["advisory_scope_warnings"][:20], ensure_ascii=False, sort_keys=True))
    if not report["ok"]:
        return 1
    if args.fail_on_advisory and report["services"]["advisory_scope_warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
