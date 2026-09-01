#!/usr/bin/env python3
"""Controller-owned static gate receipt creation and admission."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path

from scripts.ops.freeze_phase_runtime import (
    PHASE_A_NESTED_SEATBELT_TEST_COUNT,
    PHASE_A_NESTED_SEATBELT_TEST_FILES,
    PHASE_A_NESTED_SEATBELT_TESTS,
)
from scripts.ops.freeze_worktree_contract import BuildIdentity, FreezeError


CANONICAL_STATIC_STEP_PLAN = (
    "release candidate worktree (required for deploy)",
    "frontend contracts are checked in and current",
    "frontend i18n dictionary + missing-English ratchet",
    "frontend production dependency security audit (moderate+)",
    "silent exception baseline",
    "repo hardening + reviewed warning ratchet",
    "alembic heads",
    "Python compile (in-memory; no bytecode writes)",
    "backend pytest",
    "frontend vitest",
    "frontend tsc --noEmit",
    "frontend isolated production build + chunk graph/bundle budget guards",
    "redline grep (viltrox_fit_score write)",
    "line guard >1000 (zero allowlist)",
    "runtime trust (not requested static-gate mode)",
    "local release acceptance (skipped in static-gate mode)",
    "browser console live extension-free release gate (not requested)",
    "post-restart runtime log leak canary (not requested)",
)
CONTROLLER_STATIC_RECEIPT_RUNTIME_STEP_PLAN = (
    "controller-bound canonical static receipt",
    "frontend isolated production build + chunk graph/bundle budget guards",
    "runtime trust (required)",
    "local release acceptance (all required GETs)",
    "browser console live extension-free release gate (not requested)",
    "post-restart runtime log leak canary (not requested)",
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_bound_regular_file(
    path: Path,
    *,
    label: str,
    required_mode: int | None = None,
) -> bytes:
    """Read one owner-controlled file through one no-follow descriptor."""

    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise FreezeError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or (
                required_mode is not None
                and stat.S_IMODE(before.st_mode) != required_mode
            )
        ):
            raise FreezeError(f"{label} is not a protected regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise FreezeError(f"{label} changed while being read")
    data = b"".join(chunks)
    if len(data) != before.st_size:
        raise FreezeError(f"{label} changed size while being read")
    return data


def trusted_file_identity(path: Path) -> dict[str, object]:
    """Bind a controller tool by physical path and immutable file attributes."""

    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) & 0o022
        or info.st_nlink != 1
    ):
        raise FreezeError(f"controller tool is unsafe: {resolved}")
    return {
        "path": str(resolved),
        "sha256": _sha256_path(resolved),
        "size_bytes": info.st_size,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "nlink": info.st_nlink,
    }


def assert_trusted_file_identity(record: object, *, label: str) -> Path:
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise FreezeError(f"static receipt {label} identity is missing")
    try:
        observed = trusted_file_identity(Path(record["path"]))
    except (OSError, RuntimeError) as exc:
        raise FreezeError(f"static receipt {label} tool is unavailable") from exc
    if observed != record:
        raise FreezeError(f"static receipt {label} tool identity changed")
    return Path(str(observed["path"]))


def _validate_nested_seatbelt_tests(record: object, *, snapshot: Path) -> None:
    if not isinstance(record, dict):
        raise FreezeError("nested Seatbelt test proof is missing")
    if (
        record.get("test_files") != list(PHASE_A_NESTED_SEATBELT_TEST_FILES)
        or record.get("file_counts") != dict(PHASE_A_NESTED_SEATBELT_TESTS)
        or record.get("expected_count") != PHASE_A_NESTED_SEATBELT_TEST_COUNT
    ):
        raise FreezeError("nested Seatbelt test proof binding mismatch")
    if record.get("status") == "not_present_fixture":
        if any((snapshot / relative).exists() for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES):
            raise FreezeError("nested Seatbelt tests were not executed")
        return
    command = record.get("command")
    if (
        record.get("status") != "passed"
        or record.get("exit_code") != 0
        or record.get("collected_count") != PHASE_A_NESTED_SEATBELT_TEST_COUNT
        or record.get("passed_count") != PHASE_A_NESTED_SEATBELT_TEST_COUNT
        or not isinstance(command, list)
        or command[1:] != [
            "-B", "-m", "pytest", "-q", *PHASE_A_NESTED_SEATBELT_TEST_FILES,
        ]
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(record.get(name, ""))) is None
            for name in (
                "collect_log_sha256", "run_log_sha256",
                "candidate_digest_before", "candidate_digest_after",
                "source_digest_before", "source_digest_after",
            )
        )
        or record.get("candidate_digest_before") != record.get("candidate_digest_after")
        or record.get("source_digest_before") != record.get("source_digest_after")
    ):
        raise FreezeError("nested Seatbelt test proof is invalid")


def _validate_verification_mirror(
    record: object, *, candidate_digest: object, candidate_file_count: object,
) -> None:
    if (
        not isinstance(record, dict)
        or not isinstance(candidate_digest, str)
        or not isinstance(candidate_file_count, int)
    ):
        raise FreezeError("Phase A verification mirror proof is missing")
    if (
        record.get("status") != "passed"
        or record.get("copy_method") != "independent_physical_files"
        or record.get("file_count") != candidate_file_count
        or candidate_file_count <= 0
        or any(
            record.get(name) != candidate_digest
            for name in (
                "candidate_digest_before", "mirror_digest_before",
                "candidate_digest_after", "mirror_digest_after",
            )
        )
    ):
        raise FreezeError("Phase A verification mirror proof is invalid")


def controller_static_receipt_payload(
    *,
    output: Path,
    snapshot: Path,
    candidate_digest: str,
    candidate_file_count: int,
    source_digest: str,
    source_file_count: int,
    source_status_sha256: str,
    source_dirty: bool,
    identity: BuildIdentity,
    verify_log: Path,
    static_gate_run: dict[str, object],
) -> dict[str, object]:
    canonical = static_gate_run.get("canonical_receipt")
    toolchain = static_gate_run.get("toolchain")
    if not isinstance(canonical, dict) or not isinstance(toolchain, dict):
        raise FreezeError("canonical static gate evidence is incomplete")
    canonical_candidate = canonical.get("candidate")
    if (
        not isinstance(canonical_candidate, dict)
        or canonical_candidate.get("release_head") != identity.git_sha
        or canonical_candidate.get("git_head") != identity.git_sha
        or canonical_candidate.get("branch") != identity.git_branch
        or canonical_candidate.get("clean_worktree") is not True
        or canonical_candidate.get("dirty_path_count") != 0
    ):
        raise FreezeError("canonical static gate Git identity mismatch")
    for name in ("git", "node", "npm", "npx", "python"):
        assert_trusted_file_identity(toolchain.get(name), label=name)
    nested_seatbelt_tests = static_gate_run.get("nested_seatbelt_tests")
    _validate_nested_seatbelt_tests(nested_seatbelt_tests, snapshot=snapshot)
    verification_mirror = static_gate_run.get("verification_mirror")
    _validate_verification_mirror(
        verification_mirror,
        candidate_digest=candidate_digest,
        candidate_file_count=candidate_file_count,
    )
    return {
        "schema": "vkpi.controller-static-gate/v1",
        "nonce": secrets.token_hex(32),
        "passed": True,
        "candidate": {
            "content_sha256": candidate_digest,
            "snapshot_path": str(output),
            "verify_script_sha256": _sha256_path(snapshot / "scripts/verify.sh"),
        },
        "source": {
            "branch": identity.git_branch,
            "content_sha256": source_digest,
            "file_count": source_file_count,
            "head": identity.git_sha,
            "status_sha256": source_status_sha256,
            "worktree_dirty": source_dirty,
        },
        "build_identity": identity.payload(),
        "canonical_receipt": canonical,
        "canonical_receipt_sha256": hashlib.sha256(
            _canonical_bytes(canonical)
        ).hexdigest(),
        "canonical_step_plan_sha256": hashlib.sha256(
            _canonical_bytes(canonical.get("steps"))
        ).hexdigest(),
        "verify_log": {
            "path": str(verify_log),
            "sha256": _sha256_path(verify_log),
        },
        "nested_seatbelt_tests": nested_seatbelt_tests,
        "verification_mirror": verification_mirror,
        "toolchain": toolchain,
    }


def validate_controller_static_receipt(
    *,
    manifest: dict[str, object],
    snapshot: Path,
) -> tuple[dict[str, object], bytes]:
    verification = manifest.get("verification")
    record = verification.get("static_receipt") if isinstance(verification, dict) else None
    if not isinstance(record, dict):
        raise FreezeError("deploy requires a controller static gate receipt")
    raw_path = record.get("path")
    expected_path = snapshot.with_suffix(snapshot.suffix + ".static-receipt.json")
    if not isinstance(raw_path, str) or Path(raw_path).resolve() != expected_path.resolve():
        raise FreezeError("controller static receipt path binding mismatch")
    data = read_bound_regular_file(
        Path(raw_path),
        label="controller static receipt",
        required_mode=0o600,
    )
    if hashlib.sha256(data).hexdigest() != record.get("sha256"):
        raise FreezeError("controller static receipt artifact hash mismatch")
    try:
        payload = json.loads(data.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError("controller static receipt artifact is invalid") from exc
    if payload != record.get("payload") or not isinstance(payload, dict):
        raise FreezeError("controller static receipt manifest binding mismatch")
    candidate = manifest.get("candidate")
    source = manifest.get("source")
    build = manifest.get("build")
    identity = build.get("identity") if isinstance(build, dict) else None
    receipt_candidate = payload.get("candidate")
    receipt_source = payload.get("source")
    candidate_files = candidate.get("files") if isinstance(candidate, dict) else None
    nonce = payload.get("nonce")
    if (
        payload.get("schema") != "vkpi.controller-static-gate/v1"
        or payload.get("passed") is not True
        or not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{64}", nonce) is None
        or not isinstance(candidate, dict)
        or not isinstance(source, dict)
        or not isinstance(identity, dict)
        or not isinstance(receipt_candidate, dict)
        or not isinstance(receipt_source, dict)
        or not isinstance(candidate_files, list)
        or candidate.get("file_count") != len(candidate_files)
        or receipt_candidate.get("content_sha256") != candidate.get("content_sha256")
        or receipt_candidate.get("snapshot_path") != str(snapshot)
        or receipt_candidate.get("verify_script_sha256")
        != _sha256_path(snapshot / "scripts/verify.sh")
        or receipt_source != {
            "branch": source.get("branch"),
            "content_sha256": source.get("content_sha256"),
            "file_count": source.get("file_count"),
            "head": source.get("head"),
            "status_sha256": source.get("status_sha256"),
            "worktree_dirty": source.get("worktree_dirty"),
        }
        or payload.get("build_identity") != identity
    ):
        raise FreezeError("controller static receipt full-source binding mismatch")
    canonical = payload.get("canonical_receipt")
    if not isinstance(canonical, dict):
        raise FreezeError("controller static receipt canonical evidence is missing")
    canonical_candidate = canonical.get("candidate")
    steps = canonical.get("steps")
    if (
        canonical.get("schema_version") != "vkpi_canonical_gate_receipt_v1"
        or canonical.get("passed") is not True
        or canonical.get("failed_steps") != []
        or not isinstance(canonical_candidate, dict)
        or canonical_candidate.get("release_head") != identity.get("git_sha")
        or canonical_candidate.get("git_head") != identity.get("git_sha")
        or canonical_candidate.get("branch") != identity.get("git_branch")
        or canonical_candidate.get("clean_worktree") is not True
        or canonical_candidate.get("dirty_path_count") != 0
        or not isinstance(steps, list)
        or not steps
        or [item.get("name") for item in steps if isinstance(item, dict)]
        != list(CANONICAL_STATIC_STEP_PLAN)
        or any(
            not isinstance(item, dict)
            or item.get("index") != index
            or item.get("status") != "passed"
            or item.get("exit_code") != 0
            for index, item in enumerate(steps, 1)
        )
        or payload.get("canonical_receipt_sha256")
        != hashlib.sha256(_canonical_bytes(canonical)).hexdigest()
        or payload.get("canonical_step_plan_sha256")
        != hashlib.sha256(_canonical_bytes(steps)).hexdigest()
    ):
        raise FreezeError("controller static receipt canonical step proof mismatch")
    if canonical.get("verification") != {
        "runtime": "not_requested",
        "acceptance": "not_requested",
        "browser_console": "not_requested",
        "runtime_log_canary": "not_requested",
    }:
        raise FreezeError("controller static receipt contains non-static claims")
    toolchain = payload.get("toolchain")
    if not isinstance(toolchain, dict):
        raise FreezeError("controller static receipt toolchain binding is missing")
    for name in ("git", "node", "npm", "npx", "python"):
        assert_trusted_file_identity(toolchain.get(name), label=name)
    _validate_nested_seatbelt_tests(
        payload.get("nested_seatbelt_tests"), snapshot=snapshot
    )
    _validate_verification_mirror(
        payload.get("verification_mirror"),
        candidate_digest=receipt_candidate.get("content_sha256"),
        candidate_file_count=candidate.get("file_count"),
    )
    log_record = payload.get("verify_log")
    if not isinstance(log_record, dict) or log_record.get("path") != verification.get("log_path"):
        raise FreezeError("controller static receipt log binding mismatch")
    log_bytes = read_bound_regular_file(
        Path(str(log_record.get("path", ""))),
        label="controller static receipt verify log",
        required_mode=0o600,
    )
    if (
        hashlib.sha256(log_bytes).hexdigest() != log_record.get("sha256")
        or log_record.get("sha256") != verification.get("log_sha256")
    ):
        raise FreezeError("controller static receipt log hash mismatch")
    return payload, data
