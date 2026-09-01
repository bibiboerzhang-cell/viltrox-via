#!/usr/bin/env bash
set -euo pipefail

# A release bearer must never be expanded into an operator/CI xtrace stream.
# Disable an inherited ``bash -x`` before any child process or credential mint;
# this safety setting is intentionally monotonic for the rest of the deploy.
case "$-" in
  *x*) set +x ;;
esac

if [ -n "${VKPI_SAFE_PYTHON_PROFILE:-}" ]; then
  echo "GitHub static Python profile is forbidden for deployment." >&2
  exit 1
fi

# Production browser credentials are minted only after the restarted remote
# runtime has passed API acceptance.  Reject and erase caller-supplied values
# before this script runs even one child process; exported shell attributes
# must never carry an admin bearer through git/build/SSH/preflight commands.
if [ -n "${VKPI_BROWSER_GATE_TOKEN:-}" ] \
  || [ -n "${POST_DEPLOY_BROWSER_TOKEN:-}" ]; then
  unset VKPI_BROWSER_GATE_TOKEN POST_DEPLOY_BROWSER_TOKEN
  echo "Caller-supplied browser gate tokens are forbidden; production mints a short-lived token remotely." >&2
  exit 1
fi
unset VKPI_BROWSER_GATE_TOKEN POST_DEPLOY_BROWSER_TOKEN
POST_DEPLOY_BROWSER_TOKEN=""

# These loopback endpoints are interpolated into several reviewed remote shell
# commands.  Validate caller input before the first child process (and therefore
# before any possible SSH wrapper invocation), then bind the production values
# to constants. Exact equality also rejects quotes, whitespace, path changes,
# alternate hosts, and alternate ports without reflecting hostile input.
PRODUCTION_HEALTH_URL="http://127.0.0.1:8001/health"
PRODUCTION_REMOTE_ACCEPTANCE_BASE_URL="http://127.0.0.1:8001"
PRODUCTION_SSH_TARGET="viltrox"
PRODUCTION_SYNC_SERVICE="vkpi-sync-daily.service"
PRODUCTION_SYNC_TIMER="vkpi-sync-daily.timer"
PRODUCTION_SYNC_UNIT_RELATIVE="scripts/ops/systemd/vkpi-sync-daily.service"
PRODUCTION_CHROME_APP="/Applications/Google Chrome.app"
PRODUCTION_CHROME_PATH="${PRODUCTION_CHROME_APP}/Contents/MacOS/Google Chrome"
BROWSER_GATE_CONTROLLER_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if { [ "${HEALTH_URL+x}" = x ] && [ "${HEALTH_URL}" != "${PRODUCTION_HEALTH_URL}" ]; } \
  || { [ "${VKPI_REMOTE_ACCEPTANCE_BASE_URL+x}" = x ] \
    && [ "${VKPI_REMOTE_ACCEPTANCE_BASE_URL}" != "${PRODUCTION_REMOTE_ACCEPTANCE_BASE_URL}" ]; } \
  || { [ "${SSH_TARGET+x}" = x ] && [ "${SSH_TARGET}" != "${PRODUCTION_SSH_TARGET}" ]; } \
  || { [ "${SYNC_SERVICE+x}" = x ] && [ "${SYNC_SERVICE}" != "${PRODUCTION_SYNC_SERVICE}" ]; } \
  || { [ "${SYNC_TIMER+x}" = x ] && [ "${SYNC_TIMER}" != "${PRODUCTION_SYNC_TIMER}" ]; } \
  || { [ "${REMOTE_SYNC_SERVICE_UNIT_RELATIVE+x}" = x ] \
    && [ "${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}" != "${PRODUCTION_SYNC_UNIT_RELATIVE}" ]; } \
  || { [ "${VKPI_CHROME_PATH+x}" = x ] \
    && [ "${VKPI_CHROME_PATH}" != "${PRODUCTION_CHROME_PATH}" ]; }; then
  echo "Production host, health, sync, and browser identities must remain exact reviewed values." >&2
  exit 1
fi

# The deploy process prepends private immutable ``ssh``/``scp`` wrapper links to
# PATH so every direct call, child shell, Python subprocess, and rsync remote
# shell shares one ControlMaster.  Wrapper mode exits before any deploy gate or
# repository read.
run_deploy_ssh_transport_wrapper() {
  local tool="${0##*/}" real_binary=""
  local control_path="${VKPI_DEPLOY_SSH_CONTROL_PATH:-}"
  local connect_timeout="${VKPI_DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS:-}"
  local control_persist="${VKPI_DEPLOY_SSH_CONTROL_PERSIST_SECONDS:-}"
  local fail_closed_proxy="${VKPI_DEPLOY_SSH_FAIL_CLOSED_PROXY:-}"
  local deploy_lock_required="${VKPI_DEPLOY_REMOTE_LOCK_REQUIRED:-0}"
  local deploy_lock_holder_pid="${VKPI_DEPLOY_REMOTE_LOCK_HOLDER_PID:-}"
  local deploy_lock_status_file="${VKPI_DEPLOY_REMOTE_LOCK_STATUS_FILE:-}"

  case "${tool}" in
    ssh) real_binary="${VKPI_DEPLOY_REAL_SSH:-}" ;;
    scp) real_binary="${VKPI_DEPLOY_REAL_SCP:-}" ;;
    *)
      echo "Deployment SSH transport wrapper must be invoked as ssh or scp." >&2
      exit 64
      ;;
  esac
  if [ "${real_binary#/}" = "${real_binary}" ] || [ ! -x "${real_binary}" ]; then
    echo "Deployment SSH transport wrapper has no trusted real ${tool} binary." >&2
    exit 64
  fi
  if [ "${control_path#/}" = "${control_path}" ] \
    || [ "${fail_closed_proxy#/}" = "${fail_closed_proxy}" ] \
    || [ ! -x "${fail_closed_proxy}" ] \
    || ! [[ "${connect_timeout}" =~ ^[1-9][0-9]*$ ]] \
    || ! [[ "${control_persist}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Deployment SSH transport wrapper configuration is invalid." >&2
    exit 64
  fi
  if [ "${deploy_lock_required}" != "0" ]; then
    if [ "${deploy_lock_required}" != "1" ] \
      || ! [[ "${deploy_lock_holder_pid}" =~ ^[1-9][0-9]*$ ]] \
      || [ "${deploy_lock_status_file#/}" = "${deploy_lock_status_file}" ] \
      || [ -s "${deploy_lock_status_file}" ] \
      || ! kill -0 "${deploy_lock_holder_pid}" 2>/dev/null; then
      echo "Deployment mutex holder is no longer alive; refusing remote operation." >&2
      exit 75
    fi
  fi

  exec "${real_binary}" \
    -o BatchMode=yes \
    -o ControlMaster=no \
    -o "ControlPersist=${control_persist}" \
    -o "ControlPath=${control_path}" \
    -o "ProxyCommand=${fail_closed_proxy}" \
    -o ConnectionAttempts=1 \
    -o "ConnectTimeout=${connect_timeout}" \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    "$@"
}

if [ "${VKPI_DEPLOY_SSH_WRAPPER_MODE:-0}" = "1" ]; then
  run_deploy_ssh_transport_wrapper "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

# Every local controller-side Python command must enter through the no-site
# router.  ``-I`` alone is insufficient for a venv launcher because it still
# imports executable ``.pth`` files before the reviewed command starts.  Keep
# the physical interpreter identity explicit for the wrapper and for the one
# candidate-runtime argument whose downstream controller validates it.
LOCAL_SAFE_PYTHON="${PROJECT_ROOT}/scripts/ops/safe_python.sh"
DEPLOY_PHYSICAL_PYTHON="${VKPI_SAFE_PYTHON_REAL:-${PROJECT_ROOT}/.venv/bin/python}"
export VKPI_SAFE_PYTHON_REAL="${DEPLOY_PHYSICAL_PYTHON}"

# Feed reviewed inline controller programs to the router on stdin.  When a
# caller pipes JSON, preserve that data on fd 3 so it cannot be confused with
# the program stream; inline programs must read it with ``os.fdopen(3)``.
run_local_python_program() {
  local program="$1"
  shift
  "${LOCAL_SAFE_PYTHON}" - "$@" 3<&0 <<<"${program}"
}

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
if [ -z "${LOCAL_GIT_BRANCH}" ]; then
  LOCAL_GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
fi
DEPLOY_CANDIDATE_DIR="${VKPI_DEPLOY_CANDIDATE_DIR:-}"
DEPLOY_CANDIDATE_MANIFEST="${VKPI_DEPLOY_CANDIDATE_MANIFEST:-}"
if [ -z "${DEPLOY_CANDIDATE_DIR}" ] || [ -z "${DEPLOY_CANDIDATE_MANIFEST}" ]; then
  echo "VKPI_DEPLOY_CANDIDATE_DIR and VKPI_DEPLOY_CANDIDATE_MANIFEST are mandatory." >&2
  exit 1
fi
DEPLOY_VERIFIER_BUNDLE_DIR=""
DEPLOY_VERIFIER_BUNDLE_SHA256=""
TRUSTED_CANDIDATE_VERIFIER=""
TRUSTED_RUNTIME_ADMISSION=""
DEPLOY_VERIFIER_BUNDLE_READY=0
RESCUE_ROLLBACK_CANDIDATE_DIR="${VKPI_RESCUE_ROLLBACK_CANDIDATE_DIR:-}"
RESCUE_ROLLBACK_CANDIDATE_MANIFEST="${VKPI_RESCUE_ROLLBACK_CANDIDATE_MANIFEST:-}"
RESCUE_ROLLBACK_CONFIRM="${VKPI_RESCUE_ROLLBACK_CONFIRM:-}"
unset VKPI_RESCUE_ROLLBACK_CONFIRM
RESCUE_ROLLBACK_MODE=0
if [ -n "${RESCUE_ROLLBACK_CANDIDATE_DIR}" ] \
  || [ -n "${RESCUE_ROLLBACK_CANDIDATE_MANIFEST}" ] \
  || [ -n "${RESCUE_ROLLBACK_CONFIRM}" ]; then
  if [ -z "${RESCUE_ROLLBACK_CANDIDATE_DIR}" ] \
    || [ -z "${RESCUE_ROLLBACK_CANDIDATE_MANIFEST}" ] \
    || [ -z "${RESCUE_ROLLBACK_CONFIRM}" ]; then
    echo "VKPI_RESCUE_ROLLBACK_CANDIDATE_DIR, VKPI_RESCUE_ROLLBACK_CANDIDATE_MANIFEST, and VKPI_RESCUE_ROLLBACK_CONFIRM must be supplied together." >&2
    exit 1
  fi
  RESCUE_ROLLBACK_MODE=1
fi
RESCUE_ROLLBACK_CANDIDATE_BRANCH=""

verify_deploy_candidate() {
  local verifier="${PROJECT_ROOT}/scripts/ops/freeze_worktree_candidate.py"
  if [ "${DEPLOY_VERIFIER_BUNDLE_READY}" = "1" ]; then
    verifier="${TRUSTED_CANDIDATE_VERIFIER}"
    run_sealed_controller_python \
      "${verifier}" \
      verify-deploy-source \
      --manifest "${DEPLOY_CANDIDATE_MANIFEST}" \
      --snapshot "${DEPLOY_CANDIDATE_DIR}" \
      --expected-head "${LOCAL_GIT_SHA}" \
      --expected-branch "${LOCAL_GIT_BRANCH}" >/dev/null
    return
  fi
  PYTHONDONTWRITEBYTECODE=1 "${LOCAL_SAFE_PYTHON}" -I -B \
    "${verifier}" \
    verify-deploy-source \
    --manifest "${DEPLOY_CANDIDATE_MANIFEST}" \
    --snapshot "${DEPLOY_CANDIDATE_DIR}" \
    --expected-head "${LOCAL_GIT_SHA}" \
    --expected-branch "${LOCAL_GIT_BRANCH}" >/dev/null
}

compute_deploy_verifier_bundle_digest() {
  local root="$1"
  PYTHONDONTWRITEBYTECODE=1 "${LOCAL_SAFE_PYTHON}" -I -B - "${root}" <<'PY'
import hashlib
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
paths = {
    Path("scripts/ops/candidate_physical_tree.py"): 0o400,
    Path("scripts/ops/controller_static_receipt.py"): 0o400,
    Path("scripts/ops/controlled_candidate_process.py"): 0o400,
    Path("scripts/ops/deploy_gate_runtime.py"): 0o400,
    Path("scripts/ops/deploy_runtime_admission.py"): 0o500,
    Path("scripts/ops/freeze_worktree_candidate.py"): 0o500,
    Path("scripts/ops/freeze_worktree_contract.py"): 0o400,
    Path("scripts/ops/freeze_git_bridge.py"): 0o400,
    Path("scripts/ops/freeze_deploy_gate.py"): 0o400,
    Path("scripts/ops/freeze_phase_runtime.py"): 0o400,
    Path("scripts/ops/legacy_to_atomic_preflight.py"): 0o500,
    Path("scripts/ops/legacy_to_atomic_preflight_report.py"): 0o400,
    Path("scripts/ops/legacy_to_atomic_preflight_transport.py"): 0o400,
    Path("scripts/ops/strict_runtime_seatbelt.py"): 0o400,
    Path("scripts/ops/trusted_git.py"): 0o400,
    Path("scripts/ops/trusted_npm_audit.py"): 0o400,
    Path("scripts/ops/verify_legacy_bootstrap_anchor.py"): 0o500,
    Path("scripts/stdout_utils.py"): 0o400,
    Path("scripts/verify_redis_worker_health.py"): 0o500,
    Path("scripts/verify_runtime_health.py"): 0o500,
}
expected_directories = {Path("scripts"), Path("scripts/ops")}
observed_directories = set()
observed_files = set()
pending = [(root, Path())]

while pending:
    directory, relative_directory = pending.pop()
    with os.scandir(directory) as entries:
        for entry in entries:
            relative = relative_directory / entry.name
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                observed_directories.add(relative)
                pending.append((Path(entry.path), relative))
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise SystemExit("trusted verifier file link count mismatch")
                observed_files.add(relative)
            else:
                raise SystemExit("trusted verifier bundle contains an unsupported node")

if observed_directories != expected_directories:
    raise SystemExit("trusted verifier directory inventory mismatch")
if observed_files != set(paths):
    raise SystemExit("trusted verifier file inventory mismatch")

for directory in (root, root / "scripts", root / "scripts" / "ops"):
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise SystemExit("trusted verifier directory mode/type mismatch")
    if info.st_uid != os.geteuid():
        raise SystemExit("trusted verifier directory owner mismatch")
digest = hashlib.sha256()
for relative, expected_mode in sorted(paths.items(), key=lambda item: item[0].as_posix()):
    path = root / relative
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != expected_mode:
        raise SystemExit("trusted verifier file mode/type mismatch")
    if info.st_uid != os.geteuid():
        raise SystemExit("trusted verifier file owner mismatch")
    digest.update(relative.as_posix().encode("utf-8") + b"\0")
    digest.update(path.read_bytes())
print(digest.hexdigest())
PY
}

verify_deploy_verifier_bundle() {
  local observed
  if [ "${DEPLOY_VERIFIER_BUNDLE_READY}" != "1" ] \
    || [ -z "${DEPLOY_VERIFIER_BUNDLE_DIR}" ] \
    || [ -z "${DEPLOY_VERIFIER_BUNDLE_SHA256}" ] \
    || [ -z "${TRUSTED_CANDIDATE_VERIFIER}" ] \
    || [ -z "${TRUSTED_RUNTIME_ADMISSION}" ]; then
    echo "Trusted deploy candidate verifier bundle is not ready." >&2
    return 1
  fi
  observed="$(compute_deploy_verifier_bundle_digest "${DEPLOY_VERIFIER_BUNDLE_DIR}")" \
    || return 1
  if [ "${observed}" != "${DEPLOY_VERIFIER_BUNDLE_SHA256}" ]; then
    echo "Trusted deploy candidate verifier bundle digest changed." >&2
    return 1
  fi
}

run_sealed_controller_python() {
  local script="$1" rc=0
  shift
  case "${script}" in
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/freeze_worktree_candidate.py"|\
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/deploy_runtime_admission.py"|\
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/legacy_to_atomic_preflight.py"|\
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py"|\
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/verify_runtime_health.py"|\
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/verify_redis_worker_health.py") ;;
    *)
      echo "Refusing an unreviewed sealed controller Python entrypoint: ${script}" >&2
      return 126
      ;;
  esac
  verify_deploy_verifier_bundle || return 1
  # This private bundle intentionally lives outside the candidate tree, so the
  # candidate-root router must reject it.  Run its exact allowlisted, mode-0400/
  # 0500 entrypoint with the same physical interpreter but with site disabled;
  # the bundle digest is checked immediately before and after execution.
  PYTHONDONTWRITEBYTECODE=1 "${DEPLOY_PHYSICAL_PYTHON}" -I -S -B \
    "${script}" "$@" || rc=$?
  verify_deploy_verifier_bundle || return 1
  return "${rc}"
}

run_frozen_candidate_python() {
  local script="$1" rc=0
  shift
  case "${script}" in
    "${DEPLOY_CANDIDATE_DIR}/scripts/ops/fetch_runtime_health.py"|\
    "${DEPLOY_CANDIDATE_DIR}/scripts/ops/legacy_to_atomic_preflight.py"|\
    "${DEPLOY_CANDIDATE_DIR}/scripts/ops/staging_db_clone.py"|\
    "${DEPLOY_CANDIDATE_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py"|\
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_browser_console_capture.py"|\
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_private_surface_live.py"|\
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_redis_worker_health.py"|\
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_runtime_health.py"|\
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_runtime_journal_canary.py") ;;
    *)
      echo "Refusing an unreviewed frozen-candidate Python entrypoint: ${script}" >&2
      return 126
      ;;
  esac
  verify_deploy_candidate || return 1
  # The router owns dependency setup and startup isolation.  The bootstrap is
  # reviewed controller code; fd 3 preserves any JSON piped to the validator.
  "${LOCAL_SAFE_PYTHON}" - "${DEPLOY_CANDIDATE_DIR}" "${script}" "$@" \
    3<&0 <<'PY' || rc=$?
import os
from pathlib import Path
import runpy
import sys

root = Path(sys.argv[1]).resolve(strict=True)
script = Path(sys.argv[2]).resolve(strict=True)
if not script.is_relative_to(root):
    raise SystemExit("frozen candidate entrypoint escaped its verified root")
os.dup2(3, 0)
sys.path[:0] = [
    str(script.parent), str(root), str(root / "scripts"), str(root / "backend")
]
sys.argv = [str(script), *sys.argv[3:]]
runpy.run_path(str(script), run_name="__main__")
PY
  verify_deploy_candidate || return 1
  return "${rc}"
}

seal_deploy_verifier_bundle() {
  local relative source candidate target
  local tmp_base="${TMPDIR:-/tmp}"
  DEPLOY_VERIFIER_BUNDLE_DIR="$(mktemp -d "${tmp_base%/}/vkpi-deploy-verifier.XXXXXX")" || return 1
  chmod 700 "${DEPLOY_VERIFIER_BUNDLE_DIR}"
  install -d -m 0700 \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops"
  for relative in \
    scripts/ops/candidate_physical_tree.py \
    scripts/ops/controller_static_receipt.py \
    scripts/ops/controlled_candidate_process.py \
    scripts/ops/deploy_gate_runtime.py \
    scripts/ops/deploy_runtime_admission.py \
    scripts/ops/freeze_worktree_candidate.py \
    scripts/ops/freeze_worktree_contract.py \
    scripts/ops/freeze_git_bridge.py \
    scripts/ops/freeze_deploy_gate.py \
    scripts/ops/freeze_phase_runtime.py \
    scripts/ops/legacy_to_atomic_preflight.py \
    scripts/ops/legacy_to_atomic_preflight_report.py \
    scripts/ops/legacy_to_atomic_preflight_transport.py \
    scripts/ops/strict_runtime_seatbelt.py \
    scripts/ops/trusted_git.py \
    scripts/ops/trusted_npm_audit.py \
    scripts/ops/verify_legacy_bootstrap_anchor.py \
    scripts/stdout_utils.py \
    scripts/verify_redis_worker_health.py \
    scripts/verify_runtime_health.py; do
    source="${PROJECT_ROOT}/${relative}"
    candidate="${DEPLOY_CANDIDATE_DIR}/${relative}"
    target="${DEPLOY_VERIFIER_BUNDLE_DIR}/${relative}"
    if [ ! -f "${source}" ] || [ -L "${source}" ] \
      || [ ! -f "${candidate}" ] || [ -L "${candidate}" ]; then
      echo "Trusted deploy verifier source is missing or unsafe: ${relative}" >&2
      return 1
    fi
    case "${relative}" in
      scripts/ops/candidate_physical_tree.py|scripts/ops/controller_static_receipt.py|scripts/ops/controlled_candidate_process.py|scripts/ops/deploy_gate_runtime.py|scripts/ops/freeze_deploy_gate.py|scripts/ops/freeze_git_bridge.py|scripts/ops/freeze_phase_runtime.py|scripts/ops/freeze_worktree_contract.py|scripts/ops/legacy_to_atomic_preflight_report.py|scripts/ops/legacy_to_atomic_preflight_transport.py|scripts/ops/strict_runtime_seatbelt.py|scripts/ops/trusted_git.py|scripts/ops/trusted_npm_audit.py|scripts/stdout_utils.py)
        install -m 0400 "${source}" "${target}"
        ;;
      *)
        install -m 0500 "${source}" "${target}"
        ;;
    esac
    if ! cmp -s "${source}" "${candidate}" || ! cmp -s "${source}" "${target}"; then
      echo "Trusted deploy verifier bytes disagree with the verified candidate: ${relative}" >&2
      return 1
    fi
  done
  TRUSTED_CANDIDATE_VERIFIER="${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/freeze_worktree_candidate.py"
  TRUSTED_RUNTIME_ADMISSION="${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/deploy_runtime_admission.py"
  DEPLOY_VERIFIER_BUNDLE_SHA256="$(
    compute_deploy_verifier_bundle_digest "${DEPLOY_VERIFIER_BUNDLE_DIR}"
  )" || return 1
  DEPLOY_VERIFIER_BUNDLE_READY=1
  verify_deploy_verifier_bundle
}

cleanup_deploy_verifier_bundle() {
  local path
  if [ -z "${DEPLOY_VERIFIER_BUNDLE_DIR}" ]; then
    return 0
  fi
  for path in \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/candidate_physical_tree.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/controller_static_receipt.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/controlled_candidate_process.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/deploy_gate_runtime.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/deploy_runtime_admission.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/freeze_worktree_candidate.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/freeze_worktree_contract.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/freeze_git_bridge.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/freeze_deploy_gate.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/freeze_phase_runtime.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/legacy_to_atomic_preflight.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/legacy_to_atomic_preflight_report.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/legacy_to_atomic_preflight_transport.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/strict_runtime_seatbelt.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/trusted_git.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/trusted_npm_audit.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/stdout_utils.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/verify_redis_worker_health.py" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/verify_runtime_health.py"; do
    chmod u+w "${path}" >/dev/null 2>&1 || true
    rm -f -- "${path}" >/dev/null 2>&1 || true
  done
  rmdir -- \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts" \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}" >/dev/null 2>&1 || {
      echo "[deploy] WARNING: preserving non-empty verifier bundle: ${DEPLOY_VERIFIER_BUNDLE_DIR}" >&2
      return 0
    }
  DEPLOY_VERIFIER_BUNDLE_DIR=""
  DEPLOY_VERIFIER_BUNDLE_SHA256=""
  TRUSTED_CANDIDATE_VERIFIER=""
  TRUSTED_RUNTIME_ADMISSION=""
  DEPLOY_VERIFIER_BUNDLE_READY=0
}

LOCAL_CANDIDATE_WEB_PID=""
LOCAL_CANDIDATE_WEB_PGID=""
LOCAL_CANDIDATE_WEB_PORT=""
LOCAL_CANDIDATE_WEB_RUNTIME=""
LOCAL_CANDIDATE_RUNTIME_ENV=""
LOCAL_CANDIDATE_WEB_PROFILE=""
LOCAL_CANDIDATE_VERIFY_PROFILE=""
LOCAL_CANDIDATE_ADMISSION=""

cleanup_local_candidate_browser_runtime() {
  local pid="${LOCAL_CANDIDATE_WEB_PID}" pgid="${LOCAL_CANDIDATE_WEB_PGID}"
  local port="${LOCAL_CANDIDATE_WEB_PORT}"
  local runtime_root="${LOCAL_CANDIDATE_WEB_RUNTIME}" cleanup_failed=0 attempt state

  candidate_process_group_live() {
    ps -axo pgid=,stat= 2>/dev/null \
      | awk -v expected="$1" \
        '$1 == expected && $2 !~ /^Z/ { found=1 } END { exit(found ? 0 : 1) }'
  }

  if ! [[ "${pgid}" =~ ^[1-9][0-9]*$ ]] \
    && [[ "${pid}" =~ ^[1-9][0-9]*$ ]] \
    && kill -0 "${pid}" 2>/dev/null; then
    state="$(ps -p "${pid}" -o pgid= 2>/dev/null | tr -d '[:space:]')"
    if [ "${state}" = "${pid}" ]; then
      pgid="${state}"
      LOCAL_CANDIDATE_WEB_PGID="${state}"
    fi
  fi

  if [[ "${pgid}" =~ ^[1-9][0-9]*$ ]]; then
    if candidate_process_group_live "${pgid}"; then
      kill -TERM -- "-${pgid}" 2>/dev/null || cleanup_failed=1
      for attempt in $(seq 1 50); do
        if ! candidate_process_group_live "${pgid}"; then
          break
        fi
        sleep 0.1
      done
      if candidate_process_group_live "${pgid}"; then
        kill -KILL -- "-${pgid}" 2>/dev/null || cleanup_failed=1
      fi
    fi
  elif [[ "${pid}" =~ ^[1-9][0-9]*$ ]] && kill -0 "${pid}" 2>/dev/null; then
    # The launcher may fail before os.setsid() commits.  In that narrow state
    # only the exact child PID is safe to signal; it cannot have spawned the
    # candidate Gunicorn worker yet.
    kill -TERM "${pid}" 2>/dev/null || cleanup_failed=1
    for attempt in $(seq 1 50); do
      if ! kill -0 "${pid}" 2>/dev/null; then
        break
      fi
      state="$(ps -p "${pid}" -o stat= 2>/dev/null || true)"
      case "${state}" in
        *Z*) break ;;
      esac
      sleep 0.1
    done
    state="$(ps -p "${pid}" -o stat= 2>/dev/null || true)"
    if kill -0 "${pid}" 2>/dev/null && [[ "${state}" != *Z* ]]; then
      kill -KILL "${pid}" 2>/dev/null || cleanup_failed=1
    fi
  fi
  if [[ "${pid}" =~ ^[1-9][0-9]*$ ]]; then
    wait "${pid}" 2>/dev/null || true
  fi
  if [[ "${pgid}" =~ ^[1-9][0-9]*$ ]] \
    && candidate_process_group_live "${pgid}"; then
    echo "[deploy] CRITICAL: isolated candidate process group is still live: ${pgid}" >&2
    cleanup_failed=1
  elif [[ "${pid}" =~ ^[1-9][0-9]*$ ]] && kill -0 "${pid}" 2>/dev/null; then
    echo "[deploy] CRITICAL: isolated candidate process is still live: ${pid}" >&2
    cleanup_failed=1
  else
    LOCAL_CANDIDATE_WEB_PID=""
    LOCAL_CANDIDATE_WEB_PGID=""
  fi

  if [[ "${port}" =~ ^[1-9][0-9]*$ ]]; then
    if ! "${LOCAL_SAFE_PYTHON:-${PROJECT_ROOT}/scripts/ops/safe_python.sh}" -I -B - "${port}" <<'PY'
import errno
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.settimeout(0.5)
    result = probe.connect_ex(("127.0.0.1", port))
if result == 0:
    raise SystemExit("candidate browser listener is still accepting connections")
if result != errno.ECONNREFUSED:
    raise SystemExit(f"candidate browser listener state is ambiguous: {result}")
PY
    then
      echo "[deploy] CRITICAL: isolated candidate browser port is still occupied: ${port}" >&2
      cleanup_failed=1
    else
      LOCAL_CANDIDATE_WEB_PORT=""
    fi
  else
    LOCAL_CANDIDATE_WEB_PORT=""
  fi

  if [ "${cleanup_failed}" -eq 0 ] && [ -n "${runtime_root}" ]; then
    if ! "${LOCAL_SAFE_PYTHON:-${PROJECT_ROOT}/scripts/ops/safe_python.sh}" -I -B - "${runtime_root}" <<'PY'
from pathlib import Path
import os
import shutil
import stat
import sys

path = Path(sys.argv[1])
canonical_tmp = Path("/tmp").resolve(strict=True)
if (
    path.parent != canonical_tmp
    or not path.name.startswith("vkpi-candidate-browser-runtime.")
):
    raise SystemExit("candidate browser runtime cleanup target is unsafe")
try:
    info = path.lstat()
except FileNotFoundError:
    raise SystemExit(0)
if (
    not stat.S_ISDIR(info.st_mode)
    or path.is_symlink()
    or info.st_uid != os.geteuid()
):
    raise SystemExit("candidate browser runtime cleanup target is unsafe")
# O2: a temporary Postgres rooted here (RUNTIME_ROOT=<root>/runtime inherits
# port 54329) must be stopped before rmtree, or the data dir is ripped from
# under a live postmaster that then holds the port for hours.
data_dir = path / "runtime" / "data" / "postgres"
pid_file = data_dir / "postmaster.pid"
def _live_pid():
    try:
        pid = int(pid_file.read_text(encoding="utf-8").splitlines()[0].strip())
        os.kill(pid, 0)
        return pid if pid > 1 else None
    except (OSError, UnicodeDecodeError, IndexError, ValueError):
        return None
if _live_pid() is not None:
    import subprocess
    candidates = [
        Path(os.environ.get("POSTGRES_BIN") or "/nonexistent") / "pg_ctl",
        Path("/opt/homebrew/opt/postgresql@16/bin/pg_ctl"),
        Path("/opt/homebrew/bin/pg_ctl"),
        Path("/usr/local/bin/pg_ctl"),
        Path("/usr/lib/postgresql/16/bin/pg_ctl"),
    ]
    found = shutil.which("pg_ctl")
    if found:
        candidates.append(Path(found))
    pg_ctl = next((c for c in candidates if c.is_file() and os.access(c, os.X_OK)), None)
    if pg_ctl is not None:
        subprocess.run(
            [str(pg_ctl), "-D", str(data_dir), "stop", "-m", "fast", "-t", "30"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False,
        )
    if _live_pid() is not None:
        raise SystemExit(
            f"candidate browser runtime postgres is still live under {data_dir}"
            f" (pg_ctl={'missing' if pg_ctl is None else pg_ctl}); port 54329 may stay held"
        )
shutil.rmtree(path)
PY
    then
      echo "[deploy] CRITICAL: isolated candidate browser runtime cleanup failed: ${runtime_root}" >&2
      cleanup_failed=1
    else
      LOCAL_CANDIDATE_WEB_RUNTIME=""
      LOCAL_CANDIDATE_RUNTIME_ENV=""
      LOCAL_CANDIDATE_WEB_PROFILE=""
      LOCAL_CANDIDATE_VERIFY_PROFILE=""
      LOCAL_CANDIDATE_ADMISSION=""
    fi
  elif [ -z "${runtime_root}" ]; then
    LOCAL_CANDIDATE_WEB_RUNTIME=""
    LOCAL_CANDIDATE_RUNTIME_ENV=""
    LOCAL_CANDIDATE_WEB_PROFILE=""
    LOCAL_CANDIDATE_VERIFY_PROFILE=""
    LOCAL_CANDIDATE_ADMISSION=""
  fi
  return "${cleanup_failed}"
}

cleanup_initial_deploy_resources() {
  local original_rc=$?
  set +e
  trap - EXIT
  if ! cleanup_local_candidate_browser_runtime; then
    original_rc=1
  fi
  if ! cleanup_deploy_verifier_bundle; then
    original_rc=1
  fi
  exit "${original_rc}"
}

bind_rescue_rollback_candidate() {
  RESCUE_ROLLBACK_CANDIDATE_BRANCH="$(
    "${LOCAL_SAFE_PYTHON}" -B - \
      "${RESCUE_ROLLBACK_CANDIDATE_MANIFEST}" "${PREDEPLOY_APP_SHA}" <<'PY'
import json
import re
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit("rescue rollback candidate manifest is invalid") from exc
source = payload.get("source") if isinstance(payload, dict) else None
head = source.get("head") if isinstance(source, dict) else None
branch = source.get("branch") if isinstance(source, dict) else None
if head != sys.argv[2]:
    raise SystemExit("rescue rollback candidate HEAD does not match pre-deploy runtime")
if not isinstance(branch, str) or not re.fullmatch(r"[^\s\x00]+", branch):
    raise SystemExit("rescue rollback candidate branch identity is invalid")
print(branch)
PY
  )"
}

verify_rescue_rollback_candidate() {
  verify_deploy_verifier_bundle
  run_sealed_controller_python \
    "${TRUSTED_CANDIDATE_VERIFIER}" \
    verify-deploy-source \
    --manifest "${RESCUE_ROLLBACK_CANDIDATE_MANIFEST}" \
    --snapshot "${RESCUE_ROLLBACK_CANDIDATE_DIR}" \
    --expected-head "${PREDEPLOY_APP_SHA}" \
    --expected-branch "${RESCUE_ROLLBACK_CANDIDATE_BRANCH}" >/dev/null
}

LOCAL_HEALTH_ENV_FILE="${VKPI_HEALTH_ENV_FILE:-}"
if [ -z "${LOCAL_HEALTH_ENV_FILE}" ]; then
  echo "VKPI_HEALTH_ENV_FILE must explicitly name the protected local health-token dotenv." >&2
  exit 1
fi
verify_deploy_candidate
trap cleanup_initial_deploy_resources EXIT
seal_deploy_verifier_bundle
verify_deploy_candidate

# Resolve the immutable browser identity from the already-verified frozen
# candidate.  The public browser gate must prove these exact bytes were served;
# a healthy loopback process or a matching file on the host is insufficient.
BROWSER_EXPECTED_GIT_SHA="${LOCAL_GIT_SHA}"
BROWSER_EXPECTED_APP_ASSET=""
BROWSER_EXPECTED_APP_ASSET_SHA256=""
if ! BROWSER_CANDIDATE_IDENTITY="$(
  PYTHONDONTWRITEBYTECODE=1 "${LOCAL_SAFE_PYTHON}" -I -B - \
    "${DEPLOY_CANDIDATE_DIR}/frontend/dist" <<'PY'
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat
import sys

dist = Path(sys.argv[1])
index = dist / "index.html"
try:
    index_info = index.lstat()
    source = index.read_text(encoding="utf-8")
except (OSError, UnicodeError) as exc:
    raise SystemExit("frozen candidate frontend index is unreadable") from exc
if not stat.S_ISREG(index_info.st_mode) or index.is_symlink():
    raise SystemExit("frozen candidate frontend index is not a regular file")
assets = sorted(set(re.findall(r"app-[A-Za-z0-9_-]+\.js", source)))
if len(assets) != 1:
    raise SystemExit("frozen candidate frontend index must name exactly one app asset")
asset = dist / "assets" / assets[0]
try:
    asset_info = asset.lstat()
    payload = asset.read_bytes()
except OSError as exc:
    raise SystemExit("frozen candidate app asset is unreadable") from exc
if (
    not stat.S_ISREG(asset_info.st_mode)
    or asset.is_symlink()
    or len(payload) <= 0
    or len(payload) > 50 * 1024 * 1024
):
    raise SystemExit("frozen candidate app asset is unsafe")
print(assets[0], hashlib.sha256(payload).hexdigest())
PY
)"; then
  echo "Could not resolve the frozen candidate browser identity." >&2
  exit 1
fi
read -r BROWSER_EXPECTED_APP_ASSET BROWSER_EXPECTED_APP_ASSET_SHA256 \
  <<<"${BROWSER_CANDIDATE_IDENTITY}"
BROWSER_CANDIDATE_IDENTITY=""
if ! [[ "${BROWSER_EXPECTED_APP_ASSET}" =~ ^app-[A-Za-z0-9_-]+\.js$ ]] \
  || ! [[ "${BROWSER_EXPECTED_APP_ASSET_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Frozen candidate browser identity is invalid." >&2
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
# 此处先固化候选与源绑定。强运行门必须等候选的非 8102 回环 runtime
# 真实就绪后执行，由 run_predeploy_embedded_browser_gate 在第一个 SSH 之前闭合。
verify_deploy_candidate
assert_deploy_source_unchanged

SSH_TARGET="${PRODUCTION_SSH_TARGET}"
SSH_CONNECT_TIMEOUT_SECONDS="${VKPI_DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS:-10}"
SSH_INITIAL_CONNECT_ATTEMPTS="${VKPI_DEPLOY_SSH_INITIAL_CONNECT_ATTEMPTS:-3}"
SSH_CONTROL_PERSIST_SECONDS="${VKPI_DEPLOY_SSH_CONTROL_PERSIST_SECONDS:-3600}"
SSH_TRANSPORT_DIR=""
SSH_WRAPPER_SNAPSHOT=""
SSH_CONTROL_PATH=""
SSH_REAL_BIN=""
SCP_REAL_BIN=""
SSH_FAIL_CLOSED_PROXY=""
SSH_TRANSPORT_READY=0
SSH_ORIGINAL_PATH="${PATH}"
SSH_ORIGINAL_RSYNC_RSH_SET=0
SSH_ORIGINAL_RSYNC_RSH=""
if [ "${RSYNC_RSH+x}" = x ]; then
  SSH_ORIGINAL_RSYNC_RSH_SET=1
  SSH_ORIGINAL_RSYNC_RSH="${RSYNC_RSH}"
fi

setup_deploy_ssh_transport() {
  local attempt=1
  local effective_control_master=""
  local tmp_base="${TMPDIR:-/tmp}"
  local wrapper_mode=""
  local wrapper_path=""
  local -a bootstrap_options=()

  if ! [[ "${SSH_CONNECT_TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]] \
    || [ "${SSH_CONNECT_TIMEOUT_SECONDS}" -gt 60 ]; then
    echo "VKPI_DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS must be an integer from 1 through 60." >&2
    return 1
  fi
  if ! [[ "${SSH_INITIAL_CONNECT_ATTEMPTS}" =~ ^[1-9][0-9]*$ ]] \
    || [ "${SSH_INITIAL_CONNECT_ATTEMPTS}" -gt 3 ]; then
    echo "VKPI_DEPLOY_SSH_INITIAL_CONNECT_ATTEMPTS must be an integer from 1 through 3." >&2
    return 1
  fi
  if ! [[ "${SSH_CONTROL_PERSIST_SECONDS}" =~ ^[1-9][0-9]*$ ]] \
    || [ "${SSH_CONTROL_PERSIST_SECONDS}" -gt 7200 ]; then
    echo "VKPI_DEPLOY_SSH_CONTROL_PERSIST_SECONDS must be an integer from 1 through 7200." >&2
    return 1
  fi

  SSH_REAL_BIN="$(type -P ssh || true)"
  SCP_REAL_BIN="$(type -P scp || true)"
  SSH_FAIL_CLOSED_PROXY="/usr/bin/false"
  if [ "${SSH_REAL_BIN#/}" = "${SSH_REAL_BIN}" ] || [ ! -x "${SSH_REAL_BIN}" ] \
    || [ "${SCP_REAL_BIN#/}" = "${SCP_REAL_BIN}" ] || [ ! -x "${SCP_REAL_BIN}" ] \
    || [ "${SSH_FAIL_CLOSED_PROXY#/}" = "${SSH_FAIL_CLOSED_PROXY}" ] \
    || [ ! -x "${SSH_FAIL_CLOSED_PROXY}" ]; then
    echo "Deployment requires absolute executable ssh, scp, and false clients." >&2
    return 1
  fi

  SSH_TRANSPORT_DIR="$(mktemp -d "${tmp_base%/}/vkpi-deploy-ssh.XXXXXX")" || return 1
  chmod 700 "${SSH_TRANSPORT_DIR}"
  SSH_WRAPPER_SNAPSHOT="${SSH_TRANSPORT_DIR}/transport-wrapper"
  SSH_CONTROL_PATH="${SSH_TRANSPORT_DIR}/master.sock"
  if [ "${#SSH_CONTROL_PATH}" -gt 96 ]; then
    echo "Deployment SSH control socket path is too long for a portable Unix socket." >&2
    return 1
  fi
  install -m 0500 \
    "${PROJECT_ROOT}/scripts/ops/deploy_local_to_cloud.sh" \
    "${SSH_WRAPPER_SNAPSHOT}"
  ln "${SSH_WRAPPER_SNAPSHOT}" "${SSH_TRANSPORT_DIR}/ssh"
  ln "${SSH_WRAPPER_SNAPSHOT}" "${SSH_TRANSPORT_DIR}/scp"

  # Freeze wrapper code before any remote mutation.  The worktree is replaced
  # during deploy and cannot remain the executable backing rollback transport.
  for wrapper_path in \
    "${SSH_WRAPPER_SNAPSHOT}" \
    "${SSH_TRANSPORT_DIR}/ssh" \
    "${SSH_TRANSPORT_DIR}/scp"; do
    if [ ! -f "${wrapper_path}" ] || [ -L "${wrapper_path}" ]; then
      echo "Deployment SSH wrapper snapshot is not a regular non-symlink file." >&2
      return 1
    fi
    if wrapper_mode="$(stat -f '%Lp' "${wrapper_path}" 2>/dev/null)"; then
      :
    elif wrapper_mode="$(stat -c '%a' "${wrapper_path}" 2>/dev/null)"; then
      :
    else
      echo "Deployment SSH wrapper snapshot permissions could not be verified." >&2
      return 1
    fi
    if [ "${wrapper_mode}" != "500" ]; then
      echo "Deployment SSH wrapper snapshot must have exact mode 0500." >&2
      return 1
    fi
  done
  if [ ! "${SSH_WRAPPER_SNAPSHOT}" -ef "${SSH_TRANSPORT_DIR}/ssh" ] \
    || [ ! "${SSH_WRAPPER_SNAPSHOT}" -ef "${SSH_TRANSPORT_DIR}/scp" ] \
    || ! cmp -s "${PROJECT_ROOT}/scripts/ops/deploy_local_to_cloud.sh" "${SSH_WRAPPER_SNAPSHOT}" \
    || ! cmp -s "${SSH_WRAPPER_SNAPSHOT}" "${SSH_TRANSPORT_DIR}/ssh" \
    || ! cmp -s "${SSH_WRAPPER_SNAPSHOT}" "${SSH_TRANSPORT_DIR}/scp"; then
    echo "Deployment SSH wrapper snapshot identity or content verification failed." >&2
    return 1
  fi

  bootstrap_options=(
    -o BatchMode=yes
    -o ControlMaster=yes
    -o "ControlPersist=${SSH_CONTROL_PERSIST_SECONDS}"
    -o "ControlPath=${SSH_CONTROL_PATH}"
    -o ConnectionAttempts=1
    -o "ConnectTimeout=${SSH_CONNECT_TIMEOUT_SECONDS}"
    -o ServerAliveInterval=15
    -o ServerAliveCountMax=3
  )
  effective_control_master="$(
    "${SSH_REAL_BIN}" -G "${bootstrap_options[@]}" -N -f "${SSH_TARGET}" 2>/dev/null \
      | awk '$1 == "controlmaster" { print $2; exit }'
  )"
  if [ "${effective_control_master}" != "true" ]; then
    echo "Refusing deploy because the effective SSH bootstrap ControlMaster mode is not non-interactive true." >&2
    return 1
  fi

  # Only this no-command transport bootstrap is retried, and at most three
  # times.  Do not repeat -M after ControlMaster=yes: OpenSSH interprets the
  # second enable as ControlMaster=ask and silently rejects non-interactive
  # multiplexed sessions.
  while [ "${attempt}" -le "${SSH_INITIAL_CONNECT_ATTEMPTS}" ]; do
    if "${SSH_REAL_BIN}" "${bootstrap_options[@]}" -N -f "${SSH_TARGET}" \
      && "${SSH_REAL_BIN}" -o "ControlPath=${SSH_CONTROL_PATH}" -O check "${SSH_TARGET}" >/dev/null 2>&1; then
      SSH_TRANSPORT_READY=1
      break
    fi
    if [ -S "${SSH_CONTROL_PATH}" ]; then
      "${SSH_REAL_BIN}" \
        -o "ControlPath=${SSH_CONTROL_PATH}" \
        -O exit "${SSH_TARGET}" >/dev/null 2>&1 || true
      if "${SSH_REAL_BIN}" \
        -o "ControlPath=${SSH_CONTROL_PATH}" \
        -O check "${SSH_TARGET}" >/dev/null 2>&1; then
        echo "Refusing deploy because a failed SSH bootstrap left a live ControlMaster; preserving its private directory." >&2
        return 1
      fi
    fi
    rm -f -- "${SSH_CONTROL_PATH}"
    if [ "${attempt}" -lt "${SSH_INITIAL_CONNECT_ATTEMPTS}" ]; then
      sleep 1
    fi
    attempt=$((attempt + 1))
  done
  if [ "${SSH_TRANSPORT_READY}" != "1" ]; then
    echo "Refusing deploy because the bounded SSH ControlMaster bootstrap failed." >&2
    return 1
  fi
  # A control check proves only that the master process is alive.  Execute one
  # harmless session through the socket, with direct TCP fallback disabled,
  # before any remote read or mutation.  Every later command executes exactly
  # once through this same fail-closed transport.
  if ! "${SSH_REAL_BIN}" \
    -o BatchMode=yes \
    -o ControlMaster=no \
    -o "ControlPath=${SSH_CONTROL_PATH}" \
    -o "ProxyCommand=${SSH_FAIL_CLOSED_PROXY}" \
    -o ConnectionAttempts=1 \
    -o "ConnectTimeout=${SSH_CONNECT_TIMEOUT_SECONDS}" \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    "${SSH_TARGET}" true; then
    echo "Refusing deploy because the SSH ControlMaster cannot execute a non-interactive session." >&2
    return 1
  fi

  export VKPI_DEPLOY_SSH_WRAPPER_MODE=1
  export VKPI_DEPLOY_REAL_SSH="${SSH_REAL_BIN}"
  export VKPI_DEPLOY_REAL_SCP="${SCP_REAL_BIN}"
  export VKPI_DEPLOY_SSH_CONTROL_PATH="${SSH_CONTROL_PATH}"
  export VKPI_DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS="${SSH_CONNECT_TIMEOUT_SECONDS}"
  export VKPI_DEPLOY_SSH_CONTROL_PERSIST_SECONDS="${SSH_CONTROL_PERSIST_SECONDS}"
  export VKPI_DEPLOY_SSH_FAIL_CLOSED_PROXY="${SSH_FAIL_CLOSED_PROXY}"
  PATH="${SSH_TRANSPORT_DIR}:${PATH}"
  export PATH
  RSYNC_RSH="${SSH_TRANSPORT_DIR}/ssh"
  export RSYNC_RSH
  hash -r
  echo "[deploy] SSH transport ready: one private non-interactive ControlMaster, direct TCP fallback disabled, ${SSH_INITIAL_CONNECT_ATTEMPTS} bounded bootstrap attempt(s) maximum."
}

cleanup_deploy_ssh_transport() {
  local preserve_transport=0
  local safe_to_remove=0

  if [ -n "${SSH_TRANSPORT_DIR}" ]; then
    if [ -n "${SSH_CONTROL_PATH}" ] && [ -S "${SSH_CONTROL_PATH}" ]; then
      if [ -n "${SSH_REAL_BIN}" ] && [ -x "${SSH_REAL_BIN}" ]; then
        if ! "${SSH_REAL_BIN}" \
          -o "ControlPath=${SSH_CONTROL_PATH}" \
          -O exit "${SSH_TARGET}" >/dev/null 2>&1; then
          echo "[deploy] WARNING: SSH ControlMaster exit request failed; checking whether it is still alive." >&2
        fi
        # An exit request can fail or be asynchronous.  Never unlink a live
        # control socket: check after every exit request and preserve the whole
        # private directory when the master still answers.
        if "${SSH_REAL_BIN}" \
          -o "ControlPath=${SSH_CONTROL_PATH}" \
          -O check "${SSH_TARGET}" >/dev/null 2>&1; then
          preserve_transport=1
        else
          safe_to_remove=1
        fi
      else
        preserve_transport=1
      fi
    else
      # No Unix-domain control socket remains, so no master can be reached
      # through this deployment-private transport directory.
      safe_to_remove=1
    fi
  fi
  SSH_TRANSPORT_READY=0
  PATH="${SSH_ORIGINAL_PATH}"
  export PATH
  if [ "${SSH_ORIGINAL_RSYNC_RSH_SET}" = "1" ]; then
    RSYNC_RSH="${SSH_ORIGINAL_RSYNC_RSH}"
    export RSYNC_RSH
  else
    unset RSYNC_RSH
  fi
  unset VKPI_DEPLOY_SSH_WRAPPER_MODE VKPI_DEPLOY_REAL_SSH VKPI_DEPLOY_REAL_SCP
  unset VKPI_DEPLOY_SSH_CONTROL_PATH VKPI_DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS
  unset VKPI_DEPLOY_SSH_CONTROL_PERSIST_SECONDS VKPI_DEPLOY_SSH_FAIL_CLOSED_PROXY
  hash -r
  if [ -n "${SSH_TRANSPORT_DIR}" ]; then
    if [ "${preserve_transport}" = "1" ]; then
      chmod 700 "${SSH_TRANSPORT_DIR}" >/dev/null 2>&1 || true
      echo "[deploy] WARNING: SSH ControlMaster is still alive; preserving mode-0700 transport directory: ${SSH_TRANSPORT_DIR}" >&2
    elif [ "${safe_to_remove}" = "1" ]; then
      rm -f -- \
        "${SSH_TRANSPORT_DIR}/ssh" \
        "${SSH_TRANSPORT_DIR}/scp" \
        "${SSH_WRAPPER_SNAPSHOT}" \
        "${SSH_CONTROL_PATH}"
      rmdir -- "${SSH_TRANSPORT_DIR}" >/dev/null 2>&1 || true
      SSH_TRANSPORT_DIR=""
      SSH_WRAPPER_SNAPSHOT=""
      SSH_CONTROL_PATH=""
    fi
  fi
}

REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
SERVICE_NAME="${SERVICE_NAME:-viltrox-2.0-test.service}"
REMOTE_SERVICE_UNIT_RELATIVE="${REMOTE_SERVICE_UNIT_RELATIVE:-scripts/ops/systemd/viltrox-2.0-test.service}"
HEALTH_URL="${PRODUCTION_HEALTH_URL}"
SYNC_SERVICE="${PRODUCTION_SYNC_SERVICE}"
REMOTE_SYNC_SERVICE_UNIT_RELATIVE="${PRODUCTION_SYNC_UNIT_RELATIVE}"
SYNC_TIMER="${PRODUCTION_SYNC_TIMER}"
HEALTH_SENTINEL_SERVICE="vkpi-health-sentinel.service"
HEALTH_SENTINEL_TIMER="vkpi-health-sentinel.timer"
HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE="scripts/ops/systemd/vkpi-health-sentinel.service"
ALLOW_DURING_SYNC="${ALLOW_DURING_SYNC:-0}"
VKPI_EXPECT_ACCESS_GATED="${VKPI_EXPECT_ACCESS_GATED:-0}"
REMOTE_APP_USER="${REMOTE_APP_USER:-viltrox}"
REMOTE_APP_GROUP="${REMOTE_APP_GROUP:-viltrox}"
LANE_OVERRIDE_TEMPLATE_RELATIVE="scripts/ops/systemd/vkpi-lane-overrides.env"
REMOTE_LANE_OVERRIDE_DIR="/etc/vkpi"
REMOTE_LANE_OVERRIDE_FILE="${REMOTE_LANE_OVERRIDE_DIR}/vkpi-lane-overrides.env"
MAX_WORKER_AGE_SECONDS="${VKPI_VERIFY_MAX_WORKER_AGE_SECONDS:-180}"
REMOTE_DEPLOY_LOCK_DIR="/run/lock/vkpi-deploy"
REMOTE_DEPLOY_LOCK_FILE="${REMOTE_DEPLOY_LOCK_DIR}/deploy.lock"
REMOTE_DEPLOY_LOCK_ACK="vkpi-deploy-lock/v1 acquired"
REMOTE_DEPLOY_LOCK_CONTROL_FIFO=""
REMOTE_DEPLOY_LOCK_ACK_FILE=""
REMOTE_DEPLOY_LOCK_STATUS_FILE=""
REMOTE_DEPLOY_LOCK_HOLDER_PID=""
REMOTE_DEPLOY_LOCK_CONTROL_FD_OPEN=0
REMOTE_DEPLOY_LOCK_HELD=0

acquire_remote_deploy_lock() {
  local attempt=0
  local holder_rc=""
  local observed_ack=""
  local observed_status=""

  if [ "${SSH_TRANSPORT_READY}" != "1" ] || [ -z "${SSH_TRANSPORT_DIR}" ]; then
    echo "Refusing deploy because the remote deployment mutex requires the verified SSH transport." >&2
    return 1
  fi
  REMOTE_DEPLOY_LOCK_CONTROL_FIFO="${SSH_TRANSPORT_DIR}/deploy-lock.control"
  REMOTE_DEPLOY_LOCK_ACK_FILE="${SSH_TRANSPORT_DIR}/deploy-lock.ack"
  REMOTE_DEPLOY_LOCK_STATUS_FILE="${SSH_TRANSPORT_DIR}/deploy-lock.status"
  mkfifo -- "${REMOTE_DEPLOY_LOCK_CONTROL_FIFO}"
  chmod 600 "${REMOTE_DEPLOY_LOCK_CONTROL_FIFO}"
  : > "${REMOTE_DEPLOY_LOCK_ACK_FILE}"
  : > "${REMOTE_DEPLOY_LOCK_STATUS_FILE}"
  chmod 600 "${REMOTE_DEPLOY_LOCK_ACK_FILE}" "${REMOTE_DEPLOY_LOCK_STATUS_FILE}"

  # The remote root shell owns fd 9 for the lifetime of this SSH channel.  The
  # kernel releases the flock when local fd 9 is closed on EXIT and the channel
  # ends.  The persistent lock inode is never removed, so one deployment cannot
  # unlink another deployment's live lock.
  (
    set +e
    ssh "${SSH_TARGET}" \
      "sudo -n /bin/bash -c '
set -euo pipefail
umask 077
[ -x /usr/bin/flock ] || exit 69
if ! /usr/bin/mkdir -m 0700 -- /run/lock/vkpi-deploy 2>/dev/null; then
  [ -d /run/lock/vkpi-deploy ] && [ ! -L /run/lock/vkpi-deploy ] || exit 70
fi
[ \"\$(/usr/bin/stat -c \"%u:%g:%a\" -- /run/lock/vkpi-deploy)\" = \"0:0:700\" ] || exit 70
if [ ! -e /run/lock/vkpi-deploy/deploy.lock ] && [ ! -L /run/lock/vkpi-deploy/deploy.lock ]; then
  (set -o noclobber; : > /run/lock/vkpi-deploy/deploy.lock) 2>/dev/null || true
fi
[ -f /run/lock/vkpi-deploy/deploy.lock ] && [ ! -L /run/lock/vkpi-deploy/deploy.lock ] || exit 70
[ \"\$(/usr/bin/stat -c \"%u:%g:%a:%h\" -- /run/lock/vkpi-deploy/deploy.lock)\" = \"0:0:600:1\" ] || exit 70
exec 9>>/run/lock/vkpi-deploy/deploy.lock
/usr/bin/flock -n 9 || exit 75
printf \"%s\\n\" \"vkpi-deploy-lock/v1 acquired\"
IFS= read -r _ || true
'" < "${REMOTE_DEPLOY_LOCK_CONTROL_FIFO}" > "${REMOTE_DEPLOY_LOCK_ACK_FILE}"
    holder_rc=$?
    printf '%s\n' "${holder_rc}" > "${REMOTE_DEPLOY_LOCK_STATUS_FILE}"
    exit "${holder_rc}"
  ) &
  REMOTE_DEPLOY_LOCK_HOLDER_PID=$!

  # Opening the write side unblocks the background SSH stdin setup.  Keep this
  # descriptor open until EXIT; closing it is the only normal unlock signal.
  exec 9>"${REMOTE_DEPLOY_LOCK_CONTROL_FIFO}"
  REMOTE_DEPLOY_LOCK_CONTROL_FD_OPEN=1

  while [ "${attempt}" -lt 150 ]; do
    if IFS= read -r observed_ack < "${REMOTE_DEPLOY_LOCK_ACK_FILE}"; then
      if [ "${observed_ack}" = "${REMOTE_DEPLOY_LOCK_ACK}" ]; then
        REMOTE_DEPLOY_LOCK_HELD=1
        export VKPI_DEPLOY_REMOTE_LOCK_REQUIRED=1
        export VKPI_DEPLOY_REMOTE_LOCK_HOLDER_PID="${REMOTE_DEPLOY_LOCK_HOLDER_PID}"
        export VKPI_DEPLOY_REMOTE_LOCK_STATUS_FILE="${REMOTE_DEPLOY_LOCK_STATUS_FILE}"
        echo "[deploy] remote deployment mutex acquired."
        return 0
      fi
      break
    fi
    if [ -s "${REMOTE_DEPLOY_LOCK_STATUS_FILE}" ]; then
      IFS= read -r observed_status < "${REMOTE_DEPLOY_LOCK_STATUS_FILE}" || true
      break
    fi
    sleep 0.1
    attempt=$((attempt + 1))
  done

  if [ "${REMOTE_DEPLOY_LOCK_CONTROL_FD_OPEN}" = "1" ]; then
    exec 9>&-
    REMOTE_DEPLOY_LOCK_CONTROL_FD_OPEN=0
  fi
  if [ -n "${REMOTE_DEPLOY_LOCK_HOLDER_PID}" ]; then
    wait "${REMOTE_DEPLOY_LOCK_HOLDER_PID}" || holder_rc=$?
  fi
  if [ -z "${holder_rc}" ] && [[ "${observed_status}" =~ ^[0-9]+$ ]]; then
    holder_rc="${observed_status}"
  fi
  REMOTE_DEPLOY_LOCK_HOLDER_PID=""
  rm -f -- \
    "${REMOTE_DEPLOY_LOCK_CONTROL_FIFO}" \
    "${REMOTE_DEPLOY_LOCK_ACK_FILE}" \
    "${REMOTE_DEPLOY_LOCK_STATUS_FILE}"
  REMOTE_DEPLOY_LOCK_CONTROL_FIFO=""
  REMOTE_DEPLOY_LOCK_ACK_FILE=""
  REMOTE_DEPLOY_LOCK_STATUS_FILE=""
  if [ "${holder_rc:-}" = "75" ]; then
    echo "Refusing deploy because another deployment already holds the production mutex." >&2
  else
    echo "Refusing deploy because the production deployment mutex could not be acquired safely." >&2
  fi
  return 1
}

release_remote_deploy_lock() {
  local holder_rc=0
  local observed_status=""

  if [ "${REMOTE_DEPLOY_LOCK_CONTROL_FD_OPEN}" = "1" ]; then
    exec 9>&-
    REMOTE_DEPLOY_LOCK_CONTROL_FD_OPEN=0
  fi
  if [ -n "${REMOTE_DEPLOY_LOCK_HOLDER_PID}" ]; then
    wait "${REMOTE_DEPLOY_LOCK_HOLDER_PID}" || holder_rc=$?
  fi
  if [ -s "${REMOTE_DEPLOY_LOCK_STATUS_FILE}" ]; then
    IFS= read -r observed_status < "${REMOTE_DEPLOY_LOCK_STATUS_FILE}" || true
    if [[ "${observed_status}" =~ ^[0-9]+$ ]] && [ "${observed_status}" -ne 0 ]; then
      holder_rc="${observed_status}"
    fi
  fi
  unset VKPI_DEPLOY_REMOTE_LOCK_REQUIRED VKPI_DEPLOY_REMOTE_LOCK_HOLDER_PID
  unset VKPI_DEPLOY_REMOTE_LOCK_STATUS_FILE
  REMOTE_DEPLOY_LOCK_HOLDER_PID=""
  REMOTE_DEPLOY_LOCK_HELD=0
  rm -f -- \
    "${REMOTE_DEPLOY_LOCK_CONTROL_FIFO}" \
    "${REMOTE_DEPLOY_LOCK_ACK_FILE}" \
    "${REMOTE_DEPLOY_LOCK_STATUS_FILE}"
  REMOTE_DEPLOY_LOCK_CONTROL_FIFO=""
  REMOTE_DEPLOY_LOCK_ACK_FILE=""
  REMOTE_DEPLOY_LOCK_STATUS_FILE=""
  if [ "${holder_rc}" -ne 0 ]; then
    echo "[deploy] CRITICAL: remote deployment mutex channel ended unexpectedly (${holder_rc})." >&2
    return 1
  fi
  return 0
}

EXPECTED_WORKER_COUNT=16
WORKER_SYSTEMD_UNITS=(
  vkpi-worker-interactive.service
  vkpi-worker-bulk@1.service
  vkpi-worker-bulk@2.service
  vkpi-worker-bulk@3.service
  vkpi-worker-bulk@4.service
  vkpi-worker-bulk@5.service
  vkpi-worker-bulk@6.service
  vkpi-worker-bulk@7.service
  vkpi-worker-bulk@8.service
  vkpi-worker-bulk@9.service
  vkpi-worker-bulk@10.service
  vkpi-worker-bulk@11.service
  vkpi-worker-bulk@12.service
  vkpi-worker-bulk@13.service
  vkpi-worker-bulk@14.service
  vkpi-worker-bulk@15.service
)
LEGACY_WRITER_UNITS=(
  viltrox-2.0-scheduler.service
  viltrox-2.0-worker.service
  viltrox-2.0-admin.service
  viltrox-2.0-public.service
)
POST_DEPLOY_BROWSER_URL="${VKPI_BROWSER_GATE_URL:-}"
# The production-only entrypoint always mints this bearer remotely after API
# acceptance.  It remains a non-exported shell variable until the single Node
# controller invocation at the final browser gate.
BROWSER_GATE_OVERALL_TIMEOUT_MS="${VKPI_BROWSER_GATE_OVERALL_TIMEOUT_MS:-600000}"
BROWSER_GATE_TOKEN_SAFETY_MARGIN_SECONDS=120
BROWSER_GATE_SETTLE_MS="${VKPI_BROWSER_GATE_SETTLE_MS:-5000}"
BROWSER_GATE_PAGE_SETTLE_MS="${VKPI_BROWSER_GATE_PAGE_SETTLE_MS:-1000}"
BROWSER_GATE_PAGE_TIMEOUT_MS="${VKPI_BROWSER_GATE_PAGE_TIMEOUT_MS:-30000}"
BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS="${VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS:-}"
POST_DEPLOY_CHROME_PATH="${PRODUCTION_CHROME_PATH}"
PREDEPLOY_BROWSER_URL=""
REMOTE_ACCEPTANCE_BASE_URL="${PRODUCTION_REMOTE_ACCEPTANCE_BASE_URL}"
LOCAL_ACCEPTANCE_REPORT_TMP=""
RELEASE_ID="${VKPI_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)-${LOCAL_GIT_SHA:0:12}}"
if ! [[ "${RELEASE_ID}" =~ ^[A-Za-z0-9_.-]+$ ]] || [ "${RELEASE_ID}" = "." ] || [ "${RELEASE_ID}" = ".." ]; then
  echo "VKPI_RELEASE_ID must be a safe release directory name." >&2
  exit 1
fi
REMOTE_RELEASES_DIR="${REMOTE_ROOT}/releases"
REMOTE_RELEASE_DIR="${REMOTE_RELEASES_DIR}/${RELEASE_ID}"
REMOTE_CURRENT_DIR="${REMOTE_ROOT}/current"
ROLLBACK_ANCHOR_RELEASE_ID=""
REMOTE_ROLLBACK_ANCHOR_DIR=""
ROLLBACK_ANCHOR_PREPARE_OPTION=""
ROLLBACK_ANCHOR_DATABASE_STRATEGY="in-place"
ROLLBACK_ANCHOR_TARGET_DATABASE=""
ROLLBACK_ANCHOR_DATABASE_OWNER_RELEASE_ID=""
ROLLBACK_ANCHOR_ENV_FINGERPRINT=""
EXPECTED_PREVIOUS_RELEASE_DIR=""
ROLLBACK_ARMED=0
ROLLBACK_COMPLETED=0
ROLLBACK_PREPARE_MAY_HAVE_COMMITTED=0
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
PGBOUNCER_SERVICE="pgbouncer.service"
PGBOUNCER_SOCKET="pgbouncer.socket"
PGBOUNCER_PORT="6432"
PGBOUNCER_CONFIG_PATH="/etc/pgbouncer/pgbouncer.ini"
PGBOUNCER_MAP_BACKUP_PATH="/etc/pgbouncer/.vkpi-release-map-${RELEASE_ID}.ini"
PGBOUNCER_MAP_RECEIPT_PATH="/etc/pgbouncer/.vkpi-release-map-${RELEASE_ID}.json"
PGBOUNCER_STATE_CAPTURED=0
PGBOUNCER_MAY_HAVE_BEEN_MUTATED=0
PGBOUNCER_QUIESCED=0
PGBOUNCER_RESTORED=0
PGBOUNCER_MAP_CAPTURED=0
PGBOUNCER_MAP_MUTATION_INTENT=0
PGBOUNCER_MAP_PREPARED=0
PGBOUNCER_MAP_RESTORED=0
PGBOUNCER_MAP_CONFIG_SHA_BEFORE=""
PGBOUNCER_MAP_CONFIG_SHA_AFTER=""
PGBOUNCER_WEB_POOL_EFFECTIVE=""
PGBOUNCER_WEB_POOL_EFFECTIVE_BEFORE=""
PGBOUNCER_SERVICE_LOAD_STATE=""
PGBOUNCER_SERVICE_ACTIVE_STATE=""
PGBOUNCER_SERVICE_UNIT_FILE_STATE=""
PGBOUNCER_SOCKET_LOAD_STATE=""
PGBOUNCER_SOCKET_ACTIVE_STATE=""
PGBOUNCER_SOCKET_UNIT_FILE_STATE=""
DATABASE_RELEASE_STRATEGY="in-place"
DATABASE_ENV_ASSERT_RUNTIME_POOL_FLAG=""
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
LIVE_RELEASE_DRAIN_VERIFIED=0
RELEASE_DRAIN_VERIFIED=0
FENCED_RELEASE_DRAIN_VERIFIED=0
RELEASE_VALIDATION_FENCE="/run/vkpi-release-validation.fence"
RELEASE_VALIDATION_FENCE_INSTALLED=0
RELEASE_VALIDATION_FENCE_INSTALL_MAY_HAVE_COMMITTED=0
RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED=0
RELEASE_VALIDATION_COMMIT_STARTED=0
SYNC_UNITS_CAPTURED=0
SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=0
SYNC_UNITS_QUIESCED=0
SYNC_UNITS_RESTORED=0
SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED=0
SYNC_UNITS_RESTORE_RECONCILE_STATE=""
SYNC_SERVICE_ACTIVE_STATE=""
SYNC_SERVICE_UNIT_FILE_STATE=""
SYNC_TIMER_ACTIVE_STATE=""
SYNC_TIMER_UNIT_FILE_STATE=""
HEALTH_SENTINEL_SERVICE_ACTIVE_STATE=""
HEALTH_SENTINEL_SERVICE_UNIT_FILE_STATE=""
HEALTH_SENTINEL_TIMER_ACTIVE_STATE=""
HEALTH_SENTINEL_TIMER_UNIT_FILE_STATE=""

if [ "${PGBOUNCER_SERVICE}" != "pgbouncer.service" ] \
  || [ "${PGBOUNCER_SOCKET}" != "pgbouncer.socket" ] \
  || [ "${PGBOUNCER_PORT}" != "6432" ] \
  || [ "${PGBOUNCER_CONFIG_PATH}" != "/etc/pgbouncer/pgbouncer.ini" ]; then
  echo "The reviewed PgBouncer service, socket, listener, and config must remain fixed." >&2
  exit 1
fi

if [ -n "${FIRST_ATOMIC_BOOTSTRAP_PLAN}" ] || [ -n "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" ]; then
  if [ -z "${FIRST_ATOMIC_BOOTSTRAP_PLAN}" ] || [ -z "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" ]; then
    echo "VKPI_FIRST_ATOMIC_BOOTSTRAP_PLAN and VKPI_FIRST_ATOMIC_BOOTSTRAP_CONFIRM must be supplied together." >&2
    exit 1
  fi
  FIRST_ATOMIC_BOOTSTRAP_MODE=1
fi
if [ "${RESCUE_ROLLBACK_MODE}" = "1" ] \
  && [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  echo "A sealed rollback-candidate rescue cannot be combined with first atomic bootstrap." >&2
  exit 1
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
if [ "${VKPI_EXPECT_ACCESS_GATED}" != "0" ] && [ "${VKPI_EXPECT_ACCESS_GATED}" != "1" ]; then
  echo "VKPI_EXPECT_ACCESS_GATED must be exactly 0 or 1." >&2
  exit 1
fi
if ! [[ "${REMOTE_SERVICE_UNIT_RELATIVE}" =~ ^scripts/ops/systemd/[A-Za-z0-9@_.-]+\.service$ ]]; then
  echo "REMOTE_SERVICE_UNIT_RELATIVE must name one direct reviewed unit under scripts/ops/systemd/." >&2
  exit 1
fi
if [ "${REMOTE_SERVICE_UNIT_RELATIVE##*/}" != "${SERVICE_NAME}" ]; then
  echo "REMOTE_SERVICE_UNIT_RELATIVE basename must exactly match SERVICE_NAME." >&2
  exit 1
fi
if [ ! -f "${DEPLOY_CANDIDATE_DIR}/${REMOTE_SERVICE_UNIT_RELATIVE}" ] \
  || [ -L "${DEPLOY_CANDIDATE_DIR}/${REMOTE_SERVICE_UNIT_RELATIVE}" ]; then
  echo "Reviewed web service unit must be an existing regular non-symlink file: ${REMOTE_SERVICE_UNIT_RELATIVE}" >&2
  exit 1
fi
if ! [[ "${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}" =~ ^scripts/ops/systemd/[A-Za-z0-9@_.-]+\.service$ ]]; then
  echo "REMOTE_SYNC_SERVICE_UNIT_RELATIVE must name one direct reviewed unit under scripts/ops/systemd/." >&2
  exit 1
fi
if [ "${REMOTE_SYNC_SERVICE_UNIT_RELATIVE##*/}" != "${SYNC_SERVICE}" ]; then
  echo "REMOTE_SYNC_SERVICE_UNIT_RELATIVE basename must exactly match SYNC_SERVICE." >&2
  exit 1
fi
if [ ! -f "${DEPLOY_CANDIDATE_DIR}/${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}" ] \
  || [ -L "${DEPLOY_CANDIDATE_DIR}/${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}" ]; then
  echo "Reviewed sync service unit must be an existing regular non-symlink file: ${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}" >&2
  exit 1
fi
if [ "${HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE##*/}" != "${HEALTH_SENTINEL_SERVICE}" ] \
  || [ ! -f "${DEPLOY_CANDIDATE_DIR}/${HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE}" ] \
  || [ -L "${DEPLOY_CANDIDATE_DIR}/${HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE}" ]; then
  echo "Reviewed health sentinel service unit must be an existing regular non-symlink file: ${HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE}" >&2
  exit 1
fi
if [ ! -f "${DEPLOY_CANDIDATE_DIR}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}" ] \
  || [ -L "${DEPLOY_CANDIDATE_DIR}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}" ]; then
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
VILTROXTEST_ROOT_MATCH=0
VILTROXTEST_SERVICE_MATCH=0
VILTROXTEST_UNIT_MATCH=0
[ "${REMOTE_ROOT}" = "/opt/viltrox-2.0" ] && VILTROXTEST_ROOT_MATCH=1
[ "${SERVICE_NAME}" = "viltrox-2.0-test.service" ] && VILTROXTEST_SERVICE_MATCH=1
[ "${REMOTE_SERVICE_UNIT_RELATIVE}" = "scripts/ops/systemd/viltrox-2.0-test.service" ] \
  && VILTROXTEST_UNIT_MATCH=1
VILTROXTEST_SCOPE_MATCHES=$((
  VILTROXTEST_ROOT_MATCH + VILTROXTEST_SERVICE_MATCH + VILTROXTEST_UNIT_MATCH
))
if [ "${VILTROXTEST_SCOPE_MATCHES}" -ne 0 ] \
  && [ "${VILTROXTEST_SCOPE_MATCHES}" -ne 3 ]; then
  echo "A partial viltroxtest production scope is forbidden; root, service name, and reviewed unit must match together." >&2
  exit 1
fi
if [ "${VILTROXTEST_SCOPE_MATCHES}" -eq 3 ]; then
  VILTROXTEST_RELEASE_SCOPE=1
fi
if [ "${VILTROXTEST_RELEASE_SCOPE}" != "1" ]; then
  echo "This deployment entrypoint is restricted to the exact reviewed viltroxtest production scope." >&2
  exit 1
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
  "${LOCAL_SAFE_PYTHON}" - "${POST_DEPLOY_BROWSER_URL}" <<'PY'
import sys
valid = sys.argv[1] == "https://www.viltroxtest.com/"
raise SystemExit(0 if valid else 1)
PY
}
if [ "${VILTROXTEST_RELEASE_SCOPE}" = "1" ] && ! viltroxtest_browser_gate_is_exact; then
  echo "The reviewed viltroxtest release scope requires VKPI_BROWSER_GATE_URL=https://www.viltroxtest.com/." >&2
  exit 1
fi

validate_lane_override_template() {
  "${LOCAL_SAFE_PYTHON}" - "${DEPLOY_CANDIDATE_DIR}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}" <<'PY'
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
    "DB_USE_PGBOUNCER",
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
    # 2026-07-22 满功率收编:资源槽是 DB 全局信号量,代码侧硬上限 MAX_RESOURCE_SLOT_CAP=16
    # (apify_job_resource_slots.py env range fail-closed),验证器同界。
    integer(key, 1, 16)
pool_min = integer("POSTGRES_POOL_MIN_SIZE", 1, 16)
pool_max = integer("POSTGRES_POOL_MAX_SIZE", 1, 64)
if pool_min > pool_max:
    raise SystemExit("POSTGRES_POOL_MIN_SIZE exceeds POSTGRES_POOL_MAX_SIZE")
# 2026-07-22 多并发地基:worker 车道用 session 级 advisory lock(Gemini QPS 闸/LLM
# slot),PgBouncer transaction pooling 下 session 锁会漂移——车道永远直连,值只许 0。
integer("DB_USE_PGBOUNCER", 0, 0)
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
# P0 data boundary: the current clone path starts writable web/Redis/worker
# processes before final API/browser acceptance.  A later rollback restores the
# old source database, so accepted writes disappear while provider side effects
# cannot be reversed.  Keep new-clone activation unreachable until validation
# runs in a proved read-only lane and a one-way commit precedes writable ingress.
if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
  echo "Refusing VKPI_STAGING_DB_CLONE=1 before remote mutation: writable staging-clone validation can lose accepted writes or external effects on rollback; a proven read-only validation and irreversible commit protocol is not implemented." >&2
  exit 1
fi
if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
  if [ "${VILTROXTEST_RELEASE_SCOPE}" != "1" ]; then
    echo "VKPI_STAGING_DB_CLONE=1 is restricted to the reviewed viltroxtest root and service." >&2
    exit 1
  fi
  if ! viltroxtest_browser_gate_is_exact; then
    echo "VKPI_STAGING_DB_CLONE=1 requires an HTTPS browser gate on host www.viltroxtest.com." >&2
    exit 1
  fi
  if [ -n "${FORWARD_COMPATIBILITY_DECLARATION}" ]; then
    echo "A staging database clone must not claim in-place forward compatibility." >&2
    exit 1
  fi
  DATABASE_RELEASE_STRATEGY="staging-clone"
  STAGING_CLONE_DATABASE="$(run_frozen_candidate_python \
    "${DEPLOY_CANDIDATE_DIR}/scripts/ops/staging_db_clone.py" name --release-id "${RELEASE_ID}")"
fi
if ! [[ "${BROWSER_GATE_OVERALL_TIMEOUT_MS}" =~ ^[0-9]+$ ]] \
  || [ "${BROWSER_GATE_OVERALL_TIMEOUT_MS}" -lt 60000 ] \
  || [ "${BROWSER_GATE_OVERALL_TIMEOUT_MS}" -gt 1080000 ]; then
  echo "VKPI_BROWSER_GATE_OVERALL_TIMEOUT_MS must be an integer within [60000, 1080000]." >&2
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
# The controller enforces one wall-clock deadline across Chromium discovery,
# every CDP command, all page/search waits, and the final idle proof. Derive the
# short-lived bearer TTL from that real bound instead of summing mutually
# exclusive per-step maxima. Round milliseconds up, then add exactly 120s.
BROWSER_GATE_CAPTURE_BUDGET_SECONDS=$(((BROWSER_GATE_OVERALL_TIMEOUT_MS + 999) / 1000))
BROWSER_GATE_TOKEN_TTL_SECONDS=$((
  BROWSER_GATE_CAPTURE_BUDGET_SECONDS + BROWSER_GATE_TOKEN_SAFETY_MARGIN_SECONDS
))
if ! "${LOCAL_SAFE_PYTHON}" - \
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
# Chromium on macOS delegates HTTPS trust evaluation to the signed-in account's
# Security framework context.  A synthetic HOME such as /tmp makes Chrome 151
# stall in SecTrustSettingsXPCRead before Page.navigate can acknowledge, even
# though the owned browser still starts and the page may partially execute.
# Derive the minimum non-secret account identity from the effective uid rather
# than trusting caller-controlled HOME/USER/LOGNAME.  The browser continues to
# use a fresh --user-data-dir, incognito mode, disabled extensions, and the
# controller's strict child-environment allowlist.
BROWSER_GATE_OS_IDENTITY="$(
  PYTHONDONTWRITEBYTECODE=1 "${LOCAL_SAFE_PYTHON}" -B -I - <<'PY'
import os
import pwd

entry = pwd.getpwuid(os.geteuid())
values = (str(os.geteuid()), str(entry.pw_name), str(entry.pw_dir))
if any(not value or ":" in value or "\n" in value or "\r" in value for value in values):
    raise SystemExit(1)
if (
    not os.path.isabs(entry.pw_dir)
    or os.path.normpath(entry.pw_dir) != entry.pw_dir
    or os.path.realpath(entry.pw_dir) != entry.pw_dir
):
    raise SystemExit(1)
print(":".join(values))
PY
)" || {
  BROWSER_GATE_OS_IDENTITY=""
  echo "Reviewed browser gate could not resolve the effective macOS account identity." >&2
  exit 1
}
IFS=: read -r BROWSER_GATE_OS_UID BROWSER_GATE_OS_USER BROWSER_GATE_OS_HOME BROWSER_GATE_OS_EXTRA \
  <<<"${BROWSER_GATE_OS_IDENTITY}"
BROWSER_GATE_OS_IDENTITY=""
if ! [[ "${BROWSER_GATE_OS_UID}" =~ ^[1-9][0-9]*$ ]] \
  || ! [[ "${BROWSER_GATE_OS_USER}" =~ ^[A-Za-z0-9._-]+$ ]] \
  || [ "${BROWSER_GATE_OS_USER}" = "." ] \
  || [ "${BROWSER_GATE_OS_USER}" = ".." ] \
  || [ -n "${BROWSER_GATE_OS_EXTRA}" ] \
  || [ "${BROWSER_GATE_OS_HOME#/}" = "${BROWSER_GATE_OS_HOME}" ] \
  || [ ! -d "${BROWSER_GATE_OS_HOME}" ] \
  || [ -L "${BROWSER_GATE_OS_HOME}" ]; then
  echo "Reviewed browser gate macOS account identity is unsafe." >&2
  exit 1
fi
if ! BROWSER_GATE_OS_HOME_UID="$(/usr/bin/stat -f '%u' "${BROWSER_GATE_OS_HOME}")" \
  || ! BROWSER_GATE_OS_HOME_MODE="$(/usr/bin/stat -f '%Lp' "${BROWSER_GATE_OS_HOME}")"; then
  echo "Reviewed browser gate macOS home metadata is unavailable." >&2
  exit 1
fi
if [ "${BROWSER_GATE_OS_HOME_UID}" != "${BROWSER_GATE_OS_UID}" ] \
  || ! [[ "${BROWSER_GATE_OS_HOME_MODE}" =~ ^[0-7]{3,4}$ ]] \
  || (( (8#${BROWSER_GATE_OS_HOME_MODE} & 8#022) != 0 )); then
  echo "Reviewed browser gate macOS home ownership or mode is unsafe." >&2
  exit 1
fi
BROWSER_GATE_OS_HOME_UID=""
BROWSER_GATE_OS_HOME_MODE=""
if ! /usr/bin/codesign --verify --deep "${PRODUCTION_CHROME_APP}" >/dev/null 2>&1; then
  echo "Reviewed production Chrome failed deep code-signature verification." >&2
  exit 1
fi
CHROME_GATEKEEPER_ASSESSMENT="$(
  /usr/sbin/spctl --assess --type execute --verbose=4 "${PRODUCTION_CHROME_APP}" 2>&1
)" || {
  CHROME_GATEKEEPER_ASSESSMENT=""
  echo "Reviewed production Chrome failed Gatekeeper execution assessment." >&2
  exit 1
}
if ! grep -Fq "accepted" <<<"${CHROME_GATEKEEPER_ASSESSMENT}" \
  || ! grep -Fq "source=Notarized Developer ID" <<<"${CHROME_GATEKEEPER_ASSESSMENT}"; then
  CHROME_GATEKEEPER_ASSESSMENT=""
  echo "Reviewed production Chrome is not an accepted notarized Developer ID application." >&2
  exit 1
fi
CHROME_GATEKEEPER_ASSESSMENT=""
CHROME_CODE_IDENTITY="$(/usr/bin/codesign -d --verbose=4 "${PRODUCTION_CHROME_APP}" 2>&1)"
if ! grep -Fxq "Identifier=com.google.Chrome" <<<"${CHROME_CODE_IDENTITY}" \
  || ! grep -Fxq "TeamIdentifier=EQHXZ8M8AV" <<<"${CHROME_CODE_IDENTITY}"; then
  CHROME_CODE_IDENTITY=""
  echo "Reviewed production Chrome identifier or signing team is invalid." >&2
  exit 1
fi
CHROME_CODE_IDENTITY=""

if [ -n "${VKPI_POST_DEPLOY_EVIDENCE_DIR:-}" ]; then
  POST_DEPLOY_EVIDENCE_DIR="${VKPI_POST_DEPLOY_EVIDENCE_DIR}"
  POST_DEPLOY_EVIDENCE_OWNED=0
else
  POST_DEPLOY_EVIDENCE_DIR="${PROJECT_ROOT}/runtime/ops/post-deploy/${RELEASE_ID}"
  POST_DEPLOY_EVIDENCE_OWNED=1
fi
PREDEPLOY_BROWSER_EVIDENCE_DIR="${PROJECT_ROOT}/runtime/ops/predeploy/${RELEASE_ID}"
REMOTE_LOG_BASELINE=""
REMOTE_ACCEPTANCE_REPORT=""
DEPLOY_ACCEPTED=0

start_local_candidate_browser_runtime() {
  local candidate_build_time="" controller_tmp_root="" health_tmp="" observed_pid="" observed_pgid=""
  local ready=0 attempt

  verify_deploy_candidate
  assert_deploy_source_unchanged
  if [ -n "${LOCAL_CANDIDATE_WEB_PID}" ] \
    || [ -n "${LOCAL_CANDIDATE_WEB_PGID}" ] \
    || [ -n "${LOCAL_CANDIDATE_WEB_PORT}" ] \
    || [ -n "${LOCAL_CANDIDATE_WEB_RUNTIME}" ]; then
    echo "Isolated candidate browser runtime state is already occupied." >&2
    return 1
  fi

  controller_tmp_root="$(cd /tmp && pwd -P)" || {
    echo "Could not resolve the controller's physical temporary directory." >&2
    return 1
  }
  if [ "${controller_tmp_root#/}" = "${controller_tmp_root}" ] \
    || [ ! -d "${controller_tmp_root}" ]; then
    echo "Controller physical temporary directory is invalid." >&2
    return 1
  fi
  LOCAL_CANDIDATE_WEB_RUNTIME="$(
    mktemp -d "${controller_tmp_root%/}/vkpi-candidate-browser-runtime.XXXXXX"
  )"
  chmod 700 "${LOCAL_CANDIDATE_WEB_RUNTIME}"
  install -d -m 0700 \
    "${LOCAL_CANDIDATE_WEB_RUNTIME}/home" \
    "${LOCAL_CANDIDATE_WEB_RUNTIME}/cache" \
    "${LOCAL_CANDIDATE_WEB_RUNTIME}/tmp" \
    "${LOCAL_CANDIDATE_WEB_RUNTIME}/runtime" \
    "${LOCAL_CANDIDATE_WEB_RUNTIME}/controller"
  LOCAL_CANDIDATE_WEB_PORT="$(
    "${LOCAL_SAFE_PYTHON}" -I -B - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
  )"
  if ! [[ "${LOCAL_CANDIDATE_WEB_PORT}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Could not reserve an isolated candidate browser port." >&2
    return 1
  fi
  candidate_build_time="$(tr -d '[:space:]' <"${DEPLOY_CANDIDATE_DIR}/BUILD_TIME")"
  PREDEPLOY_BROWSER_URL="http://127.0.0.1:${LOCAL_CANDIDATE_WEB_PORT}/"
  LOCAL_CANDIDATE_RUNTIME_ENV="${LOCAL_CANDIDATE_WEB_RUNTIME}/controller/candidate-runtime.env"
  LOCAL_CANDIDATE_WEB_PROFILE="${LOCAL_CANDIDATE_WEB_RUNTIME}/controller/candidate-web.sb"
  LOCAL_CANDIDATE_VERIFY_PROFILE="${LOCAL_CANDIDATE_WEB_RUNTIME}/controller/candidate-verify.sb"
  LOCAL_CANDIDATE_ADMISSION="${LOCAL_CANDIDATE_WEB_RUNTIME}/controller/runtime-admission.json"

  verify_deploy_verifier_bundle
  if ! run_sealed_controller_python \
    "${TRUSTED_RUNTIME_ADMISSION}" \
    --manifest "${DEPLOY_CANDIDATE_MANIFEST}" \
    --snapshot "${DEPLOY_CANDIDATE_DIR}" \
    --expected-head "${LOCAL_GIT_SHA}" \
    --expected-branch "${LOCAL_GIT_BRANCH}" \
    --source "${PROJECT_ROOT}" \
    --runtime-root "${LOCAL_CANDIDATE_WEB_RUNTIME}" \
    --source-env-file "${PROJECT_ROOT}/.env" \
    --health-env-file "${LOCAL_HEALTH_ENV_FILE}" \
    --web-port "${LOCAL_CANDIDATE_WEB_PORT}" \
    --env-out "${LOCAL_CANDIDATE_RUNTIME_ENV}" \
    --web-profile-out "${LOCAL_CANDIDATE_WEB_PROFILE}" \
    --verify-profile-out "${LOCAL_CANDIDATE_VERIFY_PROFILE}" \
    --admission-out "${LOCAL_CANDIDATE_ADMISSION}" >/dev/null; then
    echo "Controller could not prepare the strict candidate runtime admission." >&2
    return 1
  fi

  env -i \
    PATH="${BROWSER_GATE_CONTROLLER_PATH}" \
    HOME="${LOCAL_CANDIDATE_WEB_RUNTIME}/home" \
    XDG_CACHE_HOME="${LOCAL_CANDIDATE_WEB_RUNTIME}/cache" \
    TMPDIR="${LOCAL_CANDIDATE_WEB_RUNTIME}/tmp" \
    LANG=C.UTF-8 \
    PROJECT_ROOT="${PROJECT_ROOT}" \
    CANDIDATE_ROOT="${DEPLOY_CANDIDATE_DIR}" \
    CANDIDATE_RUNTIME="${LOCAL_CANDIDATE_WEB_RUNTIME}/runtime" \
    CANDIDATE_LOCAL_ENV_FILE="${LOCAL_CANDIDATE_RUNTIME_ENV}" \
    CANDIDATE_PORT="${LOCAL_CANDIDATE_WEB_PORT}" \
    APP_GIT_SHA="${LOCAL_GIT_SHA}" \
    APP_GIT_BRANCH="${LOCAL_GIT_BRANCH}" \
    APP_BUILD_TIME="${candidate_build_time}" \
    CANDIDATE_LAUNCHER="${DEPLOY_CANDIDATE_DIR}/scripts/ops/run_isolated_candidate_web.sh" \
    /usr/bin/sandbox-exec -f "${LOCAL_CANDIDATE_WEB_PROFILE}" \
    "${DEPLOY_PHYSICAL_PYTHON}" -I -S -B - \
      >>"${PREDEPLOY_BROWSER_EVIDENCE_DIR}/candidate-web.log" 2>&1 <<'PY' &
import os

launcher = os.environ["CANDIDATE_LAUNCHER"]
os.setsid()
os.execve("/bin/bash", ["/bin/bash", launcher], os.environ)
PY
  LOCAL_CANDIDATE_WEB_PID=$!

  for attempt in $(seq 1 40); do
    if ! kill -0 "${LOCAL_CANDIDATE_WEB_PID}" 2>/dev/null; then
      break
    fi
    observed_pgid="$(
      ps -p "${LOCAL_CANDIDATE_WEB_PID}" -o pgid= 2>/dev/null \
        | tr -d '[:space:]'
    )"
    if [ "${observed_pgid}" = "${LOCAL_CANDIDATE_WEB_PID}" ]; then
      LOCAL_CANDIDATE_WEB_PGID="${observed_pgid}"
      break
    fi
    sleep 0.05
  done
  if [ "${LOCAL_CANDIDATE_WEB_PGID}" != "${LOCAL_CANDIDATE_WEB_PID}" ]; then
    echo "Isolated candidate browser runtime did not obtain a private process group." >&2
    return 1
  fi

  health_tmp="${PREDEPLOY_BROWSER_EVIDENCE_DIR}/.candidate-health.tmp"
  rm -f -- "${health_tmp}"
  for attempt in $(seq 1 120); do
    if curl --fail --silent --show-error --max-time 2 \
      "${PREDEPLOY_BROWSER_URL}health" >"${health_tmp}" 2>/dev/null; then
      ready=1
      break
    fi
    if ! kill -0 "${LOCAL_CANDIDATE_WEB_PID}" 2>/dev/null; then
      break
    fi
    sleep 0.25
  done
  if [ "${ready}" != "1" ]; then
    echo "Isolated candidate browser runtime did not become healthy." >&2
    return 1
  fi
  mv -- "${health_tmp}" "${PREDEPLOY_BROWSER_EVIDENCE_DIR}/candidate-health.json"
  chmod 600 "${PREDEPLOY_BROWSER_EVIDENCE_DIR}/candidate-health.json"

  observed_pid="$(cat -- "${LOCAL_CANDIDATE_WEB_RUNTIME}/runtime/gunicorn.pid")"
  observed_pgid="$(
    ps -p "${LOCAL_CANDIDATE_WEB_PID}" -o pgid= 2>/dev/null \
      | tr -d '[:space:]'
  )"
  if [ "${observed_pid}" != "${LOCAL_CANDIDATE_WEB_PID}" ] \
    || [ "${observed_pgid}" != "${LOCAL_CANDIDATE_WEB_PGID}" ] \
    || [ "${LOCAL_CANDIDATE_WEB_PGID}" != "${LOCAL_CANDIDATE_WEB_PID}" ]; then
    echo "Isolated candidate browser runtime PID binding is invalid." >&2
    return 1
  fi
  run_frozen_candidate_python \
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_runtime_health.py" \
    --expected-head "${LOCAL_GIT_SHA}" \
    --expected-migration "${LATEST_MIGRATION}" \
    --require-worker \
    --expected-worker-count "${EXPECTED_WORKER_COUNT}" \
    --max-worker-age-seconds 180 \
    <"${PREDEPLOY_BROWSER_EVIDENCE_DIR}/candidate-health.json" \
    >"${PREDEPLOY_BROWSER_EVIDENCE_DIR}/candidate-runtime-verdict.txt"
  chmod 600 "${PREDEPLOY_BROWSER_EVIDENCE_DIR}/candidate-runtime-verdict.txt"
}

run_predeploy_canonical_gate() {
  local runtime_root="${LOCAL_CANDIDATE_WEB_RUNTIME}"
  local controller_tmp_root=""
  local health_url="${PREDEPLOY_BROWSER_URL}health"
  local base_url="${PREDEPLOY_BROWSER_URL}"
  local verify_receipt="${runtime_root}/controller/canonical-verify.json"
  local acceptance_receipt="${runtime_root}/controller/canonical-acceptance.json"
  local retained_verify="${PREDEPLOY_BROWSER_EVIDENCE_DIR}/canonical-verify.json"
  local retained_acceptance="${PREDEPLOY_BROWSER_EVIDENCE_DIR}/canonical-acceptance.json"

  controller_tmp_root="$(cd /tmp && pwd -P)" || {
    echo "Canonical deploy gate could not resolve the physical temporary directory." >&2
    return 1
  }
  case "${runtime_root}" in
    "${controller_tmp_root%/}"/vkpi-candidate-browser-runtime.*) ;;
    *)
      echo "Canonical deploy gate runtime root is not controller-owned." >&2
      return 1
      ;;
  esac
  if [ -z "${LOCAL_CANDIDATE_WEB_PID}" ] \
    || [ -z "${LOCAL_CANDIDATE_WEB_PGID}" ] \
    || [ "${LOCAL_CANDIDATE_WEB_PID}" != "${LOCAL_CANDIDATE_WEB_PGID}" ] \
    || [ ! -d "${runtime_root}" ] \
    || [ -L "${runtime_root}" ] \
    || [ ! -f "${LOCAL_CANDIDATE_ADMISSION}" ] \
    || [ -L "${LOCAL_CANDIDATE_ADMISSION}" ] \
    || [ ! -f "${LOCAL_CANDIDATE_VERIFY_PROFILE}" ] \
    || [ -L "${LOCAL_CANDIDATE_VERIFY_PROFILE}" ]; then
    echo "Canonical deploy gate requires the live controlled candidate runtime." >&2
    return 1
  fi

  verify_deploy_candidate
  assert_deploy_source_unchanged
  echo "[deploy] gate: frozen candidate strict code + runtime trust verification(全绿才继续)..."
  if ! run_sealed_controller_python \
    "${TRUSTED_CANDIDATE_VERIFIER}" run-deploy-gate \
    --manifest "${DEPLOY_CANDIDATE_MANIFEST}" \
    --snapshot "${DEPLOY_CANDIDATE_DIR}" \
    --expected-head "${LOCAL_GIT_SHA}" \
    --expected-branch "${LOCAL_GIT_BRANCH}" \
    --source "${PROJECT_ROOT}" \
    --admission-json "${LOCAL_CANDIDATE_ADMISSION}" \
    --python "${DEPLOY_PHYSICAL_PYTHON}" \
    --runtime-root "${runtime_root}" \
    --health-env-file "${LOCAL_HEALTH_ENV_FILE}" \
    --health-url "${health_url}" \
    --base-url "${base_url}" \
    --verify-json-out "${verify_receipt}" \
    --acceptance-json-out "${acceptance_receipt}"; then
    echo "[deploy] verify.sh 非零退出 —— 部署中止，未产生远端变更。" >&2
    return 1
  fi

  if [ ! -f "${verify_receipt}" ] || [ -L "${verify_receipt}" ] \
    || [ ! -f "${acceptance_receipt}" ] || [ -L "${acceptance_receipt}" ]; then
    echo "Canonical deploy gate did not produce safe bound receipts." >&2
    return 1
  fi

  install -m 0600 "${verify_receipt}" "${retained_verify}"
  install -m 0600 "${acceptance_receipt}" "${retained_acceptance}"
  cmp -s "${verify_receipt}" "${retained_verify}"
  cmp -s "${acceptance_receipt}" "${retained_acceptance}"
  verify_deploy_candidate
  assert_deploy_source_unchanged
}

run_predeploy_final_runtime_gate() {
  local health_tmp=""
  local health_path="${PREDEPLOY_BROWSER_EVIDENCE_DIR}/candidate-final-health.json"
  local runtime_verdict="${PREDEPLOY_BROWSER_EVIDENCE_DIR}/candidate-final-runtime-verdict.txt"
  local redis_verdict="${PREDEPLOY_BROWSER_EVIDENCE_DIR}/candidate-final-redis-verdict.txt"

  health_tmp="$(
    mktemp "${PREDEPLOY_BROWSER_EVIDENCE_DIR}/.candidate-final-health.XXXXXX"
  )"
  chmod 600 "${health_tmp}"
  if ! env -i \
    PATH="${BROWSER_GATE_CONTROLLER_PATH}" \
    HOME=/tmp \
    TMPDIR=/tmp \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    "${LOCAL_SAFE_PYTHON}" -I -B \
    "${PROJECT_ROOT}/scripts/ops/fetch_runtime_health.py" \
    --url "${PREDEPLOY_BROWSER_URL}health" \
    --env-file "${LOCAL_HEALTH_ENV_FILE}" \
    --timeout-seconds 3 >"${health_tmp}"; then
    rm -f -- "${health_tmp}"
    echo "Final authenticated candidate health fetch failed; no remote change was made." >&2
    return 1
  fi
  rm -f -- "${health_path}"
  mv -- "${health_tmp}" "${health_path}"
  chmod 600 "${health_path}"
  rm -f -- "${runtime_verdict}" "${redis_verdict}"
  : >"${runtime_verdict}"
  : >"${redis_verdict}"
  chmod 600 "${runtime_verdict}" "${redis_verdict}"

  if ! run_frozen_candidate_python \
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_runtime_health.py" \
    --expected-head "${LOCAL_GIT_SHA}" \
    --expected-migration "${LATEST_MIGRATION}" \
    --require-worker \
    --expected-worker-count "${EXPECTED_WORKER_COUNT}" \
    --max-worker-age-seconds 180 \
    <"${health_path}" >"${runtime_verdict}"; then
    echo "Final candidate 16-worker runtime contract failed; no remote change was made." >&2
    return 1
  fi
  if ! run_frozen_candidate_python \
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_redis_worker_health.py" \
    --expected-head "${LOCAL_GIT_SHA}" \
    --expected-count 1 \
    --max-age-seconds 180 \
    <"${health_path}" >"${redis_verdict}"; then
    echo "Final candidate Redis-worker runtime contract failed; no remote change was made." >&2
    return 1
  fi
}

run_predeploy_embedded_browser_gate() {
  local token=""
  local capture_status=0
  local capture_path="${PREDEPLOY_BROWSER_EVIDENCE_DIR}/capture.json"
  local report_path="${PREDEPLOY_BROWSER_EVIDENCE_DIR}/gate-report.json"
  local failure_log=""

  verify_deploy_candidate
  assert_deploy_source_unchanged
  mkdir -p -- "${PREDEPLOY_BROWSER_EVIDENCE_DIR}"
  chmod 700 "${PREDEPLOY_BROWSER_EVIDENCE_DIR}"
  rm -f -- "${capture_path}" "${report_path}"

  start_local_candidate_browser_runtime
  run_predeploy_canonical_gate

  if ! token="$(
    env -i \
      PATH="${BROWSER_GATE_CONTROLLER_PATH}" \
      HOME="${LOCAL_CANDIDATE_WEB_RUNTIME}/home" \
      XDG_CACHE_HOME="${LOCAL_CANDIDATE_WEB_RUNTIME}/cache" \
      TMPDIR="${LOCAL_CANDIDATE_WEB_RUNTIME}/tmp" \
      LANG=C.UTF-8 \
      PYTHONDONTWRITEBYTECODE=1 \
      ENVIRONMENT=local \
      LOCAL_ENV_FILE="${LOCAL_CANDIDATE_RUNTIME_ENV}" \
      RUNTIME_ENV_KEEP_DB_URL=1 \
      RUNTIME_ROOT="${LOCAL_CANDIDATE_WEB_RUNTIME}/runtime" \
      RUNTIME_ENV_QUIET=1 \
      VKPI_SAFE_PYTHON_CONTROLLER_RUNTIME_ROOT="${LOCAL_CANDIDATE_WEB_RUNTIME}" \
      VKPI_SAFE_PYTHON_REAL="${DEPLOY_PHYSICAL_PYTHON}" \
      LOG_LEVEL=CRITICAL \
      /usr/bin/sandbox-exec -f "${LOCAL_CANDIDATE_VERIFY_PROFILE}" \
      "${DEPLOY_CANDIDATE_DIR}/scripts/ops/safe_python.sh" -I -B - \
      "${BROWSER_GATE_TOKEN_TTL_SECONDS}" \
      "${DEPLOY_CANDIDATE_DIR}/scripts" \
      "${DEPLOY_CANDIDATE_DIR}/backend" <<'PY'
import sys

sys.path[:0] = sys.argv[2:4]
from local_release_acceptance import create_local_auth_context

print(create_local_auth_context(int(sys.argv[1])).token, end="")
PY
  )"; then
    echo "Local embedded-production browser token mint failed; no remote change was made." >&2
    return 1
  fi
  if ! [[ "${token}" =~ ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]]; then
    token=""
    echo "Local embedded-production browser token is not a compact JWT." >&2
    return 1
  fi

  failure_log="$(
    mktemp "${PREDEPLOY_BROWSER_EVIDENCE_DIR}/browser-capture-failure.log.XXXXXX"
  )"
  chmod 600 "${failure_log}"
  env -i \
    PATH="${BROWSER_GATE_CONTROLLER_PATH}" \
    HOME="${BROWSER_GATE_OS_HOME}" \
    USER="${BROWSER_GATE_OS_USER}" \
    LOGNAME="${BROWSER_GATE_OS_USER}" \
    XDG_CACHE_HOME=/tmp \
    TMPDIR=/tmp \
    LANG=C.UTF-8 \
    VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS="${BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS}" \
    VKPI_BROWSER_GATE_TOKEN="${token}" \
    node \
    "${DEPLOY_CANDIDATE_DIR}/scripts/capture_browser_console_cdp.mjs" \
    --url "${PREDEPLOY_BROWSER_URL}" \
    --output "${capture_path}" \
    --settle-ms "${BROWSER_GATE_SETTLE_MS}" \
    --page-settle-ms "${BROWSER_GATE_PAGE_SETTLE_MS}" \
    --page-timeout-ms "${BROWSER_GATE_PAGE_TIMEOUT_MS}" \
    --overall-timeout-ms "${BROWSER_GATE_OVERALL_TIMEOUT_MS}" \
    --chrome "${POST_DEPLOY_CHROME_PATH}" >/dev/null 2>"${failure_log}" || capture_status=$?
  token=""
  if [ "${capture_status}" -ne 0 ]; then
    echo "Local embedded-production browser capture failed; stage evidence retained at ${failure_log}; no remote change was made." >&2
    return "${capture_status}"
  fi
  rm -f -- "${failure_log}"

  run_frozen_candidate_python \
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_browser_console_capture.py" \
    --input "${capture_path}" \
    --json-out "${report_path}" \
    --expected-git-sha "${BROWSER_EXPECTED_GIT_SHA}" \
    --expected-app-asset "${BROWSER_EXPECTED_APP_ASSET}" \
    --expected-app-asset-sha256 "${BROWSER_EXPECTED_APP_ASSET_SHA256}" >/dev/null
  "${LOCAL_SAFE_PYTHON}" -B - "${report_path}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
overall = payload.get("overall") or {}
pages = (payload.get("metrics") or {}).get("pages") or {}
if (
    payload.get("schema_version") != "vkpi-browser-console-gate/v1"
    or overall.get("pass") is not True
    or overall.get("release_eligible") is not True
    or pages.get("required") != 21
    or pages.get("captured") != 21
    or pages.get("passed") != 21
    or pages.get("missing") not in ([], None)
):
    raise SystemExit("local embedded-production browser receipt is incomplete")
PY
  chmod 600 "${capture_path}" "${report_path}"
  run_predeploy_final_runtime_gate

  # The candidate process and its loopback listener must be gone before the
  # first SSH call. Browser execution must also not race a source edit or swap
  # in a different candidate after the canonical gate.
  cleanup_local_candidate_browser_runtime
  assert_deploy_source_unchanged
  verify_deploy_candidate
  echo "[deploy] isolated frozen-candidate browser gate passed: 21/21 (${PREDEPLOY_BROWSER_EVIDENCE_DIR})"
}

capture_remote_sync_unit_state() {
  local captured active_state unit_file_state
  if ! captured="$(ssh "${SSH_TARGET}" "for unit in '${SYNC_SERVICE}' '${SYNC_TIMER}' '${HEALTH_SENTINEL_SERVICE}' '${HEALTH_SENTINEL_TIMER}'; do [ \"\$(systemctl show --property LoadState --value \"\${unit}\")\" = loaded ] || { echo \"reviewed timer/service is not loaded: \${unit}\" >&2; exit 1; }; done; printf '%s:%s:%s:%s:%s:%s:%s:%s\n' \"\$(systemctl show --property ActiveState --value '${SYNC_SERVICE}')\" \"\$(systemctl show --property UnitFileState --value '${SYNC_SERVICE}')\" \"\$(systemctl show --property ActiveState --value '${SYNC_TIMER}')\" \"\$(systemctl show --property UnitFileState --value '${SYNC_TIMER}')\" \"\$(systemctl show --property ActiveState --value '${HEALTH_SENTINEL_SERVICE}')\" \"\$(systemctl show --property UnitFileState --value '${HEALTH_SENTINEL_SERVICE}')\" \"\$(systemctl show --property ActiveState --value '${HEALTH_SENTINEL_TIMER}')\" \"\$(systemctl show --property UnitFileState --value '${HEALTH_SENTINEL_TIMER}')\"")"; then
    echo "Refusing deploy because the reviewed sync/sentinel service and timer state is unreadable." >&2
    return 1
  fi
  IFS=: read -r SYNC_SERVICE_ACTIVE_STATE SYNC_SERVICE_UNIT_FILE_STATE \
    SYNC_TIMER_ACTIVE_STATE SYNC_TIMER_UNIT_FILE_STATE \
    HEALTH_SENTINEL_SERVICE_ACTIVE_STATE HEALTH_SENTINEL_SERVICE_UNIT_FILE_STATE \
    HEALTH_SENTINEL_TIMER_ACTIVE_STATE HEALTH_SENTINEL_TIMER_UNIT_FILE_STATE <<<"${captured}"
  for active_state in \
    "${SYNC_SERVICE_ACTIVE_STATE}" "${SYNC_TIMER_ACTIVE_STATE}" \
    "${HEALTH_SENTINEL_SERVICE_ACTIVE_STATE}" "${HEALTH_SENTINEL_TIMER_ACTIVE_STATE}"; do
    case "${active_state}" in
    active|activating|inactive) ;;
    *)
      echo "Refusing deploy because a reviewed sync/sentinel unit has an unrestorable active state: ${active_state}." >&2
      return 1
      ;;
    esac
  done
  for unit_file_state in \
    "${SYNC_SERVICE_UNIT_FILE_STATE}" "${SYNC_TIMER_UNIT_FILE_STATE}" \
    "${HEALTH_SENTINEL_SERVICE_UNIT_FILE_STATE}" "${HEALTH_SENTINEL_TIMER_UNIT_FILE_STATE}"; do
    case "${unit_file_state}" in
    enabled|enabled-runtime|linked|linked-runtime|alias|static|indirect|disabled|generated|transient|masked|masked-runtime) ;;
    *)
      echo "Refusing deploy because a reviewed sync/sentinel unit has an unrestorable unit-file state: ${unit_file_state}." >&2
      return 1
      ;;
    esac
  done
  SYNC_UNITS_CAPTURED=1
}

capture_remote_pgbouncer_unit_state() {
  local captured
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    return 0
  fi
  if [ "${PGBOUNCER_SERVICE}" != "pgbouncer.service" ] \
    || [ "${PGBOUNCER_SOCKET}" != "pgbouncer.socket" ] \
    || [ "${PGBOUNCER_PORT}" != "6432" ]; then
    echo "Refusing staging clone because the PgBouncer service/socket scope is not reviewed." >&2
    return 1
  fi
  if ! captured="$(ssh "${SSH_TARGET}" "command -v ss >/dev/null || { echo 'ss is required for PgBouncer listener verification' >&2; exit 1; }; exec_start=\$(systemctl show --property ExecStart --value '${PGBOUNCER_SERVICE}'); case \"\${exec_start}\" in *'/usr/sbin/pgbouncer /etc/pgbouncer/pgbouncer.ini'*) ;; *) echo 'PgBouncer ExecStart is outside the reviewed config boundary' >&2; exit 1;; esac; [ \"\$(printf '%s' \"\${exec_start}\" | grep -oF '/etc/pgbouncer/pgbouncer.ini' | wc -l)\" -eq 1 ] || { echo 'PgBouncer ExecStart has an ambiguous config path' >&2; exit 1; }; service_load=\$(systemctl show --property LoadState --value '${PGBOUNCER_SERVICE}'); service_active=\$(systemctl show --property ActiveState --value '${PGBOUNCER_SERVICE}'); service_file=\$(systemctl show --property UnitFileState --value '${PGBOUNCER_SERVICE}'); socket_load=\$(systemctl show --property LoadState --value '${PGBOUNCER_SOCKET}'); socket_active=\$(systemctl show --property ActiveState --value '${PGBOUNCER_SOCKET}'); socket_file=\$(systemctl show --property UnitFileState --value '${PGBOUNCER_SOCKET}'); printf '%s:%s:%s:%s:%s:%s\n' \"\${service_load}\" \"\${service_active}\" \"\${service_file}\" \"\${socket_load}\" \"\${socket_active}\" \"\${socket_file}\"")"; then
    echo "Refusing staging clone because PgBouncer service/socket state is unreadable." >&2
    return 1
  fi
  IFS=: read -r PGBOUNCER_SERVICE_LOAD_STATE PGBOUNCER_SERVICE_ACTIVE_STATE \
    PGBOUNCER_SERVICE_UNIT_FILE_STATE PGBOUNCER_SOCKET_LOAD_STATE \
    PGBOUNCER_SOCKET_ACTIVE_STATE PGBOUNCER_SOCKET_UNIT_FILE_STATE <<<"${captured}"
  if [ "${PGBOUNCER_SERVICE_LOAD_STATE}" != "loaded" ] \
    || [ "${PGBOUNCER_SOCKET_LOAD_STATE}" != "loaded" ]; then
    echo "Refusing staging clone because the reviewed PgBouncer units are not loaded." >&2
    return 1
  fi
  for active_state in "${PGBOUNCER_SERVICE_ACTIVE_STATE}" "${PGBOUNCER_SOCKET_ACTIVE_STATE}"; do
    case "${active_state}" in
      active|inactive) ;;
      *)
        echo "Refusing staging clone because a PgBouncer unit has an unrestorable active state: ${active_state}." >&2
        return 1
        ;;
    esac
  done
  for unit_file_state in "${PGBOUNCER_SERVICE_UNIT_FILE_STATE}" "${PGBOUNCER_SOCKET_UNIT_FILE_STATE}"; do
    case "${unit_file_state}" in
      enabled|disabled|static|indirect) ;;
      *)
        echo "Refusing staging clone because a PgBouncer unit has an unrestorable unit-file state: ${unit_file_state}." >&2
        return 1
        ;;
    esac
  done
  PGBOUNCER_STATE_CAPTURED=1
}

capture_remote_web_database_runtime() {
  local expected_database="$1" runtime_json pool_effective
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    return 0
  fi
  if ! runtime_json="$(ssh "${SSH_TARGET}" \
    "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - '${SERVICE_NAME}' '${expected_database}'" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qsl, unquote, urlsplit

service, expected_database = sys.argv[1:]
if not re.fullmatch(r"[A-Za-z0-9@_.-]+\.service", service):
    raise SystemExit("web service identity is invalid")
if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", expected_database):
    raise SystemExit("expected database identity is invalid")
main_pid = subprocess.run(
    ["systemctl", "show", "--property", "MainPID", "--value", service],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
if not main_pid.isdigit() or int(main_pid) <= 0:
    raise SystemExit("web service MainPID is invalid")
raw = Path(f"/proc/{main_pid}/environ").read_bytes()
values: dict[str, str] = {}
for entry in raw.split(b"\0"):
    if not entry or b"=" not in entry:
        continue
    key, value = entry.split(b"=", 1)
    name = key.decode("ascii")
    if name in values:
        raise SystemExit("web process environment contains a duplicate key")
    values[name] = value.decode("utf-8")

def boolean(name: str) -> bool:
    value = values.get(name, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} is not an explicit boolean")

def endpoint(name: str, expected_port: int) -> dict[str, object]:
    value = values.get(name, "")
    try:
        parsed = urlsplit(value)
        port = parsed.port if parsed.port is not None else 5432
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise SystemExit(f"{name} is invalid") from None
    safe_query_parameters = {
        "application_name", "channel_binding", "connect_timeout",
        "fallback_application_name", "gssencmode", "keepalives",
        "keepalives_count", "keepalives_idle", "keepalives_interval",
        "ssl_min_protocol_version", "ssl_max_protocol_version", "sslcrl",
        "sslcrldir", "sslmode", "sslrootcert", "sslsni",
        "tcp_user_timeout",
    }
    database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != "127.0.0.1"
        or port != expected_port
        or parsed.fragment
        or parsed.path.count("/") != 1
        or database != expected_database
        or any(key.lower() not in safe_query_parameters for key, _value in query)
    ):
        raise SystemExit(f"{name} does not match the reviewed database endpoint")
    return {"host": "127.0.0.1", "port": port, "database_name": database}

direct = endpoint("DATABASE_URL", 5432)
pool = endpoint("DATABASE_POOL_URL", 6432)
print(json.dumps({
    "schema_version": "vkpi-effective-web-database/v1",
    "operation": "inspect-effective-runtime",
    "service": service,
    "main_pid": int(main_pid),
    "pool_enabled": boolean("DB_USE_PGBOUNCER"),
    "direct": direct,
    "pool": pool,
    "credentials_included": False,
}, sort_keys=True, separators=(",", ":")))
PY
  )"; then
    echo "Refusing staging clone because the effective Web database runtime is unproven." >&2
    return 1
  fi
  if ! pool_effective="$(printf '%s' "${runtime_json}" \
    | run_local_python_program 'import json,os,re,sys
p=json.load(os.fdopen(3))
expected=sys.argv[1]
assert p["schema_version"]=="vkpi-effective-web-database/v1"
assert p["operation"]=="inspect-effective-runtime"
assert p["credentials_included"] is False
assert isinstance(p["main_pid"],int) and p["main_pid"]>0
assert p["direct"]=={"host":"127.0.0.1","port":5432,"database_name":expected}
assert p["pool"]=={"host":"127.0.0.1","port":6432,"database_name":expected}
assert isinstance(p["pool_enabled"],bool)
print("1" if p["pool_enabled"] else "0")' "${expected_database}")"; then
    echo "Refusing staging clone because the effective Web runtime receipt is invalid." >&2
    return 1
  fi
  PGBOUNCER_WEB_POOL_EFFECTIVE="${pool_effective}"
}

verify_remote_web_database_runtime() {
  local expected_database="$1" expected_pool_effective="$2" observed
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    return 0
  fi
  observed="${PGBOUNCER_WEB_POOL_EFFECTIVE}"
  if ! capture_remote_web_database_runtime "${expected_database}"; then
    PGBOUNCER_WEB_POOL_EFFECTIVE="${observed}"
    return 1
  fi
  if [ "${PGBOUNCER_WEB_POOL_EFFECTIVE}" != "${expected_pool_effective}" ]; then
    echo "Effective Web PgBouncer mode changed across the release boundary." >&2
    PGBOUNCER_WEB_POOL_EFFECTIVE="${observed}"
    return 1
  fi
  PGBOUNCER_WEB_POOL_EFFECTIVE="${expected_pool_effective}"
}

capture_remote_pgbouncer_database_map() {
  local inspect_json
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    return 0
  fi
  if ! inspect_json="$(ssh "${SSH_TARGET}" \
    "sudo -n -u postgres env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 python3 -B - inspect --config '${PGBOUNCER_CONFIG_PATH}' --source-db '${STAGING_SOURCE_DATABASE}'" \
    <"${DEPLOY_CANDIDATE_DIR}/scripts/ops/pgbouncer_release_map.py")"; then
    echo "Refusing staging clone because the PgBouncer source mapping is unsafe." >&2
    return 1
  fi
  if ! PGBOUNCER_MAP_CONFIG_SHA_BEFORE="$(printf '%s' "${inspect_json}" \
    | run_local_python_program 'import json,os,re,sys
p=json.load(os.fdopen(3))
source=sys.argv[1]
assert p["schema_version"]=="vkpi-pgbouncer-release-map/v1"
assert p["operation"]=="inspect"
assert p["mapping_endpoint"]=="127.0.0.1:5432"
assert p["database_mapping_credentials_included"] is False
assert source in p["databases"]
assert p["mapping_count"]==len(p["databases"]) and p["mapping_count"]>=1
assert re.fullmatch(r"[0-9a-f]{64}",p["config_sha256"])
print(p["config_sha256"])' "${STAGING_SOURCE_DATABASE}")"; then
    echo "Refusing staging clone because the PgBouncer source-map receipt is invalid." >&2
    return 1
  fi
  PGBOUNCER_MAP_CAPTURED=1
}

prepare_remote_pgbouncer_database_map() {
  local prepare_json
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    return 0
  fi
  if [ "${PGBOUNCER_MAP_CAPTURED}" != "1" ] \
    || [ "${PGBOUNCER_QUIESCED}" != "1" ]; then
    echo "Refusing PgBouncer map mutation without captured config and quiesced units." >&2
    return 1
  fi
  PGBOUNCER_MAP_MUTATION_INTENT=1
  if ! prepare_json="$(ssh "${SSH_TARGET}" \
    "sudo -n -u postgres env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/pgbouncer_release_map.py' prepare --config '${PGBOUNCER_CONFIG_PATH}' --source-db '${STAGING_SOURCE_DATABASE}' --target-db '${STAGING_CLONE_DATABASE}' --backup '${PGBOUNCER_MAP_BACKUP_PATH}' --receipt '${PGBOUNCER_MAP_RECEIPT_PATH}' --expected-sha256 '${PGBOUNCER_MAP_CONFIG_SHA_BEFORE}'")"; then
    echo "PgBouncer dual-map preparation failed; rollback recovery is armed." >&2
    return 1
  fi
  if ! PGBOUNCER_MAP_CONFIG_SHA_AFTER="$(printf '%s' "${prepare_json}" \
    | run_local_python_program 'import json,os,re,sys
p=json.load(os.fdopen(3))
source,target,before=sys.argv[1:]
assert p["schema_version"]=="vkpi-pgbouncer-release-map/v1"
assert p["operation"]=="prepare"
assert p["databases"]==[source,target] and p["mapping_count"]==2
assert p["mapping_endpoint"]=="127.0.0.1:5432"
assert p["database_mapping_credentials_included"] is False
assert p["config_sha256_before"]==before
assert p["backup_sha256"]==before
assert p["config_sha256_after"]==p["config_sha256"]
assert re.fullmatch(r"[0-9a-f]{64}",p["config_sha256_after"])
print(p["config_sha256_after"])' \
      "${STAGING_SOURCE_DATABASE}" "${STAGING_CLONE_DATABASE}" \
      "${PGBOUNCER_MAP_CONFIG_SHA_BEFORE}")"; then
    echo "PgBouncer dual-map preparation receipt is invalid." >&2
    return 1
  fi
  PGBOUNCER_MAP_PREPARED=1
}

verify_remote_pgbouncer_database_map() {
  local verify_json
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    return 0
  fi
  if [ "${PGBOUNCER_MAP_PREPARED}" != "1" ] \
    || ! [[ "${PGBOUNCER_MAP_CONFIG_SHA_AFTER}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Cannot verify PgBouncer dual map without its prepared receipt." >&2
    return 1
  fi
  if ! verify_json="$(ssh "${SSH_TARGET}" \
    "sudo -n -u postgres env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/pgbouncer_release_map.py' verify --config '${PGBOUNCER_CONFIG_PATH}' --source-db '${STAGING_SOURCE_DATABASE}' --target-db '${STAGING_CLONE_DATABASE}' --expected-sha256 '${PGBOUNCER_MAP_CONFIG_SHA_AFTER}'")"; then
    echo "PgBouncer dual-map verification failed." >&2
    return 1
  fi
  printf '%s' "${verify_json}" | run_local_python_program 'import json,os,sys
p=json.load(os.fdopen(3))
source,target,expected=sys.argv[1:]
assert p["schema_version"]=="vkpi-pgbouncer-release-map/v1"
assert p["operation"]=="verify" and p["verified"] is True
assert p["databases"]==[source,target] and p["mapping_count"]==2
assert p["config_sha256"]==expected
assert p["mapping_endpoint"]=="127.0.0.1:5432"
assert p["database_mapping_credentials_included"] is False' \
    "${STAGING_SOURCE_DATABASE}" "${STAGING_CLONE_DATABASE}" \
    "${PGBOUNCER_MAP_CONFIG_SHA_AFTER}"
}

restore_remote_pgbouncer_database_map() {
  local restore_json
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ] \
    || [ "${PGBOUNCER_MAP_MUTATION_INTENT}" != "1" ]; then
    return 0
  fi
  if [ "${PGBOUNCER_QUIESCED}" != "1" ] \
    || ! [[ "${PGBOUNCER_MAP_CONFIG_SHA_BEFORE}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Cannot restore PgBouncer config without quiesced units and captured hash." >&2
    return 1
  fi
  if ! restore_json="$(ssh "${SSH_TARGET}" \
    "set -eu; backup='${PGBOUNCER_MAP_BACKUP_PATH}'; receipt='${PGBOUNCER_MAP_RECEIPT_PATH}'; helper='${REMOTE_RELEASE_DIR}/scripts/ops/pgbouncer_release_map.py'; if sudo -n -u postgres test -e \"\${backup}\" && sudo -n -u postgres test -e \"\${receipt}\"; then sudo -n -u postgres env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B \"\${helper}\" restore-original --config '${PGBOUNCER_CONFIG_PATH}' --backup \"\${backup}\" --receipt \"\${receipt}\"; elif ! sudo -n -u postgres test -e \"\${receipt}\"; then sudo -n -u postgres env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B \"\${helper}\" inspect --config '${PGBOUNCER_CONFIG_PATH}' --source-db '${STAGING_SOURCE_DATABASE}'; else echo 'PgBouncer map receipt exists without its backup' >&2; exit 1; fi")"; then
    echo "CRITICAL: PgBouncer original config could not be recovered." >&2
    return 1
  fi
  if ! printf '%s' "${restore_json}" | run_local_python_program 'import json,os,sys
p=json.load(os.fdopen(3))
source,expected=sys.argv[1:]
assert p["schema_version"]=="vkpi-pgbouncer-release-map/v1"
assert p["operation"] in {"restore-original","inspect"}
assert p["config_sha256"]==expected
assert source in p["databases"]
assert p["mapping_endpoint"]=="127.0.0.1:5432"
assert p["database_mapping_credentials_included"] is False
if p["operation"]=="restore-original":
    assert p["restored"] is True and p["backup_sha256"]==expected' \
      "${STAGING_SOURCE_DATABASE}" "${PGBOUNCER_MAP_CONFIG_SHA_BEFORE}"; then
    echo "CRITICAL: PgBouncer original-config recovery receipt is invalid." >&2
    return 1
  fi
  PGBOUNCER_MAP_RESTORED=1
}

probe_remote_pgbouncer_database() {
  local expected_database="$1" probe_json
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    return 0
  fi
  if [ "${PGBOUNCER_RESTORED}" != "1" ]; then
    echo "Cannot probe PgBouncer before its reviewed service state is restored." >&2
    return 1
  fi
  if ! probe_json="$(ssh "${SSH_TARGET}" \
    "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i HOME=/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/pgbouncer_release_map.py' probe --env-file '${REMOTE_ROOT}/.env' --expected-db '${expected_database}'")"; then
    echo "PgBouncer route probe failed for the reviewed database alias." >&2
    return 1
  fi
  printf '%s' "${probe_json}" | run_local_python_program 'import json,os,sys
p=json.load(os.fdopen(3))
expected=sys.argv[1]
assert p=={
  "schema_version":"vkpi-pgbouncer-release-map/v1",
  "operation":"probe",
  "connected":True,
  "database_name":expected,
  "mapping_endpoint":"127.0.0.1:6432",
  "credentials_included":False,
}' "${expected_database}"
}

restore_remote_pgbouncer_state() {
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    return 0
  fi
  if [ "${PGBOUNCER_MAY_HAVE_BEEN_MUTATED}" != "1" ]; then
    return 0
  fi
  if [ "${PGBOUNCER_STATE_CAPTURED}" != "1" ]; then
    echo "Cannot restore PgBouncer without a captured pre-mutation state." >&2
    return 1
  fi
  # The remote EXIT trap is deliberately fail-closed: if a pre/postcondition
  # fails after unmask/start, both activation paths are stopped and runtime
  # masked again.  No persistent enablement or PgBouncer config is modified.
  if ! ssh "${SSH_TARGET}" \
    "bash -s -- '${PGBOUNCER_SERVICE}' '${PGBOUNCER_SOCKET}' '${PGBOUNCER_PORT}' '${PGBOUNCER_SERVICE_LOAD_STATE}' '${PGBOUNCER_SERVICE_ACTIVE_STATE}' '${PGBOUNCER_SERVICE_UNIT_FILE_STATE}' '${PGBOUNCER_SOCKET_LOAD_STATE}' '${PGBOUNCER_SOCKET_ACTIVE_STATE}' '${PGBOUNCER_SOCKET_UNIT_FILE_STATE}'" <<'REMOTE_PGBOUNCER_RESTORE'
set -euo pipefail
service="$1"
socket="$2"
port="$3"
service_load="$4"
service_active="$5"
service_file="$6"
socket_load="$7"
socket_active="$8"
socket_file="$9"
restore_complete=0
fail_closed_pgbouncer() {
  if [ "${restore_complete}" != 1 ]; then
    sudo systemctl stop "${socket}" "${service}" >/dev/null 2>&1 || true
    sudo systemctl mask --runtime "${socket}" "${service}" >/dev/null 2>&1 || true
  fi
}
runtime_mask_state() {
  local unit="$1" mask_path="/run/systemd/system/$1"
  if [ -L "${mask_path}" ] && [ "$(readlink -- "${mask_path}")" = /dev/null ]; then
    printf 'masked'
  elif [ ! -e "${mask_path}" ] && [ ! -L "${mask_path}" ]; then
    printf 'clear'
  else
    printf 'invalid'
  fi
}
trap fail_closed_pgbouncer EXIT
service_mask="$(runtime_mask_state "${service}")"
socket_mask="$(runtime_mask_state "${socket}")"
case "${service_mask}:${socket_mask}" in
  masked:masked)
    sudo systemctl unmask --runtime "${socket}" "${service}" >/dev/null
    sudo systemctl daemon-reload
    ;;
  clear:clear)
    ;;
  *)
    echo "PgBouncer runtime masks are mixed or unsafe before restore" >&2
    exit 1
    ;;
esac
[ "$(systemctl show --property LoadState --value "${service}")" = "${service_load}" ] \
  && [ "$(systemctl show --property LoadState --value "${socket}")" = "${socket_load}" ] \
  || { echo "PgBouncer LoadState changed during restore" >&2; exit 1; }
[ "$(systemctl show --property UnitFileState --value "${service}")" = "${service_file}" ] \
  && [ "$(systemctl show --property UnitFileState --value "${socket}")" = "${socket_file}" ] \
  || { echo "PgBouncer UnitFileState was not restored" >&2; exit 1; }
sudo systemctl stop "${socket}" "${service}"
[ "${socket_active}" != active ] || sudo systemctl start "${socket}"
[ "${service_active}" != active ] || sudo systemctl start "${service}"
[ "$(systemctl show --property ActiveState --value "${service}")" = "${service_active}" ] \
  && [ "$(systemctl show --property ActiveState --value "${socket}")" = "${socket_active}" ] \
  || { echo "PgBouncer service/socket ActiveState was not restored" >&2; exit 1; }
[ "$(systemctl show --property UnitFileState --value "${service}")" = "${service_file}" ] \
  && [ "$(systemctl show --property UnitFileState --value "${socket}")" = "${socket_file}" ] \
  || { echo "PgBouncer service/socket UnitFileState changed after activation" >&2; exit 1; }
if [ "${service_active}" = active ]; then
  main_pid="$(systemctl show --property MainPID --value "${service}")"
  case "${main_pid}" in ""|0|*[!0-9]*)
    echo "PgBouncer service MainPID is invalid after restore" >&2
    exit 1
  esac
fi
if [ "${service_active}" = active ] || [ "${socket_active}" = active ]; then
  listener_ready=0
  for _attempt in $(seq 1 20); do
    if ss -H -ltn "sport = :${port}" | grep -q .; then
      listener_ready=1
      break
    fi
    sleep 0.25
  done
  [ "${listener_ready}" = 1 ] \
    || { echo "PgBouncer listener did not return after restore" >&2; exit 1; }
elif ss -H -ltn "sport = :${port}" | grep -q .; then
  echo "PgBouncer listener is present after inactive restore" >&2
  exit 1
fi
restore_complete=1
trap - EXIT
REMOTE_PGBOUNCER_RESTORE
  then
    echo "CRITICAL: captured PgBouncer service/socket state could not be restored; both remain fail-closed." >&2
    return 1
  fi
  PGBOUNCER_RESTORED=1
}

inspect_remote_sync_unit_restore_receipt() {
  if [ "${SYNC_UNITS_CAPTURED}" != "1" ]; then
    echo "Cannot inspect sync-unit restore state without a captured pre-mutation state." >&2
    return 1
  fi
  # Read-only, exact receipt used after an SSH acknowledgement is lost.  Its
  # success vocabulary is fixed and contains no unit output or environment
  # values, so diagnostics cannot accidentally disclose production settings.
  ssh "${SSH_TARGET}" "bash -s -- \
    '${SYNC_SERVICE}' '${SYNC_SERVICE_ACTIVE_STATE}' '${SYNC_SERVICE_UNIT_FILE_STATE}' service \
    '${SYNC_TIMER}' '${SYNC_TIMER_ACTIVE_STATE}' '${SYNC_TIMER_UNIT_FILE_STATE}' sync-timer \
    '${HEALTH_SENTINEL_SERVICE}' '${HEALTH_SENTINEL_SERVICE_ACTIVE_STATE}' '${HEALTH_SENTINEL_SERVICE_UNIT_FILE_STATE}' service \
    '${HEALTH_SENTINEL_TIMER}' '${HEALTH_SENTINEL_TIMER_ACTIVE_STATE}' '${HEALTH_SENTINEL_TIMER_UNIT_FILE_STATE}' timer" <<'REMOTE_INSPECT_REVIEWED_TIMERS'
set -euo pipefail
receipt=restored

inspect_unit() {
  local unit="$1" expected_active="$2" expected_file="$3" kind="$4"
  local should_start=0 observed_load observed_active observed_file
  case "${expected_active}" in active|activating) should_start=1 ;; esac
  if [ "${kind}" = sync-timer ] && [ "${expected_file}" = enabled ]; then
    should_start=1
  fi
  observed_load="$(systemctl show --property LoadState --value "${unit}")"
  observed_file="$(systemctl show --property UnitFileState --value "${unit}")"
  observed_active="$(systemctl show --property ActiveState --value "${unit}")"
  if [ "${observed_load}" != loaded ] || [ "${observed_file}" != "${expected_file}" ]; then
    receipt=not-restored
    return 0
  fi
  if [ "${should_start}" = 1 ]; then
    case "${observed_active}" in active|activating) ;; *) receipt=not-restored ;; esac
  elif [ "${observed_active}" != inactive ]; then
    receipt=not-restored
  fi
}

while [ "$#" -gt 0 ]; do
  inspect_unit "$1" "$2" "$3" "$4"
  shift 4
done
printf 'vkpi-sync-unit-restore/v1:%s\n' "${receipt}"
REMOTE_INSPECT_REVIEWED_TIMERS
}

reconcile_remote_sync_unit_restore() {
  local receipt=""
  if [ "${SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED}" != "1" ]; then
    return 0
  fi
  if ! receipt="$(inspect_remote_sync_unit_restore_receipt 2>/dev/null)"; then
    SYNC_UNITS_RESTORE_RECONCILE_STATE="unknown"
    echo "[deploy] CRITICAL: sync-unit restore state is unknown after SSH acknowledgement loss." >&2
    return 1
  fi
  case "${receipt}" in
    vkpi-sync-unit-restore/v1:restored)
      SYNC_UNITS_RESTORE_RECONCILE_STATE="restored"
      SYNC_UNITS_RESTORED=1
      SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED=0
      echo "[deploy] recovered exact sync-unit restore receipt after a lost SSH acknowledgement." >&2
      return 0
      ;;
    vkpi-sync-unit-restore/v1:not-restored)
      SYNC_UNITS_RESTORE_RECONCILE_STATE="not-restored"
      echo "[deploy] CRITICAL: exact sync-unit receipt proves the final restore is incomplete." >&2
      return 1
      ;;
    *)
      SYNC_UNITS_RESTORE_RECONCILE_STATE="invalid"
      echo "[deploy] CRITICAL: sync-unit restore receipt is invalid after SSH acknowledgement loss." >&2
      return 1
      ;;
  esac
}

restore_remote_sync_unit_state() {
  local retry_count="${1:-0}"
  if [ "${retry_count}" != "0" ] && [ "${retry_count}" != "1" ]; then
    echo "Sync-unit restore retry count is outside the reviewed bound." >&2
    return 1
  fi
  if [ "${SYNC_UNITS_MAY_HAVE_BEEN_MUTATED}" != "1" ]; then
    return 0
  fi
  if [ "${SYNC_UNITS_RESTORED}" = "1" ]; then
    return 0
  fi
  if [ "${SYNC_UNITS_CAPTURED}" != "1" ]; then
    echo "Cannot restore sync units without a captured pre-mutation state." >&2
    return 1
  fi
  # A prior call can have committed remotely even though its SSH channel died.
  # Reconcile read-only first; if the exact receipt proves an incomplete state,
  # the idempotent restore below safely resumes it.
  if [ "${SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED}" = "1" ]; then
    if reconcile_remote_sync_unit_restore; then
      return 0
    fi
    if [ "${SYNC_UNITS_RESTORE_RECONCILE_STATE}" != "not-restored" ]; then
      echo "CRITICAL: sync-unit restore state is not exact enough to resume safely; preserve release ${RELEASE_ID} and reconcile the read-only receipt before retrying. Do not roll back an activated release." >&2
      return 1
    fi
  fi
  SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED=1
  SYNC_UNITS_RESTORED=0
  if ! ssh "${SSH_TARGET}" "bash -s -- \
    '${SYNC_SERVICE}' '${SYNC_SERVICE_ACTIVE_STATE}' '${SYNC_SERVICE_UNIT_FILE_STATE}' service \
    '${SYNC_TIMER}' '${SYNC_TIMER_ACTIVE_STATE}' '${SYNC_TIMER_UNIT_FILE_STATE}' sync-timer \
    '${HEALTH_SENTINEL_SERVICE}' '${HEALTH_SENTINEL_SERVICE_ACTIVE_STATE}' '${HEALTH_SENTINEL_SERVICE_UNIT_FILE_STATE}' service \
    '${HEALTH_SENTINEL_TIMER}' '${HEALTH_SENTINEL_TIMER_ACTIVE_STATE}' '${HEALTH_SENTINEL_TIMER_UNIT_FILE_STATE}' timer" <<'REMOTE_RESTORE_REVIEWED_TIMERS'
set -euo pipefail

restore_unit() {
  local unit="$1" expected_active="$2" expected_file="$3" kind="$4"
  local should_start=0 observed_active observed_file
  if [ "${expected_file}" != masked-runtime ]; then
    sudo systemctl unmask --runtime "${unit}" >/dev/null
  fi
  case "${expected_active}" in active|activating) should_start=1 ;; esac
  # Preserve the reviewed sync-timer safety invariant: an enabled daily timer
  # is made live even if an earlier incident left it transiently inactive.
  if [ "${kind}" = sync-timer ] && [ "${expected_file}" = enabled ]; then
    should_start=1
  fi
  if [ "${should_start}" = 1 ]; then
    sudo systemctl start --no-block "${unit}"
  else
    sudo systemctl stop "${unit}"
  fi
  observed_file="$(systemctl show --property UnitFileState --value "${unit}")"
  [ "${observed_file}" = "${expected_file}" ] \
    || { echo "reviewed unit-file state was not restored: ${unit}" >&2; return 1; }
  observed_active="$(systemctl show --property ActiveState --value "${unit}")"
  if [ "${should_start}" = 1 ]; then
    case "${observed_active}" in active|activating) ;;
      *) echo "reviewed unit active state was not restored: ${unit}" >&2; return 1 ;;
    esac
  elif [ "${observed_active}" != inactive ]; then
    echo "reviewed inactive unit was not restored: ${unit}" >&2
    return 1
  fi
}

sudo systemctl daemon-reload
while [ "$#" -gt 0 ]; do
  restore_unit "$1" "$2" "$3" "$4"
  shift 4
done
REMOTE_RESTORE_REVIEWED_TIMERS
  then
    if reconcile_remote_sync_unit_restore; then
      return 0
    fi
    if [ "${SYNC_UNITS_RESTORE_RECONCILE_STATE}" = "not-restored" ] \
      && [ "${retry_count}" = "0" ]; then
      echo "[deploy] exact receipt proves sync restore is incomplete; retrying the idempotent restore once." >&2
      restore_remote_sync_unit_state 1
      return $?
    fi
    echo "CRITICAL: reviewed sync/sentinel state is not confirmed restored; preserve release ${RELEASE_ID}, inspect the exact receipt, and resume this idempotent restore. Do not roll back an activated release." >&2
    return 1
  fi
  if ! reconcile_remote_sync_unit_restore; then
    if [ "${SYNC_UNITS_RESTORE_RECONCILE_STATE}" = "not-restored" ] \
      && [ "${retry_count}" = "0" ]; then
      echo "[deploy] exact receipt remains incomplete after sync restore acknowledgement; retrying once." >&2
      restore_remote_sync_unit_state 1
      return $?
    fi
    echo "CRITICAL: reviewed sync/sentinel mutation returned success but its independent read-only receipt is unconfirmed; preserve release ${RELEASE_ID} and resume reconciliation." >&2
    return 1
  fi
}

reconcile_remote_prepare_commit_state() {
  local captured_state="" presence=""
  if [ "${ROLLBACK_PREPARE_MAY_HAVE_COMMITTED}" != "1" ]; then
    return 0
  fi

  # ``prepare`` writes a digest-bound rollback receipt before atomically
  # changing ``previous``.  Re-read that receipt after a lost SSH acknowledgement
  # instead of assuming either commit or non-commit from the transport status.
  if captured_state="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' rollback-unit-state --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --unit-name '${STAGING_REDIS_WORKER_SERVICE}'" 2>/dev/null)"; then
    if [ "${captured_state}" != "${STAGING_REDIS_WORKER_CAPTURED_STATE}" ]; then
      echo "[deploy] CRITICAL: recovered prepare receipt disagrees with the captured Redis unit state." >&2
      return 1
    fi
    # A verified digest-bound capture is sufficient to make rollback safe even
    # if the final ``previous`` link step did not commit.  Only continue forward
    # when the separate pointer proof below also succeeds.
    ROLLBACK_ARMED=1
    ROLLBACK_PREPARE_MAY_HAVE_COMMITTED=0
    if ! ssh "${SSH_TARGET}" \
      "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - '${REMOTE_ROOT}' '${RELEASE_ID}'" <<'REMOTE_VERIFY_PREPARE_COMMIT'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve(strict=True)
release_id = sys.argv[2]
rollback = root / ".release-controller" / "rollbacks" / release_id
metadata_path = rollback / "metadata.json"
digest_path = rollback / "metadata.sha256"
metadata_bytes = metadata_path.read_bytes()
digest = digest_path.read_text(encoding="ascii").strip()
if hashlib.sha256(metadata_bytes).hexdigest() != digest:
    raise SystemExit("prepare metadata digest mismatch")
metadata = json.loads(metadata_bytes)
if metadata.get("release_id") != release_id:
    raise SystemExit("prepare receipt release id mismatch")
expected_previous = Path(str(metadata.get("active_release") or "")).resolve(strict=True)
observed_previous = (root / "previous").resolve(strict=True)
releases = (root / "releases").resolve(strict=True)
if releases not in expected_previous.parents:
    raise SystemExit("prepare expected previous pointer escapes releases")
if observed_previous != expected_previous:
    raise SystemExit("prepare previous pointer was not committed")
REMOTE_VERIFY_PREPARE_COMMIT
    then
      echo "[deploy] CRITICAL: recovered prepare receipt is rollback-capable but its previous-pointer commit is unproven." >&2
      return 1
    fi
    echo "[deploy] recovered committed atomic prepare after a lost SSH acknowledgement." >&2
    return 0
  fi

  if presence="$(ssh "${SSH_TARGET}" "if sudo test -e '${REMOTE_ROOT}/.release-controller/rollbacks/${RELEASE_ID}'; then echo present; else echo absent; fi" 2>/dev/null)" \
    && [ "${presence}" = absent ]; then
    ROLLBACK_PREPARE_MAY_HAVE_COMMITTED=0
    return 0
  fi
  echo "[deploy] CRITICAL: atomic prepare commit state is unknown after SSH acknowledgement loss." >&2
  return 1
}

attempt_automatic_rollback() {
  local rollback_not_before rollback_health rollback_migration rollback_env_state
  local rollback_database rollback_env_sha256
  local rollback_candidate_health="" rollback_redis_not_before="" rollback_redis_main_pid=""
  local rollback_redis_ready=0
  if [ "${ROLLBACK_PREPARE_MAY_HAVE_COMMITTED}" = "1" ] \
    && ! reconcile_remote_prepare_commit_state; then
    echo "[deploy] CRITICAL: rollback is blocked until the remote prepare receipt can be reconciled." >&2
    return 1
  fi
  if [ "${RELEASE_VALIDATION_FENCE_INSTALL_MAY_HAVE_COMMITTED}" = "1" ] \
    && ! reconcile_remote_release_validation_fence_install; then
    echo "[deploy] CRITICAL: rollback is blocked until the remote validation-fence receipt can be reconciled." >&2
    return 1
  fi
  verify_deploy_verifier_bundle || return 1
  echo "[deploy] acceptance failed; restoring previous application release, environment, database identity, and reviewed units..." >&2
  rollback_not_before="$(ssh "${SSH_TARGET}" "date -u +%Y-%m-%dT%H:%M:%SZ")" || return 1
  # Restore shared env/current/unit state only after every process that can read
  # it has stopped.  Stopping just the Redis worker leaves web and Apify workers
  # executing across the pointer/env switch and creates a mixed-release window.
  if ! ssh "${SSH_TARGET}" "redis_unit=''; if systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then redis_unit='${STAGING_REDIS_WORKER_SERVICE}'; fi; sudo systemctl stop '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS} \${redis_unit}; for unit in '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}; do if systemctl is-active --quiet \"\${unit}\"; then echo \"release consumer failed to stop before rollback: \${unit}\" >&2; exit 1; fi; done; if systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then echo 'Redis worker failed to stop before rollback' >&2; exit 1; fi; if systemctl is-enabled --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then sudo systemctl disable --now '${STAGING_REDIS_WORKER_SERVICE}'; fi"; then
    echo "[deploy] CRITICAL: complete web/worker fleet could not be quiesced before rollback." >&2
    return 1
  fi
  if [ "${PGBOUNCER_MAP_MUTATION_INTENT}" = "1" ]; then
    if ! quiesce_remote_pgbouncer_for_clone; then
      echo "[deploy] CRITICAL: PgBouncer could not be re-quiesced before config rollback." >&2
      return 1
    fi
  fi
  if ! ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' restore --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --unit-dir /etc/systemd/system && sudo systemctl daemon-reload"; then
    echo "[deploy] CRITICAL: filesystem, environment, or unit rollback failed; operator intervention required." >&2
    return 1
  fi
  if [ "${DATABASE_RELEASE_STRATEGY}" = "staging-clone" ] \
    || [ "${DATABASE_RELEASE_STRATEGY}" = "reuse-active-clone" ]; then
    if ! rollback_env_state="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' assert-env --env-file '${REMOTE_ROOT}/.env' --expected-db '${PREDEPLOY_DATABASE_NAME}' ${DATABASE_ENV_ASSERT_RUNTIME_POOL_FLAG}")"; then
      echo "[deploy] CRITICAL: restored environment database identity could not be verified." >&2
      return 1
    fi
    read -r rollback_database rollback_env_sha256 < <(printf '%s' "${rollback_env_state}" | run_local_python_program 'import json,os; p=json.load(os.fdopen(3)); print(p["database_name"], p["env_sha256"])')
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
  # Restore the original PgBouncer bytes while both activation paths are
  # runtime-masked.  Only then may its captured service state return.
  if [ "${PGBOUNCER_MAP_MUTATION_INTENT}" = "1" ]; then
    if ! restore_remote_pgbouncer_database_map; then
      echo "[deploy] CRITICAL: rollback database is restored but PgBouncer config is not; consumers remain stopped." >&2
      return 1
    fi
  fi
  if ! restore_remote_pgbouncer_state; then
    echo "[deploy] CRITICAL: rollback database is restored but PgBouncer is not; application consumers remain stopped." >&2
    return 1
  fi
  if [ "${PGBOUNCER_SERVICE_ACTIVE_STATE}" = "active" ] \
    && ! probe_remote_pgbouncer_database "${PREDEPLOY_DATABASE_NAME}"; then
    echo "[deploy] CRITICAL: rollback PgBouncer source route could not be proven; consumers remain stopped." >&2
    return 1
  fi
  if [ "${RELEASE_VALIDATION_FENCE_INSTALLED}" = "1" ]; then
    # The restored release may predate the runtime fence implementation.  Open
    # it only after filesystem/database/PgBouncer rollback is proven and before
    # any restored worker or web process starts.
    if ! remove_remote_release_validation_fence; then
      echo "[deploy] CRITICAL: rollback validation fence could not be removed; consumers remain stopped." >&2
      return 1
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
      rollback_redis_not_before="$(ssh "${SSH_TARGET}" "date -u +%Y-%m-%dT%H:%M:%SZ")" || return 1
      if ! rollback_redis_main_pid="$(ssh "${SSH_TARGET}" "sudo systemctl start '${STAGING_REDIS_WORKER_SERVICE}' && systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}' && systemctl show --property MainPID --value '${STAGING_REDIS_WORKER_SERVICE}'")"; then
        echo "[deploy] CRITICAL: restored Redis worker active state could not be restored." >&2
        return 1
      fi
      if ! [[ "${rollback_redis_main_pid}" =~ ^[1-9][0-9]*$ ]]; then
        echo "[deploy] CRITICAL: restored Redis worker systemd MainPID is invalid." >&2
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
  if ! verify_remote_web_database_runtime \
    "${PREDEPLOY_DATABASE_NAME}" "${PGBOUNCER_WEB_POOL_EFFECTIVE_BEFORE}"; then
    echo "[deploy] CRITICAL: restored Web database runtime does not match the captured source." >&2
    return 1
  fi
  if ! rollback_health="$(ssh "${SSH_TARGET}" "for attempt in \$(seq 1 60); do if sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/fetch_runtime_health.py' --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env' 2>/dev/null; then exit 0; fi; sleep 2; done; exit 1")"; then
    echo "[deploy] CRITICAL: restored release did not return authenticated health." >&2
    return 1
  fi
  if [ "${STAGING_REDIS_WORKER_UNIT_WAS_ACTIVE}" = "1" ]; then
    # Authenticated web health can recover before the dedicated Redis worker
    # has completed the two same-PID heartbeat cycles required by the strict
    # gate.  Re-fetch on every attempt instead of validating the first web
    # snapshot forever.  Ninety attempts cover the reviewed 60-second maximum
    # heartbeat interval twice, plus startup/scheduling margin.
    for attempt in $(seq 1 90); do
      if rollback_candidate_health="$(ssh "${SSH_TARGET}" "sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/fetch_runtime_health.py' --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env' 2>/dev/null")" \
        && printf '%s' "${rollback_candidate_health}" | run_sealed_controller_python \
          "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/verify_redis_worker_health.py" \
          --expected-head "${PREDEPLOY_APP_SHA}" \
          --expected-count 1 \
          --expected-main-pid "${rollback_redis_main_pid}" \
          --min-ready-sequence 3 \
          --worker-not-before "${rollback_redis_not_before}" \
          --max-age-seconds "${MAX_WORKER_AGE_SECONDS}" >/dev/null; then
        rollback_health="${rollback_candidate_health}"
        rollback_redis_ready=1
        break
      fi
      sleep 2
    done
    if [ "${rollback_redis_ready}" != "1" ]; then
      echo "[deploy] CRITICAL: restored Redis worker did not reach strict same-PID readiness." >&2
      return 1
    fi
  fi
  rollback_migration="$(printf '%s' "${rollback_health}" | run_local_python_program 'import json,os; print(str((json.load(os.fdopen(3)).get("trust") or {}).get("db_migration_max") or ""))')"
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
    if run_sealed_controller_python \
      "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/legacy_to_atomic_preflight.py" \
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
      <"${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py" \
      >"${rollback_anchor}"; then
      echo "[deploy] CRITICAL: restored legacy filesystem anchor could not be collected." >&2
      return 1
    fi
    chmod 600 "${rollback_anchor}"
    if ! run_sealed_controller_python \
      "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py" verify-rollback \
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
  elif ! printf '%s' "${rollback_health}" | run_sealed_controller_python \
    "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/verify_runtime_health.py" \
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
    # Close the gap between the readiness poll and rollback completion.  The
    # worker may restart or lose readiness after the polling snapshot passed,
    # so the final gate must validate a newly fetched snapshot.
    if ! rollback_candidate_health="$(ssh "${SSH_TARGET}" "sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/fetch_runtime_health.py' --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env' 2>/dev/null")" \
      || ! printf '%s' "${rollback_candidate_health}" | run_sealed_controller_python \
        "${DEPLOY_VERIFIER_BUNDLE_DIR}/scripts/verify_redis_worker_health.py" \
        --expected-head "${PREDEPLOY_APP_SHA}" \
        --expected-count 1 \
        --expected-main-pid "${rollback_redis_main_pid}" \
        --min-ready-sequence 3 \
        --worker-not-before "${rollback_redis_not_before}" \
        --max-age-seconds "${MAX_WORKER_AGE_SECONDS}" >/dev/null; then
      echo "[deploy] CRITICAL: restored Redis worker failed strict runtime validation." >&2
      return 1
    fi
    rollback_health="${rollback_candidate_health}"
  fi
  local rollback_apify_binding=""
  if ! rollback_apify_binding="$(
    printf '%s' "${rollback_health}" | ssh "${SSH_TARGET}" \
      "sudo -n env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/verify_apify_worker_process_binding.py' --health-json - --current-release '${REMOTE_CURRENT_DIR}' --expected-head '${PREDEPLOY_APP_SHA}'"
  )" || [ -z "${rollback_apify_binding}" ]; then
    echo "[deploy] CRITICAL: restored Apify worker fleet failed process binding validation." >&2
    return 1
  fi
  rollback_apify_binding=""
  if ! restore_remote_sync_unit_state; then
    echo "[deploy] CRITICAL: rollback runtime is restored but sync unit state is not; units remain fail-closed." >&2
    return 1
  fi
  verify_deploy_verifier_bundle || return 1
  ROLLBACK_COMPLETED=1
  echo "[deploy] rollback accepted: app=${PREDEPLOY_APP_SHA} migration=${rollback_migration} database=${PREDEPLOY_DATABASE_NAME:-in-place}." >&2
  return 0
}

report_final_activation_recovery_path() {
  echo "[deploy] RECOVERY: preserve release ${RELEASE_ID} and its digest-bound rollback receipt under ${REMOTE_ROOT}/.release-controller/rollbacks/${RELEASE_ID}; first prove ${RELEASE_VALIDATION_FENCE} exactly active or absent. If active, resume only the validated fence removal. If absent, resume the captured sync/sentinel restore until receipt vkpi-sync-unit-restore/v1:restored, then rerun final legacy-writer, seal, candidate, and source checks. Never activate the previous release after this boundary." >&2
}

cleanup_post_deploy_evidence() {
  local original_rc=$?
  set +e
  if ! cleanup_local_candidate_browser_runtime; then
    original_rc=1
  fi
  if [ "${original_rc}" -ne 0 ] \
    && [ "${ROLLBACK_PREPARE_MAY_HAVE_COMMITTED}" = "1" ]; then
    reconcile_remote_prepare_commit_state || true
  fi
  if [ "${original_rc}" -ne 0 ] \
    && [ "${RELEASE_VALIDATION_FENCE_INSTALL_MAY_HAVE_COMMITTED}" = "1" ] \
    && declare -F reconcile_remote_release_validation_fence_install >/dev/null; then
    reconcile_remote_release_validation_fence_install || true
  fi
  if [ "${original_rc}" -ne 0 ] \
    && [ "${RELEASE_VALIDATION_COMMIT_STARTED}" = "1" ] \
    && [ "${DEPLOY_ACCEPTED}" != "1" ]; then
    if [ "${RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED}" = "1" ]; then
      reconcile_remote_release_validation_fence_remove || true
    fi
    # Once exact marker absence proves the irreversible boundary committed,
    # finishing/reconciling the captured sync restore is roll-forward recovery,
    # not rollback.  Never touch those units while marker state is unknown.
    if [ "${RELEASE_VALIDATION_FENCE_INSTALLED}" = "0" ] \
      && [ "${SYNC_UNITS_MAY_HAVE_BEEN_MUTATED}" = "1" ] \
      && [ "${SYNC_UNITS_RESTORED}" != "1" ]; then
      restore_remote_sync_unit_state || true
    fi
  fi
  if [ "${original_rc}" -ne 0 ] \
    && [ "${DEPLOY_ACCEPTED}" != "1" ] \
    && [ "${RELEASE_VALIDATION_COMMIT_STARTED}" != "1" ]; then
    if [ "${ROLLBACK_ARMED}" = "1" ]; then
      # A failed rollback intentionally leaves sync fail-closed.  Resuming a
      # timer against an untrusted app/database state would compound failure.
      attempt_automatic_rollback || true
    else
      # Before prepare there is no release state to roll back.  Reassert every
      # captured unit independently so a partial restore cannot suppress the
      # other EXIT-trap recovery attempt.
      if [ "${PGBOUNCER_MAY_HAVE_BEEN_MUTATED}" = "1" ] \
        && [ "${PGBOUNCER_RESTORED}" != "1" ]; then
        restore_remote_pgbouncer_state || true
      fi
      if [ "${SYNC_UNITS_MAY_HAVE_BEEN_MUTATED}" = "1" ] \
        && [ "${SYNC_UNITS_RESTORED}" != "1" ]; then
        restore_remote_sync_unit_state || true
      fi
    fi
  elif [ "${original_rc}" -ne 0 ] \
    && [ "${RELEASE_VALIDATION_COMMIT_STARTED}" = "1" ] \
    && [ "${DEPLOY_ACCEPTED}" != "1" ]; then
    echo "[deploy] CRITICAL: activation commit started; automatic rollback is forbidden because provider side effects may now exist." >&2
    if [ "${RELEASE_VALIDATION_FENCE_INSTALLED}" = "0" ] \
      && [ "${RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED}" = "0" ] \
      && [ "${SYNC_UNITS_RESTORED}" = "1" ] \
      && [ "${SYNC_UNITS_RESTORE_MAY_HAVE_COMMITTED}" = "0" ]; then
      echo "[deploy] remote activation and sync-unit restore are reconciled complete; final acceptance checks remain unconfirmed." >&2
    else
      report_final_activation_recovery_path
    fi
  fi
  if [ -n "${REMOTE_LOG_BASELINE}" ] || [ -n "${REMOTE_ACCEPTANCE_REPORT}" ]; then
    ssh "${SSH_TARGET}" "rm -f -- '${REMOTE_LOG_BASELINE}' '${REMOTE_ACCEPTANCE_REPORT}'" >/dev/null 2>&1
  fi
  if [ -n "${LOCAL_ACCEPTANCE_REPORT_TMP}" ]; then
    rm -f -- "${LOCAL_ACCEPTANCE_REPORT_TMP}"
  fi
  if [ -d "${POST_DEPLOY_EVIDENCE_DIR}" ]; then
    echo "[deploy] post-restart evidence retained: ${POST_DEPLOY_EVIDENCE_DIR}" >&2
  fi
  if [ -n "${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}" ]; then
    rm -rf -- "${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}"
  fi
  # Keep the mutex and master available through rollback and remote evidence
  # cleanup. Release the mutex before asking its ControlMaster to exit.
  if ! release_remote_deploy_lock; then
    if [ "${original_rc}" -eq 0 ]; then
      original_rc=1
    fi
  fi
  cleanup_deploy_ssh_transport
  # Rollback validators also come from the frozen candidate, so retain the
  # digest-bound verifier bundle until every rollback/evidence action ends.
  cleanup_deploy_verifier_bundle
  trap - EXIT
  exit "${original_rc}"
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

verify_remote_legacy_writers_absent() {
  if ! ssh "${SSH_TARGET}" "bash -s -- '${REMOTE_ROOT}' ${LEGACY_WRITER_UNITS[*]}" <<'REMOTE_VERIFY_LEGACY_WRITERS'
set -euo pipefail
root="$1"
shift
for unit in "$@"; do
  state="$(systemctl show --property ActiveState --value "${unit}")" \
    || { echo "legacy writer state is unreadable: ${unit}" >&2; exit 1; }
  case "${state}" in
    inactive|failed) ;;
    *) echo "legacy writer is not inactive: ${unit}" >&2; exit 1 ;;
  esac
done

pidfile="${root}/runtime/worker.pid"
if [ -L "${pidfile}" ]; then
  echo "legacy worker marker is a symlink" >&2
  exit 1
fi
if [ -e "${pidfile}" ]; then
  [ -f "${pidfile}" ] || { echo "legacy worker marker is not a regular file" >&2; exit 1; }
  IFS= read -r pid <"${pidfile}" || true
  [ "$(awk 'END {print NR}' "${pidfile}")" = 1 ] \
    || { echo "legacy worker marker must contain one line" >&2; exit 1; }
  case "${pid:-}" in ''|*[!0-9]*)
    echo "legacy worker marker is not a PID" >&2
    exit 1
    ;;
  esac
  [ "${pid}" -gt 1 ] || { echo "legacy worker marker PID is unsafe" >&2; exit 1; }
  if { [ -d /proc ] && [ -d "/proc/${pid}" ]; } \
    || { [ ! -d /proc ] && kill -0 "${pid}" 2>/dev/null; }; then
    echo "legacy runtime/worker.pid still represents a live process" >&2
    exit 1
  fi
fi
REMOTE_VERIFY_LEGACY_WRITERS
  then
    echo "Refusing complete fleet claim while a legacy writer or live worker.pid remains." >&2
    return 1
  fi
}

verify_remote_release_validation_fence() {
  local expected="$1"
  if [ "${expected}" != active ] && [ "${expected}" != absent ]; then
    echo "Release validation fence expectation must be active or absent." >&2
    return 1
  fi
  ssh "${SSH_TARGET}" \
    "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - '${RELEASE_VALIDATION_FENCE}' '${expected}'" <<'REMOTE_VERIFY_RELEASE_VALIDATION_FENCE'
import os
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
expected = sys.argv[2]
payload = b"vkpi-release-validation/v1\n"
try:
    metadata = path.lstat()
except FileNotFoundError:
    if expected == "absent":
        raise SystemExit(0)
    raise SystemExit("release validation fence is absent")
if expected == "absent":
    raise SystemExit("unexpected release validation fence is present")
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
descriptor = os.open(path, flags)
try:
    observed = os.fstat(descriptor)
    content = os.read(descriptor, len(payload) + 1)
finally:
    os.close(descriptor)
valid = (
    stat.S_ISREG(metadata.st_mode)
    and stat.S_ISREG(observed.st_mode)
    and (metadata.st_dev, metadata.st_ino) == (observed.st_dev, observed.st_ino)
    and observed.st_uid == 0
    and observed.st_gid == 0
    and stat.S_IMODE(observed.st_mode) == 0o444
    and observed.st_nlink == 1
    and content == payload
)
if not valid:
    raise SystemExit("release validation fence metadata or payload is invalid")
REMOTE_VERIFY_RELEASE_VALIDATION_FENCE
}

reconcile_remote_release_validation_fence_install() {
  if [ "${RELEASE_VALIDATION_FENCE_INSTALL_MAY_HAVE_COMMITTED}" != "1" ]; then
    return 0
  fi
  if verify_remote_release_validation_fence active >/dev/null 2>&1; then
    RELEASE_VALIDATION_FENCE_INSTALLED=1
    RELEASE_VALIDATION_FENCE_INSTALL_MAY_HAVE_COMMITTED=0
    echo "[deploy] recovered installed release-validation fence after a lost SSH acknowledgement." >&2
    return 0
  fi
  if verify_remote_release_validation_fence absent >/dev/null 2>&1; then
    RELEASE_VALIDATION_FENCE_INSTALLED=0
    RELEASE_VALIDATION_FENCE_INSTALL_MAY_HAVE_COMMITTED=0
    return 0
  fi
  echo "[deploy] CRITICAL: release-validation fence install state is unknown after SSH acknowledgement loss." >&2
  return 1
}

install_remote_release_validation_fence() {
  if [ "${RELEASE_CONSUMERS_QUIESCED}" != "1" ] \
    || [ "${RELEASE_DRAIN_VERIFIED}" != "1" ]; then
    echo "Refusing validation-fence install before complete quiesce and drain." >&2
    return 1
  fi
  RELEASE_VALIDATION_FENCE_INSTALL_MAY_HAVE_COMMITTED=1
  if ! ssh "${SSH_TARGET}" \
    "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - '${RELEASE_VALIDATION_FENCE}'" <<'REMOTE_INSTALL_RELEASE_VALIDATION_FENCE'
import os
from pathlib import Path
import stat
import sys
import tempfile

path = Path(sys.argv[1])
payload = b"vkpi-release-validation/v1\n"
parent = path.parent
parent_meta = parent.lstat()
if not stat.S_ISDIR(parent_meta.st_mode) or parent.is_symlink() or parent_meta.st_uid != 0:
    raise SystemExit("release validation fence parent is unsafe")
try:
    path.lstat()
except FileNotFoundError:
    pass
else:
    raise SystemExit("release validation fence already exists")
descriptor, temporary_name = tempfile.mkstemp(prefix=".vkpi-release-validation.", dir=parent)
temporary = Path(temporary_name)
try:
    os.fchown(descriptor, 0, 0)
    os.fchmod(descriptor, 0o444)
    os.write(descriptor, payload)
    os.fsync(descriptor)
    os.close(descriptor)
    descriptor = -1
    os.link(temporary, path, follow_symlinks=False)
    temporary.unlink()
    directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if descriptor >= 0:
        os.close(descriptor)
    temporary.unlink(missing_ok=True)
REMOTE_INSTALL_RELEASE_VALIDATION_FENCE
  then
    if ! reconcile_remote_release_validation_fence_install \
      || [ "${RELEASE_VALIDATION_FENCE_INSTALLED}" != "1" ]; then
      echo "Release-validation fence install failed without a verified committed receipt." >&2
      return 1
    fi
    return 0
  fi
  RELEASE_VALIDATION_FENCE_INSTALLED=1
  RELEASE_VALIDATION_FENCE_INSTALL_MAY_HAVE_COMMITTED=0
  verify_remote_release_validation_fence active
}

reconcile_remote_release_validation_fence_remove() {
  if [ "${RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED}" != "1" ]; then
    return 0
  fi
  # The marker itself is the durable commit receipt.  Probe both exact states
  # read-only after a transport failure; never infer activation from SSH status.
  if verify_remote_release_validation_fence absent >/dev/null 2>&1; then
    RELEASE_VALIDATION_FENCE_INSTALLED=0
    RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED=0
    echo "[deploy] recovered committed release-validation fence removal after a lost SSH acknowledgement." >&2
    return 0
  fi
  if verify_remote_release_validation_fence active >/dev/null 2>&1; then
    RELEASE_VALIDATION_FENCE_INSTALLED=1
    RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED=0
    echo "[deploy] release-validation fence removal is confirmed uncommitted; activation remains fenced." >&2
    return 0
  fi
  echo "[deploy] CRITICAL: release-validation fence removal state is unknown after SSH acknowledgement loss." >&2
  return 1
}

remove_remote_release_validation_fence() {
  local retry_count="${1:-0}"
  if [ "${retry_count}" != "0" ] && [ "${retry_count}" != "1" ]; then
    echo "Release-validation fence removal retry count is outside the reviewed bound." >&2
    return 1
  fi
  if [ "${RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED}" = "1" ]; then
    if ! reconcile_remote_release_validation_fence_remove; then
      return 1
    fi
    if [ "${RELEASE_VALIDATION_FENCE_INSTALLED}" = "0" ]; then
      return 0
    fi
  fi
  if [ "${RELEASE_VALIDATION_FENCE_INSTALLED}" != "1" ]; then
    echo "Refusing release activation without a controller-installed fence." >&2
    return 1
  fi
  verify_remote_release_validation_fence active || return 1
  RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED=1
  if ! ssh "${SSH_TARGET}" \
    "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - '${RELEASE_VALIDATION_FENCE}'" <<'REMOTE_REMOVE_RELEASE_VALIDATION_FENCE'
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.unlink()
directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(directory)
finally:
    os.close(directory)
try:
    path.lstat()
except FileNotFoundError:
    raise SystemExit(0)
raise SystemExit("release validation fence still exists after activation")
REMOTE_REMOVE_RELEASE_VALIDATION_FENCE
  then
    if ! reconcile_remote_release_validation_fence_remove; then
      echo "Release-validation fence removal failed without a verified committed receipt; activation remains blocked or unknown." >&2
      return 1
    fi
    if [ "${RELEASE_VALIDATION_FENCE_INSTALLED}" = "0" ]; then
      return 0
    fi
    if [ "${retry_count}" = "0" ]; then
      echo "[deploy] exact marker receipt proves fence removal was uncommitted; retrying the idempotent removal once." >&2
      remove_remote_release_validation_fence 1
      return $?
    fi
    echo "Release-validation fence remains active after the bounded retry; activation is safely blocked." >&2
    return 1
  fi
  if verify_remote_release_validation_fence absent; then
    RELEASE_VALIDATION_FENCE_INSTALLED=0
    RELEASE_VALIDATION_FENCE_REMOVE_MAY_HAVE_COMMITTED=0
    return 0
  fi
  if ! reconcile_remote_release_validation_fence_remove; then
    echo "Release-validation fence removal acknowledgement could not be reconciled; preserve release ${RELEASE_ID} and do not roll it back." >&2
    return 1
  fi
  if [ "${RELEASE_VALIDATION_FENCE_INSTALLED}" = "0" ]; then
    return 0
  fi
  if [ "${retry_count}" = "0" ]; then
    echo "[deploy] marker remains active after a successful SSH return; retrying the idempotent removal once." >&2
    remove_remote_release_validation_fence 1
    return $?
  fi
  echo "Release-validation fence remains active after the bounded retry; activation is safely blocked." >&2
  return 1
}

quiesce_remote_pgbouncer_for_clone() {
  if [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
    return 0
  fi
  if [ "${PGBOUNCER_STATE_CAPTURED}" != "1" ]; then
    echo "Refusing staging clone PgBouncer quiesce without captured state." >&2
    return 1
  fi
  # Set before SSH because validation/stop/mask can fail after a unit changed.
  # The remote trap leaves both activation paths stopped and runtime-masked.
  PGBOUNCER_MAY_HAVE_BEEN_MUTATED=1
  PGBOUNCER_RESTORED=0
  if ! ssh "${SSH_TARGET}" \
    "bash -s -- '${PGBOUNCER_SERVICE}' '${PGBOUNCER_SOCKET}' '${PGBOUNCER_PORT}' '${PGBOUNCER_SERVICE_LOAD_STATE}' '${PGBOUNCER_SERVICE_ACTIVE_STATE}' '${PGBOUNCER_SERVICE_UNIT_FILE_STATE}' '${PGBOUNCER_SOCKET_LOAD_STATE}' '${PGBOUNCER_SOCKET_ACTIVE_STATE}' '${PGBOUNCER_SOCKET_UNIT_FILE_STATE}'" <<'REMOTE_PGBOUNCER_QUIESCE'
set -euo pipefail
service="$1"
socket="$2"
port="$3"
service_load="$4"
service_active="$5"
service_file="$6"
socket_load="$7"
socket_active="$8"
socket_file="$9"
quiesce_complete=0
fail_closed_pgbouncer() {
  if [ "${quiesce_complete}" != 1 ]; then
    sudo systemctl stop "${socket}" "${service}" >/dev/null 2>&1 || true
    sudo systemctl mask --runtime "${socket}" "${service}" >/dev/null 2>&1 || true
  fi
}
runtime_mask_state() {
  local unit="$1" mask_path="/run/systemd/system/$1"
  if [ -L "${mask_path}" ] && [ "$(readlink -- "${mask_path}")" = /dev/null ]; then
    printf 'masked'
  elif [ ! -e "${mask_path}" ] && [ ! -L "${mask_path}" ]; then
    printf 'clear'
  else
    printf 'invalid'
  fi
}
trap fail_closed_pgbouncer EXIT
service_mask="$(runtime_mask_state "${service}")"
socket_mask="$(runtime_mask_state "${socket}")"
case "${service_mask}:${socket_mask}" in
  clear:clear)
    [ "$(systemctl show --property LoadState --value "${service}")" = "${service_load}" ] \
      && [ "$(systemctl show --property LoadState --value "${socket}")" = "${socket_load}" ] \
      || { echo "PgBouncer LoadState changed before quiesce" >&2; exit 1; }
    [ "$(systemctl show --property ActiveState --value "${service}")" = "${service_active}" ] \
      && [ "$(systemctl show --property ActiveState --value "${socket}")" = "${socket_active}" ] \
      || { echo "PgBouncer ActiveState changed before quiesce" >&2; exit 1; }
    [ "$(systemctl show --property UnitFileState --value "${service}")" = "${service_file}" ] \
      && [ "$(systemctl show --property UnitFileState --value "${socket}")" = "${socket_file}" ] \
      || { echo "PgBouncer UnitFileState changed before quiesce" >&2; exit 1; }
    # Closing and masking the socket first prevents systemd from reactivating
    # the service while its min_pool_size server connections are draining.
    sudo systemctl stop "${socket}"
    sudo systemctl mask --runtime "${socket}" >/dev/null
    sudo systemctl stop "${service}"
    sudo systemctl mask --runtime "${service}" >/dev/null
    ;;
  masked:masked)
    ;;
  *)
    echo "PgBouncer runtime masks are mixed or unsafe before quiesce" >&2
    exit 1
    ;;
esac
for unit in "${socket}" "${service}"; do
  [ "$(systemctl show --property ActiveState --value "${unit}")" = inactive ] \
    || { echo "PgBouncer unit failed to stop: ${unit}" >&2; exit 1; }
  mask_path="/run/systemd/system/${unit}"
  [ -L "${mask_path}" ] && [ "$(readlink -- "${mask_path}")" = /dev/null ] \
    || { echo "PgBouncer runtime mask missing: ${unit}" >&2; exit 1; }
done
if ss -H -ltn "sport = :${port}" | grep -q .; then
  echo "PgBouncer listener remains on 6432 after quiesce" >&2
  exit 1
fi
quiesce_complete=1
trap - EXIT
REMOTE_PGBOUNCER_QUIESCE
  then
    echo "[deploy] PgBouncer service/socket could not be quiesced safely; both remain fail-closed." >&2
    return 1
  fi
  PGBOUNCER_QUIESCED=1
}

quiesce_remote_sync_units() {
  if [ "${SYNC_UNITS_CAPTURED}" != "1" ]; then
    echo "Refusing sync quiesce without a captured service/timer state." >&2
    return 1
  fi
  # Set this before SSH because the stop/mask sequence can succeed partially
  # before the remote shell reports failure.  The EXIT trap then restores the captured
  # state while no release rollback is armed.
  SYNC_UNITS_MAY_HAVE_BEEN_MUTATED=1
  if ! ssh "${SSH_TARGET}" "sudo systemctl stop '${SYNC_TIMER}'; sudo systemctl mask --runtime '${SYNC_TIMER}' >/dev/null; sudo systemctl stop '${HEALTH_SENTINEL_TIMER}'; sudo systemctl mask --runtime '${HEALTH_SENTINEL_TIMER}' >/dev/null; sudo systemctl stop '${SYNC_SERVICE}'; sudo systemctl mask --runtime '${SYNC_SERVICE}' >/dev/null; sudo systemctl stop '${HEALTH_SENTINEL_SERVICE}'; sudo systemctl mask --runtime '${HEALTH_SENTINEL_SERVICE}' >/dev/null; for sync_unit in '${SYNC_TIMER}' '${HEALTH_SENTINEL_TIMER}' '${SYNC_SERVICE}' '${HEALTH_SENTINEL_SERVICE}'; do if systemctl is-active --quiet \"\${sync_unit}\"; then echo \"reviewed timer/service failed to stop before deployment staging: \${sync_unit}\" >&2; exit 1; fi; sync_mask_path=\"/run/systemd/system/\${sync_unit}\"; if [ ! -L \"\${sync_mask_path}\" ] || [ \"\$(readlink -- \"\${sync_mask_path}\")\" != /dev/null ]; then echo \"reviewed timer/service failed to mask before deployment staging: \${sync_unit}\" >&2; exit 1; fi; done"; then
    echo "[deploy] reviewed sync/sentinel timers and services could not be quiesced before build, backup, or remote staging." >&2
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
  if ! ssh "${SSH_TARGET}" "for unit in '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}; do systemctl is-active --quiet \"\${unit}\" || { echo \"required release consumer is not active before quiesce: \${unit}\" >&2; exit 1; }; done; if [ '${expected_redis_state}' = active ]; then systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}' || { echo 'captured active Redis worker changed state before quiesce' >&2; exit 1; }; redis_unit='${STAGING_REDIS_WORKER_SERVICE}'; else if systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then echo 'captured inactive Redis worker changed state before quiesce' >&2; exit 1; fi; redis_unit=''; fi; sudo systemctl stop '${SYNC_TIMER}'; sudo systemctl mask --runtime '${SYNC_TIMER}' >/dev/null; sudo systemctl stop '${HEALTH_SENTINEL_TIMER}'; sudo systemctl mask --runtime '${HEALTH_SENTINEL_TIMER}' >/dev/null; sudo systemctl stop '${SYNC_SERVICE}'; sudo systemctl mask --runtime '${SYNC_SERVICE}' >/dev/null; sudo systemctl stop '${HEALTH_SENTINEL_SERVICE}'; sudo systemctl mask --runtime '${HEALTH_SENTINEL_SERVICE}' >/dev/null; for sync_unit in '${SYNC_TIMER}' '${HEALTH_SENTINEL_TIMER}' '${SYNC_SERVICE}' '${HEALTH_SENTINEL_SERVICE}'; do if systemctl is-active --quiet \"\${sync_unit}\"; then echo \"reviewed timer/service failed to stop: \${sync_unit}\" >&2; exit 1; fi; sync_mask_path=\"/run/systemd/system/\${sync_unit}\"; if [ ! -L \"\${sync_mask_path}\" ] || [ \"\$(readlink -- \"\${sync_mask_path}\")\" != /dev/null ]; then echo \"reviewed timer/service failed to mask: \${sync_unit}\" >&2; exit 1; fi; done; sudo systemctl stop '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS} \${redis_unit}; for unit in '${SERVICE_NAME}' ${WORKER_SYSTEMD_UNIT_ARGS}; do if systemctl is-active --quiet \"\${unit}\"; then echo \"release consumer failed to stop: \${unit}\" >&2; exit 1; fi; done; if systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}'; then echo 'Redis worker failed to stop' >&2; exit 1; fi"; then
    echo "[deploy] complete web/worker fleet could not be quiesced before release mutation." >&2
    return 1
  fi
  if ! quiesce_remote_pgbouncer_for_clone; then
    return 1
  fi
  RELEASE_CONSUMERS_QUIESCED=1
  SYNC_UNITS_QUIESCED=1
}

verify_remote_release_drain() {
  local phase="${1:-}" report="" diagnostics=""
  local expected_database="${PREDEPLOY_DATABASE_NAME}"
  local expected_migration="${PREDEPLOY_MIGRATION}"
  case "${phase}" in
    live)
      if [ "${SYNC_UNITS_MAY_HAVE_BEEN_MUTATED}" != "0" ] \
        || [ "${RELEASE_CONSUMERS_QUIESCED}" != "0" ]; then
        echo "Refusing live drain preflight after any reviewed service/timer mutation." >&2
        return 1
      fi
      ;;
    quiesced)
      if [ "${RELEASE_CONSUMERS_QUIESCED}" != "1" ] \
        || [ "${SYNC_UNITS_QUIESCED}" != "1" ]; then
        echo "Refusing final release drain verification until every reviewed ingress and consumer is quiesced." >&2
        return 1
      fi
      ;;
    fenced)
      if [ "${RELEASE_VALIDATION_FENCE_INSTALLED}" != "1" ]; then
        echo "Refusing fenced drain verification without the active release fence." >&2
        return 1
      fi
      # Candidate startup may have advanced an explicitly reviewed in-place
      # migration or switched to a candidate clone.  Bind the post-validation
      # drain to the identity the candidate health gate already proved, never to
      # the pre-deploy rollback anchor.
      expected_database="${STAGING_CLONE_DATABASE:-${PREDEPLOY_DATABASE_NAME}}"
      expected_migration="${LATEST_MIGRATION}"
      ;;
    *)
      echo "Release drain phase must be exactly live, quiesced, or fenced." >&2
      return 1
      ;;
  esac
  verify_deploy_candidate
  assert_deploy_source_unchanged
  if ! report="$(ssh "${SSH_TARGET}" "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i HOME=/tmp XDG_CACHE_HOME=/tmp TMPDIR=/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B - --env-file '${REMOTE_ROOT}/.env' --expected-database '${expected_database}' --current-migration '${expected_migration}'" <"${DEPLOY_CANDIDATE_DIR}/scripts/ops/verify_release_drain.py")"; then
    verify_deploy_candidate
    assert_deploy_source_unchanged
    echo "${phase} Redis/database/provider drain verification failed; no release pointer or database identity was changed." >&2
    return 1
  fi
  verify_deploy_candidate
  assert_deploy_source_unchanged
  if ! diagnostics="$(printf '%s' "${report}" | run_local_python_program 'import json,os,sys
p=json.load(os.fdopen(3))
assert p.get("schema_version")=="vkpi-release-drain/v1"
assert p.get("read_only") is True and p.get("history_mutated") is False
assert p.get("credentials_emitted") is False
assert (p.get("overall") or {}).get("pass") is True
r=p.get("redis") or {}; d=p.get("database") or {}
assert r.get("passed") is True and r.get("pending_count")==0 and r.get("undelivered_count")==0
assert d.get("passed") is True
assert d.get("current_migration")==sys.argv[1]
assert d.get("database_identity_verified") is True
assert d.get("search_path_verified") is True
counts=d.get("active_counts") or {}
assert counts and all(isinstance(v,int) and not isinstance(v,bool) and v==0 for v in counts.values())
diagnostic_counts=d.get("diagnostic_counts") or {}
assert diagnostic_counts and all(isinstance(v,int) and not isinstance(v,bool) and v>=0 for v in diagnostic_counts.values())
diag=r.get("diagnostics") or {}
assert diag.get("lag_or_consumer_count_blocks_release") is False
print("lag={} consumers={}".format(diag.get("raw_xinfo_lag"),diag.get("historical_consumer_count")))' "${expected_migration}")"; then
    echo "${phase} release drain receipt is invalid; no release pointer or database identity was changed." >&2
    return 1
  fi
  if [ "${phase}" = live ]; then
    LIVE_RELEASE_DRAIN_VERIFIED=1
  elif [ "${phase}" = fenced ]; then
    FENCED_RELEASE_DRAIN_VERIFIED=1
  else
    RELEASE_DRAIN_VERIFIED=1
  fi
  echo "[deploy] ${phase} Redis/database/provider drain accepted: ${diagnostics} (diagnostic only)."
}

# The first atomic release may target a legacy tree that does not yet contain
# scripts/ops/fetch_runtime_health.py.  Execute the reviewed local probe over
# stdin for the pre-mutation identity read, so bootstrap does not depend on a
# file that only arrives later with the release payload.  The probe reads the
# remote .env in memory and returns health JSON; it never writes to the host.
fetch_predeploy_runtime_health() {
  ssh "${SSH_TARGET}" \
    "sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B - --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env'" \
    < "${DEPLOY_CANDIDATE_DIR}/scripts/ops/fetch_runtime_health.py"
}

# Ancestor guard: the candidate must descend from the release production is
# currently serving.  A deploy whose HEAD does not contain the live commit
# would silently roll back every commit that landed on the server since the
# operator's branch diverged (e.g. a prod hotfix that was never merged back).
# Read ``readlink /opt/viltrox-2.0/current`` over the already-open transport
# before the deployment mutex (read-only, like the prelock auth preflight)
# and again after the mutex to close the inter-deploy race.  The only bypass
# is the explicit VKPI_DEPLOY_ALLOW_NON_ANCESTOR=1 operator override.
DEPLOY_ALLOW_NON_ANCESTOR="${VKPI_DEPLOY_ALLOW_NON_ANCESTOR:-0}"
REMOTE_CURRENT_RELEASE_SHA=""
assert_remote_release_is_ancestor() {
  local phase="${1:-}" remote_listing="" remote_link="" remote_build_sha=""
  local remote_release_id="" remote_short="" remote_sha="" candidate_sha=""
  case "${phase}" in
    prelock|locked) ;;
    *)
      echo "Remote release ancestor guard failed (category=invalid_phase)." >&2
      return 1
      ;;
  esac

  # One read-only round trip: the current pointer target plus the build stamp
  # the accepted release carried (the pointer suffix is only a 12-hex prefix).
  if ! remote_listing="$(
    ssh "${SSH_TARGET}" \
      "readlink -- '${REMOTE_CURRENT_DIR}' 2>/dev/null || echo ''; cat -- '${REMOTE_CURRENT_DIR}/BUILD_GIT_SHA' 2>/dev/null || true" \
      2>/dev/null
  )"; then
    echo "Remote release ancestor guard failed: could not read the current release pointer (category=transport_failed)." >&2
    return 1
  fi
  remote_link="$(printf '%s\n' "${remote_listing}" | sed -n '1p')"
  remote_build_sha="$(printf '%s\n' "${remote_listing}" | sed -n '2p' | tr -d '[:space:]')"
  remote_listing=""

  if [ -z "${remote_link}" ]; then
    if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
      echo "[deploy] ancestor guard (${phase}): remote current pointer absent; first atomic bootstrap has no served release to compare."
      return 0
    fi
    echo "Remote release ancestor guard failed: ${REMOTE_CURRENT_DIR} is not a release pointer (category=current_pointer_missing)." >&2
    return 1
  fi
  remote_release_id="${remote_link##*/}"
  remote_short="${remote_release_id##*-}"
  if [[ "${remote_build_sha}" =~ ^[0-9a-f]{40}$ ]]; then
    remote_sha="${remote_build_sha}"
    if [[ "${remote_short}" =~ ^[0-9a-f]{12}$ ]] \
      && [ "${remote_sha:0:12}" != "${remote_short}" ]; then
      echo "Remote release ancestor guard failed: pointer suffix ${remote_short} disagrees with BUILD_GIT_SHA ${remote_sha} (category=release_identity_conflict)." >&2
      return 1
    fi
  elif [[ "${remote_short}" =~ ^[0-9a-f]{12}$ ]]; then
    remote_sha="${remote_short}"
  else
    echo "Remote release ancestor guard failed: cannot derive a commit from release '${remote_release_id}' (category=release_identity_unparseable)." >&2
    return 1
  fi

  if ! remote_sha="$(git rev-parse --verify --quiet "${remote_sha}^{commit}")"; then
    echo "Remote release ancestor guard failed: production serves ${remote_short} which this clone does not contain; run 'git fetch' and merge it before deploying (category=remote_commit_unknown_locally)." >&2
    if [ "${DEPLOY_ALLOW_NON_ANCESTOR}" = "1" ]; then
      echo "!!! VKPI_DEPLOY_ALLOW_NON_ANCESTOR=1: continuing past an unknown remote commit; the live release history may be discarded. !!!" >&2
      return 0
    fi
    return 1
  fi
  candidate_sha="${LOCAL_GIT_SHA}"
  REMOTE_CURRENT_RELEASE_SHA="${remote_sha}"
  if git merge-base --is-ancestor "${remote_sha}" "${candidate_sha}"; then
    echo "[deploy] ancestor guard (${phase}): remote ${remote_sha:0:12} is an ancestor of candidate ${candidate_sha:0:12}."
    return 0
  fi

  echo "Remote release ancestor guard failed (category=candidate_not_descendant)." >&2
  echo "  remote (currently served): ${remote_sha}  release=${remote_release_id}" >&2
  echo "  candidate (local HEAD):    ${candidate_sha}" >&2
  echo "  commits on remote missing from candidate (git log --oneline ${candidate_sha:0:12}..${remote_sha:0:12}):" >&2
  git log --oneline "${candidate_sha}..${remote_sha}" 2>/dev/null | sed 's/^/    /' >&2 || true
  if [ "${DEPLOY_ALLOW_NON_ANCESTOR}" = "1" ]; then
    echo "!!! VKPI_DEPLOY_ALLOW_NON_ANCESTOR=1: deploying a candidate that does NOT contain the live release; the commits above will disappear from production. !!!" >&2
    return 0
  fi
  echo "Refusing deploy: merge the served release into the candidate, or set VKPI_DEPLOY_ALLOW_NON_ANCESTOR=1 for a deliberate override." >&2
  return 1
}

verify_remote_candidate_production_auth_contract() {
  local phase="${1:-}" category="" preflight_rc=0
  case "${phase}" in
    prelock|locked) ;;
    *)
      echo "Remote production auth preflight failed (category=invalid_phase)." >&2
      return 1
      ;;
  esac

  verify_deploy_candidate
  assert_deploy_source_unchanged
  category="$(
    ssh "${SSH_TARGET}" \
      "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i HOME=/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin ENVIRONMENT=production PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B -I - --production-auth-preflight '${REMOTE_ROOT}/.env' '${REMOTE_APP_GROUP}'" \
      2>/dev/null < "${DEPLOY_CANDIDATE_DIR}/scripts/runtime_env.py"
  )" || preflight_rc=$?
  verify_deploy_candidate
  assert_deploy_source_unchanged

  # Never reflect arbitrary candidate/transport output.  Only reviewed fixed
  # categories may cross this boundary; credential values, lengths, and
  # fingerprints are neither computed nor emitted.
  case "${category}" in
    verified|env_missing|env_stat_unavailable|env_not_regular|env_link_count_invalid|env_owner_invalid|env_group_invalid|env_mode_invalid|env_open_failed|env_identity_changed|env_descriptor_unavailable|env_read_invalid|jwt_secret_missing|jwt_secret_public_default|admin_password_missing|admin_password_public_default|candidate_auth_rejected|candidate_runtime_invalid|expected_group_unavailable|invalid_invocation) ;;
    *) category="candidate_or_transport_invalid" ;;
  esac
  if [ "${preflight_rc}" -ne 0 ] || [ "${category}" != "verified" ]; then
    echo "Remote production auth preflight failed (category=${category})." >&2
    return 1
  fi
  echo "[deploy] remote production auth contract accepted: ${phase}."
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

LATEST_MIGRATION="$(find "${DEPLOY_CANDIDATE_DIR}/migrations" -maxdepth 1 -type f -name '*.sql' ! -name '*_down.sql' -exec basename {} \; | LC_ALL=C sort | tail -n 1)"
if [ -z "${LATEST_MIGRATION}" ]; then
  echo "Refusing deploy because the local migration manifest is empty." >&2
  exit 1
fi
MIGRATION_MANIFEST_CSV="$(find "${DEPLOY_CANDIDATE_DIR}/migrations" -maxdepth 1 -type f -name '*.sql' ! -name '*_down.sql' -exec basename {} \; | LC_ALL=C sort | paste -sd, -)"

run_predeploy_embedded_browser_gate
setup_deploy_ssh_transport
# The first read-only auth check is deliberately before the deployment mutex:
# even lock acquisition is a remote controller mutation.  Recheck immediately
# after acquiring the mutex to close the inter-deploy race, still before any
# timer/service mask, stop, quiesce, upload, or current-pointer activation.
verify_remote_candidate_production_auth_contract prelock
assert_remote_release_is_ancestor prelock
acquire_remote_deploy_lock
verify_remote_candidate_production_auth_contract locked
assert_remote_release_is_ancestor locked
capture_remote_sync_unit_state
if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
  capture_remote_pgbouncer_unit_state
fi
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
verify_deploy_candidate
assert_deploy_source_unchanged
if ! REMOTE_PREDEPLOY_HEALTH_JSON="$(fetch_predeploy_runtime_health)"; then
  echo "Refusing deploy because pre-deploy authenticated runtime identity could not be read." >&2
  exit 1
fi
read -r PREDEPLOY_APP_SHA PREDEPLOY_MIGRATION < <(printf '%s' "${REMOTE_PREDEPLOY_HEALTH_JSON}" | run_local_python_program 'import json,os; p=json.load(os.fdopen(3)); t=p.get("trust") or {}; print(str(t.get("server_git_sha") or ""), str(t.get("db_migration_max") or ""))')
if ! [[ "${PREDEPLOY_APP_SHA}" =~ ^[0-9a-f]{40}$ ]] || [ -z "${PREDEPLOY_MIGRATION}" ]; then
  echo "Refusing deploy because pre-deploy app SHA or migration identity is untrusted." >&2
  exit 1
fi
# Every normal deploy keeps the strict aligned rollback anchor and has no legacy mismatch bypass.
# The one-time
# legacy bootstrap is not a boolean bypass: it is accepted only later by the
# separately hashed plan whose old server/client identities are bound exactly.
if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" != "1" ]; then
  if ! printf '%s' "${REMOTE_PREDEPLOY_HEALTH_JSON}" | run_frozen_candidate_python \
    "${DEPLOY_CANDIDATE_DIR}/scripts/verify_runtime_health.py" \
    --strict-deploy \
    --expected-head "${PREDEPLOY_APP_SHA}" \
    --expected-migration "${PREDEPLOY_MIGRATION}" \
    --require-worker \
    --expected-worker-count "${EXPECTED_WORKER_COUNT}" \
    --worker-not-before "1970-01-01T00:00:00Z" \
    --max-worker-age-seconds "${MAX_WORKER_AGE_SECONDS}" >/dev/null; then
    echo "Refusing deploy because the pre-deploy rollback anchor is not a strict aligned web/client/16-worker runtime." >&2
    exit 1
  fi
fi
verify_deploy_candidate
assert_deploy_source_unchanged
if [ "${RESCUE_ROLLBACK_MODE}" = "1" ]; then
  if [ "${RESCUE_ROLLBACK_CONFIRM}" != "RESCUE_ROLLBACK:${PREDEPLOY_APP_SHA}" ]; then
    echo "VKPI_RESCUE_ROLLBACK_CONFIRM must exactly bind the rescue to the pre-deploy runtime SHA." >&2
    exit 1
  fi
  ROLLBACK_ANCHOR_RELEASE_ID="rollback-anchor-${RELEASE_ID}-${PREDEPLOY_APP_SHA:0:12}"
  REMOTE_ROLLBACK_ANCHOR_DIR="${REMOTE_RELEASES_DIR}/${ROLLBACK_ANCHOR_RELEASE_ID}"
  ROLLBACK_ANCHOR_PREPARE_OPTION="--rollback-anchor-release-id ${ROLLBACK_ANCHOR_RELEASE_ID}"
  EXPECTED_PREVIOUS_RELEASE_DIR="${REMOTE_ROLLBACK_ANCHOR_DIR}"
  bind_rescue_rollback_candidate
  verify_rescue_rollback_candidate
fi
if [ "${VILTROXTEST_RELEASE_SCOPE}" = "1" ]; then
  if ! REMOTE_PREDEPLOY_DB_STATE_JSON="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B - '${REMOTE_ROOT}/.env' '${STAGING_DB_CLONE_MODE}'" <<'PY'
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

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
staging_clone_mode = sys.argv[2]
if staging_clone_mode not in {"0", "1"}:
    raise SystemExit("staging clone mode must be exactly 0 or 1")
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
    key = key.strip()
    if key in runtime_values:
        raise SystemExit(f"environment file contains duplicate key: {key}")
    runtime_values[key] = value.strip().strip("'\"")
    if key == "DATABASE_URL":
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        matches.append(value)
if len(matches) != 1:
    raise SystemExit("expected exactly one active DATABASE_URL")
def database_name_from_url(value, label):
    try:
        parsed = urlsplit(value)
        query = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=64,
        )
    except ValueError:
        raise SystemExit(f"{label} is invalid") from None
    name = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
    safe_query_parameters = {
        "application_name", "channel_binding", "connect_timeout",
        "fallback_application_name", "gssencmode", "keepalives",
        "keepalives_count", "keepalives_idle", "keepalives_interval",
        "ssl_min_protocol_version", "ssl_max_protocol_version", "sslcrl",
        "sslcrldir", "sslmode", "sslrootcert", "sslsni",
        "tcp_user_timeout",
    }
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.fragment
        or not name
        or "/" in name
        or any(key.lower() not in safe_query_parameters for key, _value in query)
    ):
        raise SystemExit(f"{label} database path is invalid")
    return name

database_name = database_name_from_url(matches[0], "DATABASE_URL")
pool_url = runtime_values.get("DATABASE_POOL_URL", "").strip()
pool_flag = runtime_values.get("DB_USE_PGBOUNCER", "1" if pool_url else "0").strip().lower()
if pool_flag not in {"0", "false", "no", "off", "1", "true", "yes", "on"}:
    raise SystemExit("DB_USE_PGBOUNCER must be an explicit boolean")
pool_enabled = pool_flag in {"1", "true", "yes", "on"}
if pool_enabled and not pool_url:
    raise SystemExit("DB_USE_PGBOUNCER requires DATABASE_POOL_URL")
if staging_clone_mode == "1" and pool_enabled:
    raise SystemExit("DB_USE_PGBOUNCER must be disabled for staging clone")
if pool_url and database_name_from_url(pool_url, "DATABASE_POOL_URL") != database_name:
    raise SystemExit("DATABASE_POOL_URL database identity must match DATABASE_URL")
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
        pending = manifest.get("pending_migrations") or []
        compatible = manifest.get("forward_compatible_migrations") or []
        if (
            not isinstance(pending, list)
            or not isinstance(compatible, list)
            or pending != compatible
        ):
            raise SystemExit("clone-reuse migrations lack an exact declaration")
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
  read -r PREDEPLOY_DATABASE_NAME PREDEPLOY_ENV_SHA256 STAGING_SOURCE_KIND PREDEPLOY_DATABASE_OWNER_RELEASE_ID ACTIVE_RELEASE_ID < <(printf '%s' "${REMOTE_PREDEPLOY_DB_STATE_JSON}" | run_local_python_program 'import json,os; p=json.load(os.fdopen(3)); print(p["database_name"], p["env_sha256"], p["source_kind"], p["database_owner_release_id"], p["active_release_id"])')
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
if ! PENDING_MIGRATIONS="$("${LOCAL_SAFE_PYTHON}" - "${PREDEPLOY_MIGRATION}" "${MIGRATION_MANIFEST_CSV}" <<'PY'
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
fi
if [ "${STAGING_SOURCE_KIND}" = "prior-release-clone" ] \
  && [ "${STAGING_DB_CLONE_MODE}" != "1" ]; then
  # App-only releases and exactly declared forward-compatible migrations keep
  # using the proven active clone.  Preserve its original owner/receipt lineage
  # so later releases never mistake the reused database for an in-place base.
  DATABASE_RELEASE_STRATEGY="reuse-active-clone"
  DATABASE_ENV_ASSERT_RUNTIME_POOL_FLAG="--allow-runtime-pool"
  STAGING_SOURCE_DATABASE=""
  STAGING_CLONE_DATABASE="${PREDEPLOY_DATABASE_NAME}"
  DATABASE_OWNER_RELEASE_ID="${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}"
fi
if [ "${RESCUE_ROLLBACK_MODE}" = "1" ] \
  && [ "${STAGING_SOURCE_KIND}" = "prior-release-clone" ]; then
  ROLLBACK_ANCHOR_DATABASE_STRATEGY="reuse-active-clone"
  ROLLBACK_ANCHOR_TARGET_DATABASE="${PREDEPLOY_DATABASE_NAME}"
  ROLLBACK_ANCHOR_DATABASE_OWNER_RELEASE_ID="${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}"
  ROLLBACK_ANCHOR_ENV_FINGERPRINT="${PREDEPLOY_ENV_SHA256}"
fi

# Prove the live boundary is already idle before stopping even one service or
# timer or applying first-bootstrap filesystem hardening. A non-empty
# queue/provider/DB receipt exits in place with no partial quiesce to restore.
verify_remote_legacy_writers_absent
verify_remote_release_validation_fence absent
verify_remote_release_drain live
if [ "${LIVE_RELEASE_DRAIN_VERIFIED}" != "1" ]; then
  echo "Refusing timer/service quiesce without a verified live idle boundary." >&2
  exit 1
fi

if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
  # Bind the real running Web process, not only the base dotenv.  Reviewed
  # systemd drop-ins may enable PgBouncer after the main file is loaded.
  capture_remote_web_database_runtime "${STAGING_SOURCE_DATABASE}"
  PGBOUNCER_WEB_POOL_EFFECTIVE_BEFORE="${PGBOUNCER_WEB_POOL_EFFECTIVE}"
  if [ "${PGBOUNCER_WEB_POOL_EFFECTIVE_BEFORE}" = "1" ] \
    && [ "${PGBOUNCER_SERVICE_ACTIVE_STATE}" != "active" ]; then
    echo "Refusing staging clone because Web uses PgBouncer but its service is inactive." >&2
    exit 1
  fi
  capture_remote_pgbouncer_database_map
fi

if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  verify_deploy_candidate
  assert_deploy_source_unchanged
  if [ "${STAGING_SOURCE_KIND}" != "legacy-base" ] \
    || [ "${PREDEPLOY_DATABASE_NAME}" != "viltrox2_test" ] \
    || [ -z "${PENDING_MIGRATIONS}" ]; then
    echo "The first atomic bootstrap requires the untouched legacy database source and at least one pending migration." >&2
    exit 1
  fi

  FIRST_ATOMIC_BOOTSTRAP_BACKUP_STAMP="$(
    run_frozen_candidate_python \
      "${DEPLOY_CANDIDATE_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py" plan-field \
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

  if run_frozen_candidate_python \
    "${DEPLOY_CANDIDATE_DIR}/scripts/ops/legacy_to_atomic_preflight.py" \
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
    <"${DEPLOY_CANDIDATE_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py" \
    >"${BOOTSTRAP_ANCHOR_JSON}"
  chmod 600 "${BOOTSTRAP_ANCHOR_JSON}"

  FIRST_ATOMIC_BOOTSTRAP_SUMMARY="$(
    run_frozen_candidate_python \
      "${DEPLOY_CANDIDATE_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py" verify-plan \
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
      printf '%s' "${FIRST_ATOMIC_BOOTSTRAP_SUMMARY}" | run_local_python_program \
        'import json,os; p=json.load(os.fdopen(3)); print(p["plan_sha256"],p["server_git_sha"],p["client_git_sha"],p["root_build_git_sha"],p["database_name"],p["db_migration"],p["environment_sha256"])'
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
  verify_deploy_candidate
  assert_deploy_source_unchanged
fi

# Freeze timer-triggered writers before backup and release staging.  Frontend
# bytes were already rebuilt in a private directory and compared file-for-file
# with the frozen candidate before the first SSH call; never rebuild the ambient
# source dist after remote writers have been quiesced.
quiesce_remote_sync_units

read_prior_clone_backup_boundary() {
  ssh "${SSH_TARGET}" \
    "sudo -n env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B - --root '${REMOTE_ROOT}' --expected-active-release-id '${ACTIVE_RELEASE_ID}' --expected-database-owner-release-id '${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}' --expected-database '${PREDEPLOY_DATABASE_NAME}'" \
    <"${DEPLOY_CANDIDATE_DIR}/scripts/ops/prior_clone_backup_boundary.py"
}

if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  verify_deploy_candidate
  assert_deploy_source_unchanged
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
    "${LOCAL_SAFE_PYTHON}" - \
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
  elif [ "${STAGING_SOURCE_KIND}" = "prior-release-clone" ] \
    && [ -n "${PENDING_MIGRATIONS}" ]; then
    if ! PRIOR_CLONE_BOUNDARY_BEFORE="$(read_prior_clone_backup_boundary)"; then
      echo "Refusing prior-clone migration because its pre-backup boundary is unreadable." >&2
      exit 1
    fi
    if ! read -r PRIOR_CLONE_ENV_SHA_BEFORE PRIOR_CLONE_ACTIVE_MANIFEST_SHA256 < <(
      printf '%s' "${PRIOR_CLONE_BOUNDARY_BEFORE}" | run_local_python_program \
        'import json,os; p=json.load(os.fdopen(3)); print(p["env_sha256"], p["active_manifest_sha256"])'
    ); then
      echo "Refusing prior-clone migration because its pre-backup evidence is invalid." >&2
      exit 1
    fi
    if [ "${PRIOR_CLONE_ENV_SHA_BEFORE}" != "${PREDEPLOY_ENV_SHA256}" ] \
      || ! [[ "${PRIOR_CLONE_ACTIVE_MANIFEST_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
      echo "Refusing prior-clone migration because its environment or manifest changed before backup." >&2
      exit 1
    fi
    PRIOR_CLONE_BACKUP_STAMP="${RELEASE_ID}-pre-migration"
    PRIOR_CLONE_BACKUP_DIR="${PROJECT_ROOT}/runtime/prod-sync/${PRIOR_CLONE_BACKUP_STAMP}"
    STAMP="${PRIOR_CLONE_BACKUP_STAMP}" LOCAL_DIR="${PRIOR_CLONE_BACKUP_DIR}" \
      REMOTE_APP_USER="${REMOTE_APP_USER}" REMOTE_APP_GROUP="${REMOTE_APP_GROUP}" \
      "${SCRIPT_DIR}/backup_prod_vkpi.sh"
    if ! PRIOR_CLONE_BOUNDARY_AFTER="$(read_prior_clone_backup_boundary)"; then
      echo "Refusing prior-clone migration because its post-backup boundary is unreadable." >&2
      exit 1
    fi
    if ! read -r PRIOR_CLONE_ENV_SHA_AFTER PRIOR_CLONE_MANIFEST_SHA_AFTER < <(
      printf '%s' "${PRIOR_CLONE_BOUNDARY_AFTER}" | run_local_python_program \
        'import json,os; p=json.load(os.fdopen(3)); print(p["env_sha256"], p["active_manifest_sha256"])'
    ); then
      echo "Refusing prior-clone migration because its post-backup evidence is invalid." >&2
      exit 1
    fi
    if [ "${PRIOR_CLONE_ENV_SHA_AFTER}" != "${PREDEPLOY_ENV_SHA256}" ] \
      || [ "${PRIOR_CLONE_MANIFEST_SHA_AFTER}" != "${PRIOR_CLONE_ACTIVE_MANIFEST_SHA256}" ]; then
      echo "Refusing prior-clone migration because its environment or manifest changed during backup." >&2
      exit 1
    fi
    pg_restore --list "${PRIOR_CLONE_BACKUP_DIR}/prod-db.dump" >/dev/null
    "${LOCAL_SAFE_PYTHON}" - \
      "${PRIOR_CLONE_BACKUP_DIR}" \
      "${PRIOR_CLONE_BACKUP_STAMP}" \
      "${RELEASE_ID}" \
      "${ACTIVE_RELEASE_ID}" \
      "${PREDEPLOY_APP_SHA}" \
      "${PREDEPLOY_MIGRATION}" \
      "${PREDEPLOY_DATABASE_NAME}" \
      "${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}" \
      "${PREDEPLOY_ENV_SHA256}" \
      "${PRIOR_CLONE_ACTIVE_MANIFEST_SHA256}" \
      "${PENDING_MIGRATIONS}" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

directory = Path(sys.argv[1])
stamp, release_id, active_release_id = sys.argv[2:5]
predeploy_sha, predeploy_migration, database, database_owner_release_id = sys.argv[5:9]
predeploy_env_sha256, active_manifest_sha256 = sys.argv[9:11]
pending = [value for value in sys.argv[11].split(",") if value]
dump = directory / "prod-db.dump"
sidecar = directory / "prod-db.dump.sha256"
runtime_state = directory / "runtime-state.txt"
receipt = directory / "release-migration-backup-receipt.json"
if not directory.is_dir() or directory.is_symlink():
    raise SystemExit("prior-clone backup directory is unsafe")
for path in (dump, sidecar, runtime_state):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
        raise SystemExit("prior-clone backup artifact is unsafe")
parts = sidecar.read_text(encoding="ascii").split()
if len(parts) != 2 or parts[1] != "prod-db.dump" or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
    raise SystemExit("prior-clone backup sidecar is invalid")
digest = hashlib.sha256()
with dump.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
if digest.hexdigest() != parts[0]:
    raise SystemExit("prior-clone backup digest mismatch")
state: dict[str, str] = {}
for line in runtime_state.read_text(encoding="utf-8").splitlines():
    if "=" not in line:
        raise SystemExit("prior-clone runtime manifest is malformed")
    key, value = line.split("=", 1)
    if not key or key in state:
        raise SystemExit("prior-clone runtime manifest has duplicate keys")
    state[key] = value
expected = {
    "stamp": stamp,
    "remote_root": "/opt/viltrox-2.0",
    "service": "viltrox-2.0-test.service",
    "release_state": "valid",
    "current_path": f"/opt/viltrox-2.0/releases/{active_release_id}",
    "release_id": active_release_id,
    "git_head": predeploy_sha,
}
if any(state.get(key) != value for key, value in expected.items()):
    raise SystemExit("prior-clone runtime manifest is not bound to the predeploy release")
if not re.fullmatch(r"app-[A-Za-z0-9_-]+\.js", state.get("frontend_asset", "")):
    raise SystemExit("prior-clone runtime manifest lacks a valid frontend asset")
if not re.fullmatch(r"[A-Za-z0-9_.-]+", database_owner_release_id):
    raise SystemExit("prior-clone backup lost its database owner lineage")
if not re.fullmatch(r"[0-9a-f]{64}", predeploy_env_sha256):
    raise SystemExit("prior-clone backup lost its environment lineage")
if not re.fullmatch(r"[0-9a-f]{64}", active_manifest_sha256):
    raise SystemExit("prior-clone backup lost its active manifest lineage")
payload = {
    "schema_version": "vkpi-release-migration-backup/v1",
    "release_id": release_id,
    "backup_stamp": stamp,
    "active_release_id": active_release_id,
    "predeploy_git_sha": predeploy_sha,
    "predeploy_migration": predeploy_migration,
    "database": database,
    "database_owner_release_id": database_owner_release_id,
    "predeploy_env_sha256": predeploy_env_sha256,
    "active_manifest_sha256": active_manifest_sha256,
    "pending_migrations": pending,
    "forward_compatible_migrations": pending,
    "db_sha256": parts[0],
    "runtime_state_sha256": hashlib.sha256(runtime_state.read_bytes()).hexdigest(),
    "pg_restore_list_passed": True,
    "local_copy_verified": True,
}
descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
parent = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(parent)
finally:
    os.close(parent)
print(f"[deploy] verified release-bound prior-clone backup receipt: {receipt}")
PY
  else
    REMOTE_APP_USER="${REMOTE_APP_USER}" REMOTE_APP_GROUP="${REMOTE_APP_GROUP}" \
      "${SCRIPT_DIR}/backup_prod_vkpi.sh"
  fi
fi

# A local gate/build/backup must not have changed the payload or HEAD.
assert_deploy_source_unchanged
verify_deploy_candidate

# Create a new immutable destination first.  The running services continue to
# resolve the old current symlink until the complete payload is sealed and the
# symlink is atomically replaced.
ssh "${SSH_TARGET}" "sudo install -d -o root -g root -m 0755 '${REMOTE_RELEASES_DIR}' && if [ -e '${REMOTE_RELEASE_DIR}' ] || [ -L '${REMOTE_RELEASE_DIR}' ]; then echo 'Refusing to reuse an existing release destination.' >&2; exit 1; fi && sudo install -d -o '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' -m 0755 '${REMOTE_RELEASE_DIR}'"
rsync -az --delete \
  --no-owner \
  --no-group \
  --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '.pytest_cache' \
  --exclude '.mypy_cache' \
  --exclude '.ruff_cache' \
  --exclude '.vite' \
  --exclude '.claude' \
  --exclude '.codegraph' \
  --exclude '.codex-backups' \
  --exclude '.integration' \
  --exclude '.state' \
  --exclude '.coverage' \
  --exclude 'coverage' \
  --exclude '.DS_Store' \
  --exclude 'node_modules' \
  --exclude 'uploads' \
  --exclude 'frames' \
  --exclude 'creator_profiles' \
  --exclude 'runtime' \
  --exclude 'backups' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'deploy/env' \
  --exclude 'artifacts' \
  --exclude 'exports' \
  --exclude 'output' \
  --exclude 'outputs' \
  --exclude 'tmp' \
  --exclude 'submissions.db' \
  --exclude 'submissions.db-shm' \
  --exclude 'submissions.db-wal' \
  --exclude 'id_ed25519' \
  --exclude 'id_rsa' \
  --exclude '*.dump' \
  --exclude '*.key' \
  --exclude '*.log' \
  --exclude '*.p12' \
  --exclude '*.pem' \
  --exclude '*.pfx' \
  --exclude '*.sqlite' \
  --exclude '*.sqlite3' \
  --exclude 'video-production-platform/' \
  --exclude 'reports/generated' \
  -- "${DEPLOY_CANDIDATE_DIR}/" "${SSH_TARGET}:${REMOTE_RELEASE_DIR}/"

# If either the reviewed candidate or the worktree identity changed during
# rsync, do not seal or restart the remote tree as though it represented the
# verified immutable source.
verify_deploy_candidate
assert_deploy_source_unchanged

ssh "${SSH_TARGET}" "cd '${REMOTE_RELEASE_DIR}' && [ \"\$(cat -- BUILD_GIT_SHA)\" = '${LOCAL_GIT_SHA}' ] || { echo 'Uploaded candidate build SHA mismatch.' >&2; exit 1; }"

# Seal the release and prove every runtime dependency before current can move.
# Package installation during deployment is forbidden: the shared venv is an
# independently prepared prerequisite, not mutable release state.
# Every remote Python call carries both the environment guard and -B.  The
# redundancy is intentional: helpers run before and after sealing, and neither
# a sudo environment policy nor a future env -i refactor may recreate bytecode
# inside the immutable release/current tree and invalidate its payload digest.
if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  # The legacy .env is intentionally still writable until prepare has captured
  # it.  Provision only the absent non-secret job-results directory needed by
  # seal; after prepare, harden .env and run the full worker permission checks
  # while the complete consumer fleet is stopped.
  ssh "${SSH_TARGET}" "runtime_dir='${REMOTE_ROOT}/runtime'; job_results_dir='${REMOTE_ROOT}/runtime/job-results'; if [ ! -d \"\${runtime_dir}\" ] || [ -L \"\${runtime_dir}\" ] || [ \"\$(stat -c '%U:%G:%a' \"\${runtime_dir}\")\" != '${REMOTE_APP_USER}:${REMOTE_APP_GROUP}:755' ]; then echo 'bootstrap shared runtime parent is unsafe' >&2; exit 1; fi; if [ ! -e \"\${job_results_dir}\" ] && [ ! -L \"\${job_results_dir}\" ]; then sudo install -d -o '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' -m 0750 \"\${job_results_dir}\"; fi; if [ ! -d \"\${job_results_dir}\" ] || [ -L \"\${job_results_dir}\" ] || [ \"\$(stat -c '%U:%G:%a' \"\${job_results_dir}\")\" != '${REMOTE_APP_USER}:${REMOTE_APP_GROUP}:750' ]; then echo 'bootstrap job-results directory is unsafe' >&2; exit 1; fi"
  ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' seal --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --git-sha '${LOCAL_GIT_SHA}' --pending-migrations '${PENDING_MIGRATIONS}' --compatibility-declaration '' --database-strategy 'staging-clone' --source-database '${STAGING_SOURCE_DATABASE}' --target-database '${STAGING_CLONE_DATABASE}' --env-fingerprint-before '${PREDEPLOY_ENV_SHA256}' --database-owner-release-id '' --owner-uid 0 --owner-gid 0 && sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' verify-seal --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --expected-owner-uid 0 --expected-owner-gid 0 && sudo -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B -m yt_dlp --version >/dev/null && sudo systemd-analyze verify '${REMOTE_RELEASE_DIR}/${REMOTE_SERVICE_UNIT_RELATIVE}' '${REMOTE_RELEASE_DIR}/${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}' '${REMOTE_RELEASE_DIR}/${HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE}' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/vkpi-worker-interactive.service' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/vkpi-worker-bulk@.service' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/${STAGING_REDIS_WORKER_SERVICE}'"
  BOOTSTRAP_CANDIDATE_MANIFEST="${FIRST_ATOMIC_BOOTSTRAP_EVIDENCE_DIR}/candidate-manifest.json"
  ssh "${SSH_TARGET}" "sudo cat -- '${REMOTE_RELEASE_DIR}/.vkpi-release.json'" >"${BOOTSTRAP_CANDIDATE_MANIFEST}"
  chmod 600 "${BOOTSTRAP_CANDIDATE_MANIFEST}"
  run_frozen_candidate_python \
    "${DEPLOY_CANDIDATE_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py" verify-candidate \
    --plan "${FIRST_ATOMIC_BOOTSTRAP_PLAN}" \
    --confirm "${FIRST_ATOMIC_BOOTSTRAP_CONFIRM}" \
    --manifest "${BOOTSTRAP_CANDIDATE_MANIFEST}" \
    --target-database "${STAGING_CLONE_DATABASE}" >/dev/null
  verify_deploy_candidate
  assert_deploy_source_unchanged
else
  ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' worker-layout-preflight --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --app-user '${REMOTE_APP_USER}' --app-group '${REMOTE_APP_GROUP}' --provision-missing && sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' seal --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --git-sha '${LOCAL_GIT_SHA}' --pending-migrations '${PENDING_MIGRATIONS}' --compatibility-declaration '${FORWARD_COMPATIBILITY_DECLARATION}' --database-strategy '${DATABASE_RELEASE_STRATEGY}' --source-database '${STAGING_SOURCE_DATABASE}' --target-database '${STAGING_CLONE_DATABASE}' --env-fingerprint-before '${PREDEPLOY_ENV_SHA256}' --database-owner-release-id '${DATABASE_OWNER_RELEASE_ID}' --owner-uid 0 --owner-gid 0 && sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' verify-seal --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --expected-owner-uid 0 --expected-owner-gid 0 && sudo -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B -m yt_dlp --version >/dev/null && sudo -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env VKPI_JOB_RESULTS_DIR='${REMOTE_ROOT}/runtime/job-results' HOME=/tmp/vkpi-worker-home XDG_CACHE_HOME=/tmp/vkpi-worker-cache TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' worker-runtime-preflight --root '${REMOTE_ROOT}' --release-path '${REMOTE_RELEASE_DIR}' --app-user '${REMOTE_APP_USER}' --app-group '${REMOTE_APP_GROUP}' --job-results-dir '${REMOTE_ROOT}/runtime/job-results' && sudo systemd-analyze verify '${REMOTE_RELEASE_DIR}/${REMOTE_SERVICE_UNIT_RELATIVE}' '${REMOTE_RELEASE_DIR}/${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}' '${REMOTE_RELEASE_DIR}/${HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE}' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/vkpi-worker-interactive.service' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/vkpi-worker-bulk@.service' '${REMOTE_RELEASE_DIR}/scripts/ops/systemd/${STAGING_REDIS_WORKER_SERVICE}'"
fi

if [ "${RESCUE_ROLLBACK_MODE}" = "1" ]; then
  # Rebuild the running SHA from a separately frozen clean worktree.  Never
  # copy, clean, or reseal the contaminated active release: it remains intact
  # for audit while this non-active release becomes the only rollback target.
  verify_rescue_rollback_candidate
  ssh "${SSH_TARGET}" "if [ -e '${REMOTE_ROLLBACK_ANCHOR_DIR}' ] || [ -L '${REMOTE_ROLLBACK_ANCHOR_DIR}' ]; then echo 'Refusing to reuse an existing rescue rollback destination.' >&2; exit 1; fi && sudo install -d -o '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' -m 0755 '${REMOTE_ROLLBACK_ANCHOR_DIR}'"
  rsync -az --delete \
    --no-owner \
    --no-group \
    --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude 'venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    --exclude '.pytest_cache' \
    --exclude '.mypy_cache' \
    --exclude '.ruff_cache' \
    --exclude '.vite' \
    --exclude '.claude' \
    --exclude '.codegraph' \
    --exclude '.codex-backups' \
    --exclude '.integration' \
    --exclude '.state' \
    --exclude '.coverage' \
    --exclude 'coverage' \
    --exclude '.DS_Store' \
    --exclude 'node_modules' \
    --exclude 'uploads' \
    --exclude 'frames' \
    --exclude 'creator_profiles' \
    --exclude 'runtime' \
    --exclude 'backups' \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude 'deploy/env' \
    --exclude 'artifacts' \
    --exclude 'exports' \
    --exclude 'output' \
    --exclude 'outputs' \
    --exclude 'tmp' \
    --exclude 'submissions.db' \
    --exclude 'submissions.db-shm' \
    --exclude 'submissions.db-wal' \
    --exclude 'id_ed25519' \
    --exclude 'id_rsa' \
    --exclude '*.dump' \
    --exclude '*.key' \
    --exclude '*.log' \
    --exclude '*.p12' \
    --exclude '*.pem' \
    --exclude '*.pfx' \
    --exclude '*.sqlite' \
    --exclude '*.sqlite3' \
    --exclude 'video-production-platform/' \
    --exclude 'reports/generated' \
    -- "${RESCUE_ROLLBACK_CANDIDATE_DIR}/" "${SSH_TARGET}:${REMOTE_ROLLBACK_ANCHOR_DIR}/"
  verify_rescue_rollback_candidate
  ssh "${SSH_TARGET}" "cd '${REMOTE_ROLLBACK_ANCHOR_DIR}' && [ \"\$(cat -- BUILD_GIT_SHA)\" = '${PREDEPLOY_APP_SHA}' ] || { echo 'Uploaded rescue rollback build SHA mismatch.' >&2; exit 1; }"
  ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' worker-layout-preflight --root '${REMOTE_ROOT}' --release-id '${ROLLBACK_ANCHOR_RELEASE_ID}' --app-user '${REMOTE_APP_USER}' --app-group '${REMOTE_APP_GROUP}' --provision-missing && sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' seal --root '${REMOTE_ROOT}' --release-id '${ROLLBACK_ANCHOR_RELEASE_ID}' --git-sha '${PREDEPLOY_APP_SHA}' --pending-migrations '' --compatibility-declaration '' --database-strategy '${ROLLBACK_ANCHOR_DATABASE_STRATEGY}' --source-database '' --target-database '${ROLLBACK_ANCHOR_TARGET_DATABASE}' --env-fingerprint-before '${ROLLBACK_ANCHOR_ENV_FINGERPRINT}' --database-owner-release-id '${ROLLBACK_ANCHOR_DATABASE_OWNER_RELEASE_ID}' --owner-uid 0 --owner-gid 0 && sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' verify-seal --root '${REMOTE_ROOT}' --release-id '${ROLLBACK_ANCHOR_RELEASE_ID}' --expected-owner-uid 0 --expected-owner-gid 0"
fi

# Capture the exact effective env/units and establish previous before changing
# shared configuration or the active application pointer.
if ! STAGING_REDIS_WORKER_CAPTURED_STATE="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' inspect-unit-state --unit-dir /etc/systemd/system --unit-name '${STAGING_REDIS_WORKER_SERVICE}'")"; then
  echo "Refusing deploy because the Redis worker systemd state is not exactly restorable." >&2
  exit 1
fi
ROLLBACK_PREPARE_MAY_HAVE_COMMITTED=1
if ! ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/atomic_release_layout.py' prepare --root '${REMOTE_ROOT}' --release-id '${RELEASE_ID}' --unit-dir /etc/systemd/system --unit-name '${SERVICE_NAME}' --unit-name '${SYNC_SERVICE}' --unit-name '${HEALTH_SENTINEL_SERVICE}' --unit-name vkpi-worker-interactive.service --unit-name 'vkpi-worker-bulk@.service' --optional-unit-name '${STAGING_REDIS_WORKER_SERVICE}' --optional-unit-state '${STAGING_REDIS_WORKER_SERVICE}=${STAGING_REDIS_WORKER_CAPTURED_STATE}' --rollback-file '${REMOTE_LANE_OVERRIDE_FILE}' --pending-migrations '${PENDING_MIGRATIONS}' --compatibility-declaration '${FORWARD_COMPATIBILITY_DECLARATION}' --database-strategy '${DATABASE_RELEASE_STRATEGY}' --source-database '${STAGING_SOURCE_DATABASE}' --target-database '${STAGING_CLONE_DATABASE}' --env-fingerprint-before '${PREDEPLOY_ENV_SHA256}' --database-owner-release-id '${DATABASE_OWNER_RELEASE_ID}' ${ROLLBACK_ANCHOR_PREPARE_OPTION}"; then
  if ! reconcile_remote_prepare_commit_state \
    || [ "${ROLLBACK_ARMED}" != "1" ]; then
    echo "Atomic release prepare failed without a verified committed receipt." >&2
    exit 1
  fi
else
  ROLLBACK_ARMED=1
  ROLLBACK_PREPARE_MAY_HAVE_COMMITTED=0
fi

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
verify_remote_release_drain quiesced

if [ "${RELEASE_DRAIN_VERIFIED}" != "1" ]; then
  echo "Refusing release mutation without a verified empty Redis/database/provider boundary." >&2
  exit 1
fi
install_remote_release_validation_fence

if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
  if [ "${STAGING_BACKUP_VERIFIED}" != "1" ]; then
    echo "Refusing staging clone creation without a verified release-bound backup." >&2
    exit 1
  fi
  # CREATE DATABASE ... TEMPLATE requires the source database to have no app
  # connections.  The common quiesce step above covers this and app-only releases.
  if [ "${RELEASE_CONSUMERS_QUIESCED}" != "1" ] \
    || [ "${PGBOUNCER_QUIESCED}" != "1" ]; then
    echo "Refusing staging clone creation while release consumers or PgBouncer are not quiesced." >&2
    exit 1
  fi

  STAGING_CLONE_CREATE_JSON="$(ssh "${SSH_TARGET}" "sudo -n -u postgres env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' create --source-db '${STAGING_SOURCE_DATABASE}' --target-db '${STAGING_CLONE_DATABASE}'")"
  if ! printf '%s' "${STAGING_CLONE_CREATE_JSON}" | run_local_python_program 'import json,os,sys; p=json.load(os.fdopen(3)); assert p["source_database"] == sys.argv[1]; assert p["target_database"] == sys.argv[2]; assert int(p["free_bytes_before"]) >= int(p["source_size_bytes"]) + 1024**3' "${STAGING_SOURCE_DATABASE}" "${STAGING_CLONE_DATABASE}"; then
    echo "Staging clone creation receipt did not satisfy the reviewed disk/identity contract." >&2
    exit 1
  fi

  # PgBouncer is still stopped and runtime-masked here.  Install exactly the
  # old+new aliases before either URL or current pointer can name the clone.
  prepare_remote_pgbouncer_database_map

  STAGING_CLONE_ENV_STATE="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' switch-env --env-file '${REMOTE_ROOT}/.env' --expected-source-db '${STAGING_SOURCE_DATABASE}' --target-db '${STAGING_CLONE_DATABASE}'")"
  read -r STAGING_CLONE_ENV_DATABASE STAGING_CLONE_ENV_SHA256 < <(printf '%s' "${STAGING_CLONE_ENV_STATE}" | run_local_python_program 'import json,os; p=json.load(os.fdopen(3)); print(p["database_name"], p["env_sha256"])')
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
  STAGING_FINAL_ENV_STATE="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_RELEASE_DIR}/scripts/ops/staging_db_clone.py' assert-env --env-file '${REMOTE_ROOT}/.env' --expected-db '${STAGING_CLONE_DATABASE}' ${DATABASE_ENV_ASSERT_RUNTIME_POOL_FLAG}")"
  read -r STAGING_FINAL_ENV_DATABASE STAGING_CLONE_ENV_SHA256 < <(printf '%s' "${STAGING_FINAL_ENV_STATE}" | run_local_python_program 'import json,os; p=json.load(os.fdopen(3)); print(p["database_name"], p["env_sha256"])')
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
# The reviewed daily-sync unit is itself part of the captured rollback set.
# Install it atomically while the sync timer/service remain runtime-masked, then
# make systemd observe the byte-checked replacement before any state restore.
ssh "${SSH_TARGET}" "set -eu; sync_unit_source='${REMOTE_CURRENT_DIR}/${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}'; sync_unit_target='/etc/systemd/system/${SYNC_SERVICE}'; if [ ! -f \"\${sync_unit_source}\" ] || [ -L \"\${sync_unit_source}\" ]; then echo 'reviewed sync service source is not a regular non-symlink file' >&2; exit 1; fi; sync_unit_tmp=\$(sudo mktemp '/etc/systemd/system/.${SYNC_SERVICE}.XXXXXX'); cleanup_sync_unit_tmp() { if [ -n \"\${sync_unit_tmp}\" ]; then sudo rm -f -- \"\${sync_unit_tmp}\"; fi; }; trap cleanup_sync_unit_tmp EXIT; sudo install -o root -g root -m 0644 \"\${sync_unit_source}\" \"\${sync_unit_tmp}\"; if [ -L \"\${sync_unit_tmp}\" ] || [ \"\$(sudo stat -c '%u:%g:%a' \"\${sync_unit_tmp}\")\" != '0:0:644' ] || ! sudo cmp -s \"\${sync_unit_source}\" \"\${sync_unit_tmp}\"; then echo 'staged sync service owner, mode, or content verification failed' >&2; exit 1; fi; sudo mv -f -- \"\${sync_unit_tmp}\" \"\${sync_unit_target}\"; sync_unit_tmp=''; trap - EXIT; if [ ! -f \"\${sync_unit_target}\" ] || [ -L \"\${sync_unit_target}\" ] || [ \"\$(sudo stat -c '%u:%g:%a' \"\${sync_unit_target}\")\" != '0:0:644' ] || ! sudo cmp -s \"\${sync_unit_source}\" \"\${sync_unit_target}\"; then echo 'installed sync service owner, mode, or content verification failed' >&2; exit 1; fi; sudo systemctl daemon-reload"
# The sentinel service is in the same rollback capture as the web/worker units.
# Replace it through a verified same-filesystem temporary file so a lost SSH
# connection cannot leave a truncated unit between prepare and state restore.
ssh "${SSH_TARGET}" "set -eu; sentinel_unit_source='${REMOTE_CURRENT_DIR}/${HEALTH_SENTINEL_SERVICE_UNIT_RELATIVE}'; sentinel_unit_target='/etc/systemd/system/${HEALTH_SENTINEL_SERVICE}'; if [ ! -f \"\${sentinel_unit_source}\" ] || [ -L \"\${sentinel_unit_source}\" ]; then echo 'reviewed health sentinel source is not a regular non-symlink file' >&2; exit 1; fi; sentinel_unit_tmp=\$(sudo mktemp '/etc/systemd/system/.${HEALTH_SENTINEL_SERVICE}.XXXXXX'); cleanup_sentinel_unit_tmp() { if [ -n \"\${sentinel_unit_tmp}\" ]; then sudo rm -f -- \"\${sentinel_unit_tmp}\"; fi; }; trap cleanup_sentinel_unit_tmp EXIT; sudo install -o root -g root -m 0644 \"\${sentinel_unit_source}\" \"\${sentinel_unit_tmp}\"; if [ -L \"\${sentinel_unit_tmp}\" ] || [ \"\$(sudo stat -c '%u:%g:%a' \"\${sentinel_unit_tmp}\")\" != '0:0:644' ] || ! sudo cmp -s \"\${sentinel_unit_source}\" \"\${sentinel_unit_tmp}\"; then echo 'staged health sentinel owner, mode, or content verification failed' >&2; exit 1; fi; sudo mv -f -- \"\${sentinel_unit_tmp}\" \"\${sentinel_unit_target}\"; sentinel_unit_tmp=''; trap - EXIT; if [ ! -f \"\${sentinel_unit_target}\" ] || [ -L \"\${sentinel_unit_target}\" ] || [ \"\$(sudo stat -c '%u:%g:%a' \"\${sentinel_unit_target}\")\" != '0:0:644' ] || ! sudo cmp -s \"\${sentinel_unit_source}\" \"\${sentinel_unit_target}\"; then echo 'installed health sentinel owner, mode, or content verification failed' >&2; exit 1; fi; sudo systemctl daemon-reload"
ssh "${SSH_TARGET}" "sudo systemctl unmask '${STAGING_REDIS_WORKER_SERVICE}' >/dev/null 2>&1 || true; sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/${REMOTE_SERVICE_UNIT_RELATIVE}' '/etc/systemd/system/${SERVICE_NAME}' && sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/scripts/ops/systemd/vkpi-worker-interactive.service' '/etc/systemd/system/vkpi-worker-interactive.service' && sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/scripts/ops/systemd/vkpi-worker-bulk@.service' '/etc/systemd/system/vkpi-worker-bulk@.service' && sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/scripts/ops/systemd/${STAGING_REDIS_WORKER_SERVICE}' '/etc/systemd/system/${STAGING_REDIS_WORKER_SERVICE}' && sudo install -d -o root -g root -m 0755 '${REMOTE_LANE_OVERRIDE_DIR}' && [ ! -L '${REMOTE_LANE_OVERRIDE_DIR}' ] && [ \"\$(sudo stat -c '%u:%g:%a' '${REMOTE_LANE_OVERRIDE_DIR}')\" = '0:0:755' ] && lane_tmp=\$(sudo mktemp '${REMOTE_LANE_OVERRIDE_DIR}/.vkpi-lane-overrides.env.XXXXXX') && cleanup_lane_tmp() { if [ -n \"\${lane_tmp}\" ]; then sudo rm -f -- \"\${lane_tmp}\"; fi; } && trap cleanup_lane_tmp EXIT && sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}' \"\${lane_tmp}\" && sudo cmp -s '${REMOTE_CURRENT_DIR}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}' \"\${lane_tmp}\" && sudo mv -f -- \"\${lane_tmp}\" '${REMOTE_LANE_OVERRIDE_FILE}' && lane_tmp='' && trap - EXIT && [ ! -L '${REMOTE_LANE_OVERRIDE_FILE}' ] && [ \"\$(sudo stat -c '%u:%g:%a' '${REMOTE_LANE_OVERRIDE_FILE}')\" = '0:0:644' ] && sudo cmp -s '${REMOTE_CURRENT_DIR}/${LANE_OVERRIDE_TEMPLATE_RELATIVE}' '${REMOTE_LANE_OVERRIDE_FILE}' && sudo systemctl daemon-reload"

# The static dual map must still match the prepared hash before PgBouncer is
# allowed to listen.  Then prove both aliases through 6432 before any new
# release consumer can connect.
verify_remote_pgbouncer_database_map
restore_remote_pgbouncer_state
if [ "${PGBOUNCER_SERVICE_ACTIVE_STATE}" = "active" ]; then
  probe_remote_pgbouncer_database "${STAGING_SOURCE_DATABASE}"
  probe_remote_pgbouncer_database "${STAGING_CLONE_DATABASE}"
fi

REDIS_WORKER_RESTART_NOT_BEFORE="$(ssh "${SSH_TARGET}" "date -u +%Y-%m-%dT%H:%M:%SZ")"
REDIS_WORKER_MAIN_PID="$(ssh "${SSH_TARGET}" "sudo systemctl enable --now '${STAGING_REDIS_WORKER_SERVICE}' && systemctl is-active --quiet '${STAGING_REDIS_WORKER_SERVICE}' && systemctl is-enabled --quiet '${STAGING_REDIS_WORKER_SERVICE}' && systemctl show --property MainPID --value '${STAGING_REDIS_WORKER_SERVICE}'")"
if ! [[ "${REDIS_WORKER_MAIN_PID}" =~ ^[1-9][0-9]*$ ]]; then
  echo "Dedicated Redis worker systemd MainPID is invalid after restart." >&2
  exit 1
fi

ssh "${SSH_TARGET}" "sudo systemctl restart '${SERVICE_NAME}' && systemctl is-active '${SERVICE_NAME}' && cd '${REMOTE_CURRENT_DIR}' && for attempt in \$(seq 1 30); do if sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/fetch_runtime_health.py --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env' >/dev/null; then exit 0; fi; sleep 1; done; echo 'authenticated health check failed after service restart: ${HEALTH_URL}' >&2; exit 1"
verify_remote_web_database_runtime \
  "${STAGING_CLONE_DATABASE}" "${PGBOUNCER_WEB_POOL_EFFECTIVE_BEFORE}"

ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && for attempt in \$(seq 1 60); do if sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/fetch_runtime_health.py --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env' | env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/verify_redis_worker_health.py --expected-head '${LOCAL_GIT_SHA}' --expected-count 1 --expected-main-pid '${REDIS_WORKER_MAIN_PID}' --min-ready-sequence 3 --worker-not-before '${REDIS_WORKER_RESTART_NOT_BEFORE}' --max-age-seconds '${MAX_WORKER_AGE_SECONDS}' >/dev/null; then exit 0; fi; sleep 2; done; echo 'dedicated Redis worker failed strict readiness after restart: ${HEALTH_URL}' >&2; exit 1"

# Worker restart is mandatory for deployment acceptance.  Cloud capacity is a
# reviewed 16-service systemd fleet (one interactive + fifteen batch). Never
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
then exit 0; fi; sleep 2; done; echo 'exact 16-service worker fleet failed readiness after restart: ${HEALTH_URL}' >&2; exit 1"

# A healthy 16-lane receipt is not a complete fleet proof if a legacy flat
# scheduler/web/worker or its PID marker is still alive alongside systemd.
verify_remote_legacy_writers_absent

# Remote acceptance is deliberately separate from the pre-deploy local gate:
# fetch the post-restart JSON remotely, then validate it with the local, reviewed
# validator and explicit expected HEAD/migration/worker freshness parameters.
verify_deploy_candidate
assert_deploy_source_unchanged
if ! REMOTE_HEALTH_JSON="$(ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/fetch_runtime_health.py --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env'")"; then
  echo "Failed to fetch post-restart remote health JSON: ${HEALTH_URL}" >&2
  exit 1
fi
if [ -z "${REMOTE_HEALTH_JSON}" ]; then
  echo "Post-restart remote health JSON is empty." >&2
  exit 1
fi
if ! printf '%s' "${REMOTE_HEALTH_JSON}" | run_frozen_candidate_python \
  "${DEPLOY_CANDIDATE_DIR}/scripts/verify_runtime_health.py" \
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
if ! printf '%s' "${REMOTE_HEALTH_JSON}" | run_frozen_candidate_python \
  "${DEPLOY_CANDIDATE_DIR}/scripts/verify_redis_worker_health.py" \
  --expected-head "${LOCAL_GIT_SHA}" \
  --expected-count 1 \
  --expected-main-pid "${REDIS_WORKER_MAIN_PID}" \
  --min-ready-sequence 3 \
  --worker-not-before "${REDIS_WORKER_RESTART_NOT_BEFORE}" \
  --max-age-seconds "${MAX_WORKER_AGE_SECONDS}" >/dev/null; then
  echo "Post-restart Redis worker trust validation failed; deployment is not accepted." >&2
  exit 1
fi
APIFY_WORKER_PROCESS_BINDING_JSON=""
if ! APIFY_WORKER_PROCESS_BINDING_JSON="$(
  printf '%s' "${REMOTE_HEALTH_JSON}" | ssh "${SSH_TARGET}" \
    "sudo -n env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B '${REMOTE_CURRENT_DIR}/scripts/ops/verify_apify_worker_process_binding.py' --health-json - --current-release '${REMOTE_CURRENT_DIR}' --expected-head '${LOCAL_GIT_SHA}'"
)"; then
  echo "Post-restart Apify worker process binding failed; deployment is not accepted." >&2
  exit 1
fi
if [ -z "${APIFY_WORKER_PROCESS_BINDING_JSON}" ]; then
  echo "Post-restart Apify worker process binding produced no evidence." >&2
  exit 1
fi
verify_deploy_candidate
assert_deploy_source_unchanged
if [ "${DATABASE_RELEASE_STRATEGY}" = "staging-clone" ] \
  || [ "${DATABASE_RELEASE_STRATEGY}" = "reuse-active-clone" ]; then
  POST_RESTART_DB_STATE="$(ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_CURRENT_DIR}/scripts/ops/staging_db_clone.py' prove-active-source --root '${REMOTE_ROOT}' --expected-db '${STAGING_CLONE_DATABASE}' ${DATABASE_ENV_ASSERT_RUNTIME_POOL_FLAG}")"
  read -r POST_RESTART_DATABASE POST_RESTART_ENV_SHA256 POST_RESTART_DB_OWNER POST_RESTART_ACTIVE_RELEASE < <(printf '%s' "${POST_RESTART_DB_STATE}" | run_local_python_program 'import json,os; p=json.load(os.fdopen(3)); print(p["database_name"], p["env_sha256"], p["database_owner_release_id"], p["active_release_id"])')
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
# The strict validator above has already proven 16 unique, fresh nonce hashes;
# hash their sorted set into one stable deployment-scoped fleet identity.
WORKER_BOOT_NONCE_SHA256="$(printf '%s' "${REMOTE_HEALTH_JSON}" | run_local_python_program 'import hashlib,json,os; data=json.load(os.fdopen(3)); rows=(data.get("trust",{}).get("worker_fleet",{}).get("workers") or []); nonces=sorted(str(row.get("boot_nonce_sha256") or "") for row in rows if row.get("online") is True); print(hashlib.sha256(("\n".join(nonces)).encode()).hexdigest())')"
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
LOCAL_BROWSER_FAILURE_LOG="$(
  mktemp "${POST_DEPLOY_EVIDENCE_DIR}/browser-capture-failure.log.XXXXXX"
)"
LOCAL_APIFY_WORKER_PROCESS_BINDING="${POST_DEPLOY_EVIDENCE_DIR}/apify-worker-process-binding.json"
REMOTE_LOG_BASELINE="/tmp/vkpi-runtime-log-baseline-${WORKER_BOOT_NONCE_SHA256}.json"
REMOTE_ACCEPTANCE_REPORT="/tmp/vkpi-release-acceptance-${WORKER_BOOT_NONCE_SHA256}.json"
printf '%s\n' "${APIFY_WORKER_PROCESS_BINDING_JSON}" \
  >"${LOCAL_APIFY_WORKER_PROCESS_BINDING}"
APIFY_WORKER_PROCESS_BINDING_JSON=""
chmod 600 "${LOCAL_APIFY_WORKER_PROCESS_BINDING}"

# The reviewed systemd units write to journald, not the legacy runtime/logs
# files.  Bind a cursor to the exact web + 16 Apify + Redis unit filter.
ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/audit_systemd_journal_media_log_leaks.py ${JOURNAL_SYSTEMD_UNIT_FLAGS} --worker-boot-nonce-sha256 '${WORKER_BOOT_NONCE_SHA256}' --worker-not-before '${WORKER_RESTART_NOT_BEFORE}' --compact > '${REMOTE_LOG_BASELINE}' && chmod 600 '${REMOTE_LOG_BASELINE}'"
ssh "${SSH_TARGET}" "cat -- '${REMOTE_LOG_BASELINE}'" >"${LOCAL_LOG_BASELINE}"

# Repeat the complete manifest-driven read-only API acceptance against the
# restarted remote service; a local pre-deploy 41/41+ receipt is not transferable.
REMOTE_ACCEPTANCE_RC=0
ssh "${SSH_TARGET}" "rm -f -- '${REMOTE_ACCEPTANCE_REPORT}' && cd '${REMOTE_CURRENT_DIR}' && sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i HOME=/tmp/vkpi-acceptance-home TMPDIR=/tmp ENVIRONMENT=production V2_PRODUCTION_MODE=1 APP_ROLE=admin-web DB_RUNTIME_BACKEND=postgres LOCAL_RUNTIME_FORCE_STACK=0 LOCAL_ENV_FILE='${REMOTE_ROOT}/.env' RUNTIME_ROOT='${REMOTE_ROOT}/runtime' PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/local_release_acceptance.py --base-url '${REMOTE_ACCEPTANCE_BASE_URL}' --json-out '${REMOTE_ACCEPTANCE_REPORT}' --token-ttl 1200 --overall-timeout 1170 >/dev/null" || REMOTE_ACCEPTANCE_RC=$?
LOCAL_ACCEPTANCE_REPORT_TMP="$(mktemp "${POST_DEPLOY_EVIDENCE_DIR}/.release-acceptance.XXXXXX")"
if ! ssh "${SSH_TARGET}" "cat -- '${REMOTE_ACCEPTANCE_REPORT}'" >"${LOCAL_ACCEPTANCE_REPORT_TMP}"; then
  rm -f -- "${LOCAL_ACCEPTANCE_REPORT_TMP}"
  echo "Remote acceptance report could not be retained locally; deployment is not accepted." >&2
  exit 1
fi
chmod 600 "${LOCAL_ACCEPTANCE_REPORT_TMP}"
mv -- "${LOCAL_ACCEPTANCE_REPORT_TMP}" "${LOCAL_ACCEPTANCE_REPORT}"
if [ "${REMOTE_ACCEPTANCE_RC}" -ne 0 ]; then
  echo "Remote acceptance failed with exit ${REMOTE_ACCEPTANCE_RC}; report retained at ${LOCAL_ACCEPTANCE_REPORT}." >&2
  exit 1
fi
"${LOCAL_SAFE_PYTHON}" - "${LOCAL_ACCEPTANCE_REPORT}" <<'PY'
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

# Only after strict post-restart web + worker identity and read-only acceptance
# have passed, mint one 60-1200s admin JWT inside the active remote release. The
# production JWT secret and DB identity never leave the host: SSH stdout carries
# only the short-lived token directly into this shell variable, never argv,
# evidence, logs, or a file.  Any lookup/signing failure blocks the deployment.
if ! POST_DEPLOY_BROWSER_TOKEN="$(ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i HOME=/tmp XDG_CACHE_HOME=/tmp TMPDIR=/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 LOG_LEVEL=CRITICAL ENVIRONMENT=production V2_PRODUCTION_MODE=1 APP_ROLE=admin-web '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/mint_browser_gate_token.py --ttl-seconds '${BROWSER_GATE_TOKEN_TTL_SECONDS}'")"; then
  echo "Remote short-lived browser gate token mint failed; deployment is not accepted." >&2
  exit 1
fi
if ! [[ "${POST_DEPLOY_BROWSER_TOKEN}" =~ ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]]; then
  echo "Browser gate token is not a single compact JWT; deployment is not accepted." >&2
  POST_DEPLOY_BROWSER_TOKEN=""
  exit 1
fi

# Use an owned, clean, extension-free Chrome process.  The controller consumes
# the token from its environment, strips it from Chrome, and virtualizes the
# frontend token lookup in renderer memory without persistent browser storage.
verify_deploy_candidate
assert_deploy_source_unchanged
BROWSER_CAPTURE_STATUS=0
chmod 600 "${LOCAL_BROWSER_FAILURE_LOG}"
env -i \
  PATH="${BROWSER_GATE_CONTROLLER_PATH}" \
  HOME="${BROWSER_GATE_OS_HOME}" \
  USER="${BROWSER_GATE_OS_USER}" \
  LOGNAME="${BROWSER_GATE_OS_USER}" \
  XDG_CACHE_HOME=/tmp \
  TMPDIR=/tmp \
  LANG=C.UTF-8 \
  VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS="${BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS}" \
  VKPI_BROWSER_GATE_TOKEN="${POST_DEPLOY_BROWSER_TOKEN}" \
  node \
  "${DEPLOY_CANDIDATE_DIR}/scripts/capture_browser_console_cdp.mjs" \
  --url "${POST_DEPLOY_BROWSER_URL}" \
  --output "${LOCAL_BROWSER_CAPTURE}" \
  --settle-ms "${BROWSER_GATE_SETTLE_MS}" \
  --page-settle-ms "${BROWSER_GATE_PAGE_SETTLE_MS}" \
  --page-timeout-ms "${BROWSER_GATE_PAGE_TIMEOUT_MS}" \
  --overall-timeout-ms "${BROWSER_GATE_OVERALL_TIMEOUT_MS}" \
  --chrome "${POST_DEPLOY_CHROME_PATH}" 2>"${LOCAL_BROWSER_FAILURE_LOG}" || BROWSER_CAPTURE_STATUS=$?
POST_DEPLOY_BROWSER_TOKEN=""
if [ "${BROWSER_CAPTURE_STATUS}" -ne 0 ]; then
  echo "Post-restart authenticated browser capture failed; stage evidence retained at ${LOCAL_BROWSER_FAILURE_LOG}." >&2
  exit "${BROWSER_CAPTURE_STATUS}"
fi
rm -f -- "${LOCAL_BROWSER_FAILURE_LOG}"
run_frozen_candidate_python \
  "${DEPLOY_CANDIDATE_DIR}/scripts/verify_browser_console_capture.py" \
  --input "${LOCAL_BROWSER_CAPTURE}" \
  --json-out "${LOCAL_BROWSER_REPORT}" \
  --expected-git-sha "${BROWSER_EXPECTED_GIT_SHA}" \
  --expected-app-asset "${BROWSER_EXPECTED_APP_ASSET}" \
  --expected-app-asset-sha256 "${BROWSER_EXPECTED_APP_ASSET_SHA256}"
verify_deploy_candidate
assert_deploy_source_unchanged

# Scan only bytes appended after the bound baseline.  Missing baseline coverage,
# truncation, unread tails, or any new sensitive URL/credential finding fails the
# deployment.  The standalone validator re-reads both receipts instead of
# trusting the scanner exit code alone.
verify_deploy_candidate
assert_deploy_source_unchanged
if ! ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/audit_systemd_journal_media_log_leaks.py ${JOURNAL_SYSTEMD_UNIT_FLAGS} --baseline-state '${REMOTE_LOG_BASELINE}' --worker-boot-nonce-sha256 '${WORKER_BOOT_NONCE_SHA256}' --worker-not-before '${WORKER_RESTART_NOT_BEFORE}' --require-complete-baseline --compact --fail-on-new" >"${LOCAL_LOG_CANARY}"; then
  echo "Post-restart remote runtime log canary failed." >&2
  exit 1
fi
run_frozen_candidate_python \
  "${DEPLOY_CANDIDATE_DIR}/scripts/verify_runtime_journal_canary.py" \
  --baseline-state "${LOCAL_LOG_BASELINE}" \
  --canary-report "${LOCAL_LOG_CANARY}" \
  --expected-worker-boot-nonce-sha256 "${WORKER_BOOT_NONCE_SHA256}" \
  --worker-not-before "${WORKER_RESTART_NOT_BEFORE}" \
  ${JOURNAL_SYSTEMD_UNIT_FLAGS}
verify_deploy_candidate
assert_deploy_source_unchanged
chmod 600 "${LOCAL_LOG_BASELINE}" "${LOCAL_LOG_CANARY}" \
  "${LOCAL_ACCEPTANCE_REPORT}" "${LOCAL_BROWSER_CAPTURE}" "${LOCAL_BROWSER_REPORT}" \
  "${LOCAL_APIFY_WORKER_PROCESS_BINDING}"

LOCAL_ASSET="${BROWSER_EXPECTED_APP_ASSET}"
REMOTE_ASSET="$(ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && grep -o 'app-[A-Za-z0-9_-]*\\.js' frontend/dist/index.html | head -1")"

echo "local_asset=${LOCAL_ASSET}"
echo "remote_asset=${REMOTE_ASSET}"
test "${LOCAL_ASSET}" = "${REMOTE_ASSET}"

# Private-surface gate. The default contract requires the safe noindex shell;
# Access mode instead requires anonymous interception at both HTML and asset paths.
PRIVATE_SURFACE_URLS="${PRIVATE_SURFACE_URLS:-https://viltroxtest.com https://www.viltroxtest.com}"
read -r -a PRIVATE_SURFACE_URL_LIST <<< "${PRIVATE_SURFACE_URLS}"
verify_deploy_candidate
assert_deploy_source_unchanged
if [ "${VKPI_EXPECT_ACCESS_GATED}" = "1" ]; then
  run_frozen_candidate_python "${DEPLOY_CANDIDATE_DIR}/scripts/verify_private_surface_live.py" \
    --expect-access-gated "${PRIVATE_SURFACE_URL_LIST[@]}"
else
  run_frozen_candidate_python "${DEPLOY_CANDIDATE_DIR}/scripts/verify_private_surface_live.py" \
    "${PRIVATE_SURFACE_URL_LIST[@]}"
fi
verify_deploy_candidate
assert_deploy_source_unchanged

if [ "${STAGING_DB_CLONE_MODE}" = "1" ]; then
  verify_remote_pgbouncer_database_map
  if [ "${PGBOUNCER_SERVICE_ACTIVE_STATE}" = "active" ]; then
    probe_remote_pgbouncer_database "${STAGING_SOURCE_DATABASE}"
    probe_remote_pgbouncer_database "${STAGING_CLONE_DATABASE}"
  fi
  verify_remote_web_database_runtime \
    "${STAGING_CLONE_DATABASE}" "${PGBOUNCER_WEB_POOL_EFFECTIVE_BEFORE}"
fi
ssh "${SSH_TARGET}" "set -eu; current_path=\$(readlink -f -- '${REMOTE_CURRENT_DIR}'); previous_path=\$(readlink -f -- '${REMOTE_ROOT}/previous'); [ \"\${current_path}\" = '${REMOTE_RELEASE_DIR}' ] || { echo 'post-deploy current pointer does not name the accepted release' >&2; exit 1; }; case \"\${previous_path}\" in '${REMOTE_RELEASES_DIR}'/*) ;; *) echo 'post-deploy previous pointer escapes releases' >&2; exit 1;; esac; if [ -n '${EXPECTED_PREVIOUS_RELEASE_DIR}' ] && [ \"\${previous_path}\" != '${EXPECTED_PREVIOUS_RELEASE_DIR}' ]; then echo 'post-deploy previous pointer does not name the rescue rollback anchor' >&2; exit 1; fi; current_id=\${current_path##*/}; previous_id=\${previous_path##*/}; sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_CURRENT_DIR}/scripts/ops/atomic_release_layout.py' verify-seal --root '${REMOTE_ROOT}' --release-id \"\${current_id}\" --expected-owner-uid 0 --expected-owner-gid 0 && sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_CURRENT_DIR}/scripts/ops/atomic_release_layout.py' verify-seal --root '${REMOTE_ROOT}' --release-id \"\${previous_id}\" --expected-owner-uid 0 --expected-owner-gid 0"
if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" = "1" ]; then
  ssh "${SSH_TARGET}" "sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B '${REMOTE_CURRENT_DIR}/scripts/ops/verify_legacy_bootstrap_anchor.py' write-success-marker --marker '${FIRST_ATOMIC_BOOTSTRAP_SUCCESS_MARKER}' --plan-sha256 '${FIRST_ATOMIC_BOOTSTRAP_PLAN_SHA256}' --release-id '${RELEASE_ID}' --git-sha '${LOCAL_GIT_SHA}' >/dev/null"
fi
verify_remote_legacy_writers_absent
verify_remote_release_validation_fence active
if ! FENCED_REMOTE_HEALTH="$(ssh "${SSH_TARGET}" "cd '${REMOTE_CURRENT_DIR}' && sudo -n -u viltrox -g viltrox env PYTHONDONTWRITEBYTECODE=1 '${REMOTE_ROOT}/.venv/bin/python' -B scripts/ops/fetch_runtime_health.py --url '${HEALTH_URL}' --env-file '${REMOTE_ROOT}/.env'")" \
  || ! printf '%s' "${FENCED_REMOTE_HEALTH}" | run_local_python_program 'import json,os
p=json.load(os.fdopen(3))
f=(p.get("trust") or {}).get("release_validation") or {}
assert f.get("active") is True
assert f.get("valid") is True
assert f.get("source")=="verified_marker"'; then
  echo "The active runtime did not prove the release-validation fence." >&2
  exit 1
fi
verify_remote_release_drain fenced
if [ "${FENCED_RELEASE_DRAIN_VERIFIED}" != "1" ]; then
  echo "Refusing activation without an empty post-validation provider boundary." >&2
  exit 1
fi
verify_deploy_candidate
assert_deploy_source_unchanged
# From this point the release is an irreversible roll-forward commit: removing
# the marker can let existing processes claim external/billed work immediately.
# Any later failure must preserve the accepted app/database identity rather than
# pretend those provider side effects can be rolled back.
RELEASE_VALIDATION_COMMIT_STARTED=1
remove_remote_release_validation_fence
restore_remote_sync_unit_state
verify_remote_legacy_writers_absent
verify_deploy_candidate
assert_deploy_source_unchanged
DEPLOY_ACCEPTED=1
echo "[deploy] retained post-restart evidence: ${POST_DEPLOY_EVIDENCE_DIR}"
