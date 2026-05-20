#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-viltrox}"
SYNC_SERVICE="${SYNC_SERVICE:-vkpi-sync-daily.service}"
LOG_DATE="${LOG_DATE:-$(date -u +%Y%m%d)}"
LOG_PATH="${LOG_PATH:-/var/log/vkpi/sync_daily_${LOG_DATE}.log}"

ssh "${SSH_TARGET}" "SYNC_SERVICE='${SYNC_SERVICE}' LOG_PATH='${LOG_PATH}' python3 -" <<'PY'
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def run(args: list[str]) -> str:
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return result.stdout.strip()
    except Exception:
        return ""


def tail_lines(path: Path, limit: int = 3000) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    except Exception as exc:
        return [f"log_read_error={type(exc).__name__}: {exc}"]
    return lines[-limit:]


service = os.environ.get("SYNC_SERVICE") or "vkpi-sync-daily.service"
log_path = Path(os.environ.get("LOG_PATH") or "/var/log/vkpi/sync_daily.log")
lines = tail_lines(log_path)
marker_patterns = (
    "cron_daily_sync_",
    "daily sync stage",
    "daily sync official",
    "daily sync kol light",
    "finished_at",
    "Traceback",
    "ERROR",
    "FAILED",
    "failed",
)
progress_lines = [
    line for line in lines
    if any(pattern in line for pattern in marker_patterns)
]
apify_lines = [line for line in lines if "api.apify.com" in line]
failure_lines = [
    line for line in lines
    if any(pattern in line for pattern in ("Traceback", "ERROR", "FAILED", "failed"))
]

status = {
    "service": service,
    "service_state": run(["systemctl", "is-active", service]) or "unknown",
    "active_state": run(["systemctl", "show", service, "-p", "ActiveState", "--value"]) or "unknown",
    "sub_state": run(["systemctl", "show", service, "-p", "SubState", "--value"]) or "unknown",
    "result": run(["systemctl", "show", service, "-p", "Result", "--value"]) or "unknown",
    "exec_main_status": run(["systemctl", "show", service, "-p", "ExecMainStatus", "--value"]) or "unknown",
    "log_path": str(log_path),
    "log_exists": log_path.exists(),
    "log_size_bytes": log_path.stat().st_size if log_path.exists() else 0,
    "tail_lines_scanned": len(lines),
    "progress_tail": progress_lines[-20:],
    "last_progress_line": progress_lines[-1] if progress_lines else "",
    "last_apify_line": apify_lines[-1] if apify_lines else "",
    "failure_tail": failure_lines[-10:],
    "last_log_line": lines[-1] if lines else "",
}

print(json.dumps(status, ensure_ascii=False, indent=2))
PY
