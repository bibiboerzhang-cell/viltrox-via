#!/usr/bin/env bash
# 本地全栈监督器(launchd 常驻):每 60s 巡检六件套,缺谁补谁,daily 更新不断档。
# 由 ~/Library/LaunchAgents/com.vkpi.stack-supervisor.plist 以 KeepAlive 拉起;
# 各 start 脚本自带防重复守卫(pidfile/端口/refusing mixed topology),巡检幂等。
# 烧钱红线:scheduler 永远带任务白名单起(只放 daily 刷新类;kol_auto_poll、
# official_visual_scan 等自动烧钱方不放,点火须用户发令改本清单)。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/runtime/logs/supervisor.log"
mkdir -p "$ROOT/runtime/logs"

# 本地与云端统一采用「1 条 interactive + 15 条 batch」的审定拓扑。把容量目标
# 显式传给 web/worker 健康与 KOL 进度合同；只负责报告期望值，不会因此放宽费用闸。
export APIFY_WORKER_EXPECTED_INSTANCES="${APIFY_WORKER_EXPECTED_INSTANCES:-16}"

# Redis 是通用异步 Worker 的硬前置。失败重试采用有界指数退避，避免 Redis
# 停机时 supervisor 每分钟重拉 worker_main 并无限放大 traceback 日志。
REDIS_RETRY_BASE_SECONDS="${VKPI_SUPERVISOR_REDIS_RETRY_BASE_SECONDS:-60}"
REDIS_RETRY_MAX_SECONDS="${VKPI_SUPERVISOR_REDIS_RETRY_MAX_SECONDS:-900}"
REDIS_RETRY_DELAY_SECONDS="$REDIS_RETRY_BASE_SECONDS"
REDIS_NEXT_RETRY_EPOCH=0
REDIS_AUTORECOVER_ENABLED="${VKPI_SUPERVISOR_REDIS_AUTORECOVER:-0}"
REDIS_AUTORECOVER_NOTICE_EMITTED=0

# 日志自限长:超 5MB 截断,防再造一个 452MB 事故
if [[ -f "$LOG" && $(stat -f%z "$LOG" 2>/dev/null || echo 0) -gt 5242880 ]]; then
  : > "$LOG"
fi

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# 版本自检(O1):launchd KeepAlive 只在进程退出时重拉,部署/dist 拷回改了本文件后
# 常驻的老进程仍跑旧逻辑(改白名单/拓扑不生效)。启动时记自身 sha256,每轮巡检
# 比对,变化即 exec 自身重载(同 PID 语义对 launchd 安全;exec 前 bash -n 防半写文件)。
SELF_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
self_sha256() {
  if command -v shasum > /dev/null 2>&1; then
    shasum -a 256 "$SELF_PATH" 2>/dev/null | awk '{print $1}'
  else
    sha256sum "$SELF_PATH" 2>/dev/null | awk '{print $1}'
  fi
}
SELF_SHA="$(self_sha256)"
reload_if_self_changed() {
  local now
  now="$(self_sha256)"
  if [[ -z "$now" || "$now" == "$SELF_SHA" ]]; then
    return 0
  fi
  if ! bash -n "$SELF_PATH" > /dev/null 2>&1; then
    log "supervisor 文件已变(${SELF_SHA:0:12}→${now:0:12})但 bash -n 不过,暂不重载"
    return 0
  fi
  log "supervisor 文件已变 ${SELF_SHA:0:12}→${now:0:12},exec 自身重载"
  exec bash "$SELF_PATH" "$@"
}

# Per-video refresh and explicit per-staff KOL monitoring are registered here;
# migrations 285/286 keep both task switches OFF until an operator enables them.
SCHEDULER_ALLOWLIST="vkpi_kol_video_metric_refresh,vkpi_kol_content_monitoring,vkpi_market_listening_daily,vkpi_ai_today_hot,vkpi_fit_snapshot,vkpi_comment_sentiment_refresh,daily_action_inbox_generate,fulfillment_due_scan,fulfillment_content_scan,vkpi_market_signal_refresh,vkpi_market_intelligence_refresh,vkpi_official_catalog_sync,vkpi_competitor_radar,vkpi_forecast_outcomes_refresh,vkpi_prediction_weekly_rollup,vkpi_official_daily_report_asia,vkpi_official_daily_report_americas,vkpi_market_mention_sentiment,scheduler_fire_stale_recovery"

ensure_admin_web() {
  if ! curl -sf -m 5 http://127.0.0.1:8102/health > /dev/null 2>&1; then
    log "admin-web 不健康,拉起"
    bash "$ROOT/scripts/start_admin.sh" >> "$LOG" 2>&1 || log "admin-web 拉起失败"
  fi
}

ensure_apify_pool() {
  local alive
  # 审定拓扑:interactive 1 + bulk 15 = 16 个进程。进程数不是付费并发数；
  # Provider 实际并发仍由 BURST_TIER 与 family budget/circuit breaker 约束。
  alive=$(pgrep -f "app.workers.apify_jobs_worker" | wc -l | tr -d ' ')
  if [[ "$alive" -lt 16 ]]; then
    log "apify 车道存活 $alive/16,补齐"
    # 清掉死 pidfile 防 pool 脚本误判已在跑
    for l in interactive bulk{1..15}; do
      local pf="$ROOT/runtime/worker-$l.pid"
      if [[ -f "$pf" ]] && ! kill -0 "$(cat "$pf" 2>/dev/null)" 2>/dev/null; then rm -f "$pf"; fi
    done
    APIFY_WORKER_POOL_BULK_COUNT=15 APIFY_WORKER_BURST_TIER=2 bash "$ROOT/scripts/start_apify_worker_pool.sh" >> "$LOG" 2>&1 \
      || log "apify pool 拉起失败(可能部分车道已在跑)"
  fi
}

ensure_scheduler() {
  if ! pgrep -f "scripts/scheduler_daemon.py" > /dev/null 2>&1; then
    log "scheduler_daemon 不在,拉起(白名单模式)"
    VKPI_SCHEDULER_TASK_ALLOWLIST="$SCHEDULER_ALLOWLIST" \
    VKPI_SCHEDULER_FIRE_RECOVERY_INTERVAL_SECONDS=900 \
    VKPI_EXTERNAL_SIGNAL_AUTOWRITE_ENABLED=1 \
      nohup bash "$ROOT/scripts/run_scheduler_daemon.sh" >> "$ROOT/runtime/logs/scheduler-daemon.log" 2>&1 &
  fi
}

redis_ready() (
  RUNTIME_ENV_QUIET=1
  source "$ROOT/scripts/runtime_env.sh" > /dev/null 2>&1 || return 1
  local redis_cli="$REDIS_BIN_DIR/redis-cli"
  [[ -x "$redis_cli" ]] || return 1
  [[ "$("$redis_cli" -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null)" == "PONG" ]]
)

ensure_redis() {
  if redis_ready; then
    REDIS_RETRY_DELAY_SECONDS="$REDIS_RETRY_BASE_SECONDS"
    REDIS_NEXT_RETRY_EPOCH=0
    REDIS_AUTORECOVER_NOTICE_EMITTED=0
    return 0
  fi

  # Starting the durable AOF-backed instance changes local runtime state.  The
  # supervisor may detect and report an outage by default, but an operator must
  # explicitly authorize automatic recovery after reviewing the state split.
  if [[ "$REDIS_AUTORECOVER_ENABLED" != "1" ]]; then
    if [[ "$REDIS_AUTORECOVER_NOTICE_EMITTED" != "1" ]]; then
      log "仓库 Redis 不可用;自动恢复未授权(VKPI_SUPERVISOR_REDIS_AUTORECOVER=1)"
      REDIS_AUTORECOVER_NOTICE_EMITTED=1
    fi
    return 1
  fi

  local now next_delay
  now="$(date +%s)"
  if [[ "$now" -lt "$REDIS_NEXT_RETRY_EPOCH" ]]; then
    return 1
  fi

  log "仓库 Redis 不可用,尝试按 runtime contract 恢复"
  if RUNTIME_ENV_QUIET=1 bash "$ROOT/scripts/start_redis_local.sh" >> "$LOG" 2>&1 \
    && redis_ready; then
    REDIS_RETRY_DELAY_SECONDS="$REDIS_RETRY_BASE_SECONDS"
    REDIS_NEXT_RETRY_EPOCH=0
    REDIS_AUTORECOVER_NOTICE_EMITTED=0
    log "仓库 Redis 已恢复"
    return 0
  fi

  REDIS_NEXT_RETRY_EPOCH=$((now + REDIS_RETRY_DELAY_SECONDS))
  next_delay=$((REDIS_RETRY_DELAY_SECONDS * 2))
  if [[ "$next_delay" -gt "$REDIS_RETRY_MAX_SECONDS" ]]; then
    next_delay="$REDIS_RETRY_MAX_SECONDS"
  fi
  log "仓库 Redis 恢复失败,${REDIS_RETRY_DELAY_SECONDS}s 后再试"
  REDIS_RETRY_DELAY_SECONDS="$next_delay"
  return 1
}

ensure_worker_main() {
  if ! redis_ready; then
    return 0
  fi
  if ! pgrep -f "app.workers.worker_main" > /dev/null 2>&1; then
    log "worker_main 不在,拉起(consumers=2)"
    (
      source "$ROOT/scripts/runtime_env.sh" > /dev/null 2>&1
      APP_ROLE=worker ENABLE_SCHEDULER=0 WORKER_ASYNC_CONSUMERS=2 PYTHONPATH="$ROOT/backend" \
        nohup "$ROOT/.venv/bin/python" -m app.workers.worker_main >> "$ROOT/runtime/logs/worker-main.log" 2>&1 &
    )
  fi
}

log "supervisor 上岗 self=${SELF_SHA:0:12}"
while true; do
  reload_if_self_changed "$@"
  ensure_admin_web
  ensure_apify_pool
  ensure_scheduler
  ensure_redis
  ensure_worker_main
  sleep 60
done
