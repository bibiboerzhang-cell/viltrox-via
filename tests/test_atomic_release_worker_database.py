from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
from pathlib import Path

from test_atomic_release_layout import (
    UNITS,
    _layout,
    _release,
    _rollback_metadata,
    _run,
)


def _current_account() -> tuple[str, str]:
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    return user, group


def test_worker_layout_preflight_provisions_only_absent_shared_roots(tmp_path: Path) -> None:
    root, _unit_dir = _layout(tmp_path)
    release = _release(root, "release-1", "1" * 40)
    user, group = _current_account()
    (root / ".env").chmod(0o400)
    (root / "frames").rmdir()
    (root / "runtime/job-results").rmdir()

    result = _run(
        "worker-layout-preflight",
        "--root",
        str(root),
        "--release-id",
        release.name,
        "--app-user",
        user,
        "--app-group",
        group,
        "--provision-missing",
    )

    assert result.returncode == 0
    assert (root / "frames").is_dir()
    assert not (root / "frames").is_symlink()
    assert (root / "runtime/job-results").is_dir()
    assert not (root / "runtime/job-results").is_symlink()


def test_worker_runtime_preflight_exercises_exact_nonroot_surface(tmp_path: Path) -> None:
    root, _unit_dir = _layout(tmp_path)
    release = _release(root, "release-1", "1" * 40)
    user, group = _current_account()
    (root / ".env").chmod(0o400)

    bin_dir = root / ".venv" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python_bin = bin_dir / "python"
    python_bin.chmod(0o700)
    python_bin.write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = '-m' ] && [ \"$2\" = 'yt_dlp' ] && [ \"$3\" = '--version' ]\n",
        encoding="utf-8",
    )
    python_bin.chmod(0o500)
    ytdlp_bin = bin_dir / "yt-dlp"
    ytdlp_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ytdlp_bin.chmod(0o500)
    fake_tools = tmp_path / "tools"
    fake_tools.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        tool = fake_tools / name
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o500)

    runtime_env = os.environ.copy()
    runtime_env.update(
        {
            "PATH": str(fake_tools),
            "HOME": str(tmp_path / "private-home"),
            "XDG_CACHE_HOME": str(tmp_path / "private-cache"),
            "TMPDIR": str(tmp_path / "private-tmp"),
            "VKPI_JOB_RESULTS_DIR": str(root / "runtime/job-results"),
        }
    )
    result = _run(
        "worker-runtime-preflight",
        "--root",
        str(root),
        "--release-path",
        str(release),
        "--app-user",
        user,
        "--app-group",
        group,
        "--job-results-dir",
        str(root / "runtime/job-results"),
        env=runtime_env,
    )

    assert result.returncode == 0, result.stderr
    assert not list(root.glob("**/.vkpi-worker-preflight-*"))
    assert not list(tmp_path.glob("**/.vkpi-worker-preflight-*"))


def test_worker_runtime_preflight_fails_closed_when_ytdlp_module_is_missing(
    tmp_path: Path,
) -> None:
    root, _unit_dir = _layout(tmp_path)
    release = _release(root, "release-1", "1" * 40)
    user, group = _current_account()
    (root / ".env").chmod(0o400)
    python_bin = root / ".venv/bin/python"
    python_bin.chmod(0o700)
    python_bin.write_text(
        "#!/bin/sh\nprintf 'raw-secret-must-not-leak\\n' >&2\nexit 7\n",
        encoding="utf-8",
    )
    python_bin.chmod(0o500)
    ytdlp_bin = root / ".venv/bin/yt-dlp"
    ytdlp_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ytdlp_bin.chmod(0o500)
    fake_tools = tmp_path / "tools"
    fake_tools.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        binary = fake_tools / name
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o500)
    runtime_env = {
        **os.environ,
        "PATH": str(fake_tools),
        "HOME": str(tmp_path / "private-home"),
        "XDG_CACHE_HOME": str(tmp_path / "private-cache"),
        "TMPDIR": str(tmp_path / "private-tmp"),
        "VKPI_JOB_RESULTS_DIR": str(root / "runtime/job-results"),
    }

    result = _run(
        "worker-runtime-preflight",
        "--root",
        str(root),
        "--release-path",
        str(release),
        "--app-user",
        user,
        "--app-group",
        group,
        "--job-results-dir",
        str(root / "runtime/job-results"),
        env=runtime_env,
        check=False,
    )

    assert result.returncode != 0
    assert "worker tool execution preflight failed: yt_dlp module" in result.stderr
    assert "raw-secret-must-not-leak" not in result.stderr


def test_worker_runtime_preflight_fails_when_ytdlp_console_is_missing(
    tmp_path: Path,
) -> None:
    root, _unit_dir = _layout(tmp_path)
    release = _release(root, "release-1", "1" * 40)
    user, group = _current_account()
    (root / ".env").chmod(0o400)
    python_bin = root / ".venv/bin/python"
    python_bin.chmod(0o700)
    python_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python_bin.chmod(0o500)
    fake_tools = tmp_path / "tools"
    fake_tools.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        binary = fake_tools / name
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o500)
    runtime_env = {
        **os.environ,
        "PATH": str(fake_tools),
        "HOME": str(tmp_path / "private-home"),
        "XDG_CACHE_HOME": str(tmp_path / "private-cache"),
        "TMPDIR": str(tmp_path / "private-tmp"),
        "VKPI_JOB_RESULTS_DIR": str(root / "runtime/job-results"),
    }

    result = _run(
        "worker-runtime-preflight",
        "--root", str(root),
        "--release-path", str(release),
        "--app-user", user,
        "--app-group", group,
        "--job-results-dir", str(root / "runtime/job-results"),
        env=runtime_env,
        check=False,
    )

    assert result.returncode != 0
    assert "required venv worker tool is missing or not executable: yt-dlp" in result.stderr


def test_worker_runtime_preflight_requires_systemd_readonly_mounts(tmp_path: Path) -> None:
    root, _unit_dir = _layout(tmp_path)
    release = _release(root, "release-1", "1" * 40)
    user, group = _current_account()
    (root / ".env").chmod(0o400)
    fake_tools = tmp_path / "tools"
    fake_tools.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        binary = fake_tools / name
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o500)
    runtime_env = os.environ.copy()
    runtime_env.update(
        {
            "PATH": str(fake_tools),
            "HOME": str(tmp_path / "home"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "TMPDIR": str(tmp_path / "tmp"),
        }
    )

    result = _run(
        "worker-runtime-preflight",
        "--root",
        str(root),
        "--release-path",
        str(release),
        "--app-user",
        user,
        "--app-group",
        group,
        "--require-sandbox-readonly",
        env=runtime_env,
        check=False,
    )

    assert result.returncode != 0
    assert "unexpectedly remains writable inside the worker sandbox" in result.stderr
    assert not list(root.glob("**/.vkpi-worker-forbidden-*"))


def test_worker_layout_preflight_refuses_worker_writable_env(tmp_path: Path) -> None:
    root, _unit_dir = _layout(tmp_path)
    release = _release(root, "release-1", "1" * 40)
    user, group = _current_account()
    (root / ".env").chmod(0o600)

    result = _run(
        "worker-layout-preflight",
        "--root",
        str(root),
        "--release-id",
        release.name,
        "--app-user",
        user,
        "--app-group",
        group,
        check=False,
    )

    assert result.returncode != 0
    assert "must not be writable" in result.stderr


def test_staging_clone_release_records_database_lineage_without_forward_claim(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-clone-1"
    target_database = "viltrox2_test_release_" + hashlib.sha256(
        release_id.encode("utf-8")
    ).hexdigest()[:20]
    pending = "240.sql,241.sql,252.sql"
    fingerprint = "a" * 64
    release = _release(
        root,
        release_id,
        "3" * 40,
        seal_args=(
            "--pending-migrations",
            pending,
            "--database-strategy",
            "staging-clone",
            "--source-database",
            "viltrox2_test",
            "--target-database",
            target_database,
            "--env-fingerprint-before",
            fingerprint,
        ),
    )
    prepare_args = [
        "prepare",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-dir",
        str(unit_dir),
        "--pending-migrations",
        pending,
        "--database-strategy",
        "staging-clone",
        "--source-database",
        "viltrox2_test",
        "--target-database",
        target_database,
        "--env-fingerprint-before",
        fingerprint,
    ]
    for unit in UNITS:
        prepare_args.extend(("--unit-name", unit))
    _run(*prepare_args)

    manifest = json.loads((release / ".vkpi-release.json").read_text(encoding="utf-8"))
    rollback = _rollback_metadata(root, release_id)
    for payload in (manifest, rollback):
        assert payload["database_strategy"] == "staging-clone"
        assert payload["source_database"] == "viltrox2_test"
        assert payload["target_database"] == target_database
        assert payload["env_fingerprint_before"] == fingerprint
        assert payload["forward_compatible_migrations"] == []
    assert rollback["database_rollback"] == "restore-captured-env-to-original-database"


def test_app_only_clone_reuse_restore_is_symmetric(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    owner_release_id = "release-a"
    app_release_id = "release-b"
    database = "viltrox2_test_release_" + hashlib.sha256(
        owner_release_id.encode("utf-8")
    ).hexdigest()[:20]
    fingerprint = hashlib.sha256((root / ".env").read_bytes()).hexdigest()
    owner_release = _release(
        root,
        owner_release_id,
        "6" * 40,
        seal_args=(
            "--pending-migrations",
            "252.sql",
            "--database-strategy",
            "staging-clone",
            "--source-database",
            "viltrox2_test",
            "--target-database",
            database,
            "--env-fingerprint-before",
            fingerprint,
        ),
    )
    (root / "current").symlink_to(
        Path("releases") / owner_release_id,
        target_is_directory=True,
    )
    app_release = _release(
        root,
        app_release_id,
        "7" * 40,
        seal_args=(
            "--database-strategy",
            "reuse-active-clone",
            "--target-database",
            database,
            "--env-fingerprint-before",
            fingerprint,
            "--database-owner-release-id",
            owner_release_id,
        ),
    )
    args = [
        "prepare",
        "--root",
        str(root),
        "--release-id",
        app_release_id,
        "--unit-dir",
        str(unit_dir),
        "--database-strategy",
        "reuse-active-clone",
        "--target-database",
        database,
        "--env-fingerprint-before",
        fingerprint,
        "--database-owner-release-id",
        owner_release_id,
    ]
    for unit in UNITS:
        args.extend(("--unit-name", unit))
    _run(*args)
    rollback = _rollback_metadata(root, app_release_id)
    assert rollback["database_strategy"] == "reuse-active-clone"
    assert rollback["database_owner_release_id"] == owner_release_id
    assert rollback["database_rollback"] == "restore-captured-env-on-reused-database"

    _run("activate", "--root", str(root), "--release-id", app_release_id)
    assert (root / "current").resolve() == app_release.resolve()
    (root / ".env").write_text("APP_GIT_SHA=release-b\n", encoding="utf-8")
    _run(
        "restore",
        "--root",
        str(root),
        "--release-id",
        app_release_id,
        "--unit-dir",
        str(unit_dir),
    )
    assert (root / "current").resolve() == owner_release.resolve()
    assert (root / ".env").read_text(encoding="utf-8") == (
        "APP_GIT_SHA=old\nSECRET=preserved\n"
    )


def test_forward_compatible_clone_reuse_retains_schema3_rollback_lineage(
    tmp_path: Path,
) -> None:
    root, unit_dir = _layout(tmp_path)
    owner_release_id = "release-owner"
    release_id = "release-forward-migration"
    database = "viltrox2_test_release_" + hashlib.sha256(
        owner_release_id.encode("utf-8")
    ).hexdigest()[:20]
    fingerprint = hashlib.sha256((root / ".env").read_bytes()).hexdigest()
    _release(
        root,
        owner_release_id,
        "8" * 40,
        seal_args=(
            "--pending-migrations",
            "295.sql",
            "--database-strategy",
            "staging-clone",
            "--source-database",
            "viltrox2_test",
            "--target-database",
            database,
            "--env-fingerprint-before",
            fingerprint,
        ),
    )
    (root / "current").symlink_to(
        Path("releases") / owner_release_id,
        target_is_directory=True,
    )
    pending = "296.sql,297.sql"
    release = _release(
        root,
        release_id,
        "9" * 40,
        seal_args=(
            "--pending-migrations",
            pending,
            "--compatibility-declaration",
            pending,
            "--database-strategy",
            "reuse-active-clone",
            "--target-database",
            database,
            "--env-fingerprint-before",
            fingerprint,
            "--database-owner-release-id",
            owner_release_id,
        ),
    )
    args = [
        "prepare",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-dir",
        str(unit_dir),
        "--pending-migrations",
        pending,
        "--compatibility-declaration",
        pending,
        "--database-strategy",
        "reuse-active-clone",
        "--target-database",
        database,
        "--env-fingerprint-before",
        fingerprint,
        "--database-owner-release-id",
        owner_release_id,
    ]
    for unit in UNITS:
        args.extend(("--unit-name", unit))
    _run(*args)

    manifest = json.loads((release / ".vkpi-release.json").read_text(encoding="utf-8"))
    rollback = _rollback_metadata(root, release_id)
    for payload in (manifest, rollback):
        assert payload["database_strategy"] == "reuse-active-clone"
        assert payload["database_owner_release_id"] == owner_release_id
        assert payload["target_database"] == database
        assert payload["env_fingerprint_before"] == fingerprint
        assert payload["pending_migrations"] == ["296.sql", "297.sql"]
        assert payload["forward_compatible_migrations"] == ["296.sql", "297.sql"]
    assert rollback["schema"] == 3
    assert rollback["database_rollback"] == "restore-captured-env-on-reused-database"
    assert rollback["schema_retained_on_app_rollback"] is True
