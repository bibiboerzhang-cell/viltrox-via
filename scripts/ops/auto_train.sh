#!/usr/bin/env bash
# 定时自动发车(launchd com.vkpi.auto-train,每晚 00:30 北京)。
#
# 四道守卫全过才发:①无班车/freeze/deploy 在飞 ②树净 ③HEAD ≠ 上次成功落地的 sha
# ④GitHub CI 对 HEAD 的最新 run 为 success(API 匿名可读;读不到=不发,宁缺毋滥)。
# 附加:本地栈 /health 200 且 sha==HEAD 则 SKIP_RESTART=1;HEAD 的候选包已在则 REUSE_CANDIDATE=1。
# 班车自身带排水等待(VKPI_TRAIN_DRAIN_WAIT_SECONDS)与失败自动回滚;本脚本只做「该不该发」。
#   scripts/ops/auto_train.sh [--dry-run]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
LOG="$ROOT/runtime/logs/auto-train.log"; mkdir -p "$(dirname "$LOG")"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
REPO_SLUG="${VKPI_GITHUB_REPO:-bibiboerzhang-cell/viltrox-via}"
BRANCH="$(git branch --show-current 2>/dev/null || git rev-parse --abbrev-ref HEAD)"
log() { printf '[auto-train %s] %s\n' "$(date '+%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
skip() { log "不发:$*"; exit 0; }

# ① 无班车在飞
pgrep -f "scripts/ops/train.sh|freeze_worktree_candidate.py|deploy_local_to_cloud.sh" >/dev/null && skip "班车/freeze/deploy 在飞"
# ② 树净
[ -z "$(git status --porcelain)" ] || skip "工作树不净($(git status --porcelain | wc -l | tr -d ' ') 处)"
HEAD_SHA="$(git rev-parse HEAD)"; SHORT9="${HEAD_SHA:0:9}"; SHA12="${HEAD_SHA:0:12}"
# ③ HEAD ≠ 上次成功落地
LAST_OK="$(for d in $(ls -d runtime/ops/post-deploy/*/ 2>/dev/null | sort); do
  f="$d/outcome.json"; [ -f "$f" ] || continue
  grep -q '"result": *"success"' "$f" && basename "$d" | sed -E 's/^[0-9TZ]+-//'
done | tail -n 1)"
[ -n "$LAST_OK" ] && [ "$LAST_OK" = "$SHA12" ] && skip "HEAD $SHORT9 已是上次成功落地的版本"
# ④ CI 绿(对 HEAD 的最新 run)
CI="$(curl -s -m 20 "https://api.github.com/repos/$REPO_SLUG/actions/runs?branch=$BRANCH&head_sha=$HEAD_SHA&per_page=1" \
  | "$ROOT/.venv/bin/python" -c 'import sys,json
try:
    runs=json.load(sys.stdin).get("workflow_runs",[])
    print((runs[0]["status"]+"/"+str(runs[0]["conclusion"])) if runs else "none")
except Exception: print("unreadable")' 2>/dev/null || echo unreadable)"
[ "$CI" = "completed/success" ] || skip "CI 对 HEAD $SHORT9 不是绿($CI)"
# 附加:本地栈是否已在 HEAD
SKIP_RESTART=0
if body="$("$ROOT/.venv/bin/python" -B scripts/ops/fetch_runtime_health.py --url http://127.0.0.1:8102/health --env-file runtime/ops/local-health.env --timeout-seconds 5 2>/dev/null)"; then
  srv="$(printf '%s' "$body" | "$ROOT/.venv/bin/python" -c 'import sys,json; d=json.load(sys.stdin); t=d.get("trust",{}); print(str(t.get("server_git_sha") or "")[:9], t.get("sha_aligned"))' 2>/dev/null || echo "")"
  case "$srv" in "$SHORT9 True") SKIP_RESTART=1;; esac
fi
REUSE=0; [ -f "runtime/ops/www-release-candidate-$SHORT9.manifest.json" ] && REUSE=1
log "守卫全过:HEAD=$SHORT9 上次落地=${LAST_OK:-无} CI=$CI 本地栈已对齐=$SKIP_RESTART 候选包可复用=$REUSE"
[ "$DRY" = "1" ] && { log "dry-run:将执行 VKPI_TRAIN_REUSE_CANDIDATE=$REUSE VKPI_TRAIN_SKIP_RESTART=$SKIP_RESTART bash scripts/ops/train.sh"; exit 0; }
log "发车 → runtime/ops/train-$SHORT9-*.log"
VKPI_TRAIN_REUSE_CANDIDATE="$REUSE" VKPI_TRAIN_SKIP_RESTART="$SKIP_RESTART" bash scripts/ops/train.sh >>"$LOG.train" 2>&1 && log "班车退出 rc=0" || log "班车退出 rc=$?(看 outcome.json / deploy 日志)"
