from __future__ import annotations

from pathlib import Path

from test_atomic_release_layout import (
    UNITS,
    _layout,
    _prepare_args,
    _release,
    _rollback_metadata,
    _run,
)


def test_sealed_payload_tamper_is_rejected_before_prepare_or_activate(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-tampered"
    release = _release(root, release_id, "8" * 40)
    target = release / "backend/app/workers/apify_jobs_worker.py"

    target.chmod(0o644)
    target.write_text("# payload changed after seal\n", encoding="utf-8")
    target.chmod(0o444)

    verified = _run(
        "verify-seal",
        "--root",
        str(root),
        "--release-id",
        release_id,
        check=False,
    )
    assert verified.returncode != 0
    assert "payload digest mismatch" in verified.stderr

    prepare_args = [
        "prepare",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-dir",
        str(unit_dir),
        "--pending-migrations",
        "250.sql",
        "--compatibility-declaration",
        "250.sql",
    ]
    for name in UNITS:
        prepare_args.extend(("--unit-name", name))
    prepared = _run(*prepare_args, check=False)
    activated = _run(
        "activate",
        "--root",
        str(root),
        "--release-id",
        release_id,
        check=False,
    )
    assert prepared.returncode != 0
    assert "payload digest mismatch" in prepared.stderr
    assert activated.returncode != 0
    assert "payload digest mismatch" in activated.stderr


def test_release_manifest_is_single_use_and_parent_must_be_nonwritable(
    tmp_path: Path,
) -> None:
    root, _unit_dir = _layout(tmp_path)
    release_id = "release-single-use"
    _release(root, release_id, "9" * 40)

    resealed = _run(
        "seal",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--git-sha",
        "9" * 40,
        check=False,
    )
    assert resealed.returncode != 0
    assert "refusing to reseal" in resealed.stderr

    releases = root / "releases"
    releases.chmod(0o775)
    verified = _run(
        "verify-seal",
        "--root",
        str(root),
        "--release-id",
        release_id,
        check=False,
    )
    assert verified.returncode != 0
    assert "parent must not be group/world writable" in verified.stderr


def test_prepare_rejects_unsealed_current_before_rollback_capture_mutation(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    current = _release(root, "release-current-drifted", "1" * 40)
    _release(root, "release-next", "2" * 40)
    (root / "current").symlink_to("releases/release-current-drifted")
    (current / "backend/app").chmod(0o755)
    (current / "backend/app/__pycache__").mkdir()

    prepared = _run(
        *_prepare_args(root, unit_dir, "release-next"),
        check=False,
    )

    assert prepared.returncode != 0
    assert "immutable release directory mode mismatch" in prepared.stderr
    assert not (root / ".release-controller").exists()
    assert not (root / "previous").exists()
    assert (root / "current").resolve() == current.resolve()


def test_prepare_rescue_binds_rollback_to_clean_same_sha_anchor(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    observed_current = _release(root, "release-current-drifted", "3" * 40)
    clean_anchor = _release(root, "release-current-clean-anchor", "3" * 40)
    prior_previous = _release(root, "release-prior-previous", "0" * 40)
    new_release = _release(root, "release-next", "4" * 40)
    (root / "current").symlink_to("releases/release-current-drifted")
    (root / "previous").symlink_to("releases/release-prior-previous")
    (observed_current / "backend/app").chmod(0o755)
    (observed_current / "backend/app/__pycache__").mkdir()

    _run(
        *_prepare_args(root, unit_dir, "release-next"),
        "--rollback-anchor-release-id",
        clean_anchor.name,
    )

    metadata = _rollback_metadata(root, "release-next")
    assert Path(metadata["observed_predeploy_current_release"]).resolve() == (
        observed_current.resolve()
    )
    assert Path(metadata["original_current_release"]).resolve() == clean_anchor.resolve()
    assert Path(metadata["active_release"]).resolve() == clean_anchor.resolve()
    assert Path(metadata["original_previous_release"]).resolve() == (
        prior_previous.resolve()
    )
    assert (root / "previous").resolve() == clean_anchor.resolve()

    _run("activate", "--root", str(root), "--release-id", new_release.name)
    _run(
        "restore",
        "--root",
        str(root),
        "--release-id",
        new_release.name,
        "--unit-dir",
        str(unit_dir),
    )
    assert (root / "current").resolve() == clean_anchor.resolve()
    assert (root / "previous").resolve() == prior_previous.resolve()


def test_prepare_rescue_rejects_anchor_with_different_observed_sha(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    observed_current = _release(root, "release-current-drifted", "5" * 40)
    clean_but_wrong_anchor = _release(root, "release-wrong-anchor", "6" * 40)
    _release(root, "release-next", "7" * 40)
    (root / "current").symlink_to("releases/release-current-drifted")
    (observed_current / "backend/app").chmod(0o755)
    (observed_current / "backend/app/__pycache__").mkdir()

    prepared = _run(
        *_prepare_args(root, unit_dir, "release-next"),
        "--rollback-anchor-release-id",
        clean_but_wrong_anchor.name,
        check=False,
    )

    assert prepared.returncode != 0
    assert "rollback anchor Git SHA does not match" in prepared.stderr
    assert not (root / ".release-controller").exists()
    assert not (root / "previous").exists()


def test_prepare_rescue_rejects_unsealed_anchor_before_rollback_capture(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    observed_current = _release(root, "release-current-drifted", "8" * 40)
    unsealed_anchor = _release(root, "release-anchor-drifted", "8" * 40)
    _release(root, "release-next", "9" * 40)
    (root / "current").symlink_to("releases/release-current-drifted")
    (observed_current / "backend/app").chmod(0o755)
    (observed_current / "backend/app/__pycache__").mkdir()
    (unsealed_anchor / "backend/app").chmod(0o755)
    (unsealed_anchor / "backend/app/__pycache__").mkdir()

    prepared = _run(
        *_prepare_args(root, unit_dir, "release-next"),
        "--rollback-anchor-release-id",
        unsealed_anchor.name,
        check=False,
    )

    assert prepared.returncode != 0
    assert "immutable release directory mode mismatch" in prepared.stderr
    assert not (root / ".release-controller").exists()
    assert not (root / "previous").exists()
