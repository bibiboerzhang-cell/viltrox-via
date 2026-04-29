#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

PG_CTL_BIN="$POSTGRES_BIN/pg_ctl"

if [ ! -x "$PG_CTL_BIN" ]; then
  echo "pg_ctl not found under $POSTGRES_BIN" >&2
  exit 1
fi

if "$PG_CTL_BIN" -D "$POSTGRES_DATA_DIR" status >/dev/null 2>&1; then
  "$PG_CTL_BIN" -D "$POSTGRES_DATA_DIR" stop -m fast
  echo "Postgres stopped"
else
  echo "Postgres not running"
fi

