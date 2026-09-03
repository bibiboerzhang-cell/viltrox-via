# 公测期维护窗与发车纪律(方案 v0.3 · 2026-09-02)

> 目标:公测期间(架构 A1 的蓝绿 / 车道滚动落地之前)每次发车都是**全站停机 + 排水必须为空**,所以发车只能在向测试者公告过的窗口内做。
> 本文给:可核的时钟事实 → 冲突表 → 候选窗口打分 → 推荐方案 → 一次发车的时间线 → 纪律 → 公告模板 → 关 auto-train 的操作 → 退出条件。
> 对应计划项 BK-09(最小版)/ human_items H-06;完整版见架构 A1 W2–W3(`A/architecture.json` 为会话易失文件,本次合成时已不在)。
> v0.3:§1 按 21:56 EDT 实测更新 launchd 状态(**代理仍在,今晚会再判一次**)、最近一次发车结果;§4 加入 A1-W1 已在树的排水收窄开关 `VKPI_DRAIN_BLOCKING_SCOPE`;§8 补 `verify-receipt` 一并处置。

---

## 0. 一页结论

- **公测期关掉每晚自动发车**(`com.vkpi.auto-train`),改为**每周一个固定窗口 + 一个热修槽**,手动 `train.sh`,窗前 24h 与 45 分钟各公告一次。
- **主窗(常规班车)**:**每周六 02:00–03:45 UTC** = 周五 22:00–23:45 美东(EDT)= 周六 10:00–11:45 北京。
- **热修槽(周中,只修阻断)**:**周二 / 周四 22:15–23:45 UTC** = 18:15–19:45 美东 = 次日 06:15–07:45 北京。
- **大改(迁移 / 依赖大版本)**:**周日 22:15–23:45 UTC** = 周日 18:15 美东 = 周一 06:15 北京。
- 每次发车硬性 **105 分钟**封顶(排水等待改 `VKPI_TRAIN_DRAIN_WAIT_SECONDS=3600`),超时即放弃,不跨 04:00 UTC 的每日同步。
- 周一至周五 13:00–22:00 UTC(美东工作时段)**零发车**;新一批测试者进门后 48h 内零发车。
- 建设波 1(11 车道 + 3 张迁移 + 依赖大版本)按「大改」口径走 **W5(周日 22:15 UTC)**,不走热修槽。

---

## 1. 可核的时钟事实

| 事实 | 证据 |
|---|---|
| 自动发车由 launchd `com.vkpi.auto-train` 触发,`StartCalendarInterval Hour=0 Minute=30`,**用控制器本机时区** | `~/Library/LaunchAgents/com.vkpi.auto-train.plist`(`plutil -p` 实测,`RunAtLoad=false`) |
| 控制器(笔记本)当前时区 **EDT(UTC-4)** | 本机 `date +%Z %z` = `EDT -0400`(2026-09-02 21:56);`runtime/logs/auto-train.log` 时间戳为本地时间 |
| 因此现实触发点 = **00:30 美东 = 04:30 UTC = 12:30 北京** | 换算 |
| **21:56 EDT 实测:`launchctl print gui/501/com.vkpi.auto-train` 找得到服务**(state = not running,calendar 已登记),`com.vkpi.verify-receipt` 同样在;两份 plist 16:41 重写过。记忆里「已 bootout」与现状不符 | `launchctl print`;`ls -la ~/Library/LaunchAgents` |
| 今晚它会跑守卫:① 无班车在飞 ② 树净 ③ HEAD ≠ 上次成功落地 ④ CI 对 HEAD 绿。此刻 ② 会拒(11 车道未提交);**一旦主会话提交推送 + CI 转绿而人没关它,04:30 UTC 会无公告发车** | `scripts/ops/auto_train.sh` 四道守卫;`auto-train.log` 最后一行是 16:59 的 dry-run「守卫全过」 |
| `auto_train.sh` 注释写「每晚 00:30 北京」——与当前时区对不上;若笔记本切到北京时区,触发点变为 16:30 UTC = 12:30 美东(北美工作高峰) | `scripts/ops/auto_train.sh:2` |
| 一次发车 = freeze → 本地验收 → 排水等待(最长 5400s,每 120s 探)→ deploy(stop web + 16 车道 + redis worker → 切 release → 迁移 → 起 → 严格验证)→ post-deploy | `scripts/ops/train.sh`(`VKPI_TRAIN_DRAIN_WAIT_SECONDS` / `_PROBE_INTERVAL_SECONDS`) |
| 排水阻断项(`all` 口径):`apify_jobs` queued/running、`job_execution_ledger` 活跃、`vkpi_action_inbox` executing、`vkpi_workflow_runs` running(lease 有效)、provider 预留 | `scripts/ops/verify_release_drain.py` |
| A1-W1 已在树:`VKPI_DRAIN_BLOCKING_SCOPE=interactive` 时批量车道行只报数不阻断(车道单元 `TimeoutStopSec=1300` 优雅停 + 租约回收);交互车道与 provider 预留仍阻断 | `verify_release_drain.py` 新增 `apify_jobs_active_interactive` / `_batch` 探针;`tests/test_verify_release_drain_blocking_scope.py` |
| 停机时长:9/1 成功车 08:44:10Z 发车 → 08:50:13Z worker 重启 → 08:54:03Z 日志收尾;精确秒数无时间戳(**估 5–10 分钟**) | 9/1 deploy 日志 |
| 失败画像:9/1 13 次尝试 12 次失败(4 次 macOS 控制器环境);**最近一次** 9/2 03:50Z(= 9/1 23:50 美东 = 9/2 11:50 北京)`outcome.json` = `failed`,原因「release drain is not empty」 | `runtime/ops/post-deploy/20260902T035013Z-b6a1b14484d3/outcome.json` |
| deploy 期间会 stop + mask `vkpi-sync-daily.timer` 与 `vkpi-health-sentinel.timer`,结束后恢复——**跨过 04:00 UTC 的发车会吞掉当天同步**,10:15 UTC 看门狗会报警 | `deploy_local_to_cloud.sh` quiesce 段 |
| prod 定时器(UTC):备份 00 / 06 / 12 / 18;哨兵 01:30;每日同步 04:00;看门狗 10:15 | `scripts/ops/systemd/*.timer`(`OnCalendar` 实测) |
| 独立 scheduler 单元(`vkpi-scheduler.service`,默认关)**不在 quiesce 清单里**,启用后发车期间它跑旧代码直到 gate 段重启它 | `deploy/systemd/README.md` Known gap |
| 美国夏令时 2026-11-01 结束:窗口**按 UTC 定义**,美东本地时间届时晚 1 小时;北京无夏令时 | 日历 |

负载假设(人工确认项,来自 H-01):北美测试者工作时段 09:00–18:00 美东 = 13:00–22:00 UTC(周一至周五);中国总部员工 09:00–19:00 北京 = 01:00–11:00 UTC(周一至周五,周六上午可能有人)。
**两段工作时段合起来覆盖 01:00–22:00 UTC 的工作日,没有对双方都空闲的工作日时段**——这就是为什么主窗放在周末交界。

---

## 2. 24 小时冲突表(UTC)

| UTC | 美东(EDT) | 北京 | 北美测试者 | 中国内部 | prod 定时器 | 排水可空? |
|---|---|---|---|---|---|---|
| 00:00–01:00 | 20:00–21:00 | 08:00–09:00 | 空闲 | 上班前 | 00:00 备份 | 可(避开备份) |
| 01:00–04:00 | 21:00–00:00 | 09:00–12:00 | 空闲 | **上班** | 01:30 哨兵 | 工作日难;**周六可** |
| 04:00–06:00 | 00:00–02:00 | 12:00–14:00 | 空闲 | 午间 / 上班 | **04:00 每日同步**(占用车道 ~30–60 分钟,推断) | 难 |
| 06:00–11:00 | 02:00–07:00 | 14:00–19:00 | 空闲 | **上班** | 06:00 备份;10:15 看门狗 | 工作日难 |
| 11:00–13:00 | 07:00–09:00 | 19:00–21:00 | 上班前 | 下班 / 加班 | 12:00 备份 | 中 |
| 13:00–22:00 | 09:00–18:00 | 21:00–06:00 | **上班(高峰)** | 空闲 | 18:00 备份 | **禁止发车** |
| 22:00–00:00 | 18:00–20:00 | 06:00–08:00 | 下班(晚间可能有人) | 上班前 | — | 可 |

注:「排水可空」取决于**中国内部员工与调度任务**制造的在飞任务,不只是测试者;把 `VKPI_DRAIN_BLOCKING_SCOPE` 设为 `interactive`(H-17)后,批量车道的长任务不再挡车,这一列会整体变「可」——但交互搜索仍会挡,所以公告里「请勿发起在线搜索」那句不能省。

---

## 3. 候选窗口打分

打分 1–5(5 最好):A 北美测试者影响小;B 中国内部影响小;C 无定时器冲突;D 排水可空概率;E 操作者(美东)清醒可值守。

| 窗 | UTC | 美东 | 北京 | A | B | C | D | E | 合计 | 结论 |
|---|---|---|---|---|---|---|---|---|---|---|
| W1 | 周六 02:00–03:45 | 周五 22:00–23:45 | 周六 10:00–11:45 | 5 | 4(周末) | 5(00 备份已过、01:30 哨兵已过、04:00 前收工) | 4 | 4 | **22** | **主窗** |
| W2 | 周二/四 22:15–23:45 | 18:15–19:45 | 次日 06:15–07:45 | 4(晚间) | 5 | 5(18:00 备份已过) | 4 | 5 | **23** | **热修槽** |
| W3 | 工作日 11:15–12:45 | 07:15–08:45 | 19:15–20:45 | 4 | 3(加班) | 4(12:00 备份在窗尾) | 3 | 3(早起) | 17 | 备选 |
| W4(现状 auto-train) | 每日 04:30 起 | 00:30 | 12:30 | 5 | 2 | **1(紧贴 04:00 同步)** | 2 | 1(睡觉,无人值守、无公告) | 11 | **停用** |
| W5 | 周日 22:15–23:45 | 周日 18:15 | 周一 06:15 | 4 | 5 | 5 | 5 | 5 | **24** | **大改专用** |

W2 合计略高于 W1 但**每周只用一次 W1** 的理由:热修槽应保持「空着」——常规班车全堆在周末交界,让周中给测试者一段稳定周期(周一到周五零变更),反馈才能归因到同一个版本。

---

## 4. 推荐方案

1. **常规班车:每周一次,W1(周六 02:00 UTC)**;两周内累计的车道合并成一车。
2. **热修:W2(周二 / 周四 22:15 UTC)**,只发「测试者被阻断」级别的修复;每周最多一次;4 小时前公告。
3. **大改:W5(周日 22:15 UTC)**,迁移不可前向兼容、依赖大版本(H-08)、systemd 单元变更(H-15 / A1 W1)一律走这里;24h 前公告,窗内**先演练回滚再发车**。**建设波 1 首车走这里**(含 307/308/309 三张迁移 + W3 依赖升级)。
4. **紧急(整站不可用)**:随时,先在群里发「紧急维护」再动手;结束补事故台账(`runtime/ops/incidents.jsonl`,`caused_by_release` 填 sha12)。
5. **auto-train 处置**:公测期关闭(§8)。等两个条件都满足再考虑恢复:① `VKPI_DRAIN_BLOCKING_SCOPE=interactive` 在 prod 生效且一次窗内发车排水 ≤60 分钟;② 连续 2 次手动 W1 一次成功。恢复时改成周五 22:00 本机时间触发(§8 第二段),且公告由固定文案「每周六 10:00 北京 / 周五 22:00 美东」代替。
6. **窗长**:105 分钟(公告说「最长 2 小时」)。排水等待用 `VKPI_TRAIN_DRAIN_WAIT_SECONDS=3600 VKPI_TRAIN_DRAIN_PROBE_INTERVAL_SECONDS=60`,给 deploy + 验证留 45 分钟,**永远在 04:00 UTC 前收工**。
7. **变更冻结**:周一至周五 13:00–22:00 UTC 零发车;新一批测试者进门后 48h 零发车;演示 / 汇报当天零发车。

---

## 5. 一次发车的时间线(以 W1 为例,UTC)

| 时刻 | 动作 | 谁 |
|---|---|---|
| 周五 02:00(T-24h) | 群里发「维护公告 A」;确认本周要上的车道全部合入、CI 绿、树净 | 操作者 |
| 周六 00:30(T-90m) | 预检:`git status` 净;`gh run list --limit 1` 绿;`systemctl list-timers vkpi-backup-r2.timer`(00:00 备份已跑);本地栈 `/health` 对齐;笔记本插电 + `caffeinate -i`;其它 Agent / 车道**停止写工作区**(memory:班车在飞禁开新工地);`bash scripts/ops/auto_train.sh --dry-run` 看守卫是否全过(它只判不发) | 操作者 |
| 01:15(T-45m) | 群里发「维护公告 B」(45 分钟后开始;请在 02:00 前收工,在飞的在线搜索会中断) | 操作者 |
| 01:50(T-10m) | 探排水:`ssh viltrox` 后按 `train.sh` 同口径跑 `verify_release_drain.py`(或直接让 train 探);非空且在下降 → 等;非空且在上升 → 查是谁(调度任务 / 员工)再决定 | 操作者 |
| 02:00(T-0) | `VKPI_TRAIN_DRAIN_WAIT_SECONDS=3600 VKPI_TRAIN_DRAIN_PROBE_INTERVAL_SECONDS=60 bash scripts/ops/train.sh`;群里发「开始」 | 操作者 |
| 02:00–03:00 | 排水等待(最长 60 分钟);03:00 仍非空 → train 自行放弃,发「取消公告」,记原因,下周再来 | train.sh |
| 03:00–03:15 | deploy(停机 5–10 分钟)+ 严格验证 + post-deploy 冒烟 | train.sh |
| 03:15–03:30 | 人工冒烟:登录、搜一次、开详情、收藏、反馈弹窗;`/health` sha = HEAD;`outcome.json` 为 success;建设波 1 首车另加 H-18 三迁移列核验 + 登出后旧 cookie 401 | 操作者 |
| 03:30(T+90m) | 群里发「完成公告」(附本次改了什么、测试者需要重做什么;W4 首车要提醒「所有人需要重新登录一次」) | 操作者 |
| 03:45(硬停) | 若此时仍在 deploy / 回滚:不再重试;若站点不可用 → 手工回滚(runbook §6.1)并发「回滚公告」 | 操作者 |
| 04:00 | 每日同步照常(未被 mask) | prod |

失败分流(memory「发车失败先分抖动还是真缺陷」):断言失败数 / 错误码 / 负载三判别器;抖动 → 同窗内**最多重试一次**(`VKPI_TRAIN_REUSE_CANDIDATE=1 VKPI_TRAIN_SKIP_RESTART=1`);真缺陷 → 不重试,下窗。两次连续失败 → 停,写 RCA。

---

## 6. 纪律条款(贴墙版)

1. 窗外零发车;紧急也要先公告后动手。
2. 每周最多 2 次发车(W1 + 一次热修);大改只走 W5。
3. 发车前 24h 与 45 分钟各公告一次;完成 / 取消 / 回滚都要公告。
4. 窗内硬停 105 分钟,永不跨 04:00 UTC。
5. 同窗内最多重试一次,且只对「抖动」;两次连败即停。
6. 迁移优先前向兼容(`VKPI_FORWARD_COMPATIBLE_MIGRATIONS`);不可兼容的迁移只上 W5,且窗内先演练回滚。
7. 发车期间其它车道 / Agent 不写共享工作树;撞上了逐文件提交重发,**绝不 stash**。
8. 用户可见故障进 `runtime/ops/incidents.jsonl`,`caused_by_release` 填 `post-deploy/` 目录里的 sha12。
9. 每次发车的 `outcome.json` + 公告链接登记到 §9 表;BK-09 验收口径 = 「公告窗外 7 天零发车」。
10. 测试者手册 §5 与本文 §0 的窗口文字必须一致;改一处改两处。
11. launchd 三代理(`stack-supervisor` / `verify-receipt` / `auto-train`)的加载状态以 `launchctl print` 为准,**不以记忆为准**;每次发车预检顺手查一次。

---

## 7. 公告模板(zh / en 各一份,占位用尖括号)

**A · 提前 24h**
> 【维护公告】V-KPI 测试环境将于 **<日期> <时段>(北京)/ <日期> <时段>(美东)** 进行版本更新,预计中断 10–15 分钟,最长 2 小时。期间正在进行的在线搜索会中断,请在开始前完成手头操作。本次更新内容:<一句话>。
> [Maintenance] The V-KPI beta will be updated on **<date> <window> (Beijing) / <date> <window> (US Eastern)**. Expect a 10–15 min interruption, up to 2 h max. Running online searches will be cut off — please wrap up before it starts. What's changing: <one line>.

**B · 提前 45 分钟**
> 【提醒】45 分钟后(<时刻>)开始维护,请在此之前保存 / 收工;之后请勿发起在线搜索。
> [Reminder] Maintenance starts in 45 min (<time>). Please wrap up now and don't start online searches after that.

**C · 开始**
> 【维护中】已开始,预计 <时刻> 前完成;完成会再通知。
> [In progress] Maintenance started; expected to finish by <time>. We'll post when done.

**D · 完成**
> 【维护完成】已恢复。本次改了:<要点 1–3 条>。<若本次含登录改造:所有人需要重新登录一次。>如果你之前有在线搜索被中断,请重新发起;遇到异常请用 Help → 提交反馈,并注明「维护后」。
> [Done] Service is back. Changes: <1–3 bullets>. <If the login change shipped: everyone needs to sign in again once.> If an online search was interrupted, please re-run it. Report anything odd via Help → Feedback, tagged "post-maintenance".

**E · 取消 / 延期**
> 【取消】本次维护因 <原因:后台任务未清空 / 预检未过> 取消,改到 <下次窗口>;服务未受影响。
> [Cancelled] Tonight's maintenance is cancelled (<reason>) and moved to <next window>. No impact on service.

**F · 回滚**
> 【回滚】更新后发现 <问题>,已回退到上一版本,服务恢复;本次改动将在 <下次窗口> 重新上线。
> [Rolled back] We hit <issue> after the update and reverted to the previous version. Service is back; the change will ship again on <next window>.

**G · 紧急**
> 【紧急维护】<时刻> 起紧急处理 <问题>,预计 <时长>;期间无法使用,恢复后通知。
> [Emergency] Emergency maintenance starting <time> for <issue>, ~<duration>. The beta will be unavailable; we'll post when restored.

---

## 8. 关 / 改 auto-train(控制器上,用户执行)

**关闭(公测期默认;今晚 00:30 EDT 前做)**
```bash
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.vkpi.auto-train.plist
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.vkpi.verify-receipt.plist     # 施工期一并关(它会跑本地栈验收样本)
launchctl print "gui/$(id -u)/com.vkpi.auto-train" 2>&1 | head -1     # 期望:Could not find service
launchctl print "gui/$(id -u)/com.vkpi.verify-receipt" 2>&1 | head -1
tail -n 3 runtime/logs/auto-train.log                                   # 此后不再新增行
```
验收:次日 04:30 UTC 后 `runtime/logs/auto-train.log` 无新行、`runtime/ops/post-deploy/` 无新目录。
回退(恢复每晚自动):`launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.vkpi.auto-train.plist`,或 `bash scripts/ops/install_local_launchd.sh`(会同时重装 verify-receipt 并 kickstart supervisor)。
注意:`install_local_launchd.sh` **会把两个代理都装回来**——任何车道 / 会话跑过它,本节就要重做;这就是 21:56 看到代理仍在的最可能原因。

**改成周五 22:00 本机时间触发(仅在 §4 第 5 条两个条件满足后)**
```bash
cp ~/Library/LaunchAgents/com.vkpi.auto-train.plist ~/Library/LaunchAgents/com.vkpi.auto-train.plist.bak.$(date -u +%Y%m%dT%H%M%SZ)
# 把 StartCalendarInterval 的 dict 改为:
#   <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
# (Weekday 5 = 周五;Hour/Minute 是本机时区;夏令时切换后 UTC 会偏 1 小时,届时按 UTC 窗口重新对表)
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.vkpi.auto-train.plist 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.vkpi.auto-train.plist
launchctl print "gui/$(id -u)/com.vkpi.auto-train" | grep -A4 "calendar"   # 核对 weekday/hour/minute
```
并在 `scripts/ops/auto_train.sh` 里默认导出 `VKPI_TRAIN_DRAIN_WAIT_SECONDS=3600`(代码车道改;plist 由 `install_local_launchd.sh` 生成,改 plist 模板也要改那个脚本)。
注意:auto-train 没有公告步骤,恢复它的前提是**固定窗口已写进测试者手册**,公告 A 用固定文案。

---

## 9. 退出条件与登记

**可以放宽到「只公告不限窗」**,当以下全部成立:
- A1 W3 落地:web 双端口蓝绿 + 车道滚动,`verify_release_drain` 阻断收窄(`interactive` 口径已在树,是第一步);
- 连续 2 个窗口发车期间 `/health` 拨测(5s 间隔)零中断,`apify_jobs` 无 lost;
- 回滚在小机上演练成功一次并留 `outcome.json`。
**可以恢复自动发车**,当上述成立且控制器已迁 Linux 小机(H-07)。

| 日期(UTC) | 窗 | HEAD sha12 | 排水等待(分) | 停机(分) | 结果 | 公告链接 | 备注 |
|---|---|---|---|---|---|---|---|
| 2026-09-02 03:50 | (auto/手动,窗外) | b6a1b14484d3 | — | 0(未进 deploy) | failed:drain not empty | 无 | 公测纪律生效前 |
| | | | | | | | |
