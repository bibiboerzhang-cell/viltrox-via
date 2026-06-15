# V-KPI 升级总方案(修复盘 P0–P3 + Auto-Ops OS W0–W5 合一)

> 单一事实源。承重层(修复盘)先浇,价值层(Auto-Ops OS)后建。
> 红线:永不写 `viltrox_fit_score` / 不动 `rule_v0` / 指纹 `SUM=32828.726, n=999` 恒定。
> 生成于本会话审计 + 代码核对;表/函数/端点均已只读验证存在。

---

## 0. 现状基线(本会话已交付,作为起点)
- **P0–P15 全done**:Events 读鉴权、Dashboard 每用户隔离(P1,**但 account_picker 仍有漏网**)、穿透、新建项目假失败、新鲜度移出首页、任务-session 可追踪、收藏进 MY KOL、头像可点、账号卡头像粉丝+抽屉、媒体路由、履约自动化骨架+告警、分享/撤销审计、状态词归一、库存真后端+审计、视频预算解封+整段分镜、活动地图+跳转。
- **A 级**:A1 类型债 `@ts-nocheck` 124→52、A3 policy.py(additive 未采用)、A4 告警、A5 authz 矩阵测试(+33)、A7 Shopify 脚手架。
- **量化**:后端 574 pytest 绿、前端 tsc 0/build 通过、迁移到 138、指纹守恒。
- **审计评分(严苛口径)**:安全 42 / 性能 32 / 代码质量 52 / 前后端契约 58 → 工程硬度 ~46;产品力 ~78。

## 1. 目标
把工程硬度从 ~46 拉到可上线线 ~70,同时把平台从"查数据工具"升级为"自动运营 OS"。
```
        ┌──────────────────────────────────────────────┐
  价值层 │ Auto-Ops OS:Action Inbox · KOL Memory · 团队工作流  │
        ├──────────────────────────────────────────────┤
  承重层 │ 修复盘:安全 · 权限单一源 · 性能 · 履约闭环 · 运行态     │
        └──────────────────────────────────────────────┘
依赖:承重层(W0)不过,价值层(W1+)不启 —— 否则 Action Inbox 会把 scope 漏放大成"系统每天主动推送别人的数据"。
```

---

## W0 · 承重地基(= 修复盘 P0/P0.5,必须最先,1–3 天)

### 安全(审计 critical/high)
- [ ] **补 `dashboard_account_picker.py` 端点的 staff scope**(P1 漏网,员工从这条路仍看全量 KOL)
- [ ] **内联 SQL 改参数化**:`account_picker.py:295`、`summary.py:_actor_kols_sql/_actor_projects_sql`(现 `int()` 安全但坏习惯)
- [ ] **Redis 缓存 pickle → JSON**:`memory_cache.py:59/65/101`(堵 RCE)
- [ ] **删前端假数据 fallback**:`DashboardReplicaPage` API 失败回退渲染 SIGNALS_ALERTS(Sony/CineGear)→ 改错误态 + CI 拦截(违反"不造假"铁律)
- [ ] **`.env` 移出仓库追踪 + `.gitignore`**(密钥**轮换 H2 再做**,但先别再提交);代理账密拆出 URL;日志脱敏
- [ ] CORS 收口(禁通配)、上传扩展名白名单、natural_search 输入大小限

### 权限单一源(审计 high + 你的 Phase 0)
- [ ] **policy.py 真采用**:路由逐个迁到 `require_project/event_read/write`(现 additive 未用)→ 权限收成单一事实源
- [ ] 去掉 Projects "非 restricted 全员 write" 旧放行

### 性能(审计 critical/high)
- [ ] **隔离 join 复合索引**:`vkpi_kol_pool_favorites(staff_id,kol_pool_id)`、`vkpi_project_kol_assignments(project_id,kol_pool_id)`
- [ ] **重端点异步化**:content-fit / video 不在请求内烧 LLM(根治 60s 超时)
- [ ] **dashboard 聚合 Redis 缓存 30s** + stale-while-revalidate
- [ ] N+1 整治:`eleven_dimensions.py:545` compose_dimensions_11 批量化

### 运行态(你的 Phase 0)
- [ ] **`worker_online` 改真存活心跳**(现为"近5min有apify_jobs活动"启发式,空闲误判离线)
- [ ] scheduler 开关可控 + `/health` server_sha==client_sha==HEAD 对齐
**验收**:员工跨账号查不到任何全局数据;`/health` 对齐;574+ 测试绿;指纹守恒。

---

## W1 · Action Inbox 地基(dry-run only,你的"第一刀",最小风险)
- [ ] 迁移 `vkpi_action_inbox`(带 `CHECK(touches_v6_fit=FALSE)`)+ `vkpi_action_execution_ledger`
- [ ] `backend/app/domains/actions/{inbox,producers}.py`:`generate_daily_action_inbox(dry_run=True)` 产 8 类建议(kol_profile/deep_missing/failed_retry/project_observation/content_candidate/retrospective/event_followup/inventory_low),**只产不执行不写业务表**
- [ ] `vkpi_actions.py`:`GET /actions/inbox` + `POST /actions/generate-daily?dry_run=true`
- [ ] 前端只读 `ActionInboxPanel`(Dashboard 右侧"今日建议")
- [ ] scheduler 加 `daily_action_inbox_generate`(默认关)
> Action Inbox 即你 P2 列的 "Action Registry" 的落地形态:每条 action 自带 权限/endpoint/成本/是否写库·LLM/影响实体/为什么。
**验收**:dry-run 出建议、不写业务表、不碰 v6_fit。

---

## W2 · 执行 + Projects 履约闭环(= 修复盘 P1 履约链路 + P12 + P13)
- [ ] `actions/{executors,validators}.py` + approve/dismiss/snooze(人审后才执行,进 ledger)
- [ ] 接 `project_observation_due / content_candidate / retrospective_due`,**复用**已存在的 `observation_windows` + `retrospective_aggregate`
- [ ] **Projects 自动化审计表**:哪单同步/哪天签收/哪天开窗/扫了谁/命中啥/为何进复盘
- [ ] 灰度开 `project_shipment_sync` + `project_content_observation_scan`(medium)
- [ ] **状态词统一**已做(P14);UI 出一条时间线:建→选→寄→签→观察→发布→复盘
- [ ] 左侧任务看板补"最近完成 / 可打开结果 / 失败原因 / 重试入口"
**验收**:签收→开窗→扫发布→候选→人确认→复盘 全链可追;失败池分桶(download/provider/media/content_restricted/code_error)各自重试策略。

---

## W3 · KOL 长期记忆 + 视频全量优化(= 修复盘 P1 KOL深析 + 你的 Phase 3)
### KOL Memory
- [ ] 迁移 `vkpi_kol_memory_snapshots` + `vkpi_kol_lifecycle_events`
- [ ] `domains/kol/{memory,lifecycle}.py`:**v1 纯聚合不烧 LLM**(读已验证的 deep_results 780 / evidence / content_posts / assignments / failed jobs);v2 才上 LLM 写 summary
- [ ] KOLDetailDrawer 加"长期记忆"区(内容风格/推荐产品线/风险/合作履约/时间线,**显式独立于 V6 Fit**)
### 视频全量方案(回答"每个 KOL 全部视频分析慢"的问题)
- 真因:`GEMINI_QPS=0.05`(1次/20s)× `LLM_CONCURRENCY=1` × 单条 ~31s → ~1 视频/40-50s 串行;200 条 ≈ 3 小时,逐条 final_v1 不可行。
- [ ] **两层架构**:全量物化(Apify 抓全部视频元数据,便宜)用于"看到全部" + 限量深析(代表作/近期 top-N 跑 final_v1)
- [ ] **全量便宜初筛**:对所有视频先轻量判 Viltrox 使用 + 主题,命中再上 final_v1(解决"识别零命中")
- [ ] **异步 + 进度**(W2 任务看板)
- [ ] **QPS/并发随配额拉**:`APIFY_WORKER_GEMINI_QPS` 0.05→0.5、`LLM_CONCURRENCY` 1→2、`WORKER_SERVICE_PROCESSES` 2→4-8(天花板=Gemini 配额,**配合 H2 多账号**)
**验收**:任意 KOL 有 timeline + memory + 全量视频可见 + 代表作深析;不写 viltrox_fit_score。

---

## W4 · 团队工作流(= 你的 Phase 4 + 修复盘 刀1文案 + P5)
- [ ] 统一"公司账号(全局)/ 成员账号(自己负责·被分享·被分配)/ 无权限"三层语义
- [ ] share→自动建 `project_shared_to_you` action 提醒;成员只看自己的 inbox;company 看全局
- [ ] Settings 做成管理中心(权限/账号/模块开关/scheduler/预算/API key),个人/公司视图分层
- [ ] 文案清零:员工→成员、管理员可见→无权限/需授权、管理层可见→公司账号可见、员工授权→成员访问
**验收**:成员看不到全局数据/全局 action;被 share 能看、撤销不可见、有 audit;action 与权限一致。

---

## W5 · 上线级测试 + 灰度(= 修复盘 P2 smoke + A5 + 审计补的前端零测试)
- [ ] **全页面 smoke**(Dashboard/KOLPool/MYKOL/Projects/Events/Settings/次级页):能开 + 无 console error + enabled 有反馈 + disabled 有原因 + 无 404/500
- [ ] 角色 E2E + Action/履约 E2E;**前端测试从 0 起**(目标 30% 覆盖)
- [ ] 灰度阶梯:low(task_queue_health/session_reconcile/daily_action_inbox)→ 24h → medium(shipment_sync/observation_scan/provider_retry)→ high(batch_video_analysis/deep_backfill/failed_pool_recycle)**永远人审**

---

## P3 质量治理(滚动,不抢业务闭环)
拆 3 巨文件(`apify_jobs_worker.py 3852`、`ProjectDetailTabs.tsx 2352`、`V615ReplicaApp.tsx 1310`)· 清 A1 大件类型债(52→个位)· 归因/支付真接(`payouts.py` PayPal/Stripe TODO)· 多租户 tenant_id+RLS(做 SaaS 才需要)。

---

## 红线合集(写死)
1. Action 系统永不写 `viltrox_fit_score`(CHECK 兜底)· 2. LLM 批量 action `requires_approval=true` · 3. 单批成本超阈值拦住(走已建 budget_guard)· 4. 成员账号看不到全局 action · 5. 每动作进 ledger、可重试可忽略 · 6. scheduler 默认全关、灰度开 · 7. 每个自动动作可追溯原因 · 8. **W0 不过 W1 不启** · 9. 密钥 H2 轮换前,secret 不准进 payload_json/日志。

## 依赖图
```
W0(地基/安全/scope/性能/运行态)
  └─> W1(Action Inbox dry-run)
        ├─> W2(执行 + Projects 履约)──┐
        ├─> W3(KOL Memory + 视频全量) │
        └─> W4(团队工作流)────────────┘──> W5(测试 + 灰度上线)
```

## 第一刀(校准版)
**真·第一刀 = W0 里我能立刻做的**:account_picker scope 补 + 内联 SQL 参数化 + pickle→JSON + 隔离索引 + 删前端假 fallback + worker 真存活 + .gitignore 收口(密钥只收口不轮换)。
**第二刀 = W1 Action Inbox dry-run 地基**。
