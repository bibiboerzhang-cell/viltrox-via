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

# ---- STEP 3: 前端 npm run build + dist 分包护栏 ----
# F2 分包后教训:「grep 入口=假阴性」,验证必须扫全部 chunk。
# check_chunk_graph.py 对 dist/assets 做两道硬校验:
#   a) 任一 JS chunk > 600KB → fail(F2 分包目标线)
#   b) chunk 间静态 import 成环 → fail(R3 级运行时「页面加载失败」的前置条件)
frontend_build() {
  if [ ! -d "$ROOT/frontend" ]; then
    echo "[verify] 缺 frontend/ 目录"
    return 1
  fi
  ( cd "$ROOT/frontend" && npm run build ) || return 1
  python3 "$ROOT/scripts/check_chunk_graph.py" "$ROOT/frontend/dist/assets" 600
}
run_step "frontend npm run build + chunk graph guard" frontend_build

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
#   1) backend/app/domains/kol/pool.py + backend/app/domains/kol/pool_enrich.py
#      (rule_v0 enrich_item 在线写;god-object 拆分后 enrich_item 行为不变地搬到 pool_enrich.py)
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
    | grep -v 'backend/app/domains/kol/pool_enrich.py' \
    | grep -v 'backend/scripts/backfill_fit_scores.py' \
    || true
  )"
  if [ -n "$offenders" ]; then
    echo "[verify] 红线违规:viltrox_fit_score 写点只允许在 pool.py / pool_enrich.py(及已放行的 backfill_fit_scores.py):"
    echo "$offenders"
    return 1
  fi
  echo "[verify] 红线 OK:无非法 viltrox_fit_score 写点。"
  return 0
}
run_step "redline grep (viltrox_fit_score write)" redline_fit_score

# ---- STEP 5.5: 千行卫兵 —— 源码文件 >1000 行即 fail(F2 发布门)----
# 复用 scripts/check_line_guard.py 的扫描口径(backend/app frontend/src scripts tests,
# 含 .py/.ts/.tsx/.css,剔除 node_modules/dist/.venv 等)。
# 白名单 = 2026-07-07 立门时既有的 5 个巨文件(任务书预估 4 个,实测 5 个;
# 第 6 个 backend/app/main.py 已在 F2 路由注册收敛中降到 1000 行内,不再豁免)。
# 【还债计划】白名单只出不进,新文件零豁免;按体量从大到小逐个拆(与 god-object
# 瘦身同套路:AST 对照保行为不变),拆完一个就从名单删一行:
#   1576 backend/app/domains/kol/audience_stats.py          (受众链主模块,拆 stats/geo/rollup 三层)
#   1232 frontend/src/components/vkpi/pages/projects/styles/project-detail-drawer.css (按抽屉分区拆分文件)
#   1186 backend/app/domains/projects/observation_windows.py (开窗/匹配/复盘三段拆)
#   1115 frontend/src/components/vkpi/pages/myKol/MyKolPage.Sections.tsx (按 Section 组件拆)
#   1110 scripts/etl_excel_to_vkpi.py                        (历史一次性 ETL,择机归档或拆 reader/writer)
line_guard_1000() {
  if [ ! -x "$VENV_PY" ]; then
    echo "[verify] .venv 解释器缺失:$VENV_PY"
    return 1
  fi
  PYTHONPATH="$ROOT/scripts" "$VENV_PY" - "$ROOT" <<'PY'
import json
import subprocess
import sys

root = sys.argv[1]
allow = {
    "backend/app/domains/kol/audience_stats.py",
    "frontend/src/components/vkpi/pages/projects/styles/project-detail-drawer.css",
    "backend/app/domains/projects/observation_windows.py",
    "frontend/src/components/vkpi/pages/myKol/MyKolPage.Sections.tsx",
    "scripts/etl_excel_to_vkpi.py",
}
proc = subprocess.run(
    [sys.executable, f"{root}/scripts/check_line_guard.py", "--limit", "1000", "--json"],
    cwd=root,
    capture_output=True,
    text=True,
)
if proc.returncode != 0 or not proc.stdout.strip():
    print("[verify] check_line_guard.py 执行失败:")
    print(proc.stdout)
    print(proc.stderr)
    raise SystemExit(1)
payload = json.loads(proc.stdout)
violations = [v for v in payload.get("violations", []) if v["path"] not in allow]
exempted = [v for v in payload.get("violations", []) if v["path"] in allow]
for item in exempted:
    print(f"[verify] 千行卫兵(白名单豁免,待还债): {item['lines']:>5} {item['path']}")
if violations:
    print("[verify] 千行卫兵 FAIL:以下文件 >1000 行且不在既有债白名单(新文件零豁免):")
    for item in violations:
        print(f"[verify]   {item['lines']:>5} {item['path']}")
    raise SystemExit(1)
print(f"[verify] 千行卫兵 OK:无新增 >1000 行文件(白名单剩余 {len(exempted)} 个待还)。")
PY
}
run_step "line guard >1000 (whitelist=legacy 5)" line_guard_1000

# ---- STEP 6: 运行态 sha 对齐(8001 起着才校验;没起则跳过,静态 CI 不破)----
# 根治"HEAD 领先运行态 / client 停在旧 sha"反复:服务在跑就强校验
# /health 的 .trust.sha_aligned 必须为 true,否则整体非 0 退出。
# 8001 没响应 = 纯静态校验场景,跳过(返回 0),不算失败。
runtime_sha_aligned() {
  local url="${VKPI_HEALTH_URL:-http://localhost:8102/health}"
  local body
  body="$(curl -s --max-time 3 "$url" 2>/dev/null || true)"
  if [ -z "$body" ]; then
    echo "[verify] ${url} 未响应,跳过运行态对齐校验(静态校验不依赖运行服务)。"
    return 0
  fi
  local verdict
  verdict="$(printf '%s' "$body" | "$VENV_PY" -c '
import sys, json
try:
    t = (json.load(sys.stdin) or {}).get("trust") or {}
except Exception:
    print("ERR|?|?"); raise SystemExit
a = t.get("sha_aligned")
s = str(t.get("server_git_sha") or "?")[:8]
c = str(t.get("client_git_sha") or "?")[:8]
print(("OK" if a is True else "FAIL") + "|" + s + "|" + c)
' 2>/dev/null || echo "ERR|?|?")"
  local state="${verdict%%|*}"
  local shas="${verdict#*|}"
  if [ "$state" = "OK" ]; then
    echo "[verify] 运行态对齐 OK:server/client sha 一致 (${shas/|/ vs })。"
    return 0
  fi
  echo "[verify] 运行态 sha 未对齐(.trust.sha_aligned!=true)—— 需 build+重启对齐 HEAD。server vs client: ${shas/|/ vs }"
  return 1
}
run_step "runtime sha aligned (skip if 8001 down)" runtime_sha_aligned

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
