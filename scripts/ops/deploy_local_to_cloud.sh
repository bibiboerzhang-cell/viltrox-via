#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
SERVICE_NAME="${SERVICE_NAME:-viltrox-2.0-test.service}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8001/health}"

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

RSYNC_DELETE_FLAG=""
if [ "${RSYNC_DELETE:-0}" = "1" ]; then
  RSYNC_DELETE_FLAG="--delete"
fi

rsync -az ${RSYNC_DELETE_FLAG} \
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
  ./ "${SSH_TARGET}:${REMOTE_ROOT}/"

ssh "${SSH_TARGET}" "sudo systemctl restart '${SERVICE_NAME}' && systemctl is-active '${SERVICE_NAME}' && for attempt in \$(seq 1 30); do if curl -fsS '${HEALTH_URL}' >/tmp/vkpi-health.json; then cat /tmp/vkpi-health.json; exit 0; fi; sleep 1; done; echo 'health check failed: ${HEALTH_URL}' >&2; exit 1"

LOCAL_ASSET="$(grep -o 'app-[A-Za-z0-9_-]*\.js' frontend/dist/index.html | head -1)"
REMOTE_ASSET="$(ssh "${SSH_TARGET}" "cd '${REMOTE_ROOT}' && grep -o 'app-[A-Za-z0-9_-]*\\.js' frontend/dist/index.html | head -1")"

echo "local_asset=${LOCAL_ASSET}"
echo "remote_asset=${REMOTE_ASSET}"
test "${LOCAL_ASSET}" = "${REMOTE_ASSET}"
