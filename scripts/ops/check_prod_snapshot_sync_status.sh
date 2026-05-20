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
import os
import plistlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

label = os.environ.get("LABEL") or "com.viltrox.prod-snapshot-sync"
plist_path = Path(os.environ.get("PLIST") or Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")
sync_root = Path(os.environ.get("SYNC_ROOT") or "runtime/prod-sync")

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True)

def latest_snapshot() -> dict[str, object]:
    if not sync_root.exists():
        return {"exists": False}
    candidates = [path for path in sync_root.iterdir() if path.is_dir() and (path / "prod-db.dump").exists()]
    if not candidates:
        return {"exists": False}
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    dump = latest / "prod-db.dump"
    readme = latest / "README.txt"
    return {
        "exists": True,
        "path": str(latest),
        "dump_size_bytes": dump.stat().st_size,
        "mtime": datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "has_sha256": (latest / "prod-db.dump.sha256").exists(),
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

payload = {
    "checked_at": utcnow(),
    "label": label,
    "plist_exists": plist_path.exists(),
    "plist": str(plist_path),
    "loaded": launchctl.returncode == 0,
    "state": state,
    "runs": runs,
    "last_exit_code": last_exit,
    "run_at_load": bool(plist.get("RunAtLoad")) if isinstance(plist, dict) else False,
    "schedule": calendar or {},
    "program": program_args,
    "sync_root": str(sync_root),
    "latest_snapshot": latest_snapshot(),
}
print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
raise SystemExit(0 if payload["loaded"] and payload["plist_exists"] else 2)
PY
