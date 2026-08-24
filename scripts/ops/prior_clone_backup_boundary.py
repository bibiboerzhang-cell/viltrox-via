#!/usr/bin/env python3
"""Read a secret-free, race-resistant prior-clone backup boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import unquote, urlsplit


RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
DATABASE_RE = re.compile(r"^viltrox2_test_release_[0-9a-f]{20}$")


class BoundaryError(RuntimeError):
    """The live backup source no longer matches its proven release lineage."""


def _read_regular(path: Path, *, label: str) -> bytes:
    try:
        initial = path.lstat()
    except FileNotFoundError as exc:
        raise BoundaryError(f"{label} is missing") from exc
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise BoundaryError(f"{label} must be a regular single-link file")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino)
        ):
            raise BoundaryError(f"{label} changed before it could be read")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino, after.st_nlink)
            != (before.st_dev, before.st_ino, before.st_nlink)
            or (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            != (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        ):
            raise BoundaryError(f"{label} changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _environment_database(content: bytes) -> str:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise BoundaryError("shared environment is not UTF-8") from exc
    matches: list[str] = []
    seen: set[str] = set()
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key in seen:
            raise BoundaryError("shared environment contains duplicate keys")
        seen.add(key)
        if key != "DATABASE_URL":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        matches.append(value)
    if len(matches) != 1:
        raise BoundaryError("shared environment must contain one DATABASE_URL")
    try:
        parsed = urlsplit(matches[0])
    except ValueError as exc:
        raise BoundaryError("shared environment DATABASE_URL is invalid") from exc
    database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.fragment
        or not database
        or "/" in database
    ):
        raise BoundaryError("shared environment database identity is invalid")
    return database


def snapshot(
    *,
    root: Path,
    expected_active_release_id: str,
    expected_database_owner_release_id: str,
    expected_database: str,
) -> dict[str, str]:
    for label, value in (
        ("active release id", expected_active_release_id),
        ("database owner release id", expected_database_owner_release_id),
    ):
        if not RELEASE_ID_RE.fullmatch(value) or value in {".", ".."}:
            raise BoundaryError(f"{label} is invalid")
    if not DATABASE_RE.fullmatch(expected_database):
        raise BoundaryError("expected database is not a release clone")
    expected_clone = "viltrox2_test_release_" + hashlib.sha256(
        expected_database_owner_release_id.encode("utf-8")
    ).hexdigest()[:20]
    if expected_database != expected_clone:
        raise BoundaryError("expected database does not belong to its release owner")

    root = root.resolve(strict=True)
    releases = (root / "releases").resolve(strict=True)
    current = root / "current"
    if not current.is_symlink():
        raise BoundaryError("atomic current pointer is missing")
    active = current.resolve(strict=True)
    if active != releases / expected_active_release_id or releases not in active.parents:
        raise BoundaryError("atomic current pointer changed")

    environment = _read_regular(root / ".env", label="shared environment")
    if _environment_database(environment) != expected_database:
        raise BoundaryError("shared environment database changed")
    manifest_content = _read_regular(
        active / ".vkpi-release.json", label="active release manifest"
    )
    try:
        manifest = json.loads(manifest_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundaryError("active release manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise BoundaryError("active release manifest is not an object")
    strategy = manifest.get("database_strategy")
    if strategy == "staging-clone":
        observed_owner = expected_active_release_id
    elif strategy == "reuse-active-clone":
        observed_owner = str(manifest.get("database_owner_release_id") or "")
        pending = manifest.get("pending_migrations") or []
        compatible = manifest.get("forward_compatible_migrations") or []
        if (
            not isinstance(pending, list)
            or not isinstance(compatible, list)
            or pending != compatible
        ):
            raise BoundaryError("active reuse manifest lacks an exact declaration")
    else:
        raise BoundaryError("active release manifest lost its clone strategy")
    if (
        manifest.get("release_id") != expected_active_release_id
        or manifest.get("target_database") != expected_database
        or observed_owner != expected_database_owner_release_id
    ):
        raise BoundaryError("active release manifest lineage changed")
    return {
        "env_sha256": hashlib.sha256(environment).hexdigest(),
        "active_manifest_sha256": hashlib.sha256(manifest_content).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-active-release-id", required=True)
    parser.add_argument("--expected-database-owner-release-id", required=True)
    parser.add_argument("--expected-database", required=True)
    args = parser.parse_args(argv)
    try:
        payload = snapshot(
            root=Path(args.root),
            expected_active_release_id=args.expected_active_release_id,
            expected_database_owner_release_id=args.expected_database_owner_release_id,
            expected_database=args.expected_database,
        )
    except (BoundaryError, OSError, ValueError) as exc:
        sys.stderr.write(f"prior-clone backup boundary failed: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
