#!/usr/bin/env python3
"""V-KPI agent boundary and module ownership helper.

Default mode prints live repository statistics and module ownership status.
Boundary mode checks changed files against allowed path prefixes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT_MARKERS = ("backend", "frontend", "scripts", "docs", ".git")
OWNERSHIP = {
    "activities": "app-core",
    "ai": "ai-platform",
    "audit": "governance",
    "auth": "identity-access",
    "cache": "platform-infra",
    "commerce": "commerce",
    "creators": "creator-workflow",
    "ingestion": "data-ingestion",
    "intelligence": "ai-platform",
    "jobs": "platform-infra",
    "kol": "kol-domain",
    "media": "media-domain",
    "memory": "ai-platform",
    "monitoring": "ops-monitoring",
    "scheduler": "platform-infra",
    "scoring": "ai-platform",
    "scraping": "data-ingestion",
    "security": "identity-access",
    "system": "platform-infra",
    "via": "vos-future-isolate",
    "vkpi": "vkpi-core",
    "deepsight": "future-isolate",
    "party": "future-isolate",
    "rewards": "future-isolate",
    "verification": "identity-access",
}
UNKNOWN_CANDIDATES = ["student_identity", "trust"]


def repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() and all((candidate / m).exists() for m in ("backend", "frontend")):
            return candidate
    return current


def git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=str(cwd), text=True, stderr=subprocess.STDOUT)


def changed_files(root: Path, diff_base: str | None, staged: bool) -> list[str]:
    if staged:
        out = git(["diff", "--cached", "--name-only"], root)
    elif diff_base:
        out = git(["diff", "--name-only", diff_base], root)
    else:
        out = git(["status", "--short"], root)
        files = []
        for line in out.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path)
        return files
    return [line.strip() for line in out.splitlines() if line.strip()]


def starts_with_any(path: str, prefixes: Iterable[str]) -> bool:
    normalized = path.replace("\\", "/")
    for prefix in prefixes:
        p = prefix.strip().replace("\\", "/")
        if not p:
            continue
        if normalized == p or normalized.startswith(p.rstrip("/") + "/"):
            return True
    return False


def live_stats(root: Path) -> dict:
    services_dir = root / "backend" / "app" / "services"
    vkpi_dir = services_dir / "vkpi"
    router_dir = root / "backend" / "app" / "api" / "routers"
    service_dirs = sorted(p.name for p in services_dir.iterdir() if p.is_dir() and not p.name.startswith("__")) if services_dir.exists() else []
    vkpi_top_py = sorted(p.name for p in vkpi_dir.glob("*.py") if p.name != "__init__.py") if vkpi_dir.exists() else []
    routers = sorted(p.name for p in router_dir.glob("*.py") if p.name != "__init__.py") if router_dir.exists() else []
    vkpi_routers = sorted(p.name for p in router_dir.glob("vkpi*.py") if p.name != "__init__.py") if router_dir.exists() else []
    module_status = []
    for name in service_dirs:
        owner = OWNERSHIP.get(name, "unknown-needs-decision")
        module_status.append({"module": name, "owner": owner, "status": "unknown" if owner.startswith("unknown") else "owned"})
    existing_unknown_candidates = []
    for name in UNKNOWN_CANDIDATES:
        found = bool(list(root.rglob(name)))
        if found or name in service_dirs:
            existing_unknown_candidates.append(name)
    unknown_modules = sorted({m["module"] for m in module_status if m["status"] == "unknown"} | set(existing_unknown_candidates))
    return {
        "repo_root": str(root),
        "services_subdir_count": len(service_dirs),
        "vkpi_top_level_py_count": len(vkpi_top_py),
        "routers_count": len(routers),
        "vkpi_routers_count": len(vkpi_routers),
        "unknown_modules": unknown_modules,
        "module_status": module_status,
    }


def print_markdown(stats: dict) -> None:
    print("# V-KPI Live Module Ownership Stats")
    print()
    print(f"repo_root: `{stats['repo_root']}`")
    print(f"services_subdir_count: `{stats['services_subdir_count']}`")
    print(f"vkpi_top_level_py_count: `{stats['vkpi_top_level_py_count']}`")
    print(f"routers_count: `{stats['routers_count']}`")
    print(f"vkpi_routers_count: `{stats['vkpi_routers_count']}`")
    print()
    print("## Unknown Modules")
    if stats["unknown_modules"]:
        for item in stats["unknown_modules"]:
            print(f"- {item}")
    else:
        print("- none")
    print()
    print("## Module Ownership")
    print("| module | owner | status |")
    print("|---|---|---|")
    for item in stats["module_status"]:
        print(f"| {item['module']} | {item['owner']} | {item['status']} |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify V-KPI agent boundaries and print live module stats.")
    parser.add_argument("--root", default=".", help="Repository root or any path inside it.")
    parser.add_argument("--json", action="store_true", help="Print stats as JSON.")
    parser.add_argument("--allowed", action="append", default=[], help="Allowed path prefix. Repeatable.")
    parser.add_argument("--diff-base", help="Git diff base for boundary check, e.g. origin/main...HEAD or HEAD~1.")
    parser.add_argument("--staged", action="store_true", help="Check staged files only.")
    parser.add_argument("--fail-on-unknown", action="store_true", help="Exit non-zero if unknown modules exist.")
    args = parser.parse_args()

    root = repo_root(Path(args.root))
    stats = live_stats(root)

    if args.allowed:
        files = changed_files(root, args.diff_base, args.staged)
        violations = [f for f in files if not starts_with_any(f, args.allowed)]
        print(f"changed_files: {len(files)}")
        print(f"allowed_prefixes: {args.allowed}")
        if violations:
            print("BOUNDARY_VIOLATION")
            for path in violations:
                print(path)
            return 2
        print("BOUNDARY_OK")
        return 0

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print_markdown(stats)

    if args.fail_on_unknown and stats["unknown_modules"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
