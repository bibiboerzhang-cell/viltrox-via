#!/usr/bin/env python3
"""Audit V-KPI route guards and data-scope coverage.

This is intentionally read-only. It is a guardrail for P3.10C: prove which
routes have an auth/permission guard, which public routes are intentionally
unguarded, and where data-scope helpers are used.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROUTER_DIR = ROOT / "backend" / "app" / "api" / "routers"
SERVICE_DIR = ROOT / "backend" / "app" / "services" / "vkpi"

ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
GUARD_TOKENS = (
    "require_tab(",
    "require_permission(",
    "require_admin",
    "get_current_user",
)
SCOPE_TOKENS = (
    "scope.",
    "ScopeDenied",
    "project_filter(",
    "link_filter(",
    "staff_filter(",
    "row_staff_filter(",
    "assert_project_access(",
    "assert_link_access(",
    "assert_staff_access(",
    "assert_kol_access(",
)

# Public redirect and webhooks are not staff-session endpoints. The webhook
# services verify provider signatures instead of using staff RBAC.
PUBLIC_ALLOWLIST = {
    ("vkpi.py", "redirect_link"),
    ("vkpi.py", "shopify_order_webhook"),
    ("vkpi.py", "shopify_refund_webhook"),
}


@dataclass
class EndpointFinding:
    file: str
    function: str
    router: str
    method: str
    path: str
    line: int
    guarded: bool
    public_allowlisted: bool


def _source_segment(lines: list[str], node: ast.AST) -> str:
    start = max(0, getattr(node, "lineno", 1) - 1)
    end = max(start + 1, getattr(node, "end_lineno", start + 1))
    return "".join(lines[start:end])


def _route_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, str, str]]:
    routes: list[tuple[str, str, str]] = []
    for deco in node.decorator_list:
        if not isinstance(deco, ast.Call):
            continue
        func = deco.func
        if not isinstance(func, ast.Attribute) or func.attr not in ROUTE_METHODS:
            continue
        router = ""
        if isinstance(func.value, ast.Name):
            router = func.value.id
        path = ""
        if deco.args and isinstance(deco.args[0], ast.Constant):
            path = str(deco.args[0].value)
        routes.append((router, func.attr.upper(), path))
    return routes


def _scan_router_file(path: Path) -> list[EndpointFinding]:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    findings: list[EndpointFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = _route_decorators(node)
        if not routes:
            continue
        segment = _source_segment(lines, node)
        guarded = any(token in segment for token in GUARD_TOKENS)
        public_allowlisted = (path.name, node.name) in PUBLIC_ALLOWLIST
        for router, method, route_path in routes:
            findings.append(
                EndpointFinding(
                    file=str(path.relative_to(ROOT)),
                    function=node.name,
                    router=router,
                    method=method,
                    path=route_path,
                    line=int(getattr(node, "lineno", 0)),
                    guarded=guarded,
                    public_allowlisted=public_allowlisted,
                )
            )
    return findings


def _scan_services() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(SERVICE_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        rows.append(
            {
                "file": str(path.relative_to(ROOT)),
                "scope_token_count": sum(src.count(token) for token in SCOPE_TOKENS),
                "function_count": len(re.findall(r"^def |^async def ", src, flags=re.M)),
            }
        )
    return rows


def audit() -> dict[str, Any]:
    endpoints: list[EndpointFinding] = []
    for path in sorted(ROUTER_DIR.glob("vkpi*.py")):
        endpoints.extend(_scan_router_file(path))

    unguarded = [
        item
        for item in endpoints
        if not item.guarded and not item.public_allowlisted
    ]
    public = [item for item in endpoints if item.public_allowlisted]
    services = _scan_services()
    scoped_services = [row for row in services if row["scope_token_count"] > 0]

    return {
        "status": "pass" if not unguarded else "fail",
        "endpoint_count": len(endpoints),
        "guarded_endpoint_count": sum(1 for item in endpoints if item.guarded),
        "public_allowlisted_count": len(public),
        "unguarded_endpoint_count": len(unguarded),
        "unguarded_endpoints": [asdict(item) for item in unguarded],
        "public_allowlisted_endpoints": [asdict(item) for item in public],
        "service_file_count": len(services),
        "scoped_service_file_count": len(scoped_services),
        "service_scope_summary": services,
    }


def main() -> int:
    result = audit()
    if "--json" in sys.argv:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status={result['status']}")
        print(f"endpoints={result['endpoint_count']}")
        print(f"guarded={result['guarded_endpoint_count']}")
        print(f"public_allowlisted={result['public_allowlisted_count']}")
        print(f"unguarded={result['unguarded_endpoint_count']}")
        if result["unguarded_endpoints"]:
            print("unguarded endpoints:")
            for item in result["unguarded_endpoints"]:
                print(f"- {item['file']}:{item['line']} {item['method']} {item['path']} -> {item['function']}")
        print(f"scoped_services={result['scoped_service_file_count']}/{result['service_file_count']}")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
