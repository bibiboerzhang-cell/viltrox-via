#!/usr/bin/env bash
# 本地一键备份(local-first):DB pg_dump + .env 快照 + 迁移指纹。保留最近 N 份。
# 用法:bash scripts/ops/backup_local_vkpi.sh   (从 .env 读 DATABASE_URL)
# gate_12 灾备:DB / env / 迁移 三件套;R2 媒体由 runtime 同步另行处理。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DB_DIR="runtime/db-backups"
ENV_DIR="runtime/env-backups"
RETAIN="${RETAIN:-14}"   # 保留最近 14 份
umask 077
mkdir -p "${DB_DIR}" "${ENV_DIR}"

DATABASE_URL="$(grep -E '^DATABASE_URL=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"'' || true)"
if [ -z "${DATABASE_URL}" ]; then
  echo "ERR: .env 无 DATABASE_URL" >&2; exit 1
fi

# 1) DB 全量逻辑备份(自定义格式,便于选择性恢复)
DUMP="${DB_DIR}/vkpi-${STAMP}.dump"
echo "→ pg_dump → ${DUMP}"
pg_dump --no-owner --no-privileges -Fc -d "${DATABASE_URL}" -f "${DUMP}"
echo "  $(du -h "${DUMP}" | cut -f1)"

# 2) .env 快照(密钥不进 git,仅本地灾备)
cp -p .env "${ENV_DIR}/.env.${STAMP}" && echo "→ .env 快照 ${ENV_DIR}/.env.${STAMP}"

# 3) 迁移指纹(迁移本体在 git;此处记录当前已应用 max,便于核对)
MIGMAX="$(ls migrations/*.sql 2>/dev/null | grep -vE '_down' | sed -E 's#.*/([0-9]+).*#\1#' | sort -n | tail -1 || echo '?')"
echo "{\"stamp\":\"${STAMP}\",\"migration_max\":\"${MIGMAX}\",\"dump\":\"$(basename "${DUMP}")\"}" > "${DB_DIR}/vkpi-${STAMP}.meta.json"

# 4) 保留策略:只留最近 RETAIN 份
ls -1t "${DB_DIR}"/vkpi-*.dump 2>/dev/null | tail -n +"$((RETAIN+1))" | xargs -r rm -f
ls -1t "${ENV_DIR}"/.env.* 2>/dev/null | tail -n +"$((RETAIN+1))" | xargs -r rm -f

echo "✓ 本地备份完成 stamp=${STAMP} migration_max=${MIGMAX} (保留最近 ${RETAIN} 份)"
echo "  恢复:pg_restore --clean --no-owner -d \"\$DATABASE_URL\" ${DUMP}"
