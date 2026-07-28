#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-viltrox}"
SYNC_SERVICE="${SYNC_SERVICE:-vkpi-sync-daily.service}"
LOG_DATE="${LOG_DATE:-$(date -u +%Y%m%d)}"
LOG_PATH="${LOG_PATH:-/var/log/vkpi/sync_daily_${LOG_DATE}.log}"

ssh "${SSH_TARGET}" "SYNC_SERVICE='${SYNC_SERVICE}' LOG_PATH='${LOG_PATH}' PYTHONDONTWRITEBYTECODE=1 python3 -B -" <<'PY'
from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
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


def parse_log_time(line: str) -> str:
    match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
    if not match:
        return ""
    try:
        parsed = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def actor_from_line(line: str) -> str:
    match = re.search(r"/acts/([^/]+)/runs", line)
    if not match:
        return ""
    return match.group(1).replace("~", "/")


def platform_from_actor(actor: str) -> str:
    lowered = actor.lower()
    if "tiktok" in lowered:
        return "tiktok"
    if "instagram" in lowered:
        return "instagram"
    if "youtube" in lowered:
        return "youtube"
    if "facebook" in lowered:
        return "facebook"
    if "reddit" in lowered:
        return "reddit"
    if "twitter" in lowered or "x-" in lowered:
        return "x"
    return ""


def apify_summary(lines: list[str]) -> dict:
    apify = [line for line in lines if "api.apify.com" in line]
    actor_posts = [line for line in apify if " HTTP Request: POST " in line and "/acts/" in line]
    dataset_fetches = [line for line in apify if "/datasets/" in line and "/items" in line]
    actor_counts = Counter(actor_from_line(line) for line in actor_posts)
    actor_counts.pop("", None)
    last_actor = actor_from_line(actor_posts[-1]) if actor_posts else ""
    last_apify_line = apify[-1] if apify else ""
    return {
        "apify_request_count": len(apify),
        "actor_run_count": len(actor_posts),
        "dataset_fetch_count": len(dataset_fetches),
        "actor_counts": dict(actor_counts),
        "last_actor": last_actor,
        "last_platform": platform_from_actor(last_actor),
        "last_activity_at": parse_log_time(last_apify_line),
        "last_apify_line": last_apify_line,
    }


def is_failure_line(line: str) -> bool:
    if any(pattern in line for pattern in ("Traceback", "ERROR", "FAILED")):
        return True
    if re.search(r"\bfailed\b", line, flags=re.IGNORECASE):
        return not re.search(r'"failed"\s*:\s*0\b', line)
    return False


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
apify = apify_summary(lines)
failure_lines = [
    line for line in lines
    if is_failure_line(line)
]
inferred_stage = ""
if apify.get("last_platform"):
    inferred_stage = f"{apify['last_platform']} provider sync"
elif apify.get("last_actor"):
    inferred_stage = f"{apify['last_actor']} provider sync"

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
    "last_apify_line": apify.get("last_apify_line", ""),
    "last_activity_at": apify.get("last_activity_at", ""),
    "inferred_stage": inferred_stage,
    "last_actor": apify.get("last_actor", ""),
    "last_platform": apify.get("last_platform", ""),
    "apify_request_count": apify.get("apify_request_count", 0),
    "actor_run_count": apify.get("actor_run_count", 0),
    "dataset_fetch_count": apify.get("dataset_fetch_count", 0),
    "actor_counts": apify.get("actor_counts", {}),
    "failure_tail": failure_lines[-10:],
    "last_log_line": lines[-1] if lines else "",
}

print(json.dumps(status, ensure_ascii=False, indent=2))
PY
