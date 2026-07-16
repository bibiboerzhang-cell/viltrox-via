#!/usr/bin/env python3
"""Check source files against the V-KPI 800-line guard.

Default mode is report-only because the repo currently has legacy files above
the limit. Use --fail once a domain batch has brought the touched area below
the threshold.
"""

from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import fnmatch
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_LIMIT = 800
DEFAULT_ROOTS = ("backend/app", "frontend/src", "scripts", "tests")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".css"}
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

# These are not business source files and should not drive domain refactors.
DEFAULT_EXCLUDE_GLOBS = (
    "*/viltrox_product_catalog_seed.json",
    "*/generated/*",
    "*/fixtures/*",
)


@dataclass(frozen=True)
class Violation:
    lines: int
    path: str
    owner: str
    category: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots",
        nargs="*",
        default=list(DEFAULT_ROOTS),
        help="Directories or files to scan. Defaults to backend/app frontend/src scripts tests.",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum allowed lines per source file.")
    parser.add_argument("--fail", action="store_true", help="Exit non-zero when violations are found.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Keep test files in the violation set. Tests are included by default roots, but this flag is explicit for CI logs.",
    )
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="Skip tests while measuring production source debt.",
    )
    return parser.parse_args()


def should_skip(path: Path) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return True
    text = path.as_posix()
    return any(fnmatch.fnmatch(text, pattern) for pattern in DEFAULT_EXCLUDE_GLOBS)


def iter_source_files(roots: Iterable[str], *, no_tests: bool = False) -> Iterable[Path]:
    for root_text in roots:
        root = Path(root_text)
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            if no_tests and (path.parts[0] == "tests" or "/tests/" in path.as_posix()):
                continue
            if should_skip(path):
                continue
            yield path


def count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def owner_for(path: Path) -> str:
    parts = path.parts
    text = path.as_posix()
    if text.startswith("frontend/src/components/vkpi/pages/"):
        return "frontend:legacy-pages"
    if text.startswith("frontend/src/components/vkpi/"):
        return "frontend:legacy-vkpi-components"
    if text.startswith("frontend/src/services/"):
        return "frontend:services"
    if text.startswith("frontend/src/domains/"):
        return f"frontend:domain:{parts[3]}" if len(parts) > 3 else "frontend:domains"
    if text.startswith("backend/app/services/vkpi/"):
        return "backend:legacy-vkpi-services"
    if text.startswith("backend/app/domains/"):
        return f"backend:domain:{parts[3]}" if len(parts) > 3 else "backend:domains"
    if text.startswith("backend/app/platform/"):
        return "backend:platform"
    if text.startswith("backend/app/"):
        return "backend:platform-legacy"
    if text.startswith("scripts/"):
        return "scripts"
    if text.startswith("tests/"):
        return "tests"
    return "unassigned"


def category_for(path: Path) -> str:
    suffix = path.suffix
    text = path.as_posix()
    if suffix == ".css":
        return "style"
    if text.startswith("frontend/"):
        return "frontend"
    if text.startswith("backend/"):
        return "backend"
    if text.startswith("tests/"):
        return "test"
    if text.startswith("scripts/"):
        return "script"
    return "source"


def collect_violations(roots: Iterable[str], *, limit: int, no_tests: bool) -> list[Violation]:
    violations: list[Violation] = []
    for path in iter_source_files(roots, no_tests=no_tests):
        lines = count_lines(path)
        if lines > limit:
            violations.append(
                Violation(
                    lines=lines,
                    path=path.as_posix(),
                    owner=owner_for(path),
                    category=category_for(path),
                )
            )
    return sorted(violations, key=lambda item: (-item.lines, item.path))


def render_table(violations: list[Violation], *, limit: int) -> str:
    lines = [
        f"Line guard limit: {limit}",
        f"Violations: {len(violations)}",
        "",
        "| Lines | Owner | Category | Path |",
        "|---:|---|---|---|",
    ]
    for item in violations:
        lines.append(f"| {item.lines} | {item.owner} | {item.category} | `{item.path}` |")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    violations = collect_violations(args.roots, limit=args.limit, no_tests=args.no_tests)
    if args.json:
        payload = {
            "limit": args.limit,
            "violation_count": len(violations),
            "violations": [asdict(item) for item in violations],
        }
        stdout_out(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        stdout_out(render_table(violations, limit=args.limit))
    return 1 if args.fail and violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
