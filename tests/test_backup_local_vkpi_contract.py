from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest


SOURCE = Path(__file__).parents[1] / "scripts" / "ops" / "backup_local_vkpi.sh"
STAMP = "20260713T200000Z"
SECRET_URL = (
    "postgresql://backup_user:do-not-print-%21this@127.0.0.1:54329/viltrox2"
    "?sslmode=prefer&application_name=vkpi%20backup"
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _make_fake_repo(tmp_path: Path, *, mode: str = "success") -> tuple[Path, dict[str, str]]:
    root = tmp_path / "repo"
    script = root / "scripts" / "ops" / SOURCE.name
    script.parent.mkdir(parents=True)
    shutil.copy2(SOURCE, script)
    (root / ".env").write_text(f'DATABASE_URL="{SECRET_URL}"\nOTHER=value\n', encoding="utf-8")
    # A newer migration file must not influence metadata; the fake DB reports 244.
    migrations = root / "migrations"
    migrations.mkdir()
    (migrations / "999_not_applied.sql").write_text("SELECT 1;\n", encoding="utf-8")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "pg_dump",
        """
mode="$(stat -c '%a' "$PGSERVICEFILE" 2>/dev/null || stat -f '%Lp' "$PGSERVICEFILE")"
pass_mode="$(stat -c '%a' "$PGPASSFILE" 2>/dev/null || stat -f '%Lp' "$PGPASSFILE")"
[ "$PGSERVICE" = 'vkpi_backup' ]
[ "$mode" = '600' ]
[ "$pass_mode" = '600' ]
grep -F -- 'application_name=vkpi backup' "$PGSERVICEFILE" >/dev/null
grep -F -- "$DATABASE_PASSWORD" "$PGPASSFILE" >/dev/null
printf 'service=%s mode=%s pgpass_mode=%s secret=present\\n' "$PGSERVICE" "$mode" "$pass_mode" >> "$COMMAND_LOG"
printf 'pg_dump %s\\n' "$*" >> "$COMMAND_LOG"
out=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = '-f' ]; then out="$2"; shift 2; else shift; fi
done
case "$FAKE_MODE" in
  zero) : > "$out" ;;
  dump_fail) printf '%s\\n' "$DATABASE_SECRET" >&2; exit 2 ;;
  *) printf 'PGDMP-valid-fixture' > "$out" ;;
esac
""",
    )
    _write_executable(
        fake_bin / "pg_restore",
        """
printf 'pg_restore %s\\n' "$*" >> "$COMMAND_LOG"
if [ "$FAKE_MODE" = 'restore_fail' ]; then
  printf '%s\\n' "$DATABASE_SECRET" >&2
  exit 3
fi
[ -s "${@: -1}" ]
printf 'fixture archive list\\n'
""",
    )
    _write_executable(
        fake_bin / "psql",
        """
mode="$(stat -c '%a' "$PGSERVICEFILE" 2>/dev/null || stat -f '%Lp' "$PGSERVICEFILE")"
pass_mode="$(stat -c '%a' "$PGPASSFILE" 2>/dev/null || stat -f '%Lp' "$PGPASSFILE")"
[ "$PGSERVICE" = 'vkpi_backup' ]
[ "$mode" = '600' ]
[ "$pass_mode" = '600' ]
grep -F -- 'application_name=vkpi backup' "$PGSERVICEFILE" >/dev/null
grep -F -- "$DATABASE_PASSWORD" "$PGPASSFILE" >/dev/null
printf 'service=%s mode=%s pgpass_mode=%s secret=present\\n' "$PGSERVICE" "$mode" "$pass_mode" >> "$COMMAND_LOG"
printf 'psql %s\\n' "$*" >> "$COMMAND_LOG"
if [ "$FAKE_MODE" = 'migration_fail' ]; then
  printf '%s\\n' "$DATABASE_SECRET" >&2
  exit 4
fi
printf '244_vkpi_event_radar_truth_scope.sql\\n'
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "STAMP": STAMP,
            "FAKE_MODE": mode,
            "COMMAND_LOG": str(command_log),
            "DATABASE_SECRET": SECRET_URL,
            "DATABASE_PASSWORD": "do-not-print-!this",
        }
    )
    return root, env


def _run(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/ops/backup_local_vkpi.sh"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _assert_no_published_bundle(root: Path) -> None:
    assert not list((root / "runtime" / "db-backups").glob("vkpi-*"))
    assert not list((root / "runtime" / "env-backups").glob(".env.*"))
    assert not list((root / "runtime").rglob("*.tmp.*"))


def test_backup_publishes_only_verified_database_state(tmp_path: Path) -> None:
    root, env = _make_fake_repo(tmp_path)

    result = _run(root, env)

    assert result.returncode == 0, result.stderr
    dump = root / "runtime" / "db-backups" / f"vkpi-{STAMP}.dump"
    sidecar = dump.with_suffix(".dump.sha256")
    meta_path = root / "runtime" / "db-backups" / f"vkpi-{STAMP}.meta.json"
    env_copy = root / "runtime" / "env-backups" / f".env.{STAMP}"
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    assert dump.read_bytes() == b"PGDMP-valid-fixture"
    assert sidecar.read_text(encoding="utf-8") == f"{digest}  {dump.name}\n"
    assert env_copy.read_text(encoding="utf-8").startswith("DATABASE_URL=")
    assert stat.S_IMODE(env_copy.stat().st_mode) == 0o600
    assert json.loads(meta_path.read_text(encoding="utf-8")) == {
        "stamp": STAMP,
        "migration_max": "244_vkpi_event_radar_truth_scope.sql",
        "migration_max_source": "schema_migrations",
        "dump": dump.name,
        "dump_bytes": len(dump.read_bytes()),
        "dump_sha256": digest,
        "archive_verified": True,
    }
    command_log = (tmp_path / "commands.log").read_text(encoding="utf-8")
    assert "pg_restore --list" in command_log
    assert "SELECT MAX(version_key) FROM schema_migrations" in command_log
    assert command_log.index("psql ") < command_log.index("pg_dump ")
    pg_dump_line = next(line for line in command_log.splitlines() if line.startswith("pg_dump "))
    assert f"{dump}.tmp" not in pg_dump_line
    assert ".dump.tmp." in pg_dump_line
    assert command_log.count(
        "service=vkpi_backup mode=600 pgpass_mode=600 secret=present"
    ) == 2
    assert SECRET_URL not in command_log
    assert SECRET_URL not in result.stdout
    assert SECRET_URL not in result.stderr
    assert 'pg_restore --clean --no-owner -d "$DATABASE_URL"' not in result.stdout
    assert "postgres_restore_rehearsal.py" in result.stdout
    assert not list(root.rglob("*.pgservice.tmp.*"))
    assert not list(root.rglob("*.pgpass.tmp.*"))


def test_existing_backup_stamp_is_never_overwritten_or_deleted(tmp_path: Path) -> None:
    root, env = _make_fake_repo(tmp_path)
    first = _run(root, env)
    assert first.returncode == 0, first.stderr
    dump = root / "runtime" / "db-backups" / f"vkpi-{STAMP}.dump"
    before = dump.read_bytes()

    second = _run(root, env)

    assert second.returncode != 0
    assert "拒绝覆盖" in second.stderr
    assert dump.read_bytes() == before


@pytest.mark.parametrize("mode", ["zero", "restore_fail", "migration_fail", "dump_fail"])
def test_backup_failure_leaves_no_success_looking_bundle(tmp_path: Path, mode: str) -> None:
    root, env = _make_fake_repo(tmp_path, mode=mode)

    result = _run(root, env)

    assert result.returncode != 0
    _assert_no_published_bundle(root)
    assert SECRET_URL not in result.stdout
    assert SECRET_URL not in result.stderr
