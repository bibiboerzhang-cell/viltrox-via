# V-KPI Button Truth Audit + Data Lineage Audit

时间: 2026-05-15 06:30 CST  
工作目录: `/Users/bibiboer/Documents/V-KPI——marketing`  
分支: `codex/vkpi-cleanup-d7`  
HEAD: `20cd80d fix(p4): harden content list actions`  
性质: 全项目静态扫描 + 既有动态 QA 汇总；只输出报告, 不改功能代码。

## 0. 当前整体进度

| 项目 | 当前状态 | 证据 |
|---|---|---|
| 后端运行 | 正常 | `GET /health` 返回 `status=ok`, `git_sha=20cd80d...`, `client_matches_server=true` |
| 前端运行 | 由 8102 静态 bundle 提供 | 5173 未监听, 当前以 backend static bundle 为准 |
| 最新测试面 | 稳定 | P4 Step35: matched smoke `11/11`, `npm run build` PASS, pytest `85 passed + 5 subtests` |
| 当前工作树 | 未收口 | `43` 条脏改: `27 M`, `16 ??` |
| 纯净包 | 已生成 | `/Users/bibiboer/Downloads/vkpi-p4-clean-package-2026-05-15.zip` |
| 当前 P4 主线 | 治理/真实性 QA | 已完成 mutation safety audit, Daily Top100 source diagnostics, Data Quality button audit, Media loaded-window truth |

### 阶段判断

P4 目前不是“更多功能没做完”的状态, 而是“已有功能需要按真实使用标准治理”的状态。

当前主问题按优先级排序:

1. 危险写操作缺统一确认/审计/回滚策略。
2. Data Analysis / Socialinsider 对标区存在本地聚合和 beta 口径, 需要明确标注, 不是全部后端 drilldown。
3. 媒体/帖子链路已有真实数据窗口, 但仍需浏览器路径验证: 原帖打开、视频播放兜底、单帖分析、全量加载语义。
4. Daily Top100 候选源已经有真实数据, 旧 `0/11` 是历史口径; 当前 DB 是 `2/2` active staff 覆盖, 未来员工新增需要 provisioning 策略。
5. 工作树仍有 43 条脏改, 影响 release/tag/团队交接可信度。

---

## 1. 扫描范围与原始规模

| 扫描项 | 数量 |
|---|---:|
| 前端按钮/操作命中 | 764 hits / 73 files |
| 主要前端 page 文件 | 61 |
| 后端写接口 | 296 |
| 后端读接口 | 293 |
| 后端 router 文件 | 50 |
| 既有 mutation safety 静态 P0 | 81 |
| 既有 mutation safety 静态 P1 | 171 |
| 既有 mutation safety 静态 P2 | 44 |

前端操作命中最多的文件:

| 文件 | 命中数 | 说明 |
|---|---:|---|
| `frontend/src/components/vkpi/panels/KolPoolPanel.tsx` | 50 | KOL 池搜索、导入、富化、链接主表 |
| `frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx` | 38 | 账号抽屉多 tab / 媒体 / 互动 / audience |
| `frontend/src/components/vkpi/pages/DataQualityPage.tsx` | 28 | 数据质量处理动作 |
| `frontend/src/components/vkpi/pages/settings/SettingsControlPanels.tsx` | 26 | 设置开关、预算、平台抓取 |
| `frontend/src/components/vkpi/pages/data-analysis/tabs/HomeTab.tsx` | 26 | 数据分析首页、媒体窗口、详情入口 |
| `frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx` | 25 | Socialinsider 风格分析面板 |
| `frontend/src/components/vkpi/pages/AttributionPage.tsx` | 25 | Shopify/Amazon/手动归因 |
| `frontend/src/components/vkpi/pages/ReportsPage.tsx` | 21 | PDF/CSV/周报/KPI rollup |
| `frontend/src/components/vkpi/pages/ProjectsPage.tsx` | 20 | 项目创建、阶段流转、成本、附件 |
| `frontend/src/components/vkpi/pages/analytics/OutreachTables.tsx` | 20 | Daily Top100 / outreach suggestion 动作 |

后端写接口最多的 router:

| Router | 写接口 | 读接口 | 备注 |
|---|---:|---:|---|
| `admin.py` | 37 | 25 | 非 V-KPI 通用 admin, P0 较多 |
| `system_admin.py` | 28 | 22 | 系统配置/API token/运行时操作, 高风险 |
| `intelligence.py` | 20 | 26 | VIA/智能模块, 多个 operational mutation |
| `vkpi_operations.py` | 18 | 15 | V-KPI team/offboarding/budget/cron |
| `vkpi_industry_automation.py` | 13 | 12 | 行业数据/账号抓取/自动化 |
| `vkpi_kol_links.py` | 12 | 8 | 短链创建、归档、健康检查 |
| `vkpi_evidence_assets.py` | 11 | 9 | 项目附件/沟通/发货/内容证据 |
| `kol_ops.py` | 11 | 6 | KOL 搜索、分析、删除、promote |
| `commerce.py` | 10 | 13 | Shopify/订单/结算 |
| `vkpi_projects.py` | 10 | 2 | 项目创建/更新/阶段/删除 |

---

## 2. Button Truth Audit 总表

分类定义:

- 真按钮: 有明确前端事件、调用真实 API、后端有真实 DB/service 读写, 当前已有 smoke/QA 或代码链路清楚。
- 半真按钮: 会调用 API 或改变 UI, 但只完成部分工作流, 或只记录请求, 或本地聚合/前端状态, 不是完整后端业务闭环。
- 假按钮: 静态扫描未发现有效业务入口, 或表现为可操作但实际无数据/无动作。当前主要集中在 beta 展示/空态而非核心业务按钮。
- 危险按钮: 会改权限、预算、抓取、删除、导入、回滚、成本、外部 API 调用或大量数据, 需要权限 + 确认 + 审计 + 回滚/补偿策略。

| 模块/页面 | 主要按钮/入口 | 当前真实性 | 主要证据 | 主要缺口 |
|---|---|---|---|---|
| 登录/登出 | 登录、退出 | 真按钮 | auth service + 当前浏览器登录链路可用 | 仅需固定测试账号与密码策略文档 |
| 顶部栏 | 搜索、日期范围、刷新、导出 PDF/CSV、生成周报、切换员工视角 | 真/半真混合 | `VkpiTopbar`, `exportVkpiReport`, `generateWeeklyReport`; reports smoke 已有 | 日期范围影响部分页面是前端过滤/加载窗口, 不是所有图表后端聚合; 生成/导出需 endpoint 级 QA |
| 管理主控 | KPI 卡片、证据抽屉、员工/Profile/项目打开 | 真按钮 | `MetricCard`, `useMetricEvidence`, `vkpi.lineage-api`, `/drilldown` | 部分 fallback rows 仍存在; 空数据时需要更清晰说明 |
| 红人搜索/查重/认领 | 搜索 URL/handle、抓取、认领、补录、保存、查看全部、消息查看全部 | 真/半真混合 | KOL lookup + claim/update + profile drawer; 右侧消息/近期内容来自 profile/post rows | 右侧“查看全部”如果没有完整列表页是半真; 头像/媒体/视频需继续 QA; KOL 详情点击入口需要更明显 |
| KOL Pool | 搜索、刷新、一键导入、enrich、链接到主表、promote | 真按钮 | `kolPool-api.ts`, `KolPoolPanel.tsx` | 部分导入数据字段来自 CSV/Apify, 适配度/平均播放可能缺真实抓取补齐 |
| 项目跟进 | 创建项目、多选产品、阶段推进、附件/沟通/内容/条款/发货、删除 | 真按钮 + 危险按钮 | `ProjectsPage`, `ProjectDetailDrawer`, `vkpi_projects.py`, `workflow_evidence.py` | 删除/阶段推进需要确认文案一致; 附件 OCR/快递识别属于后续增强, 当前是记录/上传闭环 |
| 短链中心 | 创建短链、暂停、归档、健康检查、复制 | 真按钮 + 危险按钮 | `LinkTable`, `link_center.py`, `archiveMarketingLink` | 归档/暂停需要确认和回滚说明; 健康检查需显示失败原因和 last_checked |
| 销售归因 | Shopify sync/backfill、Amazon upload/import、手动归因 | 真按钮 + 危险按钮 | `vkpi_attribution_metrics.py`, `AttributionPage`, upload API | 导入/回滚/重复处理需要明确确认、审计和撤销策略 |
| 成本台 | 登记成本、更新、审批、作废、证据 | 真按钮 + 危险按钮 | `CostsPage`, `vkpi_costs.py`, `approveMarketingCost`, `voidMarketingCost` | approve/void 需要确认、理由必填、回滚/补偿说明; 成本 ledger drilldown 还需统一显示来源 |
| 产品作战/推荐 | 创建产品、导入 KOL 池、推荐、候选动作 | 真/半真混合 | `ProductRecommendationPanel`, `runProductRecommendations`, `RecommendationOutcomeTable` | 推荐动作真写 DB, 但后续 outcome 和项目闭环仍需 UI 化; score 解释需来源 tooltip |
| 数据分析/Socialinsider 区 | Account add/refresh、Filters、Content Pillars、Download、tabs、Post detail、Top/All、Open original | 半真偏真 | `industry-data` API, Step33/34 loaded-window contract, `SourceTooltip` | Filters/Pillars/Compare/Download 多为本地视图或 beta; “All”只是当前 500-row loaded window; 视频播放兜底需 browser QA |
| Sentiment/Topic Tracking/Pillars | tab 切换、图表查看、content intelligence | 半真 | 有 comments/sentiment/pillars 后端和部分面板 | 还不是 Socialinsider 级聚合; 当前应标 beta/本地聚合, 不应当作 P3 阻塞 |
| Daily Top100 | 刷新口径、生成 Top100、认领、建项目、忽略 | 真按钮 + 半真治理 | Step29/30: suggestions=96, digest items=99, current staff coverage 2/2 | 员工新增 provisioning 规则未收口; 认领/建项目/忽略需确认和 audit 验证 |
| 数据质量 | 重新检查、已处理、忽略、指派、重检、补证据、重新打开 | 真/半真混合 | Step31/32: action log + audit + smoke PASS | 重检/补证据只是记录请求, 不是自动修复 worker; 已处理/忽略已加确认但仍需 browser QA |
| 员工平台/Channels | 绑定账号、同步、视图切换 | 真/半真混合 | `ChannelsPage`, `bindEmployeeChannel`, `syncEmployeeChannel` | 需要员工真实账号 E2E; sync-now 是真实 API 但结果可观测不足 |
| 活动预算/Campaigns | 创建 campaign、关联项目、预算池、offboarding | 真按钮 + 危险按钮 | `CampaignsPage`, `vkpi_operations.py` | offboarding / budget pool 是高风险; 需要强确认、审计、回滚 runbook |
| 系统设置 | feature flags、平台抓取、预算、provider probe、comment alerts、preferences、notifications、staff invite | 真按钮 + 危险按钮 | `vkpi_settings.py`, `SettingsControlPanels`, `SettingsAdminCards` | 抓取开关/预算/feature flag 需要统一确认、变更摘要、audit visible; UI 已从密集卡片改进过但需无痕/浏览器验证 |
| 审计页 | audit overview/export/settings changes | 真按钮 | `vkpi_audit.py`, `AuditPage` | 审计只读, 但导出审计本身需要记录 export log |
| 反馈 | 提交反馈、状态更新 | 真按钮 | `FeedbackWidget`, `vkpi_feedback.py` | 需要员工内测反馈归档和通知流程 |

### 当前没有证据支持“全局都是假按钮”

静态和既有动态 QA 显示: 核心按钮大多已经接真实 API。真正需要治理的是:

- 危险按钮缺确认/理由/可回滚设计。
- 半真按钮没有明确标注 beta/本地聚合/只记录请求。
- 部分 “查看全部/Download/Compare/Content Pillars” 是前端视图能力, 不是完整后端聚合服务。

---

## 3. Data Lineage Audit 总表

| KPI/数字区域 | 数据来源 | 当前真实性 | Drilldown | 主要缺口 |
|---|---|---|---|---|
| Dashboard 总 KPI: 播放量/成本/GMV/新增 KOL/已发布内容/进行中项目 | `/api/marketing/dashboard`, `vkpi_metric_values`, `metric_lineage_store`, 项目/短链/成本/归因表 | 真 | 部分支持: `metricValueId`, `drilldownUrl`, `useMetricEvidence` | fallback evidence 仍存在; 每张卡的 source tooltip 不够统一 |
| KPI Ledger / PDF 附录 | `vkpi_kpi_ledger`, `vkpi_metric_source_rows`, `pdf_renderer.py` | 真 | 支持 source rows appendix | UI 端 lineage 可视化还不统一 |
| Data Analysis 账号 KPI: Followers/Posts/Views/Engagement | `vkpi_industry_accounts`, `vkpi_industry_snapshots`, `vkpi_industry_posts` | 真/半真 | 部分 SourceTooltip, 部分无 drilldown | 有些是当前 loaded window/快照, 不是完整历史; 需标口径 |
| Data Analysis 趋势/图表 | posts/snapshots 前端聚合 | 半真 | 部分无 drilldown | Socialinsider 级后端聚合未完成, 建议标 beta 而非重做 |
| Media 列表 count | `listIndustryPosts(limit=500)`, Step34 loaded-window contract | 真 | 单帖 drawer 支持 | 500-row window 不是全历史; 需要分页/cursor 才能宣称全量 |
| 单帖指标: likes/comments/views | `vkpi_industry_posts` / crawler raw fields | 真/半真 | PostDetailDrawer 部分支持 | 不同平台字段映射仍需 live QA; 视频/图片代理 browser QA 未全部闭环 |
| Sentiment / Topic / Pillars | comments + sentiment/pillars service + LLM gateway | 半真 | 局部 | 当前只是部分接入, 不是完整 SI 级聚合和交互 |
| Daily Top100 候选数/覆盖率 | `vkpi_outreach_suggestions`, `vkpi_staff_outreach_digests`, `vkpi_staff_outreach_digest_items` | 真 | 状态页/endpoint 支持 | 员工新增后 provisioning/eligibility 规则要写清; UI 需显示当前 active staff 而非固定 11 |
| KOL Pool 粉丝/平均播放/互动率/适配度 | import CSV + Apify/Youtube/TikTok enrich + `vkpi_kol_pool` | 半真偏真 | 当前缺统一 drilldown | 粉丝可从导入/抓取来; 平均播放/互动率/适配度依赖真实抓取和模型评分, 需显示 data_status/source |
| KOL 详情画像/近期内容/消息 | `kol_claims_profile.py`, snapshot/posts/messages/projects/links/costs | 真/半真 | 部分 | 头像/媒体链接/近期内容已可读, 但“查看全部”与播放兜底仍需 QA |
| 项目成本/GMV/ROI | costs + attribution + project tables | 真 | 成本证据和 attribution source rows 部分支持 | ROI 公式/口径需要统一 SourceTooltip |
| Data Quality counts | `vkpi_data_quality_*`, checks service | 真 | issue rows 支持 | 修复动作结果要和实际修复 worker 区分 |
| Provider/API status | provider probe + settings table + env/API readiness | 真/半真 | 无业务 drilldown | probe 是实时或轻量测试, 不等于所有平台抓取成功; 应显示 last_test_status/last_test_at |
| Audit overview | audit tables | 真 | audit list 支持 | 静态 audit 不等于每个 service 一定审计; 需动态 mutation QA |

---

## 4. 操作治理审计: 权限 / 确认 / 审计 / 回滚

| 操作族 | 权限 | 确认 | 审计 | 回滚/补偿 | 判定 |
|---|---|---|---|---|---|
| 项目创建/阶段推进/附件 | `require_tab`, scope | 部分 | workflow/audit service 有线索 | 阶段可再推进, 非完整 undo | 真按钮, 治理中等 |
| 项目删除 | 有权限 | 有确认线索 | service audit 需动态验 | 软删/恢复需确认 | 危险按钮 |
| 成本 approve/void | manager/write/admin gate | 不完全统一 | cost service/audit 需动态验 | void 是补偿, 非 undo | 危险按钮 |
| 短链 pause/archive/health | 有权限 | 不完全统一 | 需动态验 | archive/pause 可补偿 | 危险按钮 |
| 归因 import/backfill/upload | 有权限 | 不完全统一 | 需动态验 | 回滚策略不统一 | 危险按钮 |
| 设置 feature/platform/budget | admin gate | 目前主要靠用户操作, 确认不统一 | settings change log 有线索 | 无统一 rollback, 需变更历史恢复 | 危险按钮 |
| provider probe / crawl refresh | read/write/admin 视接口而定 | 不统一 | 需动态验 | 无回滚, 但可重试 | 危险/半危险 |
| Data Quality resolve/ignore/reopen | manager/write gate | Step32 已补确认/重开 | action log + audit | reopen 支持 | 真按钮, 已治理一部分 |
| Daily Top100 claim/create/dismiss | 有权限 | 不完全统一 | service audit 需动态验 | dismiss/claim 需要撤销策略 | 真按钮, 治理待补 |
| KOL claim/update/delete | 有权限/scope | 删除需确认 | kol audit 有线索 | 删除/claim 需要恢复路径 | 危险/真混合 |
| 员工权限/invite/offboarding/API tokens | admin/system admin | 不完全统一 | system audit 需动态验 | 高风险, 需强确认和 runbook | 高危险 |
| 导出 PDF/CSV/周报 | 有权限 | 一般不需要强确认 | export log 需验证 | 可重跑 | 真按钮, endpoint QA 待补 |

### 关键事实

`docs/audits/2026-05-14-backend-mutation-safety-audit.md` 已确认后端写接口静态扫描:

- 总写接口: 296
- 静态 P0: 81
- 静态 P1: 171
- 静态 P2: 44

这不是说 81 个漏洞已经确认, 而是说这些端点应该优先做动态验证。Router 层看不到 audit 不等于 service 层没 audit。

---

## 5. 抽屉详情/表格字段真实性

| 组件 | 字段/详情 | 当前真实性 | 缺口 |
|---|---|---|---|
| `ProjectDetailDrawer` | 项目、events、links、sales_attributions、costs、附件 | 真/半真 | 部分 action 需要确认; 附件 OCR/快递识别是后续增强 |
| `KolProfileDrawer` | KOL 画像、项目、短链、成本、KPI、推荐、messages、content posts、assets | 真/半真 | 头像/媒体 fallback 和“查看全部”需要 browser QA; 权限 scope 需真实员工账号验证 |
| `AccountDrawer` | Summary/Content/Engagement/Views/Audience/Pillars/Compare | 半真偏真 | 账号详情来自真实抓取表, 但多个 tab 是本地/窗口聚合; 需标 beta/source |
| `PostDetailDrawer` | 单帖媒体、指标、原帖、分析 | 半真偏真 | 原帖/视频播放兜底/媒体代理需要浏览器 QA |
| `EvidenceDrawer` | KPI source rows | 真 | fallback rows 需要明确“回退证据” |
| `LinkDetailDrawer` | 短链点击/订单/归因 | 真 | archive/pause action 治理待补 |
| `StaffProfileDrawer` | 员工项目/KPI/权限 | 真/半真 | 多员工真实账号 E2E 未完成 |
| `DataQualityPage` table | issue rows/action buttons | 真 | 重检/补证据是请求记录, 不是自动修复 |
| `OutreachTables` | suggestions/digest/candidate rows | 真 | claim/create/dismiss 需确认、审计动态验 |
| `KolPoolPanel` table | imported KOL fields | 半真偏真 | avatar/avg views/engagement/fit score 取决于 enrich/live crawl; 需显示 source/data_status |

---

## 6. 当前最重要结论

1. **P4 的核心不是继续堆功能, 是把现有功能治理成可解释、可追溯、可回滚。**
2. **Data Analysis 当前不能按 Socialinsider 级完成度衡量。** P4 收口标准应是团队可用、真实数据、关键操作安全; Socialinsider 级聚合是 P5。
3. **危险按钮治理优先级高于新图表。** 预算、抓取、删除、导入、成本、员工权限、offboarding 这些动作必须先有一致确认/审计/回滚策略。
4. **KPI lineage 已经有后端基础, 但前端展示不统一。** `SourceTooltip` 存在, 但还没有覆盖所有 KPI/表格字段/抽屉详情。
5. **Daily Top100 后端候选源不是空。** 当前 blockers 为 0, 问题转成员工 provisioning 和浏览器口径显示。
6. **媒体功能进入“真实但 UX/兜底不足”阶段。** loaded-window truth 已修, 下一步是浏览器 QA 和单帖播放/原帖/分析闭环。

---

## 7. 接下来 20 步明细方案

原则: 每一步只处理一个模块, 每一步都有匹配测试, 不再混多个方向。

| Step | 模块 | 目标 | 交付物 | 匹配测试/验收 |
|---:|---|---|---|---|
| 36 | Git 状态冻结 | 当前 43 脏改分类, 标出功能/文档/包/临时文件 | `docs/audits/*dirty-classification*` 更新 | `git status --short` 分类清楚, 不做大清理 |
| 37 | Button Truth 动态验证 P0-A | 只验证 Settings: feature flag/platform/budget/provider probe | 动态 QA 报告 | admin 成功, employee 拒绝, audit/settings change 可查 |
| 38 | Button Truth 动态验证 P0-B | 只验证 Costs: approve/void/update | 动态 QA 报告 | manager gate, reason/confirm, void 不计入 totals |
| 39 | Button Truth 动态验证 P0-C | 只验证 Projects: create/move/delete/upload | 动态 QA 报告 | project stage/event/audit/source rows 一致 |
| 40 | Button Truth 动态验证 P0-D | 只验证 Links/Attribution: archive/pause/health/import/upload/backfill | 动态 QA 报告 | archive/pause 可补偿, import 有防重复/审计 |
| 41 | KPI SourceTooltip 标准化设计 | 只出设计, 不改全量 | `docs/specs/kpi-source-tooltip-contract.md` | 每个 KPI 必含 source/status/capturedAt/drilldown/口径 |
| 42 | Dashboard KPI lineage patch | 主控 KPI 卡片统一来源说明 | 代码 + smoke | metric card hover/click 可解释, smoke 覆盖 source_count |
| 43 | Data Analysis beta 口径标识 | 给本地聚合/loaded-window/窗口数据打明确标签 | 代码 + smoke | 页面显示“本地聚合/窗口数据/Beta 口径” |
| 44 | Media browser QA | 用真实浏览器验证: Top/All、原帖、单帖、视频/图片 fallback | browser QA 报告 | 截图/路径/失败项清单 |
| 45 | Media single-post action closeout | 单帖分析、打开原帖、下载/播放兜底只处理一个 | 代码 + smoke/browser | 不再出现可点但无效果按钮 |
| 46 | Daily Top100 provisioning | 多员工新增后 eligibility/coverage 规则文档和状态显示 | 文档 + 小 patch | 新员工不自动算入覆盖, UI 解释 active/eligible |
| 47 | Daily Top100 actions QA | claim/create_project/dismiss 动态验证 | QA 报告 | 无重复分发, audit 可查, 可恢复/补偿规则明确 |
| 48 | Data Quality browser QA | Step32 的确认/分组/reopen 浏览器复验 | browser QA 报告 | resolve/ignore/reopen 真实可见 |
| 49 | Export QA | PDF/CSV/weekly endpoint 真实验证 | QA 报告 | 文件可下载, 内容来自真实 DB, export audit 记录 |
| 50 | Permission E2E | 两个真实员工账号互看/manager 切换验证 | E2E 报告 | 自己/团队/全部 scope 和前端切换一致 |
| 51 | Audit visibility | 把关键业务 audit 在 AuditPage 中查到 | QA 报告 | 每类 mutation 至少 1 条 audit 可检索 |
| 52 | Rollback/compensation policy | 为设置/预算/抓取/导入/成本/删除定义恢复路径 | `docs/runbooks/rollback-policy.md` | 每个危险动作有恢复说明 |
| 53 | Worktree cleanup | 分批提交或归档 43 条脏改 | Git 清理报告 | docs/package 临时文件分离, 功能改动可 review |
| 54 | Clean package refresh | 重新生成纯净包 | zip + release notes | 密钥/缓存/大文件扫描 PASS |
| 55 | P4 internal handoff | 给团队内测包 + 已知问题 + 使用路径 | handoff doc | 员工按路径能完成 KOL/项目/媒体/周报基础流 |

---

## 8. 下一步推荐

不要下一步继续加新功能。建议从 Step36 开始:

1. **Step36: Git 状态冻结。** 先把 43 条脏改分类,避免后续回滚困难。
2. **Step37: Settings 动态验证。** 它是最高风险按钮集中区: 抓取开关、预算、feature flag、provider probe。
3. **Step38: Costs 动态验证。** 成本 approve/void 直接影响 ROI/财务口径。

这三步完成后,再决定是否进入 Media UX 或 KPI lineage patch。

