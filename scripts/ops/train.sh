#!/usr/bin/env bash
# 班车脚本(O3):把「出海」的手工口诀收编成一条命令,每一步都是过去真踩过的坑。
#
#   scripts/ops/train.sh [SHA]          # SHA 缺省取 HEAD;给了就必须等于 HEAD(部署脚本只发 HEAD)
#
# 流程:脏树检查 → freeze(候选包 runtime/ops/www-release-candidate-<sha9>)→ 脏树复查
#       → dist 拷回(候选包 frontend/dist 盖回本地,免得 STEP13 runtime trust 抓到旧 client)
#       → xargs kill 本地栈(纯数字 pid 逐个点名,launchd supervisor 60s 巡检拉新)
#       → 等 supervisor 对齐(/health server sha + client sha + apify workers≥N + redis worker 单代)
#       → deploy_local_to_cloud.sh(日志 runtime/ops/deploy-<sha>.log)→ 打印结果。
#
# 可调 env:
#   VKPI_TRAIN_BROWSER_GATE_URL   默认 https://www.viltroxtest.com/(部署脚本审定值)
#   VKPI_TRAIN_HEALTH_ENV_FILE    默认 runtime/ops/local-health.env(本地 /health 私有令牌)
#   VKPI_TRAIN_MIN_WORKERS        默认 7(本地 apify 车道在线下限;供应商拓扑 16)
#   VKPI_TRAIN_WAIT_SECONDS       默认 900(等 supervisor 对齐上限)
#   VKPI_TRAIN_REUSE_CANDIDATE=1  候选包已存在且 manifest 校验通过时复用(浏览器闸误杀重试省一次 freeze)
#   VKPI_TRAIN_SKIP_RESTART=1     本地栈已经是 HEAD 时跳过 kill/等待(仍会核对 /health 对齐)
#   VKPI_DEPLOY_ALLOW_NON_ANCESTOR 透传给部署脚本的祖先硬检查覆盖口(默认关)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${ROOT}"

PYTHON_BIN="${ROOT}/.venv/bin/python"
HEALTH_URL="http://127.0.0.1:8102/health"
HEALTH_ENV_FILE="${VKPI_TRAIN_HEALTH_ENV_FILE:-${ROOT}/runtime/ops/local-health.env}"
BROWSER_GATE_URL="${VKPI_TRAIN_BROWSER_GATE_URL:-https://www.viltroxtest.com/}"
MIN_WORKERS="${VKPI_TRAIN_MIN_WORKERS:-7}"
WAIT_SECONDS="${VKPI_TRAIN_WAIT_SECONDS:-900}"
REUSE_CANDIDATE="${VKPI_TRAIN_REUSE_CANDIDATE:-0}"
SKIP_RESTART="${VKPI_TRAIN_SKIP_RESTART:-0}"
OPS_DIR="${ROOT}/runtime/ops"
mkdir -p "${OPS_DIR}"

TRAIN_STARTED_AT="$(date -u +%Y%m%dT%H%M%SZ)"
log() { printf '[train %s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { printf '[train] FATAL: %s\n' "$*" >&2; exit 1; }

[ -x "${PYTHON_BIN}" ] || die ".venv 解释器缺失:${PYTHON_BIN}(V-KPI 必须用 .venv)"
[ -f "${HEALTH_ENV_FILE}" ] || die "本地 /health 私有令牌文件缺失:${HEALTH_ENV_FILE}(VKPI_TRAIN_HEALTH_ENV_FILE)"
[[ "${MIN_WORKERS}" =~ ^[1-9][0-9]*$ ]] || die "VKPI_TRAIN_MIN_WORKERS 必须是正整数"
[[ "${WAIT_SECONDS}" =~ ^[1-9][0-9]*$ ]] || die "VKPI_TRAIN_WAIT_SECONDS 必须是正整数"

# ── 1. 脏树检查(多代理并行施工会撞脏树;部署脚本自己也拒脏树,这里是第一道)──
assert_clean_tree() {
  local phase="$1" dirty
  dirty="$(git status --porcelain=v1 --untracked-files=all)"
  if [ -n "${dirty}" ]; then
    printf '%s\n' "${dirty}" >&2
    die "工作树不干净(${phase})。先 commit/stash(配方:git stash → train → git stash pop),或等并行会话收工。"
  fi
  log "脏树检查通过(${phase})"
}
assert_clean_tree "出发前"

# ── 2. SHA:缺省 HEAD;显式给的必须等于 HEAD ──
HEAD_SHA="$(git rev-parse --verify HEAD)"
if [ "$#" -ge 1 ] && [ -n "${1}" ]; then
  REQUESTED_SHA="$(git rev-parse --verify --quiet "${1}^{commit}")" || die "SHA 无法解析:${1}"
  [ "${REQUESTED_SHA}" = "${HEAD_SHA}" ] || die "请求的 ${REQUESTED_SHA:0:12} ≠ HEAD ${HEAD_SHA:0:12};部署脚本只发 HEAD,先 checkout 再发车。"
fi
SHA="${HEAD_SHA}"
SHORT9="${SHA:0:9}"
BRANCH="$(git branch --show-current)"
[ -n "${BRANCH}" ] || BRANCH="$(git rev-parse --abbrev-ref HEAD)"
CANDIDATE_DIR="${OPS_DIR}/www-release-candidate-${SHORT9}"
CANDIDATE_MANIFEST="${CANDIDATE_DIR}.manifest.json"
TRAIN_LOG="${OPS_DIR}/train-${SHORT9}-${TRAIN_STARTED_AT}.log"
DEPLOY_LOG="${OPS_DIR}/deploy-${SHORT9}.log"
log "班车 sha=${SHA} branch=${BRANCH} candidate=${CANDIDATE_DIR}"
log "班车日志 ${TRAIN_LOG}"
exec > >(tee -a "${TRAIN_LOG}") 2>&1

# ── 3. freeze(部署脚本只验证不生成;候选名用 --short=9)──
candidate_manifest_matches_head() {
  "${PYTHON_BIN}" -B - "${CANDIDATE_MANIFEST}" "${SHA}" <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(1)
source = payload.get("source") if isinstance(payload, dict) else None
raise SystemExit(0 if isinstance(source, dict) and source.get("head") == sys.argv[2] else 1)
PY
}

if [ -e "${CANDIDATE_DIR}" ] || [ -f "${CANDIDATE_MANIFEST}" ]; then
  if [ "${REUSE_CANDIDATE}" = "1" ] \
    && candidate_manifest_matches_head \
    && PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -I -B "${ROOT}/scripts/ops/freeze_worktree_candidate.py" \
      verify-manifest --manifest "${CANDIDATE_MANIFEST}" --snapshot "${CANDIDATE_DIR}" >/dev/null; then
    log "复用已冻结候选包(manifest 校验通过,head 一致)"
  else
    die "候选包已存在:${CANDIDATE_DIR}。要复用请 VKPI_TRAIN_REUSE_CANDIDATE=1(manifest 须校验通过且 head=${SHORT9}),否则先移走。"
  fi
else
  log "freeze 开始(构建 dist + 静态门 + 归档;日志见候选包同名 .build.log/.verify.log)"
  PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -I -B "${ROOT}/scripts/ops/freeze_worktree_candidate.py" \
    freeze --repo "${ROOT}" --output "${CANDIDATE_DIR}" >"${CANDIDATE_DIR}.freeze.json" \
    || die "freeze 失败(看 ${CANDIDATE_DIR}.verify.log;常见:裸 pass/千行卫兵/裸 print 警告棘轮)"
  log "freeze 完成:$(wc -c <"${CANDIDATE_DIR}.freeze.json" | tr -d ' ') bytes manifest 摘要 → ${CANDIDATE_DIR}.freeze.json"
fi
[ -d "${CANDIDATE_DIR}/frontend/dist" ] || die "候选包缺 frontend/dist:${CANDIDATE_DIR}"
assert_clean_tree "freeze 后"

# ── 4. dist 拷回:本地栈要服务与候选包同一份前端,否则 runtime trust 抓到旧 client ──
rsync -a --delete "${CANDIDATE_DIR}/frontend/dist/" "${ROOT}/frontend/dist/"
log "候选包 frontend/dist 已拷回本地(runtime trust client sha 对齐用)"

# ── 5. xargs kill 本地栈:纯数字 pid 逐个点名(带噪声令牌整条 kill 会失效) ──
# 五件套:admin-web gunicorn / apify 车道 / scheduler_daemon / worker_main。
# launchd supervisor(local_stack_supervisor.sh)不杀,它 60s 内会把五件套按新代码拉起。
LOCAL_STACK_PATTERNS=(
  "gunicorn app.main:app"
  "app.workers.apify_jobs_worker"
  "scripts/scheduler_daemon.py"
  "app.workers.worker_main"
)
local_stack_pids() {
  local pattern
  for pattern in "${LOCAL_STACK_PATTERNS[@]}"; do
    pgrep -f -- "${pattern}" 2>/dev/null || true
  done | grep -E '^[0-9]+$' | grep -vx -- "$$" | sort -un || true
}
restart_local_stack() {
  local pids attempt
  pids="$(local_stack_pids)"
  if [ -z "${pids}" ]; then
    log "本地栈无进程在跑;等 supervisor 拉起"
    return 0
  fi
  log "kill 本地栈 TERM:$(printf '%s' "${pids}" | tr '\n' ' ')"
  printf '%s\n' "${pids}" | xargs -n1 kill -TERM 2>/dev/null || true
  for attempt in $(seq 1 30); do
    [ -z "$(local_stack_pids)" ] && break
    sleep 1
  done
  pids="$(local_stack_pids)"
  if [ -n "${pids}" ]; then
    log "仍存活,升级 KILL:$(printf '%s' "${pids}" | tr '\n' ' ')"
    printf '%s\n' "${pids}" | xargs -n1 kill -KILL 2>/dev/null || true
    sleep 2
  fi
  # 死 pidfile 会让 start 脚本误判「已在跑」,supervisor 自己也清,这里提前清干净。
  local pidfile
  for pidfile in "${ROOT}"/runtime/worker-*.pid "${ROOT}"/runtime/gunicorn.pid "${ROOT}"/runtime/worker.pid; do
    if [ -f "${pidfile}" ] && ! kill -0 "$(cat "${pidfile}" 2>/dev/null)" 2>/dev/null; then
      rm -f -- "${pidfile}"
    fi
  done
  log "本地栈已清空;launchd supervisor 巡检拉新中"
}

# ── 6. 等 supervisor 对齐:/health server sha + client sha + apify workers≥N + redis 单代 ──
health_alignment() {
  # 输出一行诊断;exit 0 = 全对齐。令牌只走 --env-file,不进 argv/日志。
  local body
  body="$("${PYTHON_BIN}" -B "${ROOT}/scripts/ops/fetch_runtime_health.py" \
    --url "${HEALTH_URL}" --env-file "${HEALTH_ENV_FILE}" --timeout-seconds 5 2>/dev/null)" || {
    echo "health=unreachable"
    return 1
  }
  # 正文经 env 传入(heredoc 占了 stdin);令牌不在正文里。
  VKPI_TRAIN_HEALTH_BODY="${body}" "${PYTHON_BIN}" -B - "${SHA}" "${MIN_WORKERS}" <<'PY'
import json
import os
import sys

expected, min_workers = sys.argv[1], int(sys.argv[2])
try:
    payload = json.loads(os.environ.get("VKPI_TRAIN_HEALTH_BODY") or "")
except json.JSONDecodeError:
    print("health=invalid_json")
    raise SystemExit(1)
build = payload.get("build") if isinstance(payload.get("build"), dict) else {}
trust = payload.get("trust") if isinstance(payload.get("trust"), dict) else {}
fleet = trust.get("worker_fleet") if isinstance(trust.get("worker_fleet"), dict) else {}
redis = trust.get("redis_worker_fleet") if isinstance(trust.get("redis_worker_fleet"), dict) else {}
server = str(build.get("git_sha") or "")
client = str(trust.get("client_git_sha") or "")
workers = fleet.get("online_count") if isinstance(fleet.get("online_count"), int) else 0
redis_count = redis.get("online_count") if isinstance(redis.get("online_count"), int) else 0
checks = {
    "status": payload.get("status") == "ok",
    "server": server == expected,
    "client": client == expected and build.get("client_matches_server") is True,
    "workers": workers >= min_workers and fleet.get("all_worker_sha_aligned") is True,
    "redis": redis_count == 1 and redis.get("all_worker_sha_aligned") is True and redis.get("all_redis_ready") is True,
}
print(
    f"server={server[:9] or '-'} client={client[:9] or '-'} workers={workers}/{min_workers}"
    f" redis={redis_count}/1 " + " ".join(f"{k}={'ok' if v else 'NO'}" for k, v in checks.items())
)
raise SystemExit(0 if all(checks.values()) else 1)
PY
}
wait_for_alignment() {
  local deadline=$(( $(date +%s) + WAIT_SECONDS )) line
  while :; do
    if line="$(health_alignment)"; then
      log "本地栈已对齐:${line}"
      return 0
    fi
    log "等待对齐:${line}"
    if [ "$(date +%s)" -ge "${deadline}" ]; then
      die "本地栈 ${WAIT_SECONDS}s 内未对齐(最后:${line})。查 runtime/logs/supervisor.log;worker_main 有时要手动按 ensure_worker_main 同款命令拉。"
    fi
    sleep 15
  done
}

if [ "${SKIP_RESTART}" = "1" ]; then
  log "VKPI_TRAIN_SKIP_RESTART=1:不杀本地栈,只核对对齐"
else
  restart_local_stack
fi
wait_for_alignment

# ── 7. deploy(祖先硬检查/远端互斥/quiesce/rsync/激活/验收/回滚都在部署脚本内)──
log "deploy 开始 → ${DEPLOY_LOG}"
set +e
env \
  VKPI_DEPLOY_CANDIDATE_DIR="${CANDIDATE_DIR}" \
  VKPI_DEPLOY_CANDIDATE_MANIFEST="${CANDIDATE_MANIFEST}" \
  VKPI_HEALTH_ENV_FILE="${HEALTH_ENV_FILE}" \
  VKPI_BROWSER_GATE_URL="${BROWSER_GATE_URL}" \
  VKPI_STAGING_DB_CLONE=0 \
  bash "${ROOT}/scripts/ops/deploy_local_to_cloud.sh" 2>&1 | tee "${DEPLOY_LOG}"
DEPLOY_RC="${PIPESTATUS[0]}"
set -e

# ── 8. 结果 ──
echo
echo "================ 班车结果 ================"
echo "  sha        ${SHA}"
echo "  branch     ${BRANCH}"
echo "  candidate  ${CANDIDATE_DIR}"
echo "  deploy log ${DEPLOY_LOG}"
echo "  train log  ${TRAIN_LOG}"
if [ "${DEPLOY_RC}" -eq 0 ]; then
  echo "  result     已上线(deploy rc=0)"
  latest_evidence="$(ls -1dt "${ROOT}"/runtime/ops/post-deploy/*/ 2>/dev/null | head -n 1 || true)"
  [ -n "${latest_evidence}" ] && echo "  evidence   ${latest_evidence}"
else
  echo "  result     失败(deploy rc=${DEPLOY_RC})"
  echo "  复盘口诀   浏览器闸时序误杀 → VKPI_TRAIN_REUSE_CANDIDATE=1 重跑只走 deploy 段;"
  echo "             祖先检查拒绝 → 先 merge 线上 sha 再发车(VKPI_DEPLOY_ALLOW_NON_ANCESTOR=1 仅限明知故犯);"
  echo "             dirty → 多半是并行会话落盘,git status 看谁。"
fi
echo "=========================================="
exit "${DEPLOY_RC}"
