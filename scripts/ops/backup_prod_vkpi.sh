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
VKPI_BACKUP_ENCRYPT_ENV="${VKPI_BACKUP_ENCRYPT_ENV:-0}"
VKPI_BACKUP_GPG_PASSPHRASE_FILE="${VKPI_BACKUP_GPG_PASSPHRASE_FILE:-}"
LOCAL_PYTHON_BIN="${LOCAL_PYTHON_BIN:-python3}"

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
LOCAL_ENCRYPTION_TMP_DIR=""
cleanup_local() {
  rm -f -- "${LOCAL_VALIDATION_ERR}"
  if [ -n "${LOCAL_ENCRYPTION_TMP_DIR}" ]; then
    rm -rf -- "${LOCAL_ENCRYPTION_TMP_DIR}"
  fi
}
trap cleanup_local EXIT

case "${VKPI_BACKUP_ENCRYPT_ENV}" in
  0|1) ;;
  *)
    echo "VKPI_BACKUP_ENCRYPT_ENV must be exactly 0 or 1" >&2
    exit 1
    ;;
esac
if [ "${VKPI_BACKUP_ENCRYPT_ENV}" = "1" ]; then
  for required_command in gpg "${LOCAL_PYTHON_BIN}"; do
    if ! command -v "${required_command}" >/dev/null 2>&1; then
      echo "missing required local encrypted-backup command: ${required_command}" >&2
      exit 1
    fi
  done
  if [ -z "${VKPI_BACKUP_GPG_PASSPHRASE_FILE}" ]; then
    echo "VKPI_BACKUP_GPG_PASSPHRASE_FILE is required when encrypted backup is enabled" >&2
    exit 1
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 "${LOCAL_PYTHON_BIN}" -B - "${VKPI_BACKUP_GPG_PASSPHRASE_FILE}" <<'PY'
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
try:
    metadata = path.lstat()
except OSError as exc:
    raise SystemExit("passphrase file is unavailable") from exc
mode = stat.S_IMODE(metadata.st_mode)
if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
    raise SystemExit("passphrase file must be a regular non-symlink file")
if metadata.st_nlink != 1:
    raise SystemExit("passphrase file must have exactly one hard link")
if metadata.st_uid != os.geteuid():
    raise SystemExit("passphrase file must be owned by the current local user")
if mode & 0o077 or mode & ~0o700 or not mode & stat.S_IRUSR:
    raise SystemExit("passphrase file must be owner-readable and owner-only")
if metadata.st_size < 1 or metadata.st_size > 65536:
    raise SystemExit("passphrase file size is invalid")
PY
  then
    echo "encrypted backup passphrase file failed local safety validation" >&2
    exit 1
  fi
  for artifact_name in environment.gpg environment.gpg.sha256 off-host-backup-receipt.json; do
    artifact_path="${LOCAL_DIR}/${artifact_name}"
    if [ -e "${artifact_path}" ] || [ -L "${artifact_path}" ]; then
      echo "refusing to overwrite an existing encrypted backup artifact" >&2
      exit 1
    fi
  done
fi

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
if ! PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -B - "${remote_pgservice}" "${remote_pgpass}" <<'PY'
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

if [ "${VKPI_BACKUP_ENCRYPT_ENV}" = "1" ]; then
  LOCAL_ENV_CIPHERTEXT="${LOCAL_DIR}/environment.gpg"
  LOCAL_ENV_SIDECAR="${LOCAL_DIR}/environment.gpg.sha256"
  LOCAL_OFFHOST_RECEIPT="${LOCAL_DIR}/off-host-backup-receipt.json"
  for artifact in \
    "${LOCAL_ENV_CIPHERTEXT}" \
    "${LOCAL_ENV_SIDECAR}" \
    "${LOCAL_OFFHOST_RECEIPT}"; do
    if [ -e "${artifact}" ] || [ -L "${artifact}" ]; then
      echo "refusing to overwrite an existing encrypted backup artifact" >&2
      exit 1
    fi
  done

  LOCAL_ENCRYPTION_TMP_DIR="$(mktemp -d "${LOCAL_DIR}/.env-encryption.tmp.XXXXXX")"
  TMP_ENV_CIPHERTEXT="${LOCAL_ENCRYPTION_TMP_DIR}/environment.gpg"
  TMP_ENV_SIDECAR="${LOCAL_ENCRYPTION_TMP_DIR}/environment.gpg.sha256"
  TMP_OFFHOST_RECEIPT="${LOCAL_ENCRYPTION_TMP_DIR}/off-host-backup-receipt.json"

  stream_protected_remote_environment() {
    ssh "${SSH_TARGET}" "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env REMOTE_ROOT='${REMOTE_ROOT}' REMOTE_APP_USER='${REMOTE_APP_USER}' REMOTE_APP_GROUP='${REMOTE_APP_GROUP}' VKPI_BACKUP_STREAM_ENV=1 bash -s" <<'REMOTE_ENV'
set -euo pipefail
cd "${REMOTE_ROOT}"
if [ ! -f .env ] || [ -L .env ]; then
  echo "remote environment file must be a regular non-symlink file" >&2
  exit 1
fi
IFS=: read -r env_owner env_group env_mode env_links env_extra <<EOF
$(stat -c '%U:%G:%a:%h' .env)
EOF
if [ -n "${env_extra:-}" ] || [ "${env_links}" != "1" ]; then
  echo "remote environment file link metadata is unsafe" >&2
  exit 1
fi
case "${env_owner}:${env_group}" in
  "${REMOTE_APP_USER}:${REMOTE_APP_GROUP}"|"root:${REMOTE_APP_GROUP}") ;;
  *) echo "remote environment file ownership is unsafe" >&2; exit 1 ;;
esac
case "${env_mode}" in
  400|440|600|640) ;;
  *) echo "remote environment file permissions are unsafe" >&2; exit 1 ;;
esac
if [ ! -s .env ]; then
  echo "remote environment file is empty" >&2
  exit 1
fi
cat -- .env
REMOTE_ENV
  }

  if ! stream_protected_remote_environment \
    | gpg --no-options --batch --quiet --pinentry-mode loopback \
        --passphrase-file "${VKPI_BACKUP_GPG_PASSPHRASE_FILE}" \
        --cipher-algo AES256 --symmetric --output "${TMP_ENV_CIPHERTEXT}" \
        2>"${LOCAL_VALIDATION_ERR}"; then
    echo "remote environment encryption failed (details hidden)" >&2
    exit 1
  fi
  if [ ! -s "${TMP_ENV_CIPHERTEXT}" ] || [ -L "${TMP_ENV_CIPHERTEXT}" ]; then
    echo "environment encryption did not create a safe ciphertext" >&2
    exit 1
  fi
  chmod 600 "${TMP_ENV_CIPHERTEXT}"
  if ! gpg --no-options --batch --quiet --pinentry-mode loopback \
    --passphrase-file "${VKPI_BACKUP_GPG_PASSPHRASE_FILE}" \
    --decrypt "${TMP_ENV_CIPHERTEXT}" >/dev/null 2>"${LOCAL_VALIDATION_ERR}"; then
    echo "local environment ciphertext decryption verification failed (details hidden)" >&2
    exit 1
  fi
  rm -f -- "${LOCAL_VALIDATION_ERR}"

  if command -v shasum >/dev/null 2>&1; then
    ENV_CIPHERTEXT_SHA="$(shasum -a 256 "${TMP_ENV_CIPHERTEXT}" | awk '{print $1}')"
  else
    ENV_CIPHERTEXT_SHA="$(sha256sum "${TMP_ENV_CIPHERTEXT}" | awk '{print $1}')"
  fi
  if ! [[ "${ENV_CIPHERTEXT_SHA}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "environment ciphertext SHA-256 calculation failed" >&2
    exit 1
  fi
  printf '%s  environment.gpg\n' "${ENV_CIPHERTEXT_SHA}" > "${TMP_ENV_SIDECAR}"
  chmod 600 "${TMP_ENV_SIDECAR}"
  if ! PYTHONDONTWRITEBYTECODE=1 "${LOCAL_PYTHON_BIN}" -B - \
    "${TMP_OFFHOST_RECEIPT}" "${STAMP}" "${ACTUAL_SHA}" "${ENV_CIPHERTEXT_SHA}" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
payload = {
    "schema_version": "vkpi-off-host-backup-receipt/v1",
    "method": "ssh_pull_verified_mac",
    "stamp": sys.argv[2],
    "db_artifact": "prod-db.dump",
    "db_sha256": sys.argv[3],
    "environment_ciphertext_artifact": "environment.gpg",
    "environment_ciphertext_sha256": sys.argv[4],
    "pg_restore_list_passed": True,
    "environment_decryption_verified": True,
    "local_copy_verified": True,
    "plaintext_environment_persisted": False,
}
encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "wb") as handle:
    handle.write(encoded)
PY
  then
    echo "unable to create off-host backup receipt" >&2
    exit 1
  fi

  # Hard-link publication is atomic and refuses an existing destination.  All
  # source and destination paths are inside the same private backup directory.
  for artifact_name in environment.gpg environment.gpg.sha256 off-host-backup-receipt.json; do
    if ! ln "${LOCAL_ENCRYPTION_TMP_DIR}/${artifact_name}" "${LOCAL_DIR}/${artifact_name}"; then
      echo "refusing to overwrite an existing encrypted backup artifact" >&2
      exit 1
    fi
    rm -f -- "${LOCAL_ENCRYPTION_TMP_DIR:?}/${artifact_name}"
    chmod 600 "${LOCAL_DIR}/${artifact_name}"
  done

  push_private_backup_artifact() {
    local source_path="$1"
    local artifact_name="$2"
    case "${artifact_name}" in
      environment.gpg|environment.gpg.sha256|off-host-backup-receipt.json) ;;
      *) echo "refusing unsafe remote backup artifact name" >&2; return 1 ;;
    esac
    if ! ssh "${SSH_TARGET}" "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env REMOTE_ROOT='${REMOTE_ROOT}' REMOTE_BACKUP_DIR='${REMOTE_BACKUP_DIR}' REMOTE_APP_USER='${REMOTE_APP_USER}' REMOTE_APP_GROUP='${REMOTE_APP_GROUP}' VKPI_BACKUP_ARTIFACT_NAME='${artifact_name}' bash -c 'set -euo pipefail; umask 077; cd \"\${REMOTE_ROOT}\"; destination=\"\${REMOTE_BACKUP_DIR}/\${VKPI_BACKUP_ARTIFACT_NAME}\"; if [ -e \"\${destination}\" ] || [ -L \"\${destination}\" ]; then echo \"refusing to overwrite remote encrypted backup artifact\" >&2; exit 1; fi; set -C; cat > \"\${destination}\"; test -s \"\${destination}\"; chmod 600 \"\${destination}\"'" < "${source_path}"; then
      echo "encrypted backup artifact transfer failed: ${artifact_name}" >&2
      return 1
    fi
  }

  push_private_backup_artifact "${LOCAL_ENV_CIPHERTEXT}" environment.gpg
  push_private_backup_artifact "${LOCAL_ENV_SIDECAR}" environment.gpg.sha256
  push_private_backup_artifact "${LOCAL_OFFHOST_RECEIPT}" off-host-backup-receipt.json

  if ! ssh "${SSH_TARGET}" "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env REMOTE_ROOT='${REMOTE_ROOT}' REMOTE_BACKUP_DIR='${REMOTE_BACKUP_DIR}' PYTHON_BIN='${PYTHON_BIN}' REMOTE_APP_USER='${REMOTE_APP_USER}' REMOTE_APP_GROUP='${REMOTE_APP_GROUP}' bash -s" <<'REMOTE_VERIFY'
set -euo pipefail
case "${PYTHON_BIN}" in
  /*) remote_python="${PYTHON_BIN}" ;;
  *) remote_python="${REMOTE_ROOT}/${PYTHON_BIN}" ;;
esac
if [ ! -x "${remote_python}" ]; then
  echo "remote Python runtime is unavailable during receipt verification" >&2
  exit 1
fi
cd "${REMOTE_ROOT}/${REMOTE_BACKUP_DIR}"
for artifact in environment.gpg environment.gpg.sha256 off-host-backup-receipt.json; do
  if [ ! -f "${artifact}" ] || [ -L "${artifact}" ]; then
    echo "remote encrypted backup artifact is incomplete or unsafe" >&2
    exit 1
  fi
  IFS=: read -r artifact_owner artifact_group artifact_mode artifact_links artifact_extra <<EOF
$(stat -c '%U:%G:%a:%h' "${artifact}")
EOF
  if [ -n "${artifact_extra:-}" ] || [ "${artifact_owner}:${artifact_group}" != "${REMOTE_APP_USER}:${REMOTE_APP_GROUP}" ] \
    || [ "${artifact_mode}" != "600" ] || [ "${artifact_links}" != "1" ]; then
    echo "remote encrypted backup artifact metadata is unsafe" >&2
    exit 1
  fi
done
read -r env_sha env_name env_extra < environment.gpg.sha256 || true
if ! [[ "${env_sha:-}" =~ ^[0-9a-f]{64}$ ]] || [ "${env_name:-}" != "environment.gpg" ] \
  || [ -n "${env_extra:-}" ]; then
  echo "remote environment ciphertext sidecar is invalid" >&2
  exit 1
fi
actual_env_sha="$(sha256sum environment.gpg | awk '{print $1}')"
if [ "${actual_env_sha}" != "${env_sha}" ]; then
  echo "remote environment ciphertext SHA-256 mismatch" >&2
  exit 1
fi
read -r db_sha db_name db_extra < prod-db.dump.sha256 || true
if ! [[ "${db_sha:-}" =~ ^[0-9a-f]{64}$ ]] || [ "${db_name:-}" != "prod-db.dump" ] \
  || [ -n "${db_extra:-}" ]; then
  echo "remote database sidecar is invalid during off-host receipt verification" >&2
  exit 1
fi
PYTHONDONTWRITEBYTECODE=1 "${remote_python}" -B - off-host-backup-receipt.json "${db_sha}" "${env_sha}" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = {
    "schema_version": "vkpi-off-host-backup-receipt/v1",
    "method": "ssh_pull_verified_mac",
    "db_sha256": sys.argv[2],
    "environment_ciphertext_sha256": sys.argv[3],
    "pg_restore_list_passed": True,
    "environment_decryption_verified": True,
    "local_copy_verified": True,
    "plaintext_environment_persisted": False,
}
if any(payload.get(key) != value for key, value in required.items()):
    raise SystemExit("remote off-host receipt checksum binding is invalid")
PY
REMOTE_VERIFY
  then
    echo "remote encrypted backup receipt verification failed" >&2
    exit 1
  fi
fi

ln -sfn "${STAMP}" "${LOCAL_PARENT}/latest"

echo "prod backup downloaded: ${LOCAL_DIR}"
echo "db dump: ${LOCAL_DIR}/prod-db.dump"
echo "media manifest: ${LOCAL_DIR}/media-cache-manifest.tsv"
echo "backup verification: sha256=passed pg_restore_list=passed"
if [ "${VKPI_BACKUP_ENCRYPT_ENV}" = "1" ]; then
  echo "off-host verification: encrypted_environment=passed local_copy=passed remote_receipt=passed"
fi
