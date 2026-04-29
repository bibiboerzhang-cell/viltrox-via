#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

PG_ISREADY_BIN="$POSTGRES_BIN/pg_isready"
PSQL_BIN="$POSTGRES_BIN/psql"
REDIS_CLI_BIN="$REDIS_BIN_DIR/redis-cli"

echo "[stack] Postgres"
"$PG_ISREADY_BIN" -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -d "$POSTGRES_DB"
"$PSQL_BIN" "$LOCAL_DATABASE_URL" -tAc "SELECT current_database(), current_user;"

echo "[stack] Redis"
"$REDIS_CLI_BIN" -h "$REDIS_HOST" -p "$REDIS_PORT" ping

