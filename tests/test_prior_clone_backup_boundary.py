from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest


OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
sys.path.insert(0, str(OPS))

import prior_clone_backup_boundary as boundary  # noqa: E402


def _fixture(tmp_path: Path) -> tuple[Path, str, str, Path]:
    root = tmp_path / "app"
    owner = "database-owner"
    active_release_id = "forward-migration"
    database = "viltrox2_test_release_" + hashlib.sha256(
        owner.encode("utf-8")
    ).hexdigest()[:20]
    release = root / "releases" / active_release_id
    release.mkdir(parents=True)
    manifest = release / ".vkpi-release.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 2,
                "release_id": active_release_id,
                "database_strategy": "reuse-active-clone",
                "database_owner_release_id": owner,
                "target_database": database,
                "pending_migrations": ["296.sql", "297.sql"],
                "forward_compatible_migrations": ["296.sql", "297.sql"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (root / "current").symlink_to(
        Path("releases") / active_release_id, target_is_directory=True
    )
    (root / ".env").write_text(
        f"DATABASE_URL=postgresql://app:secret@db/{database}?sslmode=require\n",
        encoding="utf-8",
    )
    return root, owner, database, manifest


def test_snapshot_binds_environment_database_owner_and_active_manifest(tmp_path: Path) -> None:
    root, owner, database, manifest = _fixture(tmp_path)

    payload = boundary.snapshot(
        root=root,
        expected_active_release_id="forward-migration",
        expected_database_owner_release_id=owner,
        expected_database=database,
    )

    assert payload == {
        "env_sha256": hashlib.sha256((root / ".env").read_bytes()).hexdigest(),
        "active_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }


def test_snapshot_rejects_symlinked_environment_and_manifest_declaration_drift(
    tmp_path: Path,
) -> None:
    root, owner, database, manifest = _fixture(tmp_path)
    environment = root / ".env"
    real_environment = root / "environment.real"
    environment.rename(real_environment)
    environment.symlink_to(real_environment.name)
    with pytest.raises(boundary.BoundaryError, match="regular single-link"):
        boundary.snapshot(
            root=root,
            expected_active_release_id="forward-migration",
            expected_database_owner_release_id=owner,
            expected_database=database,
        )

    environment.unlink()
    real_environment.rename(environment)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["forward_compatible_migrations"] = ["296.sql"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(boundary.BoundaryError, match="exact declaration"):
        boundary.snapshot(
            root=root,
            expected_active_release_id="forward-migration",
            expected_database_owner_release_id=owner,
            expected_database=database,
        )
