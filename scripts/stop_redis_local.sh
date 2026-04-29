#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

REDIS_CLI_BIN="$REDIS_BIN_DIR/redis-cli"

if [ -x "$REDIS_CLI_BIN" ] && "$REDIS_CLI_BIN" -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
  "$REDIS_CLI_BIN" -h "$REDIS_HOST" -p "$REDIS_PORT" shutdown nosave || true
  echo "Redis stopped"
elif [ -f "$REDIS_PID_FILE" ] && kill -0 "$(cat "$REDIS_PID_FILE")" 2>/dev/null; then
  kill "$(cat "$REDIS_PID_FILE")"
  echo "Redis stopped"
else
  echo "Redis not running"
fi

