#!/usr/bin/env bash
# R58E: 加 LOCAL_DATABASE_URL 守门 + 启动可见性

if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
  SCRIPT_SOURCE="${BASH_SOURCE[0]}"
elif [[ -n "${ZSH_VERSION:-}" ]]; then
  SCRIPT_SOURCE="${(%):-%N}"
else
  SCRIPT_SOURCE="$0"
fi
ROOT="$(cd "$(dirname "$SCRIPT_SOURCE")/.." && pwd)"
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
    elif eval "[[ -z \"\${$key+x}\" ]]"; then
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

# ──────────────────────────────────────────────────────────
# R58E NEW: LOCAL stack 守门
# 当 ENVIRONMENT=local 时,强制 DATABASE_URL = LOCAL_DATABASE_URL
# 防止 .env 里残留旧值导致 server 和 smoke 写读不同库
# ──────────────────────────────────────────────────────────
if [[ "${ENVIRONMENT:-local}" == "local" ]]; then
  # 本地强制覆盖 (除非显式 set RUNTIME_ENV_KEEP_DB_URL=1)
  if [[ "${RUNTIME_ENV_KEEP_DB_URL:-0}" != "1" ]]; then
    export DATABASE_URL="$LOCAL_DATABASE_URL"
    export REDIS_URL="$LOCAL_REDIS_URL"
  else
    export DATABASE_URL="${DATABASE_URL:-$LOCAL_DATABASE_URL}"
    export REDIS_URL="${REDIS_URL:-$LOCAL_REDIS_URL}"
  fi
else
  # 非 local 用环境变量为准 (生产环境)
  export DATABASE_URL="${DATABASE_URL:-$LOCAL_DATABASE_URL}"
  export REDIS_URL="${REDIS_URL:-$LOCAL_REDIS_URL}"
fi

export JWT_SECRET="${JWT_SECRET:-$INSECURE_LOCAL_JWT_SECRET}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-AdminPass123!}"
export WORKER_CLUSTER_TIER="${WORKER_CLUSTER_TIER:-60}"

redact_runtime_url() {
  local value="${1:-}"
  printf '%s' "$value" | sed -E 's#^([A-Za-z][A-Za-z0-9+.-]*://)([^/@]+@)#\1***@#'
}

# ── LLM/AI 出网代理 ───────────────────────────────────────────────────────
# api.openai.com / generativelanguage(Gemini)/ api.anthropic.com 在本网络下不可直连
# (SSL 握手超时 / Remote end closed connection,区域封锁),走与 yt-dlp 同一残留代理可达
# (实测 Gemini generate 4.1s、OpenAI HTTP200)。LLM SDK(genai/openai/anthropic,httpx 基)
# 认 HTTPS_PROXY/HTTP_PROXY env。NO_PROXY 排除本地 Postgres/Qdrant/admin,避免本地回环被代理。
if [[ -n "${YTDLP_PROXY:-}" && -z "${VKPI_DISABLE_LLM_PROXY:-}" ]]; then
  export HTTPS_PROXY="${HTTPS_PROXY:-$YTDLP_PROXY}"
  export HTTP_PROXY="${HTTP_PROXY:-$YTDLP_PROXY}"
  export https_proxy="${https_proxy:-$YTDLP_PROXY}"
  export http_proxy="${http_proxy:-$YTDLP_PROXY}"
  export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,0.0.0.0,::1}"
  export no_proxy="${no_proxy:-localhost,127.0.0.1,0.0.0.0,::1}"
  # 2026-07-11:api.apify.com 直连(实测 1.18s vs 走代理 3.05s,且代理下 Apify 频出
  # Cloudflare 522 → 评论采集逐帖全败)。LLM(OpenAI/Gemini/Anthropic)照旧走代理。
  if [[ ",$NO_PROXY," != *",api.apify.com,"* ]]; then
    export NO_PROXY="$NO_PROXY,api.apify.com"
  fi
  if [[ ",$no_proxy," != *",api.apify.com,"* ]]; then
    export no_proxy="$no_proxy,api.apify.com"
  fi
fi

# ──────────────────────────────────────────────────────────
# R58E NEW: 启动可见性 - 打印当前生效的关键环境
# 让任何调用 runtime_env.sh 的脚本都能看到真实值
# ──────────────────────────────────────────────────────────
if [[ "${RUNTIME_ENV_QUIET:-0}" != "1" ]]; then
  cat >&2 <<EOF
─── runtime_env.sh loaded ───
  ENVIRONMENT       = $ENVIRONMENT
  DATABASE_URL      = $(redact_runtime_url "$DATABASE_URL")
  REDIS_URL         = $(redact_runtime_url "$REDIS_URL")
  DB_RUNTIME_BACKEND = $DB_RUNTIME_BACKEND
  APP_ROLE          = ${APP_ROLE:-(unset)}
─────────────────────────────
EOF
fi
