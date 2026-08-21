from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = ROOT / "scripts" / "package_share.sh"
SHARE_IGNORE = ROOT / ".shareignore"


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(PACKAGE_SCRIPT), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_package_share_uses_tracked_allowlist_and_writes_verified_sidecars(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    shutil.copy2(SHARE_IGNORE, checkout / ".shareignore")

    _write(checkout / "app" / "main.py", "print('safe source')\n")
    _write(checkout / "migrations" / "001_init.sql", "SELECT 1;\n")
    _write(checkout / "backend" / "app" / "db" / "sql" / "001_v5_admin_schema.sql", "SELECT 2;\n")
    _write(checkout / "backend" / "app" / "db" / "sql" / "unreviewed.sql", "SELECT 3;\n")
    _write(checkout / ".env.example", "JWT_SECRET=\nADMIN_PASSWORD=\n")
    _write(checkout / ".env", "DUMMY_SECRET=must-never-be-read-or-printed\n")
    _write(checkout / ".env.production", "DUMMY_SECRET=excluded\n")
    _write(checkout / "nested" / ".env.example", "DUMMY_SECRET=excluded\n")
    _write(checkout / "private.key", "dummy private key material\n")
    _write(checkout / "certificate.pem", "dummy certificate material\n")
    _write(checkout / "snapshot.sqlite3", "dummy database material\n")
    _write(checkout / "backups" / "production.sql", "dummy backup material\n")
    _write(checkout / "runtime" / "worker.log", "dummy runtime material\n")
    _write(checkout / "reports" / "audit.html", "dummy report material\n")
    _write(checkout / ".venv" / "bin" / "python", "dummy toolchain material\n")
    _write(checkout / "frontend" / "node_modules" / "module.js", "dummy dependency\n")
    _write(checkout / "frontend" / "dist" / "bundle.js", "dummy build output\n")
    _write(checkout / ".pytest_cache" / "state", "dummy cache material\n")

    subprocess.run(["git", "-C", str(checkout), "add", "-f", "."], check=True)
    _write(checkout / "untracked.txt", "must not enter a tracked-only package\n")
    # The package must succeed without opening an excluded tracked secret.
    os.chmod(checkout / ".env", 0)

    output = tmp_path / "out" / "portable.tar.gz"
    dry_run = _run(
        "--root",
        str(checkout),
        "--output",
        str(output),
        "--dry-run",
        "--list",
    )
    assert "app/main.py" in dry_run.stdout
    assert "migrations/001_init.sql" in dry_run.stdout
    assert "backend/app/db/sql/001_v5_admin_schema.sql" in dry_run.stdout
    assert "viltrox-2.0/.env.example" in dry_run.stdout
    assert "DUMMY_SECRET" not in dry_run.stdout
    assert "nested/.env.example" not in dry_run.stdout
    assert ".env.production" not in dry_run.stdout
    assert "unreviewed.sql" not in dry_run.stdout
    assert not output.exists()
    assert not Path(f"{output}.sha256").exists()
    assert not Path(f"{output}.files.txt").exists()

    built = _run("--root", str(checkout), "--output", str(output))
    assert "tracked allowlist validated" in built.stdout
    checksum_path = Path(f"{output}.sha256")
    manifest_path = Path(f"{output}.files.txt")
    assert output.is_file()
    assert checksum_path.is_file()
    assert manifest_path.is_file()

    with tarfile.open(output, "r:gz") as archive:
        archived_files = sorted(
            member.name.rstrip("/")
            for member in archive.getmembers()
            if not member.isdir()
        )
    manifest_files = manifest_path.read_text(encoding="utf-8").splitlines()
    assert archived_files == manifest_files
    assert "viltrox-2.0/app/main.py" in archived_files
    assert "viltrox-2.0/migrations/001_init.sql" in archived_files
    assert "viltrox-2.0/backend/app/db/sql/001_v5_admin_schema.sql" in archived_files
    assert "viltrox-2.0/.env.example" in archived_files
    assert "viltrox-2.0/untracked.txt" not in archived_files
    assert "viltrox-2.0/nested/.env.example" not in archived_files
    assert "viltrox-2.0/.env.production" not in archived_files
    assert "viltrox-2.0/backend/app/db/sql/unreviewed.sql" not in archived_files
    assert not any("node_modules" in path for path in archived_files)
    assert not any("frontend/dist" in path for path in archived_files)
    assert not any("runtime/" in path for path in archived_files)
    assert not any("reports/" in path for path in archived_files)
    assert not any("backups/" in path for path in archived_files)

    recorded_digest, recorded_name = checksum_path.read_text(
        encoding="utf-8"
    ).split()
    assert recorded_name == output.name
    assert recorded_digest == hashlib.sha256(output.read_bytes()).hexdigest()

    refused = _run(
        "--root",
        str(checkout),
        "--output",
        str(output),
        check=False,
    )
    assert refused.returncode != 0
    assert "refusing to overwrite" in refused.stderr
    assert "DUMMY_SECRET" not in refused.stdout + refused.stderr
