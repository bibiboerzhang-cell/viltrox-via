# V-KPI v5.3.1 Naming And Compatibility Conventions

整理日期：2026-05-19

这份文档是 v5.3.1 后续代码包的命名约束。目的不是统一审美，而是避免再次出现战略文档和当前仓库不贴合的问题。

## 1. API Namespace

前端业务调用优先使用：

```text
/api/marketing/...
```

后端 router 注册使用：

```text
/api/admin/vkpi/...
```

当前 `backend/app/main.py` 已有兼容转发层，将 `/api/marketing/...` 转到 `/api/admin/vkpi/...`。后续不要新增：

```text
/v1/*
/api/v1/*
/api/vkpi/v1/*
```

新 router 文件命名建议：

```text
backend/app/api/routers/vkpi_budgets.py
backend/app/api/routers/vkpi_legacy_import.py
backend/app/api/routers/vkpi_memory.py
backend/app/api/routers/vkpi_recommendations.py
```

router prefix 示例：

```python
APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-budgets"])
```

前端 service 可以按现有习惯放在：

```text
frontend/src/services/vkpi.ui-api.ts
frontend/src/services/vkpi/*.ts
```

P1 不新增前端 service，P5 再接 BudgetMonitorPage。

## 2. Migration 命名

后续 SQL 文件放在 repo 根目录：

```text
migrations/
```

不要写成：

```text
backend/app/db/migrations/
```

当前 v5.3.1 预留顺序：

```text
057_vkpi_ai_cost_budget.sql
058_vkpi_legacy_import.sql
059_vkpi_memory_tables.sql
```

命名规则：

```text
NNN_vkpi_<domain>.sql
```

如果有 down migration，使用：

```text
NNN_vkpi_<domain>_down.sql
```

任何新增 migration 都要检查 `backend/app/db/connection.py` 的 `_POSTGRES_MIGRATION_SEQUENCE`。如果不加入自动序列，文档或提交说明里必须标记为手动执行。

## 3. 表命名

V-KPI 专属表使用：

```text
vkpi_<domain>
```

复用现有主数据时，不要复制成另一套 V-KPI 表：

```text
staff       # 员工身份
users       # 登录用户
kols        # KOL 主体
orders      # Shopify / commerce 订单
```

禁止新建：

```text
vkpi_staff
employees
vkpi_employees
vkpi_kols_main
```

如果确实需要 V-KPI 扩展员工信息，使用：

```text
vkpi_staff_profiles
```

并通过：

```text
staff_id BIGINT REFERENCES staff(id)
```

关联现有员工。

## 4. 成本表命名

现有业务成本表：

```text
vkpi_cost_ledger
```

用于样品、物流、现金费用、关税、产品成本、项目成本等业务成本。

P1 新增 AI/provider 成本表：

```text
vkpi_ai_cost_ledger
```

用于 LLM、爬虫、provider、cron、task item 的调用成本记录。

两者不能混用：

```text
vkpi_cost_ledger     # business/project cost
vkpi_ai_cost_ledger  # AI/provider execution cost
```

P1 只能新增 `vkpi_ai_cost_ledger`，不要修改或复用 `vkpi_cost_ledger`。

## 5. JSON 字段风格

默认使用 TEXT 存 JSON 字符串：

```sql
metadata_json TEXT NOT NULL DEFAULT '{}'
identity_json TEXT NOT NULL DEFAULT '{}'
risk_flags_json TEXT NOT NULL DEFAULT '[]'
raw_payload_json TEXT NOT NULL DEFAULT '{}'
```

字段后缀：

```text
*_json
```

Python 层负责 parse / dump。不要把 API response 暴露为 `metadata_json` 原始字符串，service 或 router 层应转换为：

```text
metadata
identity
risk_flags
raw_payload
```

默认不要使用：

```sql
JSONB
TEXT[]
BIGINT[]
```

只有同时满足以下条件，才允许使用 Postgres-only 类型：

```text
1. 该表不会走 SQLite 本地兼容层；
2. migration 注释写明 Postgres-only；
3. 测试或 fallback 覆盖了本地开发路径；
4. service 层没有假设 SQLite 可以执行同样 SQL。
```

## 6. ID 与引用字段

常规外键使用单数 `_id`：

```text
staff_id
kol_id
kol_pool_id
project_id
task_item_id
recommendation_id
```

不要使用数组字段保存关系：

```text
staff_ids
kol_ids
BIGINT[]
TEXT[]
```

多对多关系使用关联表：

```text
vkpi_recommendation_kols
vkpi_project_memory_refs
vkpi_legacy_import_row_refs
```

## 7. 时间字段

新表默认使用：

```text
created_at
updated_at
occurred_at
reset_at
processed_at
```

含义：

```text
created_at: 记录创建时间
updated_at: 最近一次人工或系统更新
occurred_at: 成本、事件、调用真实发生时间
reset_at: 预算周期重置时间
processed_at: 导入或异步处理完成时间
```

## 8. P2 Legacy Import 命名

P2 拆成：

```text
P2A legacy Excel read-only audit
P2B legacy import staging + commit/rollback
```

建议命名：

```text
migrations/058_vkpi_legacy_import.sql
backend/app/services/vkpi/legacy_import_audit.py
backend/app/services/vkpi/legacy_import_staging.py
backend/app/api/routers/vkpi_legacy_import.py
```

P2A 输出文件建议：

```text
docs/audits/YYYY-MM-DD-vkpi-legacy-excel-audit.md
docs/audits/YYYY-MM-DD-vkpi-legacy-excel-audit.csv
```

P2B staging 表建议：

```text
vkpi_legacy_import_batches
vkpi_legacy_import_rows
vkpi_legacy_import_errors
```

## 9. P6 内容脑命名

帖子级字段优先落到：

```text
vkpi_industry_account_posts
```

媒体级字段优先落到：

```text
vkpi_industry_post_media
```

不要把帖子级标签直接放到：

```text
vkpi_industry_account_snapshots
```

推荐字段后缀：

```text
content_tags_json
product_intents_json
risk_flags_json
brand_mentions_json
ai_summary
analyzed_at
analysis_version
```

账号快照只保留账号级事实，例如粉丝数、互动率、简介、账号状态、抓取日期。

## 10. Package 命名

后续包名使用执行顺序，而不是战略阶段名：

```text
P1  technical alignment
P2A legacy Excel audit
P2B legacy import staging
P3  memory v0
P5  budget guard full integration
P4  recommendations v0
P6  content brain v0
P7  alerts
P8  competitor brain
P9  natural search
P10 learning loop
P12 RBAC / magic link
P11 SSE optional
```

提交信息示例：

```text
docs(vkpi): add v5.3.1 execution plan
feat(vkpi): add AI cost ledger and budget guard foundation
feat(vkpi): add legacy Excel audit report
feat(vkpi): add memory v0 tables
```
