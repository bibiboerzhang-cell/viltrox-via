"""Shared contracts and path validation for atomic V-KPI releases."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

if __package__:
    from .atomic_release_units import LayoutError
else:
    from atomic_release_units import LayoutError


RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ENV_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
STAGING_CLONE_RE = re.compile(r"^viltrox2_test_release_[0-9a-f]{20}$")
ROLLBACK_METADATA_SCHEMA = 3
RELEASE_CONTROLLER_NAME = ".release-controller"
ROLLBACK_METADATA_DIGEST_NAME = "metadata.sha256"
CONTROLLER_DIRECTORY_MODE = 0o700
CONTROLLER_FILE_MODE = 0o600
REQUIRED_RELEASE_PATHS = (
    "backend",
    "frontend/dist",
    "migrations",
    "scripts/start_admin.sh",
    "scripts/ops/systemd/viltrox-2.0-test.service",
    "scripts/ops/systemd/vkpi-worker-interactive.service",
    "scripts/ops/systemd/vkpi-worker-bulk@.service",
    "scripts/ops/systemd/vkpi-redis-worker.service",
    "BUILD_GIT_SHA",
    "BUILD_GIT_BRANCH",
    "BUILD_TIME",
)
SHARED_DIRECTORIES = ("runtime", "uploads", "frames", "creator_profiles", "backups")
SHARED_NESTED_DIRECTORIES = ("runtime/job-results",)
WORKER_WRITABLE_DIRECTORIES = ("uploads",)
WORKER_READONLY_DIRECTORIES = ("runtime", "frames", "creator_profiles")
SHARED_REQUIRED = (".env", ".venv")
SHARED_OPTIONAL = (".env.production", "submissions.db")
RELEASE_SHARED_ALIASES = {
    *SHARED_REQUIRED,
    *SHARED_DIRECTORIES,
    *SHARED_OPTIONAL,
}


def _release_clone_name(release_id: str) -> str:
    release_id = _id(release_id)
    return "viltrox2_test_release_" + hashlib.sha256(
        release_id.encode("utf-8")
    ).hexdigest()[:20]


def _database_release_metadata(
    *,
    strategy: str,
    source_database: str,
    target_database: str,
    env_fingerprint_before: str,
    pending_migrations: str,
    compatibility_declaration: str,
    database_owner_release_id: str = "",
) -> dict[str, str | None]:
    if strategy == "in-place":
        if (
            source_database
            or target_database
            or env_fingerprint_before
            or database_owner_release_id
        ):
            raise LayoutError("in-place releases must not declare staging clone metadata")
        return {
            "database_strategy": "in-place",
            "source_database": None,
            "target_database": None,
            "env_fingerprint_before": None,
            "database_owner_release_id": None,
        }
    if strategy == "reuse-active-clone":
        if source_database:
            raise LayoutError("clone-reuse releases must not declare a new source database")
        if not database_owner_release_id:
            raise LayoutError("clone-reuse releases require the database owner release id")
        if not STAGING_CLONE_RE.fullmatch(target_database):
            raise LayoutError("clone-reuse target database is not release-specific")
        if _release_clone_name(database_owner_release_id) != target_database:
            raise LayoutError("clone-reuse owner release does not own the target database")
        if not ENV_FINGERPRINT_RE.fullmatch(env_fingerprint_before):
            raise LayoutError("clone-reuse env fingerprint must be a SHA-256 digest")
        if pending_migrations:
            raise LayoutError("clone-reuse strategy is app-only and forbids migrations")
        if compatibility_declaration:
            raise LayoutError("clone-reuse strategy must not claim forward compatibility")
        return {
            "database_strategy": "reuse-active-clone",
            "source_database": None,
            "target_database": target_database,
            "env_fingerprint_before": env_fingerprint_before,
            "database_owner_release_id": database_owner_release_id,
        }
    if strategy != "staging-clone":
        raise LayoutError(f"unsupported database release strategy: {strategy!r}")
    if database_owner_release_id:
        raise LayoutError("new staging clones must not inherit another database owner")
    if source_database != "viltrox2_test" and not STAGING_CLONE_RE.fullmatch(
        source_database
    ):
        raise LayoutError(
            "staging clone source must be the legacy base or a proven prior release clone"
        )
    if not STAGING_CLONE_RE.fullmatch(target_database):
        raise LayoutError("staging clone target database is not release-specific")
    if not ENV_FINGERPRINT_RE.fullmatch(env_fingerprint_before):
        raise LayoutError("staging clone env fingerprint must be a SHA-256 digest")
    if not pending_migrations:
        raise LayoutError("staging clone strategy requires pending migrations")
    if compatibility_declaration:
        raise LayoutError(
            "staging clone strategy must not claim in-place forward compatibility"
        )
    return {
        "database_strategy": "staging-clone",
        "source_database": source_database,
        "target_database": target_database,
        "env_fingerprint_before": env_fingerprint_before,
        "database_owner_release_id": None,
    }


def _id(value: str) -> str:
    if not RELEASE_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise LayoutError(f"invalid release id: {value!r}")
    return value


def _inside_releases(root: Path, target: Path) -> Path:
    releases = (root / "releases").resolve()
    resolved = target.resolve(strict=True)
    if resolved == releases or releases not in resolved.parents:
        raise LayoutError(f"release target escapes {releases}: {resolved}")
    if not resolved.is_dir():
        raise LayoutError(f"release target is not a directory: {resolved}")
    return resolved
