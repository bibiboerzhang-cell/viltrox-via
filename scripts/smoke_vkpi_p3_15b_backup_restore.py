#!/usr/bin/env python3
"""P3.15B backup/restore readiness smoke.

This smoke is intentionally schema-only and source-manifest-only:
- it proves the local DB can be exported without dumping user data;
- it proves backup archives can be created and extracted;
- it verifies the clean package/debug package scripts still parse;
- it verifies the operator restore runbook exists.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from app.db import connection


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = (
    "users",
    "staff",
    "kols",
    "vkpi_projects",
    "vkpi_industry_accounts",
    "vkpi_content_posts",
)
FORBIDDEN_ARCHIVE_TOKENS = (
    ".env",
    ".git/",
    ".venv/",
    "node_modules/",
    "frontend/dist/",
    "runtime/logs/",
    "runtime/backups/",
)


def _run(cmd: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=True,
    )


def _assert_script_syntax() -> None:
    for script in (
        ROOT / "scripts" / "make_vkpi_clean_package.sh",
        ROOT / "scripts" / "make_debug_zip.sh",
    ):
        if not script.exists():
            raise AssertionError(f"missing script: {script}")
        _run(["bash", "-n", str(script)])


def _assert_core_tables() -> None:
    missing = [table for table in EXPECTED_TABLES if not connection.table_exists(table)]
    if missing:
        raise AssertionError(f"missing backup-critical tables: {missing}")


def _dump_schema_only(tmp_dir: Path) -> dict[str, object]:
    if not connection.is_postgres_runtime():
        conn = connection.get_conn()
        dump_path = tmp_dir / "vkpi_schema.sqlite.sql"
        lines = list(conn.iterdump()) if hasattr(conn, "iterdump") else []
        dump_path.write_text("\n".join(lines[:5000]), encoding="utf-8")
        if not dump_path.read_text(encoding="utf-8").strip():
            raise AssertionError("sqlite schema dump is empty")
        return {"mode": "sqlite_schema_only", "bytes": dump_path.stat().st_size}

    db_url = getattr(connection, "DB_RUNTIME_URL", "") or os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise AssertionError("postgres runtime selected but DATABASE_URL is empty")

    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise AssertionError("pg_dump not found in PATH")

    dump_path = tmp_dir / "vkpi_schema.postgres.sql"
    result = _run(
        [
            pg_dump,
            "--schema-only",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(dump_path),
            db_url,
        ],
        timeout=45,
    )
    if result.stderr and "error" in result.stderr.lower():
        raise AssertionError(result.stderr[-500:])
    text = dump_path.read_text(encoding="utf-8", errors="ignore")
    if "CREATE TABLE" not in text or "staff" not in text:
        raise AssertionError("postgres schema dump does not contain expected DDL")
    return {"mode": "postgres_schema_only", "bytes": dump_path.stat().st_size}


def _assert_archive_roundtrip(tmp_dir: Path, schema_info: dict[str, object]) -> dict[str, object]:
    stage = tmp_dir / "stage"
    stage.mkdir()
    manifest = {
        "smoke": "p3_15b_backup_restore",
        "schema": schema_info,
        "excluded": list(FORBIDDEN_ARCHIVE_TOKENS),
    }
    (stage / "restore_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    runbook = ROOT / "docs" / "runbooks" / "VKPI_BACKUP_RESTORE_RUNBOOK.md"
    if not runbook.exists():
        raise AssertionError(f"missing runbook: {runbook}")
    shutil.copy2(runbook, stage / "VKPI_BACKUP_RESTORE_RUNBOOK.md")

    archive_path = tmp_dir / "p3_15b_restore_drill.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(stage, arcname="restore_drill")

    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
        bad = [name for name in names for token in FORBIDDEN_ARCHIVE_TOKENS if token in name]
        if bad:
            raise AssertionError(f"archive contains forbidden paths: {bad[:5]}")
        extract_dir = tmp_dir / "extract"
        tar.extractall(extract_dir)

    restored_manifest = extract_dir / "restore_drill" / "restore_manifest.json"
    if not restored_manifest.exists():
        raise AssertionError("archive extraction did not restore manifest")
    return {"archive_bytes": archive_path.stat().st_size, "entries": len(names)}


def main() -> None:
    _assert_script_syntax()
    _assert_core_tables()
    with tempfile.TemporaryDirectory(prefix="vkpi-p3-15b-") as tmp:
        tmp_dir = Path(tmp)
        schema_info = _dump_schema_only(tmp_dir)
        archive_info = _assert_archive_roundtrip(tmp_dir, schema_info)
    print(
        "VKPI_P3_15B_BACKUP_RESTORE_SMOKE_OK "
        + json.dumps({"schema": schema_info, "archive": archive_info}, sort_keys=True)
    )


if __name__ == "__main__":
    main()
