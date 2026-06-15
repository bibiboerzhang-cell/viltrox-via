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
   - 全站 → 走 §1(代码/dist 整体回退)。
   - 单灰度任务异常(`scheduler_tasks`)→ 先走 §4 一键关该档,**通常不必整体回退**。
2. **记录现场**:先抓 `/health`(见 §6)拿当前 `git_sha` / `built_at`,截图告警,
   再动手。回滚后现场会消失。
3. **通知**:在 ops 群发「正在回滚 + 原因一句话 + 预计影响」。
4. 按下面对应章节执行。**先停写、后回退、再验证、最后开量**。

---

## 1. 回退到上一个 commit / dist

部署形态:nginx → `viltrox-2.0-public` / `viltrox-2.0-admin`(Python web)→
gunicorn `app.main:app` 同时托管前端 `frontend/dist`。所以「回退」= 回退代码树 +
重建 dist + 重启 web。前端构建**不会**自动跟随后端,必须显式重建(见门卡构建戳对齐)。

### 1.1 确认要退回的目标 commit

```bash
ROOT=/Users/bibiboer/Documents/V-KPI——marketing   # 生产为 /opt/viltrox-2.0
cd "$ROOT"
git log --oneline -n 10                  # 找「上一个已知好的」commit <GOOD_SHA>
cat BUILD_GIT_SHA 2>/dev/null            # 当前部署戳(/health 的 git_sha 来源)
```

### 1.2 回退代码树(二选一)

- **快速回退(推荐,不改历史)**:checkout 到目标 commit(detached)或新建回滚分支。
  ```bash
  git checkout <GOOD_SHA>                 # 或: git checkout -b rollback/<date> <GOOD_SHA>
  ```
- **revert 单个坏 commit**(只有一个坏改且历史干净时):
  ```bash
  git revert --no-edit <BAD_SHA>
  ```

> 注意:`git checkout <GOOD_SHA>` 进 detached HEAD 只为「先止血」。事后必须把
> 修复正式 commit 回主干,别长期停在 detached。

### 1.3 重建并对齐构建戳(顺序固定:commit → build → 重启)

```bash
# 1) 后端构建戳(/health git_sha 的真源,HUP 不会刷,必须重写文件)
git rev-parse HEAD            > "$ROOT/BUILD_GIT_SHA"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$ROOT/BUILD_TIME"

# 2) 重建前端 dist(否则浏览器仍跑旧 JS,与后端 git_sha 不一致)
cd "$ROOT/frontend" && npm ci && npm run build      # 产物落 frontend/dist/

# 3) 全量重启 web(见 §2),不要只 reload —— dist/构建戳要全量生效
```

校验:`/health` 的 `git_sha` 应等于 `BUILD_GIT_SHA`,`frontend/dist/build-info.json`
里的 `gitSha` 也应一致(见 `vite.config.ts` 的 `vkpi-build-info` 插件)。三者不齐 = 没退干净。

### 1.4 不想重建?用上一份 dist 快照

若上线前对 `frontend/dist/` 做过快照(强烈建议每次上线前 `cp -r dist dist.bak.<sha>`),
回滚可直接换目录,免 `npm run build`:

```bash
cd "$ROOT/frontend"
mv dist dist.broken.$(date +%s)
cp -r dist.bak.<GOOD_SHA> dist
# 再重写 BUILD_GIT_SHA/BUILD_TIME 为 <GOOD_SHA>,然后 §2 重启
```

> 本 PR 只改 `vite.config.ts` 的 `manualChunks`(纯 build 分包),**不动任何源**。
> 若回滚仅为撤掉本次分包改动:`git checkout <prev> -- frontend/vite.config.ts`
> 后重建 dist 即可,无需碰后端/迁移/灰度。分包改动**不影响运行时行为**,
> 只影响产物 chunk 切分,回退风险极低。

---

## 2. 停 / 启 admin · worker · 等服务

生产为 systemd(`deploy/systemd/*.service`,实例化 `@<user>`);本地用 `scripts/start_*.sh`。

### 2.1 systemd(生产)

| 角色 | unit | 说明 |
| --- | --- | --- |
| Admin Web | `viltrox-2.0-admin` | `app.main:app`,`PORT 8102` |
| Public Web | `viltrox-2.0-public` | 对外站点 |
| Worker | `viltrox-2.0-worker` | 任务执行,`ENABLE_SCHEDULER=0` |
| Scheduler | `viltrox-2.0-scheduler` | 定时调度,`ENABLE_SCHEDULER=1` |

```bash
# 回滚时的安全顺序:先停「写侧」再停「读侧」,起来时反过来
sudo systemctl stop  viltrox-2.0-scheduler@<user>   # 1) 先停定时器,止住新任务
sudo systemctl stop  viltrox-2.0-worker@<user>      # 2) 停 worker,排空在跑任务
# (此处做 §1 代码/dist 回退、§3 迁移回退)
sudo systemctl restart viltrox-2.0-admin@<user>     # 3) 起 admin
sudo systemctl restart viltrox-2.0-public@<user>    # 4) 起 public
sudo systemctl start   viltrox-2.0-worker@<user>    # 5) 起 worker
sudo systemctl start   viltrox-2.0-scheduler@<user> # 6) 最后再开 scheduler(放量见 §4)

sudo systemctl status viltrox-2.0-admin@<user>      # 看 active/running
journalctl -u viltrox-2.0-admin@<user> -n 100 --no-pager   # 看启动日志
```

> 只想**暂停新任务、不动 web**:`stop scheduler` + `stop worker` 即可,读侧 admin/public
> 继续服务。这是「单灰度任务出事」的最小动作,优先于整体回退。

### 2.2 本地启动脚本(开发 / 应急直跑)

```bash
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

## 3. 迁移 `_down` 怎么用(仅手动)

迁移结构:`migrations/NNN_<name>.sql` 正向,配对 `migrations/NNN_<name>_down.sql` 反向。
正向由 `scripts/alembic_upgrade.sh`(→ `alembic upgrade head`)在部署时跑。
**`_down` 永远不进流水线,只手动、单条、确认后执行。**

### 3.1 决策:要不要回滚迁移?

大多数回滚**不需要**碰库 —— 代码退回旧版后,旧代码读新表通常兼容(新表/新列向后兼容是设计前提)。
**只有**当本次上线的迁移引入了「旧代码无法容忍的破坏性 schema 变更」时,才回退迁移。

> 优先「代码回退 + 保留新 schema」。回退迁移有丢数据风险(`_down` 多含 `DROP`),是最后手段。

### 3.2 手动执行单条 `_down`

```bash
ROOT=/Users/bibiboer/Documents/V-KPI——marketing
# 0) 先备份(必须!_down 不可逆)
pg_dump "$DATABASE_URL" -t '<受影响表>' > "$ROOT/backups/pre_down_<NNN>_$(date +%s).sql"

# 1) 先 dry-read:看清这条 _down 会做什么(常是 DROP TABLE / DROP COLUMN)
cat "$ROOT/migrations/<NNN>_<name>_down.sql"

# 2) 确认无 viltrox_fit_score / rule_v0 字样后,手动 apply
psql "$DATABASE_URL" -1 -v ON_ERROR_STOP=1 -f "$ROOT/migrations/<NNN>_<name>_down.sql"
```

要点:
- **逆序回退**:多条迁移要回退时,从最新 `NNN` 往小退,一条一条来。
- `-1`(单事务)+ `ON_ERROR_STOP=1`:出错整条回滚,不留半截 schema。
- 回退后 alembic 版本表可能与 SQL 现状不符 —— 记录在案,事后由迁移负责人对齐,
  **别**为对齐而盲跑 `alembic downgrade`。
- 红线复核:执行前 `grep -i 'viltrox_fit_score\|rule_v0' <该 _down.sql>` 必须为空。

### 3.3 灰度整表撤销(极端)

撤掉整套调度任务表:`migrations/130_vkpi_scheduler_tasks_down.sql`(`DROP TABLE scheduler_tasks`)。
**仅在确认调度子系统整体不要时**用;通常 §4 关开关就够,不必删表。

---

## 4. 灰度任务一键关

灰度只有一个开关:`scheduler_tasks.enabled`。关掉 = 对应 job 进函数即 `return`,**不执行**。
详见 `GRAY_RELEASE_RUNBOOK.md` 与 `backend/app/services/scheduler/jobs.py` 的
`_scheduler_task_enabled(task_key)`。

```bash
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
- 关开关**立即生效**,无需重启 scheduler(job 每次进函数都查开关)。但要彻底停掉
  「正在跑的一轮」,叠加 §2.1 `stop scheduler + stop worker`。
- `OPS_SCHEDULER_FORCE_ENABLE=1` 会**绕过开关整体强开**,生产严禁;回滚时确认它**未**被设置。
- 关开关**不删数据、不触既有表**,是最低风险的回退动作。

---

## 5. 验证回滚成功(开量前必过)

```bash
# 1) 构建戳三齐(见 §1.3):/health git_sha == BUILD_GIT_SHA == dist/build-info.json gitSha
curl -fsS https://admin.viltrox.com/health | python3 -m json.tool   # 看 git_sha / built_at / status

# 2) 关键页能开(admin 登录、Dashboard、Projects)—— 人工点一遍
# 3) 灰度全 OFF(若走了 §4):
$VENV scripts/gray_release.py --list   # 期望全 OFF 或仅留预期档
# 4) web 日志无新错误:
tail -n 200 runtime/logs/admin-8102-error.log    # 本地
journalctl -u viltrox-2.0-admin@<user> -n 200 --no-pager   # 生产
# 5)(可选)上线门 smoke:
$VENV scripts/smoke_vkpi_v3_release_gate.py
```

全绿后,再按 `GRAY_RELEASE_RUNBOOK.md` 逐档慢慢放量,**别一次开满**。

---

## 6. 出事联系 / 排查顺序

**排查顺序(从外到内,先止血后定因):**

1. `/health` —— `status` / `git_sha` / `built_at`。sha 与预期不符 → 部署没生效或没退干净。
2. **是否单灰度任务** —— `gray_release.py --list` 看 `last_error` 哪个非空。是 → §4 关该档,多半到此为止。
3. **web 起没起** —— `systemctl status viltrox-2.0-admin@<user>` + `journalctl`。没起 → 看启动横幅的库/sha 是否错配。
4. **前端白屏 / 跑旧 JS** —— `dist/build-info.json` 的 sha 对不对;对不上 → §1.3 重建 dist + 全量重启。
5. **库层** —— 仅当 1~4 排除后,才怀疑迁移;按 §3 谨慎处理,先备份。
6. **彻底回退** —— 以上都搞不定 → §1 整体退回上一个 good commit + dist。

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

## 附:本次改动的回退(分包专用)

本 PR 仅改 `frontend/vite.config.ts` 的 `build.rollupOptions.output.manualChunks`
(把 react/router/framer-motion/lucide/recharts/three/leaflet/d3-geo 拆独立 vendor chunk),
**零源码改动、运行时行为完全不变**。如只需撤掉它:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
git checkout <prev_sha> -- frontend/vite.config.ts
cd frontend && npm run build        # 重建 dist
# 再 §2 全量重启 web
```

无需碰后端、迁移、灰度。chunk 文件名带 hash,旧缓存不会串版。
