#!/usr/bin/env bash
# 安装/刷新本地 launchd 代理(幂等):
#   com.vkpi.verify-receipt   每日 08:00 / 20:00 跑 scheduled_verify_receipt.sh(交付维样本)
# supervisor(com.vkpi.stack-supervisor)已由早前安装,本脚本只 kickstart 让它吃到新代码。
#   scripts/ops/install_local_launchd.sh            # 安装并加载
#   scripts/ops/install_local_launchd.sh --status   # 只看状态
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LABEL="com.vkpi.verify-receipt"
PLIST="$AGENTS/$LABEL.plist"
DOMAIN="gui/$(id -u)"
mkdir -p "$AGENTS" "$ROOT/runtime/logs"

status() {
  for l in com.vkpi.stack-supervisor "$LABEL" com.vkpi.auto-train; do
    if launchctl print "$DOMAIN/$l" >/dev/null 2>&1; then
      printf '  %-28s loaded  %s\n' "$l" "$(launchctl print "$DOMAIN/$l" 2>/dev/null | grep -E 'state =|last exit code' | tr -s ' ' | tr '\n' ' ')"
    else
      printf '  %-28s NOT loaded\n' "$l"
    fi
  done
}
if [ "${1:-}" = "--status" ]; then status; exit 0; fi

cat >"$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$ROOT/scripts/ops/scheduled_verify_receipt.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LC_ALL</key><string>en_US.UTF-8</string>
    <key>LANG</key><string>en_US.UTF-8</string>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$ROOT/runtime/logs/scheduled-verify.launchd.log</string>
  <key>StandardErrorPath</key><string>$ROOT/runtime/logs/scheduled-verify.launchd.log</string>
</dict>
</plist>
PL
plutil -lint "$PLIST" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "已加载 $LABEL(08:00 / 20:00)"

# 自动发车:每晚 00:30(北京时间;调度重活 02:30 才开始)。四道守卫在 auto_train.sh 里。
AT_LABEL="com.vkpi.auto-train"; AT_PLIST="$AGENTS/$AT_LABEL.plist"
cat >"$AT_PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AT_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/ops/auto_train.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>0</integer><key>Minute</key><integer>30</integer></dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LC_ALL</key><string>en_US.UTF-8</string>
    <key>LANG</key><string>en_US.UTF-8</string>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$ROOT/runtime/logs/auto-train.launchd.log</string>
  <key>StandardErrorPath</key><string>$ROOT/runtime/logs/auto-train.launchd.log</string>
</dict>
</plist>
PL
plutil -lint "$AT_PLIST" >/dev/null
launchctl bootout "$DOMAIN/$AT_LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$AT_PLIST"
echo "已加载 $AT_LABEL(每晚 00:30 自动发车,四道守卫)"
# supervisor 吃新代码:kickstart -k 重启(KeepAlive 会立刻拉回)
if launchctl print "$DOMAIN/com.vkpi.stack-supervisor" >/dev/null 2>&1; then
  launchctl kickstart -k "$DOMAIN/com.vkpi.stack-supervisor" && echo "已重启 com.vkpi.stack-supervisor"
fi
status
