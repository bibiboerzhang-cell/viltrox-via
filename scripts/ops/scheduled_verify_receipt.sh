#!/usr/bin/env bash
# 定时 verify 落回执(交付维 build_test_p95 的样本来源)。
#
# 交付采集器只读 runtime/ops/verify-receipts/ 里带 duration_seconds 的回执;
# 09-27 起的 30 天窗要 50 个样本,人肉记不住,交给 launchd(每日 08:00 / 20:00)。
# 纪律:班车/freeze/另一个 verify 在飞就让路(不抢资源、不干扰源树断言);
#       静态门模式,不请求运行态,结果只当样本不当发布验收。
#   scripts/ops/scheduled_verify_receipt.sh [--dry-run]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1
RECEIPTS_DIR="$ROOT/runtime/ops/verify-receipts"
LOG="$ROOT/runtime/logs/scheduled-verify.log"
mkdir -p "$RECEIPTS_DIR" "$(dirname "$LOG")"
log() { printf '[scheduled-verify %s] %s\n' "$(date '+%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

if pgrep -f "scripts/ops/train.sh|freeze_worktree_candidate.py|deploy_local_to_cloud.sh" >/dev/null; then
  log "班车/freeze 在飞,本次让路"; exit 0
fi
if pgrep -f "scripts/verify.sh" >/dev/null; then
  log "另一个 verify 在跑,本次让路"; exit 0
fi
# 后端 pytest 里有打本地栈/库的用例;栈没起或正在重启时跑出来的红是假阳性(09-02 实测:
# 撞上自愈实证那一分钟,5 条角色矩阵/顾问端点红)。不健康就让路,回执宁缺毋滥。
if ! curl -s -m 4 -o /dev/null -w '%{http_code}' http://127.0.0.1:8102/health 2>/dev/null | grep -q '^200$'; then
  log "本地栈 /health 非 200,本次让路"; exit 0
fi
SHA9="$(git rev-parse --short=9 HEAD)"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
OUT="$RECEIPTS_DIR/verify-${SHA9}-${STAMP}.json"
if [ "$DRY_RUN" = "1" ]; then
  log "dry-run:将写 $OUT(HEAD=$SHA9,树改动 $(git status --short | wc -l | tr -d ' ') 个)"; exit 0
fi
log "开始 HEAD=$SHA9 → $OUT"
START=$(date +%s)
if VKPI_VERIFY_JSON_OUT="$OUT" bash scripts/verify.sh >>"$LOG.run" 2>&1; then
  log "静态门绿,用时 $(( $(date +%s) - START ))s,回执 $(basename "$OUT")"
else
  log "静态门红(rc=$?),用时 $(( $(date +%s) - START ))s;回执仍落盘(带 passed=false)供采集"
fi
# 只留最近 90 份,防目录无限长
ls -t "$RECEIPTS_DIR"/verify-*.json 2>/dev/null | tail -n +91 | xargs -I{} rm -f -- {} 2>/dev/null || true
