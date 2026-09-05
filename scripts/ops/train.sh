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
#   VKPI_TRAIN_DRAIN_WAIT_SECONDS 默认 5400:deploy 前先探 prod 排水(与 deploy 同一探针/同口径),
#                                  非空则每 VKPI_TRAIN_DRAIN_PROBE_INTERVAL_SECONDS(默认 120)再探,
#                                  超时才放弃;=0 关闭等待(回到「撞一下就退」)。探针自身出错立即停。
#   VKPI_TRAIN_SSH_TARGET / VKPI_TRAIN_REMOTE_ROOT / VKPI_TRAIN_REMOTE_APP_USER / _GROUP
#                                  排水探针的远端参数,默认 viltrox / /opt/viltrox-2.0 / viltrox / viltrox
#   VKPI_DEPLOY_ALLOW_NON_ANCESTOR 透传给部署脚本的祖先硬检查覆盖口(默认关)
#   VKPI_TRAIN_HOTFIX_OF          本次发车是为哪次部署打 hotfix(12-40 位 hex sha;
#                                  取前 12 位记入 outcome.json,交付采集器 CFR 判定用)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${ROOT}"

PHYSICAL_PYTHON_BIN="${ROOT}/.venv/bin/python"
PYTHON_BIN="${ROOT}/scripts/ops/safe_python.sh"
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

[ -z "${VKPI_SAFE_PYTHON_PROFILE:-}" ] \
  || die "GitHub static Python profile is forbidden for release trains"

[ -x "${PHYSICAL_PYTHON_BIN}" ] || die ".venv 解释器缺失:${PHYSICAL_PYTHON_BIN}(V-KPI 必须用 .venv)"
[ -x "${PYTHON_BIN}" ] || die "safe Python 包装器缺失:${PYTHON_BIN}"
export VKPI_SAFE_PYTHON_REAL="${PHYSICAL_PYTHON_BIN}"
[ -f "${HEALTH_ENV_FILE}" ] || die "本地 /health 私有令牌文件缺失:${HEALTH_ENV_FILE}(VKPI_TRAIN_HEALTH_ENV_FILE)"
[[ "${MIN_WORKERS}" =~ ^[1-9][0-9]*$ ]] || die "VKPI_TRAIN_MIN_WORKERS 必须是正整数"
[[ "${WAIT_SECONDS}" =~ ^[1-9][0-9]*$ ]] || die "VKPI_TRAIN_WAIT_SECONDS 必须是正整数"
HOTFIX_OF="${VKPI_TRAIN_HOTFIX_OF:-}"
if [ -n "${HOTFIX_OF}" ]; then
  [[ "${HOTFIX_OF}" =~ ^[0-9a-fA-F]{12,40}$ ]] || die "VKPI_TRAIN_HOTFIX_OF 必须是 12-40 位 hex sha(被 hotfix 的那次部署)"
  HOTFIX_OF="$(printf '%.12s' "${HOTFIX_OF}" | tr '[:upper:]' '[:lower:]')"
fi

# ── 1. 脏树检查(多代理并行施工会撞脏树;部署脚本自己也拒脏树,这里是第一道)──
assert_clean_tree() {
  local phase="$1" dirty
  dirty="$(git status --porcelain=v1 --untracked-files=all)"
  if [ -n "${dirty}" ]; then
    printf '%s\n' "${dirty}" >&2
    # 共享工作树禁用 git stash:它是全树操作,会连并行会话未提交的活一起卷走(且 pop 可能撞冲突)。
    # 唯一安全解:要么把上面这些文件按文件原子提交,要么等写它们的那个会话收工。
    die "工作树不干净(${phase})。把上面的文件逐个 commit,或等并行会话/工作流收工再发车——不要 git stash(共享树全树操作会卷走别人的活)。"
  fi
  log "脏树检查通过(${phase})"
}
assert_clean_tree "出发前"

# ── 1b. 迁移预检:早说,别等打包完二十几分钟才被部署脚本拦(2026-08-26 白跑一轮的教训)──
# 口径与 deploy_local_to_cloud.sh 完全一致:待应用 = 运行时迁移清单减去线上完整已应用集合。
# 历史基线替代项由 runtime_migration_manifest 排除；最高水位仅用于展示，不能掩盖中间缺项。
# train 当前固定走 in-place；待应用迁移必须与声明完全一致且通过代码内审阅策略。
# 这里先拦，避免 freeze/本地重启完成后才在 deploy 阶段发现迁移不可发布。
migration_preflight() {
  local applied remote pending declaration
  applied="$(ssh -o BatchMode=yes -o ConnectTimeout=8 viltrox \
    'set -a \
     && . /opt/viltrox-2.0/.env >/dev/null 2>&1 \
     && set +a \
     && [ -n "${DATABASE_URL:-}" ] \
     && cd /tmp \
     && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -At -c "SELECT array_to_string(array_agg(version_key ORDER BY version_key), chr(44)) FROM schema_migrations"' \
    2>/dev/null)" \
    || die "迁移预检:无法连接线上或读取 schema_migrations，拒绝在未知水位下发车"
  [ -n "${applied}" ] \
    || die "迁移预检:线上 schema_migrations 未返回完整版本集合，拒绝在未知水位下发车"
  remote="${applied##*,}"
  pending="$("${PYTHON_BIN}" -B - "${ROOT}" "${applied}" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts" / "ops"))
from atomic_release_shared import pending_runtime_migrations, runtime_migration_manifest

manifest = runtime_migration_manifest(root / "migrations")
applied = tuple(value for value in sys.argv[2].split(",") if value)
print(",".join(pending_runtime_migrations(manifest, applied)))
PY
  )" || die "迁移预检:本地 Python 执行失败或线上完整版本集合与本地运行时清单不一致，拒绝猜测待应用范围（见上方诊断）"
  if [ -z "${pending}" ]; then
    [ -z "${VKPI_FORWARD_COMPATIBLE_MIGRATIONS:-}" ] \
      || die "迁移预检:线上无待应用迁移，但 VKPI_FORWARD_COMPATIBLE_MIGRATIONS 仍有旧声明"
    log "迁移预检:线上水位 ${remote},无待应用迁移"
    return 0
  fi
  log "迁移预检:线上水位 ${remote},待应用 → ${pending}"
  declaration="${VKPI_FORWARD_COMPATIBLE_MIGRATIONS:-}"
  [ -n "${declaration}" ] \
    || die "迁移预检:待应用迁移未声明；必须先逐条进入 forward-compatibility policy"
  [ "${declaration}" = "${pending}" ] \
    || die "迁移预检:声明必须与待应用迁移精确一致；expected=${pending}"
  "${PYTHON_BIN}" -B - "${ROOT}" "${declaration}" <<'PY' \
    || die "迁移预检:声明包含未经审阅或非前向兼容迁移"
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts" / "ops"))
from atomic_release_shared import _forward_compatibility_evidence

_forward_compatibility_evidence(sys.argv[2], migrations_dir=root / "migrations")
PY
  log "迁移预检:精确声明已通过 ${pending}"
}
migration_preflight

# Anthropic Batch transport 在候选代码中硬关闭；若线上仍有旧批次，停 poll 会丢结果。
# 必须实时证明 open=0，查询失败同样拒绝发车。
llm_batch_shutdown_preflight() {
  local active
  active="$(ssh -o BatchMode=yes -o ConnectTimeout=8 viltrox \
    'set -a \
     && . /opt/viltrox-2.0/.env >/dev/null 2>&1 \
     && set +a \
     && [ -n "${DATABASE_URL:-}" ] \
     && cd /tmp \
     && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -At -c "SELECT count(*) FROM vkpi_llm_batches WHERE status IN ('"'"'submitting'"'"','"'"'provider_unknown'"'"','"'"'in_progress'"'"','"'"'expired'"'"')"' \
    2>/dev/null)" \
    || die "Anthropic Batch 下线预检:无法读取线上活动批次，拒绝停掉未知工作"
  case "${active}" in
    0) log "Anthropic Batch 下线预检:线上活动批次为 0" ;;
    ''|*[!0-9]*) die "Anthropic Batch 下线预检:活动批次数格式非法" ;;
    *) die "Anthropic Batch 下线预检:仍有 ${active} 个未对账批次(含 expired)，必须先人工回收" ;;
  esac
}
llm_batch_shutdown_preflight

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
# ── 6a. 排水等待:deploy 的 live drain 无时窗无等待,非空原地退出(09-02 实测 13 次发车里
#   12 次死在这里)。这里用同一个探针、同一口径先等到空,再把车交给 deploy。
DRAIN_WAIT_SECONDS="${VKPI_TRAIN_DRAIN_WAIT_SECONDS:-5400}"
DRAIN_PROBE_INTERVAL="${VKPI_TRAIN_DRAIN_PROBE_INTERVAL_SECONDS:-120}"
DRAIN_SSH_TARGET="${VKPI_TRAIN_SSH_TARGET:-viltrox}"
DRAIN_REMOTE_ROOT="${VKPI_TRAIN_REMOTE_ROOT:-/opt/viltrox-2.0}"
DRAIN_APP_USER="${VKPI_TRAIN_REMOTE_APP_USER:-viltrox}"
DRAIN_APP_GROUP="${VKPI_TRAIN_REMOTE_APP_GROUP:-viltrox}"
[[ "${DRAIN_WAIT_SECONDS}" =~ ^[0-9]+$ ]] || die "VKPI_TRAIN_DRAIN_WAIT_SECONDS 必须是非负整数"
[[ "${DRAIN_PROBE_INTERVAL}" =~ ^[1-9][0-9]*$ ]] || die "VKPI_TRAIN_DRAIN_PROBE_INTERVAL_SECONDS 必须是正整数"
remote_database_name() {  # 只回传库名,DATABASE_URL 不离开远端 shell
  ssh "${DRAIN_SSH_TARGET}" "sudo -n -u '${DRAIN_APP_USER}' -g '${DRAIN_APP_GROUP}' sh -c 'sed -nE \"s#^DATABASE_URL=.*/([A-Za-z0-9_]+)(\\?.*)?\\\$#\\1#p\" ${DRAIN_REMOTE_ROOT}/.env | head -1'"
}
probe_release_drain() {  # 0=空 3=非空 2=探针错;stdout=阻塞原因(非空时)
  local db mig out rc=0
  db="$(remote_database_name)" || return 2
  [ -n "${db}" ] || return 2
  mig="$(find "${ROOT}/migrations" -maxdepth 1 -type f -name '*.sql' ! -name '*_down.sql' -exec basename {} \; | LC_ALL=C sort | tail -n 1)"
  [ -n "${mig}" ] || return 2
  out="$(ssh "${DRAIN_SSH_TARGET}" "sudo -n -u '${DRAIN_APP_USER}' -g '${DRAIN_APP_GROUP}' env -i HOME=/tmp XDG_CACHE_HOME=/tmp TMPDIR=/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${DRAIN_REMOTE_ROOT}/.venv/bin/python' -B - --env-file '${DRAIN_REMOTE_ROOT}/.env' --expected-database '${db}' --current-migration '${mig}'" <"${ROOT}/scripts/ops/verify_release_drain.py" 2>/dev/null)" || rc=$?
  if [ "${rc}" -eq 3 ]; then
    printf '%s' "${out}" | "${PYTHON_BIN}" -B -c 'import sys,json
raw=sys.stdin.read().strip().splitlines()
try:
    d=json.loads(raw[-1]); print(", ".join(d.get("overall",{}).get("blocking_reasons",[])) or "unknown")
except Exception:
    print("unparsed")'
  fi
  return "${rc}"
}
wait_for_release_drain() {
  [ "${DRAIN_WAIT_SECONDS}" = "0" ] && { log "VKPI_TRAIN_DRAIN_WAIT_SECONDS=0:不等排水,直接交给 deploy"; return 0; }
  local deadline reasons rc
  deadline=$(( $(date +%s) + DRAIN_WAIT_SECONDS ))
  while :; do
    reasons="$(probe_release_drain)"; rc=$?
    case "${rc}" in
      0) log "prod 排水已空,交给 deploy"; return 0 ;;
      3) log "prod 排水非空:${reasons};${DRAIN_PROBE_INTERVAL}s 后再探(剩 $(( deadline - $(date +%s) ))s)" ;;
      *) die "排水探针出错(rc=${rc}):ssh/远端 python/库名解析之一失败,不盲等" ;;
    esac
    [ "$(date +%s)" -ge "${deadline}" ] && die "等排水 ${DRAIN_WAIT_SECONDS}s 超时,最后阻塞:${reasons}。prod 未动;换窗口再发"
    sleep "${DRAIN_PROBE_INTERVAL}"
  done
}
wait_for_release_drain
assert_clean_tree "排水后"

# ── 6b. 回滚标记观察哨:deploy 日志行没有时间戳,这里在标记行首次出现时落 UTC 时刻 ──
# 只读轮询 DEPLOY_LOG,不碰部署流程;outcome.json 的 rollback{started_at,completed_at} 用。
# 观察哨没抓到的时刻落 null,绝不编时间;交付采集器会把缺时刻的样本剔出 rollback_p95。
ROLLBACK_MARK_START="acceptance failed; restoring previous application release"
ROLLBACK_MARK_DONE="rollback accepted: app="
DEPLOY_EVENTS_FILE="${DEPLOY_LOG}.events"
DEPLOY_WATCH_STOP="${DEPLOY_LOG}.watch-stop"
watch_rollback_markers() {
  local seen_start=0 seen_done=0 stopping=0
  while :; do
    # 先记停机信号再扫一轮:stop 出现后仍保证做完最后一次全量匹配,标记行不漏。
    [ -e "${DEPLOY_WATCH_STOP}" ] && stopping=1
    if [ "${seen_start}" -eq 0 ] && grep -qF -- "${ROLLBACK_MARK_START}" "${DEPLOY_LOG}" 2>/dev/null; then
      seen_start=1
      printf 'rollback_started_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${DEPLOY_EVENTS_FILE}"
    fi
    if [ "${seen_done}" -eq 0 ] && grep -qF -- "${ROLLBACK_MARK_DONE}" "${DEPLOY_LOG}" 2>/dev/null; then
      seen_done=1
      printf 'rollback_completed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${DEPLOY_EVENTS_FILE}"
    fi
    [ "${stopping}" -eq 1 ] && return 0
    # 班车本体没了就自行退出,不留孤儿轮询($$ 在后台子 shell 里仍是父进程 pid)。
    kill -0 "$$" 2>/dev/null || return 0
    sleep 2
  done
}
rm -f -- "${DEPLOY_EVENTS_FILE}" "${DEPLOY_WATCH_STOP}"
: > "${DEPLOY_EVENTS_FILE}"
watch_rollback_markers &
DEPLOY_WATCH_PID=$!

# ── 7. deploy(祖先硬检查/远端互斥/quiesce/rsync/激活/验收/回滚都在部署脚本内)──
log "deploy 开始 → ${DEPLOY_LOG}"
set +e
env \
  VKPI_DEPLOY_CANDIDATE_DIR="${CANDIDATE_DIR}" \
  VKPI_DEPLOY_CANDIDATE_MANIFEST="${CANDIDATE_MANIFEST}" \
  VKPI_HEALTH_ENV_FILE="${HEALTH_ENV_FILE}" \
  VKPI_BROWSER_GATE_URL="${BROWSER_GATE_URL}" \
  VKPI_STAGING_DB_CLONE=0 \
  bash "${ROOT}/scripts/ops/deploy_local_to_cloud.sh" > "${DEPLOY_LOG}" 2>&1  # 不走 tee 管道:Node 会把共享管道设非阻塞,后续 Python 大输出 Errno 35 → rc 120 误回滚
DEPLOY_RC=$?
set -e

# 观察哨收工(stop 信号后哨兵还会补扫最后一轮,再退出)。
touch "${DEPLOY_WATCH_STOP}"
wait "${DEPLOY_WATCH_PID}" 2>/dev/null || true
rm -f -- "${DEPLOY_WATCH_STOP}"

# ── 7b. 落 outcome.json:发车结果三态 + 回滚时段 + hotfix 指向(交付采集器只读消费)──
# 只追加,不改既有流程;写失败只告警,不改变班车退出码。
train_outcome_result() {
  if [ "${DEPLOY_RC}" -eq 0 ]; then
    printf 'success'
  elif grep -qF -- "${ROLLBACK_MARK_DONE}" "${DEPLOY_LOG}" 2>/dev/null; then
    printf 'rolled_back'
  else
    printf 'failed'
  fi
}
resolve_outcome_dir() {
  # 找本次班车启动之后、以本 sha12 结尾的最新 post-deploy 证据目录;
  # 一个都没有(deploy 在证据落盘前就挂了)则新建同规格目录只装 outcome.json。
  local d base ts best_ts="" best=""
  for d in "${OPS_DIR}/post-deploy/"*"-${SHA:0:12}"; do
    [ -d "${d}" ] || continue
    base="${d##*/}"
    [[ "${base}" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] || continue
    ts="${base%%-*}"
    [[ "${ts}" < "${TRAIN_STARTED_AT}" ]] && continue
    if [ -z "${best_ts}" ] || [[ "${ts}" > "${best_ts}" ]]; then
      best_ts="${ts}"
      best="${d}"
    fi
  done
  if [ -z "${best}" ]; then
    best="${OPS_DIR}/post-deploy/${TRAIN_STARTED_AT}-${SHA:0:12}"
    mkdir -p -- "${best}" || return 1
  fi
  printf '%s' "${best}"
}
OUTCOME_PATH=""
write_train_outcome() {
  local dir result
  result="$(train_outcome_result)" || return 1
  dir="$(resolve_outcome_dir)" || return 1
  OUTCOME_PATH="${dir}/outcome.json"
  VKPI_TRAIN_OUTCOME_RESULT="${result}" \
  VKPI_TRAIN_OUTCOME_HOTFIX_OF="${HOTFIX_OF}" \
  VKPI_TRAIN_OUTCOME_EVENTS_FILE="${DEPLOY_EVENTS_FILE}" \
  "${PYTHON_BIN}" -B - "${OUTCOME_PATH}" <<'PY'
import json
import os
import sys
from pathlib import Path

out_path = Path(sys.argv[1])
result = os.environ["VKPI_TRAIN_OUTCOME_RESULT"]
if result not in {"success", "rolled_back", "failed"}:
    raise SystemExit(f"unsupported outcome result: {result}")
rollback = None
if result == "rolled_back":
    events: dict[str, str] = {}
    events_file = Path(os.environ.get("VKPI_TRAIN_OUTCOME_EVENTS_FILE") or "")
    if events_file.is_file():
        for line in events_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and value.strip():
                events.setdefault(key.strip(), value.strip())
    rollback = {
        "started_at": events.get("rollback_started_at"),
        "completed_at": events.get("rollback_completed_at"),
    }
hotfix_of = os.environ.get("VKPI_TRAIN_OUTCOME_HOTFIX_OF") or None
payload = {"result": result, "rollback": rollback, "hotfix_of": hotfix_of}
tmp = out_path.with_name(f".{out_path.name}.{os.getpid()}.tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.replace(out_path)
PY
}
if write_train_outcome; then
  log "outcome.json 已落:${OUTCOME_PATH}"
else
  log "WARNING: outcome.json 写入失败(不改变班车退出码;交付台账缺这一笔,请按 runbook 手补)"
fi

# ── 8. 结果 ──
echo
echo "================ 班车结果 ================"
echo "  sha        ${SHA}"
echo "  branch     ${BRANCH}"
echo "  candidate  ${CANDIDATE_DIR}"
echo "  deploy log ${DEPLOY_LOG}"
echo "  train log  ${TRAIN_LOG}"
echo "  outcome    ${OUTCOME_PATH:-未写入}"
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
