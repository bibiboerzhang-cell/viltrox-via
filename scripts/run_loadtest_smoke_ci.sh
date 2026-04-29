#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

LOG_DIR="$ROOT/runtime/logs"
mkdir -p "$LOG_DIR"

PORT="${LOAD_TEST_SMOKE_PORT:-8101}"
BASE_URL="http://127.0.0.1:${PORT}"
SERVER_LOG="$LOG_DIR/loadtest-ci-server.log"
SERVER_PID=""

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" || true
    for _ in {1..20}; do
      if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
      fi
      sleep 0.5
    done
    if kill -0 "$SERVER_PID" 2>/dev/null; then
      kill -9 "$SERVER_PID" || true
    fi
  fi
}
trap cleanup EXIT

cd "$ROOT"
export PYTHONPATH="$ROOT/backend:${PYTHONPATH:-}"
export ENVIRONMENT="${ENVIRONMENT:-development}"
export DB_RUNTIME_BACKEND="${DB_RUNTIME_BACKEND:-sqlite}"
export ENABLE_SCHEDULER=0
export ENABLE_BROWSER=0
export ENABLE_UPLOAD_CLEANUP=0
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-AdminPass123!}"

"$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$SERVER_LOG" 2>&1 &
SERVER_PID="$!"

for _ in {1..60}; do
  if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then
  echo "loadtest smoke failed: backend did not become healthy"
  echo "--- server log ---"
  tail -n 120 "$SERVER_LOG" || true
  exit 1
fi

export LOAD_TEST_PUBLIC_BASE="$BASE_URL"
export LOAD_TEST_ADMIN_BASE="$BASE_URL"
export LOAD_TEST_INCLUDE_ADMIN="${LOAD_TEST_INCLUDE_ADMIN:-0}"
export LOAD_TEST_PHASES="${LOAD_TEST_PHASES:-20,40,80}"
export LOAD_TEST_REQUESTS_PER_PHASE="${LOAD_TEST_REQUESTS_PER_PHASE:-120}"
export LOAD_TEST_TIMEOUT_SEC="${LOAD_TEST_TIMEOUT_SEC:-20}"

"$PYTHON_BIN" "$ROOT/scripts/load_test_ramp.py"

REPORT_PATH="$(ls -1t "$LOG_DIR"/load-test-*.json | head -n1)"
export REPORT_PATH
"$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

report_path = Path(os.environ["REPORT_PATH"])
summary = json.loads(report_path.read_text(encoding="utf-8"))
phases = summary.get("phases", [])
if not phases:
    raise SystemExit("loadtest smoke failed: empty phase summary")

worst_success = min(float(p.get("success_rate", 0.0)) for p in phases)
worst_p95 = max(float(p.get("latency_ms", {}).get("p95", 0.0)) for p in phases)

print(f"loadtest_smoke report={report_path}")
print(f"worst_success_rate={worst_success:.4f}")
print(f"worst_p95_ms={worst_p95:.2f}")

if worst_success < 0.90:
    raise SystemExit(f"loadtest smoke failed: success rate too low ({worst_success:.4f})")
if worst_p95 > 3000:
    raise SystemExit(f"loadtest smoke failed: p95 too high ({worst_p95:.2f}ms)")
PY
