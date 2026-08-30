#!/usr/bin/env python3
"""Temp-file-free helpers used by the canonical static shell gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
from stdout_utils import out  # noqa: E402


LINE_GUARD_ALLOWLIST: frozenset[str] = frozenset()


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("npm-audit-receipt")
    audit.add_argument("receipt", type=Path)
    audit.add_argument("lock", type=Path)
    line_guard = subparsers.add_parser("line-guard")
    line_guard.add_argument("root", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "npm-audit-receipt":
        validate_npm_audit_receipt(args.receipt, args.lock)
    elif args.command == "line-guard":
        check_release_line_guard(args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
