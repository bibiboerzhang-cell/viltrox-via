from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.ops import staging_web_preflight


ROOT = Path(__file__).resolve().parents[1]
UNIT_PATH = ROOT / "scripts/ops/systemd/viltrox-2.0-staging-web.service"


def _unit() -> str:
    return UNIT_PATH.read_text(encoding="utf-8")


def _exec_start_assignments(unit: str) -> tuple[set[str], str]:
    line = next(row for row in unit.splitlines() if row.startswith("ExecStart="))
    command = line.removeprefix("ExecStart=")
    prefix = "/usr/bin/env "
    assert command.startswith(prefix)
    tokens = command.removeprefix(prefix).split()
    assignments: set[str] = set()
    command_at = 0
    for command_at, token in enumerate(tokens):
        if "=" not in token:
            break
        assignments.add(token)
    return assignments, " ".join(tokens[command_at:])


def test_staging_web_is_a_nonroot_scheduler_off_sidecar() -> None:
    unit = _unit()
    assignments, command = _exec_start_assignments(unit)

    assert "User=viltrox" in unit
    assert "Group=viltrox" in unit
    assert "UMask=0027" in unit
    assert "WorkingDirectory=/opt/viltrox-2.0-staging/current" in unit
    assert "EnvironmentFile=/opt/viltrox-2.0-staging/.env" in unit
    assert {
        "APP_GIT_SHA=",
        "APP_GIT_BRANCH=",
        "APP_BUILD_TIME=",
        "APP_ROLE=admin-web",
        "ENVIRONMENT=production",
        "DB_RUNTIME_BACKEND=postgres",
        "LOCAL_RUNTIME_FORCE_STACK=0",
        "HOST=127.0.0.1",
        "PORT=8002",
        "BIND=127.0.0.1:8002",
        "ENABLE_LOCAL_ORCHESTRATOR=0",
        "ENABLE_SCHEDULER=0",
        "ENABLE_BROWSER=0",
        "ENABLE_UPLOAD_CLEANUP=0",
        "ADMIN_DAEMON=0",
        "PIDFILE=/run/vkpi-staging-web/gunicorn.pid",
    }.issubset(assignments)
    assert "ENABLE_SCHEDULER=1" not in assignments
    assert command.startswith(
        "/opt/viltrox-2.0-staging/.venv/bin/python -m gunicorn app.main:app"
    )
    assert "-c /opt/viltrox-2.0-staging/current/deploy/gunicorn_config.py" in command
    assert "--pythonpath /opt/viltrox-2.0-staging/current/backend" in command
    assert "start_admin.sh" not in command
    assert "app.workers" not in unit
    assert ".timer" not in unit
    assert "viltrox-2.0-test.service" not in unit


def test_staging_web_uses_the_sealed_build_identity_not_stale_env_values() -> None:
    unit = _unit()
    assignments, _command = _exec_start_assignments(unit)
    main_source = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")

    assert {"APP_GIT_SHA=", "APP_GIT_BRANCH=", "APP_BUILD_TIME="}.issubset(
        assignments
    )
    assert 'or _read_build_file("BUILD_GIT_SHA")' in main_source
    assert 'or _read_build_file("BUILD_GIT_BRANCH")' in main_source
    assert 'or _read_build_file("BUILD_TIME")' in main_source


def test_staging_web_requires_an_immutable_atomic_pointer() -> None:
    unit = _unit()

    assert "ExecStartPre=/usr/bin/test -L /opt/viltrox-2.0-staging/current" in unit
    assert (
        "ExecStartPre=/usr/bin/test -f "
        "/opt/viltrox-2.0-staging/current/.vkpi-release.json"
    ) in unit
    assert (
        "ExecStartPre=/usr/bin/test ! -w "
        "/opt/viltrox-2.0-staging/current/.vkpi-release.json"
    ) in unit
    assert "ExecStartPre=/usr/bin/test ! -w /opt/viltrox-2.0-staging/.env" in unit
    assert (
        "ExecStartPre=/opt/viltrox-2.0-staging/.venv/bin/python "
        "/opt/viltrox-2.0-staging/current/scripts/ops/staging_web_preflight.py "
        "--root /opt/viltrox-2.0-staging --app-user viltrox "
        "--expected-owner-uid 0 --expected-owner-gid 0 --env-owner-uid 0"
    ) in unit
    assert "ReadOnlyPaths=/opt/viltrox-2.0-staging/releases" in unit
    assert "ReadOnlyPaths=/opt/viltrox-2.0-staging/current" in unit
    assert "ReadOnlyPaths=/opt/viltrox-2.0-staging/.env" in unit
    assert "InaccessiblePaths=/opt/viltrox-2.0-staging/backups" in unit
    assert "RuntimeDirectory=vkpi-staging-web" in unit


def test_staging_web_keeps_a_small_explicit_write_surface() -> None:
    unit = _unit()
    writes = {
        row.split("=", 1)[1]
        for row in unit.splitlines()
        if row.startswith("ReadWritePaths=")
    }

    assert writes == {
        "/opt/viltrox-2.0-staging/uploads",
        "/opt/viltrox-2.0-staging/runtime",
        "/opt/viltrox-2.0-staging/frames",
        "/opt/viltrox-2.0-staging/creator_profiles",
    }
    assert "/opt/viltrox-2.0-staging/.env" not in writes
    assert "/opt/viltrox-2.0-staging/releases" not in writes
    assert "/opt/viltrox-2.0-staging/backups" not in writes
    assert "ProtectSystem=strict" in unit
    assert "NoNewPrivileges=true" in unit
    assert "PrivateDevices=true" in unit
    assert "CapabilityBoundingSet=\n" in unit
    assert "AmbientCapabilities=\n" in unit


def test_systemd_accepts_staging_web_unit_syntax() -> None:
    systemd_analyze = shutil.which("systemd-analyze")
    if not systemd_analyze:
        pytest.skip("systemd-analyze is not installed on this host")

    result = subprocess.run(
        [systemd_analyze, "verify", str(UNIT_PATH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _preflight_layout(tmp_path: Path, release_id: str) -> tuple[Path, Path, str]:
    root = tmp_path / "staging"
    releases = root / "releases"
    release = releases / release_id
    release.mkdir(parents=True)
    current = root / "current"
    current.symlink_to(Path("releases") / release_id)
    database = "viltrox2_test_release_" + hashlib.sha256(
        release_id.encode("utf-8")
    ).hexdigest()[:20]
    (root / ".env").write_text(
        f"DATABASE_URL=postgresql://app:secret@localhost:5432/{database}\n",
        encoding="utf-8",
    )
    (root / ".env").chmod(0o400)
    return root, release, database


def test_staging_preflight_binds_seal_pointer_and_clone_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_id = "candidate-20260716"
    root, release, database = _preflight_layout(tmp_path, release_id)
    uid, gid = os.geteuid(), os.getegid()
    monkeypatch.setattr(
        staging_web_preflight,
        "verify_sealed_release",
        lambda *args, **kwargs: {
            "release_id": release_id,
            "payload_sha256": "a" * 64,
            "database_strategy": "staging-clone",
            "target_database": database,
            "pending_migrations": ["264.sql"],
            "forward_compatible_migrations": [],
        },
    )

    receipt = staging_web_preflight.validate_staging_web_root(
        root=root,
        app_user=staging_web_preflight.pwd.getpwuid(uid).pw_name,
        expected_owner_uid=uid,
        expected_owner_gid=gid,
        env_owner_uid=uid,
    )

    assert receipt["release_id"] == release.name
    assert receipt["payload_sha256"] == "a" * 64
    assert receipt["scheduler_enabled"] == 0
    assert database not in str(receipt)


def test_staging_preflight_rejects_a_pointer_outside_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _release, _database = _preflight_layout(tmp_path, "candidate-safe")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "current").unlink()
    (root / "current").symlink_to(outside)
    uid, gid = os.geteuid(), os.getegid()
    monkeypatch.setattr(
        staging_web_preflight,
        "verify_sealed_release",
        lambda *args, **kwargs: pytest.fail("escaped release must not be verified"),
    )

    with pytest.raises(
        staging_web_preflight.StagingWebPreflightError,
        match="escapes releases",
    ):
        staging_web_preflight.validate_staging_web_root(
            root=root,
            app_user=staging_web_preflight.pwd.getpwuid(uid).pw_name,
            expected_owner_uid=uid,
            expected_owner_gid=gid,
            env_owner_uid=uid,
        )


def test_staging_preflight_rejects_production_database_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_id = "candidate-wrong-db"
    root, _release, database = _preflight_layout(tmp_path, release_id)
    (root / ".env").chmod(0o600)
    (root / ".env").write_text(
        "DATABASE_URL=postgresql://app:secret@localhost:5432/viltrox2_test\n",
        encoding="utf-8",
    )
    (root / ".env").chmod(0o400)
    uid, gid = os.geteuid(), os.getegid()
    monkeypatch.setattr(
        staging_web_preflight,
        "verify_sealed_release",
        lambda *args, **kwargs: {
            "release_id": release_id,
            "payload_sha256": "b" * 64,
            "database_strategy": "staging-clone",
            "target_database": database,
            "pending_migrations": ["264.sql"],
            "forward_compatible_migrations": [],
        },
    )

    with pytest.raises(
        staging_web_preflight.StagingWebPreflightError,
        match="database identities do not match",
    ):
        staging_web_preflight.validate_staging_web_root(
            root=root,
            app_user=staging_web_preflight.pwd.getpwuid(uid).pw_name,
            expected_owner_uid=uid,
            expected_owner_gid=gid,
            env_owner_uid=uid,
        )
