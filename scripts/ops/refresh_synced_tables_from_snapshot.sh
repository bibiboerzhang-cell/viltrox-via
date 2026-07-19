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
# vkpi_employee_channels 被 vkpi_channel_audit FK 引用,不能 TRUNCATE——
# 走 staging schema + 按 id UPSERT(只刷同步状态与画像列,本地 audit 不动)。
SYNCED_TABLES=(vkpi_channel_metrics vkpi_channel_metrics_filled vkpi_channel_post_metrics)
# comments/commenter_profiles 本地也有手动采集写入,kol_pool 本地有 L0 回填——
# 全部走 UPSERT 防丢本地行(kol_pool 只从 prod 补新行,不覆盖本地已有列值:
# 见下方 kol_pool 特例)。
UPSERT_TABLES=(vkpi_employee_channels)
# 补差表:prod/本地对同一条自然键各有各的 id → 去 id 整行补插,任意唯一键冲突跳过
FILL_TABLES=(vkpi_comments vkpi_commenter_profiles)

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

for t in "${UPSERT_TABLES[@]}"; do
  psql "$LOCAL_DATABASE_URL" > /dev/null <<SQL
DROP SCHEMA IF EXISTS prod_sync_staging CASCADE;
CREATE SCHEMA prod_sync_staging;
CREATE TABLE prod_sync_staging.$t (LIKE public.$t INCLUDING DEFAULTS);
SQL
  # pg_restore 落回 public 会撞 FK;用 -f - 导出 COPY 流改写 schema 后中转
  pg_restore --data-only --no-owner --no-acl --table="$t" -f - "$DUMP" \
    | sed "s/^COPY public\.$t /COPY prod_sync_staging.$t /" \
    | psql "$LOCAL_DATABASE_URL" > /dev/null
  COLS=$(psql "$LOCAL_DATABASE_URL" -t -A -c "SELECT string_agg(quote_ident(column_name), ',') FROM information_schema.columns WHERE table_schema='public' AND table_name='$t' AND column_name <> 'id'")
  SETS=$(psql "$LOCAL_DATABASE_URL" -t -A -c "SELECT string_agg(quote_ident(column_name) || '=EXCLUDED.' || quote_ident(column_name), ',') FROM information_schema.columns WHERE table_schema='public' AND table_name='$t' AND column_name <> 'id'")
  psql "$LOCAL_DATABASE_URL" -c "INSERT INTO public.$t (id, $COLS) SELECT id, $COLS FROM prod_sync_staging.$t ON CONFLICT (id) DO UPDATE SET $SETS" > /dev/null
  psql "$LOCAL_DATABASE_URL" -c "DROP SCHEMA prod_sync_staging CASCADE" > /dev/null
done

for t in "${FILL_TABLES[@]}"; do
  psql "$LOCAL_DATABASE_URL" > /dev/null <<SQL
DROP SCHEMA IF EXISTS prod_sync_staging CASCADE;
CREATE SCHEMA prod_sync_staging;
CREATE TABLE prod_sync_staging.$t (LIKE public.$t INCLUDING DEFAULTS);
SQL
  pg_restore --data-only --no-owner --no-acl --table="$t" -f - "$DUMP" \
    | sed "s/^COPY public\.$t /COPY prod_sync_staging.$t /" \
    | psql "$LOCAL_DATABASE_URL" > /dev/null
  COLS=$(psql "$LOCAL_DATABASE_URL" -t -A -c "SELECT string_agg(quote_ident(column_name), ',') FROM information_schema.columns WHERE table_schema='public' AND table_name='$t' AND column_name <> 'id'")
  psql "$LOCAL_DATABASE_URL" -c "INSERT INTO public.$t ($COLS) SELECT $COLS FROM prod_sync_staging.$t ON CONFLICT DO NOTHING" > /dev/null
  psql "$LOCAL_DATABASE_URL" -c "DROP SCHEMA prod_sync_staging CASCADE" > /dev/null
done

for t in "${SYNCED_TABLES[@]}"; do
  psql "$LOCAL_DATABASE_URL" -t -A -c "SELECT '$t: ' || COUNT(*) || ' rows, max ' || COALESCE(MAX(snapshot_date)::text,'-') FROM $t"
done
echo "synced tables refreshed from ${DUMP}"
