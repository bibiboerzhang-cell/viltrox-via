#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

"$ROOT/scripts/start_postgres_local.sh"
"$ROOT/scripts/start_redis_local.sh"

echo "Local stack ready"
echo "  DATABASE_URL=$LOCAL_DATABASE_URL"
echo "  REDIS_URL=$LOCAL_REDIS_URL"

