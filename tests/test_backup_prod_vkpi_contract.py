from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "backup_prod_vkpi.sh"
STAMP = "20260715T140000Z"
SECRET_PASSWORD = "do-not-print-prod-secret"
CURRENT_RELEASE_ID = "20260804T085838Z-f4106053980b"
CURRENT_GIT_SHA = "f4106053980b20a9e24335b369d845fb2fbefafa"
CURRENT_ASSET = "app-UHZk0gU6.js"
LEGACY_ASSET = "app-DswQN-ls.js"
SECRET_URL = (
    f"postgresql://backup_user:{SECRET_PASSWORD}@127.0.0.1:54329/viltrox2"
    "?sslmode=prefer&application_name=vkpi%20prod%20backup"
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _fixture(tmp_path: Path, *, transfer_mode: str = "valid") -> tuple[dict[str, str], Path, Path]:
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / ".env").write_text(f'DATABASE_URL="{SECRET_URL}"\n', encoding="utf-8")
    (remote / "backups").mkdir()
    legacy_assets = remote / "frontend" / "dist" / "assets"
    legacy_assets.mkdir(parents=True)
    (legacy_assets / LEGACY_ASSET).write_text("legacy", encoding="utf-8")
    (remote / "frontend" / "dist" / "index.html").write_text(
        f'<script type="module" src="/assets/{LEGACY_ASSET}"></script>\n',
        encoding="utf-8",
    )
    release = remote / "releases" / CURRENT_RELEASE_ID
    release_assets = release / "frontend" / "dist" / "assets"
    release_assets.mkdir(parents=True)
    (release / "BUILD_GIT_SHA").write_text(CURRENT_GIT_SHA + "\n", encoding="ascii")
    (release / ".vkpi-release.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "release_id": CURRENT_RELEASE_ID,
                "git_sha": CURRENT_GIT_SHA,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (release / "frontend" / "dist" / "index.html").write_text(
        f'<script type="module" src="/assets/{CURRENT_ASSET}"></script>\n',
        encoding="utf-8",
    )
    (release_assets / CURRENT_ASSET).write_text("current", encoding="utf-8")
    # An unreferenced but lexicographically later asset proves runtime-state is
    # derived from index.html, not from directory sorting.
    (release_assets / "app-zzzz-unused.js").write_text("unused", encoding="utf-8")
    (remote / "current").symlink_to(release, target_is_directory=True)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"

    _write_executable(
        fake_bin / "ssh",
        """
printf 'ssh %s\\n' "$*" >> "$COMMAND_LOG"
if [[ "$*" == *"VKPI_BACKUP_ARTIFACT_NAME="* ]]; then
  case "$*" in
    *"VKPI_BACKUP_ARTIFACT_NAME='environment.gpg.sha256'"*) artifact='environment.gpg.sha256' ;;
    *"VKPI_BACKUP_ARTIFACT_NAME='environment.gpg'"*) artifact='environment.gpg' ;;
    *"VKPI_BACKUP_ARTIFACT_NAME='off-host-backup-receipt.json'"*) artifact='off-host-backup-receipt.json' ;;
    *) exit 80 ;;
  esac
  destination="$FAKE_REMOTE_ROOT/backups/ops/$STAMP/$artifact"
  if [ "$TRANSFER_MODE" = "fail_environment_upload" ] && [ "$artifact" = "environment.gpg.sha256" ]; then
    exit 81
  fi
  [ ! -e "$destination" ] && [ ! -L "$destination" ]
  cat > "$destination"
  chmod 600 "$destination"
  [ -s "$destination" ]
  exit 0
fi
env REMOTE_ROOT="$FAKE_REMOTE_ROOT" \\
  REMOTE_BACKUP_DIR="backups/ops/$STAMP" \\
  PYTHON_BIN="$PYTHON_BIN_FOR_TEST" \\
  REMOTE_APP_USER=viltrox REMOTE_APP_GROUP=viltrox BACKUP_MEDIA_ARCHIVE=0 \\
  /bin/bash
""",
    )
    _write_executable(
        fake_bin / "id",
        """
case "${1:-}" in
  -un) printf 'viltrox\\n' ;;
  -Gn) printf 'viltrox\\n' ;;
  *) /usr/bin/id "$@" ;;
esac
""",
    )
    _write_executable(
        fake_bin / "stat",
        """
if [ "${1:-}" = '-c' ] && [ "${2:-}" = '%U:%G' ] && [ "${3:-}" = 'backups' ]; then
  printf 'viltrox:viltrox\\n'
elif [ "${1:-}" = '-c' ] && [ "${2:-}" = '%U:%G:%a:%h' ] && [ "${3:-}" = '.env' ]; then
  printf 'viltrox:viltrox:600:1\\n'
elif [ "${1:-}" = '-c' ] && [ "${2:-}" = '%U:%G:%a:%h' ]; then
  case "${3:-}" in
    environment.gpg|environment.gpg.sha256|off-host-backup-receipt.json)
      printf 'viltrox:viltrox:600:1\\n'
      ;;
    *) /usr/bin/stat "$@" ;;
  esac
else
  /usr/bin/stat "$@"
fi
""",
    )
    _write_executable(
        fake_bin / "pg_dump",
        """
printf 'pg_dump %s\\n' "$*" >> "$COMMAND_LOG"
[ "$PGSERVICE" = 'vkpi_prod_backup' ]
service_mode="$(stat -c '%a' "$PGSERVICEFILE" 2>/dev/null || stat -f '%Lp' "$PGSERVICEFILE")"
pass_mode="$(stat -c '%a' "$PGPASSFILE" 2>/dev/null || stat -f '%Lp' "$PGPASSFILE")"
[ "$service_mode" = '600' ]
[ "$pass_mode" = '600' ]
grep -F -- 'application_name=vkpi prod backup' "$PGSERVICEFILE" >/dev/null
grep -F -- "$SECRET_PASSWORD" "$PGPASSFILE" >/dev/null
output=''
for argument in "$@"; do
  case "$argument" in
    *"$SECRET_PASSWORD"*) exit 91 ;;
    --file=*) output="${argument#--file=}" ;;
  esac
done
[ -n "$output" ]
printf 'PGDMP-valid-production-fixture' > "$output"
""",
    )
    _write_executable(
        fake_bin / "pg_restore",
        """
printf 'pg_restore %s\\n' "$*" >> "$COMMAND_LOG"
archive="${@: -1}"
[ -s "$archive" ]
[ "$(head -c 5 "$archive")" = 'PGDMP' ]
printf 'fixture archive list\\n'
""",
    )
    _write_executable(
        fake_bin / "sha256sum",
        """
"$PYTHON_BIN_FOR_TEST" - "$1" <<'PY'
from pathlib import Path
import hashlib
import sys
path = Path(sys.argv[1])
print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}")
PY
""",
    )
    _write_executable(
        fake_bin / "gpg",
        """
printf 'gpg %s\\n' "$*" >> "$COMMAND_LOG"
output=''
passphrase_file=''
decrypt_input=''
mode=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    --passphrase-file) passphrase_file="$2"; shift 2 ;;
    --decrypt) mode='decrypt'; decrypt_input="$2"; shift 2 ;;
    --symmetric) mode='encrypt'; shift ;;
    *) shift ;;
  esac
done
[ -f "$passphrase_file" ] && [ ! -L "$passphrase_file" ]
[ "$(cat "$passphrase_file")" = "$GPG_PASSPHRASE" ]
if [ "$mode" = 'encrypt' ]; then
  [ "$GPG_MODE" != 'fail_encrypt' ]
  [ -n "$output" ] && [ ! -e "$output" ]
  input_sha="$("$PYTHON_BIN_FOR_TEST" -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
  printf 'GPGFIXTURE:%s\\n' "$input_sha" > "$output"
elif [ "$mode" = 'decrypt' ]; then
  [ "$GPG_MODE" != 'fail_decrypt' ]
  grep -F 'GPGFIXTURE:' "$decrypt_input" >/dev/null
  printf 'fixture decrypted environment\\n'
else
  exit 82
fi
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """
if [ "${1:-}" = 'is-active' ]; then printf 'active\\n'; exit 0; fi
exit 1
""",
    )
    _write_executable(
        fake_bin / "git",
        """
printf 'fe3871c4 fixture\\n'
""",
    )
    _write_executable(
        fake_bin / "find",
        """
root="${1:-}"
if [ -d "$root" ]; then
  for candidate in "$root"/*; do
    [ -f "$candidate" ] || continue
    basename "$candidate"
  done
fi
""",
    )
    _write_executable(
        fake_bin / "scp",
        """
source_spec="$2"
destination="$3"
source_path="${source_spec#*:}"
source_path="${source_path%\\*}"
mkdir -p "$destination"
cp "${source_path}"* "$destination/"
case "$TRANSFER_MODE" in
  corrupt_sha)
    printf 'tampered' >> "$destination/prod-db.dump"
    ;;
  invalid_archive)
    printf 'INVALID-archive' > "$destination/prod-db.dump"
    "$PYTHON_BIN_FOR_TEST" - "$destination/prod-db.dump" <<'PY'
from pathlib import Path
import hashlib
import sys
path = Path(sys.argv[1])
path.with_suffix(path.suffix + ".sha256").write_text(
    f"{hashlib.sha256(path.read_bytes()).hexdigest()}  prod-db.dump\\n",
    encoding="ascii",
)
PY
    ;;
esac
""",
    )

    local_parent = tmp_path / "downloaded"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "SSH_TARGET": "fixture-host",
            "REMOTE_ROOT": str(remote),
            "LOCAL_PARENT": str(local_parent),
            "STAMP": STAMP,
            "PYTHON_BIN": sys.executable,
            "PYTHON_BIN_FOR_TEST": sys.executable,
            "FAKE_REMOTE_ROOT": str(remote),
            "COMMAND_LOG": str(command_log),
            "SECRET_PASSWORD": SECRET_PASSWORD,
            "TRANSFER_MODE": transfer_mode,
            "GPG_MODE": "valid",
            "GPG_PASSPHRASE": "local-gpg-passphrase-do-not-print",
        }
    )
    return env, local_parent, command_log


def _enable_encryption(env: dict[str, str], tmp_path: Path) -> Path:
    passphrase_file = tmp_path / "backup-passphrase"
    passphrase_file.write_text(env["GPG_PASSPHRASE"] + "\n", encoding="utf-8")
    passphrase_file.chmod(0o600)
    env.update(
        {
            "VKPI_BACKUP_ENCRYPT_ENV": "1",
            "VKPI_BACKUP_GPG_PASSPHRASE_FILE": str(passphrase_file),
            "LOCAL_PYTHON_BIN": sys.executable,
        }
    )
    return passphrase_file


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_production_backup_hides_dsn_and_verifies_remote_and_downloaded_archive(
    tmp_path: Path,
) -> None:
    env, local_parent, command_log = _fixture(tmp_path)

    result = _run(env)

    assert result.returncode == 0, result.stderr
    downloaded = local_parent / STAMP / "prod-db.dump"
    sidecar = downloaded.with_suffix(".dump.sha256")
    assert downloaded.read_bytes() == b"PGDMP-valid-production-fixture"
    assert sidecar.read_text(encoding="ascii") == (
        f"{hashlib.sha256(downloaded.read_bytes()).hexdigest()}  prod-db.dump\n"
    )
    assert stat.S_IMODE(downloaded.stat().st_mode) == 0o600
    assert (local_parent / "latest").resolve() == (local_parent / STAMP).resolve()
    log = command_log.read_text(encoding="utf-8")
    assert log.count("pg_restore --list") == 2
    assert "PGSERVICEFILE" not in log
    assert SECRET_URL not in log
    assert SECRET_PASSWORD not in log
    assert SECRET_URL not in result.stdout + result.stderr
    assert SECRET_PASSWORD not in result.stdout + result.stderr
    assert not list((tmp_path / "remote").rglob(".pgservice.tmp.*"))
    assert not list((tmp_path / "remote").rglob(".pgpass.tmp.*"))
    assert "backup verification: sha256=passed pg_restore_list=passed" in result.stdout


def test_production_backup_runtime_state_is_bound_to_atomic_current(
    tmp_path: Path,
) -> None:
    env, local_parent, _command_log = _fixture(tmp_path)

    result = _run(env)

    assert result.returncode == 0, result.stderr
    state = (local_parent / STAMP / "runtime-state.txt").read_text(encoding="utf-8")
    assert "release_state=valid\n" in state
    assert f"release_id={CURRENT_RELEASE_ID}\n" in state
    assert f"git_head={CURRENT_GIT_SHA}\n" in state
    assert f"frontend_asset={CURRENT_ASSET}\n" in state
    assert "app-zzzz-unused.js" not in state
    assert LEGACY_ASSET not in state


@pytest.mark.parametrize(
    ("pointer_mode", "expected_error"),
    [
        ("broken", "current_unresolved"),
        ("escape", "current_outside_releases"),
    ],
)
def test_production_backup_keeps_db_and_media_when_atomic_current_is_invalid(
    tmp_path: Path,
    pointer_mode: str,
    expected_error: str,
) -> None:
    env, local_parent, _command_log = _fixture(tmp_path)
    remote = tmp_path / "remote"
    current = remote / "current"
    current.unlink()
    if pointer_mode == "broken":
        current.symlink_to(remote / "releases" / "missing", target_is_directory=True)
    else:
        escaped = remote / "escaped-release"
        escaped.mkdir()
        current.symlink_to(escaped, target_is_directory=True)

    result = _run(env)

    assert result.returncode == 0, result.stderr
    downloaded = local_parent / STAMP
    assert (downloaded / "prod-db.dump").read_bytes() == b"PGDMP-valid-production-fixture"
    assert (downloaded / "media-cache-size.txt").exists()
    assert (local_parent / "latest").resolve() == downloaded.resolve()
    state = (downloaded / "runtime-state.txt").read_text(encoding="utf-8")
    assert "release_state=invalid\n" in state
    assert f"release_error={expected_error}\n" in state
    assert "frontend_asset=" not in state
    assert "git_head=" not in state
    assert LEGACY_ASSET not in state


@pytest.mark.parametrize(
    ("transfer_mode", "message"),
    [
        ("corrupt_sha", "SHA-256 mismatch"),
        ("invalid_archive", "local pg_restore could not read"),
    ],
)
def test_production_backup_does_not_publish_latest_when_download_validation_fails(
    tmp_path: Path, transfer_mode: str, message: str
) -> None:
    env, local_parent, _command_log = _fixture(tmp_path, transfer_mode=transfer_mode)

    result = _run(env)

    assert result.returncode != 0
    assert message in result.stderr
    assert not (local_parent / "latest").exists()
    assert SECRET_URL not in result.stdout + result.stderr
    assert SECRET_PASSWORD not in result.stdout + result.stderr


def test_production_backup_source_never_passes_database_url_to_pg_dump() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'DATABASE_URL="$(' not in executable
    assert '"${DATABASE_URL}"' not in executable
    assert 'PGSERVICEFILE="${remote_pgservice}"' in source
    assert source.index("remote pg_restore could not read") < source.index("scp -q")
    assert source.index("downloaded production backup SHA-256 mismatch") < source.index(
        'ln -sfn "${STAMP}"'
    )
    assert source.index("local pg_restore could not read") < source.index(
        'ln -sfn "${STAMP}"'
    )
    assert 'current_link="${remote_root_path}/current"' in source
    assert 'resolved_parent="$(dirname -- "${resolved_current}")"' in source
    assert 'index_path="${resolved_current}/frontend/dist/index.html"' in source
    assert "grep -o 'app-[A-Za-z0-9_-]*\\.js' \"${index_path}\"" in source
    assert "git log --oneline" not in source
    assert "find frontend/dist/assets" not in source
    # Secrets and mutable data intentionally remain anchored at the shared root.
    assert 'env_path = Path(".env")' in source
    assert "if [ -d uploads/vkpi_media_cache ]; then" in source


def test_encrypted_environment_is_verified_bound_and_returned_to_remote_backup_set(
    tmp_path: Path,
) -> None:
    env, local_parent, command_log = _fixture(tmp_path)
    _enable_encryption(env, tmp_path)

    result = _run(env)

    assert result.returncode == 0, result.stderr
    local_dir = local_parent / STAMP
    remote_dir = tmp_path / "remote" / "backups" / "ops" / STAMP
    ciphertext = local_dir / "environment.gpg"
    ciphertext_sha = hashlib.sha256(ciphertext.read_bytes()).hexdigest()
    db_sha = hashlib.sha256((local_dir / "prod-db.dump").read_bytes()).hexdigest()
    assert ciphertext.read_bytes().startswith(b"GPGFIXTURE:")
    assert SECRET_URL.encode() not in ciphertext.read_bytes()
    assert (local_dir / "environment.gpg.sha256").read_text(encoding="ascii") == (
        f"{ciphertext_sha}  environment.gpg\n"
    )
    receipt = json.loads(
        (local_dir / "off-host-backup-receipt.json").read_text(encoding="utf-8")
    )
    assert receipt == {
        "schema_version": "vkpi-off-host-backup-receipt/v1",
        "method": "ssh_pull_verified_mac",
        "stamp": STAMP,
        "db_artifact": "prod-db.dump",
        "db_sha256": db_sha,
        "environment_ciphertext_artifact": "environment.gpg",
        "environment_ciphertext_sha256": ciphertext_sha,
        "pg_restore_list_passed": True,
        "environment_decryption_verified": True,
        "local_copy_verified": True,
        "plaintext_environment_persisted": False,
    }
    for artifact_name in (
        "environment.gpg",
        "environment.gpg.sha256",
        "off-host-backup-receipt.json",
    ):
        local_artifact = local_dir / artifact_name
        remote_artifact = remote_dir / artifact_name
        assert remote_artifact.read_bytes() == local_artifact.read_bytes()
        assert stat.S_IMODE(local_artifact.stat().st_mode) == 0o600
        assert stat.S_IMODE(remote_artifact.stat().st_mode) == 0o600
        assert local_artifact.stat().st_nlink == 1
        assert remote_artifact.stat().st_nlink == 1
    transcript = command_log.read_text(encoding="utf-8") + result.stdout + result.stderr
    assert env["GPG_PASSPHRASE"] not in transcript
    assert SECRET_URL not in transcript
    assert SECRET_PASSWORD not in transcript
    assert "--passphrase-file" in transcript
    assert "--passphrase " not in transcript
    assert "off-host verification: encrypted_environment=passed" in result.stdout


@pytest.mark.parametrize("unsafe_kind", ["group_readable", "symlink", "hardlink"])
def test_encrypted_backup_rejects_unsafe_local_passphrase_file_before_ssh(
    tmp_path: Path, unsafe_kind: str
) -> None:
    env, local_parent, command_log = _fixture(tmp_path)
    passphrase_file = _enable_encryption(env, tmp_path)
    if unsafe_kind == "group_readable":
        passphrase_file.chmod(0o640)
    elif unsafe_kind == "symlink":
        target = tmp_path / "passphrase-target"
        passphrase_file.rename(target)
        passphrase_file.symlink_to(target)
    else:
        os.link(passphrase_file, tmp_path / "second-passphrase-link")

    result = _run(env)

    assert result.returncode != 0
    assert "passphrase file failed local safety validation" in result.stderr
    assert not command_log.exists() or command_log.read_text(encoding="utf-8") == ""
    assert not (local_parent / "latest").exists()
    assert env["GPG_PASSPHRASE"] not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("failure_mode", "expected_message"),
    [
        ("fail_decrypt", "decryption verification failed"),
        ("fail_environment_upload", "artifact transfer failed"),
    ],
)
def test_encrypted_backup_failures_do_not_publish_latest_or_remote_receipt(
    tmp_path: Path, failure_mode: str, expected_message: str
) -> None:
    transfer_mode = failure_mode if failure_mode.startswith("fail_environment_upload") else "valid"
    env, local_parent, command_log = _fixture(tmp_path, transfer_mode=transfer_mode)
    _enable_encryption(env, tmp_path)
    if failure_mode == "fail_decrypt":
        env["GPG_MODE"] = failure_mode

    result = _run(env)

    assert result.returncode != 0
    assert expected_message in result.stderr
    assert not (local_parent / "latest").exists()
    remote_receipt = (
        tmp_path / "remote" / "backups" / "ops" / STAMP / "off-host-backup-receipt.json"
    )
    assert not remote_receipt.exists()
    transcript = command_log.read_text(encoding="utf-8") + result.stdout + result.stderr
    assert env["GPG_PASSPHRASE"] not in transcript
    assert SECRET_URL not in transcript
    assert SECRET_PASSWORD not in transcript


def test_encrypted_backup_never_overwrites_existing_artifact(tmp_path: Path) -> None:
    env, local_parent, _command_log = _fixture(tmp_path)
    _enable_encryption(env, tmp_path)
    remote_dir = tmp_path / "remote" / "backups" / "ops" / STAMP
    remote_dir.mkdir(parents=True)
    existing = remote_dir / "environment.gpg"
    existing.write_bytes(b"existing-ciphertext")

    result = _run(env)

    assert result.returncode != 0
    assert "refusing to overwrite an existing encrypted backup artifact" in result.stderr
    assert existing.read_bytes() == b"existing-ciphertext"
    assert not (local_parent / "latest").exists()


def test_encrypted_backup_source_streams_plaintext_only_into_gpg() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "stream_protected_remote_environment \\" in source
    assert "| gpg --no-options --batch" in source
    assert '--passphrase-file "${VKPI_BACKUP_GPG_PASSPHRASE_FILE}"' in source
    assert "--passphrase " not in executable
    assert "plaintext_environment_persisted" in source
    assert source.index("remote encrypted backup receipt verification failed") < source.index(
        'ln -sfn "${STAMP}"'
    )
