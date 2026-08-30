#!/usr/bin/env python3
"""Target selection for core-mutation receipts (pure functions, no subprocess).

Extracted verbatim from ``vkpi_engineering_health_mutation.py`` (2026-08-31) to
keep the runner under the 700-line new-file guard; the runner re-exports every
name so callers and tests keep their original import paths.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class MutationRunError(RuntimeError):
    """Raised when a mutation run cannot be performed honestly (moved with block)."""


# 常量随段落搬入(runner 侧 re-export 保原 import 路径与 CLI help)
CORE_SCOPE_GROUPS: dict[str, tuple[str, ...]] = {
    "kol_search": (
        "backend/app/domains/kol/",
        "backend/app/domains/discovery/",
        "backend/app/domains/search/",
    ),
    "gemini_video_analysis": ("backend/app/services/ai/",),
    "collaboration_followup": ("backend/app/domains/projects/", "backend/app/domains/launch/"),
}
SMOKE_FILE_COUNT = 5
MIN_SCORED_FILES = 30
MAX_SCORED_FILES = 50
MAX_MATCHED_TESTS_PER_FILE = 8

def group_prefixes(groups: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Resolve and validate the requested core-scope group names."""
    if not groups:
        raise MutationRunError("at least one core scope group is required")
    resolved: dict[str, tuple[str, ...]] = {}
    for name in groups:
        if name not in CORE_SCOPE_GROUPS:
            raise MutationRunError(f"unknown core scope group: {name}")
        if name in resolved:
            raise MutationRunError(f"duplicate core scope group: {name}")
        resolved[name] = CORE_SCOPE_GROUPS[name]
    return resolved


def eligible_targets(
    captured: snapshot.SourceSnapshot, prefixes: dict[str, tuple[str, ...]]
) -> list[snapshot.SourceFile]:
    """Production .py files inside the scope prefixes, deterministic order."""
    flat = tuple(prefix for group in prefixes.values() for prefix in group)
    selected = [
        item for item in captured.files
        if item.relative_path.endswith(".py")
        and not item.relative_path.endswith("/__init__.py")
        and item.relative_path.startswith(flat)
    ]
    if not selected:
        raise MutationRunError("core scope contains no eligible production files")
    return selected


def _test_file_index(root: Path) -> dict[str, str]:
    """Read every candidate test file once; fail loudly on unreadable files."""
    index: dict[str, str] = {}
    for base in ("tests", "backend/tests"):
        directory = root / base
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("test_*.py")):
            relative = path.relative_to(root).as_posix()
            if "__pycache__" in relative:
                continue
            try:
                index[relative] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise MutationRunError(f"cannot read test file {relative}") from exc
    if not index:
        raise MutationRunError("no test files found under tests/ or backend/tests/")
    return index


def match_tests(root: Path, target_paths: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Map each target to test files that mention its module stem (capped)."""
    index = _test_file_index(root)
    matches: dict[str, tuple[str, ...]] = {}
    for target in target_paths:
        stem = Path(target).stem
        pattern = re.compile(r"\b" + re.escape(stem) + r"\b")
        found = [rel for rel, text in index.items() if pattern.search(text)]
        found.sort(key=lambda rel: (0 if stem in Path(rel).name else 1, rel))
        matches[target] = tuple(found[:MAX_MATCHED_TESTS_PER_FILE])
    return matches


def _explicit_targets(eligible: Sequence[snapshot.SourceFile], explicit: Sequence[str]) -> list[str]:
    known = {item.relative_path for item in eligible}
    unknown = sorted(set(explicit) - known)
    if unknown:
        raise MutationRunError(f"files outside the core scope: {unknown[:5]}")
    if len(set(explicit)) != len(explicit):
        raise MutationRunError("explicit target files contain duplicates")
    return sorted(explicit)


def _smoke_targets(
    eligible: Sequence[snapshot.SourceFile], matches: dict[str, tuple[str, ...]]
) -> list[str]:
    with_tests = [item for item in eligible if matches[item.relative_path]]
    if len(with_tests) < SMOKE_FILE_COUNT:
        raise MutationRunError("not enough matched-test files for a smoke run")
    with_tests.sort(key=lambda item: (item.physical_lines, item.relative_path))
    return sorted(item.relative_path for item in with_tests[:SMOKE_FILE_COUNT])


def _scored_targets(
    eligible: Sequence[snapshot.SourceFile],
    matches: dict[str, tuple[str, ...]],
    max_files: int,
) -> list[str]:
    if not MIN_SCORED_FILES <= max_files <= MAX_SCORED_FILES:
        raise MutationRunError(f"scored runs need {MIN_SCORED_FILES}..{MAX_SCORED_FILES} files")
    ranked = sorted(
        eligible,
        key=lambda item: (0 if matches[item.relative_path] else 1, item.relative_path),
    )
    return sorted(item.relative_path for item in ranked[:max_files])


def select_targets(
    eligible: Sequence[snapshot.SourceFile],
    matches: dict[str, tuple[str, ...]],
    *,
    mode: str,
    max_files: int,
    explicit: Sequence[str],
) -> list[str]:
    """Deterministic target selection for the requested mode."""
    if explicit:
        return _explicit_targets(eligible, explicit)
    if mode == "smoke":
        return _smoke_targets(eligible, matches)
    return _scored_targets(eligible, matches, max_files)
