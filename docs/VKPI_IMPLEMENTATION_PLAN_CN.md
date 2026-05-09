# V-KPI Marketing 落地执行方案

来源文件：`/Users/bibiboer/Downloads/files (2).zip`
目标项目：`/Users/bibiboer/Documents/V-KPI——marketing`
整理日期：2026-05-06

这份文档把外部 KPI Console PRD 整理成适配当前 V-KPI 后端的工程执行方案。重点不是复述 PRD，而是明确：当前代码能复用什么、不要照搬什么、先做什么、改哪些文件、怎么验收。

## 1. 产品边界

V-KPI 是公司内部营销/KOL/KPI 管理系统，不再是 VIA 面向创作者的公开产品。

核心主流程：

```text
员工 -> KOL -> 项目 -> 自建短链 -> 点击 -> Shopify/Amazon 销售 -> 成本 -> KPI -> 管理决策
```

要从根源上切开：

| 保留 | 不再作为核心 |
| --- | --- |
| 员工账号、权限、KOL、项目、短链、归因、KPI、管理层看板 | VIA 猫图、创作者公开页、投稿奖励、share card、旧 mockups、旧 creator-facing 流程 |

## 2. 外部 PRD 哪些是有价值的

| 模块 | 是否保留 | 原因 |
| --- | --- | --- |
| Command Center 管理层大屏 | 保留 | 管理层要看 GMV、成本、ROI、员工表现、漏斗和异常。 |
| Discover KOL 搜索 | 保留 | KOL 去重、绑定、画像、联系方式是核心入口。 |
| Pipeline 项目跟进 | 保留 | KOL 合作必须有明确状态、时间线、负责人和卡点。 |
| Analytics 产品/竞品分析 | 分阶段保留 | 有价值，但不能阻塞 MVP。 |
| Channels 员工平台绑定 | 分阶段保留 | 有价值，但全平台 OAuth 不应该第一阶段全做。 |
| Settings API/模型/费用 | 保留 | Apify、Claude、Gemini、OpenAI 成本必须可控。 |
| Watermark 水印 | 保留 | 内部敏感页面截图泄露需要可追溯。 |
| Auto-release 自动释放 | 保留 | 防止员工囤积 KOL 不跟进。 |
| Net ROI | 保留 | KPI 不能只算 GMV，要算净贡献。 |

## 3. 外部 PRD 不能直接照搬的地方

| PRD 设计 | 当前决策 | 原因 |
| --- | --- | --- |
| 新建 `employees` 表 | 复用现有 `users` / `staff` / permissions | 避免两套员工身份系统。 |
| 新建通用 `projects` 表 | 使用当前 `vkpi_projects` | 当前已经有 V-KPI 项目表，且包含 Shopify/Amazon 字段。 |
| 新建 `orders` 表 | 复用现有 `orders`，新增 V-KPI attribution ledger | Shopify/order 基础已经存在。 |
| 23 张表全量照抄 | 只保留 `vkpi_*` 控制层，再桥接旧表 | 降低迁移风险。 |
| 切换 Next.js 独立栈 | 暂不切换 | 当前项目已经是 FastAPI + React/Vite，换栈会拖慢。 |
| 第一阶段全平台 OAuth | 后置 | IG/TT/YT/XHS/BILI/Weibo 一次全做太重。 |
| 大规模粉丝列表重叠 | 改成公开安全的聚合/向量/标签估算 | 平台限制和合规风险高。 |
| 每个页面渲染都写 DB | 只记录敏感页、导出、财务、联系方式访问 | 否则 `watermark_renders` 写入量会爆。 |
| 前端可读 API key | 禁止 | 前端只能写入、测试、看 mask，不能读取完整 key。 |

## 4. 当前后端可复用资产

当前项目不是空白项目，已经有很多可以直接复用。

| 现有层 | 文件/模块 | V-KPI 用途 |
| --- | --- | --- |
| App role 分离 | `backend/app/main.py` | 保留 admin/internal 路由挂载方式。 |
| 员工权限 | `backend/app/core/permissions.py`、`backend/app/services/system/staff.py` | 给 V-KPI 增加/保留 tab 权限。 |
| KOL ops | `backend/app/api/routers/kol_ops.py`、`backend/app/services/kol/*` | KOL 搜索、画像、dossier、内容分析。 |
| 活动/时间线 | `backend/app/api/routers/activities.py`、`migrations/021_activities.sql` | 项目 timeline、员工操作记录。 |
| Commerce/orders | `backend/app/services/commerce/orders.py`、`orders` | Shopify 订单和销售来源。 |
| Platform ingest | `platform_ingest_events` | Amazon 报表、平台指标、导入日志。 |
| AI usage | `backend/app/services/system/ai_usage.py` | API 费用、预算、模型成本。 |
| System admin | `backend/app/api/routers/system_admin.py` | provider/model/settings 控制。 |
| V-KPI core migration | `migrations/023_vkpi_core.sql` | 内部项目、短链、归因、KPI ledger 基础。 |
| V-KPI router | `backend/app/api/routers/vkpi.py` | 当前 API 入口。 |
| V-KPI services | `backend/app/services/vkpi/*` | 继续扩展，不要推倒重写。 |

## 5. 当前 V-KPI 已经具备的基础

| 能力 | 当前状态 |
| --- | --- |
| 架构说明接口 | `GET /api/admin/vkpi/architecture` 已有。 |
| Dashboard 基础聚合 | `GET /api/admin/vkpi/dashboard` 已有。 |
| 项目创建/列表/阶段流转 | `vkpi_projects` + `vkpi_project_stage_events` 已有。 |
| KOL claim 表 | `vkpi_kol_claims` 已有，且有单个 KOL 只允许一个 active claim 的唯一索引。 |
| 自建短链 | `vkpi_links`、`vkpi_link_destinations`、`vkpi_link_clicks` 已有。 |
| 短链跳转 | `GET /go/{slug}` 已有，会记录 click。 |
| 销售归因 | `vkpi_sales_attributions` 已有。 |
| 成本 ledger | `vkpi_cost_ledger` 已有。 |
| KPI ledger | `vkpi_kpi_ledger` 已有。 |
| 告警 | `vkpi_alerts` 已有。 |
| 决策快照 | `vkpi_decision_snapshots` 已有。 |

结论：下一步不是继续想 schema，而是把现有 V-KPI 骨架接到真实业务流、真实权限、真实前端和真实归因。

## 6. 数据模型映射

| 业务对象 | 当前/目标表 | 说明 |
| --- | --- | --- |
| 员工身份 | `staff`、`users` | 复用现有 auth/RBAC。 |
| KOL 主体 | `kols` | 尽量复用现有 KOL identity。 |
| KOL 画像 | 现有 `kol_*` 表 + 必要扩展 | 不重复存一整套 profile blob。 |
| KOL 锁定/释放 | `vkpi_kol_claims` | DB 层强制一个 KOL 只有一个 active owner。 |
| KOL 项目 | `vkpi_projects` | 一个 KOL x 产品 x campaign 的执行单元。 |
| 项目状态事件 | `vkpi_project_stage_events` | append-only，不能静默改状态。 |
| 自建短链 | `vkpi_links` | 替代 Bitly/Geniuslink 的核心层。 |
| 短链目的地 | `vkpi_link_destinations` | 后续支持国家、设备、marketplace routing。 |
| 点击 | `vkpi_link_clicks` | bot score、session、referrer、destination。 |
| Shopify 归因 | `orders` + `vkpi_sales_attributions` | 订单桥接到 KOL/project/staff。 |
| Amazon 归因 | `vkpi_sales_attributions` + import evidence | 保存 campaign/ref、ASIN、marketplace、revenue。 |
| 成本 | `vkpi_cost_ledger` | 产品成本、物流、现金费用、关税、样品。 |
| KPI 输出 | `vkpi_kpi_ledger` | 员工 KPI 和管理层统计统一来源。 |
| 告警 | `vkpi_alerts` | 卡住项目、低 ROI、短链异常、预算异常。 |
| 决策快照 | `vkpi_decision_snapshots` | 日/周/月 dashboard 缓存结果。 |

## 7. 统一主流程

必须强制所有营销/KOL 工作走同一条主流程。

1. 员工输入平台 + handle/URL。
2. 后端先 normalize handle。
3. 3 秒内完成 active claim 检查。
4. 如果 KOL 已被别人 claim，显示 owner、状态、claim 时间、是否可申请 reassign。
5. 如果未 claim，员工可以绑定 KOL 并创建项目。
6. 项目进入 `discovery` 或 `contacted`。
7. 系统为项目生成或绑定 V-KPI 自建短链。
8. 项目经过 contacted、replied、agreed、shipped、received、published、measured、closed/lost/released。
9. `/go/{slug}` 记录点击并跳转 Shopify/Amazon/其他页面。
10. Shopify/Amazon 销售导入后匹配 link/project/KOL/staff。
11. 成本写入 `vkpi_cost_ledger`。
12. KPI ledger 和 Command Center 更新。
13. Decision layer 生成 stalled、low ROI、broken link、unmatched attribution、API spend 等告警。

## 8. 决策闭环

管理层页面不能只是展示数据，要直接告诉下一步做什么。

| 决策问题 | 触发条件 | 系统动作 |
| --- | --- | --- |
| 是否释放或转派 KOL | 超过阈值无有效跟进 | 提醒 lead/admin release/reassign。 |
| 哪些 KOL 今天必须跟进 | contacted/replied 状态过久 | 生成 staff task 或 alert。 |
| 哪些产品不适合继续寄样 | 产品 ROI 低于阈值 | 标记低优先级，建议暂停 seeding。 |
| 哪些 KOL 值得二次合作 | published 后 reach/order 超阈值 | 创建 re-engage 建议。 |
| 哪些订单没归因 | 有销售但无 project/staff | 进入 reconciliation queue。 |
| 哪些链接有问题 | health check 失败或 Amazon 链接失效 | 通知 owner，必要时 pause link。 |
| 是否控制 AI 花费 | provider spend 超过阈值 | 降级模型或暂停非必要任务。 |
| 是否重复联系/重复付款 | mention 来自已合作媒体/KOL | 自动标记 worked-before。 |

## 9. 分阶段执行方案

### Phase 0：清理内部边界

目标：让项目从代码层变成 V-KPI，而不是 VIA public product 的副本。

| 文件/目录 | 工作 |
| --- | --- |
| `backend/app/main.py` | 保留 V-KPI 和 admin 路由，移除旧 VIA 静态资源挂载。 |
| `frontend/src/components/catographer/*` | 删除或彻底断开 VIA 猫图/mascot 组件。 |
| `frontend/public/cat`、`frontend/public/mockups` | 继续保持清理，不再作为 runtime 依赖。 |
| `frontend/src/routes/public/*`、account/redeem | 决定哪些公开 creator route 禁用、隐藏或保留 legacy。 |
| `docs/` | V-KPI 文档和 VIA 文档分开。 |

验收：

- 运行路径不再请求 `/cat/*` 或 `/mockups/*`。
- `.env`、`backend/.env` 等 key 文件不被删除。
- 后端能 import V-KPI router。
- source tree 内没有 `.venv`、`node_modules`、runtime DB、uploads、旧 zip。

### Phase 1：RBAC 和 V-KPI Admin Shell

目标：员工能进入真实 V-KPI admin tab，并且权限隔离正确。

| 文件/模块 | 工作 |
| --- | --- |
| `backend/app/core/permissions.py` | 确认 `vkpi` tab 有 read/write/admin 权限。 |
| `backend/app/services/system/staff.py` | 默认角色权限正确分配 V-KPI 能力。 |
| `backend/app/api/routers/vkpi.py` | 增加 operator/lead/admin 数据范围过滤。 |
| `frontend/src/components/admin/tabs_v2/` | 增加 `VkpiTab` 或拆成 Command/Discover/Pipeline。 |
| `frontend/src/services/admin.service.ts` | 增加 V-KPI API client。 |

验收：

- Admin 能打开 V-KPI 页面。
- Operator 默认只能看自己的 KOL/项目/联系方式。
- Lead/admin 根据权限看团队或全部。
- 所有 V-KPI 写操作都有 actor staff ID。

### Phase 2：KOL Discover、去重、Claim

目标：先解决重复联系 KOL 这个真实公司问题。

| 文件/模块 | 工作 |
| --- | --- |
| `backend/app/services/vkpi/kol_claims.py` | 新增 normalize、dedup check、claim、release、reassign 服务。 |
| `backend/app/services/kol/account_dossier.py` | 复用 KOL profile facts。 |
| `backend/app/services/kol/content_analyzer.py` | 复用/扩展近 50 条内容分析。 |
| `backend/app/api/routers/vkpi.py` | 增加 `/kols/lookup`、claim、release、reassign endpoint。 |
| `migrations/023_vkpi_core.sql` | 只在必要时补字段，不再新建重复 KOL 表。 |

规则：

- URL/handle 必须先标准化。
- Dedup 优先级：platform handle/channel ID、email、existing KOL refs、manual merge refs。
- active claim 阻止其他 operator 冷启动联系。
- lead/admin override 必须写 release/reassign event。
- claim 到期或长期无有效跟进要进入 stalled/release 流程。

验收：

- 已 claim KOL 查询能在 indexed data 下快速返回 owner/status。
- 同一个 KOL 不能创建第二个 active claim。
- release 后 KOL 回到 pool。
- reassign 不丢历史。

### Phase 3：项目状态机和成本 Ledger

目标：把 KOL 工作变成可计量项目。

| 文件/模块 | 工作 |
| --- | --- |
| `backend/app/services/vkpi/workflow.py` | 增加 allowed transition 和 required payload。 |
| `backend/app/services/vkpi/costs.py` | 新增产品、物流、现金、关税、样品成本服务。 |
| `backend/app/api/routers/vkpi.py` | 增加 project detail、costs、ship、receive、publish、close。 |
| `backend/app/api/routers/activities.py` | 复用或桥接成 project timeline。 |
| `migrations/023_vkpi_core.sql` | 只有现有字段不够时才补 shipment 表/字段。 |

主状态：

```text
discovery -> contacted -> replied -> agreed -> shipped -> received -> published -> measured -> closed
```

侧状态：

```text
stalled, released, lost, cancelled
```

验收：

- 每次状态变化都追加 `vkpi_project_stage_events`。
- shipped 必须有 carrier/tracking/product SKU。
- published 必须有 video URL 或平台 post ref。
- measured 能计算 revenue、cost、net ROI、confidence。
- 成本可以先于或后于销售归因录入。

### Phase 4：自建短链完全替代

目标：用 V-KPI link center 完全替代 Bitly/Geniuslink 的常规使用。

| 文件/模块 | 工作 |
| --- | --- |
| `backend/app/services/vkpi/link_center.py` | 扩展创建、更新、pause、health check、bot filtering。 |
| `backend/app/api/routers/vkpi.py` | 增加 update/pause/archive/health endpoint。 |
| `frontend/src/components/admin/tabs_v2/` | 增加短链创建和分析 UI。 |
| 域名/边缘配置 | 将 `go.viltrox.com/{slug}` 指向 `/go/{slug}`。 |

规则：

- 每个链接必须绑定 staff、KOL、project、product、campaign、platform 中可用字段。
- Shopify 链接要自动加 UTM 和 discount code。
- Amazon 链接要保存 ASIN、marketplace、Amazon Attribution campaign/ref。
- 必须有 destination host allowlist。
- 禁止 open redirect。
- 默认 hash IP，不存原始 IP。
- bot click 和 valid click 分开统计。
- slug 唯一性由 DB 强制。

验收：

- `/go/{slug}` 跳转并记录 click。
- 链接可以 pause，但历史不删除。
- 非 allowlist destination 被拒绝。
- click count 分 total/valid/bot。
- 一个项目可有多链接，但可配置每个平台/campaign 一个 canonical active link。

### Phase 5：Shopify 和 Amazon 归因

目标：销售数据回流到员工/KOL/项目。

| 文件/模块 | 工作 |
| --- | --- |
| `backend/app/services/commerce/orders.py` | 将 Shopify orders 桥接到 `vkpi_sales_attributions`。 |
| `backend/app/services/vkpi/attribution.py` | 新增 order/click/link/project/staff 匹配服务。 |
| `backend/app/services/vkpi/amazon.py` | 新增 Amazon Attribution 导入/手动导入服务。 |
| `backend/app/api/routers/vkpi.py` | 增加 import、reconcile、attribution detail endpoints。 |
| `platform_ingest_events` | 记录导入证据、原始来源和失败原因。 |

Shopify 匹配优先级：

1. V-KPI owned link click/session。
2. 项目/KOL/staff 绑定的 discount code。
3. V-KPI 自动生成的 UTM campaign/content。
4. 人工 reconciliation，必须有 evidence。

Amazon 匹配优先级：

1. 项目创建的 Amazon Attribution campaign/ref。
2. 按 campaign/ref 导入 Amazon Attribution report。
3. Amazon Associates tracking ID。
4. 按 ASIN/marketplace/KOL/project 人工导入。

验收：

- Shopify order 能产生一条 confirmed `vkpi_sales_attributions`。
- Amazon report import 可幂等创建/更新 attribution rows。
- 重复 source_ref 不会重复计收入。
- 未匹配 sales 进入 reconciliation queue。
- Dashboard 区分 confirmed 和 estimated revenue。

### Phase 6：KPI Ledger 和管理层 Dashboard

目标：让 dashboard 数据可信、可下钻、能指导行动。

| 文件/模块 | 工作 |
| --- | --- |
| `backend/app/services/vkpi/decision_engine.py` | 扩展 funnel、GMV、cost、ROI、staff leaderboard。 |
| `backend/app/services/vkpi/kpi_ledger.py` | 增加 rollup generation 和 metric definitions。 |
| `backend/app/services/vkpi/alerts.py` | 生成 stalled、low ROI、budget、broken link alerts。 |
| scheduler/worker | 增加 hourly/daily rollup jobs。 |
| `frontend/src/components/admin/tabs_v2/` | 做 Command Center UI，使用真实 endpoint。 |

核心指标定义：

| Metric | 定义 |
| --- | --- |
| New KOLs | 周期内新 claim 数。 |
| Effective touches | 会重置 stalled timer 的有效触达。 |
| Reply rate | replied / contacted。 |
| Agreement rate | agreed / replied。 |
| Ship-to-publish days | published date - shipped date。 |
| Creator GMV | confirmed creator/KOL project sales。 |
| Net contribution | creator GMV - product cost - shipping - cash fee - customs - overhead。 |
| ROI | creator GMV / total invested。 |
| KPI credit | 按员工、项目、阶段权重写入 ledger。 |

验收：

- Dashboard 支持 today/7D/MTD/QTD。
- revenue/cost 可下钻到 evidence rows。
- company-owned channel 默认排除 creator GMV，但 admin 可 toggle。
- staff leaderboard 和 detail page 使用同一套 ledger 定义。
- alerts 是可执行事项，不只是状态灯。

### Phase 7：Analytics 和市场情报

目标：主流程跑通后，再做产品/竞品/搜索分析。

| 文件/模块 | 工作 |
| --- | --- |
| existing intelligence services | 复用已有 BH/product/social scanning 能力。 |
| `backend/app/services/vkpi/market_intel.py` | 新增 product compare、mentions、competitor pool 聚合层。 |
| `platform_ingest_events` | 存 crawl/import source facts。 |
| `frontend/src/components/admin/tabs_v2/` | 做 Analytics UI。 |

顺序：

1. Product compare。
2. 我司产品/品牌 mention tracking。
3. Competitor KOL pool。
4. Search pulse。
5. Trend forecast 最后做，不要在没数据时优先做 LLM 预测。

验收：

- 已合作 KOL/media 自动标记。
- 新推荐 KOL 能一键进入项目。
- competitor pool 有 source/evidence/date。
- baseline 不依赖高风险粉丝列表抓取。

### Phase 8：Channels 和 Provider Settings

目标：接入员工平台数据，并控制 AI/API 花费。

| 文件/模块 | 工作 |
| --- | --- |
| `backend/app/api/routers/system_admin.py` | 尽量复用 provider/model settings。 |
| `backend/app/services/system/ai_usage.py` | 作为 spend 和 budget projection 来源。 |
| `backend/app/services/vkpi/channels.py` | 新增 channel connections 和 metric pull。 |
| `platform_ingest_events` | 存 channel pull snapshots 和失败原因。 |
| `frontend/src/components/admin/tabs_v2/SystemTab.tsx` | 增加 V-KPI settings/provider budget UI。 |

验收：

- API key write-only，接口不返回完整 key。
- Provider test 返回 status、latency、masked metadata。
- budget threshold 能降级/暂停非必要任务。
- OAuth 前，先支持手动 sync/import channel metrics。

## 10. API 合约

### 当前已有

```text
GET  /api/admin/vkpi/architecture
GET  /api/admin/vkpi/dashboard
GET  /api/admin/vkpi/workflow/stages
GET  /api/admin/vkpi/projects
POST /api/admin/vkpi/projects
POST /api/admin/vkpi/projects/{project_id}/stage
GET  /api/admin/vkpi/links
POST /api/admin/vkpi/links
GET  /api/admin/vkpi/alerts
GET  /api/admin/vkpi/kpi-ledger
GET  /go/{slug}
```

### 下一批要加

```text
POST /api/admin/vkpi/kols/lookup
POST /api/admin/vkpi/kols/{kol_id}/claim
POST /api/admin/vkpi/claims/{claim_id}/release
POST /api/admin/vkpi/claims/{claim_id}/reassign
GET  /api/admin/vkpi/projects/{project_id}
POST /api/admin/vkpi/projects/{project_id}/costs
POST /api/admin/vkpi/projects/{project_id}/ship
POST /api/admin/vkpi/projects/{project_id}/publish
POST /api/admin/vkpi/links/{link_id}/pause
POST /api/admin/vkpi/links/{link_id}/health-check
POST /api/admin/vkpi/attribution/shopify/reconcile
POST /api/admin/vkpi/attribution/amazon/import
GET  /api/admin/vkpi/attribution/unmatched
POST /api/admin/vkpi/rollups/run-now
```

## 11. 最小可用 MVP

如果目标是尽快优化公司内部运营，MVP 只做下面这些：

1. 员工登录/RBAC/V-KPI tab。
2. KOL 搜索、去重、claim、release。
3. 项目状态机。
4. 自建短链创建、跳转、点击记录。
5. Shopify attribution bridge。
6. Amazon manual/import attribution bridge。
7. Cost ledger。
8. Command Center：漏斗、GMV、成本、ROI、员工 leaderboard。
9. Alerts：stalled projects、broken attribution、low ROI。

MVP 不要被这些拖住：

- 全平台 OAuth。
- Trend forecast。
- 完美 audience overlap。
- Native app。
- 7 个 UI 页面像素级还原。
- 每一步都用 AI summary。

## 12. 我建议额外补的功能

| 功能 | 为什么需要 |
| --- | --- |
| Reconciliation queue | Shopify/Amazon 一定会出现未匹配订单，需要人工修正入口。 |
| Data quality dashboard | 监控 broken imports、missing UTM、重复 rows、stale metrics。 |
| KOL communication timeline | 员工离职后公司不丢 KOL 关系上下文。 |
| Product inventory/sample liability | 样品很贵，需要知道寄出、丢失、退回、成本。 |
| Content asset library | KOL 视频可二次用于广告、官网、详情页。 |
| Contract/rate terms snapshot | 防止合作交付、费用、出片数量产生纠纷。 |
| Release/reassign dispute log | 防止内部抢 KOL 或囤 KOL。 |
| Staff action SLA | 分析流程哪里卡，不只是看谁 GMV 高。 |
| Link governance | 防止 open redirect 和乱跳外部链接。 |
| Finance export | 财务/审计需要按 staff/project/product 导出 CSV/JSON。 |

## 13. 需要你确认的业务决策

| 决策 | 推荐默认值 |
| --- | --- |
| 官方短链域名 | `go.viltrox.com` 或另一个公司自有子域名。 |
| Amazon 数据来源 | Amazon Attribution report 先做，API 后做。 |
| Shopify attribution window | 默认 30 天，可配置。 |
| KOL claim timeout | contacted：10 天提醒，14 天释放；replied：14 天提醒，21 天释放。 |
| 员工 KPI 基础 | 净贡献 + 阶段动作，不只看 GMV。 |
| 产品成本来源 | 先做 admin 维护 product cost catalog，后续接 ERP。 |
| 公司账号数据 | 默认排除 creator GMV，admin 可 toggle 包含。 |
| 第一批平台 | TikTok、Instagram、YouTube 优先，XHS/BILI/Weibo 后置或 import-only。 |
| AI 默认策略 | 便宜模型先做 extraction，贵模型只做最终 summary。 |
| 敏感水印 | KOL 联系方式、财务、导出、归因详情页面强制。 |

## 14. 工程红线

- destructive cleanup 前先备份。
- 不删除 `.env`、`backend/.env` 等 key 文件。
- API key 不通过 frontend response 返回完整值。
- 未经法务确认，不存 raw follower identities。
- 不做 open redirect。
- manual import 不允许重复计收入。
- 项目状态变化必须写 event。
- claim/release/reassign 必须写审计。
- dashboard 数字必须能追溯到 source rows。

## 15. 前两周执行清单

### Week 1

| 顺序 | 工作 | 验收 |
| --- | --- | --- |
| 1 | 完成 V-KPI 清理边界 | 无 `/cat/*`、`/mockups/*` runtime 依赖，env/key 保留。 |
| 2 | 增加 V-KPI admin frontend tab skeleton | Command/Projects/Links 用真实 API 渲染。 |
| 3 | 加强 V-KPI API 权限 | operator self-scope、admin all-scope。 |
| 4 | 新增 KOL claim service | normalize、claim、release、reassign、唯一 active claim 测试。 |

### Week 2

| 顺序 | 工作 | 验收 |
| --- | --- | --- |
| 5 | 扩展 project workflow | allowed transitions、required payload、append-only timeline。 |
| 6 | 扩展 link center | update/pause/archive、allowlist、health check、bot/valid split。 |
| 7 | 增加 attribution bridge skeleton | Shopify reconcile、Amazon import stub、unmatched queue。 |
| 8 | 做第一版 dashboard rollup | active stages、clicks、revenue、cost、ROI、open alerts。 |

## 16. 验证命令

每批改完至少跑：

```bash
python3 -m py_compile backend/app/main.py backend/app/api/routers/vkpi.py backend/app/services/vkpi/*.py
git diff --check
rg -n "(/cat/|/mockups/|FloatingViaCat|viltrox-shop-vintage|viltrox-hero-lab)" backend frontend -S
```

如果前端依赖已安装，再跑：

```bash
cd frontend
npm run build
```

后端 smoke 目标：

```text
1. create project
2. transition stage
3. create link
4. call /go/{slug}
5. confirm click row
6. add sale attribution
7. add cost
8. confirm dashboard ROI changes
```

## 17. 最终架构判断

外部 PRD 应该作为产品蓝图和 UI 参考，不应该照搬成新系统。

当前项目正确路线是：

```text
保留 FastAPI/Vite 基础设施。
保留 staff、KOL、orders、AI usage、platform ingest 等已有底座。
用 vkpi_* 表作为内部营销控制层。
以自建短链作为归因中心。
Shopify 和 Amazon 都只是收入来源，最终进入同一个 attribution ledger。
KPI 来自可审计 ledger，不来自表格、截图或人工口径。
```
