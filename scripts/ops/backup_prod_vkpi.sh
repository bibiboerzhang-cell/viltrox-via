#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOCAL_PARENT="${LOCAL_PARENT:-runtime/prod-sync}"
LOCAL_DIR="${LOCAL_DIR:-${LOCAL_PARENT}/${STAMP}}"
REMOTE_BACKUP_DIR="backups/ops/${STAMP}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"

mkdir -p "${LOCAL_DIR}"

ssh "${SSH_TARGET}" "REMOTE_ROOT='${REMOTE_ROOT}' REMOTE_BACKUP_DIR='${REMOTE_BACKUP_DIR}' PYTHON_BIN='${PYTHON_BIN}' BACKUP_MEDIA_ARCHIVE='${BACKUP_MEDIA_ARCHIVE:-0}' bash -s" <<'REMOTE'
set -euo pipefail

cd "${REMOTE_ROOT}"
umask 077
mkdir -p "${REMOTE_BACKUP_DIR}"

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "python runtime not found: ${PYTHON_BIN}" >&2
  exit 1
fi

DATABASE_URL="$("${PYTHON_BIN}" - <<'PY'
from pathlib import Path

value = ""
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, val = raw.split("=", 1)
        if key.strip() == "DATABASE_URL":
            value = val.strip().strip("'\"")
            break
print(value)
PY
)"

if [ -z "${DATABASE_URL}" ]; then
  echo "DATABASE_URL missing in ${REMOTE_ROOT}/.env" >&2
  exit 1
fi

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "pg_dump not found on remote host" >&2
  exit 1
fi

pg_dump --format=custom --no-owner --no-acl "${DATABASE_URL}" > "${REMOTE_BACKUP_DIR}/prod-db.dump"
sha256sum "${REMOTE_BACKUP_DIR}/prod-db.dump" > "${REMOTE_BACKUP_DIR}/prod-db.dump.sha256"

{
  echo "stamp=${REMOTE_BACKUP_DIR##*/}"
  echo "remote_root=${REMOTE_ROOT}"
  echo "service=viltrox-2.0-test.service"
  systemctl is-active viltrox-2.0-test.service 2>/dev/null | sed 's/^/service_status=/'
  git log --oneline -1 2>/dev/null | sed 's/^/git_head=/' || true
  find frontend/dist/assets -maxdepth 1 -type f -name 'app-*.js' -printf 'frontend_asset=%f\n' 2>/dev/null | sort | tail -1 || true
} > "${REMOTE_BACKUP_DIR}/runtime-state.txt"

if [ -d uploads/vkpi_media_cache ]; then
  du -sh uploads/vkpi_media_cache > "${REMOTE_BACKUP_DIR}/media-cache-size.txt" 2>/dev/null || true
  find uploads/vkpi_media_cache -type f | wc -l > "${REMOTE_BACKUP_DIR}/media-cache-file-count.txt"
  find uploads/vkpi_media_cache -type f -printf '%P\t%s\t%TY-%Tm-%TdT%TH:%TM:%TS%TZ\n' \
    | sort > "${REMOTE_BACKUP_DIR}/media-cache-manifest.tsv"
  if [ "${BACKUP_MEDIA_ARCHIVE:-0}" = "1" ]; then
    tar -czf "${REMOTE_BACKUP_DIR}/vkpi-media-cache.tgz" uploads/vkpi_media_cache
    sha256sum "${REMOTE_BACKUP_DIR}/vkpi-media-cache.tgz" > "${REMOTE_BACKUP_DIR}/vkpi-media-cache.tgz.sha256"
  fi
else
  echo "missing uploads/vkpi_media_cache" > "${REMOTE_BACKUP_DIR}/media-cache-size.txt"
  : > "${REMOTE_BACKUP_DIR}/media-cache-file-count.txt"
  : > "${REMOTE_BACKUP_DIR}/media-cache-manifest.tsv"
fi

find "${REMOTE_BACKUP_DIR}" -maxdepth 1 -type f -printf '%f\n' | sort
REMOTE

scp -q "${SSH_TARGET}:${REMOTE_ROOT}/${REMOTE_BACKUP_DIR}/"* "${LOCAL_DIR}/"

ln -sfn "${STAMP}" "${LOCAL_PARENT}/latest"

echo "prod backup downloaded: ${LOCAL_DIR}"
echo "db dump: ${LOCAL_DIR}/prod-db.dump"
echo "media manifest: ${LOCAL_DIR}/media-cache-manifest.tsv"
