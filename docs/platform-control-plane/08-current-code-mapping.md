# 08｜当前代码映射与改造顺序

## 1. 扫描结论

当前仓库不是从零开始，也还不能直接作为多租户 SaaS 开放。最客观的描述是：

- **强底座**：单公司业务能力、发布门禁、运行健康、精确模型安全闸、预算保护、项目数据范围、个人 AI 顾问与记忆。
- **可复用但需平台化**：组织/成员、员工权限、Feature Flag、Provider/模型配置、成本账本、审计、偏好与通知。
- **明显缺失**：客户套餐和服务端 Entitlement、多区域部署、正式部门树、企业知识空间、企业可配置智能体/工作流治理、统一客户计量账本、不可变强审计、正式支持访问和 Break-glass。
- **当前最大风险**：默认组织 1 回退、租户字段覆盖不全、永久 Owner 绕过、停用与会话撤销不完整、全局配置直接复用到多公司、审计 best-effort。

因此策略是“保留业务和运行强项，替换数据归属与控制内核”，不是推倒重做页面，也不是只增加三个导航入口。

## 2. 现有能力映射

### 2.1 租户与身份

| 现有代码 | 当前能力 | 判断 | 目标改造 |
|---|---|---|---|
| [`migrations/195_vkpi_tenant_kernel.sql`](../../migrations/195_vkpi_tenant_kernel.sql) | `organizations`、`organization_members`、默认 Viltrox 公司 | 租户种子 | 扩充生命周期/区域/部署；成员关系正规化；加 FK/状态 |
| [`migrations/245_vkpi_staff_organization_membership_backfill.sql`](../../migrations/245_vkpi_staff_organization_membership_backfill.sql) | 遗留员工回填到组织 1 | 迁移起点 | 保留为遗留证据，不作为运行时默认 |
| [`backend/app/domains/platform/tenancy.py`](../../backend/app/domains/platform/tenancy.py) | 解析当前公司，异常时回退组织 1/首条成员 | 多租户 P0 风险 | 显式工作区 Token；缺失/歧义 fail-closed |
| [`backend/app/core/permissions.py`](../../backend/app/core/permissions.py) | read/write/admin 与 Owner 绕过、Tab 权限 | 单公司 RBAC 种子 | 移除邮箱/永久 Owner；动作权限 + 策略决策服务 |
| [`backend/app/services/system/staff.py`](../../backend/app/services/system/staff.py) | 邀请、更新、暂停、恢复、角色 | 可复用业务流程 | 所有查询组织限定；暂停联动会话/Token/任务 |
| [`backend/app/api/routers/system_admin_staff.py`](../../backend/app/api/routers/system_admin_staff.py) | 员工、权限、Token、审计 API | 公司人员页种子 | 拆成 platform/org 命名空间和对应角色 |

注意：现有员工停用主要改变 `active` 状态。平台化前必须让登录、旧 JWT/刷新 Token、SSE、Worker、API Token 和计划任务共同执行中央状态门禁。

### 2.2 项目、任务与数据范围

| 现有代码 | 当前能力 | 判断 | 目标改造 |
|---|---|---|---|
| [`migrations/023_vkpi_core.sql`](../../migrations/023_vkpi_core.sql) | 项目核心表 | 业务资产成熟、租户字段不足 | 加组织/部门/数据等级、复合约束 |
| [`migrations/131_vkpi_project_members.sql`](../../migrations/131_vkpi_project_members.sql) | 项目成员 viewer/editor | ReBAC 种子 | 加组织约束、有效期、自定义角色映射 |
| [`migrations/110_vkpi_projects_restricted.sql`](../../migrations/110_vkpi_projects_restricted.sql) | restricted 项目 | 可复用语义 | 统一成 visibility + data classification |
| [`migrations/133_vkpi_is_public.sql`](../../migrations/133_vkpi_is_public.sql) | 公司公开语义雏形 | 可复用语义 | 公开只能在公司内，补组织边界 |
| [`backend/app/domains/access/scope.py`](../../backend/app/domains/access/scope.py) | own/assigned/member/public/restricted 范围 | 较强底座 | 纳入中央 ReBAC/Policy，补部门与敏感规则 |
| [`backend/app/api/routers/vkpi_tasks.py`](../../backend/app/api/routers/vkpi_tasks.py) | 任务列表和状态 | 用户工作台种子 | 任务账本补组织/项目/版本/计量 |
| [`backend/app/domains/tasks/enqueue.py`](../../backend/app/domains/tasks/enqueue.py) | 入队和访问判断 | 可复用执行链 | 创建/执行均验证 TenantContext 和权益 |

### 2.3 部门与团队

| 现有代码 | 当前能力 | 判断 | 目标改造 |
|---|---|---|---|
| [`migrations/123_vkpi_staff_groups.sql`](../../migrations/123_vkpi_staff_groups.sql) | JSON 成员组和权限 | 只适合临时组 | 保留为 `teams` 迁移来源，不扩成正式部门 |
| [`backend/app/domains/staff_groups/service.py`](../../backend/app/domains/staff_groups/service.py) | 组共享项目/KOL/KPI/提醒 | 部分复用 | 加组织、正规成员关系、到期；另建 departments |

正式部门需要父子树、负责人、路径、成员关系、岗位、成本中心和状态；不能继续用 JSON 数组。

### 2.4 企业知识、智能体与工作流

| 现有代码 | 当前能力 | 判断 | 目标改造 |
|---|---|---|---|
| [`backend/app/db/repositories/knowledge.py`](../../backend/app/db/repositories/knowledge.py) | 产品知识读写 | 非企业知识空间 | 新建组织空间、来源、文档、Chunk、ACL、同步运行 |
| [`backend/app/api/routers/vkpi_agents.py`](../../backend/app/api/routers/vkpi_agents.py) | 智能体相关 API/运行 | 运行种子，非租户资产治理 | 定义/版本/授权/工具/模型/评测分离 |
| [`backend/app/domains/agents/capabilities.py`](../../backend/app/domains/agents/capabilities.py) | 代码定义能力清单 | 模块能力清单种子 | 迁移到版本化 Manifest，CI 校验 |
| [`migrations/180_vkpi_agent_orchestration.sql`](../../migrations/180_vkpi_agent_orchestration.sql) | 智能体编排/计划表 | 需高优先级租户化 | 补组织/Owner/版本/权限/成本/审计 |
| [`migrations/193_vkpi_workflow_runs.sql`](../../migrations/193_vkpi_workflow_runs.sql) | 带组织的工作流运行 | 运行账本可复用 | 增加定义、版本、触发器、步骤、审批、密钥引用 |
| [`backend/app/domains/platform/workflow_engine.py`](../../backend/app/domains/platform/workflow_engine.py) | 工作流执行基础 | 可复用执行器 | 强制版本/组织/幂等/策略/计量 |

### 2.5 AI 助手、记忆、偏好与通知

| 现有代码 | 当前能力 | 判断 | 目标改造 |
|---|---|---|---|
| [`migrations/250_vkpi_marketing_advisor_memory.sql`](../../migrations/250_vkpi_marketing_advisor_memory.sql) | 公司 + 用户隔离的会话、候选、事实、行动草稿、事件 | 当前最成熟的多租户个人资产 | 作为 `/me/assistant`、`/me/memory` 基线 |
| [`backend/app/api/dependencies/advisor_scope.py`](../../backend/app/api/dependencies/advisor_scope.py) | 顾问精确作用域 | 强底座 | 推广到其他个人资源依赖 |
| [`backend/app/api/routers/vkpi_marketing_advisor.py`](../../backend/app/api/routers/vkpi_marketing_advisor.py) | 会话、消息、记忆与操作 API | 强底座 | 调整 `/me/v1` 合同、补 DSAR 全流程 |
| [`frontend/src/components/vkpi/cockpit/pages/MarketingAdvisorWorkspace.tsx`](../../frontend/src/components/vkpi/cockpit/pages/MarketingAdvisorWorkspace.tsx) | 顾问工作区 | 用户控制台强 UI 种子 | 接入统一任务/引用/用量/会话 |
| [`migrations/047_vkpi_user_preferences.sql`](../../migrations/047_vkpi_user_preferences.sql) | 按员工偏好 | 单公司可用 | 加组织，区分公司管理与个人可改 |
| [`migrations/048_vkpi_notification_settings.sql`](../../migrations/048_vkpi_notification_settings.sql) | 通知偏好 | 只存设置 | 加通知事件、站内信、投递、重试、已读、渠道验证 |
| [`backend/app/domains/settings/notifications.py`](../../backend/app/domains/settings/notifications.py) | 偏好 CRUD | 设置层可复用 | 不再把保存偏好等同实际通知系统 |

个人记忆的关键设计应保留：候选必须确认才能成为事实、个人资源同时带公司和用户范围、行动先生成草稿而非直接执行。

### 2.6 Provider、模型与成本

| 现有代码 | 当前能力 | 判断 | 目标改造 |
|---|---|---|---|
| [`backend/app/core/model_registry.py`](../../backend/app/core/model_registry.py) | 静态模型白名单和任务绑定 | 强安全种子 | 数据化版本目录，但保留审核/精确模型原则 |
| [`backend/app/api/routers/system_admin.py`](../../backend/app/api/routers/system_admin.py) | Provider 探测、模型就绪、Fail-closed 切换、重启 | 强运维底座 | 由全局扩成公司/区域/策略版本；强化审批 |
| [`backend/app/platform/llm_production.py`](../../backend/app/platform/llm_production.py) | 精确模型、就绪、预算预留 | 强生产入口 | 设为唯一付费 LLM 路径，清理非原子旁路 |
| [`backend/app/services/system/provider_health.py`](../../backend/app/services/system/provider_health.py) | Provider 健康和告警 | 可复用 | 扩为账户 + 精确模型 + 区域维度 |
| [`backend/app/domains/settings/api_key_pool.py`](../../backend/app/domains/settings/api_key_pool.py) | Key 加密/脱敏/选择 | 部分复用 | 密钥迁 KMS/Vault；统一配额和轮转真源 |
| [`migrations/057_vkpi_ai_cost_budget.sql`](../../migrations/057_vkpi_ai_cost_budget.sql) | AI 成本和预算表 | 供应商成本底座 | 补组织/项目/功能/计费单位；与客户账本分离 |
| [`backend/app/domains/costs/budget_guard.py`](../../backend/app/domains/costs/budget_guard.py) | 多作用域预算检查和累计 | 强底座 | 接统一 Reservation/Settle/Reconcile |
| [`migrations/258_vkpi_llm_budget_reservations.sql`](../../migrations/258_vkpi_llm_budget_reservations.sql) | 原子 LLM 预算预留 | 强底座 | 推广到抓取/存储/工作流等全部付费入口 |

当前模型和预算能力主要是全局/供应商维度。不能直接把全局环境变量和 Key 池暴露为公司设置；需要 Provider Account、模型策略版本、区域/数据政策和公司覆盖。

### 2.7 Feature Flag、设置与 Firewall

| 现有代码 | 当前能力 | 判断 | 目标改造 |
|---|---|---|---|
| [`migrations/046_vkpi_settings.sql`](../../migrations/046_vkpi_settings.sql) | 全局布尔 Flag、预算/抓取设置 | 单公司全局设置 | Flag 规则加环境/公司/套餐/比例/版本/到期 |
| [`backend/app/api/routers/vkpi_settings.py`](../../backend/app/api/routers/vkpi_settings.py) | Flag、预算、偏好、通知、健康、Key API | 功能集中但职责混杂 | 拆 platform/org/me 命名空间 |
| [`backend/app/domains/settings/platform_crawl.py`](../../backend/app/domains/settings/platform_crawl.py) | Flag 与抓取/预算状态 | 可复用领域逻辑 | 统一配置来源链和组织作用域 |
| [`backend/app/domains/access/firewall.py`](../../backend/app/domains/access/firewall.py) | Flag/平台/预算组合闸构想 | 架构种子、实际接线不足 | 替换为中央 Evaluator；禁止空参数绕过 |

Entitlement、Feature Flag、Kill Switch、Permission、Quota 必须是五类明确控制，不能继续由一个 Settings/Firewall 概念模糊承担。

### 2.8 审计与事件

| 现有代码 | 当前能力 | 判断 | 目标改造 |
|---|---|---|---|
| [`migrations/012_admin_system_ops.sql`](../../migrations/012_admin_system_ops.sql) | 全局管理审计 | 平台审计种子 | 加组织/区域/有效 Actor/策略/审批/哈希 |
| [`migrations/028_vkpi_audit_logs.sql`](../../migrations/028_vkpi_audit_logs.sql) | 敏感读取、导出、设置变更、统一视图 | 丰富业务证据 | 统一 Schema、租户范围和查询权限 |
| [`backend/app/services/audit_log.py`](../../backend/app/services/audit_log.py) | 管理动作记录 | 当前 best-effort | L3/L4 改为事务意图 + 完成回执，fail-closed |
| [`backend/app/domains/audit/decorator.py`](../../backend/app/domains/audit/decorator.py) | 装饰器审计 | 便于普通事件 | 不用于需要强保证的唯一关键审计 |
| [`migrations/192_vkpi_event_ledger.sql`](../../migrations/192_vkpi_event_ledger.sql) | 带组织/Trace 的事件账本 | 统一事件信封种子 | 事务 Outbox、事件版本和幂等消费者 |

现有多张审计表可以迁移/归档，不建议直接删除；新平台先建立统一事件，旧表通过适配视图只读合并，达到保留期后再退役。

### 2.9 发布、健康与可观测性

| 现有代码 | 当前能力 | 判断 | 目标改造 |
|---|---|---|---|
| [`scripts/verify.sh`](../../scripts/verify.sh) | 测试、迁移、运行身份等发布门禁 | 强底座 | 作为 Release 验收任务，输出机器可读回执 |
| [`scripts/ops/deploy_local_to_cloud.sh`](../../scripts/ops/deploy_local_to_cloud.sh) | 单目标发布、迁移检查、回滚 | 强单区域脚本 | Release/Region 实体驱动，多批次灰度 |
| [`scripts/ops/atomic_release_layout.py`](../../scripts/ops/atomic_release_layout.py) | current/previous/rollback 原子布局 | 强底座 | 纳入签名制品和母控制台状态 |
| [`backend/app/main.py`](../../backend/app/main.py) | `/health`、版本/迁移/Worker/Redis 信任 | 强底座 | 按 Region/Cell/租户聚合并接正式 SLO |
| [`backend/app/services/monitoring/runtime.py`](../../backend/app/services/monitoring/runtime.py) | 请求/错误/延迟和快照 | 监控种子 | OpenTelemetry/Prometheus/Trace 真源 |
| [`backend/app/domains/ops/health_sentinel.py`](../../backend/app/domains/ops/health_sentinel.py) | 队列、同步、预算、成本、推荐链路健康 | 强业务健康种子 | 标准化告警、租户/区域维度和自动暂停 |

当前是单目标部署，不代表多区域。R2 自定义 Endpoint 或一个 SSH 目标也不能作为数据驻留实现。

### 2.10 前端控制台种子

| 现有代码 | 可复用方向 |
|---|---|
| [`frontend/src/components/vkpi/pages/SettingsPage.tsx`](../../frontend/src/components/vkpi/pages/SettingsPage.tsx) | 公司设置页面壳 |
| [`frontend/src/components/vkpi/pages/settings/SettingsStaffPanel.tsx`](../../frontend/src/components/vkpi/pages/settings/SettingsStaffPanel.tsx) | 成员列表与邀请交互 |
| [`frontend/src/components/vkpi/pages/settings/staffPermissionTemplates.ts`](../../frontend/src/components/vkpi/pages/settings/staffPermissionTemplates.ts) | 权限模板展示，后端真源需重做 |
| [`frontend/src/components/admin/tabs_v2/VkpiTab.tsx`](../../frontend/src/components/admin/tabs_v2/VkpiTab.tsx) | 母控制台运行/系统管理视觉种子 |
| [`frontend/src/components/vkpi/cockpit/components/TaskProgressBoard.tsx`](../../frontend/src/components/vkpi/cockpit/components/TaskProgressBoard.tsx) | 用户任务和阶段进度 |
| [`frontend/src/components/vkpi/cockpit/CockpitApp.tsx`](../../frontend/src/components/vkpi/cockpit/CockpitApp.tsx) | 用户工作台外壳、侧栏和弹层 |

前端可拆壳复用，但所有能力从新的 `/platform`、`/org`、`/me` API 和服务端 Capabilities 读取，不能继续以隐藏按钮作为授权。

## 3. 目标服务/模块边界

首版不需要立刻拆成大量微服务。建议先在当前后端形成明确领域模块和独立 Schema/接口，满足容量或合规需求后再物理拆分：

```text
platform/
├── identity
├── tenancy
├── policy
├── entitlements
├── metering
├── runtime
├── releases
└── audit

organization/
├── departments
├── members
├── projects
├── knowledge
├── agents
└── workflows

personal/
├── tasks
├── assistant
├── memory
├── notifications
├── sessions
└── preferences
```

业务模块通过 Manifest 接入这些核心，不反向把 V-KPI/影视字段写入平台核心。

## 4. 代码改造波次

### Wave 0｜冻结和旁路清单

- 固定内部 9.5 Release。
- 生成所有无组织表、SQL、缓存、队列、对象、搜索、导出和 API 清单。
- 列出全部 `or 1`、默认组织、Owner 邮箱、`is_owner`、空 Firewall 参数、非原子 LLM 入口。
- 建立“允许的遗留豁免”机器可读清单，每项有 Owner 和到期。

### Wave 1｜上下文和写双轨

- 新 TenantContext/Token，不改变旧业务结果。
- 新列可空，所有新写入带组织；监控空值。
- Outbox 和统一审计信封先接入新平台动作。
- 页面壳按新命名空间搭建但不开放真实客户。

### Wave 2｜回填和读切换

- 现有数据显式回填组织 1，异常放隔离表。
- Repository 新接口强制 TenantContext；旧/新查询影子比较。
- 缓存、队列、对象和搜索索引重建组织命名空间。
- 建立合成公司 2，仅测试数据。

### Wave 3｜强约束和控制面

- `NOT NULL`、复合 FK/唯一、RLS。
- 删除默认回退和无范围生产入口。
- Policy/Entitlement/Flag/Kill Switch/Quota 中央接线。
- 公司生命周期、支持访问和会话撤销。

### Wave 4｜企业资产和商业闭环

- 公司部门、知识、智能体、工作流和用户工作台。
- Provider/模型/区域策略版本化。
- 统一计量、成本对账、账单、发布灰度和不可变审计。
- 设计伙伴前完成双租户、恢复和外部安全验证。

## 5. 开放第二家公司的 P0 清单

以下全部完成前，第二家公司只能使用合成数据：

1. 生产认证请求无默认组织 1 回退。
2. 公司/成员暂停能撤销所有会话和异步入口。
3. 核心业务表、缓存、队列、对象、搜索、导出有强组织边界。
4. 项目、任务、KOL、智能体、工作流、通知、偏好、成本和审计完成租户化。
5. 服务端 Entitlement 与 Policy 覆盖 HTTP、Worker、Scheduler。
6. 平台身份与公司身份分离；无邮箱/永久 Owner 全绕过。
7. 权限、密钥、区域、公司状态和紧急授权有 fail-closed 强审计。
8. 双租户攻击矩阵 100% 拒绝，缓存/SSE/导出/队列无旁路。
9. 备份恢复和公司删除/导出流程演练通过。
10. 所有付费调用有公司归属、预算预留和用量事件。

## 6. 明确保留、替换和新建

### 保留并增强

- V-KPI 业务模块与关键用户旅程。
- 项目 own/member/public/restricted 的数据范围语义。
- 精确模型、Readiness、Provider Probe、预算预留。
- `verify.sh`、运行身份、原子发布和健康哨兵。
- 顾问会话、记忆候选确认、行动草稿和事件。
- 现有设置、任务、审计页面的成熟交互部分。

### 替换核心真源

- 默认组织 1/首成员租户解析。
- 硬编码 Owner 邮箱、永久全权限绕过。
- 仅按 Tab/前端隐藏的权限模型。
- 全局单层 Feature Flag 和全局模型环境变量。
- 两套分散预算与不完整成本归属。
- best-effort 关键审计。
- JSON 成员组充当组织架构的思路。

### 新建

- 套餐、订阅、模块 Entitlement。
- Region、Deployment、Release、Rollout 实体。
- 正式部门、资源授权、企业知识空间。
- 智能体/工作流定义与版本治理。
- 统一客户 Usage、账单和对账。
- 支持访问、Break-glass、不可变审计归档。
- 设备会话、通知投递和个人使用透明度。

## 7. 代码级门禁建议

在 CI/`verify.sh` 增加：

- 新租户表缺 `organization_id` 即失败。
- 新业务 Repository 存在无 TenantContext 公共方法即失败。
- 新付费 Provider 调用缺 Reservation 即失败。
- 新写 API 缺权限动作、幂等和审计声明即失败。
- 新模块 Manifest 缺权限/计量/保留/删除/审计即失败。
- 生产代码新增默认 `organization_id=1` 或 Owner 邮箱即失败。
- Feature Flag 未设置 Owner/到期或被当 Entitlement 即失败。
- L3/L4 权限动作没有 Step-up/审批契约即失败。

## 8. 当前实现的最严谨评价

作为**内部单公司 AI 营销/管理系统**，代码具备明显的深度和大量可复用资产；作为**可开通多家客户的通用 SaaS 控制平台**，目前仍处于“强业务数据平面 + 运维底座，控制平面早期”的阶段。

这不否定现有代码价值，反而说明下一轮最高杠杆不是继续堆业务页面，而是把租户、身份、权益、计量、发布和审计统一。完成这些底层契约后，影视、营销、销售情报等行业模块才能真正共享平台，而不是复制出多个隐性单租户系统。
