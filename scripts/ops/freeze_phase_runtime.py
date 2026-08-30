#!/usr/bin/env python3
"""Bounded process logging and cleanup for candidate freeze phases."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Sequence

from scripts.ops.freeze_worktree_contract import FreezeError, path_identity


def physical_special_paths(root: Path) -> list[str]:
    """List unsupported physical nodes without following candidate symlinks."""

    special: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FreezeError(
                f"candidate physical tree cannot be scanned: {directory}"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise FreezeError(
                    f"candidate physical node cannot be inspected: {path}"
                ) from exc
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                special.append(path.relative_to(root).as_posix())
    return sorted(special)


def run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    error_log_path: Path | None = None,
) -> None:
    with log_path.open("wb") as log:
        from scripts.ops.controlled_candidate_process import run_controlled_candidate

        proc = run_controlled_candidate(
            list(command),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=1200,
        )
    if proc.returncode != 0:
        raise FreezeError(
            f"command failed with exit {proc.returncode}; "
            f"inspect {error_log_path or log_path}"
        )


def publish_owned_log(
    sandbox_log: Path,
    destination: Path,
    expected_identity: tuple[int, int],
) -> None:
    """Copy a reaped candidate log into its pre-created controller inode."""

    info = sandbox_log.lstat()
    if (
        sandbox_log.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_size > 64 * 1024 * 1024
    ):
        raise FreezeError("candidate phase log is unsafe")
    data = sandbox_log.read_bytes()
    if len(data) != info.st_size:
        raise FreezeError("candidate phase log changed while reading")
    flags = os.O_WRONLY | os.O_TRUNC
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags)
    try:
        target = os.fstat(descriptor)
        if (
            (target.st_dev, target.st_ino) != expected_identity
            or not stat.S_ISREG(target.st_mode)
        ):
            raise FreezeError("candidate phase log destination identity changed")
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FreezeError("candidate phase log write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if path_identity(destination) != expected_identity:
        raise FreezeError("candidate phase log destination changed after write")


def remove_owned_phase_sandbox(root: Path) -> None:
    """Remove one controller-owned phase sandbox, including read-only fixtures."""

    physical = root.resolve(strict=True)
    info = root.lstat()
    private_tmp = Path("/private/tmp").resolve(strict=True)
    if (
        root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or physical.parent != private_tmp
        or not physical.name.startswith("vkpi-phase-a-seatbelt.")
    ):
        raise FreezeError("refusing unsafe phase sandbox cleanup")

    def restore_tree_permission(function: object, raw_path: str, _error: object) -> None:
        target = Path(raw_path).absolute()
        try:
            target.relative_to(physical)
        except ValueError as exc:
            raise FreezeError("phase sandbox cleanup escaped its root") from exc
        for candidate in (target.parent, target):
            try:
                candidate_info = candidate.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(candidate_info.st_mode) and not candidate.is_symlink():
                os.chmod(candidate, 0o700)
        function(raw_path)  # type: ignore[operator]

    shutil.rmtree(physical, onexc=restore_tree_permission)
