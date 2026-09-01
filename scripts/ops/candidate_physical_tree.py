#!/usr/bin/env python3
"""Fail-closed physical tree admission for frozen candidate artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator, Mapping

from scripts.ops.freeze_worktree_contract import FreezeError, path_identity
from scripts.ops.strict_runtime_seatbelt import SeatbeltError, trusted_user_home


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


def _read_bound_file(source: Path, expected: Mapping[str, object]) -> tuple[bytes, int]:
    size = expected.get("size_bytes")
    raw_mode = expected.get("mode")
    mode = (
        int(raw_mode, 8)
        if isinstance(raw_mode, str) and re.fullmatch(r"[0-7]{4}", raw_mode)
        else -1
    )
    digest = expected.get("sha256")
    if (
        not isinstance(size, int)
        or size < 0
        or not 0 <= mode <= 0o777
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise FreezeError("candidate verification mirror manifest is invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(source, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_size != size
            or stat.S_IMODE(before.st_mode) != mode
        ):
            raise FreezeError("candidate verification mirror source changed")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or after.st_uid != os.geteuid()
        or after.st_nlink != 1
        or stat.S_IMODE(after.st_mode) != mode
        or len(data) != size
        or hashlib.sha256(data).hexdigest() != digest
    ):
        raise FreezeError("candidate verification mirror source bytes changed")
    return data, mode


def _copy_bound_file(source: Path, target: Path, expected: Mapping[str, object]) -> None:
    data, mode = _read_bound_file(source, expected)
    target.parent.mkdir(parents=True, exist_ok=True)
    output = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        mode,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(output, view)
            if written <= 0:
                raise FreezeError("candidate verification mirror copy made no progress")
            view = view[written:]
        os.fchmod(output, mode)
        os.fsync(output)
    finally:
        os.close(output)


def _assert_candidate_payloads(root: Path, expected_files: list[dict[str, object]]) -> None:
    assert_candidate_physical_tree_bound(root, expected_files)
    for expected in expected_files:
        relative = _safe_manifest_path(str(expected["path"]))
        _read_bound_file(root / relative, expected)


def _assert_directory_identity(
    path: Path, expected: tuple[int, int], *, label: str,
) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise FreezeError(f"{label} identity is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or path_identity(path) != expected
    ):
        raise FreezeError(f"{label} identity changed")


@contextmanager
def candidate_verification_mirror(
    source: Path,
    expected_files: list[dict[str, object]],
) -> Iterator[tuple[Path, dict[str, object]]]:
    """Yield a physical, byte-bound Phase-A mirror outside protected source data."""

    candidate_digest = hashlib.sha256(
        json.dumps(
            expected_files, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    proof: dict[str, object] = {
        "status": "in_progress",
        "copy_method": "independent_physical_files",
        "file_count": len(expected_files),
        "candidate_digest_before": candidate_digest,
        "mirror_digest_before": candidate_digest,
        "candidate_digest_after": "",
        "mirror_digest_after": "",
    }
    _assert_candidate_payloads(source, expected_files)
    try:
        protected_parent = trusted_user_home()
    except SeatbeltError as exc:
        raise FreezeError("candidate verification mirror has no stable parent") from exc
    sandbox = Path(
        tempfile.mkdtemp(prefix=".vkpi-phase-a-seatbelt.", dir=protected_parent)
    ).resolve()
    sandbox.chmod(0o700)
    sandbox_identity = path_identity(sandbox)
    mirror = sandbox / "candidate"
    mirror.mkdir(mode=0o700)
    mirror_identity = path_identity(mirror)
    try:
        for expected in expected_files:
            relative = _safe_manifest_path(str(expected["path"]))
            _copy_bound_file(source / relative, mirror / relative, expected)
        _assert_candidate_payloads(mirror, expected_files)
        try:
            yield mirror, proof
        finally:
            _assert_candidate_payloads(source, expected_files)
            _assert_candidate_payloads(mirror, expected_files)
            proof.update(
                {
                    "status": "passed",
                    "candidate_digest_after": candidate_digest,
                    "mirror_digest_after": candidate_digest,
                }
            )
    finally:
        _assert_directory_identity(
            sandbox, sandbox_identity, label="candidate verification sandbox"
        )
        _assert_directory_identity(
            mirror, mirror_identity, label="candidate verification mirror"
        )
        if (
            sandbox.parent != protected_parent
            or not sandbox.name.startswith(".vkpi-phase-a-seatbelt.")
        ):
            raise FreezeError("candidate verification sandbox parent changed")
        shutil.rmtree(sandbox)
