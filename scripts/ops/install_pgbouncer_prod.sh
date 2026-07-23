#!/usr/bin/env bash
# install_pgbouncer_prod.sh — prod PgBouncer 幂等安装/体检(2026-07-22 多并发地基)。
#
# 只在 prod 主机上由主会话/人工执行(本脚本自身不 ssh、不改 app 的 .env)。
#
# 用法:
#   sudo bash scripts/ops/install_pgbouncer_prod.sh --check-only      # 纯只读体检,零写入
#   sudo bash scripts/ops/install_pgbouncer_prod.sh                   # 幂等安装/收敛
#   sudo bash scripts/ops/install_pgbouncer_prod.sh --env-file /opt/viltrox-2.0/.env
#
# 每步 set -e + 带时间戳日志;凡是失败都会带步骤名退出。
#
# ── app 切 6432 的最小 env 改法(本脚本刻意不动 .env,装完人工做)──────────────
#   读取链:config.py:192-194 —— DATABASE_URL 直连;DATABASE_POOL_URL 一旦非空,
#   DB_USE_PGBOUNCER 缺省自动=1,DB_RUNTIME_URL 切到 pool URL。所以最小改法只有一行:
#
#   1) 在 /opt/viltrox-2.0/.env 追加(host 固定 127.0.0.1,端口 5432→6432,其余同 DATABASE_URL):
#        DATABASE_POOL_URL=postgresql://<user>:<pass>@127.0.0.1:6432/<dbname>
#   2) 重启 web:sudo systemctl restart viltrox-2.0-test.service
#   3) 验证:curl -s http://127.0.0.1:8001/health | jq '.db'  → pooler_enabled=true
#
#   worker 不跟切:16 条 apify 车道由 /etc/vkpi/vkpi-lane-overrides.env 的
#   DB_USE_PGBOUNCER=0 钉死直连(EnvironmentFile 后读者胜,压过 .env);redis worker
#   由 unit ExecStart argv 的 DB_USE_PGBOUNCER=0 钉死(argv 压过一切 EnvironmentFile)。
#   原因:worker/scheduler 用 session 级 advisory lock(Gemini QPS 闸/LLM slot/leader),
#   transaction pooling 下 session 锁漂移 = 闸失效,必须直连 5432。
#   回滚:删掉 DATABASE_POOL_URL 那行(或设 DB_USE_PGBOUNCER=0)+ 重启 web 即回直连。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE_PATH="$REPO_ROOT/deploy/pgbouncer/pgbouncer.prod.ini.template"

ENV_FILE="/opt/viltrox-2.0/.env"
CHECK_ONLY=0
PGB_INI="/etc/pgbouncer/pgbouncer.ini"
PGB_USERLIST="/etc/pgbouncer/userlist.txt"
PGB_PORT=6432

log() { printf '%s [install_pgbouncer] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only) CHECK_ONLY=1; shift ;;
    --env-file) ENV_FILE="${2:?--env-file 需要路径}"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done

[[ "$(id -u)" == "0" ]] || die "必须 root(sudo)执行——要读 .env 并写 /etc/pgbouncer"
[[ -f "$ENV_FILE" ]] || die "env 文件不存在: $ENV_FILE"
[[ -f "$TEMPLATE_PATH" ]] || die "模板不存在: $TEMPLATE_PATH(需在仓库 checkout 内执行)"

PYBIN="$(command -v python3 || true)"
[[ -z "$PYBIN" && -x /opt/viltrox-2.0/.venv/bin/python ]] && PYBIN=/opt/viltrox-2.0/.venv/bin/python
[[ -n "$PYBIN" ]] || die "找不到 python3"

# ── step 1: 解析 DATABASE_URL(密码绝不进日志/argv,只经环境变量与 0600 文件)──
log "step 1/6: 解析 $ENV_FILE 的 DATABASE_URL"
RENDER_DIR="$(mktemp -d /root/.pgbouncer-render.XXXXXX)"
trap 'rm -rf "$RENDER_DIR"' EXIT
chmod 700 "$RENDER_DIR"

# pgbouncer 版本决定 auth_type:>=1.14 用 scram-sha-256,否则降 md5(userlist 均为明文密码,
# 两种 auth_type 下 client/server 两段挑战都能完成,是对 PG scram 存储最稳的组合)。
PGB_VERSION="$(dpkg-query -W -f='${Version}' pgbouncer 2>/dev/null || true)"
AUTH_TYPE="scram-sha-256"
if [[ -n "$PGB_VERSION" ]] && dpkg --compare-versions "$PGB_VERSION" lt 1.14; then
  AUTH_TYPE="md5"
  log "pgbouncer $PGB_VERSION < 1.14,auth_type 降级 md5"
fi

VKPI_ENV_FILE="$ENV_FILE" VKPI_TEMPLATE="$TEMPLATE_PATH" VKPI_OUT_DIR="$RENDER_DIR" \
VKPI_AUTH_TYPE="$AUTH_TYPE" "$PYBIN" - <<'PY'
import os, sys, urllib.parse

env_file = os.environ["VKPI_ENV_FILE"]
url = ""
with open(env_file, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")  # 后读者胜,同 dotenv
if not url:
    sys.stderr.write("env 文件里没有 DATABASE_URL\n"); sys.exit(1)
p = urllib.parse.urlsplit(url)
user = urllib.parse.unquote(p.username or "")
password = urllib.parse.unquote(p.password or "")
host = p.hostname or "127.0.0.1"
port = str(p.port or 5432)
dbname = (p.path or "/").lstrip("/") or ""
if not (user and password and dbname):
    sys.stderr.write("DATABASE_URL 缺 user/password/dbname\n"); sys.exit(1)

tpl = open(os.environ["VKPI_TEMPLATE"], encoding="utf-8").read()
for token, value in (("@DB_NAME@", dbname), ("@DB_HOST@", host), ("@DB_PORT@", port),
                     ("@APP_DB_USER@", user), ("@AUTH_TYPE@", os.environ["VKPI_AUTH_TYPE"])):
    tpl = tpl.replace(token, value)

out_dir = os.environ["VKPI_OUT_DIR"]
def write(path, content, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
write(os.path.join(out_dir, "pgbouncer.ini"), tpl, 0o640)
# userlist 明文密码,双引号按 pgbouncer 规矩写成 ""
q = password.replace('"', '""')
write(os.path.join(out_dir, "userlist.txt"), f'"{user}" "{q}"\n', 0o600)
write(os.path.join(out_dir, "meta.sh"),
      f"PGB_DB_USER='{user}'\nPGB_DB_NAME='{dbname}'\n", 0o600)
sys.stderr.write(f"parsed DATABASE_URL: user={user} host={host}:{port} db={dbname} (密码不打印)\n")
PY
# shellcheck disable=SC1091
source "$RENDER_DIR/meta.sh"

# ── step 2: 包状态 ────────────────────────────────────────────────────────────
log "step 2/6: 检查 pgbouncer 安装状态"
INSTALLED=0
if [[ -n "$PGB_VERSION" ]]; then
  INSTALLED=1
  log "已安装 pgbouncer $PGB_VERSION"
else
  log "pgbouncer 未安装"
fi

# ── step 3: 配置漂移比对 ──────────────────────────────────────────────────────
log "step 3/6: 比对 /etc/pgbouncer 现有配置"
INI_CURRENT=0; USERLIST_CURRENT=0
[[ -f "$PGB_INI" ]] && cmp -s "$RENDER_DIR/pgbouncer.ini" "$PGB_INI" && INI_CURRENT=1
[[ -f "$PGB_USERLIST" ]] && cmp -s "$RENDER_DIR/userlist.txt" "$PGB_USERLIST" && USERLIST_CURRENT=1
log "pgbouncer.ini 一致=$INI_CURRENT userlist.txt 一致=$USERLIST_CURRENT"

SVC_ENABLED="$(systemctl is-enabled pgbouncer 2>/dev/null || true)"
SVC_ACTIVE="$(systemctl is-active pgbouncer 2>/dev/null || true)"
log "service enabled=$SVC_ENABLED active=$SVC_ACTIVE"

health_check() {
  # SELECT 1 走 6432(app 凭据);密码只经环境变量传给子进程。
  VKPI_PB_DSN="postgresql://127.0.0.1:${PGB_PORT}/${PGB_DB_NAME}?user=${PGB_DB_USER}" \
  VKPI_PB_USERLIST="$RENDER_DIR/userlist.txt" "$PYBIN" - <<'PY'
import os, sys
userlist = open(os.environ["VKPI_PB_USERLIST"], encoding="utf-8").read().strip()
password = userlist.split(" ", 1)[1].strip()[1:-1].replace('""', '"')
dsn = os.environ["VKPI_PB_DSN"]
try:
    import psycopg
except ImportError:
    sys.path.insert(0, "/opt/viltrox-2.0/.venv/lib")
    try:
        import psycopg
    except ImportError:
        sys.stderr.write("SKIP: 本机无 psycopg,请用 psql 手验 SELECT 1\n"); sys.exit(0)
with psycopg.connect(dsn, password=password, connect_timeout=5) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
sys.stderr.write("health: SELECT 1 via 6432 OK\n")
PY
}

if [[ "$CHECK_ONLY" == "1" ]]; then
  log "step 4/6: --check-only 体检汇总(零写入)"
  FAIL=0
  [[ "$INSTALLED" == "1" ]] || { log "CHECK FAIL: 未安装"; FAIL=1; }
  [[ "$INI_CURRENT" == "1" ]] || { log "CHECK FAIL: pgbouncer.ini 与模板渲染结果不一致"; FAIL=1; }
  [[ "$USERLIST_CURRENT" == "1" ]] || { log "CHECK FAIL: userlist.txt 不一致"; FAIL=1; }
  [[ "$SVC_ENABLED" == "enabled" ]] || { log "CHECK FAIL: service 未 enable"; FAIL=1; }
  [[ "$SVC_ACTIVE" == "active" ]] || { log "CHECK FAIL: service 未 active"; FAIL=1; }
  if [[ "$SVC_ACTIVE" == "active" ]]; then
    health_check || { log "CHECK FAIL: 6432 SELECT 1 失败"; FAIL=1; }
  fi
  if grep -q '^DATABASE_POOL_URL=' "$ENV_FILE"; then
    log "info: .env 已有 DATABASE_POOL_URL(app 已接/待重启生效)"
  else
    log "info: .env 尚无 DATABASE_POOL_URL(app 仍直连;切换步骤见脚本头注释)"
  fi
  [[ "$FAIL" == "0" ]] && log "check-only: 全部通过" || log "check-only: 存在未收敛项"
  exit "$FAIL"
fi

# ── step 4: 安装包(幂等)──────────────────────────────────────────────────────
log "step 4/6: 安装 pgbouncer(如缺)"
if [[ "$INSTALLED" != "1" ]]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y pgbouncer
  log "apt 安装完成: $(dpkg-query -W -f='${Version}' pgbouncer)"
fi

# ── step 5: 写配置(有变化才动,变更前留 .bak)────────────────────────────────
log "step 5/6: 收敛 /etc/pgbouncer 配置"
install -d -o postgres -g postgres -m 0755 /etc/pgbouncer
CHANGED=0
if [[ "$INI_CURRENT" != "1" ]]; then
  [[ -f "$PGB_INI" ]] && cp -a "$PGB_INI" "${PGB_INI}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
  install -o postgres -g postgres -m 0640 "$RENDER_DIR/pgbouncer.ini" "$PGB_INI"
  CHANGED=1; log "pgbouncer.ini 已更新"
fi
if [[ "$USERLIST_CURRENT" != "1" ]]; then
  install -o postgres -g postgres -m 0600 "$RENDER_DIR/userlist.txt" "$PGB_USERLIST"
  CHANGED=1; log "userlist.txt 已更新(0600 postgres:postgres)"
fi
[[ "$CHANGED" == "0" ]] && log "配置无漂移,跳过写入"

# ── step 6: enable + (re)start + 健康检查 ─────────────────────────────────────
log "step 6/6: enable/启动/健康检查"
systemctl enable pgbouncer >/dev/null 2>&1 || die "systemctl enable 失败"
if [[ "$CHANGED" == "1" || "$SVC_ACTIVE" != "active" ]]; then
  systemctl restart pgbouncer
  sleep 1
fi
systemctl is-active --quiet pgbouncer || die "pgbouncer 服务未 active(journalctl -u pgbouncer 查因)"
health_check || die "6432 SELECT 1 健康检查失败"
log "完成:PgBouncer 已就绪(app 切换步骤见脚本头注释,本脚本不改 .env)"
