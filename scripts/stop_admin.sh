#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="${PIDFILE:-$ROOT/runtime/admin-web.pid}"

if [[ ! -f "$PIDFILE" ]]; then
  echo "admin-web pidfile not found: $PIDFILE"
  exit 0
fi

PID="$(cat "$PIDFILE" 2>/dev/null || true)"
if [[ -z "$PID" ]]; then
  echo "admin-web pidfile is empty: $PIDFILE"
  rm -f "$PIDFILE"
  exit 0
fi

if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in {1..20}; do
    if ! kill -0 "$PID" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID"
  fi
fi

rm -f "$PIDFILE"
echo "admin-web stopped"
