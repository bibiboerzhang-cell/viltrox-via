#!/usr/bin/env python3
"""Controller-owned identity, receipt, and local-attestation helpers."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.ops.freeze_worktree_candidate import verify_manifest


class StrictRuntimeGateError(RuntimeError):
    """Fail-closed isolated runtime controller error."""


CONTROLLER_STATIC_RECEIPT_RUNTIME_STEP_PLAN = (
    "controller-bound canonical static receipt",
    "frontend isolated production build + chunk graph/bundle budget guards",
    "runtime trust (required)",
    "local release acceptance (all required GETs)",
    "browser console live extension-free release gate (not requested)",
    "post-restart runtime log leak canary (not requested)",
)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def phase_candidate_identity(candidate: Path, phase: Mapping[str, object]) -> dict[str, object]:
    record, bridge = phase.get("candidate"), phase.get("provenance_bridge")
    if not isinstance(record, Mapping) or not isinstance(bridge, Mapping):
        raise StrictRuntimeGateError("Phase A candidate identity evidence is missing")
    manifest = candidate.with_suffix(candidate.suffix + ".manifest.json")
    verified = verify_manifest(argparse.Namespace(manifest=str(manifest), snapshot=str(candidate)))
    expected = {
        "content_sha256": record.get("candidate_content_sha256"),
        "file_count": record.get("candidate_file_count"),
        "manifest_sha256": record.get("manifest_sha256"),
        "snapshot_path": record.get("snapshot_path"), "git_head": bridge.get("git_head"),
        "git_tree": bridge.get("git_tree"), "branch": bridge.get("branch"),
        "capsule_digest": bridge.get("capsule_content_bridge_sha256"),
    }
    if (verified.get("content_sha256") != expected["content_sha256"]
            or verified.get("file_count") != expected["file_count"]
            or sha256_path(manifest) != expected["manifest_sha256"]
            or str(candidate.resolve()) != str(Path(str(expected["snapshot_path"])).resolve())
            or any(not value for value in expected.values())):
        raise StrictRuntimeGateError("candidate differs from Phase A identity")
    return expected


def expected_receipt_plan(
    root: Path, *, controller_static_receipt: bool = False
) -> tuple[list[str], list[str]]:
    verify_source = (root / "scripts/verify.sh").read_text(encoding="utf-8")
    declared = re.findall(
        r'^(run_step|run_static_step) "([^"]+)"',
        verify_source,
        flags=re.MULTILINE,
    )
    if controller_static_receipt:
        required_literals = (
            'run_step "controller-bound canonical static receipt"',
            'run_step "frontend isolated production build + chunk graph/bundle budget guards"',
            'run_step "$RUNTIME_STEP_NAME" runtime_sha_aligned',
            'run_step "$ACCEPTANCE_STEP_NAME" local_release_acceptance_gate',
            'run_step "$BROWSER_CONSOLE_STEP_NAME" browser_console_release_gate',
            'run_step "$RUNTIME_LOG_CANARY_STEP_NAME" runtime_log_canary_gate',
        )
        if any(verify_source.count(literal) != 1 for literal in required_literals):
            raise StrictRuntimeGateError(
                "controller static receipt runtime plan drifted"
            )
        steps = list(CONTROLLER_STATIC_RECEIPT_RUNTIME_STEP_PLAN)
    else:
        steps = [
            name
            for command, name in declared
            if command == "run_static_step"
            or (command == "run_step" and name != "controller-bound canonical static receipt")
        ]
    dynamic = {
        "$RUNTIME_STEP_NAME": "runtime trust (required)",
        "$ACCEPTANCE_STEP_NAME": "local release acceptance (all required GETs)",
        "$BROWSER_CONSOLE_STEP_NAME": "browser console live extension-free release gate (not requested)",
        "$RUNTIME_LOG_CANARY_STEP_NAME": "post-restart runtime log leak canary (not requested)",
    }
    steps = [dynamic.get(name, name) for name in steps]
    acceptance_source = (root / "scripts/local_release_acceptance.py").read_text(encoding="utf-8")
    endpoints = re.findall(r'\bE\("([^"]+)"', acceptance_source)
    minimum_steps = len(CONTROLLER_STATIC_RECEIPT_RUNTIME_STEP_PLAN) if controller_static_receipt else 10
    if len(steps) < minimum_steps or len(endpoints) < 10 or len(set(steps)) != len(steps) \
            or len(set(endpoints)) != len(endpoints):
        raise StrictRuntimeGateError("controller receipt plan is incomplete or ambiguous")
    return steps, endpoints


def validate_bound_receipts(*, verify_path: Path, acceptance_path: Path,
                            expected_head: str, expected_branch: str, base_url: str,
                            expected_steps: list[str], expected_endpoints: list[str],
                            runtime_nonce: str, runtime_ports: str,
                            candidate_digest: str,
                            static_receipt_sha256: str,
                            manifest_sha256: str) -> dict[str, object]:
    try:
        verify_bytes = read_receipt_nofollow(verify_path)
        acceptance_bytes = read_receipt_nofollow(acceptance_path)
        verify = json.loads(verify_bytes.decode("utf-8", "strict"))
        acceptance = json.loads(acceptance_bytes.decode("utf-8", "strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StrictRuntimeGateError("strict runtime receipt is unreadable") from exc
    candidate, verification, steps = (verify.get(name) for name in
                                      ("candidate", "verification", "steps"))
    if (verify.get("schema_version") != "vkpi_canonical_gate_receipt_v1"
            or verify.get("passed") is not True or not isinstance(candidate, dict)
            or candidate.get("release_head") != expected_head
            or candidate.get("git_head") != expected_head
            or candidate.get("branch") != expected_branch
            or candidate.get("clean_worktree") is not True
            or candidate.get("dirty_path_count") != 0
            or not isinstance(verification, dict)
            or verification.get("runtime") != "verified"
            or verification.get("acceptance") != "verified"
            or verification.get("browser_console") != "not_requested"
            or verification.get("runtime_log_canary") != "not_requested"
            or not isinstance(steps, list)
            or [item.get("name") for item in steps if isinstance(item, dict)] != expected_steps
            or any(not isinstance(item, dict) or item.get("index") != index
                   or item.get("status") != "passed" or item.get("exit_code") != 0
                   for index, item in enumerate(steps, 1))
            or verify.get("failed_steps") != []):
        raise StrictRuntimeGateError("canonical verify receipt is not strict-green")
    binding = {
        "nonce": runtime_nonce,
        "ports": runtime_ports,
        "candidate_sha256": candidate_digest,
        "static_receipt_sha256": static_receipt_sha256,
        "manifest_sha256": manifest_sha256,
    }
    if verify.get("strict_runtime_binding") != binding:
        raise StrictRuntimeGateError("canonical verify receipt runtime binding mismatch")
    overall, safety, coverage, endpoints = (acceptance.get(name) for name in
                                             ("overall", "safety", "coverage", "endpoints"))
    required = [item for item in endpoints or [] if isinstance(item, dict) and item.get("required")]
    if (acceptance.get("schema_version") != "vkpi.local-release-acceptance.v1"
            or acceptance.get("base_url") != base_url.rstrip("/")
            or not isinstance(acceptance.get("repo"), dict)
            or acceptance["repo"].get("head") != expected_head
            or not isinstance(overall, dict) or overall.get("pass") is not True
            or not isinstance(overall.get("required_total"), int)
            or overall.get("required_total", 0) <= 0
            or overall.get("required_passed") != overall.get("required_total")
            or overall.get("failed_endpoint_ids") != []
            or overall.get("deadline_exhausted") is not False
            or not isinstance(coverage, dict) or coverage.get("missing_board_families") != []
            or not isinstance(safety, dict) or safety.get("loopback_only") is not True
            or safety.get("paid_provider_calls") is not False
            or safety.get("business_record_mutations") is not False
            or safety.get("deadline_exhausted") is not False
            or len(required) != overall.get("required_total")
            or [item.get("id") for item in required] != expected_endpoints
            or any(item.get("pass") is not True for item in required)):
        raise StrictRuntimeGateError("acceptance receipt is not bound strict-green")
    if acceptance.get("strict_runtime_binding") != binding:
        raise StrictRuntimeGateError("acceptance receipt runtime binding mismatch")
    return {
        "verify_sha256": hashlib.sha256(verify_bytes).hexdigest(),
        "acceptance_sha256": hashlib.sha256(acceptance_bytes).hexdigest(),
        "verify_bytes": verify_bytes,
        "acceptance_bytes": acceptance_bytes,
    }


def read_receipt_nofollow(source: Path) -> bytes:
    """Read one controller receipt through one bound descriptor exactly once."""

    source_fd = os.open(
        source,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid():
            raise StrictRuntimeGateError("receipt source is not a trusted regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(source_fd)
    finally:
        os.close(source_fd)
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
        raise StrictRuntimeGateError("receipt source changed while being read")
    data = b"".join(chunks)
    if len(data) != before.st_size:
        raise StrictRuntimeGateError("receipt source size changed while being read")
    return data


def persist_receipt_bytes(target: Path, data: bytes, expected: str) -> None:
    """Persist already-validated receipt bytes without reopening the source."""

    if hashlib.sha256(data).hexdigest() != expected:
        raise StrictRuntimeGateError("validated receipt byte hash mismatch")
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    opened = os.fstat(descriptor)
    target_identity = (opened.st_dev, opened.st_ino)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise StrictRuntimeGateError("receipt copy made no progress")
            view = view[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        try:
            info = target.lstat()
            if not target.is_symlink() and (info.st_dev, info.st_ino) == target_identity:
                target.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    info = target.lstat()
    if target.is_symlink() or (info.st_dev, info.st_ino) != target_identity:
        raise StrictRuntimeGateError("copied receipt target identity changed")
    if sha256_path(target) != expected:
        raise StrictRuntimeGateError("copied receipt hash mismatch")


def copy_receipt_nofollow(source: Path, target: Path, expected: str) -> None:
    data = read_receipt_nofollow(source)
    if hashlib.sha256(data).hexdigest() != expected:
        raise StrictRuntimeGateError("receipt changed before evidence copy")
    persist_receipt_bytes(target, data, expected)


def control_plane_digest(root: Path) -> dict[str, object]:
    roots = ("backend", "tests", "scripts", "frontend/src")
    explicit = ("frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.ts",
                "frontend/vitest.config.ts", "pytest.ini", "requirements.txt")
    excluded = {".git", ".venv", "__pycache__", "node_modules", "runtime", "dist"}
    paths = [path for name in roots for path in (root / name).rglob("*")
             if path.is_file() and not excluded.intersection(path.parts)
             and path.suffix not in {".pyc", ".pyo"}]
    paths += [root / name for name in explicit if (root / name).is_file()]
    entries = []
    for path in sorted(set(paths)):
        if path.is_symlink():
            raise StrictRuntimeGateError("control-plane contains a symlink")
        entries.append((path.relative_to(root).as_posix(), sha256_path(path)))
    encoded = json.dumps(entries, separators=(",", ":"), ensure_ascii=True).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "file_count": len(entries),
            "roots": [*roots, *explicit]}


def sign_attestation(payload: Mapping[str, object], target: Path) -> dict[str, object]:
    private = Ed25519PrivateKey.generate()
    message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    record = {"schema": "vkpi.operator-reviewed-local-attestation/v1", "payload": payload,
              "algorithm": "Ed25519", "public_key_b64": base64.b64encode(public).decode(),
              "signature_b64": base64.b64encode(private.sign(message)).decode(),
              "trust_boundary": "operator-reviewed controller at invocation; not external CI or supply-chain signing"}
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "sha256": sha256_path(target), "public_key_b64": record["public_key_b64"]}
