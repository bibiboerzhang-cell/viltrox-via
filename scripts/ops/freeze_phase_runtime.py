#!/usr/bin/env python3
"""Bounded process logging and cleanup for candidate freeze phases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Sequence, TypeVar

from scripts.ops.freeze_worktree_contract import FreezeError, path_identity


PHASE_A_NESTED_SEATBELT_TESTS = (
    ("tests/test_strict_runtime_hardening_redteam.py", 32),
    ("tests/test_deploy_runtime_admission.py", 5),
    ("tests/test_freeze_worktree_candidate.py", 22),
    ("tests/test_phase_a_static_containment.py", 1),
)
PHASE_A_NESTED_SEATBELT_TEST_FILES = tuple(
    relative for relative, _count in PHASE_A_NESTED_SEATBELT_TESTS
)
PHASE_A_NESTED_SEATBELT_TEST_COUNT = sum(
    count for _relative, count in PHASE_A_NESTED_SEATBELT_TESTS
)
_InventoryEntry = TypeVar("_InventoryEntry")


def inventory_map_digest(
    inventories: Mapping[str, Sequence[_InventoryEntry]],
    entry_digest: Callable[[Sequence[_InventoryEntry]], str],
) -> str:
    payload = {root: entry_digest(entries) for root, entries in inventories.items()}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def bind_nested_inventory_proof(
    proof: dict[str, object], *,
    candidate_before: Sequence[_InventoryEntry],
    candidate_after: Sequence[_InventoryEntry],
    sources_before: Mapping[str, Sequence[_InventoryEntry]],
    sources_after: Mapping[str, Sequence[_InventoryEntry]],
    entry_digest: Callable[[Sequence[_InventoryEntry]], str],
) -> None:
    if candidate_after != candidate_before or sources_after != sources_before:
        raise FreezeError("nested Seatbelt tests changed source bytes")
    proof.update(
        {
            "candidate_digest_before": entry_digest(candidate_before),
            "candidate_digest_after": entry_digest(candidate_after),
            "source_digest_before": inventory_map_digest(sources_before, entry_digest),
            "source_digest_after": inventory_map_digest(sources_after, entry_digest),
        }
    )


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


def run_nested_seatbelt_tests(
    *, snapshot: Path, python_bin: Path, env: dict[str, str], runtime_root: Path,
    error_log_path: Path, failure_log_path: Path,
    failure_log_identity: tuple[int, int],
) -> dict[str, object]:
    """Run the one fixed suite that Darwin cannot nest below another profile."""

    present = [
        (snapshot / relative).is_file() and not (snapshot / relative).is_symlink()
        for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES
    ]
    if not any(present):
        return {
            "status": "not_present_fixture",
            "test_files": list(PHASE_A_NESTED_SEATBELT_TEST_FILES),
            "file_counts": dict(PHASE_A_NESTED_SEATBELT_TESTS),
            "expected_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
        }
    if not all(present):
        raise FreezeError("nested Seatbelt test suite is incomplete")
    test_env = dict(env)
    test_env["PYTHONPATH"] = os.pathsep.join(
        (str(snapshot), str(snapshot / "scripts"), str(snapshot / "backend"))
    )
    collect_log = runtime_root / "nested-seatbelt-collect.log"
    run_log = runtime_root / "nested-seatbelt-run.log"
    base = [str(python_bin), "-B", "-m", "pytest"]
    collect_command = [
        *base, "--collect-only", "-q", *PHASE_A_NESTED_SEATBELT_TEST_FILES,
    ]
    try:
        run_logged(
            collect_command, cwd=snapshot, env=test_env, log_path=collect_log,
            error_log_path=error_log_path,
        )
    except BaseException:
        publish_owned_log(collect_log, failure_log_path, failure_log_identity)
        raise
    node_ids = [
        line for line in collect_log.read_text(encoding="utf-8").splitlines()
        if any(
            line.startswith(relative + "::")
            for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES
        )
    ]
    observed_counts = {
        relative: sum(node_id.startswith(relative + "::") for node_id in node_ids)
        for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES
    }
    if (
        observed_counts != dict(PHASE_A_NESTED_SEATBELT_TESTS)
        or len(node_ids) != PHASE_A_NESTED_SEATBELT_TEST_COUNT
        or len(set(node_ids)) != len(node_ids)
    ):
        publish_owned_log(collect_log, failure_log_path, failure_log_identity)
        raise FreezeError("nested Seatbelt test collection count mismatch")
    command = [*base, "-q", *PHASE_A_NESTED_SEATBELT_TEST_FILES]
    try:
        run_logged(
            command, cwd=snapshot, env=test_env, log_path=run_log,
            error_log_path=error_log_path,
        )
    except BaseException:
        publish_owned_log(run_log, failure_log_path, failure_log_identity)
        raise
    summary = run_log.read_text(encoding="utf-8")
    expected_summary = rf"(?m)^{PHASE_A_NESTED_SEATBELT_TEST_COUNT} passed(?:, \d+ warnings?)? in "
    if re.search(expected_summary, summary) is None:
        publish_owned_log(run_log, failure_log_path, failure_log_identity)
        raise FreezeError("nested Seatbelt test pass count mismatch")
    return {
        "status": "passed",
        "test_files": list(PHASE_A_NESTED_SEATBELT_TEST_FILES),
        "file_counts": observed_counts,
        "command": command,
        "exit_code": 0,
        "collected_count": len(node_ids),
        "passed_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
        "expected_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
        "collect_log_sha256": hashlib.sha256(collect_log.read_bytes()).hexdigest(),
        "run_log_sha256": hashlib.sha256(run_log.read_bytes()).hexdigest(),
    }


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
    allowed_parents = {
        Path("/private/tmp").resolve(strict=True),
        Path("/private/var/tmp").resolve(strict=True),
    }
    parent_info = physical.parent.lstat()
    if (
        root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or physical.parent not in allowed_parents
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != 0
        or not parent_info.st_mode & stat.S_ISVTX
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
