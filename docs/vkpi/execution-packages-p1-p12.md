# V-KPI v5.3.1 Execution Packages P1-P12

整理日期：2026-05-19
目标仓库：`/Users/bibiboer/Documents/V-KPI——marketing`

这份文档定义 v5.3.1 后续执行包。原则是小包、可验收、可回滚，先修技术地基，再接智能化能力。

## 0. 总顺序

执行顺序固定为：

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

关键调整：

```text
P5 必须在 P4 前。
推荐 cron 开始跑之前，必须先有成本记录、预算查询、warning、hard stop 和 provider/cron 维度可观测。
```

## 1. P1 技术校准

目标：用最小代码改动补齐后续智能化包需要的技术底座。

范围：

```text
migrations/057_vkpi_ai_cost_budget.sql
backend/app/services/vkpi/llm_gateway.py
backend/app/services/vkpi/budget_guard.py
backend/app/api/routers/vkpi_budgets.py
backend/app/main.py
```

### 1.1 Migration

新增：

```text
migrations/057_vkpi_ai_cost_budget.sql
```

只新增：

```text
vkpi_ai_cost_ledger
vkpi_provider_budget_caps
```

不要碰现有：

```text
vkpi_cost_ledger
```

字段风格：

```text
metadata_json TEXT NOT NULL DEFAULT '{}'
```

如果生产迁移依赖 `_POSTGRES_MIGRATION_SEQUENCE`，P1 必须把 `057_vkpi_ai_cost_budget.sql` 加入序列，或明确写成手动 migration。

### 1.2 LLM Gateway

修改：

```text
backend/app/services/vkpi/llm_gateway.py
```

只做兼容扩展：

```text
invoke / chat / record_call 增加 optional cost_tag
invoke / chat / record_call 增加 optional triggered_by
没有传 cost_tag 时旧逻辑继续跑
写入 vkpi_ai_cost_ledger 失败不能影响主调用
```

不要重写整个 gateway，不要改变现有调用方默认行为。

### 1.3 Budget Guard Service

新增：

```text
backend/app/services/vkpi/budget_guard.py
```

提供：

```python
check_budget(scope: str, estimated_cost: float) -> bool
record_cost(...)
get_budget_status(...)
```

第一版只服务 P1，不需要接所有 provider，不需要改所有 cron。

### 1.4 Admin Router

新增：

```text
backend/app/api/routers/vkpi_budgets.py
```

Endpoints：

```text
GET  /api/admin/vkpi/budgets
POST /api/admin/vkpi/budgets/{scope}/update
GET  /api/admin/vkpi/budgets/usage-by-provider
GET  /api/admin/vkpi/budgets/usage-by-cron
```

注册：

```text
backend/app/main.py
```

P1 不做前端页面。BudgetMonitorPage 放到 P5。

### 1.5 P1 验收

```bash
python3 -m py_compile \
  backend/app/services/vkpi/llm_gateway.py \
  backend/app/services/vkpi/budget_guard.py \
  backend/app/api/routers/vkpi_budgets.py
```

```bash
cd frontend && npm run build && cd ..
```

```bash
rg "/v1/" backend/app/api/routers frontend/src/services
```

预期：不要新增新的 `/v1/`。

```bash
rg "vkpi_cost_ledger" migrations/057_vkpi_ai_cost_budget.sql
```

预期：不出现。P1 只能新建 `vkpi_ai_cost_ledger`。

## 2. P2A 历史 Excel 只读审计

目标：先知道历史数据质量，不写主表。

允许范围：

```text
backend/app/services/vkpi/legacy_import_audit.py
scripts/audit_vkpi_legacy_excel.py
docs/audits/YYYY-MM-DD-vkpi-legacy-excel-audit.md
docs/audits/YYYY-MM-DD-vkpi-legacy-excel-audit.csv
```

禁止：

```text
写 kols
写 vkpi_projects
写 vkpi_kol_pool
写 vkpi_cost_ledger
写 vkpi_ai_cost_ledger
```

审计输出至少包含：

```text
总行数
可识别平台/handle 数
重复 KOL 候选
缺联系方式行
缺产品/项目字段行
金额/币种异常
需要人工确认的行
导入风险分级
```

验收：

```text
审计报告可打开
原始 Excel 不被修改
git diff 不包含主表写入代码
```

## 3. P2B 历史导入 Staging

目标：把通过 P2A 审计的历史数据进入 staging，并提供 commit/rollback。

范围：

```text
migrations/058_vkpi_legacy_import.sql
backend/app/services/vkpi/legacy_import_staging.py
backend/app/api/routers/vkpi_legacy_import.py
```

建议 staging 表：

```text
vkpi_legacy_import_batches
vkpi_legacy_import_rows
vkpi_legacy_import_errors
```

必须提供：

```text
dry-run summary
commit
rollback
duplicate strategy
error export
operator audit trail
```

验收：

```text
导入 staging 不影响主表
commit 后能查到目标主表新增或更新数量
rollback 后能撤回本 batch 的写入
重复导入同一 batch 不产生重复主数据
```

## 4. P3 Memory v0

目标：建立 V-KPI 的内部营销记忆层。

范围：

```text
migrations/059_vkpi_memory_tables.sql
backend/app/services/vkpi/memory.py
backend/app/api/routers/vkpi_memory.py
```

Memory v0 先做可解释事实，不做复杂训练。

建议表：

```text
vkpi_memory_entities
vkpi_memory_facts
vkpi_memory_links
vkpi_memory_feedback
vkpi_memory_snapshots
```

字段风格：

```text
identity_json TEXT
fact_json TEXT
source_json TEXT
metadata_json TEXT
```

验收：

```text
能按 KOL / product / staff / project 写入和查询事实
能保留 source_ref 和 confidence
能记录人工反馈
不依赖向量库也能运行
```

## 5. P5 成本可观测 + Budget Guard

目标：在推荐 cron 前接好成本守门。

P5 基于 P1 的表和 service，继续扩展：

```text
provider 维度预算
cron 维度预算
staff/team 维度预算
task item 维度预算
warning / hard stop
fallback_action
前端 BudgetMonitorPage
```

范围：

```text
backend/app/services/vkpi/budget_guard.py
backend/app/services/vkpi/llm_gateway.py
backend/app/api/routers/vkpi_budgets.py
frontend/src/services/vkpi.ui-api.ts
frontend/src/components/admin/vkpi/pages/BudgetMonitorPage.tsx
```

验收：

```text
预算超 warning 能看到状态
预算超 hard stop 能阻止非必要任务
LLM 记录失败不影响主调用
BudgetMonitorPage 能看到 provider/cron 使用量
推荐任务接入前已有成本守门
```

## 6. P4 推荐 v0 三场景

目标：在已有 Budget Guard 后接第一版推荐。

三场景：

```text
产品找 KOL
KOL 找产品
项目/员工下一步动作建议
```

推荐输入：

```text
KOL pool
项目状态
产品目录
Memory v0
内容脑字段如果已存在则读取，不存在则降级
预算状态
```

验收：

```text
推荐任务先检查 budget
每条推荐有 evidence
每条推荐能被 claim / dismiss / create project
推荐结果能回写 outcome
```

## 7. P6 内容脑 v0

目标：对帖子和媒体做内容理解。

字段位置优先：

```text
vkpi_industry_account_posts
vkpi_industry_post_media
```

不要把帖子级标签放进：

```text
vkpi_industry_account_snapshots
```

输出：

```text
content_tags_json
product_intents_json
risk_flags_json
brand_mentions_json
ai_summary
analyzed_at
analysis_version
```

验收：

```text
同一账号多个帖子可分别分析
同一帖子多媒体可分别记录
重新分析不会覆盖原始抓取事实
失败项可重试
```

## 8. P7 异常检测

目标：把管理层需要立即处理的问题变成可审计 alerts。

第一版规则：

```text
项目长时间未推进
KOL 重复联系风险
低 ROI 或成本异常
链接失效或跳转异常
未匹配销售归因
AI/provider 预算超限
```

验收：

```text
alert 有 source_ref
alert 有 owner / severity / status
alert 可 resolve / dismiss
规则结果可复算
```

## 9. P8 竞品脑 v0

目标：把竞品账号、内容、产品提及和市场信号形成可查询事实。

范围优先基于已有：

```text
vkpi_industry_projects
vkpi_industry_accounts
vkpi_industry_account_posts
vkpi_industry_post_media
```

验收：

```text
可按产品/平台/竞品查询内容
可区分账号级指标和帖子级事实
可输出进入推荐层的 evidence
```

## 10. P9 自然语言搜索

目标：让内部员工用自然语言检索 KOL、项目、产品、内容和历史记忆。

第一版不要求训练模型，优先 retrieval：

```text
关键词解析
结构化过滤
Memory 检索
证据返回
权限过滤
```

验收：

```text
搜索结果有 source
operator 不能看到越权联系方式或项目
失败时返回可理解的 query correction
```

## 11. P10 Learning Loop

目标：把人工处理结果回写，提升后续推荐和搜索。

输入：

```text
recommendation accepted/dismissed
project outcome
staff feedback
alert resolution
import correction
```

输出：

```text
Memory feedback
recommendation weight adjustment
bad pattern suppression
```

验收：

```text
每次学习有可审计记录
能解释为什么某类推荐被降权
不做不可回滚的黑盒覆盖
```

## 12. P12 RBAC / Magic Link

目标：完善内部权限和轻量访问能力。

范围：

```text
staff role / permission
team scope
contact visibility
magic link access
audit trail
```

验收：

```text
operator / lead / admin 范围不同
联系方式访问有审计
magic link 有过期和撤销
敏感页面不能匿名长期访问
```

## 13. P11 SSE 可选最后

目标：只在已有任务链稳定后增加实时体验。

P11 放最后，因为它不是智能化地基，也不是推荐质量的前置条件。

适合接入：

```text
long-running import progress
recommendation cron progress
content analysis progress
budget warning events
```

验收：

```text
断线可恢复查询状态
没有 SSE 时页面仍可轮询
不会因为前端连接失败影响后台任务
```
