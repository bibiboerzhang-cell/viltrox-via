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

REDIS_START_TIMEOUT_SECONDS="${VKPI_REDIS_START_TIMEOUT_SECONDS:-15}"
if [[ ! "$REDIS_START_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] \
  || [ "$REDIS_START_TIMEOUT_SECONDS" -gt 120 ]; then
  echo "VKPI_REDIS_START_TIMEOUT_SECONDS must be an integer in [1,120]" >&2
  exit 2
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
  "$REDIS_SERVER_BIN" "$REDIS_CONF_FILE" &
  redis_bootstrap_pid=$!
  redis_ready=0
  redis_attempts=$((REDIS_START_TIMEOUT_SECONDS * 10))
  for ((attempt = 0; attempt < redis_attempts; attempt++)); do
    if [ -x "$REDIS_CLI_BIN" ] \
      && [ "$("$REDIS_CLI_BIN" -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null || true)" = "PONG" ]; then
      redis_ready=1
      break
    fi
    sleep 0.1
  done
  if [ "$redis_ready" != "1" ]; then
    if kill -0 "$redis_bootstrap_pid" 2>/dev/null; then
      kill "$redis_bootstrap_pid" 2>/dev/null || true
    fi
    wait "$redis_bootstrap_pid" 2>/dev/null || true
    echo "Redis failed to become ready within ${REDIS_START_TIMEOUT_SECONDS}s" >&2
    exit 1
  fi
  wait "$redis_bootstrap_pid" 2>/dev/null || true
fi

if [ -x "$REDIS_CLI_BIN" ]; then
  "$REDIS_CLI_BIN" -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null
fi

echo "Redis ready: $LOCAL_REDIS_URL"
