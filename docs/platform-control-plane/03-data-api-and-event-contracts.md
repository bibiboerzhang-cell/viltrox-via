# 03｜数据、API 与事件契约

## 1. 总体原则

- PostgreSQL 是权限、权益、配置、计量和审计的事务真源。
- Redis 只做缓存、短期锁、队列和限流，不保存不可恢复的唯一状态。
- 对象存储保存文件、导出、审计冷归档、发布制品和备份。
- 搜索/向量库是派生索引，必须能从租户数据库和对象存储重建。
- 所有租户资源都带不可空 `organization_id`；个人资源再带 `user_id` 或 `membership_id`。
- 外部接口使用稳定 UID，不暴露自增 ID；UID 不承担授权职责。
- 配置、角色、策略、智能体、工作流、发布和套餐均版本化。
- 高风险变更使用幂等、乐观锁、审批和审计回执。

## 2. 数据域与真源

| 数据域 | 真源 | 主要消费者 |
|---|---|---|
| 身份 | `users`、`auth_sessions`、身份源映射 | 全部控制台和服务 |
| 租户 | `organizations`、`organization_memberships` | 策略、计费、运行、业务模块 |
| 权限 | `roles`、`role_bindings`、`policy_rules`、`resource_relations` | Policy Decision Point |
| 商业权益 | `plans`、`subscriptions`、`entitlements` | 网关、页面能力清单、计量 |
| 运行配置 | `regions`、`deployments`、`provider_routes`、`model_bindings` | API、Worker、Scheduler |
| 计量成本 | `usage_events`、`reservations`、`provider_cost_events` | 配额、预算、账单、毛利 |
| 发布 | `release_artifacts`、`rollouts`、`deployment_receipts` | 部署控制器、母控制台 |
| 审计 | `audit_events`、不可变归档 | 平台/公司审计、SIEM |
| 企业资产 | 项目、数据、知识、智能体、工作流 | 公司与用户控制台 |
| 个人资产 | 任务、对话、记忆、偏好、通知 | 用户控制台 |

## 3. 公共字段约定

所有核心表至少包含：

```text
id BIGINT internal only
uid TEXT/UUID external stable id
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
version BIGINT optimistic concurrency
created_by / updated_by actor reference
```

租户表额外包含：

```text
organization_id BIGINT NOT NULL
region_id BIGINT NOT NULL（区域内数据；纯全局元数据可省略）
data_classification TEXT
deleted_at TIMESTAMPTZ NULL（需软删的资源）
```

约束标准：

- 唯一键至少包含 `organization_id`，例如 `(organization_id, project_key)`。
- 租户内外键采用复合约束，防止 A 公司资源引用 B 公司资源。
- 金额使用整数微单位或 `NUMERIC`，禁止浮点累计。
- 时间统一 UTC，展示层按用户时区转换。
- JSONB 仅用于真正开放的扩展属性；身份、权限、金额、状态和关系必须正规化。

## 4. 平台与租户核心实体

### 4.1 公司与成员

```text
organizations
- uid, name, slug, legal_name
- lifecycle_status
- primary_region_id
- deployment_mode: shared/dedicated/private
- release_channel
- data_classification_max
- suspension_reason, suspended_at
- deletion_scheduled_at

organization_domains
- organization_id, domain, verification_status, sso_required

organization_memberships
- organization_id, user_id
- status: invited/active/suspended/left
- primary_department_id
- joined_at, left_at
- invitation_id

organization_lifecycle_events
- organization_id, from_status, to_status
- reason_code, effective_at, actor_id, audit_event_id
```

`organizations.status` 不只用于展示，认证、任务入队、Worker 执行、模型调用和导出都必须检查。

### 4.2 部门、团队与角色

```text
departments
- organization_id, parent_id, name, code
- path, depth, manager_membership_id, status

department_members
- organization_id, department_id, membership_id
- title, is_primary, starts_at, ends_at

teams
- organization_id, name, purpose, expires_at

team_members
- organization_id, team_id, membership_id

roles
- scope_type: platform/organization
- organization_id NULL for platform or system templates
- code, name, is_system, version, status

permissions
- code, resource_type, action, risk_level

role_permissions
- role_id, permission_id, constraint_json

role_bindings
- organization_id, principal_type, principal_id
- role_id, scope_type, scope_id, starts_at, expires_at

policy_rules
- organization_id, effect allow/deny
- action_pattern, resource_type, condition_json, priority, version

resource_relations
- organization_id, subject_type/id
- relation, resource_type/id, expires_at
```

部门是正式组织树，团队是可过期的协作组；不使用 JSON 成员数组替代关系表。

### 4.3 套餐、订阅和模块权益

```text
modules
- code, version, status, manifest_json
- supported_regions, dependencies

capabilities
- module_id, code, permission_code
- metering_metric, risk_level

plans
- code, version, name, currency, billing_period, status

plan_entitlements
- plan_id, capability_id
- limit_value, limit_unit, overage_policy

subscriptions
- organization_id, plan_id
- status, starts_at, trial_ends_at, current_period_end
- grace_ends_at, cancel_at, external_billing_ref

organization_entitlement_overrides
- organization_id, capability_id
- value, starts_at, expires_at, reason, approval_id

entitlement_snapshots
- organization_id, version, effective_json, generated_at
```

权益覆盖必须过期，不能长期用手工 override 代替真实套餐。套餐版本一旦被订阅不可原地改写；新商业规则创建新版本。

### 4.4 Feature Flag 与 Kill Switch

```text
feature_flags
- key, owner, description, lifecycle_status
- default_variant, expires_at

feature_flag_rules
- flag_id, environment
- scope_type: global/plan/org/cohort/user
- scope_id, region_id, release_channel
- percentage, variant, starts_at, ends_at, priority

kill_switches
- capability_code, environment, region_id
- state, reason, activated_by, expires_at
```

Feature Flag 用于发布与实验；Entitlement 用于商业购买；Kill Switch 用于事故止血。三者独立存储、独立权限、统一在决策服务中合并。

## 5. 部署、模型与成本实体

### 5.1 区域与部署

```text
deployment_regions
- code, name, provider, jurisdiction
- status, capabilities, capacity_state
- db_cluster_ref, cache_cluster_ref, object_store_ref, queue_ref

tenant_deployments
- organization_id, region_id, environment
- deployment_mode, status, release_version_id
- data_residency_policy_id, encryption_key_ref

region_migration_jobs
- organization_id, source_region_id, target_region_id
- state, copy_checkpoint, validation_receipt, rollback_until

disaster_recovery_tests
- region_id, tested_at, scenario, rto_seconds, rpo_seconds, result
```

每家公司首版只有一个主区域。跨区复制、迁移或导出必须产生显式工单、客户/平台授权和审计。

### 5.2 供应商与模型

```text
provider_accounts
- provider_code, ownership: platform/byok
- organization_id NULL for shared platform account
- region_id, secret_ref, status
- data_retention_mode, processing_regions, contract_tags

model_catalog
- provider_code, exact_model_id, display_name
- modalities, context_window, data_policy_tags
- pricing_version, lifecycle_status

model_readiness
- provider_account_id, exact_model_id, region_id
- probe_status, last_success_at, evidence_ref

model_policy_versions
- organization_id, name, version, status
- data_classification_rules, budget_behavior

task_model_bindings
- organization_id, project_id NULL
- feature_code, policy_version_id
- primary_model_id, fallback_chain, max_cost, status
```

模型切换创建新策略版本并灰度；不直接修改全局环境变量作为长期配置。模型 ID 必须精确，不使用模糊别名掩盖实际调用模型。

### 5.3 配额、用量、成本和账单

```text
quota_definitions
- metric_code, unit, window, enforcement_mode

quota_allocations
- organization_id, scope_type/id
- metric_code, limit_value, starts_at, ends_at
- warning_threshold, hard_stop_threshold, overage_policy

usage_reservations
- organization_id, request_id, idempotency_key
- metric_code, reserved_value, estimated_cost
- state, expires_at

billable_usage_events
- organization_id, membership_id, project_id
- module_code, feature_code, metric_code, quantity
- request_id, reservation_id, occurred_at

provider_cost_events
- organization_id, project_id, provider_account_id
- exact_model_id, input/output tokens, cost_micro_usd
- provider_request_id, pricing_version, occurred_at

usage_rollups
- organization_id, scope_type/id, metric_code, window_start
- quantity, provider_cost, billable_amount

invoices
- organization_id, billing_period, currency
- subtotal, credits, tax, total, status, external_ref
```

供应商成本与客户计费用量分表。执行路径固定为：

`reserve → provider_start → settle/release → aggregate → reconcile → invoice`。

同一 `idempotency_key` 只能产生一个有效预留和一组计量事件。供应商超时后由对账任务根据外部请求 ID 结算，防止重复扣费或漏记。

## 6. 企业资产实体

### 6.1 项目和资源授权

```text
projects
- organization_id, owning_department_id
- name, key, visibility, data_classification
- owner_membership_id, status, archived_at

project_members
- organization_id, project_id, membership_id
- role, starts_at, expires_at

resource_grants
- organization_id, resource_type/id
- principal_type/id, permission_level
- constraints_json, expires_at, granted_by
```

已有 own/assigned/member/public/restricted 语义迁移到此模型，并补齐组织复合约束。

### 6.2 企业知识

```text
knowledge_spaces
- organization_id, project_id NULL, name
- data_classification, retention_policy_id, status

knowledge_sources
- organization_id, space_id, source_type, secret_ref
- sync_policy, status, last_sync_at

knowledge_documents
- organization_id, space_id, source_id
- title, content_ref, source_uri, checksum, version

knowledge_chunks
- organization_id, document_id, embedding_ref
- source_offsets, model_id, classification

knowledge_acl
- organization_id, space_id/document_id
- principal_type/id, permission

knowledge_ingestion_runs
- organization_id, source_id, state, counters, error_summary
```

检索必须先按组织与 ACL 过滤，再做关键词/向量排序；不能先全库召回再在应用层删除其他租户结果。

### 6.3 企业智能体

```text
agent_definitions
- organization_id, name, owner_membership_id, status

agent_versions
- organization_id, agent_id, version
- system_instructions_ref, model_policy_id
- memory_policy, tool_policy_id, evaluation_set_id
- state: draft/review/approved/published/retired

agent_grants
- organization_id, agent_id, principal_type/id, permission

agent_runs
- organization_id, agent_version_id, project_id
- initiated_by, request_id, state, cost, audit_trace_id

agent_approvals / agent_evaluations
- organization_id, agent_version_id, result, evidence_ref
```

“智能体定义”“模型策略”“工具权限”“记忆策略”“自主行动等级”分别版本化，避免一段提示词同时承载所有治理规则。

### 6.4 工作流

```text
workflow_definitions
- organization_id, name, owner_membership_id, status

workflow_versions
- organization_id, workflow_id, version
- graph_json, input_schema, output_schema, policy_version

workflow_triggers
- organization_id, workflow_version_id, type, config_ref, status

workflow_runs
- organization_id, workflow_version_id, project_id
- initiated_by, idempotency_key, state, trace_id

workflow_steps
- organization_id, run_id, node_id, state
- attempt, input_hash, output_ref, cost, started_at, ended_at

workflow_approvals
- organization_id, run_id, node_id
- requested_from, decision, reason, decided_at

workflow_secret_refs
- organization_id, workflow_id, secret_ref, allowed_steps
```

运行必须绑定不可变工作流版本；已发布版本不能原地修改。密钥引用只在执行器解析，编辑器和日志不返回明文。

## 7. 个人资产实体

```text
personal_preferences
- organization_id, user_id, locale, timezone, ui_json, ai_json

notification_endpoints
- organization_id, user_id, channel, verified_at, status

notification_preferences
- organization_id, user_id, event_type, channel
- enabled, digest_mode, quiet_hours

notification_events
- organization_id, user_id, type, resource_ref, dedupe_key

notification_deliveries
- organization_id, event_id, endpoint_id
- state, attempts, delivered_at, error_code

auth_sessions
- organization_id, user_id, token_family_hash
- device_summary, ip_hash, authn_strength
- last_seen_at, expires_at, revoked_at, revoke_reason
```

现有顾问会话、消息、记忆候选、事实、行动草稿和记忆事件模型保留，补充个人导出、全部删除和法律保留状态。

## 8. API 命名空间

### 8.1 母控制台 `/api/platform/v1`

| 资源 | 关键接口 |
|---|---|
| 公司 | `POST /organizations`、`GET /organizations`、`GET /organizations/{uid}` |
| 生命周期 | `POST /organizations/{uid}:activate`、`:suspend`、`:reactivate`、`:disable`、`:schedule-delete` |
| 管理员 | `POST /organizations/{uid}/owners`、`DELETE .../owners/{membership}` |
| 套餐模块 | `/plans`、`/modules`、`/subscriptions`、`/entitlement-overrides` |
| 区域部署 | `/regions`、`/deployments`、`/region-migrations`、`/dr-tests` |
| 供应商模型 | `/provider-accounts`、`/models`、`/model-bindings`、`:probe` |
| 配额成本 | `/quota-templates`、`/usage`、`/provider-costs`、`/reconciliation` |
| Flag/发布 | `/flags`、`/kill-switches`、`/releases`、`/rollouts`、`:rollback` |
| 运行监控 | `/runtime/services`、`/queues`、`/slo`、`/incidents` |
| 安全审计 | `/platform-users`、`/audit-events`、`/support-access`、`/emergency-sessions` |

### 8.2 公司控制台 `/api/org/v1`

| 资源 | 关键接口 |
|---|---|
| 组织 | `/organization`、`/domains`、`/security-policy` |
| 人员 | `/departments`、`/members`、`/teams`、`/roles`、`/role-bindings` |
| 数据 | `/data-policies`、`/resource-grants`、`/projects` |
| 知识 | `/knowledge/spaces`、`/sources`、`/documents`、`/ingestion-runs` |
| 智能体 | `/agents`、`/agent-versions`、`:submit`、`:publish`、`/evaluations` |
| 工作流 | `/workflows`、`/workflow-versions`、`:publish`、`/runs`、`/approvals` |
| 用量 | `/usage`、`/budgets`、`/quota-allocations`、`/cost-centers` |
| 审计 | `/audit-events`、`/sensitive-access`、`/exports` |

### 8.3 用户控制台 `/api/me/v1`

```text
/home
/tasks
/projects
/data/saved-queries
/exports
/assistant/threads
/assistant/threads/{uid}/messages:stream
/memory/candidates
/memory/facts
/memory:pause
/memory:export
/memory:delete-all
/notifications
/notification-preferences
/authorizations
/sessions
/usage
/preferences
```

`/me` 接口从会话获取用户与公司，不允许通过参数替换为他人用户 ID。

## 9. 写接口统一协议

所有写请求：

- Header 必须包含 `Idempotency-Key`（安全的纯 UI 偏好变更可豁免）。
- 更新带 `If-Match`/实体 `version`，防止覆盖并发修改。
- L2 以上包含 `reason`；L3 包含 Step-up 证明；L4 包含批准单 UID。
- 支持 `dry_run=true` 返回影响预览，不执行变更。
- 成功返回 `operation_uid`、新版本和 `audit_receipt_uid`。
- 长操作返回 `202 Accepted` 和可轮询/SSE 的 Operation 资源。

示例：

```json
{
  "operation_uid": "op_01...",
  "status": "accepted",
  "resource": {"type": "organization", "uid": "org_01...", "version": 12},
  "audit_receipt_uid": "aud_01..."
}
```

### 9.1 错误码

| HTTP | 代码 | 含义 |
|---:|---|---|
| 400 | `INVALID_ARGUMENT` | 参数或状态转换非法 |
| 401 | `AUTHENTICATION_REQUIRED` | 未登录或会话失效 |
| 401 | `STEP_UP_REQUIRED` | 需要更强认证 |
| 403 | `ENTITLEMENT_REQUIRED` | 套餐/模块未授权 |
| 403 | `POLICY_DENIED` | 角色、关系或数据策略拒绝 |
| 403 | `ORG_SUSPENDED` | 公司状态不允许 |
| 409 | `VERSION_CONFLICT` | 乐观锁冲突 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一幂等键请求内容不同 |
| 422 | `APPROVAL_REQUIRED` | 需审批后执行 |
| 429 | `QUOTA_EXCEEDED` | 配额或预算阻断 |
| 503 | `REGION_UNAVAILABLE` | 目标区域不可用 |
| 503 | `MODEL_NOT_READY` | 精确模型未通过就绪闸 |

错误响应必须包含 `request_id`、稳定代码和可行动说明，不返回其他租户是否存在。

## 10. 领域事件

事件采用事务 Outbox 写入，消息至少一次投递，消费者按 `event_id` 幂等。事件不携带密钥和大段敏感正文。

### 10.1 事件信封

```json
{
  "event_id": "evt_01...",
  "event_type": "organization.suspended.v1",
  "occurred_at": "2026-07-15T20:00:00Z",
  "organization_id": "org_01...",
  "region_id": "us-east",
  "actor": {"type": "platform_user", "id": "pusr_..."},
  "resource": {"type": "organization", "id": "org_01..."},
  "request_id": "req_...",
  "trace_id": "trc_...",
  "payload": {"from": "active", "to": "suspended", "reason_code": "PAYMENT_OVERDUE"},
  "schema_version": 1
}
```

### 10.2 核心事件目录

| 域 | 事件 |
|---|---|
| 公司 | `organization.provisioned/activated/suspended/reactivated/disabled/deletion_scheduled` |
| 成员 | `membership.invited/activated/suspended/left` |
| 权限 | `role.created/binding_changed/policy_published/access_denied` |
| 商业 | `subscription.changed/entitlement.changed/quota.changed` |
| 模型 | `provider.probe_changed/model_binding.published/budget.blocked` |
| 用量 | `usage.reserved/settled/released/reconciled` |
| 知识 | `knowledge.sync_started/completed/failed/document_deleted` |
| 智能体 | `agent.version_submitted/published/run_started/run_completed/action_pending` |
| 工作流 | `workflow.published/run_state_changed/approval_requested/decided` |
| 发布 | `release.approved/rollout_started/paused/rolled_back/completed` |
| 安全 | `support_access.requested/approved/revoked/emergency_session.*` |
| 隐私 | `personal_data.export_requested/completed/deletion_requested/completed` |

## 11. 审计事件与业务事件的区别

- 领域事件驱动系统协作，可重放业务状态。
- 审计事件回答谁做了什么以及依据，必须追加、长期保留、不可由业务消费者修改。
- 同一变更通常同时产生领域事件和审计事件，并以 `request_id/operation_uid` 关联。
- 系统自动行为的 Actor 是工作负载身份，同时记录触发它的原始人类/事件。

## 12. 配置解析契约

解析函数输入完整上下文，返回最终值和来源链：

```json
{
  "key": "ai.model.policy",
  "effective_value": "model_policy_17",
  "sources": [
    {"level": "platform_safety", "version": 8},
    {"level": "region", "scope": "us-east", "version": 3},
    {"level": "plan", "scope": "enterprise-v4", "version": 4},
    {"level": "organization", "scope": "org_01...", "version": 12},
    {"level": "project", "scope": "prj_01...", "version": 2}
  ]
}
```

禁止用“最后写入覆盖所有值”的简单配置表。每层允许的键和可扩大/仅可收紧属性由 Schema 定义。

## 13. 删除、导出与保留

### 13.1 删除流程

1. 验证权限、MFA、审批和法律保留。
2. 生成删除清单：数据库、对象、索引、缓存、队列、备份。
3. 停止新写入和后台任务。
4. 标记逻辑删除并进入可撤销等待期。
5. 清除派生索引、对象和主库数据。
6. 记录备份自然过期日期；不承诺即时修改不可变备份。
7. 运行可验证扫描，生成删除证明。
8. 保留最小审计墓碑，不保留业务正文。

### 13.2 导出流程

- 导出任务绑定公司、用户、资源范围和策略版本。
- 文件加密、短期链接、下载次数限制和水印。
- 下载本身是敏感访问事件。
- 大导出采用审批、行数预估和预算控制。
- 个人 DSAR 与公司业务导出分开，不能互相替代。

## 14. 遗留数据迁移

### 14.1 迁移波次

1. **盘点**：列出每张表、缓存、对象路径、队列、索引的归属字段和访问路径。
2. **加字段**：先可空添加 `organization_id`，写路径双写，监控未归属率。
3. **回填**：现有内部数据明确回填公司 1，生成行数与哈希证据。
4. **读切换**：Repository 强制租户条件，影子比较旧/新查询结果。
5. **约束**：设 `NOT NULL`、复合唯一/FK、RLS 和 fail-closed 上下文。
6. **清旁路**：删除默认公司回退和旧无范围查询。
7. **双租户验证**：创建空白测试公司，执行完整交叉访问与生命周期测试。

### 14.2 迁移门槛

- 每张表回填前后行数、空值、唯一冲突和孤儿关系均有报告。
- 用量/成本的组织、功能、供应商归属率 ≥98%；未知项进入明确隔离账户。
- 迁移可向前/向后兼容一个发布窗口。
- 回滚不会重新打开无租户查询。

## 15. 模块 Manifest 样例

```yaml
module: vkpi.market_intelligence
version: 1.0.0
dependencies:
  - platform.projects >= 1.0
capabilities:
  - code: kol.search
    permission: market.kol.search
    meter: kol_search_request
    risk: L1
  - code: report.export
    permission: report.export
    meter: exported_row
    risk: L3
data:
  classification_max: L2
  retention_policy: tenant_default
models:
  task_bindings:
    - market_summary
events:
  - market.search.completed.v1
audit:
  - report.exported
kill_switch: market-intelligence-write
```

Manifest 在 CI 中校验；缺权限、计量、删除或审计声明的模块不得发布。

## 16. API 与数据验收

- OpenAPI 对三套命名空间分别发布，所有写接口有幂等和错误契约。
- 所有数据库迁移有前向/回退/数据验证脚本。
- 所有租户表通过静态 Schema 检查和双租户动态测试。
- 100% 异步消息带组织、请求、追踪和幂等标识。
- 100% AI/抓取/存储付费操作产生预留、用量和成本事件。
- 配置解析可返回来源链，支持离线重放。
- 删除和导出可生成审计证明。
