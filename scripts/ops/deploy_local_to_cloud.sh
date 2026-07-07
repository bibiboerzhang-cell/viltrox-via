#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

# ---- F2 发布门:verify 全绿才允许出海(强制,非零即中止,无跳过开关)----
# 覆盖:后端 pytest + 前端 tsc/build + dist 分包护栏 + 仓库硬化 + 红线 grep
#       + 千行卫兵 + 运行态 sha 对齐。任何一步失败都不允许 rsync 上云。
echo "[deploy] gate: bash scripts/verify.sh(全绿才继续)..."
if ! bash "${PROJECT_ROOT}/scripts/verify.sh"; then
  echo "[deploy] verify.sh 非零退出 —— 部署中止。先把 verify 修绿再出海。" >&2
  exit 1
fi

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
SERVICE_NAME="${SERVICE_NAME:-viltrox-2.0-test.service}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8001/health}"
SYNC_SERVICE="${SYNC_SERVICE:-vkpi-sync-daily.service}"
ALLOW_DURING_SYNC="${ALLOW_DURING_SYNC:-0}"
REMOTE_APP_USER="${REMOTE_APP_USER:-viltrox}"
REMOTE_APP_GROUP="${REMOTE_APP_GROUP:-viltrox}"
START_WORKER="${START_WORKER:-1}"

sync_state="$(ssh "${SSH_TARGET}" "systemctl is-active '${SYNC_SERVICE}' 2>/dev/null || true")"
if [ "${ALLOW_DURING_SYNC}" != "1" ] && { [ "${sync_state}" = "active" ] || [ "${sync_state}" = "activating" ]; }; then
  echo "Refusing deploy while ${SYNC_SERVICE} is ${sync_state}. Set ALLOW_DURING_SYNC=1 only for an intentional ops override." >&2
  exit 1
fi

if [ -n "$(git status --short)" ] && [ "${ALLOW_DIRTY_DEPLOY:-0}" != "1" ]; then
  echo "Refusing deploy from dirty worktree. Set ALLOW_DIRTY_DEPLOY=1 only for an intentional package deploy." >&2
  git status --short >&2
  exit 1
fi

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  npm --prefix frontend run build
fi

if [ "${SKIP_BACKUP:-0}" != "1" ]; then
  "${SCRIPT_DIR}/backup_prod_vkpi.sh"
fi

LOCAL_GIT_SHA="$(git rev-parse HEAD)"
LOCAL_GIT_BRANCH="$(git branch --show-current)"
LOCAL_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

RSYNC_DELETE_FLAG=""
if [ "${RSYNC_DELETE:-0}" = "1" ]; then
  RSYNC_DELETE_FLAG="--delete"
fi

rsync -az ${RSYNC_DELETE_FLAG} \
  --no-owner \
  --no-group \
  --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'frontend/node_modules/' \
  --exclude 'node_modules/' \
  --exclude 'uploads/' \
  --exclude 'frames/' \
  --exclude 'creator_profiles/' \
  --exclude 'runtime/' \
  --exclude 'backups/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'submissions.db' \
  --exclude 'video-production-platform/' \
  --exclude 'reports/vkpi_*.html' \
  --exclude 'reports/vkpi_*.md' \
  ./ "${SSH_TARGET}:${REMOTE_ROOT}/"

ssh "${SSH_TARGET}" "cd '${REMOTE_ROOT}' && printf '%s\n' '${LOCAL_GIT_SHA}' > BUILD_GIT_SHA && printf '%s\n' '${LOCAL_GIT_BRANCH}' > BUILD_GIT_BRANCH && printf '%s\n' '${LOCAL_BUILD_TIME}' > BUILD_TIME && APP_GIT_SHA='${LOCAL_GIT_SHA}' APP_GIT_SHORT_SHA='${LOCAL_GIT_SHA:0:8}' APP_GIT_BRANCH='${LOCAL_GIT_BRANCH}' APP_BUILD_TIME='${LOCAL_BUILD_TIME}' python3 - <<'PY'
import os
from pathlib import Path

path = Path('.env')
updates = {
    'APP_GIT_SHA': os.environ['APP_GIT_SHA'],
    'APP_GIT_SHORT_SHA': os.environ['APP_GIT_SHORT_SHA'],
    'APP_GIT_BRANCH': os.environ['APP_GIT_BRANCH'],
    'APP_BUILD_TIME': os.environ['APP_BUILD_TIME'],
}
if path.exists():
    lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
else:
    lines = []
seen = set()
out = []
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        key = line.split('=', 1)[0].strip()
        if key in updates:
            out.append(f'{key}={updates[key]}')
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}={value}')
path.write_text('\\n'.join(out) + '\\n', encoding='utf-8')
PY
sudo chown -R '${REMOTE_APP_USER}:${REMOTE_APP_GROUP}' '${REMOTE_ROOT}'"

# yt-dlp 固化(2026-07-02):历史上部署后二进制三次丢失(video download 失败桶 15%)。
# 幂等:已装则秒过;装失败只警告不中止(worker 侧另有启动自检兜底)。
ssh "${SSH_TARGET}" "'${REMOTE_ROOT}/.venv/bin/python' -m pip install --quiet --upgrade yt-dlp || echo 'WARN: yt-dlp install failed - video downloads may break'"

ssh "${SSH_TARGET}" "sudo systemctl restart '${SERVICE_NAME}' && systemctl is-active '${SERVICE_NAME}' && for attempt in \$(seq 1 30); do if curl -fsS '${HEALTH_URL}' >/tmp/vkpi-health.json; then cat /tmp/vkpi-health.json; exit 0; fi; sleep 1; done; echo 'health check failed: ${HEALTH_URL}' >&2; exit 1"

if [ "${START_WORKER}" = "1" ]; then
  ssh "${SSH_TARGET}" "cd '${REMOTE_ROOT}' && bash scripts/stop_worker.sh >/dev/null 2>&1 || true && ENVIRONMENT=production RUNTIME_ENV_KEEP_DB_URL=1 RUNTIME_ENV_QUIET=1 bash scripts/start_worker.sh && for attempt in \$(seq 1 20); do if curl -fsS '${HEALTH_URL}' >/tmp/vkpi-health.json && python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path('/tmp/vkpi-health.json').read_text())
if data.get('trust', {}).get('worker_online') is True:
    raise SystemExit(0)
raise SystemExit(1)
PY
then cat /tmp/vkpi-health.json; exit 0; fi; sleep 1; done; echo 'worker health check failed: ${HEALTH_URL}' >&2; exit 1"
fi

LOCAL_ASSET="$(grep -o 'app-[A-Za-z0-9_-]*\.js' frontend/dist/index.html | head -1)"
REMOTE_ASSET="$(ssh "${SSH_TARGET}" "cd '${REMOTE_ROOT}' && grep -o 'app-[A-Za-z0-9_-]*\\.js' frontend/dist/index.html | head -1")"

echo "local_asset=${LOCAL_ASSET}"
echo "remote_asset=${REMOTE_ASSET}"
test "${LOCAL_ASSET}" = "${REMOTE_ASSET}"
