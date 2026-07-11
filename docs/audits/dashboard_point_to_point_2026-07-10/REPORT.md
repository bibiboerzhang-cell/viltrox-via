# V-KPI 本地版本全点对点核验与智能闭环缺口报告

> 核验日期：2026-07-10（America/New_York）  
> 仓库：`/Users/bibiboer/Documents/V-KPI——marketing`  
> 分支：`codex/dashboard-real`  
> 基线 HEAD：`9bfcd4a944104e6b010a93df50d07ed7e5c44847`  
> 范围：本地前端、真实本地 PostgreSQL、后端 API、导航与按钮、数据呈现、证据回链、任务/worker、测试和 UI 参考一致性  
> 限制：本轮未连接云端生产库、未写业务数据、未触发付费 LLM/抓取任务、未部署云端。

## 1. 最终结论

### 1.1 一句话判断

当前版本已经是一个**覆盖面很广的增长操作系统控制台原型**，但还不是可证明有效的“市场增长大脑”。页面、路由、数据模型和大量 API 已经铺好；真实业务数据、来源证据、执行结果、预测校准和 worker 闭环在本地几乎为空，因此系统现在能展示“应该如何工作”，还不能用本地证据证明“确实会工作、工作后会变聪明”。

### 1.2 是否可以作为今天的云端正式替换版本

**结论：NO-GO。**

可以继续作为本地 UI/交互预览，也可以在修完 P0 后上预发；不建议现在直接替换云端主界面。阻断原因不是单纯美观，而是：

1. SSE 登录令牌进入 URL，访问日志会记录完整令牌。
2. 系统设置向前端展示 API key 前 15 个字符，掩码粒度过松。
3. 本地发布门禁不绿，后端测试环境不可重复，运行版本元数据不对齐。
4. Dashboard 把“接口响应成功”显示为“数据可用”，空库仍会产生过度乐观的状态。
5. 核心的产品、KOL、项目、市场、行动、结果、预测表在本地基本都是 0，无法验收智能闭环。

### 1.3 量化评分

这些分数只用于定位阶段，不代表商业估值。

| 维度 | 分数 | 客观依据 |
|---|---:|---|
| 页面与路由壳 | 82/100 | 17 个主页面均可进入，点击链路未出现前端崩溃 |
| 主流程交互 | 76/100 | 顶栏、弹窗、编辑布局、主题切换等大部分可用；侧栏底部按钮在常见高度不可点 |
| Demo 视觉一致性 | 64/100 | 信息架构接近，但透明度、底色、顶栏、侧栏、动效和层次仍明显不同 |
| API 覆盖广度 | 78/100 | GTM、预测、行动、市场、证据、项目等 endpoint 已广泛存在 |
| 本地真实数据就绪 | 12/100 | 42 张关键表中绝大多数为 0，只有种子/配置类记录 |
| 溯源可信度 | 20/100 | 组件支持链接与 ledger id，但当前 AI Today/市场信号没有可回看的本地原证据 |
| 智能闭环成熟度 | 8/100 | action/outcome/prediction/eval/bet/memory/worker 均无有效本地样本 |
| 工程可发布性 | 45/100 | 前端 369 测试全绿，构建通过；总门禁、后端全测、版本对齐失败 |
| 当前综合阶段 | **高级 Alpha / 内部控制台原型** | 不是普通 CRUD 雏形，但也未达到数据驱动 Beta |

## 2. 核验方法

1. 启动真实本地 PostgreSQL/Redis 环境下的 FastAPI 和 Vite，而不是只看静态 HTML。
2. 使用当前登录态逐项点击 Dashboard 顶栏、侧栏、卡片、弹窗和布局编辑器。
3. 遍历 17 个主业务页面，观察加载、空态、可操作性、接口状态和文案真实性。
4. 只读查询本地 PostgreSQL 的核心业务表、结果表、预测表和偏好表。
5. 检查浏览器控制台和后端访问日志。
6. 执行发布门禁、前端全量 Vitest、后端全量 pytest（含 SQLite 隔离尝试）、前端生产构建。
7. 对照用户提供的 Dashboard Kit/reference 截图检查侧栏、顶栏、透明度、动态层次和布局。

## 3. 关键截图证据

### 3.1 当前 Dashboard

![Dashboard 初始状态](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_point_to_point_2026-07-10/01-dashboard-initial.png)

### 3.2 主题与风格切换

![玻璃深色](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_point_to_point_2026-07-10/04-glass-dark.png)

![仪器浅色](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_point_to_point_2026-07-10/06-instrument-light.png)

### 3.3 布局编辑和模块选择

![布局编辑](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_point_to_point_2026-07-10/15-layout-edit.png)

![模块选择](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_point_to_point_2026-07-10/16-module-picker.png)

### 3.4 证据回链现状

![市场信号下钻](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_point_to_point_2026-07-10/19-signal-drilldown.png)

![AI Today 证据](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_point_to_point_2026-07-10/21-ai-today-evidence.png)

### 3.5 GTM 与系统设置

![GTM Command](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_point_to_point_2026-07-10/22-gtm-command.png)

![系统设置](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_point_to_point_2026-07-10/24-system-settings.png)

### 3.6 参考差距

![侧栏参考与当前对比](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_ui_root_cause_2026-07-10/compare-sidebar-reference-left-current-right.png)

![顶栏参考与当前对比](/Users/bibiboer/Documents/V-KPI——marketing/docs/audits/dashboard_ui_root_cause_2026-07-10/compare-topbar-reference-left-current-right.png)

## 4. 点对点交互结果

### 4.1 顶栏与 Dashboard 控件

| 步骤 | 控件/流程 | 健康度 | 结果 |
|---:|---|---|---|
| 1 | Dashboard 首屏 | 部分健康 | 页面稳定，无 JS 崩溃；本地业务数据近乎为空 |
| 2 | “问一问”浮层 | 健康 | 可开关；未提交问题，避免产生 LLM 成本 |
| 3 | 外观：玻璃/仪器/单色 | 部分健康 | 6 种组合都能切换；风格差异不够强，弹层按钮仍有原生灰底感 |
| 4 | 深/浅主题 | 部分健康 | 状态切换有效；浅色过白、深色过实，玻璃层次不足 |
| 5 | 任务进度中心 | 部分健康 | 可打开，但队列为空；后台轮询请求严重放大 |
| 6 | 帮助 | 不健康 | 可打开，但版本 `v6.14.2`、日期 `2026/05/25` 为硬编码旧信息 |
| 7 | 工作提醒 | 部分健康 | 可打开；全 0，并与“今日该做什么”职责重复 |
| 8 | Report | 部分健康 | 面板完整；核心数据和 PDF 仍待接，当前不是可交付报告 |
| 9 | 通知 | 不健康 | 只有标题和“查看所有”，无实际通知行 |
| 10 | 用户菜单 | 部分健康 | 入口可用；显示“团队 4 人”，团队弹窗和 DB 实际为 2 人 |
| 11 | 系统设置 | 高风险 | 能加载；显示过长 API key 前缀，worker/版本状态异常 |
| 12 | 编辑布局 | 健康 | 编辑态、拖动/缩放控制和删除控制出现 |
| 13 | 添加模块 | 部分健康 | 模块选择器可打开；许多模块实际无数据却统一宣称真实接入 |
| 14 | 侧栏 Collapse | 不健康 | 键盘 Enter 有效，鼠标不可点；按钮中心落在视口底部之外 |
| 15 | 侧栏主题按钮 | 不健康 | 同上，布局溢出导致常见 833px 高度下不可点 |
| 16 | KPI 范围切换 | 部分健康 | All/KOL/公司账号可切；空库仍显示“10/10 可用” |
| 17 | Action Ledger | 部分健康 | 可展开；本地账本 0 条，无法验证执行闭环 |
| 18 | 市场信号下钻 | 不健康 | 弹窗可开；本地 0 条却出现 mentions/证据数和固定来源列表 |
| 19 | V6 Fit 下钻 | 部分健康 | 弹窗可开；0 项，且缺少有效回到 KOL Pool 的工作流 |
| 20 | AI Today 证据 | 不健康 | 弹窗支持视频/来源结构，但当前 0 视频、0 来源、时间与快照为空 |
| 21 | 备忘录 | 部分健康 | 本机存储与后端接口存在；空偏好也显示“账户偏好已保存” |
| 22 | 地图层级/返回 | 部分健康 | 返回 World 可用；KOL/Dealer/热力/Events 四层均 disabled/WAITING |

### 4.2 页面路由遍历

| 步骤 | 页面 | 健康度 | 当前事实与主要缺口 |
|---:|---|---|---|
| 23 | MY KOL | 不健康 | 0 KOL/0 官方账号；出现“暂无 个 KOL”和固定“18.6% 已深析”文案 |
| 24 | KOL Pool | 部分健康 | 页面与筛选壳存在；本地池为 0，不能验证评分/认领/溯源 |
| 25 | KOL 档案 | 不健康 | 无选中对象；空态把 `sessionStorage` key 和事件名暴露给业务用户 |
| 26 | Projects | 部分健康 | 0 项目；主 CTA disabled，无法完成建项到履约链路 |
| 27 | Events | 部分健康 | 0 活动/任务；页面另显示 14 库存，口径需明确 |
| 28 | Shopify | 部分健康 | 连接与归因结构存在；订单、GMV、ROI 本地均未形成事实 |
| 29 | Dealers | 不健康 | 0 经销商/0 定位；不能回答“给哪个 dealer 卖” |
| 30 | Intelligent 问答 | 部分健康 | UI 可用；未触发 LLM，未验证答案证据约束 |
| 31 | 市场之声 | 不健康 | 评论、提及、情绪、观察均为 0 |
| 32 | SKU 360° | 不健康 | 产品表 0，无法形成产品画像或渠道建议 |
| 33 | 创意资产库 | 不健康 | 分析缓存/素材证据为 0，无法验证成品视频推荐 |
| 34 | 回复队列 | 部分健康 | 页面可进入；队列 0，无法验证审批与发布结果 |
| 35 | 发射台 | 不健康 | 无 SKU；六输出/一键推广链路无法运行 |
| 36 | 自治驾照 | 部分健康 | 有 5 条种子驾照；无样本、预测、执行结果，属于规则壳 |
| 37 | 战略台 | 不健康 | bet/prediction/outcome/eval 全空，不能做真实策略比较 |
| 38 | GTM Command | 不健康 | 页面完整但 launch brief/dealer/verdict/outcome 均为 0，worker 未知，多项能力明确 pending |

## 5. 数据真实性核验

### 5.1 本地 PostgreSQL 核心事实

| 领域 | 核心表 | 行数 | 判断 |
|---|---|---:|---|
| 账号 | `users` | 2 | 有本地账号，但团队统计口径不一致 |
| 权限组 | `vkpi_staff_groups` | 0 | 组织分组尚未落地 |
| 产品 | `vkpi_products` | 0 | P2G 起点为空 |
| KOL | `vkpi_kol_pool` | 0 | KOL 发现、匹配、档案无法本地验收 |
| 视频证据 | `vkpi_kol_video_evidence` | 0 | 视频证据链为空 |
| 深析 | `vkpi_analysis_cache` / `vkpi_kol_llm_deep_analysis_results` | 0 / 0 | 内容理解层为空 |
| Fit | `vkpi_kol_fit_snapshot` | 0 | 推荐分数没有本地事实 |
| 项目履约 | `vkpi_projects` / assignments / shipments / content | 0 / 0 / 0 / 0 | 从推荐到交付未形成样本 |
| 公司账号 | daily/channel/post metrics | 0 / 0 / 0 | 四项公司 KPI 无本地数据来源 |
| 经销商 | `vkpi_dealers` | 0 | 无 dealer 选择基础 |
| 市场信号 | sources/mentions/observations/competitor signals | 0 / 0 / 0 / 0 | 外部市场感知为空 |
| 用户评论 | B&H reviews / comments | 0 / 0 | 痛点与口碑为空 |
| 行动 | action inbox / execution ledger | 0 / 0 | 无“建议 -> 执行”记录 |
| 结果 | recommendation outcomes | 0 | 无“执行 -> 效果”记录 |
| 预测 | prediction runs / evals / bet ledger | 0 / 0 / 0 | 无预估与事后校准 |
| 学习 | agent outcome evals / memory facts | 0 / 0 | 无可证明学习沉淀 |
| Worker | `vkpi_worker_heartbeat` | 0 | 本地 worker 未上线或未登记 |
| AI Today | `vkpi_ai_today_hot` | 0 | 当前内容不是本地证据快照 |
| 指标 | `vkpi_metric_values` | 20 | 10 个指标各重复 2 行，全部为 0，状态/置信度为空 |
| 自治种子 | `vkpi_autonomy_licenses` | 5 | 只有配置/种子，不等于自治有效 |
| 模型登记 | `vkpi_model_registry` | 1 | `rule_v0` active，尚无校准证据 |
| 用户偏好 | `vkpi_user_preferences` | 1 | 唯一记录为 `{}` |

### 5.2 最危险的数据语义问题

1. `normalizeDashboardSourceHealth()` 只统计 `_sources.*.ok`，然后生成“10/10 可用”；这实际表示 HTTP/聚合源响应，不表示数据有行、有新鲜度、有置信度。见 `frontend/src/components/vkpi/cockpit/normalizers.ts:826-855`。
2. 市场信号弹窗在无数据时仍硬编码“Google News、Reddit、NewShooter、DPReview、Brand24、Twitter API”。见 `SignalsAllModal.tsx:120-123`。
3. AI Today 底部写“所有结论均可回到 evidence / analysis / market source”，当前同屏却明确显示 0 视频、0 source。见 `AITodayEvidenceModal.tsx:170-225`。
4. 指标表中的 20 行均为 0，`data_status`、`confidence` 为空；不能把这些记录当作已接通数据。
5. 备忘录从后端读到空对象也进入 `saved`，文案“账户偏好已保存”会被理解为已有远端内容。见 `DashboardMemoCard.tsx:71-87`。

### 5.3 云端数据应如何进入本地

不建议直接复制生产库、用户 PII、授权 token 或完整 API key。应增加一条**只读、可重复、可脱敏、带快照版本**的同步链：

1. 云端生成审计快照：产品、KOL、视频证据、项目、公司账号、dealer、市场信号、行动、结果的最小必要字段。
2. 去除邮箱、电话、地址、token、私信正文等敏感内容；保留稳定匿名 ID。
3. 每个快照记录 `snapshot_id`、schema version、生成时间、源环境和行数校验。
4. 本地导入独立 schema 或数据库，不覆盖开发种子和用户偏好。
5. 导入后执行行数、主外键、时间范围、空值、重复、指标重算和页面验收。
6. 同一快照可被测试重复加载，作为 Dashboard、GTM 和闭环回归的固定夹具。

## 6. 智能闭环客观评价

### 6.1 已经有的基础

1. 领域模型覆盖较广：产品、KOL、项目、渠道、市场、推荐、预测、行动、结果、成本、记忆均有表或 endpoint。
2. GTM、策略、自治、发射台、市场之声等页面已经形成产品方向，不再只是 KOL 搜索工具。
3. Action Inbox、执行账本、推荐结果、预测评估、模型登记等名称和结构已经出现，说明闭环所需的“槽位”基本齐全。
4. 证据弹窗支持视频播放、source URL、ledger table/id、历史/关联标签，正确方向已经写进组件契约。

### 6.2 仍未闭环的地方

目前缺失的是**同一决策对象贯穿全链路的可验证记录**。最小闭环应为：

```text
产品输入/上市目标
  -> 版本化市场证据快照
  -> 地区/人群/渠道/KOL/dealer 机会评分
  -> 带假设、预算、风险和置信度的 GTM 方案
  -> 人工批准或拒绝
  -> 可执行任务与责任人
  -> 项目/发货/内容/投放/销售结果
  -> 固定观察窗归因
  -> 预测误差与策略评估
  -> 模型/规则/记忆版本更新
  -> 下一轮建议引用上一轮经验
```

当前本地仅能证明“链路的页面和表大多存在”，不能证明任意一条产品记录走完上述流程。闭环不是再加一个预测卡片，而是让 `product_id + market + channel + decision_id + action_id + outcome_window` 成为贯穿证据、决策、执行和学习的主键体系。

### 6.3 闭环上线的最低样本门槛

在宣称“智能大脑”前，至少用 3 个真实 SKU 做影子运行：

1. 每个 SKU 至少 3 个国家/地区、2 类渠道、20 个候选 KOL、5 个 dealer 候选。
2. 每条建议都有可打开的证据链接、快照时间、评分版本和缺失数据说明。
3. 每个 SKU 至少产生 10 个获批行动、10 个拒绝/修改反馈和 5 个有观察窗的结果。
4. 每次预测保存 `predicted_value`、区间、置信度、实际值和误差。
5. 第二轮建议必须能说明“因上一轮什么结果而改变了什么”。

## 7. 安全、性能与架构问题

### P0-1：SSE token 进入 URL 和访问日志

- 前端 `useEventStreamOrPoll.ts:36-40,66-70` 把 bearer token 附加为 `?access_token=`。
- 后端 `security.py:161-171` 明知 URL token 有 access log/Referer 风险，但仍为 stream 开启。
- 当前 Uvicorn 访问日志已经打印完整请求 URL，因此真实 token 会进入日志。
- 修复：优先使用 HttpOnly、SameSite cookie 鉴权；或短时、一次性、仅 SSE scope 的 ticket；并在反向代理/Uvicorn 层对 query string 脱敏。禁止把长期 JWT 放 URL。

### P0-2：API key 掩码泄露过多

- `secrets_admin.py:35-44` 返回原值前 15 个字符。
- `SettingsPage.fragments.tsx:52-61` 直接展示 `key_mask`。
- 对部分 provider，前缀长度足以暴露账号类型、项目或大量密钥材料。
- 修复：系统健康只返回 `configured: true/false`；确需识别时最多显示不可逆 fingerprint 或末 4 位。轮转接口与查看权限分开，并记录敏感访问审计。

### P0-3：发布门禁不可重复

- 默认 `make verify` 的后端测试收集阶段连接 `.env` 中不可达的 PG `54329`。
- 强制 SQLite 后为 `1004 passed / 682 skipped / 32 failed / 37 errors`，暴露 schema/default/migration 漂移。
- 本地 `BUILD_GIT_SHA` 仍为旧值；手动服务又注入了 12 位 SHA，前端 build-info 是 40 位 SHA，导致 `client_matches_server=false`。
- 修复：提供独立测试 DSN/容器、一次命令迁移、固定 seed；发布脚本只使用完整 40 位 SHA，并在 build 后原子更新 build metadata。

### P1-1：任务轮询请求放大

- `TaskCenterProvider` 前台每 3 秒刷新一次，见 `TaskCenter.tsx:92-95,174-189`。
- `listTasks()` 一次刷新并发请求 10 个 status，见 `tasks-api.ts:92-130`。
- 单开一个页面理论上约产生 **200 个 task 请求/分钟**，访问日志与此一致；另有 progress center、task queue 和其他轮询。
- 修复：新增单个聚合 endpoint，支持 `status=active,recent_terminal` 或游标；SSE 成功时停止固定轮询；页面隐藏后暂停；同一资源用 query cache 去重。

### P1-2：CSS 架构已进入覆盖债务

- `CockpitApp.tsx:11-12` 同时导入 `mockup.css` 与 `cockpit-reference.css`。
- 新的 `cockpit-reference.css` 为 2,585 行，发布 line guard 已失败。
- `frontend/src` CSS 共 355 个 `!important`；主要集中于 reference、project drawer、mockup 补丁层。
- `MockupDashboard` 仍被导入但运行路径实际使用 `DashboardReplicaPage`，存在并行实现残留。
- 修复：确定唯一生产 Dashboard 树；把 tokens、shell、layout、widgets、overlays、responsive 分文件；逐步移除全局 body/Leaflet/旧 mockup 规则和 `!important`。

### P1-3：侧栏底部控件越界

- `CockpitSidebar.tsx:73-75` 固定 `h-screen`，上部导航与下部任务卡/工具栏共同占高。
- footer `flex: 0 1 auto`，但没有保证工具按钮固定可见；在 1728×833 下 Collapse 从 y=824 开始，鼠标中心位于视口外。
- 键盘 Enter 能改变状态，证明 handler 正常，故根因是布局而不是事件绑定。
- 修复：侧栏设三段 grid：`auto minmax(0,1fr) auto`；只让 nav 滚动；底部任务卡增加高度断点，在低高度变成一行摘要。

## 8. UI 参考一致性评价

### 8.1 为什么现在仍显得“黑、实、死”

1. Glass dark token 使用 `--ds-panel: rgba(...,.76)`、`--ds-card: rgba(...,.78)`，后景本身又接近纯黑，最终视觉仍像实色板。
2. Glass light 的 card 是纯 `#fff`，并被 reference CSS 再覆盖为 0.82，导致浅色发白而不是玻璃。
3. 大面积卡片缺少可透出的真实底层内容，`backdrop-filter` 即使存在也没有足够可见差异。
4. 多层旧 CSS 和 `!important` 让 token 不能完整控制所有组件，弹窗、原生按钮、旧 Tailwind 色仍混入。
5. 动态主要是局部 glow/折线/数字；缺少参考稿中连续的流动背景、轻微视差、hover 能量反馈和层级转换。

### 8.2 与参考稿最明显的差距

| 区域 | 当前问题 | 目标 |
|---|---|---|
| 左侧栏 | logo 和导航顺序接近，但间距、任务卡位置、折叠控制、低高度适配不同 | 按参考稿固定比例，nav 独立滚动，任务状态固定底部 |
| 顶栏 | 当前控件更多、更亮、更像工具栏；参考更薄、更安静 | 保留问答、布局、状态、通知、用户，二级功能收进菜单 |
| KPI 区 | 当前颜色流动有所增加，但卡片仍厚重 | 更薄边、更低不透明度、数据线本身承担视觉重点 |
| 地图 | 深浅底图已可切，但业务图层 disabled | 真实 KOL/dealer/event/heat 图层、可筛选 pin、来源/时间可见 |
| 卡片底色 | 纯色和半透明混用 | 所有生产组件统一走 token，不允许局部硬编码白/黑底 |
| 动效 | 主要是装饰性脉冲 | 数据更新、下钻、布局变化、状态改变都有克制且有意义的动效 |
| 密度 | 页面很多板块同时出现 | 精简模式只显示判断与行动，深度模式再展开证据和检索 |

## 9. 代码与测试结果

### 9.1 代码规模

| 范围 | 文件数 | 行数 |
|---|---:|---:|
| `backend/app` Python | 1,124 | 271,005 |
| `frontend/src` TS/TSX/JS/CSS | 811 | 150,375 |
| `tests` Python | 203 | 28,964 |
| 合计所选源码 | 2,138 | 450,344 |

这已经不是“小项目代码量”。优势是领域覆盖和积累深；风险是同一能力的旧版/新版/原型并存、巨型文件、测试环境漂移和事实口径不统一。

### 9.2 验证结果

| 验证 | 结果 | 结论 |
|---|---|---|
| 浏览器 17 页面 smoke | 通过页面加载 | 所有已点页面 GET 在真实本地 PG 下返回 200，无运行时 JS 崩溃 |
| 浏览器控制台 | 基本干净 | 仅 Vite debug / React DevTools shim 提示 |
| 前端 Vitest | **369 passed / 62 files** | 功能单测基础较好；仍有多处 React `act(...)` 警告 |
| 前端生产 build | 通过 | 最大 chunk 约 572.7 KB，低于项目 600 KB 门限，但 Vite 仍提示超过 500 KB |
| `make verify` | 失败 | 后端测试环境、CSS line guard、runtime SHA 三项失败 |
| 后端全量 pytest（SQLite 强制） | **1004 passed / 682 skipped / 32 failed / 37 errors** | 不能作为发布绿灯；SQLite schema/migration 与生产路径不一致 |
| repo hardening | 通过但 1,531 warnings | 规则覆盖广，噪声过大，需设新增告警门槛 |

## 10. 分阶段整改清单

### P0：任何云端主界面替换前必须完成

| 编号 | 事项 | 验收条件 |
|---|---|---|
| P0-1 | 移除 URL 长期 token | access log、browser URL、Referer 均不出现 JWT；SSE 仍可自动重连 |
| P0-2 | 收紧 API key 展示 | API 响应和 UI 不含 key 前缀材料；只有 configured/fingerprint |
| P0-3 | 建立确定性测试环境 | 一条命令起 test PG/Redis、迁移、seed；`make verify` 全绿 |
| P0-4 | 修正 build metadata | server/client/worker 均为同一完整 40 位 SHA；health gate 为 true |
| P0-5 | 本地真实快照夹具 | 至少 3 SKU 的脱敏快照可重复导入并通过行数/外键/新鲜度校验 |
| P0-6 | 改正可用性语义 | UI 分开显示“接口响应”“有数据”“数据新鲜”“可用于决策” |

### P1：形成可用 Beta 的 1-2 个迭代

| 编号 | 事项 | 验收条件 |
|---|---|---|
| P1-1 | 产品事实层 | SKU 规格、价格、库存、目标、竞品、历史市场表现可追溯 |
| P1-2 | 地图真实图层 | KOL/dealer/customer/event 四层至少三层可用，空层明确原因 |
| P1-3 | 公司账号同步 | 四项 KPI 来自可核验 daily/post 表，有时间窗和来源 |
| P1-4 | Dealer 评估 | dealer 覆盖、品类适配、库存/销量/活动能力形成可解释评分 |
| P1-5 | 行动闭环 | 每条建议能批准、分配、执行、取消，并进入 execution ledger |
| P1-6 | 结果回收 | 内容、曝光、互动、点击、GMV、成本在固定窗口回填 outcome |
| P1-7 | 预测校准 | 每次预测与实际值对比，展示 MAE/区间覆盖率/样本量 |
| P1-8 | 证据回链 | 市场信号、AI Today、KOL Fit、GTM verdict 均有可打开原链接或 ledger id |
| P1-9 | 修复侧栏高度 | 768px 高度下 Collapse/Theme/任务状态均可鼠标操作 |
| P1-10 | 任务查询聚合 | 空闲页 task 请求低于 12 次/分钟；SSE 正常时无 3 秒轮询风暴 |
| P1-11 | 团队/文案口径 | 用户数、团队数、版本、日期、深析比例全部来自实时数据 |
| P1-12 | 备忘录/布局真同步 | UI 区分“仅本地”“已同步”“远端为空”，跨浏览器复现 |

### P2：完成高质量产品化

1. 收敛为唯一 Dashboard 实现和单一 token 体系，拆分 2,585 行 CSS。
2. 把精简/深度模式做成同一数据模型的不同信息密度，不做两套逻辑。
3. 精简模式只呈现“判断、置信度、为什么、下一步、风险”；深度模式展开证据、原始指标、检索和模型细节。
4. 按参考稿逐屏做像素对比，统一侧栏、顶栏、透明度、背景、卡片层次和动效节奏。
5. 补键盘焦点、aria pressed/selected、对比度、减少动效、弹窗焦点锁定和屏幕阅读器测试。
6. 为每个推荐建立 decision card：结论、影响、置信度、证据、反证、数据缺口、行动、负责人、观察窗。
7. 建立模型/规则版本、数据快照、prompt、成本、结果的完整审计链。

## 11. 下一阶段最合理路线

### 第 1 周：可信底座

1. 修 P0 安全与发布门禁。
2. 建脱敏生产快照导入器和 3 SKU 验收夹具。
3. 改 Dashboard source health 语义，所有空态禁止硬编码来源和“真实”字样。
4. 修侧栏低高度、顶栏层次、API key 展示、团队/版本口径。

### 第 2-3 周：跑通一个产品闭环

选择一个真实新品，只做一条纵向链路：

```text
SKU -> 市场证据 -> 3 套 GTM -> KOL/dealer/channel 候选
-> 人工批准 -> Action Inbox -> 项目/内容执行 -> 结果回填 -> 预测误差
```

不要同时铺更多页面。先让一个 SKU 从输入到复盘完整走通，再抽象为通用 P2G 模板。

### 第 4 周：产品化 UI

1. 用真实闭环数据重做 Dashboard 首屏信息优先级。
2. 精简模式默认开启；深度模式承载调查、检索和证据。
3. 完成 Demo/reference 的逐屏视觉收敛和桌面/移动/低高度回归。
4. 预发运行 7 天，记录请求量、错误、任务成功率、证据覆盖率和建议采纳率。

## 12. 上线验收门槛

只有同时满足下列条件，才建议替换云端主界面：

1. `make verify` 全绿，前后端完整 SHA 对齐。
2. 生产/预发 access log 不含 token，系统设置不返回任何可用 key 材料。
3. 17 个页面无 5xx、无前端崩溃、无错位遮挡；768/900/1080 高度均通过。
4. Dashboard 每个“可用”状态都有行数、新鲜度和数据状态支撑。
5. 3 个 SKU 的产品到结果闭环可重复演示。
6. 至少 80% 的建议有直接证据链接；没有链接的明确标记为推断。
7. action -> execution -> outcome 的关联完整率达到 95%。
8. worker 心跳在线，任务失败有重试、原因和人工接管路径。
9. 空闲 Dashboard 请求量受控，无轮询风暴。
10. 云端真实数据通过脱敏快照在本地回归，且不复制生产密钥/PII。

## 13. 完整截图索引

1. `01-dashboard-initial.png`：Dashboard 首屏。
2. `02-ask-overlay.png`：问一问浮层。
3. `03-appearance-commandos-dark.png`：外观弹层/单色深色。
4. `04-glass-dark.png`：玻璃深色。
5. `05-glass-light.png`：玻璃浅色。
6. `06-instrument-light.png`：仪器浅色。
7. `07-instrument-dark.png`：仪器深色。
8. `08-commandos-light.png`：单色浅色。
9. `09-progress-center.png`：任务进度中心。
10. `10-help-panel.png`：帮助面板。
11. `11-work-reminders.png`：工作提醒。
12. `12-report-panel.png`：报告面板。
13. `13-notifications.png`：通知面板。
14. `14-user-menu.png`：用户菜单。
15. `15-layout-edit.png`：布局编辑。
16. `16-module-picker.png`：添加模块。
17. `17-sidebar-collapsed.png`：侧栏折叠诊断状态。
18. `18-action-ledger.png`：行动账本。
19. `19-signal-drilldown.png`：市场信号下钻。
20. `20-v6-fit-drilldown.png`：V6 Fit 下钻。
21. `21-ai-today-evidence.png`：AI Today 证据。
22. `22-gtm-command.png`：GTM Command。
23. `23-team-management.png`：团队管理。
24. `24-system-settings.png`：系统设置。

## 14. 审计边界

1. 本轮证明了本地真实 PG 环境下页面可加载和接口能响应，不等于云端生产数据正确。
2. 未触发写操作、抓取、LLM、邮件、发布、发货或支付，因此这些副作用链路仍需预发验收。
3. 截图可判断布局、文案、可见交互和部分对比度，不能单独证明 WCAG 合规；仍需键盘、读屏和自动化 accessibility 测试。
4. 本地数据库接近空库，智能能力评分反映“当前可验证事实”，不否认云端可能已有数据；云端需要用同一检查表另跑一轮。

---

**最终客观评价：** 这个项目的优势不是“页面多”，而是已经把产品、市场、KOL、dealer、渠道、项目、行动、结果、预测和学习都纳入了同一系统设想。它确实比普通 KOL SaaS 更有机会形成差异化。但当前最大风险也是覆盖面太广：真实数据和闭环样本没有跟上页面与表结构，系统容易显得“很聪明”却无法证明。下一阶段应停止继续横向加板块，集中把一个真实 SKU 的完整 P2G 闭环跑通、留下证据、结果和预测误差。做到这一点，系统才从高级 Alpha 进入真正的产品 Beta。
