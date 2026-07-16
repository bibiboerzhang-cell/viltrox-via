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

# Bind every heartbeat to the exact deployed source revision. Deploy writes
# BUILD_GIT_SHA before starting the worker; local development can derive HEAD.
# Starting without a valid source identity would make runtime trust
# unprovable, so this path deliberately fails closed.
WORKER_SOURCE_SHA=""
if [[ "$ENVIRONMENT" == "production" && -f "$ROOT/BUILD_GIT_SHA" ]]; then
  WORKER_SOURCE_SHA="$(tr -d '[:space:]' < "$ROOT/BUILD_GIT_SHA")"
fi
if [[ -z "$WORKER_SOURCE_SHA" && "$ENVIRONMENT" != "production" ]] \
  && command -v git >/dev/null 2>&1; then
  WORKER_SOURCE_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
fi
if [[ -z "$WORKER_SOURCE_SHA" ]]; then
  WORKER_SOURCE_SHA="${APP_GIT_SHA:-}"
fi
WORKER_SOURCE_SHA="$(printf '%s' "$WORKER_SOURCE_SHA" | tr '[:upper:]' '[:lower:]')"
if [[ ! "$WORKER_SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "refusing to start worker without a valid 40-hex source identity" >&2
  exit 1
fi
export APP_GIT_SHA="$WORKER_SOURCE_SHA"

DEFAULT_PIDFILE="$ROOT/runtime/worker.pid"
PIDFILE="${PIDFILE:-$DEFAULT_PIDFILE}"
LOGFILE="${LOGFILE:-$ROOT/runtime/logs/worker.log}"
PID_GUARD="$ROOT/scripts/worker_pid_guard.py"

# A custom pidfile means this is one member of a process fleet.  Requiring an
# explicit lane and heartbeat identity prevents the old failure mode where
# worker-2.pid and worker-3.pid silently ran as duplicate all-lane workers and
# overwrote the singleton heartbeat row.
if [[ "$PIDFILE" != "$DEFAULT_PIDFILE" ]]; then
  if [[ -z "${APIFY_WORKER_HEARTBEAT_NAME:-}" ]]; then
    echo "custom PIDFILE requires APIFY_WORKER_HEARTBEAT_NAME" >&2
    exit 2
  fi
  if [[ -z "${APIFY_WORKER_CLAIM_LANE:-}" ]]; then
    echo "custom PIDFILE requires APIFY_WORKER_CLAIM_LANE" >&2
    exit 2
  fi
fi

cd "$ROOT"
mkdir -p "$ROOT/runtime/logs"

export DB_RUNTIME_BACKEND="${DB_RUNTIME_BACKEND:-postgres}"
export ENABLE_SCHEDULER="${ENABLE_SCHEDULER:-0}"
export ENABLE_BROWSER="${ENABLE_BROWSER:-0}"
export ENABLE_UPLOAD_CLEANUP="${ENABLE_UPLOAD_CLEANUP:-0}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
export VKPI_WORKER_BOOT_NONCE="${VKPI_WORKER_BOOT_NONCE:-$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(32))')}"
export VKPI_WORKER_STARTED_AT="${VKPI_WORKER_STARTED_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

if [[ -f "$PIDFILE" ]]; then
  GUARD_CODE=0
  GUARD_OUTPUT="$("$PYTHON_BIN" "$PID_GUARD" --pidfile "$PIDFILE" --root "$ROOT" --logfile "$LOGFILE")" || GUARD_CODE=$?
  case "$GUARD_CODE" in
    0)
      PID="$(cat "$PIDFILE")"
      echo "apify worker already running with verified identity pid $PID"
      exit 0
      ;;
    11|12|13)
      echo "removing safely classified stale worker marker: $GUARD_OUTPUT"
      rm -f "$PIDFILE"
      ;;
    *)
      echo "refusing to replace unverified worker marker: $GUARD_OUTPUT" >&2
      exit 1
      ;;
  esac
fi

# nohup only protects against SIGHUP. Codex/PTY runners can also clean up their
# own process group after the command returns, so put the worker in a new session
# before execing the real process.
nohup "$PYTHON_BIN" -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
  "$PYTHON_BIN" -m app.workers.apify_jobs_worker >>"$LOGFILE" 2>&1 </dev/null &
PID="$!"
echo "$PID" >"$PIDFILE"
sleep 0.5
GUARD_CODE=0
GUARD_OUTPUT="$("$PYTHON_BIN" "$PID_GUARD" --pidfile "$PIDFILE" --root "$ROOT" --logfile "$LOGFILE")" || GUARD_CODE=$?
if [[ "$GUARD_CODE" -ne 0 ]]; then
  if [[ "$GUARD_CODE" =~ ^(11|12|13)$ ]]; then
    rm -f "$PIDFILE"
  fi
  echo "apify worker identity was not verified after start: $GUARD_OUTPUT" >&2
  tail -n 40 "$LOGFILE" >&2 || true
  exit 1
fi

echo "apify worker started pid $PID"
echo "pidfile: $PIDFILE"
echo "logfile: $LOGFILE"
