#!/usr/bin/env bash
# launchd 入口:source 运行环境(含代理/DB/Redis)后,用 venv python 常驻起 scheduler。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/runtime_env.sh"
# LLM/Apify 必须走代理(本地 GFW);runtime_env 已派生 HTTPS_PROXY/HTTP_PROXY
export HTTPS_PROXY="${HTTPS_PROXY:-${YTDLP_PROXY:-}}"
export HTTP_PROXY="${HTTP_PROXY:-${YTDLP_PROXY:-}}"
export ENABLE_SCHEDULER=1
export APP_ROLE="${APP_ROLE:-worker}"
export PYTHONPATH="$ROOT/backend"
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/scheduler_daemon.py"
