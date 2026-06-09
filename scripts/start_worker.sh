#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ENVIRONMENT="${ENVIRONMENT:-local}"
export APP_ROLE="${APP_ROLE:-worker}"
source "$ROOT/scripts/runtime_env.sh"

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi
PIDFILE="${PIDFILE:-$ROOT/runtime/worker.pid}"
LOGFILE="${LOGFILE:-$ROOT/runtime/logs/worker.log}"

cd "$ROOT"
mkdir -p "$ROOT/runtime/logs"

export DB_RUNTIME_BACKEND="${DB_RUNTIME_BACKEND:-postgres}"
export ENABLE_SCHEDULER="${ENABLE_SCHEDULER:-0}"
export ENABLE_BROWSER="${ENABLE_BROWSER:-0}"
export ENABLE_UPLOAD_CLEANUP="${ENABLE_UPLOAD_CLEANUP:-0}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"

if [[ -f "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "apify worker already running with pid $PID"
    exit 0
  fi
  rm -f "$PIDFILE"
fi

# nohup only protects against SIGHUP. Codex/PTY runners can also clean up their
# own process group after the command returns, so put the worker in a new session
# before execing the real process.
nohup "$PYTHON_BIN" -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
  "$PYTHON_BIN" -m app.workers.apify_jobs_worker >>"$LOGFILE" 2>&1 </dev/null &
PID="$!"
echo "$PID" >"$PIDFILE"
sleep 0.5
if ! kill -0 "$PID" 2>/dev/null; then
  rm -f "$PIDFILE"
  echo "apify worker failed to start; tail $LOGFILE:" >&2
  tail -n 40 "$LOGFILE" >&2 || true
  exit 1
fi

echo "apify worker started pid $PID"
echo "pidfile: $PIDFILE"
echo "logfile: $LOGFILE"
