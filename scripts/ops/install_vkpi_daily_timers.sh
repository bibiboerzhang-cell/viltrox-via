#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
REMOTE_SERVICE="${REMOTE_SERVICE:-vkpi-sync-daily.service}"
REMOTE_TIMER="${REMOTE_TIMER:-vkpi-sync-daily.timer}"
REMOTE_QUALIFIED_KOL_SERVICE="${REMOTE_QUALIFIED_KOL_SERVICE:-vkpi-qualified-kol-refresh.service}"
REMOTE_QUALIFIED_KOL_TIMER="${REMOTE_QUALIFIED_KOL_TIMER:-vkpi-qualified-kol-refresh.timer}"
ENABLE_QUALIFIED_KOL_TIMER="${ENABLE_QUALIFIED_KOL_TIMER:-0}"
QUALIFIED_KOL_LIMIT="${QUALIFIED_KOL_LIMIT:-200}"
QUALIFIED_KOL_STALE_DAYS="${QUALIFIED_KOL_STALE_DAYS:-1}"
LOCAL_AGENT_ID="${LOCAL_AGENT_ID:-com.viltrox.prod-snapshot-sync}"
LOCAL_AGENT_PATH="${HOME}/Library/LaunchAgents/${LOCAL_AGENT_ID}.plist"

backup_remote_units() {
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  ssh "${SSH_TARGET}" "set -euo pipefail
mkdir -p '${REMOTE_ROOT}/runtime/ops/systemd-unit-backups/${stamp}'
for unit in '${REMOTE_SERVICE}' '${REMOTE_TIMER}' 'vkpi-sync-daily-alert@.service' '${REMOTE_QUALIFIED_KOL_SERVICE}' '${REMOTE_QUALIFIED_KOL_TIMER}'; do
  if [ -f \"/etc/systemd/system/\${unit}\" ]; then
    cp \"/etc/systemd/system/\${unit}\" '${REMOTE_ROOT}/runtime/ops/systemd-unit-backups/${stamp}/'
  fi
done
printf '%s\n' '${REMOTE_ROOT}/runtime/ops/systemd-unit-backups/${stamp}'"
}

install_remote_timer() {
  backup_remote_units
  ssh "${SSH_TARGET}" "cat > /etc/systemd/system/${REMOTE_SERVICE}" <<SERVICE
[Unit]
Description=V-KPI daily official sync
Wants=network-online.target
After=network-online.target viltrox-2.0-test.service
OnFailure=vkpi-sync-daily-alert@%n.service

[Service]
Type=oneshot
# Exit 75 raises OnFailure but is not auto-restarted; the next timer is the retry boundary.
RestartPreventExitStatus=75 76
WorkingDirectory=${REMOTE_ROOT}/current
Environment=PYTHONPATH=${REMOTE_ROOT}/current/backend
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/bin/bash -lc 'mkdir -p /var/log/vkpi && env PYTHONDONTWRITEBYTECODE=1 ${REMOTE_ROOT}/.venv/bin/python -B scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol --include-qualified-kol --kol-tiers hot --kol-stale-days 1 --kol-max-posts 2 --kol-limit 90 --worker-count 2 --child-timeout-seconds 300 >> /var/log/vkpi/sync_daily_\$(date -u +%%Y%%m%%d).log 2>&1'
# Legacy KOL Pool refresh is intentionally excluded until P1.X.A tier selection replaces full-pool daily refresh.
# TODO: Consider lowering to 2h after official-only runtime is observed for one week.
TimeoutStartSec=6h
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
SERVICE

  ssh "${SSH_TARGET}" "cat > /etc/systemd/system/vkpi-sync-daily-alert@.service" <<'ALERTSERVICE'
[Unit]
Description=Write V-KPI daily sync failure alert for %i

[Service]
Type=oneshot
ExecStart=/bin/bash -lc 'mkdir -p /var/log/vkpi && printf '\''{"event":"vkpi_sync_systemd_failure","unit":"%%s","at":"%%s"}\n'\'' "%i" "$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ)" >> /var/log/vkpi/sync_failure_alert.log'
ALERTSERVICE

  ssh "${SSH_TARGET}" "cat > /etc/systemd/system/${REMOTE_TIMER}" <<TIMER
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

  ssh "${SSH_TARGET}" "systemctl daemon-reload && systemctl enable --now ${REMOTE_TIMER} && systemctl list-timers --all ${REMOTE_TIMER} --no-pager"
}

install_remote_qualified_kol_units() {
  backup_remote_units
  ssh "${SSH_TARGET}" "cat > /etc/systemd/system/${REMOTE_QUALIFIED_KOL_SERVICE}" <<SERVICE
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
RestartPreventExitStatus=75 76
WorkingDirectory=${REMOTE_ROOT}/current
Environment=PYTHONPATH=${REMOTE_ROOT}/current/backend
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/bin/bash -lc 'mkdir -p /var/log/vkpi && if systemctl is-active --quiet vkpi-sync-daily.service; then printf '\''{"event":"qualified_kol_refresh_skipped","reason":"vkpi-sync-daily.service active","at":"%s"}\n'\'' "\$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ)" >> /var/log/vkpi/qualified_kol_refresh_skip.log; exit 0; fi; env PYTHONDONTWRITEBYTECODE=1 ${REMOTE_ROOT}/.venv/bin/python -B scripts/cron_daily_sync.py --skip-official --include-qualified-kol --kol-tiers hot --kol-stale-days ${QUALIFIED_KOL_STALE_DAYS} --kol-limit ${QUALIFIED_KOL_LIMIT} --kol-max-posts 1 --kol-error-stop-threshold 3 >> /var/log/vkpi/qualified_kol_refresh_\$(date -u +%%Y%%m%%d).log 2>&1'
# Qualified-only runs can enqueue up to 200 children. Keep the service budget
# aligned with cron_daily_sync.py's bounded terminal observation window instead
# of killing the observer while provider work is still legitimately running.
TimeoutStartSec=6h
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
SERVICE

  ssh "${SSH_TARGET}" "cat > /etc/systemd/system/${REMOTE_QUALIFIED_KOL_TIMER}" <<TIMER
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

  if [ "${ENABLE_QUALIFIED_KOL_TIMER}" = "1" ]; then
    ssh "${SSH_TARGET}" "systemctl daemon-reload && systemctl enable --now ${REMOTE_QUALIFIED_KOL_TIMER} && systemctl list-timers --all ${REMOTE_QUALIFIED_KOL_TIMER} --no-pager"
  else
    ssh "${SSH_TARGET}" "systemctl daemon-reload && systemctl disable --now ${REMOTE_QUALIFIED_KOL_TIMER} >/dev/null 2>&1 || true && systemctl list-timers --all ${REMOTE_QUALIFIED_KOL_TIMER} --no-pager"
  fi
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
