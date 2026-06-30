# V-KPI 当前修复项与新增功能规划

基准版本: `af32815d`

生成时间: 2026-06-28

状态口径:
- 工作树: 干净
- `/health`: 前端 / 后端 / worker 已对齐 `af32815d`
- 后端测试: `829 passed`
- 前端类型检查: `tsc --noEmit` 通过
- 前端构建: `npm run build` 通过
- 当前产品代码规模: 约 326,620 行
- 含脚本 / 测试 / 迁移: 约 425,064 行

本文只分两类:
1. 必须修复的问题
2. 前后端需要新增或补齐的功能

---

## 一、必须修复的问题

这些不是“锦上添花”,而是影响稳定性、可信度、上线体验、团队使用安全的项。

### F1. 清理页面占位与弱实现

现状:
- 产品代码里仍有 `待接入` 约 168 处。
- `placeholder` 约 478 处。
- `mock` 约 47 处。
- `@ts-nocheck` 约 9 处。
- `any` 约 1796 处。

问题:
- 用户看到“待接入 / 占位 / mock”会直接降低信任。
- 后续功能继续叠加时,弱类型会让 bug 变隐蔽。

修复方向:
- 将 `待接入` 分为三类:
  - 真缺功能: 列入功能补齐。
  - 有后端但前端没接: 接真实 endpoint。
  - 暂不做: 明确标注为“预览 / 未开放”,不要伪装成可用。
- `@ts-nocheck` 按核心页面优先逐个删除。
- `any` 先处理 service 层和核心业务数据类型。

重点文件:
- `frontend/src/components/vkpi/pages/SettingsPage.tsx`
- `frontend/src/components/vkpi/pages/ShopifyHubPage.tsx`
- `frontend/src/components/vkpi/pages/projects/ProjectDetailModals.tsx`
- `frontend/src/components/vkpi/cockpit/components/SmartKolInputPanel.Sections.tsx`
- `frontend/src/services/vkpi/*.ts`

验收:
- 首页、KOL Pool、MY KOL、Projects、Events、Settings 不出现误导性“待接入”。
- `@ts-nocheck` 从 9 降到 0。
- 关键 service 返回类型明确。

---

### F2. 前端大 chunk 与页面加载拆包

现状:
- `npm run build` 通过。
- 仍有一个约 983KB 的大 chunk。
- `SettingsPage.tsx` 同时被动态和静态导入,构建有警告。

问题:
- 本地开发没问题,但线上首屏和弱网体验会受影响。
- 页面越多,这个问题会越来越明显。

修复方向:
- 设置页、Shopify、Skill Studio、Data Analysis、Projects 详情页继续 lazy load。
- 避免同一个页面同时静态导入和动态导入。
- 对 cockpit 主壳和重业务页做 route-level split。

重点文件:
- `frontend/src/components/vkpi/cockpit/CockpitApp.Sections.tsx`
- `frontend/src/components/vkpi/pages/WorkspacePage.tsx`
- `frontend/src/components/vkpi/pages/SettingsPage.tsx`
- `frontend/vite.config.*`

验收:
- 最大 JS chunk 降到 500KB 以下,或明确配置分包策略。
- 构建不再出现 SettingsPage 动态/静态混用警告。

---

### F3. 运行态与版本对齐必须制度化

现状:
- 当前 `/health` 已对齐。
- 之前多次出现 HEAD、server、client、worker 不一致。

问题:
- 如果版本不一致,用户看到的问题可能不是当前代码的问题。
- 排查会浪费大量时间。

修复方向:
- 每次启动或部署后强制检查:
  - server git sha
  - client build sha
  - worker sha
  - db migration max
  - worker heartbeat
- 前端 Settings 或 Admin debug 增加一张“运行态卡片”。

重点文件:
- `backend/app/main_health.py`
- `frontend/src/components/vkpi/pages/SettingsPage.tsx`
- `frontend/src/services/vkpi/settings-api.ts` 或现有 settings service

验收:
- 页面可见 `sha_aligned=true`。
- worker 离线时页面明确提示。
- 部署 runbook 中固定健康检查步骤。

---

### F4. Projects 履约闭环仍需补强

现状:
- Projects 页面和复盘链路已大量完成。
- 但“真实发货 -> 签收 -> 观察窗口 -> 内容命中 -> 复盘”还需要继续压实。

问题:
- 如果没有真实物流和内容观察闭环,Projects 仍像项目管理工具,不是自动履约系统。

修复方向:
- 17track / shipments 到货后写入 `delivered_at`。
- 自动创建 7/14/21 天观察窗口。
- 定时扫描 KOL 页面或 evidence。
- 命中内容后写 `project_content_posts`。
- content post 触发 retrospective queue。

重点文件:
- `backend/app/api/routers/vkpi_projects_fulfillment.py`
- `backend/app/domains/projects/observation_windows.py`
- `backend/app/domains/projects/retrospective_aggregate.py`
- `backend/app/domains/projects/workflow_evidence*.py`
- `frontend/src/components/vkpi/pages/projects/tabs/CampaignRetrospectiveTab.tsx`

验收:
- 任一 project 可以从 shipment 到 content post 追溯到 URL。
- Projects 复盘 tab 展示内容来源、发布时间、播放/互动、是否已深析。
- 不触碰 `viltrox_fit_score`。

---

### F5. KOL Pool 搜索 / 建档 / 深析结果回填要更稳

现状:
- 搜索 session、URL 识别、建档、深析已经有较完整链路。
- 但用户体验上仍容易出现“后台跑完了,前端没及时显示”。

问题:
- 这会让用户误以为系统没工作。
- 智能搜索如果不能恢复历史,切页后体验断裂。

修复方向:
- 搜索、URL、建档、深析全部以 `search_session` 为单一事实源。
- 前端轮询 session,终态自动回填。
- 最近历史点击后恢复完整结果,不是只恢复 query 文本。
- 失败原因可见,可重试。

重点文件:
- `backend/app/domains/kol/search_sessions.py`
- `backend/app/domains/kol/url_deep_crawl*.py`
- `frontend/src/components/vkpi/cockpit/components/SmartKolInputPanel*.tsx`
- `frontend/src/services/vkpi/kolPool-api.search.ts`

验收:
- 深度查找完成后不用刷新页面即可看到结果。
- 切换页面回来仍可恢复上一次结果。
- 失败时显示可读原因,不是静默消失。

---

### F6. 视频失败池治理

现状:
- final_v1 与 deep result 已有覆盖。
- 但失败池仍存在 download / provider / media_resolve / content 类失败。

问题:
- 继续直接批量跑会浪费 API 成本。
- TikTok / IG / YouTube 失败原因应该分 lane 治理。

修复方向:
- 失败分类固定:
  - provider_pressure
  - download
  - media_resolve
  - content_restricted
  - content_blocked
  - code_error
  - unknown
- download 失败先 precheck,不要直接重跑。
- provider_pressure 走退避重试。
- TikTok/IG media_resolve 单独处理,不混入 YouTube 主波。

重点文件:
- `backend/app/workers/apify_jobs_worker*.py`
- `backend/app/services/ai/analyzers/gemini_video*.py`
- `backend/app/domains/kol/failed_pool_triage.py`
- `backend/app/domains/tasks/queue_view.py`

验收:
- unknown failed 降到 5% 以下。
- 每条 failed 都有可读分类。
- 重试只发生在可救类型。

---

### F7. 权限与多成员数据隔离继续复核

现状:
- 员工账号、管理员、分享、权限已经有基础。
- 但需要继续验证 Dashboard / MY KOL / Projects / Events 是否都按身份正确过滤。

问题:
- 员工看到全局数据会破坏组织使用边界。
- “管理员可见”这种文字会有上下级压迫感,应改成“无权限 / 仅公司账号可见 / 未分享给你”。

修复方向:
- owner / company account 看全局。
- 普通成员只看:
  - 自己的 MY KOL
  - 分享给自己的 Project
  - 分享给自己的 Event
  - 自己负责的任务
- 所有不可见文案统一改为“无权限”或“未分享给你”。

重点文件:
- `backend/app/domains/dashboard/summary_scope.py`
- `backend/app/api/routers/vkpi_events.py`
- `backend/app/api/routers/vkpi_projects*.py`
- `frontend/src/components/vkpi/pages/SettingsPage.tsx`
- `frontend/src/components/vkpi/shared/ShareModal.tsx`

验收:
- 员工账号登录后 Dashboard 不显示全公司总量。
- 直接访问未授权 Event / Project 返回 403 或前端无权限态。
- 文案不出现“管理员层可见”这类上下级表述。

---

## 二、需要新增或补齐的前后端功能

这些是让平台从“能用”变成“聪明、可持续增长、可给团队使用”的功能。

### N1. Action Inbox 2.0

目标:
让系统每天告诉用户“今天该做什么”,而不是只展示数据。

后端需要:
- 每日生成 action inbox:
  - 哪些 KOL 该补数据
  - 哪些 KOL 值得联系
  - 哪些 Project 到了观察期
  - 哪些内容值得复盘
  - 哪些失败任务值得重试
  - 哪些市场信号异常
- 每条 action 必须包含:
  - 原因
  - 预计成本
  - 预计收益
  - 风险
  - 影响表
  - 是否触碰 V6 Fit: 必须 false

前端需要:
- Dashboard / cockpit 首页展示“今日建议”。
- 每条 action 可以:
  - 批准
  - 忽略
  - 延后
  - 查看证据
  - 执行后看结果

重点文件:
- `backend/app/domains/actions/inbox.py`
- `backend/app/domains/actions/executors.py`
- `backend/app/api/routers/vkpi_agents.py`
- `frontend/src/components/vkpi/cockpit/components/ActionInboxPanel.tsx`

验收:
- 每天至少生成 5-20 条有效建议。
- 执行后有结果回写。
- 失败有原因。

---

### N2. Marketing Brain Skill 编排进入真实工作流

目标:
现在 Skill Registry 已经有了,下一步要让它参与真实业务按钮。

已有基础:
- `marketing_brain/ontology.py`
- `marketing_brain/skill_registry.py`
- `brief_generate_v1`
- `campaign_plan`
- `content_score`
- `creator_match`
- `roi_review`
- `SkillStudioPage`

后端新增:
- Skill Run 统一落库。
- Skill 输入/输出统一 schema。
- 每个 skill 记录:
  - source
  - input_refs
  - output_json
  - cost
  - latency
  - confidence
  - evaluation result

前端新增:
- 在 KOL 详情中调用 creator_match。
- 在 Projects 复盘中调用 roi_review。
- 在新品项目中调用 campaign_plan。
- 在报告页调用 brief_generate。

验收:
- 至少 3 个真实页面按钮接入 Skill。
- 每次运行可追踪输入、输出、成本、质量。

---

### N3. 新品 Launch Project 模板

目标:
让 V-KPI 成为 Viltrox 内部新品从 0 到 1 的验证机器。

后端新增:
- Project 类型增加 `launch_project`。
- 新品字段:
  - SKU
  - 价格带
  - 目标国家
  - 核心卖点
  - 竞品
  - 目标人群
  - 验证假设
- 自动生成:
  - KOL 候选
  - 内容验证任务
  - 发样计划
  - 观察窗口
  - 复盘报告

前端新增:
- 创建 Project 时可选择“新品 Launch”。
- 显示 Launch 仪表盘:
  - 候选 KOL
  - 已联系
  - 已发样
  - 已发布
  - 内容反馈
  - 市场信号
  - 是否建议放大

重点文件:
- `backend/app/domains/projects/workflow_projects.py`
- `backend/app/domains/projects/retrospective_aggregate.py`
- `frontend/src/components/vkpi/pages/ProjectsPage.tsx`
- `frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx`

验收:
- 能用一个新品从 KOL 推荐到内容复盘完整跑通。

---

### N4. MY KOL 每日学习系统

目标:
MY KOL 不只是收藏夹,而是关系和内容学习池。

后端新增:
- 每天扫描收藏 / 关注 KOL:
  - 新视频
  - 新帖子
  - 是否提到 Viltrox
  - 是否提到竞品
  - 是否出现合作机会
- 公司账号内容表现学习:
  - 哪些视频好
  - 哪些视频差
  - 哪些画面风格有效
  - 哪些标题/主题有效

前端新增:
- MY KOL 展示:
  - 今日新增动态
  - 有合作机会
  - 有 Viltrox mention
  - 需要联系
  - 近期内容趋势

重点文件:
- `backend/app/domains/kol/auto_poll.py`
- `backend/app/domains/comments/*`
- `frontend/src/components/vkpi/pages/myKol/*`

验收:
- MY KOL 每天自动出现新动态。
- 每个动态可以追溯到 URL / evidence。

---

### N5. Events 真实业务闭环

目标:
Events 从活动壳变成可复盘的活动运营系统。

后端新增:
- Event 任务:
  - 邀约
  - 物料
  - 学生/Dealer/KOL
  - 现场内容
  - 费用
  - 发票/图片证据
- 地图地址解析与校验。
- 活动后复盘:
  - 到场人数
  - 内容产出
  - 线索
  - 后续合作
  - 成本

前端新增:
- Event 创建页加强。
- Event 详情页展示:
  - 日程
  - 人员
  - 预算
  - 证据上传
  - 地图
  - 复盘

重点文件:
- `backend/app/domains/events/service.py`
- `backend/app/api/routers/vkpi_events.py`
- `frontend/src/components/vkpi/pages/EventsPage*.tsx`

验收:
- 创建活动 -> 添加任务 -> 上传证据 -> 地图定位 -> 复盘报告。

---

### N6. 数据导出 / 问数 / 报告页

目标:
让用户可以直接问系统要数据,并导出 PDF / Excel。

后端新增:
- 数据查询意图解析。
- 可访问数据源清单。
- Query plan 生成。
- SQL / 聚合执行白名单。
- 报告生成。

前端新增:
- 一个“数据导出 / 问数”页面:
  - 输入问题
  - 选择时间范围
  - 选择数据源
  - 预览结果
  - 导出 PDF / Excel

示例问题:
- 最近 30 天送出去产品价值是多少?
- 哪些 KOL 内容 ROI 最高?
- 哪个国家最近增长快?
- 哪些 Project 已签收但未发内容?

重点文件:
- 新增 `backend/app/domains/analytics/query_planner.py`
- 新增 `backend/app/api/routers/vkpi_analytics_export.py`
- 新增 `frontend/src/components/vkpi/pages/DataExportPage.tsx`

验收:
- 至少 10 个常用问题能稳定返回。
- 导出文件可打开、可复核。

---

### N7. 行业与市场趋势智能页

目标:
从内部数据升级到宏观市场观察。

后端新增:
- Google / YouTube / Reddit / Amazon / B&H / 新闻趋势抓取。
- 竞品 mention 监控。
- 产品关键词趋势。
- 国家/地区机会评分。
- 市场押注 bet ledger。

前端新增:
- 市场趋势页:
  - 热点
  - 竞品
  - 机会
  - 风险
  - 推荐动作

重点文件:
- `backend/app/domains/market/*`
- `backend/app/domains/market/bet_ledger.py`
- `backend/app/domains/market/bet_producer.py`
- 新增或扩展前端 Market / Intelligence 页面

验收:
- 每天至少生成一批市场观察。
- 每条观察有来源、证据、置信度、建议动作。

---

### N8. 多 API Token 池与本地计算接入

目标:
减少排队,提高吞吐,为未来桌面 App / 本地 worker 做准备。

后端新增:
- Provider key pool:
  - Gemini
  - OpenAI
  - Claude
  - Apify
  - YouTube API
- 每个 key 有:
  - quota
  - health
  - cooldown
  - cost
  - owner
  - last_error
- 任务按 key 池调度。

本地 worker 方向:
- 本地电脑作为 worker 注册到服务器。
- 服务器只派发任务和接收结果。
- 本地负责:
  - 抓取
  - 下载
  - 抽帧
  - 轻量 LLM
  - 上传结果

重点文件:
- `backend/app/platform/models/router.py`
- `backend/app/platform/models/registry.py`
- `backend/app/platform/models/cost_policy.py`
- `backend/app/platform/worker_lease.py`
- `backend/app/domains/platform/workflow_engine.py`

验收:
- 10-20 个 token 可轮转。
- 单 token 失败不会拖死队列。
- 本地 worker 可注册、领任务、回传结果。

---

## 三、优先级建议

### 第一批: 先稳住产品可信度

1. F1 清占位 / 弱实现
2. F3 运行态卡片
3. F5 搜索 session 回填
4. F7 权限文案和隔离复核

目标:
用户能信任页面显示的东西。

### 第二批: 打通业务闭环

1. F4 Projects 履约闭环
2. N3 新品 Launch Project
3. N4 MY KOL 每日学习
4. N5 Events 真实业务闭环

目标:
平台从“查数据”变成“跑业务”。

### 第三批: 进入智能体

1. N1 Action Inbox 2.0
2. N2 Marketing Brain Skill 接真实页面
3. N6 数据导出 / 问数 / 报告
4. N7 行业趋势智能页

目标:
平台开始主动告诉用户该做什么。

### 第四批: 性能与规模

1. F2 前端拆包
2. F6 失败池治理
3. N8 多 token + 本地 worker

目标:
支撑更多任务和更多用户。

---

## 四、上线前最低验收门

如果只追求“能给团队安全使用”,最低必须过:

- `/health.sha_aligned=true`
- `pytest` 全绿
- `tsc` 全绿
- `npm run build` 通过
- 员工账号只能看到自己的数据
- KOL Pool 搜索结果可恢复
- Projects 至少能追踪一个真实发货到内容观察
- MY KOL 收藏能稳定出现在 MY KOL
- Events 未授权不可看
- 所有待接入功能不伪装成可用

---

## 五、产品判断

当前 V-KPI 已经不是早期 demo,而是一个有真实业务骨架的内部 AI Marketing Brain 雏形。

但它距离 90+ 还有三个核心差距:

1. 业务闭环还要更硬: 尤其 Projects / Events / MY KOL。
2. 主动智能还不够: Action Inbox 和 Marketing Brain Skill 要进入真实按钮。
3. 数据沉淀要持续: 搜索、视频、评论、项目、销售、市场信号都要进入同一套学习循环。

下一阶段不要再盲目加页面。应该围绕一句话做:

> 让系统每天自动发现机会、生成建议、执行动作、验证结果、沉淀经验。

