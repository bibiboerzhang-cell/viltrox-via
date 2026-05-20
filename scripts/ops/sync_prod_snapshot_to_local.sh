#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
SYNC_SERVICE="${SYNC_SERVICE:-vkpi-sync-daily.service}"
ALLOW_DURING_SYNC="${ALLOW_DURING_SYNC:-0}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOCAL_DIR="${LOCAL_DIR:-runtime/prod-sync/${STAMP}}"

sync_state="$(ssh "${SSH_TARGET}" "systemctl is-active '${SYNC_SERVICE}' 2>/dev/null || true")"
if [ "${ALLOW_DURING_SYNC}" != "1" ] && { [ "${sync_state}" = "active" ] || [ "${sync_state}" = "activating" ]; }; then
  echo "{\"skipped\":true,\"reason\":\"${SYNC_SERVICE} is ${sync_state}; set ALLOW_DURING_SYNC=1 to override\"}"
  exit 0
fi

STAMP="${STAMP}" LOCAL_DIR="${LOCAL_DIR}" "${SCRIPT_DIR}/backup_prod_vkpi.sh"

{
  echo "snapshot=${STAMP}"
  echo "downloaded_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "source=${SSH_TARGET}:${REMOTE_ROOT}"
  echo "restore_default=disabled"
  echo "restore_command=RESTORE_LOCAL=1 ALLOW_LOCAL_DB_RESTORE=1 LOCAL_DATABASE_URL=... scripts/ops/sync_prod_snapshot_to_local.sh"
} > "${LOCAL_DIR}/README.txt"

if [ "${RESTORE_LOCAL:-0}" = "1" ]; then
  if [ "${ALLOW_LOCAL_DB_RESTORE:-0}" != "1" ]; then
    echo "Refusing local restore without ALLOW_LOCAL_DB_RESTORE=1" >&2
    exit 1
  fi
  if [ -z "${LOCAL_DATABASE_URL:-}" ]; then
    echo "LOCAL_DATABASE_URL is required for RESTORE_LOCAL=1" >&2
    exit 1
  fi
  if ! command -v pg_restore >/dev/null 2>&1; then
    echo "pg_restore not found locally" >&2
    exit 1
  fi
  pg_restore --clean --if-exists --no-owner --no-acl --dbname "${LOCAL_DATABASE_URL}" "${LOCAL_DIR}/prod-db.dump"
  echo "local restore completed into LOCAL_DATABASE_URL"
else
  echo "snapshot downloaded only; local DB restore skipped"
fi
