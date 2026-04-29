#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

REDIS_SERVER_BIN="$REDIS_BIN_DIR/redis-server"
REDIS_CLI_BIN="$REDIS_BIN_DIR/redis-cli"

if [ ! -x "$REDIS_SERVER_BIN" ]; then
  echo "redis-server not found under $REDIS_BIN_DIR" >&2
  exit 1
fi

mkdir -p "$REDIS_DATA_DIR" "$(dirname "$REDIS_LOG_FILE")"

cat >"$REDIS_CONF_FILE" <<EOF
bind $REDIS_HOST
port $REDIS_PORT
dir $REDIS_DATA_DIR
dbfilename dump.rdb
appendonly yes
appendfilename appendonly.aof
pidfile $REDIS_PID_FILE
logfile $REDIS_LOG_FILE
daemonize yes
save 60 1000
EOF

if [ -f "$REDIS_PID_FILE" ] && kill -0 "$(cat "$REDIS_PID_FILE")" 2>/dev/null; then
  echo "Redis already running on port $REDIS_PORT"
else
  rm -f "$REDIS_PID_FILE"
  "$REDIS_SERVER_BIN" "$REDIS_CONF_FILE"
fi

if [ -x "$REDIS_CLI_BIN" ]; then
  "$REDIS_CLI_BIN" -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null
fi

echo "Redis ready: $LOCAL_REDIS_URL"

