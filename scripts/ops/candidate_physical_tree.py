#!/usr/bin/env python3
"""Fail-closed physical tree admission for frozen candidate artifacts."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath
from typing import Mapping

from scripts.ops.freeze_worktree_contract import FreezeError


def manifest_files_excluding(
    payload: Mapping[str, object], excluded_paths: frozenset[str]
) -> list[dict[str, object]]:
    candidate = payload.get("candidate")
    files = candidate.get("files") if isinstance(candidate, Mapping) else None
    if not isinstance(files, list):
        raise FreezeError("candidate file inventory is missing")
    result: list[dict[str, object]] = []
    for raw in files:
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
            raise FreezeError("candidate file inventory is invalid")
        if raw["path"] not in excluded_paths:
            result.append(dict(raw))
    return sorted(result, key=lambda item: str(item["path"]))


def _safe_manifest_path(raw: str) -> str:
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts:
        raise FreezeError(f"unsafe candidate manifest path: {raw!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise FreezeError(f"unsafe candidate manifest path: {raw!r}")
    return path.as_posix()


def assert_candidate_physical_tree_bound(
    root: Path,
    expected_files: object,
) -> None:
    """Reject every physical node not represented by the candidate manifest."""

    if not isinstance(expected_files, list):
        raise FreezeError("candidate physical tree manifest is missing")
    file_paths: set[str] = set()
    directory_paths: set[str] = set()
    for item in expected_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise FreezeError("candidate physical tree manifest is invalid")
        relative = _safe_manifest_path(item["path"])
        if relative in file_paths:
            raise FreezeError("candidate physical tree manifest has duplicate paths")
        file_paths.add(relative)
        parent = PurePosixPath(relative).parent
        while parent.parts:
            directory_paths.add(parent.as_posix())
            parent = parent.parent

    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FreezeError("candidate physical tree cannot be scanned") from exc
        for entry in entries:
            relative = Path(entry.path).relative_to(root).as_posix()
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise FreezeError(
                    f"candidate physical node cannot be inspected: {relative}"
                ) from exc
            if stat.S_ISDIR(info.st_mode):
                observed_directories.add(relative)
                pending.append(Path(entry.path))
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise FreezeError(
                        f"candidate physical tree contains hard-linked file: {relative}"
                    )
                observed_files.add(relative)
            else:
                raise FreezeError(
                    f"candidate physical tree contains unmanifested node: {relative}"
                )
    unexpected = sorted(
        (observed_files - file_paths)
        | (observed_directories - directory_paths)
        | (file_paths - observed_files)
        | (directory_paths - observed_directories)
    )
    if unexpected:
        raise FreezeError(
            "candidate physical tree differs from manifest: "
            + ", ".join(unexpected[:10])
        )
