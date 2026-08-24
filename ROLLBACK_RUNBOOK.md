# 回滚 Runbook — 上线门卡 12

V-KPI 上线后出事时的**回滚预案**。门卡 12 = 上线前必须有可执行的回退路径。
本手册只描述「怎么退回到一个已知好的状态」,**不引入任何业务变更**。

> 铁律(回滚也不破):
> - 回滚**绝不** `UPDATE` / `DROP` 任何含 `viltrox_fit_score` / `rule_v0` 的表;
>   指纹 `SUM=32828.726, n=999` 不因回滚而变。
> - 迁移 `_down` **只手动执行**,永不进自动部署流水线。
> - 灰度开关只读写 `scheduler_tasks.enabled` 一列(见 §4)。

仓库根:`/Users/bibiboer/Documents/V-KPI——marketing`
生产部署根:`/opt/viltrox-2.0`(systemd `WorkingDirectory`)

---

## 0. 出事第一动作(60 秒内)

1. **判级**:是「全站不可用 / 报错刷屏」还是「单功能异常」?
   - 全站 → 走 §1(revert 前向修复 + canonical train)。
   - 单灰度任务异常(`scheduler_tasks`)→ 先走 §4 一键关该档,**通常不必整体回退**。
2. **记录现场**:先抓 `/health`(见 §6)拿当前 `git_sha` / `built_at`,截图告警,
   再动手。回滚后现场会消失。
3. **通知**:在 ops 群发「正在回滚 + 原因一句话 + 预计影响」。
4. 按下面对应章节执行。**先留证、再做前向修复、完整跑门禁、最后独立验收**。

---

## 1. 生产:revert 前向修复 + canonical train

生产常规“回滚”不是在服务器切指针,而是在本地从当前生产 SHA 创建一个新的
`git revert` 前向修复提交,再让 `scripts/ops/train.sh` 对这个新 HEAD 执行完整门禁、
冻结、部署和独立验收。生产禁止 `git checkout`、就地重建 `frontend/dist`、手改
`current` / `previous`,也禁止重写部署根的 `BUILD_GIT_SHA` / `BUILD_TIME`。

### 1.1 current/previous 双 Seal 仅作只读取证

```bash
set -euo pipefail
ROOT=/opt/viltrox-2.0
CURRENT="$(readlink -f -- "$ROOT/current")"
PREVIOUS="$(readlink -f -- "$ROOT/previous")"

case "$CURRENT" in "$ROOT/releases/"*) ;; *) echo "unsafe current: $CURRENT" >&2; exit 1;; esac
case "$PREVIOUS" in "$ROOT/releases/"*) ;; *) echo "unsafe previous: $PREVIOUS" >&2; exit 1;; esac
CURRENT_ID="${CURRENT##*/}"
PREVIOUS_ID="${PREVIOUS##*/}"
test "$CURRENT_ID" != "$PREVIOUS_ID"
CURRENT_SHA="$(tr -d '[:space:]' < "$CURRENT/BUILD_GIT_SHA")"
PREVIOUS_SHA="$(tr -d '[:space:]' < "$PREVIOUS/BUILD_GIT_SHA")"
printf '%s\n' "$CURRENT_SHA" | grep -Eq '^[0-9a-f]{40}$' || exit 1
printf '%s\n' "$PREVIOUS_SHA" | grep -Eq '^[0-9a-f]{40}$' || exit 1

for RELEASE_ID in "$CURRENT_ID" "$PREVIOUS_ID"; do
  sudo env PYTHONDONTWRITEBYTECODE=1 python3 -B \
    "$CURRENT/scripts/ops/atomic_release_layout.py" verify-seal \
    --root "$ROOT" --release-id "$RELEASE_ID" \
    --expected-owner-uid 0 --expected-owner-gid 0
done
printf 'current=%s sha=%s\nprevious=%s sha=%s\n' \
  "$CURRENT" "$CURRENT_SHA" "$PREVIOUS" "$PREVIOUS_SHA"
```

这一步只保存指针、双 Seal、`/health` 和 journal 证据,不授权任何生产写操作。
任一指针或 Seal 不可信均为 **NO-GO**;不得把 previous 的存在误当成人工激活许可。

### 1.2 在本地创建 revert 前向修复提交

以下单提交配方只适用于已确认“当前生产 SHA 就是要撤销的坏提交”、本地主分支 HEAD
与该生产 SHA 完全一致且工作树干净的情况。合并提交、多提交范围、迁移或数据副作用
必须先由代码/迁移负责人给出明确前向兼容方案。

```bash
set -euo pipefail
cd /Users/bibiboer/Documents/V-KPI——marketing
test -z "$(git status --porcelain=v1 --untracked-files=all)"
PROD_SHA="<从 §1.1 与只读 /health 核实的 CURRENT_SHA>"
test "$(git rev-parse HEAD)" = "$PROD_SHA"
BAD_SHA="$PROD_SHA"
git show --stat --oneline "$BAD_SHA"
git revert --no-edit "$BAD_SHA"
REPAIR_SHA="$(git rev-parse HEAD)"
git show --stat --oneline "$REPAIR_SHA"
```

必须评审这个新提交确实是“撤销坏行为的前向版本”,而不是恢复旧服务器目录。若 revert
冲突、需要 `_down`、或无法证明新代码兼容当前 schema,停止并升级事件负责人。

### 1.3 只走 canonical train

```bash
set -euo pipefail
cd /Users/bibiboer/Documents/V-KPI——marketing
REPAIR_SHA="$(git rev-parse HEAD)"
scripts/ops/train.sh "$REPAIR_SHA"
```

`train.sh` 必须完整执行,不得设置跳过生产门禁的临时覆盖。它调用的
`deploy_local_to_cloud.sh` 只有在 `RELEASE_VALIDATION_COMMIT_STARTED=0`、即 activation
commit 尚未开始时,才允许自动调用完整 rollback controller。完整 controller 会统一
处理所有 release consumers、正在运行的 PgBouncer activation oneshot 的 stop +
runtime mask、PgBouncer map 与 service、数据库 identity 与 `.env` fingerprint、
validation fence、Redis worker,以及 sync service/timer 和 health sentinel service/timer
这 4 个 reviewed unit 的精确状态,并重新验证 web/worker/runtime。

人工单独调用 layout `restore` 只能恢复一部分文件系统状态,不能证明上述整体事务安全,
因此**禁止作为生产命令**。一旦 activation commit 已开始,deploy 也禁止自动回退,只能
按其保留的 receipt/fence 状态执行事件级 roll-forward 恢复。

若当前生产 runtime 已失效,导致 canonical train 的认证、ancestor、prelock 或 drain
门禁无法通过,结论是 **NO-GO / 事件升级**。保留 current/previous、Seal、部署日志和
controller receipt,由事件负责人制定受审查的恢复步骤;本手册不给手改指针、手启服务
或绕过 prelock 的兜底命令。

---

## 2. 停 / 启 admin · worker · 等服务

生产为 systemd(`scripts/ops/systemd/*`);本地用 `scripts/start_*.sh`。

### 2.1 systemd(生产)

| 角色 | unit | 说明 |
| --- | --- | --- |
| Web | `viltrox-2.0-test.service` | `app.main:app`,`127.0.0.1:8001` |
| 交互 Worker | `vkpi-worker-interactive.service` | 交互/高优先级任务 |
| 批量 Worker | `vkpi-worker-bulk@1..15.service` | 15 个批量实例 |
| 可选 Redis Worker | `vkpi-redis-worker.service` | 只按捕获状态恢复,不可无条件开启 |
| 健康巡检 | `vkpi-health-sentinel.timer` | 最后恢复 timer |
| 日同步 | `vkpi-sync-daily.timer` | 最后恢复 timer |

```bash
set -euo pipefail
# 生产此处只读观察;服务状态转换由 §1 canonical train / deploy controller 独占。
sudo systemctl status viltrox-2.0-test.service
journalctl -u viltrox-2.0-test.service -n 100 --no-pager
systemctl --no-pager --state=running \
  'vkpi-worker-interactive.service' 'vkpi-worker-bulk@*.service'
```

> 本节不提供生产 `stop/start/restart/mask/unmask` 配方。部分停服会破坏 deploy
> controller 对 consumers、oneshot、fence 和 reviewed unit 状态的完整取证与恢复。

### 2.2 本地启动脚本(开发 / 应急直跑)

```bash
set -euo pipefail
ROOT=/Users/bibiboer/Documents/V-KPI——marketing
# admin:gunicorn 守护进程,默认 127.0.0.1:8102,日志 runtime/logs/admin-8102-*.log
bash "$ROOT/scripts/start_admin.sh"
bash "$ROOT/scripts/start_worker.sh"
bash "$ROOT/scripts/start_scheduler.sh"

# 停 admin(daemon 模式靠 pid / 端口)
pkill -f "gunicorn app.main:app" || true
lsof -ti:8102 | xargs -r kill          # 兜底:按端口杀
```

> `start_admin.sh` 有启动横幅,打印 `ENVIRONMENT / DATABASE_URL / APP_GIT_SHA`。
> **回滚后必看横幅**确认连的是对的库、跑的是对的 sha。本地默认
> `ENVIRONMENT=local` 且强制 LOCAL stack;生产必须显式 `export ENVIRONMENT=production`。

---

## 3. 迁移 `_down` 处置边界(事件级受审)

迁移结构:`migrations/NNN_<name>.sql` 正向,配对 `migrations/NNN_<name>_down.sql` 反向。
正向由 `scripts/alembic_upgrade.sh`(→ `alembic upgrade head`)在部署时跑。
通用 Runbook **不提供生产 `_down` 执行命令**。`_down` 常包含 `DROP TABLE` /
`DROP COLUMN`,必须由迁移负责人针对具体事件给出并评审专用方案。

### 3.1 决策:要不要回滚迁移?

大多数回滚**不需要**碰库 —— 代码退回旧版后,旧代码读新表通常兼容(新表/新列向后兼容是设计前提)。
**只有**当本次上线的迁移引入了「旧代码无法容忍的破坏性 schema 变更」时,才回退迁移。

> 优先「代码回退 + 保留新 schema」。回退迁移有丢数据风险(`_down` 多含 `DROP`),是最后手段。

### 3.2 只读审查 `_down`

```bash
set -euo pipefail
ROOT=/Users/bibiboer/Documents/V-KPI——marketing
DOWN="$ROOT/migrations/<NNN>_<name>_down.sql"
test -f "$DOWN"
sed -n '1,240p' "$DOWN"
shasum -a 256 "$DOWN"
if rg -ni 'viltrox_fit_score|rule_v0' "$DOWN"; then
  echo "protected field reference found; NO-GO" >&2
  exit 1
fi
```

上述只证明“文件内容已被人工审阅”,不证明可安全执行。任何生产 schema 逆向操作
至少要在专用 gate 中同时证明:精确数据库 identity / fingerprint、所有 consumer 与
oneshot 完整 drain、PgBouncer 与 validation fence 状态、受影响数据备份可恢复、
当前 schema 与目标代码兼容,以及执行失败后的 roll-forward 路径。任一证据缺失即
**NO-GO**;不得直接运行 `psql -f ..._down.sql` 或盲跑 `alembic downgrade`。

### 3.3 灰度整表撤销(极端)

`migrations/130_vkpi_scheduler_tasks_down.sql` 会 `DROP TABLE scheduler_tasks`,
不属于常规止血手段。通常只走 §4 关开关；若确需撤表，必须按 §3.2 的事件级受审
边界另行制定方案，通用 Runbook 不授权执行。

---

## 4. 灰度任务一键关

灰度只有一个开关:`scheduler_tasks.enabled`。关掉 = 对应 job 进函数即 `return`,**不执行**。
详见 `GRAY_RELEASE_RUNBOOK.md` 与 `backend/app/services/scheduler/jobs.py` 的
`_scheduler_task_enabled(task_key)`。

```bash
set -euo pipefail
VENV=/Users/bibiboer/Documents/V-KPI——marketing/.venv/bin/python   # 必须用 .venv,非裸 python3
cd /Users/bibiboer/Documents/V-KPI——marketing

# 看现状(11 行 + 每档 OFF/ON + last_run / last_error)
$VENV scripts/gray_release.py --list

# 一键全关(从高风险档往低关) —— 出事首选
$VENV scripts/gray_release.py --disable-tier high   --apply
$VENV scripts/gray_release.py --disable-tier medium --apply
$VENV scripts/gray_release.py --disable-tier low    --apply
```

等价 SQL(脚本不可用时的兜底):

```sql
UPDATE scheduler_tasks SET enabled=FALSE WHERE risk_level='high';
UPDATE scheduler_tasks SET enabled=FALSE WHERE risk_level='medium';
UPDATE scheduler_tasks SET enabled=FALSE WHERE risk_level='low';
-- 核武器:UPDATE scheduler_tasks SET enabled=FALSE;   -- 全关
```

要点:
- 脚本默认 **dry-run**,不带 `--apply` 绝不写库;占位符 `'?'`(sqlite 风格,内部翻 `%s`)。
- 关开关**立即生效**,无需重启 scheduler(job 每次进函数都查开关)。已在跑的一轮
  不可用手工部分停服冒充回滚;需要整体处置时走 §1 canonical train / 事件升级。
- `OPS_SCHEDULER_FORCE_ENABLE=1` 会**绕过开关整体强开**,生产严禁;回滚时确认它**未**被设置。
- 关开关**不删数据、不触既有表**,是最低风险的回退动作。

---

## 5. 验证回滚成功(开量前必过)

```bash
set -euo pipefail
VENV=/Users/bibiboer/Documents/V-KPI——marketing/.venv/bin/python
# 1) atomic 身份四齐(见 §1.3):resolved current Seal / current/BUILD_GIT_SHA /
#    /health server_git_sha / client_git_sha 必须同 SHA。
readlink -f /opt/viltrox-2.0/current
curl -fsS https://www.viltroxtest.com/health | python3 -m json.tool

# 2) 关键页能开(admin 登录、Dashboard、Projects)—— 人工点一遍
# 3) 灰度全 OFF(若走了 §4):
$VENV scripts/gray_release.py --list   # 期望全 OFF 或仅留预期档
# 4) web 日志无新错误:
tail -n 200 runtime/logs/admin-8102-error.log    # 本地
journalctl -u viltrox-2.0-test.service -n 200 --no-pager   # 生产
# 5)(可选)上线门 smoke:
$VENV scripts/smoke_vkpi_v3_release_gate.py
```

全绿后,再按 `GRAY_RELEASE_RUNBOOK.md` 逐档慢慢放量,**别一次开满**。

---

## 6. 出事联系 / 排查顺序

**排查顺序(从外到内,先止血后定因):**

1. `/health` —— `status` / `git_sha` / `built_at`。sha 与预期不符 → 部署没生效或没退干净。
2. **是否单灰度任务** —— `gray_release.py --list` 看 `last_error` 哪个非空。是 → §4 关该档,多半到此为止。
3. **web 起没起** —— `systemctl status viltrox-2.0-test.service` + `journalctl`。没起 → 核对 resolved current、Seal、环境和 sha。
4. **前端白屏 / 跑旧 JS** —— `current/frontend/dist/build-info.json` 的 sha 对不对;对不上说明 release 不完整。生产不要就地重建或激活 previous,改走 §1 的 revert 前向修复 + canonical train。
5. **库层** —— 仅当 1~4 排除后,才怀疑迁移;按 §3 谨慎处理,先备份。
6. **彻底回退** —— 以上都搞不定 → §1 前向修复;若 train prelock 不过则 NO-GO / 事件升级。

**联系人(按域):**

| 域 | 找谁 / 看哪 |
| --- | --- |
| 部署 / systemd / nginx | 部署负责人 + `deploy/systemd/`、`deploy/nginx/viltrox-2.0.conf` |
| 迁移 / schema | 迁移负责人(`_down` 必须本人或其授权才手动跑)+ `MIGRATION_MATRIX.md` |
| 灰度调度 | `GRAY_RELEASE_RUNBOOK.md` owner + `scheduler_registry.py` |
| 前端构建 / dist | `frontend/vite.config.ts`、`/health` git_sha 对齐负责人 |
| Owner(全权) | 唯一 owner 账号,RBAC 矩阵 / 强制操作的最终拍板 |

> 升级触发线(达到任一即升级为事件,拉群按 incident 走):
> 全站 5xx 持续 > 5min;数据写错且在扩大;`/health` 长时间非 200;回滚两次仍不稳。

---

## 附:分包类改动同样走前向修复

即使只是 `frontend/vite.config.ts` 的 `manualChunks` 变化,生产也不允许 checkout 文件
或替换 dist。仍须在本地创建经评审的 `git revert` 前向提交并执行 §1 canonical train,
让冻结产物、服务端/客户端 SHA 与生产 Seal 一起通过同一套门禁。
