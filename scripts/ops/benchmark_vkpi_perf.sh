#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
RUNS="${RUNS:-5}"
SYNC_SERVICE="${SYNC_SERVICE:-vkpi-sync-daily.service}"
ALLOW_DURING_SYNC="${ALLOW_DURING_SYNC:-0}"

ssh "${SSH_TARGET}" "REMOTE_ROOT='${REMOTE_ROOT}' RUNS='${RUNS}' PYTHON_BIN='${PYTHON_BIN}' SYNC_SERVICE='${SYNC_SERVICE}' ALLOW_DURING_SYNC='${ALLOW_DURING_SYNC}' bash -s" <<'REMOTE'
set -euo pipefail
cd "${REMOTE_ROOT}"

sync_state="$(systemctl is-active "${SYNC_SERVICE}" 2>/dev/null || true)"
if [ "${ALLOW_DURING_SYNC}" != "1" ] && [ "${sync_state}" = "active" -o "${sync_state}" = "activating" ]; then
  echo "{\"skipped\":true,\"reason\":\"${SYNC_SERVICE} is ${sync_state}; set ALLOW_DURING_SYNC=1 to override\"}"
  exit 0
fi

RUNS="${RUNS}" PYTHONPATH=backend "${PYTHON_BIN}" - <<'PY'
import json
import os
import statistics
import time

from app.services.cache import get_cache_stats
from app.domains.kol import history_match as kol_history_match
from app.domains.kol import pool as kol_pool
from app.domains import channels

staff = {"id": 0, "staff_id": 0, "user_id": 0, "role": "admin", "is_owner": 1}
runs = max(1, int(os.environ.get("RUNS") or 5))

checks = [
    ("channels.list_channels", lambda: channels.list_channels(staff=staff, limit=100)),
    ("channels.official_account_matrix", lambda: channels.official_account_matrix(staff=staff, limit=50)),
    ("channels.official_views_evidence", lambda: channels.official_views_evidence(staff=staff, limit=120)),
    ("channels.team_overview", lambda: channels.team_overview()),
    ("kol_pool.list_pool.fit", lambda: kol_pool.list_pool(limit=100, sort_by="fit")),
    ("kol_pool.list_pool.query", lambda: kol_pool.list_pool(limit=100, query="viltrox")),
    ("kol_history.search.viltrox", lambda: kol_history_match.search_pool_for_natural("viltrox", {"platform": "youtube", "keywords": ["viltrox"]}, limit=50)),
]

report = []
for name, fn in checks:
    samples = []
    payload_bytes = 0
    error = ""
    for _ in range(runs):
        started = time.perf_counter()
        try:
            result = fn()
            payload_bytes = len(json.dumps(result, ensure_ascii=False, default=str))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
        samples.append((time.perf_counter() - started) * 1000)
    if error:
        report.append({"name": name, "error": error})
    else:
        report.append({
            "name": name,
            "runs": runs,
            "min_ms": round(min(samples), 2),
            "avg_ms": round(statistics.mean(samples), 2),
            "max_ms": round(max(samples), 2),
            "payload_kb": round(payload_bytes / 1024, 1),
        })

print(json.dumps({"checks": report, "cache": get_cache_stats()}, ensure_ascii=False, indent=2))
PY
REMOTE
