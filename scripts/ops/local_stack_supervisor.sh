#!/usr/bin/env bash
# 本地全栈监督器(launchd 常驻):每 60s 巡检五件套,缺谁补谁,daily 更新不断档。
# 由 ~/Library/LaunchAgents/com.vkpi.stack-supervisor.plist 以 KeepAlive 拉起;
# 各 start 脚本自带防重复守卫(pidfile/端口/refusing mixed topology),巡检幂等。
# 烧钱红线:scheduler 永远带任务白名单起(只放 daily 刷新类;kol_auto_poll、
# official_visual_scan 等自动烧钱方不放,点火须用户发令改本清单)。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/runtime/logs/supervisor.log"
mkdir -p "$ROOT/runtime/logs"

# 日志自限长:超 5MB 截断,防再造一个 452MB 事故
if [[ -f "$LOG" && $(stat -f%z "$LOG" 2>/dev/null || echo 0) -gt 5242880 ]]; then
  : > "$LOG"
fi

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# kol_auto_poll:每日轻量刷新入队(评论/受众原料),2026-07-16 用户令「保证每日抓取」点火;
# 费用受 provider:apify 月帽($40)+ 各 cron 帽约束。
SCHEDULER_ALLOWLIST="kol_auto_poll,vkpi_market_listening_daily,vkpi_ai_today_hot,vkpi_fit_snapshot,vkpi_comment_sentiment_refresh,daily_action_inbox_generate,fulfillment_due_scan,fulfillment_content_scan,vkpi_market_signal_refresh,vkpi_market_intelligence_refresh,vkpi_official_catalog_sync,vkpi_competitor_radar,vkpi_forecast_outcomes_refresh,vkpi_prediction_weekly_rollup,vkpi_official_daily_report_asia,vkpi_official_daily_report_americas,vkpi_market_mention_sentiment,scheduler_fire_stale_recovery"

ensure_admin_web() {
  if ! curl -sf -m 5 http://127.0.0.1:8102/health > /dev/null 2>&1; then
    log "admin-web 不健康,拉起"
    bash "$ROOT/scripts/start_admin.sh" >> "$LOG" 2>&1 || log "admin-web 拉起失败"
  fi
}

ensure_apify_pool() {
  local alive
  alive=$(pgrep -f "app.workers.apify_jobs_worker" | wc -l | tr -d ' ')
  if [[ "$alive" -lt 4 ]]; then
    log "apify 车道存活 $alive/4,补齐"
    # 清掉死 pidfile 防 pool 脚本误判已在跑
    for l in interactive bulk1 bulk2 bulk3; do
      local pf="$ROOT/runtime/worker-$l.pid"
      if [[ -f "$pf" ]] && ! kill -0 "$(cat "$pf" 2>/dev/null)" 2>/dev/null; then rm -f "$pf"; fi
    done
    APIFY_WORKER_POOL_BULK_COUNT=3 bash "$ROOT/scripts/start_apify_worker_pool.sh" >> "$LOG" 2>&1 \
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

ensure_worker_main() {
  if ! pgrep -f "app.workers.worker_main" > /dev/null 2>&1; then
    log "worker_main 不在,拉起(consumers=2)"
    (
      source "$ROOT/scripts/runtime_env.sh" > /dev/null 2>&1
      APP_ROLE=worker ENABLE_SCHEDULER=0 WORKER_ASYNC_CONSUMERS=2 PYTHONPATH="$ROOT/backend" \
        nohup "$ROOT/.venv/bin/python" -m app.workers.worker_main >> "$ROOT/runtime/logs/worker-main.log" 2>&1 &
    )
  fi
}

log "supervisor 上岗"
while true; do
  ensure_admin_web
  ensure_apify_pool
  ensure_scheduler
  ensure_worker_main
  sleep 60
done
