from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "check_prod_snapshot_sync_status.sh"


def _run_status(tmp_path: Path, *, launchctl_output: str, snapshot: str = "none"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launchctl = bin_dir / "launchctl"
    launchctl.write_text(
        "#!/bin/sh\ncat <<'EOF'\n" + launchctl_output + "\nEOF\n",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)

    plist_path = tmp_path / "snapshot.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump(
            {
                "Label": "com.viltrox.test-snapshot",
                "ProgramArguments": ["/bin/true"],
                "RunAtLoad": True,
            },
            handle,
        )

    sync_root = tmp_path / "snapshots"
    sync_root.mkdir()
    if snapshot != "none":
        bundle = sync_root / "20260716T120000Z"
        bundle.mkdir()
        (bundle / "prod-db.dump").write_bytes(b"real-postgres-backup-evidence")
        if snapshot in {"valid", "stale"}:
            digest = hashlib.sha256(b"real-postgres-backup-evidence").hexdigest()
            (bundle / "prod-db.dump.sha256").write_text(
                digest + "  prod-db.dump\n",
                encoding="utf-8",
            )
        elif snapshot == "invalid":
            (bundle / "prod-db.dump.sha256").write_text(
                "not-a-valid-sidecar\n",
                encoding="utf-8",
            )
        if snapshot == "stale":
            old = time.time() - (3 * 24 * 60 * 60)
            os.utime(bundle, (old, old))

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "LABEL": "com.viltrox.test-snapshot",
        "PLIST": str(plist_path),
        "SYNC_ROOT": str(sync_root),
    }
    result = subprocess.run(
        [str(SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, json.loads(result.stdout)


def test_loaded_but_never_run_is_not_healthy(tmp_path: Path) -> None:
    result, payload = _run_status(
        tmp_path,
        launchctl_output="state = waiting\nruns = 0",
    )

    assert result.returncode == 3
    assert payload["runtime_status"] == "loaded_not_yet_run"
    assert payload["healthy"] is False
    assert payload["successful_run"] is False


def test_success_requires_real_snapshot_and_valid_hash_sidecar(tmp_path: Path) -> None:
    result, payload = _run_status(
        tmp_path,
        launchctl_output="state = waiting\nruns = 1\nlast exit code = 0",
        snapshot="valid",
    )

    assert result.returncode == 0
    assert payload["runtime_status"] == "ready"
    assert payload["healthy"] is True
    assert payload["snapshot_present"] is True
    assert payload["hash_evidence"] is True


def test_successful_launch_without_valid_hash_evidence_is_unhealthy(tmp_path: Path) -> None:
    result, payload = _run_status(
        tmp_path,
        launchctl_output="state = waiting\nruns = 2\nlast exit code = 0",
        snapshot="invalid",
    )

    assert result.returncode == 3
    assert payload["runtime_status"] == "snapshot_hash_invalid"
    assert payload["healthy"] is False
    assert payload["hash_evidence"] is False


def test_successful_launch_with_stale_snapshot_is_unhealthy(tmp_path: Path) -> None:
    result, payload = _run_status(
        tmp_path,
        launchctl_output="state = waiting\nruns = 2\nlast exit code = 0",
        snapshot="stale",
    )

    assert result.returncode == 3
    assert payload["runtime_status"] == "snapshot_stale"
    assert payload["healthy"] is False
    assert payload["snapshot_fresh"] is False
    assert payload["latest_snapshot"]["age_seconds"] > payload["snapshot_max_age_seconds"]
