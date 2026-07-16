#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

LABEL="${LABEL:-com.viltrox.prod-snapshot-sync}"
PLIST="${PLIST:-${HOME}/Library/LaunchAgents/${LABEL}.plist}"
SYNC_ROOT="${SYNC_ROOT:-runtime/prod-sync}"

python3 - <<'PY'
import json
import hashlib
import os
import plistlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

label = os.environ.get("LABEL") or "com.viltrox.prod-snapshot-sync"
plist_path = Path(os.environ.get("PLIST") or Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")
sync_root = Path(os.environ.get("SYNC_ROOT") or "runtime/prod-sync")
max_age_seconds = max(3600, int(os.environ.get("SNAPSHOT_MAX_AGE_SECONDS") or 129600))

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True)

def tail_text(path_value: object, *, max_bytes: int = 16384) -> str:
    path = Path(str(path_value or "")).expanduser()
    if not str(path_value or "").strip() or not path.is_file():
        return ""
    with path.open("rb") as handle:
        size = path.stat().st_size
        if size > max_bytes:
            handle.seek(size - max_bytes)
        return handle.read(max_bytes).decode("utf-8", errors="replace")

def latest_snapshot() -> dict[str, object]:
    if not sync_root.exists():
        return {"exists": False}
    candidates = [
        path
        for path in sync_root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and (path / "prod-db.dump").is_file()
        and not (path / "prod-db.dump").is_symlink()
    ]
    if not candidates:
        return {"exists": False}
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    dump = latest / "prod-db.dump"
    sidecar = latest / "prod-db.dump.sha256"
    readme = latest / "README.txt"
    sidecar_safe = sidecar.is_file() and not sidecar.is_symlink()
    sidecar_text = sidecar.read_text(encoding="utf-8", errors="replace").strip() if sidecar_safe else ""
    sidecar_match = re.fullmatch(r"([0-9a-fA-F]{64})\s+prod-db\.dump", sidecar_text)
    actual_sha256 = ""
    if sidecar_match:
        digest = hashlib.sha256()
        with dump.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_sha256 = digest.hexdigest()
    sha256_verified = bool(
        sidecar_match
        and actual_sha256 == str(sidecar_match.group(1)).lower()
    )
    age_seconds = max(0, int(datetime.now(timezone.utc).timestamp() - latest.stat().st_mtime))
    return {
        "exists": True,
        "path": str(latest),
        "dump_size_bytes": dump.stat().st_size,
        "mtime": datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_seconds": age_seconds,
        "fresh": age_seconds <= max_age_seconds,
        "has_sha256": sidecar_safe,
        "sha256_sidecar_valid": bool(sidecar_match),
        "sha256_verified": sha256_verified,
        "has_runtime_state": (latest / "runtime-state.txt").exists(),
        "readme": readme.read_text(encoding="utf-8", errors="ignore").splitlines() if readme.exists() else [],
    }

plist = {}
if plist_path.exists():
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)

launchctl = run(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
state = "unknown"
runs = ""
last_exit = ""
for line in launchctl.stdout.splitlines():
    stripped = line.strip()
    if stripped.startswith("state = "):
        state = stripped.split("=", 1)[1].strip()
    elif stripped.startswith("runs = "):
        runs = stripped.split("=", 1)[1].strip()
    elif stripped.startswith("last exit code = "):
        last_exit = stripped.split("=", 1)[1].strip()

calendar = plist.get("StartCalendarInterval") if isinstance(plist, dict) else {}
program_args = plist.get("ProgramArguments") if isinstance(plist, dict) else []
stdout_path = plist.get("StandardOutPath") if isinstance(plist, dict) else ""
stderr_path = plist.get("StandardErrorPath") if isinstance(plist, dict) else ""
log_paths = [
    stdout_path,
    stderr_path,
    sync_root / "local-snapshot-sync.log",
]
recent_log = "\n".join(tail_text(path) for path in log_paths if str(path or "").strip())
last_exit_failed = bool(last_exit) and last_exit != "0"
try:
    run_count = int(runs)
except (TypeError, ValueError):
    run_count = 0
permission_blocked = last_exit_failed and "operation not permitted" in recent_log.lower()
snapshot = latest_snapshot()
snapshot_present = bool(snapshot.get("exists")) and int(snapshot.get("dump_size_bytes") or 0) > 0
hash_evidence = bool(snapshot.get("sha256_verified"))
snapshot_fresh = bool(snapshot.get("fresh"))
successful_run = run_count > 0 and last_exit == "0"

if launchctl.returncode != 0 or not plist_path.exists():
    runtime_status = "not_loaded"
elif not last_exit or run_count <= 0:
    runtime_status = "loaded_not_yet_run"
elif permission_blocked:
    runtime_status = "permission_blocked"
elif last_exit_failed:
    runtime_status = "last_run_failed"
elif not snapshot_present:
    runtime_status = "snapshot_missing"
elif not hash_evidence:
    runtime_status = "snapshot_hash_invalid"
elif not snapshot_fresh:
    runtime_status = "snapshot_stale"
else:
    runtime_status = "ready"

healthy = (
    runtime_status == "ready"
    and successful_run
    and snapshot_present
    and hash_evidence
    and snapshot_fresh
)

payload = {
    "checked_at": utcnow(),
    "label": label,
    "plist_exists": plist_path.exists(),
    "plist": str(plist_path),
    "loaded": launchctl.returncode == 0,
    "state": state,
    "runs": runs,
    "run_count": run_count,
    "last_exit_code": last_exit,
    "runtime_status": runtime_status,
    "healthy": healthy,
    "permission_blocked": permission_blocked,
    "successful_run": successful_run,
    "snapshot_present": snapshot_present,
    "hash_evidence": hash_evidence,
    "snapshot_fresh": snapshot_fresh,
    "snapshot_max_age_seconds": max_age_seconds,
    "remediation": (
        "Grant the launch agent executable access to the macOS protected Documents folder "
        "or move the scheduled wrapper outside that folder, then bootstrap and kickstart the agent."
        if permission_blocked
        else ""
    ),
    "run_at_load": bool(plist.get("RunAtLoad")) if isinstance(plist, dict) else False,
    "schedule": calendar or {},
    "program": program_args,
    "sync_root": str(sync_root),
    "latest_snapshot": snapshot,
}
print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
raise SystemExit(0 if payload["healthy"] else 3)
PY
