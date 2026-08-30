#!/usr/bin/env python3
"""Controller-side npm audit receipt consumed by no-network candidate verify."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path


TRUSTED_NPM_CANDIDATES = (
    Path("/opt/homebrew/bin/npm"),
    Path("/usr/local/bin/npm"),
    Path("/usr/bin/npm"),
)
TRUSTED_NODE_CANDIDATES = (Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node"), Path("/usr/bin/node"))


def _trusted_npm() -> Path:
    candidates = list(TRUSTED_NPM_CANDIDATES)
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(info.st_mode)
            or not os.access(resolved, os.X_OK)
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            continue
        return resolved
    raise RuntimeError("trusted controller npm is unavailable")


def _trusted_node() -> Path:
    candidates = list(TRUSTED_NODE_CANDIDATES)
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True); info = resolved.stat()
        except FileNotFoundError:
            continue
        if (stat.S_ISREG(info.st_mode) and os.access(resolved, os.X_OK)
                and info.st_uid in {0, os.geteuid()} and not stat.S_IMODE(info.st_mode) & 0o022):
            return resolved
    raise RuntimeError("trusted controller node is unavailable")


def _trusted_npx(npm: Path | None = None) -> Path:
    candidate = (npm or _trusted_npm()).with_name("npx-cli.js").resolve(strict=True)
    info = candidate.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) & 0o022):
        raise RuntimeError("trusted controller npx is unsafe")
    return candidate


def run_trusted_npm_audit(frontend: Path, receipt: Path) -> None:
    npm = _trusted_npm()
    node = _trusted_node()
    lock = frontend / "package-lock.json"
    lock_info = lock.lstat()
    if lock.is_symlink() or not stat.S_ISREG(lock_info.st_mode):
        raise RuntimeError("package lock is unsafe")
    lock_sha = hashlib.sha256(lock.read_bytes()).hexdigest()
    if (frontend / ".npmrc").exists():
        raise RuntimeError("project npm configuration is forbidden for trusted audit")
    done = subprocess.run(
        [str(node), str(npm), "audit", "--omit=dev", "--audit-level=moderate",
         "--registry=https://registry.npmjs.org/"],
        cwd=frontend, stdin=subprocess.DEVNULL, capture_output=True, timeout=300,
        env={"HOME": str(receipt.parent), "LANG": "C", "LC_ALL": "C",
             "PATH": os.defpath, "NPM_CONFIG_AUDIT": "true",
             "NPM_CONFIG_USERCONFIG": str(receipt.parent / "no-user-npmrc"),
             "NPM_CONFIG_GLOBALCONFIG": str(receipt.parent / "no-global-npmrc")},
    )
    payload = {"schema": "vkpi.controller-npm-audit/v1", "passed": done.returncode == 0,
               "package_lock_sha256": lock_sha, "returncode": done.returncode,
               "trust_boundary": "operator-reviewed controller network step"}
    receipt.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    receipt.chmod(0o600)
    if done.returncode != 0:
        raise RuntimeError("controller npm audit failed")
