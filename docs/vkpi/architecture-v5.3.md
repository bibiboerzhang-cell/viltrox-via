# V-KPI v5.3.1 Architecture Execution Edition

整理日期：2026-05-19
目标仓库：`/Users/bibiboer/Documents/V-KPI——marketing`

这份文档把 v5.3 战略方向固化成当前仓库可执行的 v5.3.1 版本。它不是新一轮产品愿景，也不是全量重构计划；它只定义后续代码包必须遵守的架构边界、路径、命名、兼容层和执行顺序。

## 1. 当前仓库边界

V-KPI 继续沿用当前仓库的技术栈和目录：

```text
backend: FastAPI
frontend: React / Vite
migrations: repo 根目录 migrations/*.sql
admin backend namespace: /api/admin/vkpi/...
frontend marketing namespace: /api/marketing/...
```

不要引入新的 `/v1/*` API 体系，不要把 V-KPI 拆到独立 Next.js 栈，不要新建另一套员工身份系统。

前端可以继续调用：

```text
/api/marketing/...
```

后端 router 继续注册到：

```text
/api/admin/vkpi/...
```

当前 `backend/app/main.py` 已有 `/api/marketing` 到 `/api/admin/vkpi` 的兼容转发层。新功能应优先接入这条现有路径，而不是另起 namespace。

## 2. Migration 路径与生产序列

后续 v5.3.1 migration 使用 repo 根目录的 `migrations/`，不要写成 `backend/app/db/migrations/`。

当前计划中的首批 migration 路径是：

```text
migrations/057_vkpi_ai_cost_budget.sql
migrations/058_vkpi_legacy_import.sql
migrations/059_vkpi_memory_tables.sql
```

如果生产迁移依赖 `backend/app/db/connection.py` 的 `_POSTGRES_MIGRATION_SEQUENCE`，新增 migration 必须满足其中之一：

```text
1. 加入 _POSTGRES_MIGRATION_SEQUENCE，并随代码包一起验证；
2. 明确标记为手动执行 migration，并写清执行顺序、回滚策略和验收查询。
```

不能只新增 SQL 文件却不说明生产环境如何被执行。

## 3. 身份与员工模型

当前系统已有员工身份表：

```text
staff
```

V-KPI 不新建：

```text
vkpi_staff
employees
```

如果后续确实需要 V-KPI 专属员工画像、配额、平台偏好或预算字段，使用扩展表：

```text
vkpi_staff_profiles
```

该扩展表必须以 `staff.id` 为主引用，不得复制员工姓名、角色、权限为另一套主数据。

## 4. JSON 与兼容字段风格

当前仓库大量表和 SQLite 兼容层使用：

```text
metadata_json TEXT
xxx_json TEXT
```

后续新表默认沿用这个风格：

```sql
identity_json TEXT NOT NULL DEFAULT '{}'
risk_flags_json TEXT NOT NULL DEFAULT '[]'
metadata_json TEXT NOT NULL DEFAULT '{}'
```

除非明确确认该表只跑 Postgres，并且补齐 SQLite/测试兼容策略，否则不要无脑使用：

```sql
JSONB
TEXT[]
BIGINT[]
```

已经存在的历史 `JSONB` migration 不作为新表默认模式。v5.3.1 后续包应优先保证当前本地、测试、生产迁移路径一致。

## 5. AI 成本守门位置

v5.3.1 的关键调整是：P5 成本可观测和 Budget Guard 必须前置到 P4 推荐任务之前。

原因：

```text
推荐 cron 会持续调用 LLM、爬虫或外部 provider。
如果先跑推荐再补预算守门，成本风险会先发生。
```

因此顺序固定为：

```text
P1  技术校准
P2A 历史 Excel 只读审计
P2B 历史导入 staging + commit/rollback
P3  Memory v0
P5  成本可观测 + Budget Guard
P4  推荐 v0 三场景
P6  内容脑 v0
P7  异常检测
P8  竞品脑 v0
P9  自然语言搜索
P10 Learning Loop
P12 RBAC/Magic Link
P11 SSE 可选最后
```

P4 之前必须至少具备：

```text
1. AI 调用成本 ledger；
2. provider / cron / staff / task item 维度的记录能力；
3. soft warning 与 hard stop 的预算判断；
4. 失败不影响主业务链路的记录策略；
5. 可查询的 admin API。
```

## 6. 历史 Excel 导入拆包

历史数据导入不能一次性直接写主表。P2 拆成两个包：

```text
P2A: 只读 audit
P2B: staging 入库 + commit + rollback
```

P2A 只读取 Excel 或历史文件，输出审计报告，不写 `kols`、`vkpi_projects`、`vkpi_kol_pool`、`vkpi_cost_ledger` 等主表。

P2B 才允许进入 staging 表，并且必须提供：

```text
1. staging 表 schema；
2. dry-run summary；
3. commit endpoint 或脚本；
4. rollback endpoint 或脚本；
5. 重复数据处理规则；
6. 导入证据和错误报告。
```

## 7. 内容脑字段位置

P6 内容脑 v0 的标签、主题、风险、产品意图、内容摘要等字段应落在帖子级或媒体级，而不是账号快照级。

优先使用：

```text
vkpi_industry_account_posts
vkpi_industry_post_media
```

不要直接把帖子级内容标签塞进：

```text
vkpi_industry_account_snapshots
```

账号快照只适合保存账号在某个日期的粉丝、互动、简介、平台状态等账号级指标。内容理解属于 post/media 事实，应支持一账号多帖子、多媒体、多次重新分析。

## 8. v5.3.1 架构层

### 8.1 身份与权限层

复用：

```text
users
staff
permissions
backend/app/core/permissions.py
backend/app/services/system/staff.py
```

后续新增的是 V-KPI staff profile 或预算范围，不新增员工主表。

### 8.2 业务事实层

复用并继续扩展：

```text
kols
orders
vkpi_projects
vkpi_project_stage_events
vkpi_kol_pool
vkpi_cost_ledger
vkpi_kpi_ledger
vkpi_sales_attributions
```

P1 的 `vkpi_ai_cost_ledger` 是 AI/provider 成本 ledger，不能替代现有业务成本表 `vkpi_cost_ledger`。

### 8.3 Memory 层

P3 才新增 Memory v0。Memory 表用于保存稳定业务记忆、KOL 历史、产品匹配、失败原因、人工反馈和复盘结果。

Memory 不应该在 P1 抢跑，也不应该被伪装成推荐表。

### 8.4 推荐层

P4 推荐 v0 聚焦三场景：

```text
1. 产品找 KOL；
2. KOL 找产品；
3. 项目/员工下一步动作建议。
```

推荐层读取 Memory、项目、KOL、内容、成本状态，但 P4 自己不负责建立成本守门。成本守门已在 P5 前置完成。

### 8.5 内容脑层

P6 读取帖子和媒体事实，产出内容标签、产品意图、风险、品牌相关性和可复用素材判断。

字段优先落到 `vkpi_industry_account_posts` 和 `vkpi_industry_post_media`。

### 8.6 异常与学习层

P7 负责 alerts，P10 负责 Learning Loop。

异常检测先给出明确、可审计的规则；学习闭环再把人工处理结果写回 Memory 或推荐权重，不在第一版做不可解释的全自动策略。

## 9. 第一批落地顺序

v5.3.1 从两步开始：

```text
Step 1: 文档修订入仓
Step 2: P1 技术校准代码包
```

Step 1 只新增文档，不改业务代码。

Step 2 才开始最小代码改动：

```text
migrations/057_vkpi_ai_cost_budget.sql
backend/app/services/vkpi/llm_gateway.py
backend/app/services/vkpi/budget_guard.py
backend/app/api/routers/vkpi_budgets.py
backend/app/main.py
```

Step 2 不做前端预算页面。BudgetMonitorPage 放到 P5。
