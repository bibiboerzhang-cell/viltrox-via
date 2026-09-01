#!/usr/bin/env python3
"""Temp-file-free helpers used by the canonical static shell gate."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit

SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
from stdout_utils import out  # noqa: E402


LINE_GUARD_ALLOWLIST: frozenset[str] = frozenset()
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


def _candidate_controller_receipt_module(root: Path) -> object:
    """Load the manifest-bound receipt contract without weakening ``-I``.

    The canonical CLI runs this file directly, so isolated Python intentionally
    omits the repository root from ``sys.path``.  Add only the physical candidate
    that contains this exact helper, then prove the imported contract came from
    that same candidate rather than an ambient package or ``PYTHONPATH`` entry.
    """

    helper = Path(__file__)
    if not helper.is_absolute():
        helper = Path.cwd() / helper
    expected_helper = root / "scripts/verify_static_gate_helpers.py"
    expected_module = root / "scripts/ops/controller_static_receipt.py"
    try:
        if (
            helper.is_symlink()
            or helper.resolve(strict=True) != expected_helper.resolve(strict=True)
            or expected_module.is_symlink()
            or not expected_module.is_file()
        ):
            raise SystemExit("controller static receipt helper root mismatch")
    except OSError as exc:
        raise SystemExit("controller static receipt helper root mismatch") from exc

    root_text = str(root)
    inserted = root_text not in sys.path
    if inserted:
        sys.path.insert(0, root_text)
    try:
        module = importlib.import_module("scripts.ops.controller_static_receipt")
    finally:
        if inserted:
            sys.path.remove(root_text)
    module_file = getattr(module, "__file__", None)
    if (
        not isinstance(module_file, str)
        or Path(module_file).resolve(strict=True) != expected_module.resolve(strict=True)
    ):
        raise SystemExit("controller static receipt helper import mismatch")
    return module


def validate_npm_audit_receipt(receipt: Path, lock: Path) -> None:
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    if (
        payload.get("schema") != "vkpi.controller-npm-audit/v1"
        or payload.get("passed") is not True
        or payload.get("returncode") != 0
        or payload.get("package_lock_sha256")
        != hashlib.sha256(lock.read_bytes()).hexdigest()
    ):
        raise SystemExit("trusted npm audit receipt mismatch")


def check_release_line_guard(root: Path) -> None:
    from check_line_guard import DEFAULT_ROOTS, collect_violations

    previous = Path.cwd()
    os.chdir(root)
    try:
        observed = collect_violations(DEFAULT_ROOTS, limit=1000, no_tests=False)
    finally:
        os.chdir(previous)
    violations = [
        item for item in observed if item.path not in LINE_GUARD_ALLOWLIST
    ]
    exempted = [
        item for item in observed if item.path in LINE_GUARD_ALLOWLIST
    ]
    for item in exempted:
        out(
            f"[verify] 千行卫兵(白名单豁免,待还债): "
            f"{item.lines:>5} {item.path}"
        )
    if violations:
        out("[verify] 千行卫兵 FAIL:以下文件 >1000 行且不在既有债白名单(新文件零豁免):")
        for item in violations:
            out(f"[verify]   {item.lines:>5} {item.path}")
        raise SystemExit(1)
    out(
        "[verify] 千行卫兵 OK:无新增 >1000 行文件"
        f"(白名单剩余 {len(exempted)} 个待还。)"
    )


def validate_controller_static_receipt(
    receipt: Path,
    root: Path,
    runtime_root: Path,
    expected_digest: str,
    expected_head: str,
    expected_branch: str,
    expected_receipt_sha256: str,
    runtime_nonce: str,
    runtime_ports: str,
    health_url: str,
    base_url: str,
) -> None:
    try:
        ports = tuple(int(item) for item in runtime_ports.split(","))
        health_port = urlsplit(health_url).port
        base_port = urlsplit(base_url).port
    except (TypeError, ValueError) as exc:
        raise SystemExit("strict runtime receipt binding is invalid") from exc
    if (
        not re.fullmatch(r"[0-9a-f]{64}", runtime_nonce)
        or not ports
        or ports != tuple(sorted(set(ports)))
        or any(port == 8102 or not 1 <= port <= 65535 for port in ports)
        or health_port is None
        or health_port != base_port
        or health_port not in ports
    ):
        raise SystemExit("strict runtime receipt binding is invalid")
    runtime_info = runtime_root.lstat()
    controller = runtime_root / "controller"
    controller_info = controller.lstat()
    if (
        runtime_root.is_symlink()
        or not stat.S_ISDIR(runtime_info.st_mode)
        or runtime_info.st_uid != os.geteuid()
        or stat.S_IMODE(runtime_info.st_mode) != 0o700
        or controller.is_symlink()
        or not stat.S_ISDIR(controller_info.st_mode)
        or controller_info.st_uid != os.geteuid()
        or stat.S_IMODE(controller_info.st_mode) & 0o077
        or receipt.parent.resolve() != controller.resolve()
    ):
        raise SystemExit("controller static receipt runtime boundary mismatch")
    info = receipt.lstat()
    if (
        receipt.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise SystemExit("controller static receipt is not protected")
    descriptor = os.open(
        receipt,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise SystemExit("controller static receipt identity changed")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    receipt_bytes = b"".join(chunks)
    if (
        (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(receipt_bytes) != opened.st_size
        or re.fullmatch(r"[0-9a-f]{64}", expected_receipt_sha256) is None
        or hashlib.sha256(receipt_bytes).hexdigest() != expected_receipt_sha256
    ):
        raise SystemExit("controller static receipt hash mismatch")
    payload = json.loads(receipt_bytes.decode("utf-8", "strict"))
    candidate = payload.get("candidate")
    source = payload.get("source")
    identity = payload.get("build_identity")
    canonical = payload.get("canonical_receipt")
    canonical_candidate = canonical.get("candidate") if isinstance(canonical, dict) else None
    nonce = payload.get("nonce")
    if (
        payload.get("schema") != "vkpi.controller-static-gate/v1"
        or payload.get("passed") is not True
        or not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{64}", nonce) is None
        or not isinstance(candidate, dict)
        or candidate.get("content_sha256") != expected_digest
        or candidate.get("snapshot_path") != str(root)
        or candidate.get("verify_script_sha256")
        != hashlib.sha256((root / "scripts/verify.sh").read_bytes()).hexdigest()
        or not isinstance(source, dict)
        or source.get("head") != expected_head
        or source.get("branch") != expected_branch
        or source.get("worktree_dirty") is not False
        or identity
        != {
            "build_time": (root / "BUILD_TIME").read_text(encoding="utf-8").strip(),
            "git_branch": expected_branch,
            "git_sha": expected_head,
        }
        or (root / "BUILD_GIT_SHA").read_text(encoding="utf-8").strip()
        != expected_head
        or (root / "BUILD_GIT_BRANCH").read_text(encoding="utf-8").strip()
        != expected_branch
        or not isinstance(canonical, dict)
        or not isinstance(canonical_candidate, dict)
        or canonical_candidate.get("release_head") != expected_head
        or canonical_candidate.get("git_head") != expected_head
        or canonical_candidate.get("branch") != expected_branch
        or canonical_candidate.get("clean_worktree") is not True
        or canonical_candidate.get("dirty_path_count") != 0
        or payload.get("canonical_receipt_sha256")
        != hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        or payload.get("canonical_step_plan_sha256")
        != hashlib.sha256(
            json.dumps(
                canonical.get("steps"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    ):
        raise SystemExit("controller static receipt binding mismatch")
    contract = _candidate_controller_receipt_module(root)
    freeze_error = contract.FreezeError
    try:
        contract.validate_outer_static_partial(canonical)
        toolchain = payload.get("toolchain")
        if not isinstance(toolchain, dict):
            raise freeze_error("controller static receipt toolchain binding is missing")
        expected_python = contract.assert_trusted_file_identity(
            toolchain.get("python"), label="python"
        )
        contract._validate_nested_seatbelt_tests(
            payload.get("nested_seatbelt_tests"),
            snapshot=root,
            expected_python=expected_python,
        )
    except freeze_error as exc:
        raise SystemExit("controller static receipt binding mismatch") from exc
    steps = canonical.get("steps")
    if (
        not isinstance(steps, list)
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
        or canonical.get("verification")
        != {
            "runtime": "not_requested",
            "acceptance": "not_requested",
            "browser_console": "not_requested",
            "runtime_log_canary": "not_requested",
        }
    ):
        raise SystemExit("controller static receipt step proof mismatch")
    out("[verify] controller-bound canonical static receipt passed.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("npm-audit-receipt")
    audit.add_argument("receipt", type=Path)
    audit.add_argument("lock", type=Path)
    line_guard = subparsers.add_parser("line-guard")
    line_guard.add_argument("root", type=Path)
    static_receipt = subparsers.add_parser("static-receipt")
    static_receipt.add_argument("receipt", type=Path)
    static_receipt.add_argument("root", type=Path)
    static_receipt.add_argument("runtime_root", type=Path)
    static_receipt.add_argument("expected_digest")
    static_receipt.add_argument("expected_head")
    static_receipt.add_argument("expected_branch")
    static_receipt.add_argument("expected_receipt_sha256")
    static_receipt.add_argument("runtime_nonce")
    static_receipt.add_argument("runtime_ports")
    static_receipt.add_argument("health_url")
    static_receipt.add_argument("base_url")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "npm-audit-receipt":
        validate_npm_audit_receipt(args.receipt, args.lock)
    elif args.command == "line-guard":
        check_release_line_guard(args.root.resolve())
    elif args.command == "static-receipt":
        validate_controller_static_receipt(
            args.receipt,
            args.root.resolve(),
            args.runtime_root,
            args.expected_digest,
            args.expected_head,
            args.expected_branch,
            args.expected_receipt_sha256,
            args.runtime_nonce,
            args.runtime_ports,
            args.health_url,
            args.base_url,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
