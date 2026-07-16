#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOCAL_PARENT="${LOCAL_PARENT:-runtime/prod-sync}"
LOCAL_DIR="${LOCAL_DIR:-${LOCAL_PARENT}/${STAMP}}"
REMOTE_BACKUP_DIR="backups/ops/${STAMP}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
REMOTE_APP_USER="${REMOTE_APP_USER:-viltrox}"
REMOTE_APP_GROUP="${REMOTE_APP_GROUP:-viltrox}"

umask 077
mkdir -p "${LOCAL_DIR}"

for required_command in pg_restore; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "missing required local command: ${required_command}" >&2
    exit 1
  fi
done
if ! command -v shasum >/dev/null 2>&1 && ! command -v sha256sum >/dev/null 2>&1; then
  echo "missing local SHA-256 utility (shasum or sha256sum)" >&2
  exit 1
fi

LOCAL_VALIDATION_ERR="${LOCAL_DIR}/.backup-validation.err.$$"
cleanup_local() {
  rm -f -- "${LOCAL_VALIDATION_ERR}"
}
trap cleanup_local EXIT

# The SSH transport may be the locked-down deploy controller (currently root on
# the legacy host), but the backup itself must execute as the reviewed app
# account.  Drop privileges before the first remote shell statement so the
# in-script identity/ownership checks remain meaningful and backup artifacts
# cannot be created as root.
ssh "${SSH_TARGET}" "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env REMOTE_ROOT='${REMOTE_ROOT}' REMOTE_BACKUP_DIR='${REMOTE_BACKUP_DIR}' PYTHON_BIN='${PYTHON_BIN}' REMOTE_APP_USER='${REMOTE_APP_USER}' REMOTE_APP_GROUP='${REMOTE_APP_GROUP}' BACKUP_MEDIA_ARCHIVE='${BACKUP_MEDIA_ARCHIVE:-0}' bash -s" <<'REMOTE'
set -euo pipefail

cd "${REMOTE_ROOT}"
umask 077
remote_pgservice=""
remote_pgpass=""
remote_command_err=""
cleanup_remote() {
  rm -f -- "${backup_canary:-}" "${remote_pgservice:-}" \
    "${remote_pgpass:-}" "${remote_command_err:-}"
}
trap cleanup_remote EXIT

# Backup storage is not part of the worker write surface.  The SSH backup
# operator must be the reviewed app account and must already own the shared
# backup root.  Create an absent root as that account, but never recursively
# chown or chmod historical backups during a release.
if [ "$(id -un)" != "${REMOTE_APP_USER}" ]; then
  echo "backup must run as ${REMOTE_APP_USER}; current user is $(id -un)" >&2
  exit 1
fi
case " $(id -Gn) " in
  *" ${REMOTE_APP_GROUP} "*) ;;
  *) echo "backup operator is not in required group ${REMOTE_APP_GROUP}" >&2; exit 1 ;;
esac
if [ ! -e backups ]; then
  mkdir -m 0750 backups
fi
if [ ! -d backups ] || [ -L backups ]; then
  echo "backup root must be a real directory: ${REMOTE_ROOT}/backups" >&2
  exit 1
fi
if [ "$(stat -c '%U:%G' backups)" != "${REMOTE_APP_USER}:${REMOTE_APP_GROUP}" ]; then
  echo "backup root ownership mismatch; expected ${REMOTE_APP_USER}:${REMOTE_APP_GROUP}; refusing recursive chown" >&2
  exit 1
fi
backup_canary="$(mktemp backups/.vkpi-backup-preflight.XXXXXX)"
printf 'vkpi-backup-permission-canary\n' > "${backup_canary}"
test -s "${backup_canary}"
rm -f -- "${backup_canary}"
backup_canary=""
mkdir -p "${REMOTE_BACKUP_DIR}"

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "python runtime not found: ${PYTHON_BIN}" >&2
  exit 1
fi

for required_command in pg_dump pg_restore sha256sum; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "${required_command} not found on remote host" >&2
    exit 1
  fi
done

# Never put DATABASE_URL in argv or stdout. Parse the private env file directly
# into short-lived libpq service/pass files, then destroy both on every exit.
remote_pgservice="${REMOTE_BACKUP_DIR}/.pgservice.tmp.$$"
remote_pgpass="${REMOTE_BACKUP_DIR}/.pgpass.tmp.$$"
remote_command_err="${REMOTE_BACKUP_DIR}/.command.err.tmp.$$"
if ! "${PYTHON_BIN}" - "${remote_pgservice}" "${remote_pgpass}" <<'PY'
from __future__ import annotations

import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, unquote, urlsplit

service_path = Path(sys.argv[1])
pgpass_path = Path(sys.argv[2])
value = ""
env_path = Path(".env")
if env_path.is_file() and not env_path.is_symlink():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, candidate = raw.split("=", 1)
        if key.strip() == "DATABASE_URL":
            value = candidate.strip().strip("'\"")
            break
if not value:
    raise SystemExit("DATABASE_URL is missing")

parts = urlsplit(value)
if parts.scheme not in {"postgres", "postgresql"} or parts.fragment:
    raise SystemExit("DATABASE_URL is invalid")
params: dict[str, str] = {}
if parts.hostname:
    params["host"] = unquote(parts.hostname)
try:
    if parts.port is not None:
        params["port"] = str(parts.port)
except ValueError as exc:
    raise SystemExit("DATABASE_URL port is invalid") from exc
if parts.username is not None:
    params["user"] = unquote(parts.username)
password = unquote(parts.password) if parts.password is not None else ""
if parts.path and parts.path != "/":
    params["dbname"] = unquote(parts.path[1:])
for key, candidate in parse_qsl(parts.query, keep_blank_values=True):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise SystemExit("DATABASE_URL contains an invalid parameter")
    key = key.lower()
    if key == "password":
        password = candidate
    elif key in {"service", "passfile"}:
        raise SystemExit("DATABASE_URL contains a forbidden parameter")
    else:
        params[key] = candidate
if any(any(ch in candidate for ch in "\r\n\0") for candidate in params.values()):
    raise SystemExit("DATABASE_URL contains an unsafe control character")
if any(ch in password for ch in "\r\n\0"):
    raise SystemExit("DATABASE_URL password contains an unsafe control character")

def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

write_private(
    service_path,
    "[vkpi_prod_backup]\n"
    + "\n".join(f"{key}={candidate}" for key, candidate in params.items())
    + "\n",
)
escaped_password = password.replace("\\", "\\\\").replace(":", "\\:")
write_private(pgpass_path, f"*:*:*:*:{escaped_password}\n")
PY
then
  echo "unable to prepare private PostgreSQL backup credentials" >&2
  exit 1
fi

run_with_backup_service() {
  PGSERVICEFILE="${remote_pgservice}" PGSERVICE="vkpi_prod_backup" \
    PGPASSFILE="${remote_pgpass}" "$@"
}

remote_dump="${REMOTE_BACKUP_DIR}/prod-db.dump"
remote_sidecar="${remote_dump}.sha256"
if [ -e "${remote_dump}" ] || [ -e "${remote_sidecar}" ]; then
  echo "refusing to overwrite an existing production backup stamp" >&2
  exit 1
fi
if ! run_with_backup_service pg_dump --format=custom --no-owner --no-acl \
  --file="${remote_dump}" 2>"${remote_command_err}"; then
  echo "production pg_dump failed (connection details hidden)" >&2
  exit 1
fi
if [ ! -s "${remote_dump}" ]; then
  echo "production pg_dump created an empty archive" >&2
  exit 1
fi
if ! pg_restore --list "${remote_dump}" >/dev/null 2>"${remote_command_err}"; then
  echo "remote pg_restore could not read the production archive" >&2
  exit 1
fi
(
  cd "${REMOTE_BACKUP_DIR}"
  sha256sum prod-db.dump > prod-db.dump.sha256
)
rm -f -- "${remote_pgservice}" "${remote_pgpass}" "${remote_command_err}"
remote_pgservice=""
remote_pgpass=""
remote_command_err=""

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

LOCAL_DUMP="${LOCAL_DIR}/prod-db.dump"
LOCAL_SIDECAR="${LOCAL_DIR}/prod-db.dump.sha256"
if [ ! -f "${LOCAL_DUMP}" ] || [ -L "${LOCAL_DUMP}" ] \
  || [ ! -f "${LOCAL_SIDECAR}" ] || [ -L "${LOCAL_SIDECAR}" ]; then
  echo "downloaded production backup bundle is incomplete or unsafe" >&2
  exit 1
fi
chmod 600 "${LOCAL_DUMP}" "${LOCAL_SIDECAR}"

read -r EXPECTED_SHA SIDECAR_NAME SIDECAR_EXTRA < "${LOCAL_SIDECAR}" || true
if ! [[ "${EXPECTED_SHA:-}" =~ ^[0-9a-fA-F]{64}$ ]] \
  || [ "${SIDECAR_NAME:-}" != "prod-db.dump" ] \
  || [ -n "${SIDECAR_EXTRA:-}" ]; then
  echo "downloaded production backup SHA-256 sidecar is invalid" >&2
  exit 1
fi
if command -v shasum >/dev/null 2>&1; then
  ACTUAL_SHA="$(shasum -a 256 "${LOCAL_DUMP}" | awk '{print $1}')"
else
  ACTUAL_SHA="$(sha256sum "${LOCAL_DUMP}" | awk '{print $1}')"
fi
if [ "$(printf '%s' "${EXPECTED_SHA}" | tr '[:upper:]' '[:lower:]')" != "${ACTUAL_SHA}" ]; then
  echo "downloaded production backup SHA-256 mismatch" >&2
  exit 1
fi
if ! pg_restore --list "${LOCAL_DUMP}" >/dev/null 2>"${LOCAL_VALIDATION_ERR}"; then
  echo "local pg_restore could not read the downloaded production archive" >&2
  exit 1
fi
rm -f -- "${LOCAL_VALIDATION_ERR}"

ln -sfn "${STAMP}" "${LOCAL_PARENT}/latest"

echo "prod backup downloaded: ${LOCAL_DIR}"
echo "db dump: ${LOCAL_DIR}/prod-db.dump"
echo "media manifest: ${LOCAL_DIR}/media-cache-manifest.tsv"
echo "backup verification: sha256=passed pg_restore_list=passed"
