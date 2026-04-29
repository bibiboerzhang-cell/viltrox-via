#!/usr/bin/env bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSECURE_LOCAL_JWT_SECRET="viltrox2-local-dev-secret-change-me"
LOCAL_ENV_FILE="${LOCAL_ENV_FILE:-$ROOT/.env}"
ENVIRONMENT="${ENVIRONMENT:-local}"
ENV_FILE="${ENV_FILE:-}"
LEGACY_TOOLS_BIN="${LEGACY_TOOLS_BIN:-$(cd "$ROOT/.." && pwd)/viltrox-test/_tools/bin}"
RUNTIME_ROOT="${RUNTIME_ROOT:-$ROOT/runtime}"
RUNTIME_VENDOR="${RUNTIME_VENDOR:-$RUNTIME_ROOT/vendor}"
RUNTIME_DATA="${RUNTIME_DATA:-$RUNTIME_ROOT/data}"
RUNTIME_LOGS="${RUNTIME_LOGS:-$RUNTIME_ROOT/logs}"

POSTGRES_APP_HOME="${POSTGRES_APP_HOME:-$RUNTIME_VENDOR/Postgres.app}"
POSTGRES_BIN="${POSTGRES_BIN:-$POSTGRES_APP_HOME/Contents/Versions/16/bin}"
REDIS_BIN_DIR="${REDIS_BIN_DIR:-$RUNTIME_VENDOR/redis/bin}"

POSTGRES_PORT="${POSTGRES_PORT:-54329}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-viltrox2}"
POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_DATA_DIR="${POSTGRES_DATA_DIR:-$RUNTIME_DATA/postgres}"
POSTGRES_SOCKET_DIR="${POSTGRES_SOCKET_DIR:-/tmp/viltrox2-pg-$POSTGRES_PORT}"
POSTGRES_LOG_FILE="${POSTGRES_LOG_FILE:-$RUNTIME_LOGS/postgres.log}"
POSTGRES_PID_FILE="${POSTGRES_PID_FILE:-$POSTGRES_DATA_DIR/postmaster.pid}"

REDIS_PORT="${REDIS_PORT:-6380}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_DATA_DIR="${REDIS_DATA_DIR:-$RUNTIME_DATA/redis}"
REDIS_LOG_FILE="${REDIS_LOG_FILE:-$RUNTIME_LOGS/redis.log}"
REDIS_PID_FILE="${REDIS_PID_FILE:-$REDIS_DATA_DIR/redis.pid}"
REDIS_CONF_FILE="${REDIS_CONF_FILE:-$REDIS_DATA_DIR/redis.conf}"
LOCAL_NGINX_PREFIX="${LOCAL_NGINX_PREFIX:-$RUNTIME_VENDOR/nginx}"
LOCAL_NGINX_BIN="${LOCAL_NGINX_BIN:-$LOCAL_NGINX_PREFIX/sbin/nginx}"

LOCAL_DATABASE_URL="${LOCAL_DATABASE_URL:-postgresql://$POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB}"
LOCAL_REDIS_URL="${LOCAL_REDIS_URL:-redis://$REDIS_HOST:$REDIS_PORT/0}"

mkdir -p "$RUNTIME_ROOT" "$RUNTIME_DATA" "$RUNTIME_LOGS" "$REDIS_DATA_DIR"

load_env_file() {
  local file_path="$1"
  local override_mode="${2:-0}"
  [[ -f "$file_path" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    if [[ "$override_mode" == "1" ]]; then
      export "$key=$value"
    elif [[ "$key" == "JWT_SECRET" && "${ENVIRONMENT:-local}" == "local" && "${RUNTIME_ENV_KEEP_INHERITED_JWT:-0}" != "1" ]]; then
      export "$key=$value"
    elif [[ "$key" == "JWT_SECRET" && "${JWT_SECRET:-}" == "$INSECURE_LOCAL_JWT_SECRET" && "$value" != "$INSECURE_LOCAL_JWT_SECRET" ]]; then
      export "$key=$value"
    elif [[ -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done < "$file_path"
}

load_env_file "$LOCAL_ENV_FILE" 0
if [[ -f "$ROOT/.env.$ENVIRONMENT" ]]; then
  load_env_file "$ROOT/.env.$ENVIRONMENT" 1
fi
if [[ -n "$ENV_FILE" ]]; then
  load_env_file "$ENV_FILE" 1
fi

if [[ ! -x "$POSTGRES_BIN/initdb" && -x "/opt/homebrew/opt/postgresql@16/bin/initdb" ]]; then
  POSTGRES_BIN="/opt/homebrew/opt/postgresql@16/bin"
fi

if [[ ! -x "$REDIS_BIN_DIR/redis-server" && -x "/opt/homebrew/opt/redis/bin/redis-server" ]]; then
  REDIS_BIN_DIR="/opt/homebrew/opt/redis/bin"
fi

if [[ -d "$LEGACY_TOOLS_BIN" ]]; then
  export PATH="$LEGACY_TOOLS_BIN:$PATH"
fi

export DB_RUNTIME_BACKEND="${DB_RUNTIME_BACKEND:-postgres}"
export DATABASE_URL="${DATABASE_URL:-$LOCAL_DATABASE_URL}"
export REDIS_URL="${REDIS_URL:-$LOCAL_REDIS_URL}"
export JWT_SECRET="${JWT_SECRET:-$INSECURE_LOCAL_JWT_SECRET}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-AdminPass123!}"
export WORKER_CLUSTER_TIER="${WORKER_CLUSTER_TIER:-60}"
