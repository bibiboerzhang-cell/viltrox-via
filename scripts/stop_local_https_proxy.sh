#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

NGINX_BIN="${NGINX_BIN:-$LOCAL_NGINX_BIN}"
if [[ ! -x "$NGINX_BIN" ]]; then
  NGINX_BIN="$(command -v nginx || true)"
fi
if [[ -z "$NGINX_BIN" || ! -x "$NGINX_BIN" ]]; then
  echo "nginx is not installed on this machine and no local vendor binary was found." >&2
  exit 1
fi

RUNTIME_NGINX_DIR="${RUNTIME_NGINX_DIR:-$RUNTIME_ROOT/nginx}"
NGINX_CONF="${NGINX_CONF:-$ROOT/deploy/nginx/viltrox-2.0.local.conf}"
PID_FILE="$RUNTIME_NGINX_DIR/nginx.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" >/dev/null 2>&1; then
  "$NGINX_BIN" -p "$RUNTIME_NGINX_DIR/" -c "$NGINX_CONF" -s stop
  echo "Local HTTPS proxy stopped."
else
  echo "Local HTTPS proxy is not running."
fi
