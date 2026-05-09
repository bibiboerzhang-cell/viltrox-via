#!/usr/bin/env bash
# R58E: smoke 统一入口 (FIXED v2 - 单跑,不双执行)
#
# 用法:
#   ./scripts/run_smoke.sh smoke_vkpi_audit_page.py
#   ./scripts/run_smoke.sh --all                       # 跑全量 32
#   ./scripts/run_smoke.sh --batch smoke1.py smoke2.py # 批量指定
#
# 自动:
#   ✓ source runtime_env.sh
#   ✓ export DATABASE_URL=$LOCAL_DATABASE_URL
#   ✓ PYTHONPATH=backend
#   ✓ POSTGRES_POOL 调小避免连接池超时
#   ✓ smoke 之间 sleep 1 秒
#   ✓ 用退出码判断 PASS/FAIL (不被 PythonFinalizationError 干扰)
#
# v2 修复: 每个 smoke 只执行一次 (v1 有双跑 bug,会污染数据)

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ─── Setup environment ──────────────────────
export RUNTIME_ENV_QUIET="${RUNTIME_ENV_QUIET:-0}"  # runtime_env.sh 输出环境信息
source "$ROOT/scripts/runtime_env.sh"

# 强制本地 stack (避免任何 .env 残留值)
export DATABASE_URL="$LOCAL_DATABASE_URL"
export DB_RUNTIME_BACKEND=postgres
export PYTHONPATH=backend

# 连接池调小,避免批量跑超时
export POSTGRES_POOL_MIN_SIZE="${POSTGRES_POOL_MIN_SIZE:-1}"
export POSTGRES_POOL_MAX_SIZE="${POSTGRES_POOL_MAX_SIZE:-4}"
export POSTGRES_POOL_TIMEOUT_SEC="${POSTGRES_POOL_TIMEOUT_SEC:-30}"

PY="${PY:-.venv/bin/python}"
SLEEP_BETWEEN="${SLEEP_BETWEEN:-1}"

# ─── Resolve target list ────────────────────
TARGETS=()

if [[ $# -eq 0 ]]; then
  echo "Usage:"
  echo "  $0 smoke_vkpi_audit_page.py"
  echo "  $0 --all"
  echo "  $0 --batch smoke1.py smoke2.py ..."
  exit 1
fi

if [[ "$1" == "--all" ]]; then
  for f in scripts/smoke_vkpi_*.py; do
    TARGETS+=("$f")
  done
elif [[ "$1" == "--batch" ]]; then
  shift
  for f in "$@"; do
    if [[ "$f" == scripts/* ]]; then
      TARGETS+=("$f")
    else
      TARGETS+=("scripts/$f")
    fi
  done
else
  for f in "$@"; do
    if [[ "$f" == scripts/* ]]; then
      TARGETS+=("$f")
    else
      TARGETS+=("scripts/$f")
    fi
  done
fi

# ─── Run smokes (FIXED: 单跑版本) ────────────
pass=0
fail=0
fails=()

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Running ${#TARGETS[@]} smoke(s)"
echo "═══════════════════════════════════════════════════════"

# 临时日志文件,避免命名冲突
LOG_DIR="${TMPDIR:-/tmp}/run_smoke_$$"
mkdir -p "$LOG_DIR"
cleanup_logs() {
  if [[ ${fail:-0} -eq 0 ]]; then
    rm -rf "$LOG_DIR"
  else
    echo "Logs preserved at: $LOG_DIR"
  fi
}
trap cleanup_logs EXIT

for f in "${TARGETS[@]}"; do
  name=$(basename "$f")
  log_file="$LOG_DIR/$name.log"
  
  if [[ ! -f "$f" ]]; then
    echo "❌ MISSING: $name"
    fail=$((fail+1))
    fails+=("$name (file missing)")
    continue
  fi
  
  # ✅ 关键修复: 只执行一次,捕获 stdout+stderr 到日志,
  # 通过 $? 拿退出码,不二次执行
  set +e
  "$PY" "$f" > "$log_file" 2>&1
  rc=$?
  set -e
  
  if [[ $rc -eq 0 ]]; then
    pass=$((pass+1))
    echo "✅ PASS: $name"
  else
    fail=$((fail+1))
    fails+=("$name (exit=$rc)")
    echo "❌ FAIL: $name (exit=$rc)"
    # 显示 traceback 关键行 (过滤 PythonFinalizationError 噪声)
    grep -v "PythonFinalizationError\|cannot join thread\|psycopg_pool/_acompat\|psycopg_pool/pool\.py" "$log_file" \
      | tail -10 \
      | sed 's/^/    /'
  fi
  
  if [[ "$SLEEP_BETWEEN" != "0" && ${#TARGETS[@]} -gt 1 ]]; then
    sleep "$SLEEP_BETWEEN"
  fi
done

# ─── Summary ────────────────────────────────
total=$((pass + fail))
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Summary: PASS=$pass / FAIL=$fail / TOTAL=$total"
echo "═══════════════════════════════════════════════════════"

if [[ $fail -gt 0 ]]; then
  echo ""
  echo "Failed smokes:"
  for f in "${fails[@]}"; do
    echo "  - $f"
  done
  echo ""
  echo "Logs at: $LOG_DIR (will be cleaned on exit)"
  echo "To preserve logs, set TMPDIR or copy from above before exit."
  exit 1
fi

exit 0
