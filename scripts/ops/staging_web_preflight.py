#!/usr/bin/env python3
"""Fail-closed identity gate for the isolated scheduler-off staging Web."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import stat
import sys
from pathlib import Path

if __package__:
    from .atomic_release_integrity import verify_sealed_release
    from .atomic_release_units import LayoutError
    from .staging_db_clone import (
        CloneError,
        clone_name_for_release,
        env_state,
        validate_clone_name,
    )
else:
    from atomic_release_integrity import verify_sealed_release
    from atomic_release_units import LayoutError
    from staging_db_clone import (
        CloneError,
        clone_name_for_release,
        env_state,
        validate_clone_name,
    )


SHARED_ALIASES = {
    ".env",
    ".env.production",
    ".venv",
    "backups",
    "creator_profiles",
    "frames",
    "runtime",
    "submissions.db",
    "uploads",
}


class StagingWebPreflightError(RuntimeError):
    """The staging Web filesystem or database identity is unsafe."""


def _safe_directory(path: Path, *, owner_uid: int, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StagingWebPreflightError(f"{label} is missing") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise StagingWebPreflightError(f"{label} must be a real directory")
    if info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise StagingWebPreflightError(f"{label} ownership or mode is unsafe")


def _safe_environment(path: Path, *, owner_uid: int) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StagingWebPreflightError("staging environment is missing") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise StagingWebPreflightError(
            "staging environment must be a regular non-symlink file"
        )
    if info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise StagingWebPreflightError("staging environment ownership or mode is unsafe")
    if not os.access(path, os.R_OK):
        raise StagingWebPreflightError("staging environment is not readable by the app")


def validate_staging_web_root(
    *,
    root: Path,
    app_user: str,
    expected_owner_uid: int,
    expected_owner_gid: int,
    env_owner_uid: int,
) -> dict[str, str | int]:
    try:
        current_user = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError as exc:
        raise StagingWebPreflightError("staging process identity is unknown") from exc
    if current_user != app_user:
        raise StagingWebPreflightError("staging preflight must run as the app user")

    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise StagingWebPreflightError("staging root is missing") from exc
    releases = root / "releases"
    _safe_directory(root, owner_uid=env_owner_uid, label="staging root")
    _safe_directory(releases, owner_uid=expected_owner_uid, label="releases directory")
    _safe_environment(root / ".env", owner_uid=env_owner_uid)

    current = root / "current"
    if not current.is_symlink():
        raise StagingWebPreflightError("atomic current pointer is required")
    try:
        release = current.resolve(strict=True)
    except OSError as exc:
        raise StagingWebPreflightError("atomic current pointer is broken") from exc
    if release.parent != releases.resolve(strict=True) or not release.is_dir():
        raise StagingWebPreflightError("atomic current pointer escapes releases")

    manifest = verify_sealed_release(
        root,
        release,
        shared_aliases=SHARED_ALIASES,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    release_id = str(manifest.get("release_id") or "")
    target_database = str(manifest.get("target_database") or "")
    validate_clone_name(target_database)
    strategy = manifest.get("database_strategy")
    if strategy == "staging-clone":
        database_owner_release_id = release_id
    elif strategy == "reuse-active-clone":
        database_owner_release_id = str(
            manifest.get("database_owner_release_id") or ""
        )
        pending = manifest.get("pending_migrations") or []
        compatible = manifest.get("forward_compatible_migrations") or []
        if not isinstance(pending, list) or not isinstance(compatible, list) or pending != compatible:
            raise StagingWebPreflightError(
                "clone-reuse staging migrations lack an exact declaration"
            )
    else:
        raise StagingWebPreflightError(
            "staging release must own or reuse a release-specific database clone"
        )
    if clone_name_for_release(database_owner_release_id) != target_database:
        raise StagingWebPreflightError("release and clone identities do not match")

    environment = env_state(root / ".env")
    if environment["database_name"] != target_database:
        raise StagingWebPreflightError(
            "staging environment and release database identities do not match"
        )
    return {
        "release_id": release_id,
        "payload_sha256": str(manifest.get("payload_sha256") or ""),
        "database_identity_sha256": hashlib.sha256(
            target_database.encode("utf-8")
        ).hexdigest(),
        "environment_sha256": environment["env_sha256"],
        "scheduler_enabled": 0,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--root", required=True)
    result.add_argument("--app-user", required=True)
    result.add_argument("--expected-owner-uid", type=int, required=True)
    result.add_argument("--expected-owner-gid", type=int, required=True)
    result.add_argument("--env-owner-uid", type=int, required=True)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        receipt = validate_staging_web_root(
            root=Path(args.root),
            app_user=args.app_user,
            expected_owner_uid=args.expected_owner_uid,
            expected_owner_gid=args.expected_owner_gid,
            env_owner_uid=args.env_owner_uid,
        )
        sys.stdout.write(json.dumps(receipt, sort_keys=True) + "\n")
    except (CloneError, LayoutError, OSError, StagingWebPreflightError, ValueError) as exc:
        sys.stderr.write(f"staging web preflight failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
