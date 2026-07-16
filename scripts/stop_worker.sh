#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="${PIDFILE:-$ROOT/runtime/worker.pid}"
LOGFILE="${LOGFILE:-$ROOT/runtime/logs/$(basename "$PIDFILE" .pid).log}"
PID_GUARD="$ROOT/scripts/worker_pid_guard.py"

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

if [[ ! -f "$PIDFILE" ]]; then
  echo "apify worker pidfile not found: $PIDFILE"
  exit 0
fi

GUARD_CODE=0
GUARD_OUTPUT="$("$PYTHON_BIN" "$PID_GUARD" --pidfile "$PIDFILE" --root "$ROOT" --logfile "$LOGFILE")" || GUARD_CODE=$?
case "$GUARD_CODE" in
  0)
    PID="$(cat "$PIDFILE")"
    ;;
  11|12|13)
    rm -f "$PIDFILE"
    echo "removed stale worker marker without signalling a process: $GUARD_OUTPUT"
    exit 0
    ;;
  *)
    echo "refusing to signal or remove an unverified worker marker: $GUARD_OUTPUT" >&2
    exit 1
    ;;
esac

kill "$PID"
for _ in {1..20}; do
  GUARD_CODE=0
  GUARD_OUTPUT="$("$PYTHON_BIN" "$PID_GUARD" --pidfile "$PIDFILE" --root "$ROOT" --logfile "$LOGFILE")" || GUARD_CODE=$?
  if [[ "$GUARD_CODE" -ne 0 ]]; then
    break
  fi
  sleep 0.5
done

if [[ "$GUARD_CODE" -eq 0 ]]; then
  # Re-check all three identity signals immediately before escalation.  This
  # avoids sending SIGKILL to a PID that exited and was reused during the wait.
  kill -9 "$PID"
elif [[ ! "$GUARD_CODE" =~ ^(10|11|12|13)$ ]]; then
  echo "worker identity became indeterminate after SIGTERM; refusing SIGKILL: $GUARD_OUTPUT" >&2
  exit 1
fi

rm -f "$PIDFILE"
echo "apify worker stopped"
