#!/usr/bin/env bash
# 快照后置步骤:把「纯线上同步类」表从最新快照刷进本地库,让本地 daily 数据不断档。
# 只动白名单里的同步类表(本地无原创行,truncate+restore 安全);
# 绝不全量恢复——本地施工数据(dealer 候选/情绪标注/分析结果等)一概不碰。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"
source scripts/runtime_env.sh > /dev/null 2>&1

# 同步类表白名单(加表须人工评审:必须是本地零原创写入的表)
# 2026-07-18 体检修:只导两张 metrics 表让本地断更诊断永远看旧账(channels 行
# 停在 06-14、post_metrics 停 07-10 全是镜像假象)——补齐渠道行与帖子层。
SYNCED_TABLES=(vkpi_channel_metrics vkpi_channel_metrics_filled vkpi_employee_channels vkpi_channel_post_metrics)

LATEST_DIR="$(ls -dt runtime/prod-sync/*/ 2>/dev/null | head -1 || true)"
DUMP="${LATEST_DIR%/}/prod-db.dump"
if [[ ! -f "$DUMP" ]]; then
  echo "no snapshot dump found under runtime/prod-sync/, skip"
  exit 0
fi
if [[ -z "${LOCAL_DATABASE_URL:-}" ]]; then
  echo "LOCAL_DATABASE_URL missing, skip" >&2
  exit 1
fi

TRUNC_SQL="TRUNCATE $(IFS=,; echo "${SYNCED_TABLES[*]}")"
psql "$LOCAL_DATABASE_URL" -c "$TRUNC_SQL" > /dev/null

RESTORE_ARGS=(--data-only --no-owner --no-acl)
for t in "${SYNCED_TABLES[@]}"; do RESTORE_ARGS+=(--table="$t"); done
pg_restore "${RESTORE_ARGS[@]}" --dbname "$LOCAL_DATABASE_URL" "$DUMP"

for t in "${SYNCED_TABLES[@]}"; do
  psql "$LOCAL_DATABASE_URL" -t -A -c "SELECT '$t: ' || COUNT(*) || ' rows, max ' || COALESCE(MAX(snapshot_date)::text,'-') FROM $t"
done
echo "synced tables refreshed from ${DUMP}"
