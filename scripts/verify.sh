#!/usr/bin/env bash
# verify.sh — V-KPI 唯一 canonical repository/release gate。
# CI、Makefile、Runbook 与 deploy 只能调用本文件(或零逻辑薄包装),避免门禁分叉。
# 静态门覆盖 contracts/silent baseline/硬化 warning ratchet/alembic/compile/tests/build、
# chunk graph、fit-score 红线与千行卫兵；部署模式再强制 runtime trust。
# 任一步失败 → 整体非 0 退出,并清楚打印是哪一步挂了。
# 直接在本仓根跑:bash scripts/verify.sh
set -u
set -o pipefail

# A browser-gate bearer may arrive through the caller's environment.  Disable
# xtrace before touching it, capture it without export, and scrub both public
# token names before even the first command-substitution child is created.
case "$-" in *x*) set +x ;; esac
_VKPI_VERIFY_BROWSER_GATE_TOKEN="${VKPI_BROWSER_GATE_TOKEN:-}"
unset VKPI_BROWSER_GATE_TOKEN POST_DEPLOY_BROWSER_TOKEN
if ! export -n _VKPI_VERIFY_BROWSER_GATE_TOKEN 2>/dev/null; then
  echo "[verify] 无法隔离 browser gate token，拒绝继续。" >&2
  exit 1
fi

# ---- 定位仓库根(本脚本在 <root>/scripts/ 下)----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi
# Compatibility name for the already-reviewed runtime/line-guard helpers below.
VENV_PY="$PYTHON_BIN"

# ---- 回执计时:只记录本次 canonical 调用的起点与总耗时,不参与任何门禁判定 ----
VERIFY_STARTED_EPOCH="$(date +%s)"
VERIFY_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---- 记账:哪些步骤失败 ----
FAILED_STEPS=()
STEP_NAMES=()
STEP_RESULTS=()
STEP_EXIT_CODES=()
STEP_NO=0
RUNTIME_VERIFICATION_STATE="not_run"
ACCEPTANCE_VERIFICATION_STATE="not_run"
BROWSER_CONSOLE_VERIFICATION_STATE="not_requested"
RUNTIME_LOG_CANARY_STATE="not_requested"

truthy_env() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

release_head() {
  local value=""
  if [ -f "$ROOT/BUILD_GIT_SHA" ] && [ ! -L "$ROOT/BUILD_GIT_SHA" ]; then
    value="$(tr -d '[:space:]' < "$ROOT/BUILD_GIT_SHA")"
    case "$value" in
      *[!0-9a-fA-F]*|'') value="" ;;
    esac
    if [ "${#value}" -eq 40 ]; then
      printf '%s\n' "$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
      return 0
    fi
  fi
  git rev-parse HEAD
}

python_available() {
  if [[ "$PYTHON_BIN" == */* ]]; then
    [ -x "$PYTHON_BIN" ]
  else
    command -v "$PYTHON_BIN" >/dev/null 2>&1
  fi
}

run_step() {
  # run_step "步骤名" cmd args...
  local name="$1"; shift
  STEP_NO=$((STEP_NO + 1))
  echo ""
  echo "=========================================================="
  echo "[verify] STEP ${STEP_NO}: ${name}"
  echo "=========================================================="
  if "$@"; then
    STEP_NAMES+=("$name")
    STEP_RESULTS+=("passed")
    STEP_EXIT_CODES+=("0")
    echo "[verify] OK   <- ${name}"
  else
    local rc=$?
    STEP_NAMES+=("$name")
    STEP_RESULTS+=("failed")
    STEP_EXIT_CODES+=("$rc")
    echo "[verify] FAIL <- ${name} (exit=${rc})"
    append_failed_step_once "${name}"
  fi
}

append_failed_step_once() {
  local name="$1"
  local existing
  if [ "${#FAILED_STEPS[@]}" -gt 0 ]; then
    for existing in "${FAILED_STEPS[@]}"; do
      if [ "$existing" = "$name" ]; then
        return 0
      fi
    done
  fi
  FAILED_STEPS+=("$name")
}

# ---- Canonical static contract/governance checks ----
release_candidate_worktree() {
  if ! truthy_env "${VKPI_VERIFY_REQUIRE_CLEAN_WORKTREE:-0}"; then
    echo "[verify] clean-worktree enforcement is optional in development mode."
    return 0
  fi
  local status count
  status="$(git status --porcelain=v1 --untracked-files=all)" || return 1
  if [ -n "$status" ]; then
    count="$(printf '%s\n' "$status" | awk 'NF {count += 1} END {print count + 0}')"
    echo "[verify] release candidate is not immutable: ${count} dirty paths." >&2
    printf '%s\n' "$status" | head -20 | sed 's/^/[verify]   drift: /' >&2
    echo "[verify] strict deploy refuses tracked, staged, or untracked drift." >&2
    return 1
  fi
  echo "[verify] release candidate worktree is clean."
}
run_step "release candidate worktree (required for deploy)" release_candidate_worktree

frontend_contracts() {
  PYTHONPATH="$ROOT/scripts:$ROOT/backend" "$PYTHON_BIN" \
    "$ROOT/scripts/generate_frontend_contracts.py" --check
}
run_step "frontend contracts are checked in and current" frontend_contracts

frontend_i18n_contract() {
  if [ ! -d "$ROOT/frontend/node_modules/typescript" ]; then
    echo "[verify] frontend/node_modules/typescript 缺失；i18n 门禁不隐式安装依赖。"
    return 1
  fi
  ( cd "$ROOT/frontend" && npm run check:i18n )
}
run_step "frontend i18n dictionary + missing-English ratchet" frontend_i18n_contract

# ---- Production dependency security gate ----
# Audit only packages that ship in the frontend production artifact.  npm exits
# non-zero both for moderate-or-higher advisories and for registry/network
# failures; keep that exit code intact so an unavailable audit cannot become a
# green security receipt.  The canonical gate never mutates dependencies.
frontend_production_dependency_audit() {
  if ! command -v npm >/dev/null 2>&1; then
    echo "[verify] npm 缺失，无法审计前端生产依赖。" >&2
    return 1
  fi
  if [ ! -f "$ROOT/frontend/package-lock.json" ] \
    || [ -L "$ROOT/frontend/package-lock.json" ]; then
    echo "[verify] frontend/package-lock.json 缺失或为符号链接，拒绝不可复现的依赖审计。" >&2
    return 1
  fi
  if [ -n "${VKPI_TRUSTED_NPM_AUDIT_RECEIPT:-}" ]; then
    "$PYTHON_BIN" -I -B "$ROOT/scripts/verify_static_gate_helpers.py" \
      npm-audit-receipt "$VKPI_TRUSTED_NPM_AUDIT_RECEIPT" \
      "$ROOT/frontend/package-lock.json"
    return $?
  fi
  ( cd "$ROOT/frontend" && npm audit --omit=dev --audit-level=moderate )
}
run_step "frontend production dependency security audit (moderate+)" \
  frontend_production_dependency_audit

silent_exception_baseline() {
  PYTHONPATH="$ROOT/scripts:$ROOT/backend" "$PYTHON_BIN" \
    "$ROOT/scripts/check_silent_exception_baseline.py" \
    --baseline "$ROOT/scripts/silent_exception_baseline.json"
}
run_step "silent exception baseline" silent_exception_baseline

repo_hardening() {
  PYTHONPATH="$ROOT/scripts:$ROOT/backend" "$PYTHON_BIN" \
    "$ROOT/scripts/check_repo_hardening.py" \
    --strict \
    --warning-baseline "$ROOT/scripts/hardening_warning_baseline.json"
}
run_step "repo hardening + reviewed warning ratchet" repo_hardening

alembic_heads() {
  PYTHONPATH="$ROOT/backend" "$PYTHON_BIN" -m alembic -c "$ROOT/alembic.ini" heads >/dev/null
}
run_step "alembic heads" alembic_heads

python_compile() {
  "$PYTHON_BIN" "$ROOT/scripts/check_python_compile.py"
}
run_step "Python compile (in-memory; no bytecode writes)" python_compile

# ---- Backend test suite ----
backend_pytest() {
  if ! python_available; then
    echo "[verify] Python 解释器缺失:$PYTHON_BIN"
    return 1
  fi
  # 仓库根 + scripts + backend 三段:tests/test_apify_queue_capacity_model 等以
  # ``from scripts.ops import ...`` 从仓库根导入(scripts 无 __init__,命名空间包),
  # 只给 backend 会 ModuleNotFoundError;与手工口诀 PYTHONPATH=.:scripts:backend 对齐。
  PYTHONPATH="$ROOT:$ROOT/scripts:$ROOT/backend" "$PYTHON_BIN" -m pytest -q
}
run_step "backend pytest" backend_pytest

# ---- Frontend test suite ----
frontend_vitest() {
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    echo "[verify] frontend/node_modules 缺失；canonical gate 不隐式安装依赖。"
    return 1
  fi
  ( cd "$ROOT/frontend" && npm test )
}
run_step "frontend vitest" frontend_vitest

# ---- 前端 tsc --noEmit ----
frontend_tsc() {
  if [ ! -d "$ROOT/frontend" ]; then
    echo "[verify] 缺 frontend/ 目录"
    return 1
  fi
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    echo "[verify] frontend/node_modules 缺失；canonical gate 不隐式安装依赖。"
    return 1
  fi
  ( cd "$ROOT/frontend" && npx --no-install tsc --noEmit )
}
run_step "frontend tsc --noEmit" frontend_tsc

# ---- STEP 3: 前端 npm run build + dist 分包/首屏预算护栏 ----
# F2 分包后教训:「grep 入口=假阴性」,验证必须扫全部 chunk。
# check_chunk_graph.py 对 dist/assets 做两道硬校验:
#   a) 任一 JS chunk > 600KB → fail(F2 分包目标线)
#   b) chunk 间静态 import 成环 → fail(R3 级运行时「页面加载失败」的前置条件)
# check_frontend_bundle_budget.py 再按 index.html 的静态依赖闭包检查首屏 JS/CSS、
# 最大单包和可复现 gzip-9 预算；缺入口/缺资源/外链/路径逃逸均失败关闭。
frontend_build() {
  if [ ! -d "$ROOT/frontend" ]; then
    echo "[verify] 缺 frontend/ 目录"
    return 1
  fi
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    echo "[verify] frontend/node_modules 缺失；canonical gate 不隐式安装依赖。"
    return 1
  fi

  local out_dir="${VKPI_VERIFY_FRONTEND_OUT_DIR:-}"
  local owns_out_dir=0
  local rc=0
  if [ -z "$out_dir" ]; then
    out_dir="$(mktemp -d "${TMPDIR:-/tmp}/vkpi-release-gate-build.XXXXXX")" || return 1
    owns_out_dir=1
  fi
  # Build into an isolated directory so verification never overwrites the
  # checked-out frontend/dist. Deploy performs its intentional dist build only
  # after this entire canonical gate has passed.
  ( cd "$ROOT/frontend" && npm run build -- --outDir "$out_dir" ) || rc=$?
  if [ "$rc" -eq 0 ]; then
    "$PYTHON_BIN" "$ROOT/scripts/check_chunk_graph.py" "$out_dir/assets" 600 || rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    "$PYTHON_BIN" "$ROOT/scripts/check_frontend_bundle_budget.py" "$out_dir" || rc=$?
  fi
  if [ "$owns_out_dir" -eq 1 ]; then
    rm -rf -- "$out_dir"
  fi
  return "$rc"
}
run_step "frontend isolated production build + chunk graph/bundle budget guards" frontend_build

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
# 白名单 = 2026-07-07 立门时既有的 5 个巨文件,当前共 5 个,只出不进。
# 历史 >1000 行白名单已全部还清；新旧文件一律零豁免。
line_guard_1000() {
  if ! python_available; then
    echo "[verify] Python 解释器缺失:$PYTHON_BIN"
    return 1
  fi
  # Temp-file-free wrapper; delegates the scan to check_line_guard.py.
  PYTHONPATH="$ROOT/scripts" "$VENV_PY" -I -B \
    "$ROOT/scripts/verify_static_gate_helpers.py" line-guard "$ROOT"
}
run_step "line guard >1000 (zero allowlist)" line_guard_1000

# ---- STEP 6: 运行态 trust ----
# 默认静态模式不探测本机服务,避免“碰巧有一个旧/降级服务在线”让同一提交的
# 静态结果不确定；明确输出 STATIC GREEN,且不得当作发布验收。
# VKPI_VERIFY_REQUIRE_RUNTIME=1 时,服务不可达、SHA/migration 不一致、Worker
# 不在线或 heartbeat/scheduler 不可信都会失败；部署入口强制启用该严格模式。
runtime_sha_aligned() {
  local url="${VKPI_HEALTH_URL:-http://localhost:8102/health}"
  local body=""
  if ! truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME:-0}"; then
    RUNTIME_VERIFICATION_STATE="not_requested"
    echo "[verify] 静态门禁未请求运行态探测；本次结果不得称为运行态或发布验收。"
    return 0
  fi

  # Strict runtime evidence is private in production.  Keep the token out of
  # argv/stdout and delegate its protected-file/inherited-env handling to the
  # reviewed loopback-only probe.  The probe fails closed when neither source
  # is explicitly available.
  local health_fetch_args=(
    "$ROOT/scripts/ops/fetch_runtime_health.py"
    --url "$url"
    --timeout-seconds 3
  )
  if [ -n "${VKPI_HEALTH_ENV_FILE:-}" ]; then
    health_fetch_args+=(--env-file "$VKPI_HEALTH_ENV_FILE")
  fi
  if ! body="$("$PYTHON_BIN" "${health_fetch_args[@]}")"; then
    RUNTIME_VERIFICATION_STATE="failed"
    echo "[verify] ${url} 私有运行态读取失败，严格运行态门禁失败。" >&2
    return 1
  fi
  if [ -z "$body" ]; then
    RUNTIME_VERIFICATION_STATE="failed"
    echo "[verify] ${url} 未响应,严格运行态门禁失败。" >&2
    return 1
  fi
  local args=(
    "$ROOT/scripts/verify_runtime_health.py"
    --expected-head "$(release_head)"
  )
  local latest_migration
  latest_migration="$(find "$ROOT/migrations" -maxdepth 1 -type f -name '*.sql' ! -name '*_down.sql' -exec basename {} \; | LC_ALL=C sort | tail -n 1)"
  args+=(
    --expected-migration "$latest_migration"
    --require-worker
    --max-worker-age-seconds "${VKPI_VERIFY_MAX_WORKER_AGE_SECONDS:-180}"
  )
  if truthy_env "${VKPI_VERIFY_STRICT_POST_RESTART:-0}"; then
    if [ -z "${VKPI_WORKER_NOT_BEFORE:-}" ] \
      || { [ -z "${VKPI_EXPECTED_WORKER_COUNT:-}" ] \
        && [ -z "${VKPI_EXPECTED_WORKER_BOOT_NONCE_SHA256:-}" ]; }; then
      echo "[verify] strict post-restart requires not-before plus worker count or boot nonce." >&2
      RUNTIME_VERIFICATION_STATE="failed"
      return 1
    fi
    args+=(
      --worker-not-before "$VKPI_WORKER_NOT_BEFORE"
      --strict-deploy
    )
    if [ -n "${VKPI_EXPECTED_WORKER_COUNT:-}" ]; then
      args+=(--expected-worker-count "$VKPI_EXPECTED_WORKER_COUNT")
    else
      args+=(
        --expected-worker-boot-nonce-sha256 "$VKPI_EXPECTED_WORKER_BOOT_NONCE_SHA256"
      )
    fi
  fi
  if printf '%s' "$body" | "$VENV_PY" "${args[@]}"; then
    # Local pre-deploy verification may intentionally keep the Redis worker
    # offline while the 16 historical messages are adjudicated.  Cloud
    # post-restart verification must set VKPI_VERIFY_REQUIRE_REDIS_WORKER=1;
    # strict post-restart mode defaults it on.
    local require_redis_worker="${VKPI_VERIFY_REQUIRE_REDIS_WORKER:-${VKPI_VERIFY_STRICT_POST_RESTART:-0}}"
    if truthy_env "$require_redis_worker"; then
      local redis_args=(
        "$ROOT/scripts/verify_redis_worker_health.py"
        --expected-head "$(release_head)"
        --expected-count "${VKPI_REDIS_WORKER_EXPECTED_INSTANCES:-1}"
        --max-age-seconds "${VKPI_VERIFY_MAX_REDIS_WORKER_AGE_SECONDS:-180}"
      )
      if [ -n "${VKPI_WORKER_NOT_BEFORE:-}" ]; then
        redis_args+=(--worker-not-before "$VKPI_WORKER_NOT_BEFORE")
      fi
      if ! printf '%s' "$body" | "$VENV_PY" "${redis_args[@]}"; then
        RUNTIME_VERIFICATION_STATE="failed"
        echo "[verify] Redis Worker 独立身份/在线门禁失败。" >&2
        return 1
      fi
    fi
    RUNTIME_VERIFICATION_STATE="verified"
    echo "[verify] 运行态信任契约通过。"
    return 0
  fi
  RUNTIME_VERIFICATION_STATE="failed"
  echo "[verify] 运行态信任契约失败。" >&2
  return 1
}
if truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME:-0}"; then
  RUNTIME_STEP_NAME="runtime trust (required)"
else
  RUNTIME_STEP_NAME="runtime trust (not requested static-gate mode)"
fi
run_step "$RUNTIME_STEP_NAME" runtime_sha_aligned

# ---- 严格运行态模式:39 项只读真实接口验收 ----
# /health 只能证明进程身份与基础依赖可信，不能替代页面族背后的真实接口闭环。
# 部署入口已强制 VKPI_VERIFY_REQUIRE_RUNTIME=1，因此在任何 backup/rsync/restart
# 之前必须把 manifest 中全部 required GET 跑完；开发/静态 CI 明确跳过且不得称为发布验收。
local_release_acceptance_gate() {
  if ! truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME:-0}"; then
    ACCEPTANCE_VERIFICATION_STATE="not_requested"
    echo "[verify] 只读接口验收仅在严格运行态模式执行；本次静态结果不是发布验收。"
    return 0
  fi

  if [ "$RUNTIME_VERIFICATION_STATE" != "verified" ]; then
    ACCEPTANCE_VERIFICATION_STATE="blocked"
    echo "[verify] 运行态信任未通过，拒绝执行并拒绝认可接口发布验收。" >&2
    return 1
  fi

  local out_path="${VKPI_VERIFY_ACCEPTANCE_JSON_OUT:-}"
  local owns_out_path=0
  local rc=0
  if [ -z "$out_path" ]; then
    out_path="$(mktemp "${TMPDIR:-/tmp}/vkpi-local-release-acceptance.XXXXXX.json")" || {
      ACCEPTANCE_VERIFICATION_STATE="failed"
      return 1
    }
    owns_out_path=1
  fi

  "$PYTHON_BIN" "$ROOT/scripts/local_release_acceptance.py" \
    --base-url "${VKPI_LOCAL_BASE_URL:-http://127.0.0.1:8102}" \
    --json-out "$out_path" \
    >/dev/null || rc=$?

  if [ "$rc" -eq 0 ]; then
    "$PYTHON_BIN" - "$out_path" <<'PY' || rc=$?
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
overall = payload.get("overall", {})
required_passed = overall.get("required_passed")
required_total = overall.get("required_total")
if (
    overall.get("pass") is not True
    or not isinstance(required_total, int)
    or isinstance(required_total, bool)
    or required_total <= 0
    or required_passed != required_total
):
    raise SystemExit("acceptance receipt does not prove all required endpoints passed")
print(
    "[verify] read-only acceptance passed: "
    f"{required_passed}/{required_total} required; "
    f"p95={overall.get('latency_p95_ms', 0)}ms"
)
PY
  fi
  if [ "$owns_out_path" -eq 1 ]; then
    rm -f -- "$out_path"
  fi
  if [ "$rc" -eq 0 ]; then
    ACCEPTANCE_VERIFICATION_STATE="verified"
  else
    ACCEPTANCE_VERIFICATION_STATE="failed"
  fi
  return "$rc"
}
if truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME:-0}"; then
  ACCEPTANCE_STEP_NAME="local release acceptance (all required GETs)"
else
  ACCEPTANCE_STEP_NAME="local release acceptance (skipped in static-gate mode)"
fi
run_step "$ACCEPTANCE_STEP_NAME" local_release_acceptance_gate

# ---- 显式严格模式:owned/ephemeral/extension-free browser console ----
# 默认 verify/CI 绝不启动浏览器。只有调用者明确开启
# VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE=1，并同时完成严格 runtime + read-only
# acceptance、提供目标 URL/短期 token/可执行 Chrome 时，才执行 capture -> verifier。
# 任一输入、capture、report 或 live/release proof 缺失都失败；fixture 不可进入本路径。
browser_console_release_gate() {
  local token="${_VKPI_VERIFY_BROWSER_GATE_TOKEN:-}"
  unset _VKPI_VERIFY_BROWSER_GATE_TOKEN
  if ! export -n token 2>/dev/null; then
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    echo "[verify] 无法隔离 browser gate token，拒绝启动浏览器。" >&2
    return 1
  fi
  if ! truthy_env "${VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE:-0}"; then
    BROWSER_CONSOLE_VERIFICATION_STATE="not_requested"
    echo "[verify] browser console gate 未显式请求；本步骤未启动 Chrome，也不是 console 发布验收。"
    return 0
  fi

  if ! truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME:-0}"; then
    BROWSER_CONSOLE_VERIFICATION_STATE="blocked"
    echo "[verify] browser console gate 只允许在 VKPI_VERIFY_REQUIRE_RUNTIME=1 的严格模式执行。" >&2
    return 1
  fi
  if [ "$RUNTIME_VERIFICATION_STATE" != "verified" ]; then
    BROWSER_CONSOLE_VERIFICATION_STATE="blocked"
    echo "[verify] runtime trust 未验证，browser console gate fail closed；未启动 Chrome。" >&2
    return 1
  fi
  if [ "$ACCEPTANCE_VERIFICATION_STATE" != "verified" ]; then
    BROWSER_CONSOLE_VERIFICATION_STATE="blocked"
    echo "[verify] read-only acceptance 未验证，browser console gate fail closed；未启动 Chrome。" >&2
    return 1
  fi
  if [ ${#FAILED_STEPS[@]} -ne 0 ]; then
    BROWSER_CONSOLE_VERIFICATION_STATE="blocked"
    echo "[verify] canonical gate 已有失败步骤，browser console gate 不会启动 Chrome。" >&2
    return 1
  fi
  if ! python_available; then
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    echo "[verify] Python 解释器缺失，无法验证 browser console capture。" >&2
    return 1
  fi
  if ! command -v node >/dev/null 2>&1; then
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    echo "[verify] node 缺失，无法运行 browser console capture。" >&2
    return 1
  fi

  local target_url="${VKPI_BROWSER_GATE_URL:-}"
  local chrome_path="${VKPI_CHROME_PATH:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
  if [ -z "$target_url" ]; then
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    echo "[verify] VKPI_BROWSER_GATE_URL 缺失；拒绝猜测验收页面，未启动 Chrome。" >&2
    return 1
  fi
  if [ -z "$token" ]; then
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    echo "[verify] VKPI_BROWSER_GATE_TOKEN 缺失；无法证明私有页面认证，未启动 Chrome。" >&2
    return 1
  fi
  if [ ! -x "$chrome_path" ]; then
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    echo "[verify] Chrome 不可执行: $chrome_path；未启动浏览器。" >&2
    return 1
  fi
  if [ ! -f "$ROOT/scripts/capture_browser_console_cdp.mjs" ] \
    || [ ! -f "$ROOT/scripts/verify_browser_console_capture.py" ] \
    || [ ! -f "$ROOT/scripts/browser_console_release_identity.py" ]; then
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    echo "[verify] browser console capture/verifier 脚本缺失。" >&2
    return 1
  fi

  local evidence_dir="${VKPI_BROWSER_CONSOLE_EVIDENCE_DIR:-}"
  local owns_evidence_dir=0
  local rc=0
  if [ -z "$evidence_dir" ]; then
    evidence_dir="$(mktemp -d "${TMPDIR:-/tmp}/vkpi-browser-console-gate.XXXXXX")" || {
      BROWSER_CONSOLE_VERIFICATION_STATE="failed"
      return 1
    }
    owns_evidence_dir=1
  else
    mkdir -p -- "$evidence_dir" || {
      BROWSER_CONSOLE_VERIFICATION_STATE="failed"
      return 1
    }
    chmod 700 "$evidence_dir" || {
      BROWSER_CONSOLE_VERIFICATION_STATE="failed"
      return 1
    }
  fi

  local capture_path="$evidence_dir/capture.json"
  local report_path="$evidence_dir/gate-report.json"
  local expected_git_sha=""
  local expected_app_asset=""
  local expected_app_asset_sha256=""
  local browser_candidate_identity=""
  expected_git_sha="$(release_head)" || {
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    return 1
  }
  if ! browser_candidate_identity="$(
    PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -I -B - \
      "$ROOT/frontend/dist" <<'PY'
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat
import sys

dist = Path(sys.argv[1])
index = dist / "index.html"
try:
    info = index.lstat()
    source = index.read_text(encoding="utf-8")
except (OSError, UnicodeError) as exc:
    raise SystemExit("browser candidate index is unreadable") from exc
if not stat.S_ISREG(info.st_mode) or index.is_symlink():
    raise SystemExit("browser candidate index is unsafe")
assets = sorted(set(re.findall(r"app-[A-Za-z0-9_-]+\.js", source)))
if len(assets) != 1:
    raise SystemExit("browser candidate index must name exactly one app asset")
asset = dist / "assets" / assets[0]
try:
    asset_info = asset.lstat()
    payload = asset.read_bytes()
except OSError as exc:
    raise SystemExit("browser candidate app asset is unreadable") from exc
if (
    not stat.S_ISREG(asset_info.st_mode)
    or asset.is_symlink()
    or len(payload) <= 0
    or len(payload) > 50 * 1024 * 1024
):
    raise SystemExit("browser candidate app asset is unsafe")
print(assets[0], hashlib.sha256(payload).hexdigest())
PY
  )"; then
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    return 1
  fi
  read -r expected_app_asset expected_app_asset_sha256 \
    <<<"$browser_candidate_identity"
  browser_candidate_identity=""
  if ! [[ "$expected_git_sha" =~ ^[0-9a-f]{40}$ ]] \
    || ! [[ "$expected_app_asset" =~ ^app-[A-Za-z0-9_-]+\.js$ ]] \
    || ! [[ "$expected_app_asset_sha256" =~ ^[0-9a-f]{64}$ ]]; then
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
    echo "[verify] browser candidate identity 无效。" >&2
    return 1
  fi
  # Never let a stale receipt survive as the verdict for a failed fresh run.
  rm -f -- "$capture_path" "$report_path"

  VKPI_BROWSER_GATE_TOKEN="$token" node \
    "$ROOT/scripts/capture_browser_console_cdp.mjs" \
    --url "$target_url" \
    --output "$capture_path" \
    --settle-ms "${VKPI_BROWSER_GATE_SETTLE_MS:-5000}" \
    --chrome "$chrome_path" \
    >/dev/null || rc=$?
  token=""

  if [ "$rc" -eq 0 ] && [ ! -s "$capture_path" ]; then
    echo "[verify] browser capture 命令未产出非空证据。" >&2
    rc=1
  fi
  if [ "$rc" -eq 0 ]; then
    "$PYTHON_BIN" "$ROOT/scripts/verify_browser_console_capture.py" \
      --input "$capture_path" \
      --json-out "$report_path" \
      --expected-git-sha "$expected_git_sha" \
      --expected-app-asset "$expected_app_asset" \
      --expected-app-asset-sha256 "$expected_app_asset_sha256" \
      >/dev/null || rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    "$PYTHON_BIN" - "$report_path" <<'PY' || rc=$?
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.stat().st_size <= 0:
    raise SystemExit("browser console verifier did not write a report")
payload = json.loads(path.read_text(encoding="utf-8"))
overall = payload.get("overall") or {}
capture = payload.get("capture") or {}
claims = payload.get("claims") or {}
if payload.get("schema_version") != "vkpi-browser-console-gate/v1":
    raise SystemExit("unexpected browser console gate schema")
if overall.get("pass") is not True or overall.get("release_eligible") is not True:
    raise SystemExit("browser console gate report is not a release pass")
if capture.get("run_kind") != "live" or capture.get("required_live") is not True:
    raise SystemExit("browser console gate report is not live evidence")
if claims.get("live_extension_free_run_completed") is not True:
    raise SystemExit("browser console live extension-free proof is incomplete")
metrics = payload.get("metrics") or {}
print(
    "[verify] live extension-free browser console passed: "
    f"events={metrics.get('total_events', 0)} "
    f"blocking={metrics.get('blocking_events', 0)}"
)
PY
  fi

  if [ -f "$capture_path" ]; then chmod 600 "$capture_path" || rc=1; fi
  if [ -f "$report_path" ]; then chmod 600 "$report_path" || rc=1; fi
  if [ "$owns_evidence_dir" -eq 1 ]; then
    rm -rf -- "$evidence_dir"
  elif [ "$rc" -eq 0 ]; then
    echo "[verify] browser console evidence: $evidence_dir"
  else
    echo "[verify] failed browser console evidence retained: $evidence_dir" >&2
  fi

  if [ "$rc" -eq 0 ]; then
    BROWSER_CONSOLE_VERIFICATION_STATE="verified"
  else
    BROWSER_CONSOLE_VERIFICATION_STATE="failed"
  fi
  return "$rc"
}
if truthy_env "${VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE:-0}"; then
  BROWSER_CONSOLE_STEP_NAME="browser console live extension-free release gate (required)"
else
  BROWSER_CONSOLE_STEP_NAME="browser console live extension-free release gate (not requested)"
fi
run_step "$BROWSER_CONSOLE_STEP_NAME" browser_console_release_gate

# Explicit browser-console mode can never pass unless a fresh live receipt was
# both generated and independently re-read above.
if truthy_env "${VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE:-0}" \
  && [ "$BROWSER_CONSOLE_VERIFICATION_STATE" != "verified" ]; then
  append_failed_step_once "$BROWSER_CONSOLE_STEP_NAME"
fi

# ---- 显式 post-restart 日志 canary:浏览器/API 验收后只允许零新增敏感 URL/凭据 ----
# baseline 必须在受控重启后、发起 canary 前生成。scanner 从 baseline offset 起审计
# canary 产生的增长区间；历史旧命中不会洗绿/洗红当前结果，原始内容永不输出。
runtime_log_canary_gate() {
  if ! truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY:-0}"; then
    RUNTIME_LOG_CANARY_STATE="not_requested"
    echo "[verify] runtime log canary 未显式请求；本次结果不证明上线日志零新增泄露。"
    return 0
  fi
  if ! truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME:-0}" \
    || [ "$RUNTIME_VERIFICATION_STATE" != "verified" ] \
    || [ "$ACCEPTANCE_VERIFICATION_STATE" != "verified" ] \
    || [ "$BROWSER_CONSOLE_VERIFICATION_STATE" != "verified" ]; then
    RUNTIME_LOG_CANARY_STATE="blocked"
    echo "[verify] runtime log canary requires strict runtime, 39项验收和 live extension-free browser proof." >&2
    return 1
  fi
  if ! truthy_env "${VKPI_VERIFY_STRICT_POST_RESTART:-0}"; then
    RUNTIME_LOG_CANARY_STATE="blocked"
    echo "[verify] runtime log canary requires strict post-restart worker identity binding." >&2
    return 1
  fi
  local baseline="${VKPI_RUNTIME_LOG_BASELINE_STATE:-}"
  if [ -z "$baseline" ] || [ ! -f "$baseline" ]; then
    RUNTIME_LOG_CANARY_STATE="failed"
    echo "[verify] VKPI_RUNTIME_LOG_BASELINE_STATE 缺失或不是文件；拒绝猜测历史边界。" >&2
    return 1
  fi
  # Construct the expected log manifest from the same caller-owned fleet
  # counts already proved by the strict runtime gate.  The baseline/canary
  # producer cannot shrink this list.  A legacy singleton worker log remains
  # in scope whenever it is present at verification time, so an addition or
  # deletion between baseline and canary also invalidates the receipt.
  local expected_worker_count="${VKPI_EXPECTED_WORKER_COUNT:-1}"
  local expected_redis_worker_count="${VKPI_REDIS_WORKER_EXPECTED_INSTANCES:-1}"
  if [[ ! "$expected_worker_count" =~ ^[1-9][0-9]*$ ]] \
    || [[ ! "$expected_redis_worker_count" =~ ^[1-9][0-9]*$ ]]; then
    RUNTIME_LOG_CANARY_STATE="failed"
    echo "[verify] runtime log canary requires positive worker fleet counts." >&2
    return 1
  fi
  local expected_logs=(
    "runtime/logs/admin-8102-access.log"
    "runtime/logs/admin-8102-error.log"
  )
  local index
  if [ "$expected_worker_count" -eq 1 ]; then
    expected_logs+=("runtime/logs/worker.log")
  else
    expected_logs+=("runtime/logs/worker-interactive.log")
    for ((index=1; index<expected_worker_count; index++)); do
      expected_logs+=("runtime/logs/worker-bulk${index}.log")
    done
  fi
  for ((index=1; index<=expected_redis_worker_count; index++)); do
    expected_logs+=("runtime/logs/worker-${index}.log")
  done
  local legacy_worker_log_expected=0
  if [ "$expected_worker_count" -gt 1 ] \
    && { [ -e "$ROOT/runtime/logs/worker.log" ] || [ -L "$ROOT/runtime/logs/worker.log" ]; }; then
    expected_logs+=("runtime/logs/worker.log")
    legacy_worker_log_expected=1
  fi
  local log_label
  local verifier_log_args=()
  for log_label in "${expected_logs[@]}"; do
    if [ ! -f "$ROOT/$log_label" ] || [ ! -r "$ROOT/$log_label" ] || [ -L "$ROOT/$log_label" ]; then
      RUNTIME_LOG_CANARY_STATE="failed"
      echo "[verify] expected runtime log is missing, unreadable, or a symlink: $log_label" >&2
      return 1
    fi
    verifier_log_args+=(--expected-log "$log_label")
  done
  local out_path="${VKPI_RUNTIME_LOG_CANARY_JSON_OUT:-}"
  local owns_out_path=0
  local rc=0
  if [ -z "$out_path" ]; then
    out_path="$(mktemp "${TMPDIR:-/tmp}/vkpi-runtime-log-canary.XXXXXX.json")" || {
      RUNTIME_LOG_CANARY_STATE="failed"
      return 1
    }
    owns_out_path=1
  fi
  "$PYTHON_BIN" "$ROOT/scripts/ops/audit_runtime_media_log_leaks.py" \
    --root "$ROOT" \
    --baseline-state "$baseline" \
    --worker-boot-nonce-sha256 "${VKPI_EXPECTED_WORKER_BOOT_NONCE_SHA256:-}" \
    --worker-not-before "${VKPI_WORKER_NOT_BEFORE:-}" \
    --require-complete-baseline \
    --compact \
    --fail-on-new \
    "${expected_logs[@]}" \
    >"$out_path" || rc=$?
  if [ "$rc" -eq 0 ]; then
    "$PYTHON_BIN" "$ROOT/scripts/verify_runtime_log_canary.py" \
      --baseline-state "$baseline" \
      --canary-report "$out_path" \
      --expected-worker-boot-nonce-sha256 "${VKPI_EXPECTED_WORKER_BOOT_NONCE_SHA256:-}" \
      --worker-not-before "${VKPI_WORKER_NOT_BEFORE:-}" \
      --expected-worker-count "$expected_worker_count" \
      --expected-redis-worker-count "$expected_redis_worker_count" \
      "${verifier_log_args[@]}" || rc=$?
  fi
  # Close the narrow discovery/scan race: every required path must still be a
  # readable regular non-symlink, and the only optional path (legacy worker.log)
  # must have the same presence state used to construct the trusted manifest.
  if [ "$rc" -eq 0 ]; then
    for log_label in "${expected_logs[@]}"; do
      if [ ! -f "$ROOT/$log_label" ] || [ ! -r "$ROOT/$log_label" ] || [ -L "$ROOT/$log_label" ]; then
        echo "[verify] expected runtime log changed during canary: $log_label" >&2
        rc=1
        break
      fi
    done
  fi
  if [ "$rc" -eq 0 ] && [ "$expected_worker_count" -gt 1 ]; then
    local legacy_worker_log_now=0
    if [ -e "$ROOT/runtime/logs/worker.log" ] || [ -L "$ROOT/runtime/logs/worker.log" ]; then
      legacy_worker_log_now=1
    fi
    if [ "$legacy_worker_log_now" -ne "$legacy_worker_log_expected" ]; then
      echo "[verify] legacy worker log presence changed during canary." >&2
      rc=1
    fi
  fi
  if [ -f "$out_path" ]; then chmod 600 "$out_path" || rc=1; fi
  if [ "$owns_out_path" -eq 1 ]; then
    rm -f -- "$out_path"
  elif [ "$rc" -eq 0 ]; then
    echo "[verify] runtime log canary evidence: $out_path"
  fi
  if [ "$rc" -eq 0 ]; then
    RUNTIME_LOG_CANARY_STATE="verified"
  else
    RUNTIME_LOG_CANARY_STATE="failed"
  fi
  return "$rc"
}
if truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY:-0}"; then
  RUNTIME_LOG_CANARY_STEP_NAME="post-restart runtime log leak canary (required)"
else
  RUNTIME_LOG_CANARY_STEP_NAME="post-restart runtime log leak canary (not requested)"
fi
run_step "$RUNTIME_LOG_CANARY_STEP_NAME" runtime_log_canary_gate

if truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY:-0}" \
  && [ "$RUNTIME_LOG_CANARY_STATE" != "verified" ]; then
  append_failed_step_once "$RUNTIME_LOG_CANARY_STEP_NAME"
fi

# Optional machine-readable receipt for the exact canonical invocation.  The
# gate remains the single source of truth: the receipt is written only after
# every step has run, and a requested receipt that cannot be written fails the
# gate rather than leaving a stale green artifact behind.
write_verify_receipt() {
  local out_path="${VKPI_VERIFY_JSON_OUT:-}"
  if [ -z "$out_path" ]; then
    return 0
  fi
  local final_pass="1"
  if [ "${#FAILED_STEPS[@]}" -ne 0 ]; then
    final_pass="0"
  fi
  local parent
  parent="$(dirname "$out_path")"
  mkdir -p -- "$parent" || return 1
  if [ -L "$out_path" ]; then
    echo "[verify] refusing symlink canonical receipt path: $out_path" >&2
    return 1
  fi
  rm -f -- "$out_path" || return 1
  local -a step_args=()
  local index
  for ((index = 0; index < ${#STEP_NAMES[@]}; index++)); do
    step_args+=("${STEP_NAMES[$index]}" "${STEP_RESULTS[$index]}" "${STEP_EXIT_CODES[$index]}")
  done
  local duration_seconds=$(( $(date +%s) - VERIFY_STARTED_EPOCH ))
  "$PYTHON_BIN" - \
    "$out_path" \
    "$final_pass" \
    "$(release_head)" \
    "$(git rev-parse HEAD)" \
    "$(git branch --show-current)" \
    "$RUNTIME_VERIFICATION_STATE" \
    "$ACCEPTANCE_VERIFICATION_STATE" \
    "$BROWSER_CONSOLE_VERIFICATION_STATE" \
    "$RUNTIME_LOG_CANARY_STATE" \
    "$VERIFY_STARTED_AT" \
    "$duration_seconds" \
    "${step_args[@]}" <<'PY'
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from datetime import UTC, datetime

(
    out_path,
    final_pass,
    release_head,
    git_head,
    branch,
    runtime_state,
    acceptance_state,
    browser_state,
    log_state,
    started_at,
    duration_seconds,
    *step_args,
) = sys.argv[1:]
if len(step_args) % 3:
    raise SystemExit("canonical step receipt arguments are incomplete")
status = subprocess.check_output(
    [
        "git",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--no-renames",
    ],
    stderr=subprocess.PIPE,
).decode("utf-8", "strict").strip()
sys.path.insert(0, str(pathlib.Path.cwd() / "scripts"))
import vkpi_engineering_health_collect as health_collect

source_snapshot = health_collect._take_source_snapshot(pathlib.Path.cwd())
if not source_snapshot.complete:
    raise SystemExit("canonical receipt source snapshot is incomplete")
steps = []
for offset in range(0, len(step_args), 3):
    name, result, exit_code = step_args[offset : offset + 3]
    steps.append(
        {
            "index": len(steps) + 1,
            "name": name,
            "status": result,
            "exit_code": int(exit_code),
        }
    )
payload = {
    "schema_version": "vkpi_canonical_gate_receipt_v1",
    "generated_at": datetime.now(UTC).isoformat(),
    "started_at": started_at,
    "duration_seconds": int(duration_seconds),
    "passed": final_pass == "1",
    "candidate": {
        "release_head": release_head,
        "git_head": git_head,
        "branch": branch,
        "clean_worktree": not bool(status),
        "dirty_path_count": len([line for line in status.splitlines() if line]),
        "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "source_content_sha256": source_snapshot.content_sha256,
        "source_file_count": len(source_snapshot.files),
    },
    "verification": {
        "runtime": runtime_state,
        "acceptance": acceptance_state,
        "browser_console": browser_state,
        "runtime_log_canary": log_state,
    },
    "steps": steps,
    "failed_steps": [item["name"] for item in steps if item["status"] == "failed"],
    "strict_runtime_binding": {
        "nonce": os.environ.get("VKPI_STRICT_RUN_NONCE", ""),
        "ports": os.environ.get("VKPI_STRICT_RUNTIME_PORTS", ""),
        "candidate_sha256": os.environ.get("VKPI_STRICT_CANDIDATE_SHA256", ""),
    },
}
target = pathlib.Path(out_path)
temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(target)
PY
}

if ! write_verify_receipt; then
  echo "[verify] requested canonical gate receipt could not be written." >&2
  append_failed_step_once "canonical gate receipt"
fi

# ---- 汇总 ----
echo ""
echo "=========================================================="
if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
  if ! truthy_env "${VKPI_VERIFY_REQUIRE_RUNTIME:-0}"; then
    echo "[verify] STATIC GREEN — 代码门禁通过,运行态未验证；不是发布验收。"
  elif [ "$BROWSER_CONSOLE_VERIFICATION_STATE" = "verified" ] \
    && [ "$RUNTIME_LOG_CANARY_STATE" = "verified" ]; then
    echo "[verify] ALL GREEN — strict runtime、只读接口、live extension-free browser 与日志 canary 全部验证。"
  elif [ "$BROWSER_CONSOLE_VERIFICATION_STATE" = "verified" ]; then
    echo "[verify] BROWSER GREEN — runtime、只读接口和浏览器已验证；日志 canary 未执行，不是完整上线验收。"
  else
    echo "[verify] RUNTIME GREEN — strict runtime 与只读接口已验证；browser console 未执行，不是完整浏览器发布验收。"
  fi
  echo "=========================================================="
  exit 0
else
  if [ "$RUNTIME_VERIFICATION_STATE" = "skipped" ]; then
    echo "[verify] NOTE — 运行态未验证；本次失败结果也不得称为发布验收。"
  fi
  echo "[verify] 以下步骤失败(${#FAILED_STEPS[@]}):"
  for s in "${FAILED_STEPS[@]}"; do
    echo "  - ${s}"
  done
  echo "=========================================================="
  exit 1
fi
