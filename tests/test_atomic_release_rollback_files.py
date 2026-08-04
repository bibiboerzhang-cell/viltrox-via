from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from test_atomic_release_layout import (
    REDIS_WORKER_UNIT,
    UNITS,
    _layout,
    _prepare_args,
    _release,
    _rewrite_rollback_metadata,
    _rollback_dir,
    _rollback_metadata,
    _run,
)


def _prepare_with_rollback_file(
    root: Path,
    unit_dir: Path,
    release_id: str,
    target: Path,
) -> None:
    _run(*_prepare_args(root, unit_dir, release_id), "--rollback-file", str(target))


def test_external_file_restore_is_atomic_and_recovers_old_template_bytes(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-external-present"
    _release(root, release_id, "8" * 40)
    lane_dir = tmp_path / "etc" / "vkpi"
    lane_dir.mkdir(parents=True)
    lane_file = lane_dir / "vkpi-lane-overrides.env"
    old_template = b"APIFY_WORKER_MAX_CONCURRENCY=4\n"
    new_template = b"APIFY_WORKER_MAX_CONCURRENCY=16\n"
    lane_file.write_bytes(old_template)
    lane_file.chmod(0o640)
    original_info = lane_file.stat()

    _prepare_with_rollback_file(root, unit_dir, release_id, lane_file)
    metadata = _rollback_metadata(root, release_id)
    entry = metadata["rollback_files"][str(lane_file)]
    assert metadata["rollback_files_required"] is True
    assert entry == {
        "present": True,
        "capture": hashlib.sha256(str(lane_file).encode("utf-8")).hexdigest(),
        "bytes": len(old_template),
        "uid": original_info.st_uid,
        "gid": original_info.st_gid,
        "mode": 0o640,
        "sha256": hashlib.sha256(old_template).hexdigest(),
    }
    capture = _rollback_dir(root, release_id) / "rollback-files" / entry["capture"]
    assert capture.read_bytes() == old_template
    assert stat.S_IMODE(capture.stat().st_mode) == 0o600

    lane_file.write_bytes(new_template)
    lane_file.chmod(0o644)
    changed_inode = lane_dir / "changed-template-inode"
    os.link(lane_file, changed_inode)
    _run("restore", "--root", str(root), "--release-id", release_id, "--unit-dir", str(unit_dir))

    restored = lane_file.stat()
    assert lane_file.read_bytes() == old_template
    assert restored.st_uid == original_info.st_uid
    assert restored.st_gid == original_info.st_gid
    assert stat.S_IMODE(restored.st_mode) == 0o640
    assert changed_inode.read_bytes() == new_template
    assert changed_inode.stat().st_ino != restored.st_ino
    assert not list(lane_dir.glob(".vkpi-lane-overrides.env.restore-*.tmp"))


def test_external_file_originally_absent_is_removed_on_restore(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-external-absent"
    _release(root, release_id, "9" * 40)
    lane_dir = tmp_path / "etc" / "vkpi"
    lane_file = lane_dir / "vkpi-lane-overrides.env"

    _prepare_with_rollback_file(root, unit_dir, release_id, lane_file)
    metadata = _rollback_metadata(root, release_id)
    assert metadata["rollback_files_required"] is True
    assert metadata["rollback_files"] == {str(lane_file): {"present": False}}

    lane_dir.mkdir(parents=True)
    lane_file.write_text("APIFY_WORKER_MAX_CONCURRENCY=16\n", encoding="utf-8")
    _run("restore", "--root", str(root), "--release-id", release_id, "--unit-dir", str(unit_dir))
    assert not lane_file.exists() and not lane_file.is_symlink()


def test_external_file_capture_rejects_oversized_input_before_prepare_mutation(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-external-oversized"
    _release(root, release_id, "e" * 40)
    lane_dir = tmp_path / "etc" / "vkpi"
    lane_dir.mkdir(parents=True)
    lane_file = lane_dir / "vkpi-lane-overrides.env"
    lane_file.write_bytes(b"x" * (1024 * 1024 + 1))

    prepared = _run(
        *_prepare_args(root, unit_dir, release_id),
        "--rollback-file",
        str(lane_file),
        check=False,
    )

    assert prepared.returncode != 0
    assert "external rollback file exceeds the one MiB limit" in prepared.stderr
    assert not _rollback_dir(root, release_id).exists()


def test_external_file_capture_tamper_fails_before_runtime_mutation(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-external-capture-tamper"
    release = _release(root, release_id, "f" * 40)
    lane_dir = tmp_path / "etc" / "vkpi"
    lane_dir.mkdir(parents=True)
    lane_file = lane_dir / "vkpi-lane-overrides.env"
    lane_file.write_text("old\n", encoding="utf-8")
    _prepare_with_rollback_file(root, unit_dir, release_id, lane_file)
    _run("activate", "--root", str(root), "--release-id", release_id)
    metadata = _rollback_metadata(root, release_id)
    capture_name = metadata["rollback_files"][str(lane_file)]["capture"]
    capture = _rollback_dir(root, release_id) / "rollback-files" / capture_name
    capture.write_text("tampered\n", encoding="utf-8")
    capture.chmod(0o600)
    (root / ".env").write_text("APP_GIT_SHA=still-new\n", encoding="utf-8")
    lane_file.write_text("new\n", encoding="utf-8")

    restored = _run(
        "restore",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-dir",
        str(unit_dir),
        check=False,
    )

    assert restored.returncode != 0
    assert "external rollback file capture hash mismatch" in restored.stderr
    assert (root / "current").resolve() == release.resolve()
    assert (root / ".env").read_text(encoding="utf-8") == "APP_GIT_SHA=still-new\n"
    assert lane_file.read_text(encoding="utf-8") == "new\n"


def test_required_external_file_metadata_missing_fails_before_runtime_mutation(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-external-required"
    release = _release(root, release_id, "a" * 40)
    lane_dir = tmp_path / "etc" / "vkpi"
    lane_dir.mkdir(parents=True)
    lane_file = lane_dir / "vkpi-lane-overrides.env"
    lane_file.write_text("old\n", encoding="utf-8")
    _prepare_with_rollback_file(root, unit_dir, release_id, lane_file)
    _run("activate", "--root", str(root), "--release-id", release_id)
    metadata = _rollback_metadata(root, release_id)
    del metadata["rollback_files"]
    _rewrite_rollback_metadata(root, release_id, metadata)
    (root / ".env").write_text("APP_GIT_SHA=still-new\n", encoding="utf-8")
    lane_file.write_text("new\n", encoding="utf-8")

    restored = _run(
        "restore",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-dir",
        str(unit_dir),
        check=False,
    )
    assert restored.returncode != 0
    assert "required external rollback file metadata is missing" in restored.stderr
    assert (root / "current").resolve() == release.resolve()
    assert (root / ".env").read_text(encoding="utf-8") == "APP_GIT_SHA=still-new\n"
    assert lane_file.read_text(encoding="utf-8") == "new\n"


def test_schema3_without_external_file_fields_remains_restorable(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-schema3-before-external-files"
    _release(root, release_id, "b" * 40)
    _run(*_prepare_args(root, unit_dir, release_id))
    metadata = _rollback_metadata(root, release_id)
    metadata.pop("rollback_files")
    metadata.pop("rollback_files_required")
    _rewrite_rollback_metadata(root, release_id, metadata)
    (root / ".env").write_text("APP_GIT_SHA=new\n", encoding="utf-8")

    _run("restore", "--root", str(root), "--release-id", release_id, "--unit-dir", str(unit_dir))
    assert (root / ".env").read_text(encoding="utf-8").startswith("APP_GIT_SHA=old")


def _prepare_masked_optional_unit(root: Path, unit_dir: Path, release_id: str) -> None:
    args = _prepare_args(root, unit_dir, release_id)
    args.extend(
        (
            "--optional-unit-name",
            REDIS_WORKER_UNIT,
            "--optional-unit-state",
            f"{REDIS_WORKER_UNIT}=present:inactive:disabled:masked",
        )
    )
    _run(*args)


def test_immediate_rollback_allows_exact_captured_dev_null_mask(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-immediate-masked-rollback"
    _release(root, release_id, "c" * 40)
    installed = unit_dir / REDIS_WORKER_UNIT
    installed.symlink_to("/dev/null")

    _prepare_masked_optional_unit(root, unit_dir, release_id)
    assert installed.is_symlink() and os.readlink(installed) == "/dev/null"
    _run("restore", "--root", str(root), "--release-id", release_id, "--unit-dir", str(unit_dir))
    assert not installed.exists() and not installed.is_symlink()


def test_masked_optional_restore_rejects_every_other_symlink(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-masked-other-symlink"
    _release(root, release_id, "d" * 40)
    installed = unit_dir / REDIS_WORKER_UNIT
    installed.symlink_to("/dev/null")
    _prepare_masked_optional_unit(root, unit_dir, release_id)
    installed.unlink()
    dev_null_alias = unit_dir / "dev-null-alias"
    dev_null_alias.symlink_to("/dev/null")
    installed.symlink_to(dev_null_alias)

    restored = _run(
        "restore",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-dir",
        str(unit_dir),
        check=False,
    )
    assert restored.returncode != 0
    assert "refusing symlink installed unit target" in restored.stderr
    assert installed.is_symlink()
