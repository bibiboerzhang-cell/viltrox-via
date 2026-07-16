#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

assert_clean_worktree() {
  local dirty_status
  dirty_status="$(git status --porcelain=v1 --untracked-files=all)"
  if [ -n "${dirty_status}" ]; then
    echo "Refusing deploy from dirty worktree. Commit or remove every local change before deploying." >&2
    printf '%s\n' "${dirty_status}" >&2
    return 1
  fi
}

# The payload is rsynced from the worktree while its build stamp names HEAD.
# Refuse all dirty trees before any gate, build, backup, or remote operation.
assert_clean_worktree
LOCAL_GIT_SHA="$(git rev-parse --verify HEAD)"
LOCAL_GIT_BRANCH="$(git branch --show-current)"
LOCAL_HEALTH_ENV_FILE="${VKPI_HEALTH_ENV_FILE:-}"
if [ -z "${LOCAL_HEALTH_ENV_FILE}" ]; then
  echo "VKPI_HEALTH_ENV_FILE must explicitly name the protected local health-token dotenv." >&2
  exit 1
fi

assert_deploy_source_unchanged() {
  local current_head
  assert_clean_worktree
  current_head="$(git rev-parse --verify HEAD)"
  if [ "${current_head}" != "${LOCAL_GIT_SHA}" ]; then
    echo "Refusing deploy because HEAD changed during deployment preparation." >&2
    return 1
  fi
}

# ---- F2 发布门:verify 全绿才允许出海(强制,非零即中止,无跳过开关)----
# 覆盖:后端 pytest + 前端 tsc/build + dist 分包护栏 + 仓库硬化 + 红线 grep
#       + 千行卫兵 + 运行态 sha 对齐。任何一步失败都不允许 rsync 上云。
echo "[deploy] gate: strict code + runtime trust verification(全绿才继续)..."
if ! VKPI_VERIFY_REQUIRE_RUNTIME=1 VKPI_VERIFY_REQUIRE_CLEAN_WORKTREE=1 \
  OPS_HEALTH_TOKEN= VKPI_HEALTH_ENV_FILE="${LOCAL_HEALTH_ENV_FILE}" \
  VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE=0 VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY=0 \
  bash "${PROJECT_ROOT}/scripts/verify.sh"; then
  echo "[deploy] verify.sh 非零退出 —— 部署中止。先把 verify 修绿再出海。" >&2
  exit 1
fi

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
SERVICE_NAME="${SERVICE_NAME:-viltrox-2.0-test.service}"
REMOTE_SERVICE_UNIT_RELATIVE="${REMOTE_SERVICE_UNIT_RELATIVE:-scripts/ops/systemd/viltrox-2.0-test.service}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8001/health}"
SYNC_SERVICE="${SYNC_SERVICE:-vkpi-sync-daily.service}"
SYNC_TIMER="${SYNC_TIMER:-vkpi-sync-daily.timer}"
ALLOW_DURING_SYNC="${ALLOW_DURING_SYNC:-0}"
REMOTE_APP_USER="${REMOTE_APP_USER:-viltrox}"
REMOTE_APP_GROUP="${REMOTE_APP_GROUP:-viltrox}"
LANE_OVERRIDE_TEMPLATE_RELATIVE="scripts/ops/systemd/vkpi-lane-overrides.env"
REMOTE_LANE_OVERRIDE_DIR="/etc/vkpi"
REMOTE_LANE_OVERRIDE_FILE="${REMOTE_LANE_OVERRIDE_DIR}/vkpi-lane-overrides.env"
MAX_WORKER_AGE_SECONDS="${VKPI_VERIFY_MAX_WORKER_AGE_SECONDS:-180}"
EXPECTED_WORKER_COUNT=7
WORKER_SYSTEMD_UNITS=(
  vkpi-worker-interactive.service
  vkpi-worker-bulk@1.service
  vkpi-worker-bulk@2.service
  vkpi-worker-bulk@3.service
  vkpi-worker-bulk@4.service
  vkpi-worker-bulk@5.service
  vkpi-worker-bulk@6.service
)
POST_DEPLOY_BROWSER_URL="${VKPI_BROWSER_GATE_URL:-}"
POST_DEPLOY_BROWSER_TOKEN="${VKPI_BROWSER_GATE_TOKEN:-}"
# Keep an explicitly supplied bearer out of every subsequently spawned process.
# It is passed only to the in-memory browser controller at the final gate.
unset VKPI_BROWSER_GATE_TOKEN
BROWSER_GATE_TOKEN_TTL_SECONDS="${VKPI_BROWSER_GATE_TOKEN_TTL_SECONDS:-300}"
BROWSER_GATE_SETTLE_MS="${VKPI_BROWSER_GATE_SETTLE_MS:-5000}"
BROWSER_GATE_PAGE_SETTLE_MS="${VKPI_BROWSER_GATE_PAGE_SETTLE_MS:-1000}"
BROWSER_GATE_PAGE_TIMEOUT_MS="${VKPI_BROWSER_GATE_PAGE_TIMEOUT_MS:-30000}"
BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS="${VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS:-}"
POST_DEPLOY_CHROME_PATH="${VKPI_CHROME_PATH:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
REMOTE_ACCEPTANCE_BASE_URL="${VKPI_REMOTE_ACCEPTANCE_BASE_URL:-${HEALTH_URL%/health}}"
RELEASE_ID="${VKPI_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)-${LOCAL_GIT_SHA:0:12}}"
if ! [[ "${RELEASE_ID}" =~ ^[A-Za-z0-9_.-]+$ ]] || [ "${RELEASE_ID}" = "." ] || [ "${RELEASE_ID}" = ".." ]; then
  echo "VKPI_RELEASE_ID must be a safe release directory name." >&2
  exit 1
fi
REMOTE_RELEASES_DIR="${REMOTE_ROOT}/releases"
REMOTE_RELEASE_DIR="${REMOTE_RELEASES_DIR}/${RELEASE_ID}"
REMOTE_CURRENT_DIR="${REMOTE_ROOT}/current"
ROLLBACK_ARMED=0
ROLLBACK_COMPLETED=0
PREDEPLOY_APP_SHA=""
PREDEPLOY_MIGRATION=""
PENDING_MIGRATIONS=""
FORWARD_COMPATIBILITY_DECLARATION="${VKPI_FORWARD_COMPATIBLE_MIGRATIONS:-}"
STAGING_DB_CLONE_MODE="${VKPI_STAGING_DB_CLONE:-0}"
FIRST_ATOMIC_BOOTSTRAP_PLAN="${VKPI_FIRST_ATOMIC_BOOTSTRAP_PLAN:-}"
FIRST_ATOMIC_BOOTSTRAP_CONFIRM="${VKPI_FIRST_ATOMIC_BOOTSTRAP_CONFIRM:-}"
FIRST_ATOMIC_BOOTSTRAP_MODE=0
FIRST_ATOMIC_BOOTSTRAP_SUCCESS_MARKER="/etc/vkpi/first-atomic-bootstrap-accepted.json"
FIRST_ATOMIC_BOOTSTRAP_BACKUP_STAMP=""
FIRST_ATOMIC_BOOTSTRAP_PLAN_SHA256=""
FIRST_ATOMIC_BOOTSTRAP_SERVER_SHA=""
FIRST_ATOMIC_BOOTSTRAP_CLIENT_SHA=""
FIRST_ATOMIC_BOOTSTRAP_ROOT_SHA=""
FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR=""
STAGING_SOURCE_DATABASE=""
STAGING_CLONE_DATABASE=""
STAGING_REDIS_WORKER_SERVICE="vkpi-redis-worker.service"
STAGING_REDIS_WORKER_UNIT_WAS_PRESENT=""
STAGING_REDIS_WORKER_UNIT_WAS_ACTIVE=""
STAGING_REDIS_WORKER_UNIT_WAS_ENABLED=""
STAGING_REDIS_WORKER_UNIT_WAS_MASKED=""
STAGING_REDIS_WORKER_CAPTURED_STATE=""
STAGING_REDIS_WORKER_UNIT_STATE=""
DATABASE_RELEASE_STRATEGY="in-place"
DATABASE_OWNER_RELEASE_ID=""
PREDEPLOY_DATABASE_OWNER_RELEASE_ID=""
ACTIVE_RELEASE_ID=""
STAGING_SOURCE_KIND=""
PREDEPLOY_DATABASE_NAME=""
PREDEPLOY_ENV_SHA256=""
STAGING_CLONE_ENV_SHA256=""
STAGING_DB_CLONE_ACTIVATED=0
STAGING_BACKUP_VERIFIED=0
RELEASE_CONSUMERS_QUIESCED=0
SYNC_UNITS_CAPTURED=0
SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=0
SYNC_UNITS_QUIESCED=0
SYNC_UNITS_RESTORED=0
SYNC_SERVICE_ACTIVE_STATE=""
SYNC_SERVICE_UNIT_FILE_STATE=""
SYNC_TIMER_ACTIVE_STATE=""
SYNC_TIMER_UNIT_FILE_STATE=""

if [ -n "${FIRST_ATOMIC_BOOTSTRAP_PLAN}" ] || [ -n "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" ]; then
  if [ -z "${FIRST_ATOMIC_BOOTSTRAP_PLAN}" ] || [ -z "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" ]; then
    echo "VKPI_FIRST_ATOMIC_BOOTSTRAP_PLAN and VKPI_FIRST_ATOMIC_BOOTSTRAP_CONFIRM must be supplied together." >&2
    exit 1
  fi
  FIRST_ATOMIC_BOOTSTRAP_MODE=1
fi

if ! [[ "${SERVICE_NAME}" =~ ^[A-Za-z0-9@_.-]+\.service$ ]]; then
  echo "SERVICE_NAME must be a systemd service unit name." >&2
  exit 1
fi
if ! [[ "${SYNC_SERVICE}" =~ ^[A-Za-z0-9@_.-]+\.service$ ]] \
  || ! [[ "${SYNC_TIMER}" =~ ^[A-Za-z0-9@_.-]+\.timer$ ]]; then
  echo "SYNC_SERVICE and SYNC_TIMER must be direct systemd service/timer unit names." >&2
  exit 1
fi
if [ "${ALLOW_DURING_SYNC}" != "0" ] && [ "${ALLOW_DURING_SYNC}" != "1" ]; then
  echo "ALLOW_DURING_SYNC must be exactly 0 or 1." >&2
  exit 1
fi
if ! [[ "${REMOTE_SERVICE_UNIT_RELATIVE}" =~ ^scripts/ops/systemd/[A-Za-z0-9@_.-]+\.service$ ]]; then
  echo "REMOTE_SERVICE_UNIT_RELATIVE must name one direct reviewed unit under scripts/ops/systemd/." >&2
  exit 1
fi
if [ ! -f "${PROJECT_ROOT}/${REMOTE_SERVICE_UNIT_RELATIVE}" ]; then
  echo "Reviewed web service unit is missing: ${REMOTE_SERVICE_UNIT_RELATIVE}" >&2
  exit 1
fi
if [ ! -f "${PROJECT_ROOT}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}" ]; then
  echo "Reviewed worker lane override template is missing: ${LANE_OVERRIDE_TEMPLATE_RELATIVE}" >&2
  exit 1
fi
if [ "${REMOTE_SERVICE_UNIT_RELATIVE}" = "scripts/ops/systemd/viltrox-2.0-test.service" ] && {
  [ "${REMOTE_ROOT}" != "/opt/viltrox-2.0" ] ||
  [ "${REMOTE_APP_USER}" != "viltrox" ] ||
  [ "${REMOTE_APP_GROUP}" != "viltrox" ];
}; then
  echo "The reviewed viltrox-2.0-test.service is bound to /opt/viltrox-2.0 and user/group viltrox." >&2
  exit 1
fi

if [ -z "${POST_DEPLOY_BROWSER_URL}" ]; then
  echo "VKPI_BROWSER_GATE_URL is mandatory for post-restart deployment acceptance." >&2
  exit 1
fi
VILTROXTEST_RELEASE_SCOPE=0
if [ "${REMOTE_ROOT}" = "/opt/viltrox-2.0" ] \
  && [ "${SERVICE_NAME}" = "viltrox-2.0-test.service" ] \
  && [ "${REMOTE_SERVICE_UNIT_RELATIVE}" = "scripts/ops/systemd/viltrox-2.0-test.service" ]; then
  VILTROXTEST_RELEASE_SCOPE=1
fi
if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  if [ "${VILTROXTEST_RELEASE_SCOPE}" != "1" ]; then
    echo "The first atomic bootstrap is restricted to the reviewed viltroxtest release scope." >&2
    exit 1
  fi
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    echo "The first atomic bootstrap requires VKPI_STAGING_DB_CLONE=1." >&2
    exit 1
  fi
  if [ "${SKIP_BACKUP:-0}" = "1" ]; then
    echo "SKIP_BACKUP=1 is forbidden for the first atomic bootstrap." >&2
    exit 1
  fi
  if [ -n "${FORWARD_COMPATIBILITY_DECLARATION}" ]; then
    echo "The first atomic bootstrap forbids an in-place forward-compatibility declaration." >&2
    exit 1
  fi
fi
viltroxtest_browser_gate_is_exact() {
  "${PROJECT_ROOT}/.venv/bin/python" - "${POST_DEPLOY_BROWSER_URL}" <<'PY'
import sys
from urllib.parse import urlsplit

try:
    parsed = urlsplit(sys.argv[1])
    valid = (
        parsed.scheme == "https"
        and parsed.hostname == "viltroxtest.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
    )
except ValueError:
    valid = False
raise SystemExit(0 if valid else 1)
PY
}

validate_lane_override_template() {
  "${PROJECT_ROOT}/.venv/bin/python" - "${PROJECT_ROOT}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}" <<'PY'
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
allowed = {
    "APIFY_WORKER_GEMINI_QPS",
    "APIFY_WORKER_LLM_CONCURRENCY",
    "APIFY_WORKER_PROFILE_MEDIA_CONCURRENCY",
    "APIFY_WORKER_COMMENTS_CONCURRENCY",
    "APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY",
    "LLM_MONTHLY_BUDGET_USD",
    "POSTGRES_POOL_MIN_SIZE",
    "POSTGRES_POOL_MAX_SIZE",
}
values = {}
for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit(f"lane override line {line_number} is not KEY=VALUE")
    key, value = (part.strip() for part in line.split("=", 1))
    if key not in allowed or key in values:
        raise SystemExit(f"lane override key is unreviewed or duplicated: {key}")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        raise SystemExit(f"lane override value is not a plain non-secret number: {key}")
    values[key] = value
if set(values) != allowed:
    missing = ",".join(sorted(allowed - set(values)))
    raise SystemExit(f"lane override template is incomplete: {missing}")

def integer(name: str, minimum: int, maximum: int) -> int:
    value = values[name]
    if not value.isdigit():
        raise SystemExit(f"lane override must be an integer: {name}")
    number = int(value)
    if not minimum <= number <= maximum:
        raise SystemExit(f"lane override is outside reviewed bounds: {name}")
    return number

try:
    qps = Decimal(values["APIFY_WORKER_GEMINI_QPS"])
    budget = Decimal(values["LLM_MONTHLY_BUDGET_USD"])
except InvalidOperation as exc:
    raise SystemExit("lane override decimal is invalid") from exc
if not Decimal("0") < qps <= Decimal("2"):
    raise SystemExit("APIFY_WORKER_GEMINI_QPS is outside reviewed bounds")
if not Decimal("0") < budget <= Decimal("1000000"):
    raise SystemExit("LLM_MONTHLY_BUDGET_USD is outside reviewed bounds")
integer("APIFY_WORKER_LLM_CONCURRENCY", 1, 16)
for key in (
    "APIFY_WORKER_PROFILE_MEDIA_CONCURRENCY",
    "APIFY_WORKER_COMMENTS_CONCURRENCY",
    "APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY",
):
    integer(key, 1, 2)
pool_min = integer("POSTGRES_POOL_MIN_SIZE", 1, 16)
pool_max = integer("POSTGRES_POOL_MAX_SIZE", 1, 64)
if pool_min > pool_max:
    raise SystemExit("POSTGRES_POOL_MIN_SIZE exceeds POSTGRES_POOL_MAX_SIZE")
PY
}
if ! validate_lane_override_template; then
  echo "Reviewed worker lane override template failed the non-secret allowlist contract." >&2
  exit 1
fi
if [ "${STAGING_DB_CLONE_MODE}" != "0" ] && [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
  echo "VKPI_STAGING_DB_CLONE must be exactly 0 or 1." >&2
  exit 1
fi
if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
  if [ "${VILTROXTEST_RELEASE_SCOPE}" != "1" ]; then
    echo "VKPI_STAGING_DB_CLONE=1 is restricted to the reviewed viltroxtest root and service." >&2
    exit 1
  fi
  if ! viltroxtest_browser_gate_is_exact; then
    echo "VKPI_STAGING_DB_CLONE=1 requires an HTTPS browser gate on host viltroxtest.com." >&2
    exit 1
  fi
  if [ -n "${FORWARD_COMPATIBILITY_DECLARATION}" ]; then
    echo "A staging database clone must not claim in-place forward compatibility." >&2
    exit 1
  fi
  DATABASE_RELEASE_STRATEGY="staging-clone"
  STAGING_CLONE_DATABASE="$("${PROJECT_ROOT}/.venv/bin/python" \
    "${PROJECT_ROOT}/scripts/ops/staging_db_clone.py" name --release-id "${RELEASE_ID}")"
fi
if ! [[ "${BROWSER_GATE_TOKEN_TTL_SECONDS}" =~ ^[0-9]+$ ]] \
  || [ "${BROWSER_GATE_TOKEN_TTL_SECONDS}" -lt 60 ] \
  || [ "${BROWSER_GATE_TOKEN_TTL_SECONDS}" -gt 300 ]; then
  echo "VKPI_BROWSER_GATE_TOKEN_TTL_SECONDS must be an integer within [60, 300]." >&2
  exit 1
fi
if ! [[ "${BROWSER_GATE_SETTLE_MS}" =~ ^[0-9]+$ ]] \
  || [ "${BROWSER_GATE_SETTLE_MS}" -lt 1000 ] \
  || [ "${BROWSER_GATE_SETTLE_MS}" -gt 60000 ]; then
  echo "VKPI_BROWSER_GATE_SETTLE_MS must be an integer within [1000, 60000]." >&2
  exit 1
fi
if ! [[ "${BROWSER_GATE_PAGE_SETTLE_MS}" =~ ^[0-9]+$ ]] \
  || [ "${BROWSER_GATE_PAGE_SETTLE_MS}" -lt 250 ] \
  || [ "${BROWSER_GATE_PAGE_SETTLE_MS}" -gt 10000 ]; then
  echo "VKPI_BROWSER_GATE_PAGE_SETTLE_MS must be an integer within [250, 10000]." >&2
  exit 1
fi
if ! [[ "${BROWSER_GATE_PAGE_TIMEOUT_MS}" =~ ^[0-9]+$ ]] \
  || [ "${BROWSER_GATE_PAGE_TIMEOUT_MS}" -lt 5000 ] \
  || [ "${BROWSER_GATE_PAGE_TIMEOUT_MS}" -gt 60000 ]; then
  echo "VKPI_BROWSER_GATE_PAGE_TIMEOUT_MS must be an integer within [5000, 60000]." >&2
  exit 1
fi
if ! "${PROJECT_ROOT}/.venv/bin/python" - \
  "${POST_DEPLOY_BROWSER_URL}" \
  "${BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS}" <<'PY'
import sys
from urllib.parse import urlsplit

target = urlsplit(sys.argv[1])
target_port = target.port
target_authority = (target.hostname or "").lower()
if target_port is not None and target_port != (443 if target.scheme == "https" else 80):
    target_authority = f"{target_authority}:{target_port}"
target_origin = f"{target.scheme.lower()}://{target_authority}"
for raw in (item.strip() for item in sys.argv[2].split(",")):
    if not raw:
        continue
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        raise SystemExit(1)
    authority = (parsed.hostname or "").lower()
    if port is not None and port != 443:
        authority = f"{authority}:{port}"
    normalized = f"https://{authority}"
    valid = (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and "*" not in parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and not parsed.query
        and not parsed.fragment
        and raw == normalized
        and normalized != target_origin
    )
    if not valid:
        raise SystemExit(1)
PY
then
  echo "VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS must contain only exact external HTTPS origins." >&2
  exit 1
fi
if [ ! -x "${POST_DEPLOY_CHROME_PATH}" ]; then
  echo "Post-restart extension-free Chrome is not executable: ${POST_DEPLOY_CHROME_PATH}" >&2
  exit 1
fi

if [ -n "${VKPI_POST_DEPLOY_EVIDENCE_DIR:-}" ]; then
  POST_DEPLOY_EVIDENCE_DIR="${VKPI_POST_DEPLOY_EVIDENCE_DIR}"
  POST_DEPLOY_EVIDENCE_OWNED=0
else
  POST_DEPLOY_EVIDENCE_DIR="${PROJECT_ROOT}/runtime/ops/post-deploy/${RELEASE_ID}"
  POST_DEPLOY_EVIDENCE_OWNED=1
fi
REMOTE_LOG_BASELINE=""
REMOTE_ACCEPTANCE_REPORT=""
DEPLOY_ACCEPTED=0

capture_remote_sync_unit_state() {
  local captured
  if ! captured="$(ssh "${SSH_TARGET}" "service_load=\$(systemctl show --property LoadState --value '${SYNC_SERVICE}'); timer_load=\$(systemctl show --property LoadState --value '${SYNC_TIMER}'); [ \"\${service_load}\" = loaded ] && [ \"\${timer_load}\" = loaded ] || { echo 'reviewed sync service/timer is not loaded' >&2; exit 1; }; printf '%s:%s:%s:%s\n' \"\$(systemctl show --property ActiveState --value '${SYNC_SERVICE}')\" \"\$(systemctl show --property UnitFileState --value '${SYNC_SERVICE}')\" \"\$(systemctl show --property ActiveState --value '${SYNC_TIMER}')\" \"\$(systemctl show --property UnitFileState --value '${SYNC_TIMER}')\"")"; then
    echo "Refusing deploy because the reviewed sync service/timer state is unreadable." >&2
    return 1
  fi
  IFS=: read -r SYNC_SERVICE_ACTIVE_STATE SYNC_SERVICE_UNIT_FILE_STATE \
    SYNC_TIMER_ACTIVE_STATE SYNC_TIMER_UNIT_FILE_STATE <<<"${captured}"
  case "${SYNC_SERVICE_ACTIVE_STATE}" in
    active|activating|inactive) ;;
    *)
      echo "Refusing deploy because ${SYNC_SERVICE} has an unrestorable active state: ${SYNC_SERVICE_ACTIVE_STATE}." >&2
      return 1
      ;;
  esac
  case "${SYNC_TIMER_ACTIVE_STATE}" in
    active|activating|inactive) ;;
    *)
      echo "Refusing deploy because ${SYNC_TIMER} has an unrestorable active state: ${SYNC_TIMER_ACTIVE_STATE}." >&2
      return 1
      ;;
  esac
  case "${SYNC_SERVICE_UNIT_FILE_STATE}" in
    enabled|enabled-runtime|linked|linked-runtime|alias|static|indirect|disabled|generated|transient|masked|masked-runtime) ;;
    *)
      echo "Refusing deploy because ${SYNC_SERVICE} has an unrestorable unit-file state: ${SYNC_SERVICE_UNIT_FILE_STATE}." >&2
      return 1
      ;;
  esac
  case "${SYNC_TIMER_UNIT_FILE_STATE}" in
    enabled|enabled-runtime|linked|linked-runtime|alias|static|indirect|disabled|generated|transient|masked|masked-runtime) ;;
    *)
      echo "Refusing deploy because ${SYNC_TIMER} has an unrestorable unit-file state: ${SYNC_TIMER_UNIT_FILE_STATE}." >&2
      return 1
      ;;
  esac
  SYNC_UNITS_CAPTURED=1
}

restore_remote_sync_unit_state() {
  local service_unmask_runtime=1 timer_unmask_runtime=1
  local service_should_start=0 timer_should_start=0
  if [ "${SYNC_UNITS_MAY_HAVE_BEEN_MUTATED}" != "1" ]; then
    return 0
  fi
  if [ "${SYNC_UNITS_CAPTURED}" != "1" ]; then
    echo "Cannot restore sync units without a captured pre-mutation state." >&2
    return 1
  fi
  [ "${SYNC_SERVICE_UNIT_FILE_STATE}" = "masked-runtime" ] && service_unmask_runtime=0
  [ "${SYNC_TIMER_UNIT_FILE_STATE}" = "masked-runtime" ] && timer_unmask_runtime=0
  case "${SYNC_SERVICE_ACTIVE_STATE}" in active|activating) service_should_start=1 ;; esac
  case "${SYNC_TIMER_ACTIVE_STATE}" in active|activating) timer_should_start=1 ;; esac
  if ! ssh "${SSH_TARGET}" "if [ '${service_unmask_runtime}' = 1 ]; then sudo systemctl unmask --runtime '${SYNC_SERVICE}' >/dev/null; fi; if [ '${timer_unmask_runtime}' = 1 ]; then sudo systemctl unmask --runtime '${SYNC_TIMER}' >/dev/null; fi; sudo systemctl daemon-reload; if [ '${timer_should_start}' = 1 ]; then sudo systemctl start --no-block '${SYNC_TIMER}'; else sudo systemctl stop '${SYNC_TIMER}'; fi; if [ '${service_should_start}' = 1 ]; then sudo systemctl start --no-block '${SYNC_SERVICE}'; else sudo systemctl stop '${SYNC_SERVICE}'; fi; service_file_state=\$(systemctl show --property UnitFileState --value '${SYNC_SERVICE}'); timer_file_state=\$(systemctl show --property UnitFileState --value '${SYNC_TIMER}'); [ \"\${service_file_state}\" = '${SYNC_SERVICE_UNIT_FILE_STATE}' ] || { echo 'sync service unit-file state was not restored' >&2; exit 1; }; [ \"\${timer_file_state}\" = '${SYNC_TIMER_UNIT_FILE_STATE}' ] || { echo 'sync timer unit-file state was not restored' >&2; exit 1; }; if [ '${service_should_start}' = 1 ]; then service_active=\$(systemctl show --property ActiveState --value '${SYNC_SERVICE}'); case \"\${service_active}\" in active|activating) ;; *) echo 'sync service active state was not restored' >&2; exit 1;; esac; elif systemctl is-active --quiet '${SYNC_SERVICE}'; then echo 'sync service unexpectedly active after restore' >&2; exit 1; fi; if [ '${timer_should_start}' = 1 ]; then timer_active=\$(systemctl show --property ActiveState --value '${SYNC_TIMER}'); case \"\${timer_active}\" in active|activating) ;; *) echo 'sync timer active state was not restored' >&2; exit 1;; esac; elif systemctl is-active --quiet '${SYNC_TIMER}'; then echo 'sync timer unexpectedly active after restore' >&2; exit 1; fi"; then
    echo "CRITICAL: reviewed sync service/timer state could not be restored." >&2
    return 1
  fi
  SYNC_UNITS_RESTORED=1
}

attempt_automatic_rollback() {
  local rollback_not_before rollback_health rollback_migration rollback_env_state
  local rollback_database rollback_env_sha256
  echo "[deploy] acceptance failed; restoring previous application release, environment, database identity, and reviewed units..." >&2
  rollback_not_before="$(ssh "${SSH_TARGET}" "date -u +%Y-%m-%dT%H:%M:%SZ")" || return 1
  # Restore shared env/current/unit state only after every process that can read
  # it has stopped.  Stopping just the Redis worker leaves web and Apify workers
  # executing across the pointer/env switch and creates a mixed-release window.
  if ! ssh "${SSH_TARGET}" "redis_unit=''; if systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then redis_unit='${STAGING_REDIS_WORKER_SERVICE}'; fi; sudo systemctl stop '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS} \${redis_unit}; for unit in '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}; do if systemctl is-active --quiet \"\${unit}\"; then echo \"release consumer failed to stop before rollback: \${unit}\" >&2; exit 1; fi; done; if systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then echo 'Redis worker failed to stop before rollback' >&2; exit 1; fi; if systemctl is-enabled --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then sudo systemctl disable --now '${STAGING_REDIS_WORKER_SERVICE}'; fi"; then
    echo "[deploy] CRITICAL: complete web/worker fleet could not be quiesced before rollback." >&2
    return 1
  fi
  if ! ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' restore --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --unit-dir /etc/systemd/system && sudo systemctl daemon-reload"; then
    echo "[deploy] CRITICAL: filesystem, environment, or unit rollback failed; operator intervention required." >&2
    return 1
  fi
  if [ "${DATABASE_RELEASE_STRATEGY}" = "staging-clone" ] \
    || [ "${DATABASE_RELEASE_STRATEGY}" = "reuse-active-clone" ]; then
    if ! rollback_env_state="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' assert-env --env-file '${REMOTE_ROOT}/.env' --expected-db '${PREDEPLOY_DATABASE_NAME}'")"; then
      echo "[deploy] CRITICAL: restored environment database identity could not be verified." >&2
      return 1
    fi
    read -r rollback_database rollback_env_sha256 < <(printf '%s' "${rollback_env_state}" | "${PROJECT_ROOT}/.venv/bin/python" -c 'import json,sys; p=json.load(sys.stdin); print(p["database_name"], p["env_sha256"])')
    if [ "${rollback_database}" != "${PREDEPLOY_DATABASE_NAME}" ] \
      || [ "${rollback_env_sha256}" != "${PREDEPLOY_ENV_SHA256}" ]; then
      echo "[deploy] CRITICAL: rollback environment fingerprint or database identity mismatch." >&2
      return 1
    fi
    if [ "${DATABASE_RELEASE_STRATEGY}" = "staging-clone" ]; then
      if [ "${STAGING_DB_CLONE_ACTIVATED}" != "1" ]; then
        if ! ssh "${SSH_TARGET}" "sudo -n -u postgres env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' drop --target-db '${STAGING_CLONE_DATABASE}' >/dev/null"; then
          echo "[deploy] CRITICAL: unactivated staging clone cleanup failed." >&2
          return 1
        fi
      fi
      if [ -n "${STAGING_CLONE_ENV_SHA256}" ]; then
        ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' write-receipt --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --source-db '${STAGING_SOURCE_DATABASE}' --target-db '${STAGING_CLONE_DATABASE}' --env-fingerprint-before '${PREDEPLOY_ENV_SHA256}' --env-fingerprint-clone '${STAGING_CLONE_ENV_SHA256}' --migration-version '${LATEST_MIGRATION}' --state rollback-restored --rollback-env-fingerprint '${rollback_env_sha256}' >/dev/null" || return 1
      fi
    fi
  fi
  if [ "${STAGING_REDIS_WORKER_UNIT_WAS_MASKED}" = "1" ]; then
    if ! ssh "${SSH_TARGET}" "sudo systemctl mask '${STAGING_REDIS_WORKER_SERVICE}' >/dev/null && sudo systemctl daemon-reload"; then
      echo "[deploy] CRITICAL: restored Redis worker unit could not be remasked." >&2
      return 1
    fi
  elif [ "${STAGING_REDIS_WORKER_UNIT_WAS_PRESENT}" = "1" ]; then
    if [ "${STAGING_REDIS_WORKER_UNIT_WAS_ENABLED}" = "1" ]; then
      if ! ssh "${SSH_TARGET}" "sudo systemctl unmask '${STAGING_REDIS_WORKER_SERVICE}' >/dev/null && sudo systemctl enable '${STAGING_REDIS_WORKER_SERVICE}' >/dev/null"; then
        echo "[deploy] CRITICAL: restored Redis worker enablement could not be restored." >&2
        return 1
      fi
    else
      if ! ssh "${SSH_TARGET}" "sudo systemctl unmask '${STAGING_REDIS_WORKER_SERVICE}' >/dev/null && sudo systemctl disable '${STAGING_REDIS_WORKER_SERVICE}' >/dev/null"; then
        echo "[deploy] CRITICAL: restored Redis worker disablement could not be restored." >&2
        return 1
      fi
    fi
    if [ "${STAGING_REDIS_WORKER_UNIT_WAS_ACTIVE}" = "1" ]; then
      if ! ssh "${SSH_TARGET}" "sudo systemctl start '${STAGING_REDIS_WORKER_SERVICE}' && systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'"; then
        echo "[deploy] CRITICAL: restored Redis worker active state could not be restored." >&2
        return 1
      fi
    elif ! ssh "${SSH_TARGET}" "sudo systemctl stop '${STAGING_REDIS_WORKER_SERVICE}' && ! systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'"; then
      echo "[deploy] CRITICAL: restored Redis worker inactive state could not be restored." >&2
      return 1
    fi
  else
    ssh "${SSH_TARGET}" "sudo systemctl unmask '${STAGING_REDIS_WORKER_SERVICE}' >/dev/null 2>&1 || true; sudo systemctl daemon-reload" || return 1
  fi
  local restored_redis_unit_state
  if ! restored_redis_unit_state="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' inspect-unit-state --unit-dir /etc/systemd/system --unit-name '${STAGING_REDIS_WORKER_SERVICE}'")" \
    || [ "${restored_redis_unit_state}" != "${STAGING_REDIS_WORKER_UNIT_STATE}" ]; then
    echo "[deploy] CRITICAL: rollback did not restore the exact Redis worker unit state." >&2
    return 1
  fi
  if ! ssh "${SSH_TARGET}" "sudo systemctl restart '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS} && systemctl is-active --quiet '${SERVICE_NAME}' && for unit in ${WORKER_SYSTEMD_UNIT_ARGS}; do systemctl is-active --quiet \"\${unit}\"; done"; then
    echo "[deploy] CRITICAL: restored web/worker service restart failed; operator intervention required." >&2
    return 1
  fi
  if ! rollback_health="$(ssh "${SSH_TARGET}" "for attempt in \$(seq 1 60); do if sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/fetch_runtime_health.py' --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env' 2>/dev/null; then exit 0; fi; sleep 2; done; exit 1")"; then
    echo "[deploy] CRITICAL: restored release did not return authenticated health." >&2
    return 1
  fi
  rollback_migration="$(printf '%s' "${rollback_health}" | "${PROJECT_ROOT}/.venv/bin/python" -c 'import json,sys; print(str((json.load(sys.stdin).get("trust") or {}).get("db_migration_max") or ""))')"
  if { [ "${STAGING_DB_CLONE_MODE}" = "1" ] && [ "${rollback_migration}" != "${PREDEPLOY_MIGRATION}" ]; } \
    || { [ "${STAGING_DB_CLONE_MODE}" != "1" ] && [ "${rollback_migration}" != "${PREDEPLOY_MIGRATION}" ] && [ "${rollback_migration}" != "${LATEST_MIGRATION}" ]; }; then
    echo "[deploy] CRITICAL: rollback health reported an unexpected DB migration: ${rollback_migration}" >&2
    return 1
  fi
  if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
    local rollback_preflight rollback_anchor rollback_health_file rollback_preflight_status
    rollback_preflight="${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}/rollback-preflight.json"
    rollback_anchor="${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}/rollback-anchor.json"
    rollback_health_file="${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}/rollback-health.json"
    printf '%s\n' "${rollback_health}" >"${rollback_health_file}"
    chmod 600 "${rollback_health_file}"
    if "${PROJECT_ROOT}/.venv/bin/python" \
      "${PROJECT_ROOT}/scripts/ops/legacy_to_atomic_preflight.py" \
      --ssh-target "${SSH_TARGET}" \
      --root "${REMOTE_ROOT}" \
      --app-user "${REMOTE_APP_USER}" \
      --remote-python "${REMOTE_ROOT}/.venv/bin/python" \
      --health-url "${HEALTH_URL}" \
      --expected-migration "${LATEST_MIGRATION}" \
      >"${rollback_preflight}"; then
      rollback_preflight_status=0
    else
      rollback_preflight_status=$?
    fi
    chmod 600 "${rollback_preflight}"
    if [ "${rollback_preflight_status}" -ne 2 ]; then
      echo "[deploy] CRITICAL: restored legacy preflight no longer has the exact planned six blockers." >&2
      return 1
    fi
    if ! ssh "${SSH_TARGET}" \
      "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - collect-anchor --root '${REMOTE_ROOT}' --backup-stamp '${FIRST_ATOMIC_BOOTSTRAP_BACKUP_STAMP}' --success-marker '${FIRST_ATOMIC_BOOTSTRAP_SUCCESS_MARKER}'" \
      <"${PROJECT_ROOT}/scripts/ops/verify_legacy_bootstrap_anchor.py" \
      >"${rollback_anchor}"; then
      echo "[deploy] CRITICAL: restored legacy filesystem anchor could not be collected." >&2
      return 1
    fi
    chmod 600 "${rollback_anchor}"
    if ! "${PROJECT_ROOT}/.venv/bin/python" \
      "${PROJECT_ROOT}/scripts/ops/verify_legacy_bootstrap_anchor.py" verify-rollback \
      --plan "${FIRST_ATOMIC_BOOTSTRAP_PLAN}" \
      --confirm "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" \
      --preflight "${rollback_preflight}" \
      --health "${rollback_health_file}" \
      --anchor "${rollback_anchor}" \
      --ssh-target "${SSH_TARGET}" \
      --root "${REMOTE_ROOT}" \
      --service "${SERVICE_NAME}" \
      --health-url "${HEALTH_URL}" \
      --release-id "${RELEASE_ID}" \
      --git-sha "${LOCAL_GIT_SHA}" \
      --target-migration "${LATEST_MIGRATION}" \
      --pending-migrations "${PENDING_MIGRATIONS}" >/dev/null; then
      echo "[deploy] CRITICAL: restored pointers, units, environment, recovery evidence, or split legacy SHA anchor drifted." >&2
      return 1
    fi
  elif ! printf '%s' "${rollback_health}" | "${PROJECT_ROOT}/.venv/bin/python" \
    "${PROJECT_ROOT}/scripts/verify_runtime_health.py" \
    --strict-deploy \
    --expected-head "${PREDEPLOY_APP_SHA}" \
    --expected-migration "${rollback_migration}" \
    --require-worker \
    --expected-worker-count "${EXPECTED_WORKER_COUNT}" \
    --worker-not-before "${rollback_not_before}" \
    --max-worker-age-seconds "${MAX_WORKER_AGE_SECONDS}"; then
    echo "[deploy] CRITICAL: restored release failed strict runtime validation." >&2
    return 1
  fi
  if [ "${STAGING_REDIS_WORKER_UNIT_WAS_ACTIVE}" = "1" ]; then
    if ! printf '%s' "${rollback_health}" | "${PROJECT_ROOT}/.venv/bin/python" \
      "${PROJECT_ROOT}/scripts/verify_redis_worker_health.py" \
      --expected-head "${PREDEPLOY_APP_SHA}" \
      --expected-count 1 \
      --worker-not-before "${rollback_not_before}" \
      --max-age-seconds "${MAX_WORKER_AGE_SECONDS}" >/dev/null; then
      echo "[deploy] CRITICAL: restored Redis worker failed strict runtime validation." >&2
      return 1
    fi
  fi
  if ! restore_remote_sync_unit_state; then
    echo "[deploy] CRITICAL: rollback runtime is restored but sync unit state is not; units remain fail-closed." >&2
    return 1
  fi
  ROLLBACK_COMPLETED=1
  echo "[deploy] rollback accepted: app=${PREDEPLOY_APP_SHA} migration=${rollback_migration} database=${PREDEPLOY_DATABASE_NAME:-in-place}." >&2
  return 0
}

cleanup_post_deploy_evidence() {
  local original_rc=$?
  set +e
  if [ "${original_rc}" -ne 0 ] && [ "${DEPLOY_ACCEPTED}" != "1" ]; then
    if [ "${ROLLBACK_ARMED}" = "1" ]; then
      # A failed rollback intentionally leaves sync fail-closed.  Resuming a
      # timer against an untrusted app/database state would compound failure.
      attempt_automatic_rollback || true
    elif [ "${SYNC_UNITS_MAY_HAVE_BEEN_MUTATED}" = "1" ] \
      && [ "${SYNC_UNITS_RESTORED}" != "1" ]; then
      # Before prepare there is no release state to roll back, but a local
      # build/backup/staging failure must still restore the captured timer.
      restore_remote_sync_unit_state || true
    fi
  fi
  if [ -n "${REMOTE_LOG_BASELINE}" ] || [ -n "${REMOTE_ACCEPTANCE_REPORT}" ]; then
    ssh "${SSH_TARGET}" "rm -f -- '${REMOTE_LOG_BASELINE}' '${REMOTE_ACCEPTANCE_REPORT}'" >/dev/null 2>&1
  fi
  if [ -d "${POST_DEPLOY_EVIDENCE_DIR}" ]; then
    echo "[deploy] post-restart evidence retained: ${POST_DEPLOY_EVIDENCE_DIR}" >&2
  fi
  if [ -n "${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}" ]; then
    rm -rf -- "${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}"
  fi
  return "${original_rc}"
}
trap cleanup_post_deploy_evidence EXIT

if ! [[ "${MAX_WORKER_AGE_SECONDS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "VKPI_VERIFY_MAX_WORKER_AGE_SECONDS must be a positive integer." >&2
  exit 1
fi
if [ "${#WORKER_SYSTEMD_UNITS[@]}" -ne "${EXPECTED_WORKER_COUNT}" ]; then
  echo "Reviewed worker fleet must contain exactly ${EXPECTED_WORKER_COUNT} systemd units." >&2
  exit 1
fi
for worker_unit in "${WORKER_SYSTEMD_UNITS[@]}"; do
  if ! [[ "${worker_unit}" =~ ^[A-Za-z0-9@_.-]+\.service$ ]]; then
    echo "Invalid reviewed worker service unit: ${worker_unit}" >&2
    exit 1
  fi
done
WORKER_SYSTEMD_UNIT_ARGS="${WORKER_SYSTEMD_UNITS[*]}"
JOURNAL_SYSTEMD_UNITS=(
  "${SERVICE_NAME}"
  "${WORKER_SYSTEMD_UNITS[@]}"
  "${STAGING_REDIS_WORKER_SERVICE}"
)
JOURNAL_SYSTEMD_UNIT_FLAGS=""
for journal_unit in "${JOURNAL_SYSTEMD_UNITS[@]}"; do
  JOURNAL_SYSTEMD_UNIT_FLAGS+=" --unit ${journal_unit}"
done

quiesce_remote_sync_units() {
  if [ "${SYNC_UNITS_CAPTURED}" != "1" ]; then
    echo "Refusing sync quiesce without a captured service/timer state." >&2
    return 1
  fi
  # Set this before SSH because mask --now can succeed partially before the
  # remote shell reports failure.  The EXIT trap then restores the captured
  # state while no release rollback is armed.
  SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=1
  if ! ssh "${SSH_TARGET}" "sudo systemctl mask --runtime --now '${SYNC_TIMER}' '${SYNC_SERVICE}' >/dev/null; for sync_unit in '${SYNC_TIMER}' '${SYNC_SERVICE}'; do if systemctl is-active --quiet \"\${sync_unit}\"; then echo \"sync unit failed to stop before deployment staging: \${sync_unit}\" >&2; exit 1; fi; sync_file_state=\$(systemctl show --property UnitFileState --value \"\${sync_unit}\"); case \"\${sync_file_state}\" in masked|masked-runtime) ;; *) echo \"sync unit failed to mask before deployment staging: \${sync_unit}\" >&2; exit 1;; esac; done"; then
    echo "[deploy] reviewed sync timer/service could not be quiesced before build, backup, or remote staging." >&2
    return 1
  fi
  SYNC_UNITS_QUIESCED=1
}

quiesce_remote_release_consumers() {
  local expected_redis_state="inactive"
  if [ "${STAGING_REDIS_WORKER_UNIT_WAS_ACTIVE}" = "1" ]; then
    expected_redis_state="active"
  fi
  if [ "${SYNC_UNITS_CAPTURED}" != "1" ] || [ "${SYNC_UNITS_QUIESCED}" != "1" ]; then
    echo "Refusing release quiesce unless the captured sync service/timer is already fail-closed." >&2
    return 1
  fi
  # Set this before the remote transaction: even a partially failed mask/stop
  # must be treated as a mutation and restored only by the rollback path.
  SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=1
  if ! ssh "${SSH_TARGET}" "for unit in '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}; do systemctl is-active --quiet \"\${unit}\" || { echo \"required release consumer is not active before quiesce: \${unit}\" >&2; exit 1; }; done; if [ '${expected_redis_state}' = active ]; then systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}' || { echo 'captured active Redis worker changed state before quiesce' >&2; exit 1; }; redis_unit='${STAGING_REDIS_WORKER_SERVICE}'; else if systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then echo 'captured inactive Redis worker changed state before quiesce' >&2; exit 1; fi; redis_unit=''; fi; sudo systemctl mask --runtime --now '${SYNC_TIMER}' '${SYNC_SERVICE}' >/dev/null; for sync_unit in '${SYNC_TIMER}' '${SYNC_SERVICE}'; do if systemctl is-active --quiet \"\${sync_unit}\"; then echo \"sync unit failed to stop: \${sync_unit}\" >&2; exit 1; fi; sync_file_state=\$(systemctl show --property UnitFileState --value \"\${sync_unit}\"); case \"\${sync_file_state}\" in masked|masked-runtime) ;; *) echo \"sync unit failed to mask: \${sync_unit}\" >&2; exit 1;; esac; done; sudo systemctl stop '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS} \${redis_unit}; for unit in '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}; do if systemctl is-active --quiet \"\${unit}\"; then echo \"release consumer failed to stop: \${unit}\" >&2; exit 1; fi; done; if systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then echo 'Redis worker failed to stop' >&2; exit 1; fi"; then
    echo "[deploy] complete web/worker fleet could not be quiesced before release mutation." >&2
    return 1
  fi
  RELEASE_CONSUMERS_QUIESCED=1
  SYNC_UNITS_QUIESCED=1
}

# The first atomic release may target a legacy tree that does not yet contain
# scripts/ops/fetch_runtime_health.py.  Execute the reviewed local probe over
# stdin for the pre-mutation identity read, so bootstrap does not depend on a
# file that only arrives later with the release payload.  The probe reads the
# remote .env in memory and returns health JSON; it never writes to the host.
fetch_predeploy_runtime_health() {
  ssh "${SSH_TARGET}" \
    "sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B - --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env'" \
    < "${PROJECT_ROOT}/scripts/ops/fetch_runtime_health.py"
}

harden_first_atomic_root() {
  # The legacy host historically let the application user own REMOTE_ROOT.
  # A root-owned 0700 release controller is not a security boundary while its
  # parent can still be renamed by that user. Make this a one-way reliability
  # prerequisite: rollback never downgrades the root to app-writable ownership.
  ssh "${SSH_TARGET}" \
    "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - '${REMOTE_ROOT}' '${REMOTE_APP_USER}' '${REMOTE_APP_GROUP}'" <<'PY'
from __future__ import annotations

import grp
import os
from pathlib import Path
import pwd
import stat
import sys

root = Path(sys.argv[1])
app_uid = pwd.getpwnam(sys.argv[2]).pw_uid
app_gid = grp.getgrnam(sys.argv[3]).gr_gid
before = root.lstat()
if not stat.S_ISDIR(before.st_mode) or root.is_symlink():
    raise SystemExit("application root must be a real directory")
allowed = {
    (app_uid, app_gid, 0o755),
    (0, app_gid, 0o755),
}
if (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) not in allowed:
    raise SystemExit("application root does not match the reviewed legacy or hardened shape")
parent = root.parent
parent_info = parent.lstat()
if (
    not stat.S_ISDIR(parent_info.st_mode)
    or parent.is_symlink()
    or parent_info.st_uid != 0
    or stat.S_IMODE(parent_info.st_mode) & 0o022
):
    raise SystemExit("application root parent is not a trusted root-owned directory")
os.chown(root, 0, app_gid, follow_symlinks=False)
os.chmod(root, 0o755, follow_symlinks=False)
for directory in (root, parent):
    descriptor = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
after = root.lstat()
if (
    not stat.S_ISDIR(after.st_mode)
    or after.st_uid != 0
    or after.st_gid != app_gid
    or stat.S_IMODE(after.st_mode) != 0o755
):
    raise SystemExit("application root hardening postcondition failed")
sys.stdout.write("application_root_hardening=verified\n")
PY
}

LATEST_MIGRATION="$(find "${PROJECT_ROOT}/migrations" -maxdepth 1 -type f -name '*.sql' ! -name '*_down.sql' -exec basename {} \; | LC_ALL=C sort | tail -n 1)"
if [ -z "${LATEST_MIGRATION}" ]; then
  echo "Refusing deploy because the local migration manifest is empty." >&2
  exit 1
fi
MIGRATION_MANIFEST_CSV="$(find "${PROJECT_ROOT}/migrations" -maxdepth 1 -type f -name '*.sql' ! -name '*_down.sql' -exec basename {} \; | LC_ALL=C sort | paste -sd, -)"

capture_remote_sync_unit_state
sync_state="${SYNC_SERVICE_ACTIVE_STATE}"
if [ "${ALLOW_DURING_SYNC}" != "1" ] && { [ "${sync_state}" = "active" ] || [ "${sync_state}" = "activating" ]; }; then
  echo "Refusing deploy while ${SYNC_SERVICE} is ${sync_state}. Set ALLOW_DURING_SYNC=1 only for an intentional ops override." >&2
  exit 1
fi

# Read runtime identity before the first remote mutation.  For the default
# in-place strategy the database is never auto-restored: a release with pending
# migrations may proceed only when the operator declares the exact ordered set
# forward-compatible.  The explicit viltroxtest clone strategy instead restores
# the captured environment to its untouched source database on application rollback.
if ! REMOTE_PREDEPLOY_HEALTH_JSON="$(fetch_predeploy_runtime_health)"; then
  echo "Refusing deploy because pre-deploy authenticated runtime identity could not be read." >&2
  exit 1
fi
read -r PREDEPLOY_APP_SHA PREDEPLOY_MIGRATION < <(printf '%s' "${REMOTE_PREDEPLOY_HEALTH_JSON}" | "${PROJECT_ROOT}/.venv/bin/python" -c 'import json,sys; p=json.load(sys.stdin); t=p.get("trust") or {}; print(str(t.get("server_git_sha") or ""), str(t.get("db_migration_max") or ""))')
if ! [[ "${PREDEPLOY_APP_SHA}" =~ ^[0-9a-f]{40}$ ]] || [ -z "${PREDEPLOY_MIGRATION}" ]; then
  echo "Refusing deploy because pre-deploy app SHA or migration identity is untrusted." >&2
  exit 1
fi
# Every normal deploy keeps the strict aligned rollback anchor and has no legacy mismatch bypass.
# The one-time
# legacy bootstrap is not a boolean bypass: it is accepted only later by the
# separately hashed plan whose old server/client identities are bound exactly.
if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" != "1" ]; then
  if ! printf '%s' "${REMOTE_PREDEPLOY_HEALTH_JSON}" | "${PROJECT_ROOT}/.venv/bin/python" \
    "${PROJECT_ROOT}/scripts/verify_runtime_health.py" \
    --strict-deploy \
    --expected-head "${PREDEPLOY_APP_SHA}" \
    --expected-migration "${PREDEPLOY_MIGRATION}" \
    --require-worker \
    --expected-worker-count "${EXPECTED_WORKER_COUNT}" \
    --worker-not-before "1970-01-01T00:00:00Z" \
    --max-worker-age-seconds "${MAX_WORKER_AGE_SECONDS}" >/dev/null; then
    echo "Refusing deploy because the pre-deploy rollback anchor is not a strict aligned web/client/seven-worker runtime." >&2
    exit 1
  fi
fi
if [ "${VILTROXTEST_RELEASE_SCOPE}" = "1" ]; then
  if ! REMOTE_PREDEPLOY_DB_STATE_JSON="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - '${REMOTE_ROOT}/.env'" <<'PY'
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

def secure_controller_directory(path, label):
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise SystemExit(f"{label} is missing") from None
    if not stat.S_ISDIR(info.st_mode):
        raise SystemExit(f"{label} must be a real directory")
    if info.st_uid != os.geteuid():
        raise SystemExit(f"{label} owner is not the release controller")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise SystemExit(f"{label} mode must be 0700")
    return path

def read_controller_file(path, label):
    try:
        initial = path.lstat()
    except FileNotFoundError:
        raise SystemExit(f"{label} is missing") from None
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise SystemExit(f"{label} must be a regular single-link file")
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (initial.st_dev, initial.st_ino)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise SystemExit(f"{label} is not a secure controller file")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)

path = Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit("shared environment file is not a regular file")
content = path.read_bytes()
matches = []
runtime_values = {}
for line in content.decode("utf-8").splitlines():
    raw = line.strip()
    if not raw or raw.startswith("#") or "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    runtime_values[key.strip()] = value.strip().strip("'\"")
    if key.strip() == "DATABASE_URL":
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        matches.append(value)
if len(matches) != 1:
    raise SystemExit("expected exactly one active DATABASE_URL")
if runtime_values.get("DATABASE_POOL_URL", "").strip():
    raise SystemExit("DATABASE_POOL_URL must be unset for staging clone")
if runtime_values.get("DB_USE_PGBOUNCER", "").strip().lower() in {"1", "true", "yes", "on"}:
    raise SystemExit("DB_USE_PGBOUNCER must be disabled for staging clone")
try:
    parsed = urlsplit(matches[0])
except ValueError:
    raise SystemExit("DATABASE_URL is invalid") from None
database_name = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
if parsed.scheme not in {"postgres", "postgresql"} or not database_name or "/" in database_name:
    raise SystemExit("DATABASE_URL database path is invalid")
root = path.parent.resolve()
source_kind = "legacy-base"
source_release_id = ""
active_release_id = ""
database_owner_release_id = ""
clone_prefix = "viltrox2_test_release_"
clone_re = re.compile(r"^viltrox2_test_release_[0-9a-f]{20}$")
release_id_re = re.compile(r"^[A-Za-z0-9_.-]+$")
current = root / "current"
if database_name == "viltrox2_test":
    if current.is_symlink():
        manifest_path = current.resolve(strict=True) / ".vkpi-release.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        if manifest.get("database_strategy") in {"staging-clone", "reuse-active-clone"}:
            raise SystemExit("refusing fallback from an active release clone to the legacy base")
elif clone_re.fullmatch(database_name):
    if not current.is_symlink():
        raise SystemExit("release clone source requires the current pointer")
    releases = (root / "releases").resolve()
    active = current.resolve(strict=True)
    if releases not in active.parents or not active.is_dir():
        raise SystemExit("current pointer escapes releases")
    manifest_path = active / ".vkpi-release.json"
    if not manifest_path.is_file():
        raise SystemExit("active clone manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    active_release_id = str(manifest.get("release_id") or "")
    if not release_id_re.fullmatch(active_release_id) or active_release_id in {".", ".."}:
        raise SystemExit("active clone manifest release id is invalid")
    strategy = manifest.get("database_strategy")
    if strategy == "staging-clone":
        database_owner_release_id = active_release_id
    elif strategy == "reuse-active-clone":
        database_owner_release_id = str(manifest.get("database_owner_release_id") or "")
        if manifest.get("pending_migrations") not in ([], None) or manifest.get(
            "forward_compatible_migrations"
        ) not in ([], None):
            raise SystemExit("clone-reuse manifest is not app-only")
    else:
        raise SystemExit("active clone manifest lost database lineage")
    if (
        not release_id_re.fullmatch(database_owner_release_id)
        or database_owner_release_id in {".", ".."}
    ):
        raise SystemExit("database owner release id is invalid")
    expected_name = clone_prefix + hashlib.sha256(
        database_owner_release_id.encode()
    ).hexdigest()[:20]
    if manifest.get("target_database") != database_name or expected_name != database_name:
        raise SystemExit("active clone manifest identity mismatch")
    owner_release = (releases / database_owner_release_id).resolve(strict=True)
    if releases not in owner_release.parents or not owner_release.is_dir():
        raise SystemExit("database owner release escapes releases")
    owner_manifest_path = owner_release / ".vkpi-release.json"
    owner_manifest = json.loads(owner_manifest_path.read_text(encoding="utf-8"))
    if (
        owner_manifest.get("release_id") != database_owner_release_id
        or owner_manifest.get("database_strategy") != "staging-clone"
        or owner_manifest.get("target_database") != database_name
    ):
        raise SystemExit("database owner release manifest identity mismatch")
    controller = secure_controller_directory(
        root / ".release-controller", "release controller directory"
    )
    rollbacks = secure_controller_directory(
        controller / "rollbacks", "release rollback directory"
    )
    rollback_dir = secure_controller_directory(
        rollbacks / database_owner_release_id,
        "release rollback capture directory",
    )
    metadata_digest = read_controller_file(
        rollback_dir / "metadata.sha256", "rollback metadata digest"
    ).decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", metadata_digest):
        raise SystemExit("rollback metadata digest is invalid")
    metadata_payload = read_controller_file(
        rollback_dir / "metadata.json", "rollback metadata"
    )
    if hashlib.sha256(metadata_payload).hexdigest() != metadata_digest:
        raise SystemExit("rollback metadata hash mismatch")
    rollback_metadata = json.loads(metadata_payload.decode("utf-8"))
    if (
        rollback_metadata.get("schema") != 3
        or rollback_metadata.get("release_id") != database_owner_release_id
    ):
        raise SystemExit("rollback capture does not belong to the database owner release")
    receipt_path = rollback_dir / "database-clone.json"
    receipt = json.loads(
        read_controller_file(receipt_path, "database clone receipt").decode("utf-8")
    )
    if (
        receipt.get("release_id") != database_owner_release_id
        or receipt.get("database_strategy") != "staging-clone"
        or receipt.get("source_database") != owner_manifest.get("source_database")
        or receipt.get("target_database") != database_name
        or receipt.get("state") != "activated"
        or receipt.get("secrets_included") is not False
    ):
        raise SystemExit("active clone receipt identity mismatch")
    source_release_id = database_owner_release_id
    source_kind = "prior-release-clone"
else:
    raise SystemExit("DATABASE_URL is not a reviewed staging source")
print(json.dumps({
    "database_name": database_name,
    "env_sha256": hashlib.sha256(content).hexdigest(),
    "source_kind": source_kind,
    "source_release_id": source_release_id,
    "database_owner_release_id": database_owner_release_id,
    "active_release_id": active_release_id,
}, sort_keys=True))
PY
)"; then
    echo "Refusing viltroxtest deploy because the remote database identity is unreadable." >&2
    exit 1
  fi
  read -r PREDEPLOY_DATABASE_NAME PREDEPLOY_ENV_SHA256 STAGING_SOURCE_KIND PREDEPLOY_DATABASE_OWNER_RELEASE_ID ACTIVE_RELEASE_ID < <(printf '%s' "${REMOTE_PREDEPLOY_DB_STATE_JSON}" | "${PROJECT_ROOT}/.venv/bin/python" -c 'import json,sys; p=json.load(sys.stdin); print(p["database_name"], p["env_sha256"], p["source_kind"], p["database_owner_release_id"], p["active_release_id"])')
  if ! [[ "${PREDEPLOY_DATABASE_NAME}" =~ ^(viltrox2_test|viltrox2_test_release_[0-9a-f]{20})$ ]] \
    || ! [[ "${PREDEPLOY_ENV_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
    || ! [[ "${STAGING_SOURCE_KIND}" =~ ^(legacy-base|prior-release-clone)$ ]]; then
    echo "Refusing viltroxtest deploy because the active database lineage is unproven." >&2
    exit 1
  fi
  if [ "${STAGING_SOURCE_KIND}" = "prior-release-clone" ]; then
    if ! [[ "${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}" =~ ^[A-Za-z0-9_.-]+$ ]] \
      || [ "${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}" = "." ] \
      || [ "${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}" = ".." ] \
      || ! [[ "${ACTIVE_RELEASE_ID}" =~ ^[A-Za-z0-9_.-]+$ ]] \
      || ! viltroxtest_browser_gate_is_exact; then
      echo "Refusing viltroxtest deploy because active clone ownership or browser scope is invalid." >&2
      exit 1
    fi
  fi
  if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
    STAGING_SOURCE_DATABASE="${PREDEPLOY_DATABASE_NAME}"
  fi
fi
if ! PENDING_MIGRATIONS="$("${PROJECT_ROOT}/.venv/bin/python" - "${PREDEPLOY_MIGRATION}" "${MIGRATION_MANIFEST_CSV}" <<'PY'
import sys

applied = sys.argv[1]
manifest = [value for value in sys.argv[2].split(",") if value]
if applied not in manifest:
    raise SystemExit(f"remote migration is not in the reviewed local sequence: {applied}")
print(",".join(manifest[manifest.index(applied) + 1 :]))
PY
)"; then
  echo "Refusing deploy because the remote migration cannot be safely ordered against local migrations." >&2
  exit 1
fi
if [ -n "${PENDING_MIGRATIONS}" ]; then
  if [ "${STAGING_SOURCE_KIND}" = "prior-release-clone" ] \
    && [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    echo "Refusing to mutate an active release clone in place; use VKPI_STAGING_DB_CLONE=1 so the rollback anchor remains untouched." >&2
    exit 1
  fi
  if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
    if [ -n "${FORWARD_COMPATIBILITY_DECLARATION}" ]; then
      echo "A staging clone deploy must not declare in-place forward-compatible migrations." >&2
      exit 1
    fi
  elif [ "${FORWARD_COMPATIBILITY_DECLARATION}" != "${PENDING_MIGRATIONS}" ]; then
    echo "Refusing deploy with pending migrations. Set VKPI_FORWARD_COMPATIBLE_MIGRATIONS to this exact reviewed CSV: ${PENDING_MIGRATIONS}" >&2
    exit 1
  fi
  if [ "${SKIP_BACKUP:-0}" = "1" ]; then
    echo "SKIP_BACKUP=1 is forbidden when pending migrations exist." >&2
    exit 1
  fi
elif [ -n "${FORWARD_COMPATIBILITY_DECLARATION}" ]; then
  echo "Refusing stale VKPI_FORWARD_COMPATIBLE_MIGRATIONS declaration when no migration is pending." >&2
  exit 1
elif [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
  echo "VKPI_STAGING_DB_CLONE=1 requires at least one pending migration." >&2
  exit 1
elif [ "${STAGING_SOURCE_KIND}" = "prior-release-clone" ]; then
  # An application-only release keeps using the already-active clone.  The new
  # app manifest points to the original database-owning release and its
  # activated receipt so the next migration release can prove its real source.
  DATABASE_RELEASE_STRATEGY="reuse-active-clone"
  STAGING_SOURCE_DATABASE=""
  STAGING_CLONE_DATABASE="${PREDEPLOY_DATABASE_NAME}"
  DATABASE_OWNER_RELEASE_ID="${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}"
fi

if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  if [ "${STAGING_SOURCE_KIND}" != "legacy-base" ] \
    || [ "${PREDEPLOY_DATABASE_NAME}" != "viltrox2_test" ] \
    || [ -z "${PENDING_MIGRATIONS}" ]; then
    echo "The first atomic bootstrap requires the untouched legacy database source and at least one pending migration." >&2
    exit 1
  fi

  FIRST_ATOMIC_BOOTSTRAP_BACKUP_STAMP="$(
    "${PROJECT_ROOT}/.venv/bin/python" \
      "${PROJECT_ROOT}/scripts/ops/verify_legacy_bootstrap_anchor.py" plan-field \
      --plan "${FIRST_ATOMIC_BOOTSTRAP_PLAN}" \
      --confirm "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" \
      --field recovery.backup_stamp
  )"
  FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vkpi-first-atomic-bootstrap.XXXXXX")"
  chmod 700 "${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}"
  BOOTSTRAP_PREFLIGHT_JSON="${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}/preflight.json"
  BOOTSTRAP_HEALTH_JSON="${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}/health.json"
  BOOTSTRAP_ANCHOR_JSON="${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}/anchor.json"
  printf '%s\n' "${REMOTE_PREDEPLOY_HEALTH_JSON}" >"${BOOTSTRAP_HEALTH_JSON}"
  chmod 600 "${BOOTSTRAP_HEALTH_JSON}"

  if "${PROJECT_ROOT}/.venv/bin/python" \
    "${PROJECT_ROOT}/scripts/ops/legacy_to_atomic_preflight.py" \
    --ssh-target "${SSH_TARGET}" \
    --root "${REMOTE_ROOT}" \
    --app-user "${REMOTE_APP_USER}" \
    --remote-python "${REMOTE_ROOT}/.venv/bin/python" \
    --health-url "${HEALTH_URL}" \
    --expected-migration "${LATEST_MIGRATION}" \
    >"${BOOTSTRAP_PREFLIGHT_JSON}"; then
    bootstrap_preflight_status=0
  else
    bootstrap_preflight_status=$?
  fi
  chmod 600 "${BOOTSTRAP_PREFLIGHT_JSON}"
  if [ "${bootstrap_preflight_status}" -ne 2 ]; then
    echo "The first atomic bootstrap requires the exact reviewed six-blocker legacy preflight state." >&2
    exit 1
  fi

  ssh "${SSH_TARGET}" \
    "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - collect-anchor --root '${REMOTE_ROOT}' --backup-stamp '${FIRST_ATOMIC_BOOTSTRAP_BACKUP_STAMP}' --success-marker '${FIRST_ATOMIC_BOOTSTRAP_SUCCESS_MARKER}'" \
    <"${PROJECT_ROOT}/scripts/ops/verify_legacy_bootstrap_anchor.py" \
    >"${BOOTSTRAP_ANCHOR_JSON}"
  chmod 600 "${BOOTSTRAP_ANCHOR_JSON}"

  FIRST_ATOMIC_BOOTSTRAP_SUMMARY="$(
    "${PROJECT_ROOT}/.venv/bin/python" \
      "${PROJECT_ROOT}/scripts/ops/verify_legacy_bootstrap_anchor.py" verify-plan \
      --plan "${FIRST_ATOMIC_BOOTSTRAP_PLAN}" \
      --confirm "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" \
      --preflight "${BOOTSTRAP_PREFLIGHT_JSON}" \
      --health "${BOOTSTRAP_HEALTH_JSON}" \
      --anchor "${BOOTSTRAP_ANCHOR_JSON}" \
      --ssh-target "${SSH_TARGET}" \
      --root "${REMOTE_ROOT}" \
      --service "${SERVICE_NAME}" \
      --health-url "${HEALTH_URL}" \
      --release-id "${RELEASE_ID}" \
      --git-sha "${LOCAL_GIT_SHA}" \
      --target-migration "${LATEST_MIGRATION}" \
      --pending-migrations "${PENDING_MIGRATIONS}"
  )"
  read -r FIRST_ATOMIC_BOOTSTRAP_PLAN_SHA256 FIRST_ATOMIC_BOOTSTRAP_SERVER_SHA \
    FIRST_ATOMIC_BOOTSTRAP_CLIENT_SHA FIRST_ATOMIC_BOOTSTRAP_ROOT_SHA \
    bootstrap_database bootstrap_migration bootstrap_env_sha < <(
      printf '%s' "${FIRST_ATOMIC_BOOTSTRAP_SUMMARY}" | "${PROJECT_ROOT}/.venv/bin/python" -c \
        'import json,sys; p=json.load(sys.stdin); print(p["plan_sha256"],p["server_git_sha"],p["client_git_sha"],p["root_build_git_sha"],p["database_name"],p["db_migration"],p["environment_sha256"])'
    )
  if [ "${FIRST_ATOMIC_BOOTSTRAP_PLAN_SHA256}" != "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" ] \
    || [ "${FIRST_ATOMIC_BOOTSTRAP_SERVER_SHA}" != "${PREDEPLOY_APP_SHA}" ] \
    || [ "${bootstrap_database}" != "${PREDEPLOY_DATABASE_NAME}" ] \
    || [ "${bootstrap_migration}" != "${PREDEPLOY_MIGRATION}" ] \
    || [ "${bootstrap_env_sha}" != "${PREDEPLOY_ENV_SHA256}" ]; then
    echo "The first atomic bootstrap live anchor changed during verification." >&2
    exit 1
  fi
  STAGING_BACKUP_VERIFIED=1
  echo "[deploy] first atomic bootstrap plan verified: ${FIRST_ATOMIC_BOOTSTRAP_PLAN_SHA256}"
  harden_first_atomic_root
fi

# Freeze timer-triggered writers before the potentially long build, backup and
# release staging interval.  The formal fleet quiesce reasserts the mask before
# any shared environment/database/current-pointer mutation.
quiesce_remote_sync_units

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  npm --prefix frontend run build
fi

if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  # The signed-off plan already binds a verified, encrypted, off-host backup by
  # three independent SHA-256 values.  Re-running the backup here would create
  # an unplanned recovery anchor and is therefore forbidden rather than useful.
  [ "${STAGING_BACKUP_VERIFIED}" = "1" ] || {
    echo "The first atomic bootstrap recovery set was not verified." >&2
    exit 1
  }
elif [ "${SKIP_BACKUP:-0}" != "1" ]; then
  if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
    STAGING_BACKUP_STAMP="${RELEASE_ID}-preclone"
    STAGING_BACKUP_DIR="${PROJECT_ROOT}/runtime/prod-sync/${STAGING_BACKUP_STAMP}"
    STAMP="${STAGING_BACKUP_STAMP}" LOCAL_DIR="${STAGING_BACKUP_DIR}" \
      REMOTE_APP_USER="${REMOTE_APP_USER}" REMOTE_APP_GROUP="${REMOTE_APP_GROUP}" \
      "${SCRIPT_DIR}/backup_prod_vkpi.sh"
    "${PROJECT_ROOT}/.venv/bin/python" - \
      "${STAGING_BACKUP_DIR}/prod-db.dump" \
      "${STAGING_BACKUP_DIR}/prod-db.dump.sha256" <<'PY'
import hashlib
import re
import sys
from pathlib import Path

dump_path = Path(sys.argv[1])
checksum_path = Path(sys.argv[2])
if not dump_path.is_file() or not checksum_path.is_file() or dump_path.stat().st_size <= 0:
    raise SystemExit("verified staging database backup is incomplete")
expected = checksum_path.read_text(encoding="utf-8").split()[0]
if not re.fullmatch(r"[0-9a-f]{64}", expected):
    raise SystemExit("staging database backup checksum is invalid")
digest = hashlib.sha256()
with dump_path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
if digest.hexdigest() != expected:
    raise SystemExit("staging database backup checksum mismatch")
print("[deploy] verified staging database backup")
PY
    STAGING_BACKUP_VERIFIED=1
  else
    REMOTE_APP_USER="${REMOTE_APP_USER}" REMOTE_APP_GROUP="${REMOTE_APP_GROUP}" \
      "${SCRIPT_DIR}/backup_prod_vkpi.sh"
  fi
fi

LOCAL_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# A local gate/build/backup must not have changed the payload or HEAD.
assert_deploy_source_unchanged

# Create a new immutable destination first.  The running services continue to
# resolve the old current symlink until the complete payload is sealed and the
# symlink is atomically replaced.
ssh "${SSH_TARGET}" "sudo install -d -o root -g root -m 0755 '${REMOTE_RELEASES_DIR}' && if [ -e '${REMOTE_RELEASE_DIR}' ] || [ -L '${REMOTE_RELEASE_DIR}' ]; then echo 'Refusing to reuse an existing release destination.' >&2; exit 1; fi && sudo install -d -o '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' -m 0755 '${REMOTE_RELEASE_DIR}'"
rsync -az --delete \
  --no-owner \
  --no-group \
  --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '.pytest_cache/' \
  --exclude '.DS_Store' \
  --exclude 'frontend/node_modules/' \
  --exclude 'node_modules/' \
  --exclude 'uploads/' \
  --exclude 'frames/' \
  --exclude 'creator_profiles/' \
  --exclude 'runtime/' \
  --exclude 'backups/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'submissions.db' \
  --exclude 'video-production-platform/' \
  --exclude 'reports/vkpi_*.html' \
  --exclude 'reports/vkpi_*.md' \
  ./ "${SSH_TARGET}:${REMOTE_RELEASE_DIR}/"

# If the source changed during rsync, do not stamp or restart the remote tree as
# though it represented the captured HEAD.
assert_deploy_source_unchanged

ssh "${SSH_TARGET}" "cd '${REMOTE_RELEASE_DIR}' && printf '%s\n' '${LOCAL_GIT_SHA}' > BUILD_GIT_SHA && printf '%s\n' '${LOCAL_GIT_BRANCH}' > BUILD_GIT_BRANCH && printf '%s\n' '${LOCAL_BUILD_TIME}' > BUILD_TIME"

# Seal the release and prove every runtime dependency before current can move.
# Package installation during deployment is forbidden: the shared venv is an
# independently prepared prerequisite, not mutable release state.
# Every remote Python call carries both the environment guard and -B.  The
# redundancy is intentional: helpers run before and after sealing, and neither
# a sudo environment policy nor a future env -i refactor may recreate bytecode
# inside the immutable release/current tree and invalidate its payload digest.
if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  # The legacy .env is intentionally still writable until prepare has captured
  # it.  Seal first, then harden .env and run the deferred worker permission
  # checks while the complete consumer fleet is stopped.
  ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' seal --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --git-sha '${LOCAL_GIT_SHA}' --pending-migrations '${PENDING_MIGRATIONS}' --compatibility-declaration '' --database-strategy 'staging-clone' --source-database '${STAGING_SOURCE_DATABASE}' --target-database '${STAGING_CLONE_DATABASE}' --env-fingerprint-before '${PREDEPLOY_ENV_SHA256}' --database-owner-release-id '' --owner-uid 0 --owner-gid 0 && sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' verify-seal --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --expected-owner-uid 0 --expected-owner-gid 0 && sudo -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B -m yt_dlp --version >/dev/null && sudo systemd-analyze verify '${REMOTE_RELEASE_DIR}/${REMOTE_SERVICE_UNIT_RELATIVE}' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/vkpi-worker-interactive.service' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/vkpi-worker-bulk@.service' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/${STAGING_REDIS_WORKER_SERVICE}'"
  BOOTSTRAP_CANDIDATE_MANIFEST="${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}/candidate-manifest.json"
  ssh "${SSH_TARGET}" "sudo cat -- '${REMOTE_RELEASE_DIR}/.vkpi-release.json'" >"${BOOTSTRAP_CANDIDATE_MANIFEST}"
  chmod 600 "${BOOTSTRAP_CANDIDATE_MANIFEST}"
  "${PROJECT_ROOT}/.venv/bin/python" \
    "${PROJECT_ROOT}/scripts/ops/verify_legacy_bootstrap_anchor.py" verify-candidate \
    --plan "${FIRST_ATOMIC_BOOTSTRAP_PLAN}" \
    --confirm "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" \
    --manifest "${BOOTSTRAP_CANDIDATE_MANIFEST}" \
    --target-database "${STAGING_CLONE_DATABASE}" >/dev/null
else
  ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' worker-layout-preflight --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --app-user '${REMOTE_APP_USER}' --app-group '${REMOTE_APP_GROUP}' --provision-missing && sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' seal --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --git-sha '${LOCAL_GIT_SHA}' --pending-migrations '${PENDING_MIGRATIONS}' --compatibility-declaration '${FORWARD_COMPATIBILITY_DECLARATION}' --database-strategy '${DATABASE_RELEASE_STRATEGY}' --source-database '${STAGING_SOURCE_DATABASE}' --target-database '${STAGING_CLONE_DATABASE}' --env-fingerprint-before '${PREDEPLOY_ENV_SHA256}' --database-owner-release-id '${DATABASE_OWNER_RELEASE_ID}' --owner-uid 0 --owner-gid 0 && sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' verify-seal --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --expected-owner-uid 0 --expected-owner-gid 0 && sudo -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B -m yt_dlp --version >/dev/null && sudo -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env VKPI_JOB_RESULTS_DIR='${REMOTE_ROOT}/runtime/job-results' HOME=/tmp/vkpi-worker-home XDG_CACHE_HOME=/tmp/vkpi-worker-cache TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' worker-runtime-preflight --root '${REMOTE_ROOT}' --release-path '${REMOTE_RELEASE_DIR}' --app-user '${REMOTE_APP_USER}' --app-group '${REMOTE_APP_GROUP}' --job-results-dir '${REMOTE_ROOT}/runtime/job-results' && sudo systemd-analyze verify '${REMOTE_RELEASE_DIR}/${REMOTE_SERVICE_UNIT_RELATIVE}' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/vkpi-worker-interactive.service' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/vkpi-worker-bulk@.service' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/${STAGING_REDIS_WORKER_SERVICE}'"
fi

# Capture the exact effective env/units and establish previous before changing
# shared configuration or the active application pointer.
if ! STAGING_REDIS_WORKER_CAPTURED_STATE="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' inspect-unit-state --unit-dir /etc/systemd/system --unit-name '${STAGING_REDIS_WORKER_SERVICE}'")"; then
  echo "Refusing deploy because the Redis worker systemd state is not exactly restorable." >&2
  exit 1
fi
ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' prepare --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --unit-dir /etc/systemd/system --unit-name '${SERVICE_NAME}' --unit-name vkpi-worker-interactive.service --unit-name 'vkpi-worker-bulk@.service' --optional-unit-name '${STAGING_REDIS_WORKER_SERVICE}' --optional-unit-state '${STAGING_REDIS_WORKER_SERVICE}=${STAGING_REDIS_WORKER_CAPTURED_STATE}' --pending-migrations '${PENDING_MIGRATIONS}' --compatibility-declaration '${FORWARD_COMPATIBILITY_DECLARATION}' --database-strategy '${DATABASE_RELEASE_STRATEGY}' --source-database '${STAGING_SOURCE_DATABASE}' --target-database '${STAGING_CLONE_DATABASE}' --env-fingerprint-before '${PREDEPLOY_ENV_SHA256}' --database-owner-release-id '${DATABASE_OWNER_RELEASE_ID}'"
ROLLBACK_ARMED=1

if ! STAGING_REDIS_WORKER_UNIT_STATE="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' rollback-unit-state --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --unit-name '${STAGING_REDIS_WORKER_SERVICE}'")"; then
  echo "Refusing deploy because the captured Redis worker unit state is unreadable." >&2
  exit 1
fi
if [ "${STAGING_REDIS_WORKER_UNIT_STATE}" != "${STAGING_REDIS_WORKER_CAPTURED_STATE}" ]; then
  echo "Refusing deploy because the Redis worker state receipt changed during prepare." >&2
  exit 1
fi
IFS=: read -r REDIS_PRESENCE REDIS_ACTIVITY REDIS_ENABLEMENT REDIS_MASKING <<<"${STAGING_REDIS_WORKER_UNIT_STATE}"
case "${REDIS_PRESENCE}:${REDIS_ACTIVITY}:${REDIS_ENABLEMENT}:${REDIS_MASKING}" in
  absent:inactive:disabled:unmasked)
    STAGING_REDIS_WORKER_UNIT_WAS_PRESENT=0
    STAGING_REDIS_WORKER_UNIT_WAS_ACTIVE=0
    STAGING_REDIS_WORKER_UNIT_WAS_ENABLED=0
    STAGING_REDIS_WORKER_UNIT_WAS_MASKED=0
    ;;
  present:active:enabled:unmasked|present:active:disabled:unmasked|present:inactive:enabled:unmasked|present:inactive:disabled:unmasked)
    STAGING_REDIS_WORKER_UNIT_WAS_PRESENT=1
    [ "${REDIS_ACTIVITY}" = "active" ] && STAGING_REDIS_WORKER_UNIT_WAS_ACTIVE=1 || STAGING_REDIS_WORKER_UNIT_WAS_ACTIVE=0
    [ "${REDIS_ENABLEMENT}" = "enabled" ] && STAGING_REDIS_WORKER_UNIT_WAS_ENABLED=1 || STAGING_REDIS_WORKER_UNIT_WAS_ENABLED=0
    STAGING_REDIS_WORKER_UNIT_WAS_MASKED=0
    ;;
  present:inactive:disabled:masked)
    STAGING_REDIS_WORKER_UNIT_WAS_PRESENT=1
    STAGING_REDIS_WORKER_UNIT_WAS_ACTIVE=0
    STAGING_REDIS_WORKER_UNIT_WAS_ENABLED=0
    STAGING_REDIS_WORKER_UNIT_WAS_MASKED=1
    ;;
  *)
    echo "Refusing deploy because the prior Redis worker unit state is invalid." >&2
    exit 1
    ;;
esac

# From this point until the new release has been fully restarted, no reviewed
# web/Apify/Redis process may observe a current/.env/database pointer change.
quiesce_remote_release_consumers

if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
  if [ "${STAGING_BACKUP_VERIFIED}" != "1" ]; then
    echo "Refusing staging clone creation without a verified release-bound backup." >&2
    exit 1
  fi
  # CREATE DATABASE ... TEMPLATE requires the source database to have no app
  # connections.  The common quiesce step above covers this and app-only releases.
  if [ "${RELEASE_CONSUMERS_QUIESCED}" != "1" ]; then
    echo "Refusing staging clone creation while release consumers are not quiesced." >&2
    exit 1
  fi

  STAGING_CLONE_CREATE_JSON="$(ssh "${SSH_TARGET}" "sudo -n -u postgres env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' create --source-db '${STAGING_SOURCE_DATABASE}' --target-db '${STAGING_CLONE_DATABASE}'")"
  if ! printf '%s' "${STAGING_CLONE_CREATE_JSON}" | "${PROJECT_ROOT}/.venv/bin/python" -c 'import json,sys; p=json.load(sys.stdin); assert p["source_database"] == sys.argv[1]; assert p["target_database"] == sys.argv[2]; assert int(p["free_bytes_before"]) >= int(p["source_size_bytes"]) + 1024**3' "${STAGING_SOURCE_DATABASE}" "${STAGING_CLONE_DATABASE}"; then
    echo "Staging clone creation receipt did not satisfy the reviewed disk/identity contract." >&2
    exit 1
  fi

  STAGING_CLONE_ENV_STATE="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' switch-env --env-file '${REMOTE_ROOT}/.env' --expected-source-db '${STAGING_SOURCE_DATABASE}' --target-db '${STAGING_CLONE_DATABASE}'")"
  read -r STAGING_CLONE_ENV_DATABASE STAGING_CLONE_ENV_SHA256 < <(printf '%s' "${STAGING_CLONE_ENV_STATE}" | "${PROJECT_ROOT}/.venv/bin/python" -c 'import json,sys; p=json.load(sys.stdin); print(p["database_name"], p["env_sha256"])')
  if [ "${STAGING_CLONE_ENV_DATABASE}" != "${STAGING_CLONE_DATABASE}" ] \
    || ! [[ "${STAGING_CLONE_ENV_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
    || [ "${STAGING_CLONE_ENV_SHA256}" = "${PREDEPLOY_ENV_SHA256}" ]; then
    echo "Staging clone environment switch failed identity/fingerprint verification." >&2
    exit 1
  fi

  ssh "${SSH_TARGET}" "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i HOME=/tmp/vkpi-migration-home XDG_CACHE_HOME=/tmp/vkpi-migration-cache TMPDIR=/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' run-migrations-only --env-file '${REMOTE_ROOT}/.env' --release-path '${REMOTE_RELEASE_DIR}' --expected-db '${STAGING_CLONE_DATABASE}' --app-user '${REMOTE_APP_USER}' >/dev/null"
  ssh "${SSH_TARGET}" "sudo -n -u postgres env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' verify-migration --target-db '${STAGING_CLONE_DATABASE}' --expected-version '${LATEST_MIGRATION}' >/dev/null"
fi

if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  ssh "${SSH_TARGET}" "sudo chown root:'${REMOTE_APP_GROUP}' '${REMOTE_ROOT}/.env' && sudo chmod 0640 '${REMOTE_ROOT}/.env'"
fi
ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - '${REMOTE_ROOT}/.env' '${REMOTE_RELEASE_DIR}' '${FIRST_ATOMIC_BOOTSTRAP_MODE}' '${REMOTE_APP_GROUP}' <<'PY'
from __future__ import annotations

import grp
import os
from pathlib import Path
import stat
import sys
import tempfile

path = Path(sys.argv[1])
release = Path(sys.argv[2])
bootstrap = sys.argv[3] == '1'
group_name = sys.argv[4]
before = path.lstat()
if not stat.S_ISREG(before.st_mode) or path.is_symlink() or before.st_nlink != 1:
    raise SystemExit('shared environment file is unsafe')
raw = path.read_bytes()
if len(raw) > 1024 * 1024:
    raise SystemExit('shared environment file is unexpectedly large')
lines = raw.decode('utf-8').splitlines()
git_sha = (release / 'BUILD_GIT_SHA').read_text(encoding='utf-8').strip()
updates = {
    'APP_GIT_SHA': git_sha,
    'APP_GIT_SHORT_SHA': git_sha[:8],
    'APP_GIT_BRANCH': (release / 'BUILD_GIT_BRANCH').read_text(encoding='utf-8').strip(),
    'APP_BUILD_TIME': (release / 'BUILD_TIME').read_text(encoding='utf-8').strip(),
}
seen: set[str] = set()
out: list[str] = []
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        key = line.split('=', 1)[0].strip()
        if key in updates:
            if key in seen:
                raise SystemExit('duplicate application build stamp in shared environment')
            out.append(f'{key}={updates[key]}')
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}={value}')
payload = ('\\n'.join(out) + '\\n').encode('utf-8')
uid, gid, mode = before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)
if bootstrap:
    uid, gid, mode = 0, grp.getgrnam(group_name).gr_gid, 0o640
    if (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) != (uid, gid, mode):
        raise SystemExit('bootstrap environment hardening did not reach root:app-group 0640')
descriptor, temporary_name = tempfile.mkstemp(prefix='.env.build-stamp.', dir=path.parent)
temporary = Path(temporary_name)
try:
    os.fchmod(descriptor, mode)
    os.fchown(descriptor, uid, gid)
    with os.fdopen(descriptor, 'wb', closefd=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.close(descriptor)
    descriptor = -1
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if descriptor >= 0:
        os.close(descriptor)
    temporary.unlink(missing_ok=True)
after = path.lstat()
if (
    not stat.S_ISREG(after.st_mode)
    or after.st_nlink != 1
    or (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) != (uid, gid, mode)
):
    raise SystemExit('atomic application build stamp metadata mismatch')
PY
"

if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' worker-layout-preflight --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --app-user '${REMOTE_APP_USER}' --app-group '${REMOTE_APP_GROUP}' --provision-missing && sudo -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env VKPI_JOB_RESULTS_DIR='${REMOTE_ROOT}/runtime/job-results' HOME=/tmp/vkpi-worker-home XDG_CACHE_HOME=/tmp/vkpi-worker-cache TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' worker-runtime-preflight --root '${REMOTE_ROOT}' --release-path '${REMOTE_RELEASE_DIR}' --app-user '${REMOTE_APP_USER}' --app-group '${REMOTE_APP_GROUP}' --job-results-dir '${REMOTE_ROOT}/runtime/job-results'"
fi

if [ "${DATABASE_RELEASE_STRATEGY}" = "staging-clone" ] \
  || [ "${DATABASE_RELEASE_STRATEGY}" = "reuse-active-clone" ]; then
  STAGING_FINAL_ENV_STATE="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' assert-env --env-file '${REMOTE_ROOT}/.env' --expected-db '${STAGING_CLONE_DATABASE}'")"
  read -r STAGING_FINAL_ENV_DATABASE STAGING_CLONE_ENV_SHA256 < <(printf '%s' "${STAGING_FINAL_ENV_STATE}" | "${PROJECT_ROOT}/.venv/bin/python" -c 'import json,sys; p=json.load(sys.stdin); print(p["database_name"], p["env_sha256"])')
  if [ "${STAGING_FINAL_ENV_DATABASE}" != "${STAGING_CLONE_DATABASE}" ] \
    || ! [[ "${STAGING_CLONE_ENV_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Final release database identity/fingerprint verification failed." >&2
    exit 1
  fi
  if [ "${DATABASE_RELEASE_STRATEGY}" = "staging-clone" ]; then
    if [ "${STAGING_CLONE_ENV_SHA256}" = "${PREDEPLOY_ENV_SHA256}" ]; then
      echo "Staging clone environment fingerprint did not change after database switch." >&2
      exit 1
    fi
    ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' write-receipt --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --source-db '${STAGING_SOURCE_DATABASE}' --target-db '${STAGING_CLONE_DATABASE}' --env-fingerprint-before '${PREDEPLOY_ENV_SHA256}' --env-fingerprint-clone '${STAGING_CLONE_ENV_SHA256}' --migration-version '${LATEST_MIGRATION}' --state migrated-not-activated >/dev/null"
  fi
fi

ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' activate --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}'"
if [ "${DATABASE_RELEASE_STRATEGY}" = "staging-clone" ]; then
  STAGING_DB_CLONE_ACTIVATED=1
  ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' write-receipt --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --source-db '${STAGING_SOURCE_DATABASE}' --target-db '${STAGING_CLONE_DATABASE}' --env-fingerprint-before '${PREDEPLOY_ENV_SHA256}' --env-fingerprint-clone '${STAGING_CLONE_ENV_SHA256}' --migration-version '${LATEST_MIGRATION}' --state activated >/dev/null"
fi

# Install only the already-verified unit payload after current has switched.
# The lane overrides are a reviewed numeric allowlist, never a secret store.
# Stage them in /etc/vkpi and rename on the same filesystem so workers can
# never observe a partial file.  Ownership, mode and byte identity are release
# gates rather than best-effort setup.
ssh "${SSH_TARGET}" "sudo systemctl unmask '${STAGING_REDIS_WORKER_SERVICE}' >/dev/null 2>&1 || true; sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/${REMOTE_SERVICE_UNIT_RELATIVE}' '/etc/systemd/system/${SERVICE_NAME}' && sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/scripts/ops/systemd/vkpi-worker-interactive.service' '/etc/systemd/system/vkpi-worker-interactive.service' && sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/scripts/ops/systemd/vkpi-worker-bulk@.service' '/etc/systemd/system/vkpi-worker-bulk@.service' && sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/scripts/ops/systemd/${STAGING_REDIS_WORKER_SERVICE}' '/etc/systemd/system/${STAGING_REDIS_WORKER_SERVICE}' && sudo install -d -o root -g root -m 0755 '${REMOTE_LANE_OVERRIDE_DIR}' && [ ! -L '${REMOTE_LANE_OVERRIDE_DIR}' ] && [ \"\$(sudo stat -c '%u:%g:%a' '${REMOTE_LANE_OVERRIDE_DIR}')\" = '0:0:755' ] && lane_tmp=\$(sudo mktemp '${REMOTE_LANE_OVERRIDE_DIR}/.vkpi-lane-overrides.env.XXXXXX') && cleanup_lane_tmp() { if [ -n \"\${lane_tmp}\" ]; then sudo rm -f -- \"\${lane_tmp}\"; fi; } && trap cleanup_lane_tmp EXIT && sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}' \"\${lane_tmp}\" && sudo cmp -s '${REMOTE_CURRENT_DIR}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}' \"\${lane_tmp}\" && sudo mv -f -- \"\${lane_tmp}\" '${REMOTE_LANE_OVERRIDE_FILE}' && lane_tmp='' && trap - EXIT && [ ! -L '${REMOTE_LANE_OVERRIDE_FILE}' ] && [ \"\$(sudo stat -c '%u:%g:%a' '${REMOTE_LANE_OVERRIDE_FILE}')\" = '0:0:644' ] && sudo cmp -s '${REMOTE_CURRENT_DIR}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}' '${REMOTE_LANE_OVERRIDE_FILE}' && sudo systemctl daemon-reload"

REDIS_WORKER_RESTART_NOT_BEFORE="$(ssh "${SSH_TARGET}" "date -u +%Y-%m-%dT%H:%M:%SZ")"
REDIS_WORKER_MAIN_PID="$(ssh "${SSH_TARGET}" "sudo systemctl enable --now '${STAGING_REDIS_WORKER_SERVICE}' && systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}' && systemctl is-enabled --quiet '${STAGING_REDIS_WORKER_SERVICE}' && systemctl show --property MainPID --value '${STAGING_REDIS_WORKER_SERVICE}'")"
if ! [[ "${REDIS_WORKER_MAIN_PID}" =~ ^[1-9][0-9]*$ ]]; then
  echo "Dedicated Redis worker systemd MainPID is invalid after restart." >&2
  exit 1
fi

ssh "${SSH_TARGET}" "sudo systemctl restart '${SERVICE_NAME}' && systemctl is-active '${SERVICE_NAME}' && cd '${REMOTE_CURRENT_DIR}' && for attempt in \$(seq 1 30); do if sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/fetch_runtime_health.py --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env' >/dev/null; then exit 0; fi; sleep 1; done; echo 'authenticated health check failed after service restart: ${HEALTH_URL}' >&2; exit 1"

ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && for attempt in \$(seq 1 60); do if sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/fetch_runtime_health.py --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env' | env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/verify_redis_worker_health.py --expected-head '${LOCAL_GIT_SHA}' --expected-count 1 --expected-main-pid '${REDIS_WORKER_MAIN_PID}' --min-ready-sequence 3 --worker-not-before '${REDIS_WORKER_RESTART_NOT_BEFORE}' --max-age-seconds '${MAX_WORKER_AGE_SECONDS}' >/dev/null; then exit 0; fi; sleep 2; done; echo 'dedicated Redis worker failed strict readiness after restart: ${HEALTH_URL}' >&2; exit 1"

# Worker restart is mandatory for deployment acceptance.  Cloud capacity is a
# reviewed seven-service systemd fleet (one interactive + six batch).  Never
# fall back to the legacy singleton launcher: it would leave old systemd workers
# executing the in-place rsync payload and let one fresh heartbeat mask them.
WORKER_RESTART_NOT_BEFORE="$(ssh "${SSH_TARGET}" "date -u +%Y-%m-%dT%H:%M:%SZ")"
ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && bash scripts/stop_worker.sh >/dev/null && [ ! -f '${REMOTE_ROOT}/runtime/worker.pid' ] && for unit in ${WORKER_SYSTEMD_UNIT_ARGS}; do systemctl cat -- \"\${unit}\" >/dev/null; done && sudo systemctl restart ${WORKER_SYSTEMD_UNIT_ARGS} && for unit in ${WORKER_SYSTEMD_UNIT_ARGS}; do systemctl is-active --quiet \"\${unit}\"; done && for attempt in \$(seq 1 60); do if sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/fetch_runtime_health.py --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env' >/tmp/vkpi-health.json && env PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path

data = json.loads(Path('/tmp/vkpi-health.json').read_text())
trust = data.get('trust', {})
fleet = trust.get('worker_fleet') or {}
workers = [row for row in (fleet.get('workers') or []) if row.get('online') is True]
not_before = datetime.fromisoformat('${WORKER_RESTART_NOT_BEFORE}'.replace('Z', '+00:00'))

def parsed(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return moment if moment.tzinfo is not None else None
    except (TypeError, ValueError):
        return None

if (
    trust.get('worker_online') is True
    and fleet.get('online_count') == ${EXPECTED_WORKER_COUNT}
    and len(workers) == ${EXPECTED_WORKER_COUNT}
    and fleet.get('all_worker_sha_aligned') is True
    and {'interactive', 'batch'}.issubset(set(fleet.get('lane_coverage') or []))
    and all(parsed(row.get('started_at')) and parsed(row.get('started_at')) >= not_before for row in workers)
):
    raise SystemExit(0)
raise SystemExit(1)
PY
then exit 0; fi; sleep 2; done; echo 'exact seven-service worker fleet failed readiness after restart: ${HEALTH_URL}' >&2; exit 1"

# Remote acceptance is deliberately separate from the pre-deploy local gate:
# fetch the post-restart JSON remotely, then validate it with the local, reviewed
# validator and explicit expected HEAD/migration/worker freshness parameters.
if ! REMOTE_HEALTH_JSON="$(ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/fetch_runtime_health.py --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env'")"; then
  echo "Failed to fetch post-restart remote health JSON: ${HEALTH_URL}" >&2
  exit 1
fi
if [ -z "${REMOTE_HEALTH_JSON}" ]; then
  echo "Post-restart remote health JSON is empty." >&2
  exit 1
fi
if ! printf '%s' "${REMOTE_HEALTH_JSON}" | "${PROJECT_ROOT}/.venv/bin/python" \
  "${PROJECT_ROOT}/scripts/verify_runtime_health.py" \
  --strict-deploy \
  --expected-head "${LOCAL_GIT_SHA}" \
  --expected-migration "${LATEST_MIGRATION}" \
  --require-worker \
  --expected-worker-count "${EXPECTED_WORKER_COUNT}" \
  --worker-not-before "${WORKER_RESTART_NOT_BEFORE}" \
  --max-worker-age-seconds "${MAX_WORKER_AGE_SECONDS}"; then
  echo "Post-restart remote runtime trust validation failed; deployment is not accepted." >&2
  exit 1
fi
if ! printf '%s' "${REMOTE_HEALTH_JSON}" | "${PROJECT_ROOT}/.venv/bin/python" \
  "${PROJECT_ROOT}/scripts/verify_redis_worker_health.py" \
  --expected-head "${LOCAL_GIT_SHA}" \
  --expected-count 1 \
  --expected-main-pid "${REDIS_WORKER_MAIN_PID}" \
  --min-ready-sequence 3 \
  --worker-not-before "${REDIS_WORKER_RESTART_NOT_BEFORE}" \
  --max-age-seconds "${MAX_WORKER_AGE_SECONDS}" >/dev/null; then
  echo "Post-restart Redis worker trust validation failed; deployment is not accepted." >&2
  exit 1
fi
if [ "${DATABASE_RELEASE_STRATEGY}" = "staging-clone" ] \
  || [ "${DATABASE_RELEASE_STRATEGY}" = "reuse-active-clone" ]; then
  POST_RESTART_DB_STATE="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_CURRENT_DIR}/scripts/ops/staging_db_clone.py' prove-active-source --root '${REMOTE_ROOT}' --expected-db '${STAGING_CLONE_DATABASE}'")"
  read -r POST_RESTART_DATABASE POST_RESTART_ENV_SHA256 POST_RESTART_DB_OWNER POST_RESTART_ACTIVE_RELEASE < <(printf '%s' "${POST_RESTART_DB_STATE}" | "${PROJECT_ROOT}/.venv/bin/python" -c 'import json,sys; p=json.load(sys.stdin); print(p["database_name"], p["env_sha256"], p["database_owner_release_id"], p["active_release_id"])')
  if [ "${DATABASE_RELEASE_STRATEGY}" = "staging-clone" ]; then
    EXPECTED_POST_RESTART_DB_OWNER="${RELEASE_ID}"
  else
    EXPECTED_POST_RESTART_DB_OWNER="${DATABASE_OWNER_RELEASE_ID}"
  fi
  if [ "${POST_RESTART_DATABASE}" != "${STAGING_CLONE_DATABASE}" ] \
    || [ "${POST_RESTART_ENV_SHA256}" != "${STAGING_CLONE_ENV_SHA256}" ] \
    || [ "${POST_RESTART_DB_OWNER}" != "${EXPECTED_POST_RESTART_DB_OWNER}" ] \
    || [ "${POST_RESTART_ACTIVE_RELEASE}" != "${RELEASE_ID}" ]; then
    echo "Post-restart release database lineage/fingerprint verification failed." >&2
    exit 1
  fi
fi

# Bind log-canary receipts to the complete fleet without storing any raw nonce.
# The strict validator above has already proven seven unique, fresh nonce hashes;
# hash their sorted set into one stable deployment-scoped fleet identity.
WORKER_BOOT_NONCE_SHA256="$(printf '%s' "${REMOTE_HEALTH_JSON}" | "${PROJECT_ROOT}/.venv/bin/python" -c 'import hashlib,json,sys; data=json.load(sys.stdin); rows=(data.get("trust",{}).get("worker_fleet",{}).get("workers") or []); nonces=sorted(str(row.get("boot_nonce_sha256") or "") for row in rows if row.get("online") is True); print(hashlib.sha256(("\n".join(nonces)).encode()).hexdigest())')"
if ! [[ "${WORKER_BOOT_NONCE_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Failed to derive the reviewed worker-fleet boot binding." >&2
  exit 1
fi

# Establish the remote log boundary only after the exact app/worker restart has
# been proven.  The hashed fleet nonce set and not-before timestamp bind the
# baseline to this deployment without persisting any raw nonce.
if [ "${POST_DEPLOY_EVIDENCE_OWNED}" = "1" ]; then
  mkdir -p -- "$(dirname "${POST_DEPLOY_EVIDENCE_DIR}")"
  if [ -e "${POST_DEPLOY_EVIDENCE_DIR}" ] || [ -L "${POST_DEPLOY_EVIDENCE_DIR}" ]; then
    echo "Refusing to overwrite existing default post-deploy evidence: ${POST_DEPLOY_EVIDENCE_DIR}" >&2
    exit 1
  fi
  mkdir -m 0700 -- "${POST_DEPLOY_EVIDENCE_DIR}"
else
  mkdir -p -- "${POST_DEPLOY_EVIDENCE_DIR}"
  if [ -L "${POST_DEPLOY_EVIDENCE_DIR}" ] || [ ! -d "${POST_DEPLOY_EVIDENCE_DIR}" ]; then
    echo "VKPI_POST_DEPLOY_EVIDENCE_DIR must be a real local directory, not a symlink." >&2
    exit 1
  fi
fi
chmod 700 "${POST_DEPLOY_EVIDENCE_DIR}"

LOCAL_LOG_BASELINE="${POST_DEPLOY_EVIDENCE_DIR}/runtime-log-baseline.json"
LOCAL_LOG_CANARY="${POST_DEPLOY_EVIDENCE_DIR}/runtime-log-canary.json"
LOCAL_ACCEPTANCE_REPORT="${POST_DEPLOY_EVIDENCE_DIR}/release-acceptance.json"
LOCAL_BROWSER_CAPTURE="${POST_DEPLOY_EVIDENCE_DIR}/browser-capture.json"
LOCAL_BROWSER_REPORT="${POST_DEPLOY_EVIDENCE_DIR}/browser-gate-report.json"
REMOTE_LOG_BASELINE="/tmp/vkpi-runtime-log-baseline-${WORKER_BOOT_NONCE_SHA256}.json"
REMOTE_ACCEPTANCE_REPORT="/tmp/vkpi-release-acceptance-${WORKER_BOOT_NONCE_SHA256}.json"

# The reviewed systemd units write to journald, not the legacy runtime/logs
# files.  Bind a cursor to the exact web + seven Apify + Redis unit filter.
ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/audit_systemd_journal_media_log_leaks.py ${JOURNAL_SYSTEMD_UNIT_FLAGS} --worker-boot-nonce-sha256 '${WORKER_BOOT_NONCE_SHA256}' --worker-not-before '${WORKER_RESTART_NOT_BEFORE}' --compact > '${REMOTE_LOG_BASELINE}' && chmod 600 '${REMOTE_LOG_BASELINE}'"
ssh "${SSH_TARGET}" "cat -- '${REMOTE_LOG_BASELINE}'" >"${LOCAL_LOG_BASELINE}"

# Repeat the complete manifest-driven read-only API acceptance against the
# restarted remote service; a local pre-deploy 41/41+ receipt is not transferable.
ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/local_release_acceptance.py --base-url '${REMOTE_ACCEPTANCE_BASE_URL}' --json-out '${REMOTE_ACCEPTANCE_REPORT}' >/dev/null"
ssh "${SSH_TARGET}" "cat -- '${REMOTE_ACCEPTANCE_REPORT}'" >"${LOCAL_ACCEPTANCE_REPORT}"
"${PROJECT_ROOT}/.venv/bin/python" - "${LOCAL_ACCEPTANCE_REPORT}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
overall = payload.get("overall") or {}
coverage = payload.get("coverage") or {}
required_total = overall.get("required_total")
if (
    overall.get("pass") is not True
    or not isinstance(required_total, int)
    or isinstance(required_total, bool)
    or required_total < 41
    or overall.get("required_passed") != required_total
    or coverage.get("missing_board_families") not in ([], None)
):
    raise SystemExit("post-restart remote read-only acceptance is incomplete")
print(f"[deploy] post-restart remote acceptance passed: {required_total}/{required_total}")
PY

# A caller may supply an explicit reviewed token.  Otherwise, and only after
# strict post-restart web + seven-worker identity and read-only acceptance have
# passed, mint one 60-300s admin JWT inside the active remote release.  The
# production JWT secret and DB identity never leave the host: SSH stdout carries
# only the short-lived token directly into this shell variable, never argv,
# evidence, logs, or a file.  Any lookup/signing failure blocks the deployment.
if [ -z "${POST_DEPLOY_BROWSER_TOKEN}" ]; then
  if ! POST_DEPLOY_BROWSER_TOKEN="$(ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i HOME=/tmp XDG_CACHE_HOME=/tmp TMPDIR=/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 LOG_LEVEL=CRITICAL ENVIRONMENT=production V2_PRODUCTION_MODE=1 APP_ROLE=admin-web '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/mint_browser_gate_token.py --ttl-seconds '${BROWSER_GATE_TOKEN_TTL_SECONDS}'")"; then
    echo "Remote short-lived browser gate token mint failed; deployment is not accepted." >&2
    exit 1
  fi
fi
if ! [[ "${POST_DEPLOY_BROWSER_TOKEN}" =~ ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]]; then
  echo "Browser gate token is not a single compact JWT; deployment is not accepted." >&2
  POST_DEPLOY_BROWSER_TOKEN=""
  exit 1
fi

# Use an owned, clean, extension-free Chrome process.  The controller consumes
# the token from its environment, strips it from Chrome, and virtualizes the
# frontend token lookup in renderer memory without persistent browser storage.
BROWSER_CAPTURE_STATUS=0
VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS="${BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS}" \
VKPI_BROWSER_GATE_TOKEN="${POST_DEPLOY_BROWSER_TOKEN}" node \
  "${PROJECT_ROOT}/scripts/capture_browser_console_cdp.mjs" \
  --url "${POST_DEPLOY_BROWSER_URL}" \
  --output "${LOCAL_BROWSER_CAPTURE}" \
  --settle-ms "${BROWSER_GATE_SETTLE_MS}" \
  --page-settle-ms "${BROWSER_GATE_PAGE_SETTLE_MS}" \
  --page-timeout-ms "${BROWSER_GATE_PAGE_TIMEOUT_MS}" \
  --chrome "${POST_DEPLOY_CHROME_PATH}" || BROWSER_CAPTURE_STATUS=$?
POST_DEPLOY_BROWSER_TOKEN=""
if [ "${BROWSER_CAPTURE_STATUS}" -ne 0 ]; then
  echo "Post-restart authenticated browser capture failed." >&2
  exit "${BROWSER_CAPTURE_STATUS}"
fi
"${PROJECT_ROOT}/.venv/bin/python" \
  "${PROJECT_ROOT}/scripts/verify_browser_console_capture.py" \
  --input "${LOCAL_BROWSER_CAPTURE}" \
  --json-out "${LOCAL_BROWSER_REPORT}"

# Scan only bytes appended after the bound baseline.  Missing baseline coverage,
# truncation, unread tails, or any new sensitive URL/credential finding fails the
# deployment.  The standalone validator re-reads both receipts instead of
# trusting the scanner exit code alone.
if ! ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/audit_systemd_journal_media_log_leaks.py ${JOURNAL_SYSTEMD_UNIT_FLAGS} --baseline-state '${REMOTE_LOG_BASELINE}' --worker-boot-nonce-sha256 '${WORKER_BOOT_NONCE_SHA256}' --worker-not-before '${WORKER_RESTART_NOT_BEFORE}' --require-complete-baseline --compact --fail-on-new" >"${LOCAL_LOG_CANARY}"; then
  echo "Post-restart remote runtime log canary failed." >&2
  exit 1
fi
"${PROJECT_ROOT}/.venv/bin/python" \
  "${PROJECT_ROOT}/scripts/verify_runtime_journal_canary.py" \
  --baseline-state "${LOCAL_LOG_BASELINE}" \
  --canary-report "${LOCAL_LOG_CANARY}" \
  --expected-worker-boot-nonce-sha256 "${WORKER_BOOT_NONCE_SHA256}" \
  --worker-not-before "${WORKER_RESTART_NOT_BEFORE}" \
  ${JOURNAL_SYSTEMD_UNIT_FLAGS}
chmod 600 "${LOCAL_LOG_BASELINE}" "${LOCAL_LOG_CANARY}" \
  "${LOCAL_ACCEPTANCE_REPORT}" "${LOCAL_BROWSER_CAPTURE}" "${LOCAL_BROWSER_REPORT}"

LOCAL_ASSET="$(grep -o 'app-[A-Za-z0-9_-]*\.js' frontend/dist/index.html | head -1)"
REMOTE_ASSET="$(ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && grep -o 'app-[A-Za-z0-9_-]*\\.js' frontend/dist/index.html | head -1")"

echo "local_asset=${LOCAL_ASSET}"
echo "remote_asset=${REMOTE_ASSET}"
test "${LOCAL_ASSET}" = "${REMOTE_ASSET}"

# Public search-surface gate. A deployment is not accepted while either public
# hostname still exposes legacy internal descriptions or misses noindex headers.
PRIVATE_SURFACE_URLS="${PRIVATE_SURFACE_URLS:-https://viltroxtest.com https://www.viltroxtest.com}"
read -r -a PRIVATE_SURFACE_URL_LIST <<< "${PRIVATE_SURFACE_URLS}"
"${PROJECT_ROOT}/.venv/bin/python" "${PROJECT_ROOT}/scripts/verify_private_surface_live.py" \
  "${PRIVATE_SURFACE_URL_LIST[@]}"

restore_remote_sync_unit_state
if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_CURRENT_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py' write-success-marker --marker '${FIRST_ATOMIC_BOOTSTRAP_SUCCESS_MARKER}' --plan-sha256 '${FIRST_ATOMIC_BOOTSTRAP_PLAN_SHA256}' --release-id '${RELEASE_ID}' --git-sha '${LOCAL_GIT_SHA}' >/dev/null"
fi
DEPLOY_ACCEPTED=1
echo "[deploy] retained post-restart evidence: ${POST_DEPLOY_EVIDENCE_DIR}"
