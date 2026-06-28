#!/usr/bin/env bash
# verify.sh — 工程硬化②一键验证。
# 串起:后端 pytest(.venv) + 前端 tsc --noEmit + 前端 npm run build
#       + check_repo_hardening.py --strict + 红线 grep(viltrox_fit_score 写点只能在 pool.py)。
# 任一步失败 → 整体非 0 退出,并清楚打印是哪一步挂了。
# 直接在本仓根跑:bash scripts/verify.sh
set -u
set -o pipefail

# ---- 定位仓库根(本脚本在 <root>/scripts/ 下)----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VENV_PY="$ROOT/.venv/bin/python"

# ---- 记账:哪些步骤失败 ----
FAILED_STEPS=()
STEP_NO=0

run_step() {
  # run_step "步骤名" cmd args...
  local name="$1"; shift
  STEP_NO=$((STEP_NO + 1))
  echo ""
  echo "=========================================================="
  echo "[verify] STEP ${STEP_NO}: ${name}"
  echo "=========================================================="
  if "$@"; then
    echo "[verify] OK   <- ${name}"
  else
    local rc=$?
    echo "[verify] FAIL <- ${name} (exit=${rc})"
    FAILED_STEPS+=("${name}")
  fi
}

# ---- STEP 1: 后端 pytest(用 .venv 解释器 + PYTHONPATH=backend)----
backend_pytest() {
  if [ ! -x "$VENV_PY" ]; then
    echo "[verify] .venv 解释器缺失:$VENV_PY"
    return 1
  fi
  PYTHONPATH="$ROOT/backend" "$VENV_PY" -m pytest -q
}
run_step "backend pytest (.venv)" backend_pytest

# ---- STEP 2: 前端 tsc --noEmit ----
frontend_tsc() {
  if [ ! -d "$ROOT/frontend" ]; then
    echo "[verify] 缺 frontend/ 目录"
    return 1
  fi
  ( cd "$ROOT/frontend" && npx --no-install tsc --noEmit )
}
run_step "frontend tsc --noEmit" frontend_tsc

# ---- STEP 3: 前端 npm run build ----
frontend_build() {
  if [ ! -d "$ROOT/frontend" ]; then
    echo "[verify] 缺 frontend/ 目录"
    return 1
  fi
  ( cd "$ROOT/frontend" && npm run build )
}
run_step "frontend npm run build" frontend_build

# ---- STEP 4: 仓库硬化检查(strict 模式才在错误时非 0)----
repo_hardening() {
  if [ ! -x "$VENV_PY" ]; then
    echo "[verify] .venv 解释器缺失:$VENV_PY"
    return 1
  fi
  # check_repo_hardening.py 依赖同目录 stdout_utils.py,故 PYTHONPATH 带上 scripts/
  PYTHONPATH="$ROOT/scripts:$ROOT/backend" "$VENV_PY" "$ROOT/scripts/check_repo_hardening.py" --strict
}
run_step "check_repo_hardening.py --strict" repo_hardening

# ---- STEP 5: 红线 grep —— viltrox_fit_score 写点只能在 pool.py ----
# 合法写点:
#   1) backend/app/domains/kol/pool.py(rule_v0 enrich_item 在线写)
#   2) backend/scripts/backfill_fit_scores.py(2026-06-12 裁决①放行的离线 rule_v0 backfill)
# 只盯【DB 写入】语义:SQL 占位符写法 `viltrox_fit_score=?`(compat ? 占位符)、
#   `viltrox_fit_score=%`(% 字面写法)、以及 `SET viltrox_fit_score`(UPDATE)。
# 故意【不】匹配 Python 关键字参数读出(如 viltrox_fit_score=_as_float(...) 从 DB 读进对象),
# 也不匹配比较 == —— 那些都不是写点,匹配会造成假阳性。
redline_fit_score() {
  local offenders
  offenders="$(
    grep -rEn 'viltrox_fit_score[[:space:]]*=[[:space:]]*\?|viltrox_fit_score[[:space:]]*=[[:space:]]*%|SET[[:space:]]+viltrox_fit_score' \
      backend frontend \
      --include='*.py' --include='*.ts' --include='*.tsx' 2>/dev/null \
    | grep -v 'backend/app/domains/kol/pool.py' \
    | grep -v 'backend/scripts/backfill_fit_scores.py' \
    || true
  )"
  if [ -n "$offenders" ]; then
    echo "[verify] 红线违规:viltrox_fit_score 写点只允许在 pool.py(及已放行的 backfill_fit_scores.py):"
    echo "$offenders"
    return 1
  fi
  echo "[verify] 红线 OK:无非法 viltrox_fit_score 写点。"
  return 0
}
run_step "redline grep (viltrox_fit_score write)" redline_fit_score

# ---- 汇总 ----
echo ""
echo "=========================================================="
if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
  echo "[verify] ALL GREEN — 全部步骤通过。"
  echo "=========================================================="
  exit 0
else
  echo "[verify] 以下步骤失败(${#FAILED_STEPS[@]}):"
  for s in "${FAILED_STEPS[@]}"; do
    echo "  - ${s}"
  done
  echo "=========================================================="
  exit 1
fi
