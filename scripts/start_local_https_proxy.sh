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
  echo "Build or install nginx first, then rerun this script." >&2
  exit 1
fi

RUNTIME_NGINX_DIR="${RUNTIME_NGINX_DIR:-$RUNTIME_ROOT/nginx}"
NGINX_CONF="${NGINX_CONF:-$ROOT/deploy/nginx/viltrox-2.0.local.conf}"
PID_FILE="$RUNTIME_NGINX_DIR/nginx.pid"

mkdir -p \
  "$RUNTIME_NGINX_DIR/logs" \
  "$RUNTIME_NGINX_DIR/certs" \
  "$RUNTIME_NGINX_DIR/client_body_temp" \
  "$RUNTIME_NGINX_DIR/proxy_temp" \
  "$RUNTIME_NGINX_DIR/fastcgi_temp" \
  "$RUNTIME_NGINX_DIR/uwsgi_temp" \
  "$RUNTIME_NGINX_DIR/scgi_temp"

if [[ ! -f "$RUNTIME_NGINX_DIR/certs/viltrox-local.crt" || ! -f "$RUNTIME_NGINX_DIR/certs/viltrox-local.key" ]]; then
  "$ROOT/scripts/generate_local_ssl.sh"
fi

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" >/dev/null 2>&1; then
  "$NGINX_BIN" -p "$RUNTIME_NGINX_DIR/" -c "$NGINX_CONF" -s reload
else
  "$NGINX_BIN" -p "$RUNTIME_NGINX_DIR/" -c "$NGINX_CONF"
fi

echo "Local HTTPS proxy started:"
echo "  public: https://localhost:8443"
echo "  admin : https://localhost:9443"
