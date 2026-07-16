#!/usr/bin/env bash
# 本地一键备份(local-first):DB pg_dump + .env 快照 + 迁移指纹。保留最近 N 份。
# 用法:bash scripts/ops/backup_local_vkpi.sh   (从 .env 读 DATABASE_URL)
# gate_12 灾备:DB / env / 迁移 三件套;R2 媒体由 runtime 同步另行处理。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
DB_DIR="${DB_DIR:-runtime/db-backups}"
ENV_DIR="${ENV_DIR:-runtime/env-backups}"
RETAIN="${RETAIN:-14}"   # 保留最近 14 份
umask 077

if ! [[ "${STAMP}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "ERR: STAMP 格式无效" >&2
  exit 1
fi
if ! [[ "${RETAIN}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERR: RETAIN 必须为正整数" >&2
  exit 1
fi

mkdir -p "${DB_DIR}" "${ENV_DIR}"

FINAL_DUMP="${DB_DIR}/vkpi-${STAMP}.dump"
FINAL_SHA="${FINAL_DUMP}.sha256"
FINAL_META="${DB_DIR}/vkpi-${STAMP}.meta.json"
FINAL_ENV="${ENV_DIR}/.env.${STAMP}"
TMP_DUMP="${DB_DIR}/.vkpi-${STAMP}.dump.tmp.$$"
TMP_SHA="${DB_DIR}/.vkpi-${STAMP}.dump.sha256.tmp.$$"
TMP_META="${DB_DIR}/.vkpi-${STAMP}.meta.json.tmp.$$"
TMP_ENV="${ENV_DIR}/..env.${STAMP}.tmp.$$"
TMP_COMMAND_ERR="${DB_DIR}/.vkpi-${STAMP}.command.err.tmp.$$"
TMP_PGSERVICE="${DB_DIR}/.vkpi-${STAMP}.pgservice.tmp.$$"
TMP_PGPASS="${DB_DIR}/.vkpi-${STAMP}.pgpass.tmp.$$"
LOCK_DIR="${DB_DIR}/.backup_local_vkpi.lock"
BACKUP_PUBLISHED=0
OWNS_FINAL_PATHS=0
LOCK_HELD=0

cleanup() {
  local status="$?"
  trap - EXIT
  rm -f -- "${TMP_DUMP}" "${TMP_SHA}" "${TMP_META}" "${TMP_ENV}" \
    "${TMP_COMMAND_ERR}" "${TMP_PGSERVICE}" "${TMP_PGPASS}"
  if [ "${status}" -ne 0 ] && [ "${BACKUP_PUBLISHED}" -ne 1 ] && [ "${OWNS_FINAL_PATHS}" -eq 1 ]; then
    # A failed invocation must not leave a partial bundle that looks restorable.
    rm -f -- "${FINAL_DUMP}" "${FINAL_SHA}" "${FINAL_META}" "${FINAL_ENV}"
  fi
  if [ "${LOCK_HELD}" -eq 1 ]; then
    rmdir "${LOCK_DIR}" 2>/dev/null || true
  fi
  exit "${status}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

for required_command in pg_dump pg_restore psql; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "ERR: 缺少必需命令 ${required_command}" >&2
    exit 1
  fi
done
if ! command -v shasum >/dev/null 2>&1 && ! command -v sha256sum >/dev/null 2>&1; then
  echo "ERR: 缺少 SHA-256 工具(shasum 或 sha256sum)" >&2
  exit 1
fi
if [ -x "${ROOT}/.venv/bin/python" ]; then
  PYTHON_BIN="${ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "ERR: 缺少 Python 3,无法安全解析 DATABASE_URL" >&2
  exit 1
fi

if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "ERR: 另一个本地备份正在运行或备份锁待人工核验" >&2
  exit 1
fi
LOCK_HELD=1

for output_path in "${FINAL_DUMP}" "${FINAL_SHA}" "${FINAL_META}" "${FINAL_ENV}"; do
  if [ -e "${output_path}" ]; then
    echo "ERR: 备份 stamp 已存在,拒绝覆盖: ${STAMP}" >&2
    exit 1
  fi
done
OWNS_FINAL_PATHS=1

DATABASE_URL=""
while IFS= read -r env_line || [ -n "${env_line}" ]; do
  case "${env_line}" in
    DATABASE_URL=*)
      DATABASE_URL="${env_line#DATABASE_URL=}"
      break
      ;;
  esac
done < .env 2>/dev/null || true
DATABASE_URL="${DATABASE_URL%$'\r'}"
case "${DATABASE_URL}" in
  \"*\") DATABASE_URL="${DATABASE_URL:1:${#DATABASE_URL}-2}" ;;
  \'*\') DATABASE_URL="${DATABASE_URL:1:${#DATABASE_URL}-2}" ;;
esac
if [ -z "${DATABASE_URL}" ]; then
  echo "ERR: .env 无 DATABASE_URL" >&2
  exit 1
fi

# Never place DATABASE_URL (which commonly embeds a password) in argv: process
# listings, test harnesses and shell tracing can expose command arguments.
# Parse it through a private FD, put non-password libpq parameters in a 0600
# service file, and put the decoded password in a separate 0600 pgpass file.
# URI percent encoding and query parameters (for example sslmode/sslrootcert/
# application_name) are preserved as their decoded libpq parameter values.
if ! "${PYTHON_BIN}" - "${TMP_PGSERVICE}" "${TMP_PGPASS}" 3<<<"${DATABASE_URL}" <<'PY'
from __future__ import annotations

import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, unquote, urlsplit

service_path = Path(sys.argv[1])
pgpass_path = Path(sys.argv[2])
raw = os.fdopen(3, encoding="utf-8").read()
if raw.endswith("\n"):
    raw = raw[:-1]
parts = urlsplit(raw)
if parts.scheme not in {"postgres", "postgresql"}:
    raise SystemExit("DATABASE_URL scheme must be postgres/postgresql")
if parts.fragment:
    raise SystemExit("DATABASE_URL fragments are not supported")

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

for key, value in parse_qsl(parts.query, keep_blank_values=True):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise SystemExit("DATABASE_URL contains an invalid libpq parameter name")
    key = key.lower()
    if key == "password":
        password = value
        continue
    if key in {"service", "passfile"}:
        raise SystemExit(f"DATABASE_URL query parameter {key} is not allowed for backup")
    params[key] = value

for key, value in params.items():
    if any(ch in value for ch in "\r\n\0"):
        raise SystemExit(f"DATABASE_URL parameter {key} contains an unsafe control character")
if any(ch in password for ch in "\r\n\0"):
    raise SystemExit("DATABASE_URL password contains an unsafe control character")

def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

service_lines = ["[vkpi_backup]"]
service_lines.extend(f"{key}={value}" for key, value in params.items())
write_private(service_path, "\n".join(service_lines) + "\n")
escaped_password = password.replace("\\", "\\\\").replace(":", "\\:")
write_private(pgpass_path, f"*:*:*:*:{escaped_password}\n")
PY
then
  echo "ERR: DATABASE_URL 无法安全解析为 libpq 连接配置" >&2
  exit 1
fi
unset DATABASE_URL

run_with_backup_service() {
  PGSERVICEFILE="${TMP_PGSERVICE}" PGSERVICE="vkpi_backup" \
    PGPASSFILE="${TMP_PGPASS}" "$@"
}

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

# migration_max 必须来自目标数据库真实 schema_migrations，不能用代码文件名代替。
# 在 dump 前读取，若此刻恰有迁移并发，元数据最多保守地低报而不会高报归档状态。
if ! MIGMAX="$(run_with_backup_service psql -X --no-psqlrc -qAt -v ON_ERROR_STOP=1 \
  -c 'SELECT MAX(version_key) FROM schema_migrations' 2>"${TMP_COMMAND_ERR}")"; then
  echo "ERR: 无法从 schema_migrations 读取已应用迁移" >&2
  exit 1
fi
MIGMAX="${MIGMAX%$'\r'}"
if ! [[ "${MIGMAX}" =~ ^[0-9]{3}_[A-Za-z0-9_.-]+\.sql$ ]]; then
  echo "ERR: schema_migrations 未返回有效 migration_max" >&2
  exit 1
fi

# 所有产物先写入同目录临时文件；dump 最后才原子发布到最终文件名。
echo "→ pg_dump → ${FINAL_DUMP}"
if ! run_with_backup_service pg_dump --no-owner --no-privileges -Fc -f "${TMP_DUMP}" \
  2>"${TMP_COMMAND_ERR}"; then
  echo "ERR: pg_dump 失败(连接详情已隐藏)" >&2
  exit 1
fi
if [ ! -s "${TMP_DUMP}" ]; then
  echo "ERR: pg_dump 生成了空归档" >&2
  exit 1
fi
if ! pg_restore --list "${TMP_DUMP}" >/dev/null 2>"${TMP_COMMAND_ERR}"; then
  echo "ERR: pg_restore 无法读取备份归档" >&2
  exit 1
fi

DUMP_BYTES="$(wc -c < "${TMP_DUMP}" | tr -d '[:space:]')"
DUMP_SHA256="$(sha256_file "${TMP_DUMP}")"
if ! [[ "${DUMP_BYTES}" =~ ^[1-9][0-9]*$ ]] || ! [[ "${DUMP_SHA256}" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "ERR: 备份大小或 SHA-256 校验失败" >&2
  exit 1
fi
DUMP_SHA256="$(printf '%s' "${DUMP_SHA256}" | tr '[:upper:]' '[:lower:]')"

cp -p .env "${TMP_ENV}"
if [ ! -s "${TMP_ENV}" ]; then
  echo "ERR: .env 快照为空" >&2
  exit 1
fi
chmod 600 "${TMP_ENV}"
printf '%s  %s\n' "${DUMP_SHA256}" "$(basename "${FINAL_DUMP}")" > "${TMP_SHA}"
printf '{"stamp":"%s","migration_max":"%s","migration_max_source":"schema_migrations","dump":"%s","dump_bytes":%s,"dump_sha256":"%s","archive_verified":true}\n' \
  "${STAMP}" "${MIGMAX}" "$(basename "${FINAL_DUMP}")" "${DUMP_BYTES}" "${DUMP_SHA256}" > "${TMP_META}"

# 伴随文件先发布，dump 最后发布；任何中途失败由 trap 清理整套最终文件。
mv -- "${TMP_ENV}" "${FINAL_ENV}"
mv -- "${TMP_SHA}" "${FINAL_SHA}"
mv -- "${TMP_META}" "${FINAL_META}"
mv -- "${TMP_DUMP}" "${FINAL_DUMP}"
BACKUP_PUBLISHED=1
rm -f -- "${TMP_COMMAND_ERR}"

# 4) 保留策略:按 dump 时间只留最近 RETAIN 套，并同步删除其伴随文件。
shopt -s nullglob
dump_files=("${DB_DIR}"/vkpi-*.dump)
if [ "${#dump_files[@]}" -gt "${RETAIN}" ]; then
  kept=0
  while IFS= read -r old_dump; do
    kept=$((kept + 1))
    if [ "${kept}" -le "${RETAIN}" ]; then
      continue
    fi
    old_name="$(basename "${old_dump}" .dump)"
    old_stamp="${old_name#vkpi-}"
    rm -f -- "${old_dump}" "${old_dump}.sha256" \
      "${DB_DIR}/${old_name}.meta.json" "${ENV_DIR}/.env.${old_stamp}"
  done < <(ls -1t "${dump_files[@]}")
fi

echo "  ${DUMP_BYTES} bytes sha256=${DUMP_SHA256}"
echo "→ .env 快照 ${FINAL_ENV}"
echo "✓ 本地备份完成 stamp=${STAMP} migration_max=${MIGMAX} (保留最近 ${RETAIN} 份)"
echo "  恢复演练:使用 scripts/ops/postgres_restore_rehearsal.py 和独立、经证明的一次性 PostgreSQL 集群"
echo "  禁止把 DATABASE_URL 放入命令行或将归档直接恢复到活动库"
