#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

INITDB_BIN="$POSTGRES_BIN/initdb"
PG_CTL_BIN="$POSTGRES_BIN/pg_ctl"
CREATEDB_BIN="$POSTGRES_BIN/createdb"
PSQL_BIN="$POSTGRES_BIN/psql"
ADMIN_DATABASE_URL="postgresql://$POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/postgres"

if [ ! -x "$INITDB_BIN" ] || [ ! -x "$PG_CTL_BIN" ]; then
  echo "Postgres binaries not found under $POSTGRES_BIN" >&2
  exit 1
fi

mkdir -p "$POSTGRES_DATA_DIR" "$(dirname "$POSTGRES_LOG_FILE")" "$POSTGRES_SOCKET_DIR"

if [ ! -f "$POSTGRES_DATA_DIR/PG_VERSION" ] && [ -d "$POSTGRES_SOCKET_DIR" ]; then
  rmdir "$POSTGRES_SOCKET_DIR" 2>/dev/null || true
fi

if [ ! -f "$POSTGRES_DATA_DIR/PG_VERSION" ]; then
  "$INITDB_BIN" -D "$POSTGRES_DATA_DIR" --username="$POSTGRES_USER" --auth=trust >/tmp/viltrox2-initdb.log
  mkdir -p "$POSTGRES_SOCKET_DIR"
  cat >>"$POSTGRES_DATA_DIR/postgresql.conf" <<EOF
port = $POSTGRES_PORT
listen_addresses = '$POSTGRES_HOST'
unix_socket_directories = '$POSTGRES_SOCKET_DIR'
shared_buffers = '256MB'
max_connections = 200
EOF
  cat >"$POSTGRES_DATA_DIR/pg_hba.conf" <<EOF
local   all             all                                     trust
host    all             all             127.0.0.1/32            trust
host    all             all             ::1/128                 trust
EOF
fi

if [ -f "$POSTGRES_PID_FILE" ]; then
  if "$PG_CTL_BIN" -D "$POSTGRES_DATA_DIR" status >/dev/null 2>&1; then
    echo "Postgres already running on port $POSTGRES_PORT"
  else
    rm -f "$POSTGRES_PID_FILE"
  fi
fi

if ! "$PG_CTL_BIN" -D "$POSTGRES_DATA_DIR" status >/dev/null 2>&1; then
  "$PG_CTL_BIN" -D "$POSTGRES_DATA_DIR" -l "$POSTGRES_LOG_FILE" start
fi

if ! "$PSQL_BIN" "$ADMIN_DATABASE_URL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$POSTGRES_DB'" | grep -q 1; then
  "$CREATEDB_BIN" -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" "$POSTGRES_DB"
fi

echo "Postgres ready: $LOCAL_DATABASE_URL"
