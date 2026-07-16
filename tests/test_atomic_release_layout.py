from __future__ import annotations

import json
import hashlib
import os
import grp
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/ops/atomic_release_layout.py"
UNITS = (
    "viltrox-2.0-test.service",
    "vkpi-worker-interactive.service",
    "vkpi-worker-bulk@.service",
)
REDIS_WORKER_UNIT = "vkpi-redis-worker.service"
RELEASE_UNITS = (*UNITS, REDIS_WORKER_UNIT)


def _run(
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result


def _fake_systemctl(tmp_path: Path, *, active: str, enabled: str) -> tuple[Path, dict[str, str]]:
    executable = tmp_path / "systemctl"
    executable.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  is-active) printf '%s\\n' \"$VKPI_TEST_UNIT_ACTIVE\" ;;\n"
        "  is-enabled) printf '%s\\n' \"$VKPI_TEST_UNIT_ENABLED\" ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable, {
        **os.environ,
        "VKPI_TEST_UNIT_ACTIVE": active,
        "VKPI_TEST_UNIT_ENABLED": enabled,
    }


@pytest.mark.parametrize(
    ("presence", "active", "enabled", "expected"),
    [
        pytest.param("regular", "active", "enabled", "present:active:enabled:unmasked", id="present-active-enabled"),
        pytest.param("regular", "inactive", "disabled", "present:inactive:disabled:unmasked", id="present-inactive-disabled"),
        pytest.param("masked", "inactive", "masked", "present:inactive:disabled:masked", id="masked"),
        pytest.param("absent", "inactive", "not-found", "absent:inactive:disabled:unmasked", id="absent"),
    ],
)
def test_inspect_optional_unit_exact_four_dimensional_state(
    tmp_path: Path,
    presence: str,
    active: str,
    enabled: str,
    expected: str,
) -> None:
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    installed = unit_dir / REDIS_WORKER_UNIT
    if presence == "regular":
        installed.write_text("[Unit]\n", encoding="utf-8")
    elif presence == "masked":
        installed.symlink_to("/dev/null")
    systemctl, env = _fake_systemctl(tmp_path, active=active, enabled=enabled)
    result = _run(
        "inspect-unit-state",
        "--unit-dir",
        str(unit_dir),
        "--unit-name",
        REDIS_WORKER_UNIT,
        "--systemctl-bin",
        str(systemctl),
        env=env,
    )
    assert result.stdout.strip() == expected


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "app"
    unit_dir = tmp_path / "systemd"
    root.mkdir()
    unit_dir.mkdir()
    (root / ".env").write_text("APP_GIT_SHA=old\nSECRET=preserved\n", encoding="utf-8")
    (root / ".venv/bin").mkdir(parents=True)
    python_bin = root / ".venv/bin/python"
    python_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python_bin.chmod(0o500)
    for name in ("runtime", "uploads", "frames", "creator_profiles", "backups"):
        (root / name).mkdir()
    (root / "runtime/job-results").mkdir()
    (root / "legacy.txt").write_text("old-running-tree\n", encoding="utf-8")
    (root / "BUILD_GIT_SHA").write_text("0" * 40 + "\n", encoding="utf-8")
    for name in UNITS:
        (unit_dir / name).write_text(f"old:{name}\n", encoding="utf-8")
    return root, unit_dir


def _release(
    root: Path,
    release_id: str,
    sha: str,
    *,
    seal_args: tuple[str, ...] | None = None,
) -> Path:
    release = root / "releases" / release_id
    for directory in (
        "backend",
        "frontend/dist",
        "migrations",
        "scripts/ops/systemd",
    ):
        (release / directory).mkdir(parents=True, exist_ok=True)
    (release / "scripts/start_admin.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    worker_module = release / "backend/app/workers/apify_jobs_worker.py"
    worker_module.parent.mkdir(parents=True, exist_ok=True)
    worker_module.write_text("# worker preflight fixture\n", encoding="utf-8")
    for name in RELEASE_UNITS:
        (release / "scripts/ops/systemd" / name).write_text(
            f"new:{release_id}:{name}\n", encoding="utf-8"
        )
    (release / "BUILD_GIT_SHA").write_text(sha + "\n", encoding="utf-8")
    (release / "BUILD_GIT_BRANCH").write_text("codex/test\n", encoding="utf-8")
    (release / "BUILD_TIME").write_text("2026-07-14T00:00:00Z\n", encoding="utf-8")
    args = [
        "seal",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--git-sha",
        sha,
    ]
    args.extend(
        seal_args
        if seal_args is not None
        else (
            "--pending-migrations",
            "250.sql",
            "--compatibility-declaration",
            "250.sql",
        )
    )
    _run(*args)
    return release


def _prepare(root: Path, unit_dir: Path, release_id: str) -> None:
    args = [
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
        args.extend(("--unit-name", name))
    _run(*args)


def test_first_release_never_overwrites_flat_tree_and_restore_is_atomic(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-1"
    release = _release(root, release_id, "1" * 40)

    _prepare(root, unit_dir, release_id)
    legacy = (root / "previous").resolve()
    assert legacy.name == f"legacy-before-{release_id}"
    assert (root / "legacy.txt").read_text(encoding="utf-8") == "old-running-tree\n"
    assert (legacy / "legacy.txt").read_text(encoding="utf-8") == "old-running-tree\n"

    _run("activate", "--root", str(root), "--release-id", release_id)
    assert (root / "current").resolve() == release.resolve()
    assert (release / ".env").is_symlink()
    assert (release / "runtime").is_symlink()
    manifest = json.loads((release / ".vkpi-release.json").read_text(encoding="utf-8"))
    assert manifest["pending_migrations"] == ["250.sql"]
    assert manifest["schema"] == 2
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["payload_sha256"])
    assert manifest["payload_entry_count"] > 0
    assert manifest["immutable_owner_uid"] == os.geteuid()
    assert manifest["immutable_owner_gid"] == os.getegid()
    assert stat.S_IMODE(release.stat().st_mode) == 0o555
    assert all(
        not (stat.S_IMODE(path.lstat().st_mode) & 0o222)
        for path in release.rglob("*")
        if not path.is_symlink()
    )
    _run(
        "verify-seal",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--expected-owner-uid",
        str(os.geteuid()),
        "--expected-owner-gid",
        str(os.getegid()),
    )

    (root / ".env").write_text("APP_GIT_SHA=new\n", encoding="utf-8")
    for name in UNITS:
        (unit_dir / name).write_text(f"new:{name}\n", encoding="utf-8")
    _run(
        "restore",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-dir",
        str(unit_dir),
    )
    assert (root / "current").resolve() == legacy
    assert not (root / "previous").exists()
    assert (root / ".env").read_text(encoding="utf-8") == "APP_GIT_SHA=old\nSECRET=preserved\n"
    for name in UNITS:
        assert (unit_dir / name).read_text(encoding="utf-8") == f"old:{name}\n"


def test_sealed_payload_tamper_is_rejected_before_prepare_or_activate(tmp_path: Path) -> None:
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


def test_release_manifest_is_single_use_and_parent_must_be_nonwritable(tmp_path: Path) -> None:
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


def test_later_release_restores_current_and_prior_previous_pointer(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    first = _release(root, "release-1", "1" * 40)
    legacy = root / "releases" / "legacy-manual"
    legacy.mkdir()
    (root / "current").symlink_to("releases/release-1")
    (root / "previous").symlink_to("releases/legacy-manual")
    _release(root, "release-2", "2" * 40)

    _prepare(root, unit_dir, "release-2")
    assert (root / "previous").resolve() == first.resolve()
    _run("activate", "--root", str(root), "--release-id", "release-2")
    _run(
        "restore",
        "--root",
        str(root),
        "--release-id",
        "release-2",
        "--unit-dir",
        str(unit_dir),
    )
    assert (root / "current").resolve() == first.resolve()
    assert (root / "previous").resolve() == legacy.resolve()


def test_optional_unit_absent_bootstrap_is_removed_on_restore(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-optional-absent"
    _release(root, release_id, "4" * 40)
    args = [
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
        "--optional-unit-name",
        REDIS_WORKER_UNIT,
        "--optional-unit-state",
        f"{REDIS_WORKER_UNIT}=absent:inactive:disabled:unmasked",
    ]
    for name in UNITS:
        args.extend(("--unit-name", name))
    _run(*args)
    rollback = json.loads(
        (
            root
            / "runtime/ops/deploy-rollbacks"
            / release_id
            / "metadata.json"
        ).read_text(encoding="utf-8")
    )
    assert rollback["optional_unit_states"] == {
        REDIS_WORKER_UNIT: {
            "present": False,
            "active": False,
            "enabled": False,
            "masked": False,
        }
    }
    captured = _run(
        "rollback-unit-state",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-name",
        REDIS_WORKER_UNIT,
    )
    assert captured.stdout.strip() == "absent:inactive:disabled:unmasked"

    (unit_dir / REDIS_WORKER_UNIT).write_text("new worker unit\n", encoding="utf-8")
    _run(
        "restore",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-dir",
        str(unit_dir),
    )
    assert not (unit_dir / REDIS_WORKER_UNIT).exists()


def test_optional_unit_present_is_restored_byte_for_byte(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-optional-present"
    _release(root, release_id, "5" * 40)
    original = b"[Unit]\nDescription=old redis worker\n"
    (unit_dir / REDIS_WORKER_UNIT).write_bytes(original)
    args = [
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
        "--optional-unit-name",
        REDIS_WORKER_UNIT,
        "--optional-unit-state",
        f"{REDIS_WORKER_UNIT}=present:active:enabled:unmasked",
    ]
    for name in UNITS:
        args.extend(("--unit-name", name))
    _run(*args)
    rollback = json.loads(
        (
            root
            / "runtime/ops/deploy-rollbacks"
            / release_id
            / "metadata.json"
        ).read_text(encoding="utf-8")
    )
    assert rollback["optional_unit_states"] == {
        REDIS_WORKER_UNIT: {
            "present": True,
            "active": True,
            "enabled": True,
            "masked": False,
        }
    }
    captured = _run(
        "rollback-unit-state",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-name",
        REDIS_WORKER_UNIT,
    )
    assert captured.stdout.strip() == "present:active:enabled:unmasked"

    (unit_dir / REDIS_WORKER_UNIT).write_text("new worker unit\n", encoding="utf-8")
    _run(
        "restore",
        "--root",
        str(root),
        "--release-id",
        release_id,
        "--unit-dir",
        str(unit_dir),
    )
    assert (unit_dir / REDIS_WORKER_UNIT).read_bytes() == original


def test_optional_unit_present_inactive_disabled_receipt_and_restore(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-optional-inactive"
    _release(root, release_id, "6" * 40)
    original = b"[Unit]\nDescription=disabled redis worker\n"
    (unit_dir / REDIS_WORKER_UNIT).write_bytes(original)
    args = [
        "prepare", "--root", str(root), "--release-id", release_id,
        "--unit-dir", str(unit_dir), "--pending-migrations", "250.sql",
        "--compatibility-declaration", "250.sql",
        "--optional-unit-name", REDIS_WORKER_UNIT,
        "--optional-unit-state", f"{REDIS_WORKER_UNIT}=present:inactive:disabled:unmasked",
    ]
    for name in UNITS:
        args.extend(("--unit-name", name))
    _run(*args)
    captured = _run(
        "rollback-unit-state", "--root", str(root), "--release-id", release_id,
        "--unit-name", REDIS_WORKER_UNIT,
    )
    assert captured.stdout.strip() == "present:inactive:disabled:unmasked"
    (unit_dir / REDIS_WORKER_UNIT).write_text("new\n", encoding="utf-8")
    _run("restore", "--root", str(root), "--release-id", release_id, "--unit-dir", str(unit_dir))
    assert (unit_dir / REDIS_WORKER_UNIT).read_bytes() == original


def test_optional_unit_masked_receipt_removes_deployed_file_for_remask(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    release_id = "release-optional-masked"
    _release(root, release_id, "7" * 40)
    installed = unit_dir / REDIS_WORKER_UNIT
    installed.symlink_to("/dev/null")
    args = [
        "prepare", "--root", str(root), "--release-id", release_id,
        "--unit-dir", str(unit_dir), "--pending-migrations", "250.sql",
        "--compatibility-declaration", "250.sql",
        "--optional-unit-name", REDIS_WORKER_UNIT,
        "--optional-unit-state", f"{REDIS_WORKER_UNIT}=present:inactive:disabled:masked",
    ]
    for name in UNITS:
        args.extend(("--unit-name", name))
    _run(*args)
    captured = _run(
        "rollback-unit-state", "--root", str(root), "--release-id", release_id,
        "--unit-name", REDIS_WORKER_UNIT,
    )
    assert captured.stdout.strip() == "present:inactive:disabled:masked"
    installed.unlink()
    installed.write_text("new\n", encoding="utf-8")
    _run("restore", "--root", str(root), "--release-id", release_id, "--unit-dir", str(unit_dir))
    assert not installed.exists() and not installed.is_symlink()


def test_refuses_path_traversal_and_non_symlink_current(tmp_path: Path) -> None:
    root, unit_dir = _layout(tmp_path)
    result = _run(
        "activate",
        "--root",
        str(root),
        "--release-id",
        "../escape",
        check=False,
    )
    assert result.returncode != 0
    assert "invalid release id" in result.stderr

    _release(root, "release-1", "1" * 40)
    (root / "current").mkdir()
    args = [
        "prepare",
        "--root",
        str(root),
        "--release-id",
        "release-1",
        "--unit-dir",
        str(unit_dir),
    ]
    for name in UNITS:
        args.extend(("--unit-name", name))
    result = _run(*args, check=False)
    assert result.returncode != 0
    assert "refusing non-symlink release pointer" in result.stderr


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
    tool = bin_dir / "yt-dlp"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o500)
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


def test_worker_runtime_preflight_requires_systemd_readonly_mounts(tmp_path: Path) -> None:
    root, _unit_dir = _layout(tmp_path)
    release = _release(root, "release-1", "1" * 40)
    user, group = _current_account()
    (root / ".env").chmod(0o400)
    tool = root / ".venv/bin/yt-dlp"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o500)
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
    rollback = json.loads(
        (
            root
            / "runtime"
            / "ops"
            / "deploy-rollbacks"
            / release_id
            / "metadata.json"
        ).read_text(encoding="utf-8")
    )
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
    rollback_path = (
        root
        / "runtime/ops/deploy-rollbacks"
        / app_release_id
        / "metadata.json"
    )
    rollback = json.loads(rollback_path.read_text(encoding="utf-8"))
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
