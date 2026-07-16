from __future__ import annotations

import hashlib
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

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"

    _write_executable(
        fake_bin / "ssh",
        """
printf 'ssh %s\\n' "$*" >> "$COMMAND_LOG"
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
service_mode="$(stat -f '%Lp' "$PGSERVICEFILE" 2>/dev/null || stat -c '%a' "$PGSERVICEFILE")"
pass_mode="$(stat -f '%Lp' "$PGPASSFILE" 2>/dev/null || stat -c '%a' "$PGPASSFILE")"
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
        }
    )
    return env, local_parent, command_log


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
