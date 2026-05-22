#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SSH_TARGET="${SSH_TARGET:-viltrox}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/viltrox-2.0}"
REMOTE_SERVICE="${REMOTE_SERVICE:-vkpi-sync-daily.service}"
REMOTE_TIMER="${REMOTE_TIMER:-vkpi-sync-daily.timer}"
LOCAL_AGENT_ID="${LOCAL_AGENT_ID:-com.viltrox.prod-snapshot-sync}"
LOCAL_AGENT_PATH="${HOME}/Library/LaunchAgents/${LOCAL_AGENT_ID}.plist"

install_remote_timer() {
  ssh "${SSH_TARGET}" "cat > /etc/systemd/system/${REMOTE_SERVICE}" <<SERVICE
[Unit]
Description=V-KPI daily official + KOL lightweight sync
Wants=network-online.target
After=network-online.target viltrox-2.0-test.service
OnFailure=vkpi-sync-daily-alert@%n.service

[Service]
Type=oneshot
RestartPreventExitStatus=75 76
WorkingDirectory=${REMOTE_ROOT}
Environment=PYTHONPATH=backend
ExecStart=/bin/bash -lc 'mkdir -p /var/log/vkpi && .venv/bin/python scripts/cron_daily_sync.py --official-max-posts 50 --kol-limit 1200 --kol-max-posts 1 >> /var/log/vkpi/sync_daily_\$(date -u +%%Y%%m%%d).log 2>&1'
# TODO: Consider lowering to 2h. Current sync averages 30-60min; 6h releases resources slowly on real hangs.
# Confirm deep scan or other long-running jobs will not be cut before changing this value.
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
Description=Run V-KPI daily official + KOL lightweight sync at 04:00 UTC

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
  local)
    install_local_snapshot_agent
    ;;
  all)
    install_remote_timer
    install_local_snapshot_agent
    ;;
  *)
    echo "Usage: $0 [remote|local|all]" >&2
    exit 1
    ;;
esac
