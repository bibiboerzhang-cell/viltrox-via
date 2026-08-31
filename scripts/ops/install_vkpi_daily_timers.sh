#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
REMOTE_SERVICE="${REMOTE_SERVICE:-vkpi-sync-daily.service}"
REMOTE_TIMER="${REMOTE_TIMER:-vkpi-sync-daily.timer}"
REMOTE_DEADMAN_SERVICE="${REMOTE_DEADMAN_SERVICE:-vkpi-sync-deadman.service}"
REMOTE_DEADMAN_TIMER="${REMOTE_DEADMAN_TIMER:-vkpi-sync-deadman.timer}"
REMOTE_QUALIFIED_KOL_SERVICE="${REMOTE_QUALIFIED_KOL_SERVICE:-vkpi-qualified-kol-refresh.service}"
REMOTE_QUALIFIED_KOL_TIMER="${REMOTE_QUALIFIED_KOL_TIMER:-vkpi-qualified-kol-refresh.timer}"
ENABLE_QUALIFIED_KOL_TIMER="${ENABLE_QUALIFIED_KOL_TIMER:-0}"
QUALIFIED_KOL_LIMIT="${QUALIFIED_KOL_LIMIT:-200}"
QUALIFIED_KOL_STALE_DAYS="${QUALIFIED_KOL_STALE_DAYS:-1}"
LOCAL_AGENT_ID="${LOCAL_AGENT_ID:-com.viltrox.prod-snapshot-sync}"
LOCAL_AGENT_PATH="${HOME}/Library/LaunchAgents/${LOCAL_AGENT_ID}.plist"

readonly ALERT_SERVICE="vkpi-sync-daily-alert@.service"
readonly EXPECTED_REMOTE_SERVICE="vkpi-sync-daily.service"
readonly EXPECTED_REMOTE_TIMER="vkpi-sync-daily.timer"
readonly EXPECTED_DEADMAN_SERVICE="vkpi-sync-deadman.service"
readonly EXPECTED_DEADMAN_TIMER="vkpi-sync-deadman.timer"
readonly EXPECTED_QUALIFIED_SERVICE="vkpi-qualified-kol-refresh.service"
readonly EXPECTED_QUALIFIED_TIMER="vkpi-qualified-kol-refresh.timer"

LOCAL_STAGE=""
REMOTE_STAGE=""

die() {
  printf 'install_vkpi_daily_timers: %s\n' "$*" >&2
  exit 64
}

require_exact() {
  local name="$1"
  local actual="$2"
  local expected="$3"
  [ "${actual}" = "${expected}" ] || die "${name} must be exactly ${expected}"
}

validate_configuration() {
  [[ "${SSH_TARGET}" =~ ^([A-Za-z0-9][A-Za-z0-9._-]*@)?[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || die "SSH_TARGET contains unsupported characters"
  [[ "${REMOTE_ROOT}" =~ ^/([A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$ ]] \
    || die "REMOTE_ROOT must be a normalized absolute path"
  case "/${REMOTE_ROOT#/}/" in
    *"/./"*|*"/../"*|*"//"*) die "REMOTE_ROOT must not contain traversal or empty segments" ;;
  esac

  # Unit targets are an allowlist, not caller-selected paths. This prevents an
  # environment override from turning the root installer into an arbitrary
  # /etc/systemd/system file writer.
  require_exact REMOTE_SERVICE "${REMOTE_SERVICE}" "${EXPECTED_REMOTE_SERVICE}"
  require_exact REMOTE_TIMER "${REMOTE_TIMER}" "${EXPECTED_REMOTE_TIMER}"
  require_exact REMOTE_DEADMAN_SERVICE "${REMOTE_DEADMAN_SERVICE}" "${EXPECTED_DEADMAN_SERVICE}"
  require_exact REMOTE_DEADMAN_TIMER "${REMOTE_DEADMAN_TIMER}" "${EXPECTED_DEADMAN_TIMER}"
  require_exact REMOTE_QUALIFIED_KOL_SERVICE "${REMOTE_QUALIFIED_KOL_SERVICE}" "${EXPECTED_QUALIFIED_SERVICE}"
  require_exact REMOTE_QUALIFIED_KOL_TIMER "${REMOTE_QUALIFIED_KOL_TIMER}" "${EXPECTED_QUALIFIED_TIMER}"

  [[ "${ENABLE_QUALIFIED_KOL_TIMER}" =~ ^[01]$ ]] \
    || die "ENABLE_QUALIFIED_KOL_TIMER must be 0 or 1"
  [[ "${QUALIFIED_KOL_LIMIT}" =~ ^(0|[1-9][0-9]*)$ ]] \
    || die "QUALIFIED_KOL_LIMIT must be a non-negative integer"
  [[ "${QUALIFIED_KOL_STALE_DAYS}" =~ ^(0|[1-9][0-9]*)$ ]] \
    || die "QUALIFIED_KOL_STALE_DAYS must be a non-negative integer"
  (( QUALIFIED_KOL_LIMIT >= 1 && QUALIFIED_KOL_LIMIT <= 1000 )) \
    || die "QUALIFIED_KOL_LIMIT must be between 1 and 1000"
  (( QUALIFIED_KOL_STALE_DAYS <= 365 )) \
    || die "QUALIFIED_KOL_STALE_DAYS must be between 0 and 365"
  [[ "${LOCAL_AGENT_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || die "LOCAL_AGENT_ID contains unsupported characters"
}

cleanup_staging() {
  local rc=$?
  trap - EXIT HUP INT TERM
  set +e
  if [[ -n "${LOCAL_STAGE}" && "${LOCAL_STAGE}" == /tmp/vkpi-systemd-units.* ]]; then
    rm -rf -- "${LOCAL_STAGE}"
  fi
  if [[ -n "${REMOTE_STAGE}" && "${REMOTE_STAGE}" =~ ^/tmp/vkpi-systemd-install\.[A-Za-z0-9]+$ ]]; then
    ssh "${SSH_TARGET}" bash -s -- "${REMOTE_STAGE}" <<'REMOTE_CLEANUP' >/dev/null 2>&1
set -euo pipefail
stage="$1"
[[ "${stage}" =~ ^/tmp/vkpi-systemd-install\.[A-Za-z0-9]+$ ]]
rm -rf -- "${stage}"
REMOTE_CLEANUP
  fi
  exit "${rc}"
}

trap cleanup_staging EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

new_local_stage() {
  [ -z "${LOCAL_STAGE}" ] || die "local staging directory already exists"
  LOCAL_STAGE="$(mktemp -d /tmp/vkpi-systemd-units.XXXXXX)"
  [[ "${LOCAL_STAGE}" =~ ^/tmp/vkpi-systemd-units\.[A-Za-z0-9]+$ ]] \
    || die "mktemp returned an unexpected local staging path"
  chmod 0700 "${LOCAL_STAGE}"
}

stage_remote_files() {
  local -a units=("$@")
  local -a local_paths=()
  local unit

  REMOTE_STAGE="$(ssh "${SSH_TARGET}" 'umask 077; mktemp -d /tmp/vkpi-systemd-install.XXXXXX')"
  [[ "${REMOTE_STAGE}" =~ ^/tmp/vkpi-systemd-install\.[A-Za-z0-9]+$ ]] \
    || die "remote mktemp returned an unexpected staging path"
  for unit in "${units[@]}"; do
    [ -f "${LOCAL_STAGE}/${unit}" ] || die "missing staged unit ${unit}"
    local_paths+=("${LOCAL_STAGE}/${unit}")
  done
  scp -q -- "${local_paths[@]}" "${SSH_TARGET}:${REMOTE_STAGE}/"
}

run_remote_transaction() {
  local mode="$1"

  ssh "${SSH_TARGET}" bash -s -- \
    "${REMOTE_STAGE}" "${REMOTE_ROOT}" "${mode}" "${ENABLE_QUALIFIED_KOL_TIMER}" <<'REMOTE_TRANSACTION'
set -Eeuo pipefail

stage="$1"
remote_root="$2"
mode="$3"
enable_qualified="$4"
systemd_dir="/etc/systemd/system"
log_dir="/var/log/vkpi"

fail() {
  printf 'vkpi systemd transaction: %s\n' "$*" >&2
  exit 65
}

[[ "${stage}" =~ ^/tmp/vkpi-systemd-install\.[A-Za-z0-9]+$ ]] \
  || fail "untrusted staging path"
[[ "${remote_root}" =~ ^/([A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$ ]] \
  || fail "untrusted remote root"
case "/${remote_root#/}/" in
  *"/./"*|*"/../"*|*"//"*) fail "remote root is not normalized" ;;
esac
[[ "$(id -u)" = "0" ]] || fail "remote transaction requires root"
[[ -d "${stage}" && ! -L "${stage}" ]] || fail "staging path is not a real directory"
[[ "$(realpath -e -- "${stage}")" = "${stage}" ]] || fail "staging path changed identity"
[[ "$(realpath -e -- "${remote_root}")" = "${remote_root}" ]] \
  || fail "REMOTE_ROOT must exist and must not be a symlink"
[[ "$(stat -c %u -- "${remote_root}")" = "0" ]] \
  || fail "REMOTE_ROOT must be root-owned"
remote_root_mode="$(stat -c %a -- "${remote_root}")"
(( (8#${remote_root_mode} & 022) == 0 )) \
  || fail "REMOTE_ROOT must not be group/world writable"
[[ "${enable_qualified}" =~ ^[01]$ ]] || fail "invalid qualified timer policy"
command -v systemd-analyze >/dev/null || fail "systemd-analyze is unavailable"
command -v systemctl >/dev/null || fail "systemctl is unavailable"
command -v runuser >/dev/null || fail "runuser is unavailable"
command -v flock >/dev/null || fail "flock is unavailable"
[[ -x /usr/bin/python3 ]] || fail "/usr/bin/python3 is unavailable"
id -u viltrox >/dev/null 2>&1 || fail "service user viltrox is missing"
getent group viltrox >/dev/null 2>&1 || fail "service group viltrox is missing"

exec 9>/run/lock/vkpi-systemd-install.lock
flock -n 9 || fail "another V-KPI unit installation is active"

case "${mode}" in
  primary)
    units=(
      vkpi-sync-daily.service
      vkpi-sync-daily.timer
      vkpi-sync-daily-alert@.service
      vkpi-sync-deadman.service
      vkpi-sync-deadman.timer
    )
    timers=(vkpi-sync-daily.timer vkpi-sync-deadman.timer)
    policy_disable_timers=(vkpi-qualified-kol-refresh.timer)
    state_timers=(
      vkpi-sync-daily.timer
      vkpi-sync-deadman.timer
      vkpi-qualified-kol-refresh.timer
    )
    trigger_services=(
      vkpi-sync-daily.service
      vkpi-sync-deadman.service
      vkpi-qualified-kol-refresh.service
    )
    ;;
  qualified)
    units=(vkpi-qualified-kol-refresh.service vkpi-qualified-kol-refresh.timer)
    timers=(vkpi-qualified-kol-refresh.timer)
    policy_disable_timers=()
    state_timers=(vkpi-qualified-kol-refresh.timer)
    trigger_services=(vkpi-qualified-kol-refresh.service)
    ;;
  *) fail "unsupported transaction mode" ;;
esac

# Verify that the private staging directory contains exactly the allowlisted
# regular files. Symlinks, hardlinks, device nodes, and extra files fail closed.
shopt -s nullglob dotglob
stage_entries=("${stage}"/*)
[[ "${#stage_entries[@]}" -eq "${#units[@]}" ]] \
  || fail "staging directory has missing or extra entries"
verify_paths=()
for unit in "${units[@]}"; do
  path="${stage}/${unit}"
  [[ -f "${path}" && ! -L "${path}" ]] || fail "${unit} is not a regular staged file"
  [[ "$(stat -c %h -- "${path}")" = "1" ]] || fail "${unit} has multiple hard links"
  [[ "$(stat -c %u -- "${path}")" = "0" ]] || fail "${unit} is not root-owned"
  chmod 0600 -- "${path}"
  verify_paths+=("${path}")
done

# PID 1 reads EnvironmentFile as root; it must remain a regular, root-readable,
# non-writable-by-group/other secret file. The dropped-privilege service itself
# must be able to traverse/read/execute the release and virtualenv paths.
env_file="${remote_root}/.env"
[[ -f "${env_file}" && ! -L "${env_file}" && -r "${env_file}" ]] \
  || fail "${env_file} must be a root-readable regular file"
[[ "$(stat -c %u -- "${env_file}")" = "0" ]] \
  || fail "${env_file} must be root-owned"
[[ "$(stat -c %h -- "${env_file}")" = "1" ]] \
  || fail "${env_file} must have exactly one hard link"
env_mode="$(stat -c %a -- "${env_file}")"
(( (8#${env_mode} & 022) == 0 )) || fail "${env_file} must not be group/world writable"
runuser -u viltrox -- test -r "${remote_root}/current" \
  || fail "viltrox cannot read current release"
runuser -u viltrox -- test -x "${remote_root}/current" \
  || fail "viltrox cannot traverse current release"
runuser -u viltrox -- test -r "${remote_root}/current/scripts/cron_daily_sync.py" \
  || fail "viltrox cannot read cron_daily_sync.py"
runuser -u viltrox -- test -x "${remote_root}/.venv/bin/python" \
  || fail "viltrox cannot execute virtualenv Python"

stage_hashes_before="$(sha256sum -- "${verify_paths[@]}")"
systemd-analyze verify "${verify_paths[@]}"
stage_hashes_after="$(sha256sum -- "${verify_paths[@]}")"
[[ "${stage_hashes_after}" = "${stage_hashes_before}" ]] \
  || fail "staged unit content changed during verification"

# Rollback evidence must not live below app-writable runtime/. Keep the entire
# authority chain root-owned and non-writable by group/other.
backup_parent="/var/lib/vkpi/systemd-unit-backups"
for trusted_parent in /var /var/lib; do
  [[ -d "${trusted_parent}" && ! -L "${trusted_parent}" ]] \
    || fail "backup authority is not a real directory: ${trusted_parent}"
  [[ "$(stat -c %u -- "${trusted_parent}")" = "0" ]] \
    || fail "backup authority is not root-owned: ${trusted_parent}"
  trusted_mode="$(stat -c %a -- "${trusted_parent}")"
  (( (8#${trusted_mode} & 022) == 0 )) \
    || fail "backup authority is group/world writable: ${trusted_parent}"
done
for trusted_parent in /var/lib/vkpi "${backup_parent}"; do
  if [[ -e "${trusted_parent}" || -L "${trusted_parent}" ]]; then
    [[ -d "${trusted_parent}" && ! -L "${trusted_parent}" ]] \
      || fail "backup authority is not a real directory: ${trusted_parent}"
  else
    install -d -o root -g root -m 0700 "${trusted_parent}"
  fi
  [[ "$(realpath -e -- "${trusted_parent}")" = "${trusted_parent}" ]] \
    || fail "backup authority changed identity: ${trusted_parent}"
  [[ "$(stat -c %u -- "${trusted_parent}")" = "0" ]] \
    || fail "backup authority is not root-owned: ${trusted_parent}"
  trusted_mode="$(stat -c %a -- "${trusted_parent}")"
  (( (8#${trusted_mode} & 022) == 0 )) \
    || fail "backup authority is group/world writable: ${trusted_parent}"
done
backup_dir="$(mktemp -d "${backup_parent}/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
chmod 0700 "${backup_dir}"

declare -A unit_existed=()
declare -A unit_enabled=()
declare -A unit_active=()
for unit in "${units[@]}"; do
  target="${systemd_dir}/${unit}"
  [[ ! -d "${target}" ]] || fail "unit target is a directory: ${target}"
  if [[ -e "${target}" || -L "${target}" ]]; then
    [[ -f "${target}" || -L "${target}" ]] \
      || fail "unit target is not a regular file or symlink: ${target}"
    if [[ -f "${target}" && ! -L "${target}" ]]; then
      [[ "$(stat -c %h -- "${target}")" = "1" ]] \
        || fail "unit target has multiple hard links: ${target}"
    fi
    unit_existed["${unit}"]=1
    cp -a -- "${target}" "${backup_dir}/${unit}"
  else
    unit_existed["${unit}"]=0
  fi
  printf '%s\texisted=%s\n' "${unit}" "${unit_existed[${unit}]}" \
    >> "${backup_dir}/unit-file-state.tsv"
done

state_units=("${state_timers[@]}" "${trigger_services[@]}")
for unit in "${state_units[@]}"; do
  state="$(systemctl is-enabled "${unit}" 2>/dev/null || true)"
  unit_enabled["${unit}"]="${state%%$'\n'*}"
  state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
  unit_active["${unit}"]="${state%%$'\n'*}"
  printf '%s\tenabled=%s\tactive=%s\n' \
    "${unit}" \
    "${unit_enabled[${unit}]:-unknown}" "${unit_active[${unit}]:-unknown}" \
    >> "${backup_dir}/unit-state.tsv"
done

# Never replace a unit while its trigger service is running or transitioning.
# The timer is stopped again after the rollback trap opens to close the race.
for unit in "${trigger_services[@]}"; do
  state="${unit_active[${unit}]:-unknown}"
  case "${state}" in
    inactive|failed) ;;
    *) fail "refusing to install unless ${unit} is safely inactive (state=${state})" ;;
  esac
done

log_dir_existed=0
log_dir_uid=""
log_dir_gid=""
log_dir_mode=""
log_dir_dev=""
log_dir_ino=""
for trusted_parent in /var /var/log; do
  [[ -d "${trusted_parent}" && ! -L "${trusted_parent}" ]] \
    || fail "log authority is not a real directory: ${trusted_parent}"
  [[ "$(stat -c %u -- "${trusted_parent}")" = "0" ]] \
    || fail "log authority is not root-owned: ${trusted_parent}"
  trusted_mode="$(stat -c %a -- "${trusted_parent}")"
  (( (8#${trusted_mode} & 022) == 0 )) \
    || fail "log authority is group/world writable: ${trusted_parent}"
done
if [[ -e "${log_dir}" || -L "${log_dir}" ]]; then
  [[ -d "${log_dir}" && ! -L "${log_dir}" ]] \
    || fail "${log_dir} must be a real directory"
  log_dir_existed=1
  log_dir_state="$(stat -c '%u %g %a %d %i' -- "${log_dir}")"
  read -r log_dir_uid log_dir_gid log_dir_mode log_dir_dev log_dir_ino \
    <<< "${log_dir_state}"
fi

is_managed_log_name() {
  local name="$1"
  case "${mode}" in
    primary) [[ "${name}" =~ ^sync_daily_[0-9]{8}\.log$ ]] ;;
    qualified) [[ "${name}" =~ ^qualified_kol_refresh_[0-9]{8}\.log$ || "${name}" = "qualified_kol_refresh_skip.log" ]] ;;
  esac
}

log_paths=()
log_uids=()
log_gids=()
log_modes=()
log_devs=()
log_inos=()

freeze_log_directory() {
  /usr/bin/python3 - "${log_dir}" <<'PY_FREEZE_LOG_DIRECTORY'
import os
import stat
import sys

path = sys.argv[1]
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
fd = os.open(path, flags)
try:
    before = os.fstat(fd)
    if not stat.S_ISDIR(before.st_mode):
        raise SystemExit("log path is not a directory")
    # Remove every non-root access bit before changing ownership. Holding the
    # O_NOFOLLOW directory fd prevents path substitution between operations.
    os.fchmod(fd, 0)
    os.fchown(fd, 0, 0)
    os.fchmod(fd, 0o700)
    after = os.fstat(fd)
    if after.st_dev != before.st_dev or after.st_ino != before.st_ino:
        raise SystemExit("log directory identity changed")
    if after.st_uid != 0 or after.st_gid != 0 or stat.S_IMODE(after.st_mode) != 0o700:
        raise SystemExit("log directory freeze did not take effect")
finally:
    os.close(fd)
PY_FREEZE_LOG_DIRECTORY
}

transaction_open=1
rollback_transaction() {
  local rc="$1"
  local current_state i state target unit
  local rollback_failed=0
  local log_frozen=0
  trap - EXIT HUP INT TERM
  set +e
  if [[ "${transaction_open}" = "1" ]]; then
    for unit in "${state_timers[@]}"; do
      if ! systemctl disable --now "${unit}" >/dev/null 2>&1; then
        current_state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
        case "${current_state%%$'\n'*}" in
          active|activating) rollback_failed=1 ;;
        esac
      fi
    done
    # Persistent timers can fire catch-up work during a partially successful
    # activation request. Stop only services that this transaction newly activated;
    # never interrupt a service that was already active before installation.
    for unit in "${trigger_services[@]}"; do
      state="${unit_active[${unit}]:-unknown}"
      if [[ "${state}" != "active" && "${state}" != "activating" ]]; then
        current_state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
        current_state="${current_state%%$'\n'*}"
        case "${current_state}" in
          active|activating)
            systemctl stop "${unit}" >/dev/null 2>&1 || rollback_failed=1
            ;;
        esac
      fi
    done

    if [[ -d "${log_dir}" && ! -L "${log_dir}" ]]; then
      if freeze_log_directory; then
        log_frozen=1
      else
        rollback_failed=1
      fi
    elif [[ -e "${log_dir}" || -L "${log_dir}" ]]; then
      rollback_failed=1
    fi

    for unit in "${units[@]}"; do
      target="${systemd_dir}/${unit}"
      rm -f -- "${target}" || rollback_failed=1
      if [[ "${unit_existed[${unit}]}" = "1" ]]; then
        cp -a -- "${backup_dir}/${unit}" "${target}" || rollback_failed=1
        if [[ -L "${backup_dir}/${unit}" ]]; then
          [[ -L "${target}" ]] \
            && [[ "$(readlink -- "${target}")" = "$(readlink -- "${backup_dir}/${unit}")" ]] \
            || rollback_failed=1
        else
          [[ -f "${target}" && ! -L "${target}" ]] \
            && cmp -s -- "${backup_dir}/${unit}" "${target}" \
            || rollback_failed=1
        fi
      elif [[ -e "${target}" || -L "${target}" ]]; then
        rollback_failed=1
      fi
    done
    systemctl daemon-reload || rollback_failed=1
    for unit in "${state_timers[@]}"; do
      state="${unit_enabled[${unit}]:-unknown}"
      case "${state}" in
        enabled|linked)
          systemctl enable "${unit}" >/dev/null 2>&1 || rollback_failed=1
          ;;
        enabled-runtime|linked-runtime)
          systemctl enable --runtime "${unit}" >/dev/null 2>&1 || rollback_failed=1
          ;;
      esac
      state="${unit_active[${unit}]:-unknown}"
      case "${state}" in
        active|activating)
          systemctl start "${unit}" >/dev/null 2>&1 || rollback_failed=1
          ;;
      esac

      current_state="$(systemctl is-enabled "${unit}" 2>/dev/null || true)"
      current_state="${current_state%%$'\n'*}"
      state="${unit_enabled[${unit}]:-unknown}"
      case "${state}" in
        enabled|linked)
          [[ "${current_state}" = "enabled" || "${current_state}" = "linked" ]] \
            || rollback_failed=1
          ;;
        enabled-runtime|linked-runtime)
          [[ "${current_state}" = "enabled-runtime" || "${current_state}" = "linked-runtime" ]] \
            || rollback_failed=1
          ;;
        *)
          [[ "${current_state}" != "enabled" && "${current_state}" != "linked" \
            && "${current_state}" != "enabled-runtime" && "${current_state}" != "linked-runtime" ]] \
            || rollback_failed=1
          ;;
      esac

      current_state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
      current_state="${current_state%%$'\n'*}"
      state="${unit_active[${unit}]:-unknown}"
      if [[ "${state}" = "active" || "${state}" = "activating" ]]; then
        [[ "${current_state}" = "active" || "${current_state}" = "activating" ]] \
          || rollback_failed=1
      elif [[ "${current_state}" = "active" || "${current_state}" = "activating" ]]; then
        rollback_failed=1
      fi
    done

    if [[ "${log_frozen}" = "1" ]]; then
      for ((i=0; i<${#log_paths[@]}; i++)); do
        if [[ -f "${log_paths[i]}" && ! -L "${log_paths[i]}" \
          && "$(stat -c %d -- "${log_paths[i]}")" = "${log_devs[i]}" \
          && "$(stat -c %i -- "${log_paths[i]}")" = "${log_inos[i]}" \
          && "$(stat -c %h -- "${log_paths[i]}")" = "1" ]]; then
          chown --no-dereference "${log_uids[i]}:${log_gids[i]}" "${log_paths[i]}" \
            || rollback_failed=1
          chmod "${log_modes[i]}" "${log_paths[i]}" || rollback_failed=1
          restored_log_state="$(stat -c '%u %g %a' -- "${log_paths[i]}")"
          [[ "${restored_log_state}" = "${log_uids[i]} ${log_gids[i]} ${log_modes[i]}" ]] \
            || rollback_failed=1
        else
          rollback_failed=1
        fi
      done
      if [[ "${log_dir_existed}" = "1" ]]; then
        [[ "$(stat -c %d -- "${log_dir}")" = "${log_dir_dev}" \
          && "$(stat -c %i -- "${log_dir}")" = "${log_dir_ino}" ]] \
          || rollback_failed=1
        chown --no-dereference "${log_dir_uid}:${log_dir_gid}" "${log_dir}" \
          || rollback_failed=1
        chmod "${log_dir_mode}" "${log_dir}" || rollback_failed=1
        restored_log_dir_state="$(stat -c '%u %g %a' -- "${log_dir}")"
        [[ "${restored_log_dir_state}" = "${log_dir_uid} ${log_dir_gid} ${log_dir_mode}" ]] \
          || rollback_failed=1
      elif ! rmdir -- "${log_dir}" 2>/dev/null; then
        retained_log_dir="${backup_dir}/retained-vkpi-log-dir"
        mv -- "${log_dir}" "${retained_log_dir}" || rollback_failed=1
        [[ ! -e "${log_dir}" && ! -L "${log_dir}" ]] || rollback_failed=1
      fi
    fi
    if [[ "${rollback_failed}" = "1" ]]; then
      printf 'V-KPI systemd rollback incomplete; evidence=%s original_rc=%s\n' \
        "${backup_dir}" "${rc}" >&2
      rm -rf -- "${stage}" >/dev/null 2>&1 || true
      exit 70
    fi
    printf 'rolled back V-KPI systemd transaction; evidence=%s\n' "${backup_dir}" >&2
  fi
  rm -rf -- "${stage}"
  exit "${rc}"
}

trap 'rollback_transaction "$?"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Stop every controlled timer before touching units/logs, then close the race
# by requiring all trigger services to remain inactive after the stop.
for unit in "${state_timers[@]}"; do
  if ! systemctl stop "${unit}" >/dev/null 2>&1; then
    state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
    case "${state%%$'\n'*}" in
      inactive|failed) ;;
      *) fail "could not prove ${unit} stopped before install" ;;
    esac
  fi
done
for unit in "${trigger_services[@]}"; do
  state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
  state="${state%%$'\n'*}"
  case "${state}" in
    inactive|failed) ;;
    *) fail "refusing to install unless ${unit} remains safely inactive (state=${state})" ;;
  esac
done

# Freeze the directory before scanning exact-name logs. The app user cannot
# swap a checked path before the subsequent chown/chmod or rollback metadata
# restore. The parent /var/log authority was validated root-owned above.
if [[ "${log_dir_existed}" = "1" ]]; then
  freeze_log_directory
else
  install -d -o root -g root -m 0700 /var/log/vkpi
fi

log_scan="${backup_dir}/log-scan.bin"
find -P "${log_dir}" -mindepth 1 -maxdepth 1 -print0 > "${log_scan}"
while IFS= read -r -d '' log_path; do
  log_name="${log_path##*/}"
  if is_managed_log_name "${log_name}"; then
    [[ -f "${log_path}" && ! -L "${log_path}" ]] \
      || fail "managed log is not a regular file: ${log_name}"
    log_state="$(stat -c '%u %g %a %d %i %h' -- "${log_path}")"
    read -r file_uid file_gid file_mode file_dev file_ino file_nlink <<< "${log_state}"
    [[ "${file_nlink}" = "1" ]] || fail "managed log has multiple hard links: ${log_name}"
    log_paths+=("${log_path}")
    log_uids+=("${file_uid}")
    log_gids+=("${file_gid}")
    log_modes+=("${file_mode}")
    log_devs+=("${file_dev}")
    log_inos+=("${file_ino}")
    printf '%s\tuid=%s\tgid=%s\tmode=%s\tdev=%s\tino=%s\n' \
      "${log_name}" "${file_uid}" "${file_gid}" "${file_mode}" \
      "${file_dev}" "${file_ino}" >> "${backup_dir}/log-state.tsv"
  fi
done < "${log_scan}"

# Existing exact-name logs must become appendable by the dropped-privilege
# service. No glob-based chown is used; directory freeze prevents substitution.
for ((i=0; i<${#log_paths[@]}; i++)); do
  chown --no-dereference viltrox:viltrox "${log_paths[i]}"
  chmod 0640 "${log_paths[i]}"
done

# Re-check the sealed staging bytes immediately before replacing allowlisted
# targets. rm + install avoids following an existing symlink or hardlink.
[[ "$(sha256sum -- "${verify_paths[@]}")" = "${stage_hashes_before}" ]] \
  || fail "staged unit content drifted before install"
for unit in "${units[@]}"; do
  target="${systemd_dir}/${unit}"
  rm -f -- "${target}"
  install -o root -g root -m 0644 -- "${stage}/${unit}" "${target}"
done
systemctl daemon-reload

# A concurrent/manual start after the initial guard must not race the final
# permission hand-off or unit activation.
for unit in "${trigger_services[@]}"; do
  state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
  state="${state%%$'\n'*}"
  case "${state}" in
    inactive|failed) ;;
    *) fail "refusing to activate timers unless ${unit} is safely inactive (state=${state})" ;;
  esac
done

# Hand log ownership to the non-root services only after unit installation and
# reload succeeded. Rollback freezes the directory again before path-based work.
install -d -o viltrox -g viltrox -m 0750 /var/log/vkpi

# Enablement is validated while timers remain stopped. Activation is the last
# fallible transactional operation and uses --no-block so Persistent catch-up
# work begins after the request is queued, with no later validation command
# that could spuriously roll back already-started provider work. Deadman is
# queued before the provider-bearing daily timer to minimize partial-start risk.
rm -rf -- "${stage}"
printf 'prepared V-KPI systemd transaction; backup=%s\n' "${backup_dir}"

require_timer_disabled() {
  local active_state enabled_state unit="$1"
  systemctl disable --now "${unit}" >/dev/null 2>&1 || true
  enabled_state="$(systemctl is-enabled "${unit}" 2>/dev/null || true)"
  enabled_state="${enabled_state%%$'\n'*}"
  active_state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
  active_state="${active_state%%$'\n'*}"
  [[ -n "${enabled_state}" ]] || fail "could not verify disabled state for ${unit}"
  [[ "${enabled_state}" != "enabled" && "${enabled_state}" != "enabled-runtime" ]] \
    || fail "${unit} remained enabled despite default-disabled policy"
  [[ "${active_state}" = "inactive" || "${active_state}" = "failed" ]] \
    || fail "${unit} remained active or unverifiable (state=${active_state})"
}

if [[ "${mode}" = "primary" ]]; then
  for unit in "${policy_disable_timers[@]}"; do
    require_timer_disabled "${unit}"
  done
  systemctl enable vkpi-sync-deadman.timer vkpi-sync-daily.timer
  systemctl is-enabled --quiet vkpi-sync-deadman.timer
  systemctl is-enabled --quiet vkpi-sync-daily.timer
  systemctl start --no-block vkpi-sync-deadman.timer vkpi-sync-daily.timer
  activation_timers=(vkpi-sync-deadman.timer vkpi-sync-daily.timer)
elif [[ "${enable_qualified}" = "1" ]]; then
  systemctl enable vkpi-qualified-kol-refresh.timer
  systemctl is-enabled --quiet vkpi-qualified-kol-refresh.timer
  systemctl start --no-block vkpi-qualified-kol-refresh.timer
  activation_timers=(vkpi-qualified-kol-refresh.timer)
else
  activation_timers=()
  require_timer_disabled vkpi-qualified-kol-refresh.timer
fi

transaction_open=0
trap - EXIT HUP INT TERM

# --no-block proves queue admission, not activation. After the file/state
# transaction is committed, wait a bounded interval for each timer to become
# active. A failure here is reported distinctly and never mislabelled as a
# rollback: installed units remain committed for operator remediation.
activation_failed=0
for unit in "${activation_timers[@]}"; do
  active=0
  for _attempt in {1..40}; do
    if systemctl is-active --quiet "${unit}"; then
      active=1
      break
    fi
    sleep 0.25
  done
  if [[ "${active}" = "0" ]]; then
    activation_failed=1
    systemctl show "${unit}" --no-pager \
      --property=ActiveState --property=SubState --property=Result >&2 || true
  fi
done
if [[ "${activation_failed}" = "1" ]]; then
  printf 'V-KPI units committed but timer activation failed; backup=%s\n' \
    "${backup_dir}" >&2
  exit 71
fi
printf 'installed and activated V-KPI systemd units; backup=%s\n' "${backup_dir}"
REMOTE_TRANSACTION

  REMOTE_STAGE=""
}

install_remote_timer() {
  new_local_stage

  cat > "${LOCAL_STAGE}/${REMOTE_SERVICE}" <<SERVICE
[Unit]
Description=V-KPI daily official sync
Wants=network-online.target
After=network-online.target viltrox-2.0-test.service
OnFailure=vkpi-sync-daily-alert@%n.service

[Service]
Type=oneshot
User=viltrox
Group=viltrox
UMask=0077
# Exit 75 raises OnFailure but is not auto-restarted; the next timer is the retry boundary.
RestartPreventExitStatus=75 76
WorkingDirectory=${REMOTE_ROOT}/current
EnvironmentFile=${REMOTE_ROOT}/.env
Environment=PYTHONPATH=${REMOTE_ROOT}/current/backend
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/bin/bash -lc '/usr/bin/env VKPI_SKIP_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=${REMOTE_ROOT}/current/backend ${REMOTE_ROOT}/.venv/bin/python -B scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol --include-qualified-kol --kol-tiers hot --kol-stale-days 1 --kol-max-posts 2 --kol-limit 90 --worker-count 2 --child-timeout-seconds 300 >> /var/log/vkpi/sync_daily_\$(date -u +%%Y%%m%%d).log 2>&1'
# Legacy KOL Pool refresh is intentionally excluded until P1.X.A tier selection replaces full-pool daily refresh.
# TODO: Consider lowering to 2h after official-only runtime is observed for one week.
TimeoutStartSec=6h
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
SERVICE

  cat > "${LOCAL_STAGE}/${ALERT_SERVICE}" <<ALERTSERVICE
[Unit]
Description=Deliver V-KPI scheduled sync failure alert for %i
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=viltrox
Group=viltrox
UMask=0077
WorkingDirectory=${REMOTE_ROOT}/current
EnvironmentFile=${REMOTE_ROOT}/.env
ExecStart=/usr/bin/env VKPI_SKIP_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=${REMOTE_ROOT}/current/backend ${REMOTE_ROOT}/.venv/bin/python -B scripts/ops/vkpi_sync_watchdog.py unit-failure --unit %i
TimeoutStartSec=30s
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ALERTSERVICE

  cat > "${LOCAL_STAGE}/${REMOTE_TIMER}" <<TIMER
[Unit]
Description=Run V-KPI daily official sync at 04:00 UTC

[Timer]
OnCalendar=*-*-* 04:00:00 UTC
Persistent=true
RandomizedDelaySec=300
Unit=${REMOTE_SERVICE}

[Install]
WantedBy=timers.target
TIMER

  cat > "${LOCAL_STAGE}/${REMOTE_DEADMAN_SERVICE}" <<DEADMANSERVICE
[Unit]
Description=Require strict V-KPI daily sync post-run acceptance
Wants=network-online.target
After=network-online.target ${REMOTE_SERVICE}

[Service]
Type=oneshot
User=viltrox
Group=viltrox
UMask=0077
WorkingDirectory=${REMOTE_ROOT}/current
EnvironmentFile=${REMOTE_ROOT}/.env
ExecStart=/usr/bin/env VKPI_SKIP_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=${REMOTE_ROOT}/current/backend ${REMOTE_ROOT}/.venv/bin/python -B scripts/ops/vkpi_sync_watchdog.py deadman --remote-root ${REMOTE_ROOT}
TimeoutStartSec=5m
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
DEADMANSERVICE

  cat > "${LOCAL_STAGE}/${REMOTE_DEADMAN_TIMER}" <<DEADMANTIMER
[Unit]
Description=Check V-KPI daily sync acceptance after the 04:00 UTC run budget

[Timer]
OnCalendar=*-*-* 10:15:00 UTC
Persistent=true
AccuracySec=1m
Unit=${REMOTE_DEADMAN_SERVICE}

[Install]
WantedBy=timers.target
DEADMANTIMER

  stage_remote_files \
    "${REMOTE_SERVICE}" "${REMOTE_TIMER}" "${ALERT_SERVICE}" \
    "${REMOTE_DEADMAN_SERVICE}" "${REMOTE_DEADMAN_TIMER}"
  run_remote_transaction primary
  rm -rf -- "${LOCAL_STAGE}"
  LOCAL_STAGE=""
}

install_remote_qualified_kol_units() {
  new_local_stage

  cat > "${LOCAL_STAGE}/${REMOTE_QUALIFIED_KOL_SERVICE}" <<SERVICE
[Unit]
Description=V-KPI qualified hot KOL refresh
Wants=network-online.target
# Do not order this after the long primary oneshot. At 05:00 UTC the ExecStart
# is-active check must observe a still-running primary and skip, not wait for it
# to finish and then start duplicate KOL work.
After=network-online.target viltrox-2.0-test.service
OnFailure=vkpi-sync-daily-alert@%n.service

[Service]
Type=oneshot
User=viltrox
Group=viltrox
UMask=0077
RestartPreventExitStatus=75 76
WorkingDirectory=${REMOTE_ROOT}/current
EnvironmentFile=${REMOTE_ROOT}/.env
Environment=PYTHONPATH=${REMOTE_ROOT}/current/backend
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/bin/bash -lc 'if systemctl is-active --quiet vkpi-sync-daily.service; then printf '\''{"event":"qualified_kol_refresh_skipped","reason":"vkpi-sync-daily.service active","at":"%s"}\n'\'' "\$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ)" >> /var/log/vkpi/qualified_kol_refresh_skip.log; exit 0; fi; /usr/bin/env VKPI_SKIP_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=${REMOTE_ROOT}/current/backend ${REMOTE_ROOT}/.venv/bin/python -B scripts/cron_daily_sync.py --skip-official --include-qualified-kol --kol-tiers hot --kol-stale-days ${QUALIFIED_KOL_STALE_DAYS} --kol-limit ${QUALIFIED_KOL_LIMIT} --kol-max-posts 1 --kol-error-stop-threshold 3 >> /var/log/vkpi/qualified_kol_refresh_\$(date -u +%%Y%%m%%d).log 2>&1'
# Qualified-only runs can enqueue up to 200 children. Keep the service budget
# aligned with cron_daily_sync.py's bounded terminal observation window instead
# of killing the observer while provider work is still legitimately running.
TimeoutStartSec=6h
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
SERVICE

  cat > "${LOCAL_STAGE}/${REMOTE_QUALIFIED_KOL_TIMER}" <<TIMER
[Unit]
Description=Run V-KPI qualified hot KOL refresh at 05:00 UTC

[Timer]
OnCalendar=*-*-* 05:00:00 UTC
Persistent=true
RandomizedDelaySec=300
Unit=${REMOTE_QUALIFIED_KOL_SERVICE}

[Install]
WantedBy=timers.target
TIMER

  stage_remote_files "${REMOTE_QUALIFIED_KOL_SERVICE}" "${REMOTE_QUALIFIED_KOL_TIMER}"
  run_remote_transaction qualified
  rm -rf -- "${LOCAL_STAGE}"
  LOCAL_STAGE=""
}

install_local_snapshot_agent() {
  mkdir -p "${HOME}/Library/LaunchAgents" "${PROJECT_ROOT}/runtime/prod-sync"
  cat > "${LOCAL_AGENT_PATH}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LOCAL_AGENT_ID}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd '${PROJECT_ROOT}' &amp;&amp; scripts/ops/sync_prod_snapshot_to_local.sh &gt;&gt; runtime/prod-sync/local-snapshot-sync.log 2&gt;&amp;1</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>6</integer>
    <key>Minute</key><integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${PROJECT_ROOT}/runtime/prod-sync/local-snapshot-sync.launchd.log</string>
  <key>StandardErrorPath</key>
  <string>${PROJECT_ROOT}/runtime/prod-sync/local-snapshot-sync.launchd.err</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLIST

  launchctl bootout "gui/$(id -u)" "${LOCAL_AGENT_PATH}" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "${LOCAL_AGENT_PATH}"
  launchctl enable "gui/$(id -u)/${LOCAL_AGENT_ID}"
  launchctl print "gui/$(id -u)/${LOCAL_AGENT_ID}" | sed -n '1,45p'
}

validate_configuration

case "${1:-all}" in
  remote)
    install_remote_timer
    ;;
  remote-qualified-kol)
    install_remote_qualified_kol_units
    ;;
  local)
    install_local_snapshot_agent
    ;;
  all)
    install_remote_timer
    install_local_snapshot_agent
    ;;
  *)
    echo "Usage: $0 [remote|remote-qualified-kol|local|all]" >&2
    exit 1
    ;;
esac
