#!/usr/bin/env python3
"""Compile repository Python sources in memory without creating ``__pycache__``.

``compileall`` is useful in CI, but it writes bytecode into the shared dirty
worktree.  The canonical release gate only needs syntax/compile validation, so
this checker uses Python's built-in ``compile`` against the same source roots.
"""

from __future__ import annotations

import argparse
import sys
import tokenize
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = (ROOT / "backend" / "app", ROOT / "scripts", ROOT / "tests")
IGNORED_PARTS = {".git", ".venv", "__pycache__", "node_modules", "dist"}


def iter_python_files(roots: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            files.add(root)
            continue
        if not root.is_dir():
            raise ValueError(f"compile root does not exist: {root}")
        files.update(
            path
            for path in root.rglob("*.py")
            if not any(part in IGNORED_PARTS for part in path.parts)
        )
    return sorted(files)


def compile_paths(roots: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    for path in iter_python_files(roots):
        try:
            with tokenize.open(path) as handle:
                source = handle.read()
            compile(source, str(path), "exec", dont_inherit=True)
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{path}: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Optional files/directories. Defaults to backend/app, scripts, and tests.",
    )
    args = parser.parse_args()
    roots = tuple(args.paths) if args.paths else DEFAULT_ROOTS
    try:
        errors = compile_paths(roots)
    except ValueError as exc:
        sys.stderr.write(f"[compile-check] {exc}\n")
        return 2
    if errors:
        for error in errors:
            sys.stderr.write(f"[compile-check] {error}\n")
        return 1
    sys.stdout.write(
        f"[compile-check] OK: {len(iter_python_files(roots))} Python files compiled in memory.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
