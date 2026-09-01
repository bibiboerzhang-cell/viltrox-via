#!/usr/bin/env python3
"""Bind delegated Phase-A nested-suite evidence to an outer partial gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ops.freeze_phase_runtime import (  # noqa: E402
    phase_a_dependency_proof_is_valid,
    PHASE_A_NESTED_EXECUTION_BOUNDARY,
    PHASE_A_NESTED_SEATBELT_TEST_COUNT,
    PHASE_A_NESTED_SEATBELT_TEST_FILES,
    PHASE_A_NESTED_SEATBELT_TESTS,
    PHASE_A_PYTEST_BOOTSTRAP,
)
from scripts.ops.freeze_worktree_contract import (  # noqa: E402
    FreezeError,
    path_identity,
    write_owned_file_exclusive,
)
from scripts.stdout_utils import out  # noqa: E402


SCHEMA = "vkpi.phase-a-nested-precheck/v1"


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise FreezeError("Phase A nested precheck receipt schema is invalid")
    record = payload.get("nested_seatbelt_tests")
    if not isinstance(record, dict):
        raise FreezeError("Phase A nested precheck proof is missing")
    test_hashes = record.get("test_file_sha256")
    digest_names = (
        "candidate_digest_before", "candidate_digest_after",
        "source_digest_before", "source_digest_after",
    )
    dependency = record.get("dependency_mirror")
    dependency_valid = phase_a_dependency_proof_is_valid(dependency)
    if (
        record.get("status") != "passed"
        or record.get("execution_boundary") != PHASE_A_NESTED_EXECUTION_BOUNDARY
        or record.get("test_files") != list(PHASE_A_NESTED_SEATBELT_TEST_FILES)
        or record.get("file_counts") != dict(PHASE_A_NESTED_SEATBELT_TESTS)
        or record.get("expected_count") != PHASE_A_NESTED_SEATBELT_TEST_COUNT
        or record.get("collected_count") != PHASE_A_NESTED_SEATBELT_TEST_COUNT
        or record.get("passed_count") != PHASE_A_NESTED_SEATBELT_TEST_COUNT
        or record.get("junit_testcase_count") != PHASE_A_NESTED_SEATBELT_TEST_COUNT
        or record.get("junit_failures") != 0
        or record.get("junit_errors") != 0
        or record.get("junit_skipped") != 0
        or record.get("bootstrap_sha256")
        != _sha256(PHASE_A_PYTEST_BOOTSTRAP.encode("utf-8"))
        or not isinstance(test_hashes, dict)
        or set(test_hashes) != set(PHASE_A_NESTED_SEATBELT_TEST_FILES)
        or not all(_is_sha256(value) for value in test_hashes.values())
        or not _is_sha256(record.get("junit_xml_sha256"))
        or not _is_sha256(record.get("run_log_sha256"))
        or not all(_is_sha256(record.get(name)) for name in digest_names)
        or record.get("candidate_digest_before")
        != record.get("candidate_digest_after")
        or record.get("source_digest_before") != record.get("source_digest_after")
        or not dependency_valid
        or not _is_sha256(record.get("candidate_identity_sha256_before"))
        or record.get("candidate_identity_sha256_before")
        != record.get("candidate_identity_sha256_after")
    ):
        raise FreezeError("Phase A nested precheck proof is invalid")
    return payload


def _candidate_test_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES:
        path = root / relative
        before = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > 2 * 1024 * 1024
        ):
            raise FreezeError("Phase A delegated test file is unsafe")
        data = path.read_bytes()
        after = path.lstat()
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in stable):
            raise FreezeError("Phase A delegated test file changed while reading")
        result[relative] = _sha256(data)
    return result


def write_receipt(path: Path, proof: Mapping[str, object]) -> dict[str, str]:
    payload = validate_payload(
        {"schema": SCHEMA, "nested_seatbelt_tests": dict(proof)}
    )
    data = _canonical_bytes(payload)
    identity = write_owned_file_exclusive(path, data)
    os.chmod(path, 0o400)
    if path_identity(path) != identity:
        raise FreezeError("Phase A nested precheck receipt identity changed")
    return {"path": str(path), "sha256": _sha256(data)}


def _read_bound_receipt(path: Path, expected_sha256: str) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FreezeError("Phase A nested precheck receipt is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        immutable = getattr(stat, "UF_IMMUTABLE", 0) | getattr(stat, "SF_IMMUTABLE", 0)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or getattr(before, "st_flags", 0) & immutable
            or before.st_size > 2 * 1024 * 1024
        ):
            raise FreezeError("Phase A nested precheck receipt is unsafe")
        chunks = bytearray()
        while len(chunks) <= before.st_size:
            chunk = os.read(descriptor, before.st_size + 1 - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        data = bytes(chunks)
        after = os.fstat(descriptor)
        path_after = os.stat(path, follow_symlinks=False)
    finally:
        os.close(descriptor)
    stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
        any(getattr(before, name) != getattr(after, name) for name in stable)
        or (path_after.st_dev, path_after.st_ino) != (before.st_dev, before.st_ino)
        or len(data) != before.st_size
        or not _is_sha256(expected_sha256)
        or _sha256(data) != expected_sha256
    ):
        raise FreezeError("Phase A nested precheck receipt binding mismatch")
    try:
        payload = json.loads(data.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError("Phase A nested precheck receipt is invalid JSON") from exc
    return validate_payload(payload)


def validate_delegated_receipt(
    *, path: Path, expected_sha256: str, candidate_root: Path,
) -> dict[str, object]:
    root = candidate_root.resolve(strict=True)
    if candidate_root.is_symlink() or not root.is_dir():
        raise FreezeError("Phase A delegated candidate root is unsafe")
    expected_parent = root.parent / "controller-immutable"
    if path.absolute() != expected_parent / "nested-seatbelt-precheck.json":
        raise FreezeError("Phase A nested precheck receipt path is not controller-bound")
    parent = path.parent
    parent_info = parent.lstat()
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise FreezeError("Phase A nested precheck parent is unsafe")
    payload = _read_bound_receipt(path, expected_sha256)
    record = payload["nested_seatbelt_tests"]
    if record.get("test_file_sha256") != _candidate_test_hashes(root):
        raise FreezeError("Phase A delegated proof does not match candidate tests")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--candidate-root", required=True)
    args = parser.parse_args()
    try:
        validate_delegated_receipt(
            path=Path(args.receipt),
            expected_sha256=args.sha256,
            candidate_root=Path(args.candidate_root),
        )
    except (FreezeError, OSError) as exc:
        out(f"[phase-a-precheck] {exc}", file=sys.stderr)
        return 1
    out(
        f"[phase-a-precheck] delegated {PHASE_A_NESTED_SEATBELT_TEST_COUNT}-test "
        "proof is structurally bound; outer gate remains partial"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
