# V-KPI P2B Legacy Excel Multi-Staging Import Design

整理日期：2026-05-19
来源文件：`Excel/海外市场推广计划表-Viltrox.xlsx`

## 1. 核心判断

`海外市场推广计划表-Viltrox.xlsx` 不是一张 KOL 表，而是一个旧版运营系统导出的混合工作簿。

P2B 不能做“一键导入 KOL”。正确路径是：

```text
拆库 -> 映射 -> 校验 -> staging -> review -> commit -> rollback
```

本包只生成技术方案和 staging migration，不写正式数据库，不创建正式 KOL、项目、成本、内容或舆情记录。

## 2. Pipeline 拆分

P2B 使用 8 条 pipeline。Excel 里的新品立项是产品维度，不是 KOL 维度，必须从合作历史里拆出来。`官方物料排期表` 是官方素材制作/资产排期，不是官媒发布排片，也不能继续作为 skipped sheet 丢失行级审计。

### 2.1 KOL 主档 Staging

来源 sheet：

```text
【红人媒体数据建档与管理】
```

目标 staging：

```text
vkpi_legacy_kol_profiles_staging
```

用途：只承载红人/媒体账号主档，不承载合作项目历史。

字段映射：

```text
平台                 -> platform / normalized_platform
账号/媒体名称         -> handle / normalized_handle
红人媒体名称-平台      -> display_name fallback / dedup hint
红人/编辑姓名         -> display_name fallback
国家                 -> country
州/省(自动提取)       -> region
频道/主页标签         -> category
邮箱                 -> email
电话 / 电话(自动提取) -> phone
地址                 -> address
备注 / 投放排期/历史   -> notes
sheet name           -> source_sheet
Excel row number      -> source_row
raw row JSON          -> raw_row_json
```

校验规则：

```text
platform + handle 是去重核心。
缺联系方式允许进入 staging，但 contact_missing=true。
缺 platform 或 handle 进入 review queue，不允许直接进入正式表。
联系方式字段默认 contact_visibility_level='restricted'。
普通员工默认不能直接看到 email / phone / address。
```

去重逻辑：

```text
dedup_key = normalized_platform || ':' || lower(normalized_handle)
同一 batch 内重复 dedup_key 不阻断 staging，但标记 duplicate_in_batch。
跨 batch 与正式 kols / vkpi_kol_pool 匹配只写 matched_kol_id / matched_kol_pool_id，不直接 merge。
```

### 2.2 合作/项目历史 Staging

来源 sheet：

```text
各产品 sheet，例如 AF 85mm、AF 56mm、Vintage Z1、DC-A1 Monitor 等
```

目标 staging：

```text
vkpi_legacy_cooperations_staging
```

用途：承载历史合作记录，不重复创建 KOL。

字段映射：

```text
红人/媒体 / 账号 / KOL       -> handle / display_name
平台 / 红人媒体名称-平台      -> platform / normalized_platform
产品型号 / sheet name         -> product
项目归属 / 推广项目名称        -> project
合作进度 / 状态              -> status
日期 / 发布时间 / 承诺时间     -> cooperation_date
报价 / 费用 / 金额            -> cost_amount / cost_currency
发布链接 / 回片链接 / 视频链接  -> content_link
结果 / 回片状态 / 成效         -> result
备注                         -> notes
sheet name                   -> source_sheet
Excel row number              -> source_row
```

校验规则：

```text
优先通过 normalized_platform + normalized_handle 匹配 KOL 主档 staging。
再尝试匹配正式 kols / vkpi_kol_pool。
匹配不到进入 unmatched_kol_review。
同一红人跨多个产品合作是正常历史，不视为重复脏数据。
只有 source_sheet + source_row 重复才视为同一源行重复。
```

### 2.3 新品立项 Staging

来源 sheet：

```text
新品立项时间表
```

目标 staging：

```text
vkpi_legacy_launch_plans_staging
```

用途：承载产品上市和推广立项计划，不进入 KOL 项目表。

字段映射：

```text
推广项目名称       -> launch_name
产品型号           -> product_name / product_sku
一级产品类目       -> category_primary
二级产品类目       -> category_secondary
产品发布日期       -> launch_date
目标区域 / 市场     -> target_region
官媒运营排期        -> official_material_ref
红人推广计划        -> kol_plan_ref
网页链接 / 产品链接  -> product_page_url
负责人 / 对接人      -> campaign_owner
备注               -> notes
```

下游用途：

```text
P3 product / market memory 的产品上市输入。
P4 新品上市匹配推荐的触发源。
官方内容、合作历史、成本数据按 product_name / product_sku 回连 launch_plan。
```

### 2.4 官方内容 Staging

来源 sheet：

```text
官媒运营排片表
```

目标 staging：

```text
vkpi_legacy_official_content_staging
```

用途：承载官方账号内容排期和发布记录，不进入 KOL pool。

字段映射：

```text
账号名称 / 产品-平台-账号 -> official_account
发布平台                 -> platform
预计/实际发布时间         -> publish_date
内容类型 / 内容概述       -> content_type / title
产品型号                 -> product
发布链接                 -> link
制作进度 / 状态           -> status
运营人/发帖人             -> owner
备注 / 关键词/tag         -> notes
```

### 2.5 官方物料 Staging

来源 sheet：

```text
官方物料排期表
```

目标 staging：

```text
vkpi_legacy_official_materials_staging
```

用途：承载官方素材制作、交付、下载/预览资产和官媒使用引用，不进入 KOL pool，也不直接写正式 official content。

字段映射：

```text
上市时间-产品型号/项目名称 -> launch_ref
产品型号                 -> product_sku / product_name
对接/协作人              -> owner
制作进度                 -> production_status
所属项目                 -> project
内容类型                 -> content_type
内容描述                 -> content_description
参考文档                 -> reference_doc
内容格式                 -> content_format
提需时间                 -> request_date
目标交付时间             -> target_delivery_date
下载/预览链接             -> asset_link
尺寸规格                 -> size_spec
发布状态                 -> publish_status
产品发布时间             -> product_publish_date
制作团队                 -> production_team
预算                     -> budget_amount / budget_currency
官媒使用记录             -> official_usage_ref
父记录                   -> parent_ref
```

校验规则：

```text
缺 product_name 且缺 launch_ref -> review queue。
缺 content_description 且缺 asset_link -> review queue。
所有行保留 source_sheet + source_row，不再整体 skipped。
```

### 2.6 产品成本 Staging

来源 sheet：

```text
产品成本信息表
```

目标 staging：

```text
vkpi_legacy_product_costs_staging
```

用途：承载 SKU / 产品成本，不进入 KOL 或项目表。

字段映射：

```text
产品型号       -> sku / product_name
采购成本(CNY) -> cost
币种           -> currency, default CNY when source column name includes CNY
区域 / 地区     -> region
生效日期        -> effective_date
备注           -> notes
```

### 2.7 风险名单 Staging

来源 sheet：

```text
【红人媒体观察名单】
```

目标 staging：

```text
vkpi_legacy_risk_watchlist_staging
```

用途：承载风险名单，不作为普通候选红人导入。

字段映射：

```text
平台                 -> platform
红人姓名/账号名       -> handle / display_name
风险类型             -> risk_type
备注/建议             -> risk_reason / notes
拖片时长 / 回片状态     -> severity hint / status
内容发布链接 / 辅证资料  -> evidence
```

展示规则：

```text
后续 commit 后只作为 KOL profile 风险提示展示。
不得进入普通候选池。
不得自动创建合作项目。
```

分组标题行处理：

```text
红人姓名/账号名 为空且频道/主页链接 为空的行视为 Excel 分组 marker。
这类行不进入 risk_watchlist staging，也不进入 review_queue。
```

### 2.8 舆情 / VOC Staging

来源 sheet：

```text
海外舆情监控表
```

目标 staging：

```text
vkpi_legacy_voc_alerts_staging
```

用途：承载 VOC / 舆情 / 用户反馈，后续进入 comment intelligence 或 alerts。

字段映射：

```text
舆情来源平台       -> platform
相关产品型号       -> product
舆情类型           -> issue_type
舆情性质           -> sentiment
原文 / 舆情概述     -> content
链接 / 截图         -> link / evidence
日期               -> issue_date
严重性             -> severity
处理状态           -> status
舆情反馈人         -> owner
```

## 3. P2B-2 数据质量留痕

最终验证 batch：

```text
batch_uid=vkpi_20260519033921_b36c6f28ec8d
batch_id=6
```

Pipeline 行数：

```text
kol_profiles=1039
cooperations=2423
launch_plans=52
official_content=2202
official_materials=241
product_costs=834
risk_watchlist=13
voc_alerts=37
skipped=0 rows / 0 sheets
committed_refs=0
```

关键修复：

```text
official_content:
  DISCORD 已归一化为 discord。
  产品-平台-账号 字段支持 account/platform fallback。
  validation_error 从 465 降到 34，剩余主要为 missing_official_account。

risk_watchlist:
  当前 Excel 17 行中有 4 行为空分组 marker。
  parser 已跳过 marker。
  实际进入 staging 的风险业务行为 13 行。

official_materials:
  官方物料排期表 241 行已进入 vkpi_legacy_official_materials_staging。
  12 行进入 review_queue，主要是 missing_material_content / missing_product_identity。

cooperations:
  total_rows=2423
  unique_handles=715
  unique_urls=689
  no_identifier=38
  has_handle=2385
  has_url=2360
```

P2C 去重输入结论：

```text
cooperations 侧 handle 提取质量可以进入 P2C。
P2C 应优先使用 dedup_key = normalized_platform || ':' || lower(normalized_handle)。
content_link 只能作为辅助证据，因为当前字段混有主页链接和内容链接。
```

## 4. Review Queue 设计

统一 review queue：

```text
vkpi_legacy_import_review_queue
```

每条 review item 必须保留：

```text
import_batch_id
pipeline
staging_table
staging_id
source_sheet
source_row
review_type
severity
status
payload_json
resolution_json
```

review_type 建议：

```text
missing_platform
missing_handle
contact_missing
duplicate_in_batch
matched_multiple_kols
unmatched_kol_review
pii_restricted
cost_currency_missing
date_parse_failed
not_importable_sheet
```

状态：

```text
open
resolved
ignored
blocked
committed
rolled_back
```

## 5. RBAC 权限设计

导入页面需要分级权限：

```text
vkpi:read
  可看 batch 摘要、非敏感字段、校验统计。

vkpi:write
  可上传 Excel，触发 parse 到 staging，可编辑非敏感映射修正。

vkpi:admin
  可查看未脱敏联系方式，可 approve commit，可 rollback batch。
```

联系方式字段：

```text
email
phone
address
contact_raw_json
```

默认：

```text
contact_visibility_level='restricted'
contains_pii=true
```

前端普通员工预览时默认显示：

```text
email: c***@domain.com
phone: +1 *** *** 9422
address: restricted
```

查看完整联系方式必须写入：

```text
vkpi_sensitive_access_logs
```

action_type：

```text
view_kol_contact
```

## 6. 前端导入预览 UI

建议页面：

```text
frontend/src/components/admin/vkpi/pages/LegacyImportPage.tsx
```

信息架构：

```text
1. Batch header
   文件名、sha256、大小、上传人、状态、总行数、可提交行数、review 数。

2. Pipeline tabs
   KOL 主档
   合作历史
   新品立项
   官方内容
   官方物料
   产品成本
   风险名单
   VOC / 舆情
   Review Queue
   Logs / Rollback

3. Row preview table
   source_sheet
   source_row
   normalized key
   validation status
   match result
   redacted contact fields
   raw row drawer

4. Review drawer
   问题类型、原始值、建议修正、处理动作。

5. Commit controls
   dry-run summary
   commit selected pipeline
   rollback batch
```

P2B 第一版只需要后端 staging + 预览接口；页面可以 P2B-UI 包再实现。

## 7. 后端 API 设计

建议 router：

```text
backend/app/api/routers/vkpi_legacy_import.py
```

Endpoints：

```text
POST /api/admin/vkpi/legacy-import/batches
GET  /api/admin/vkpi/legacy-import/batches
GET  /api/admin/vkpi/legacy-import/batches/{batch_id}
POST /api/admin/vkpi/legacy-import/batches/{batch_id}/parse
GET  /api/admin/vkpi/legacy-import/batches/{batch_id}/preview/{pipeline}
GET  /api/admin/vkpi/legacy-import/batches/{batch_id}/review
POST /api/admin/vkpi/legacy-import/review/{review_id}/resolve
POST /api/admin/vkpi/legacy-import/batches/{batch_id}/dry-run
POST /api/admin/vkpi/legacy-import/batches/{batch_id}/commit
POST /api/admin/vkpi/legacy-import/batches/{batch_id}/rollback
GET  /api/admin/vkpi/legacy-import/batches/{batch_id}/logs
```

P2B 当前包只建表，不实现这些 endpoint。

## 8. 导入日志与回滚

日志表：

```text
vkpi_legacy_import_logs
```

记录：

```text
batch_created
file_parsed
row_validated
review_created
dry_run
commit_started
commit_finished
rollback_started
rollback_finished
failed
```

回滚引用表：

```text
vkpi_legacy_import_committed_refs
```

每个正式写入动作必须记录：

```text
import_batch_id
pipeline
staging_table
staging_id
target_table
target_id
commit_action
previous_snapshot_json
new_snapshot_json
rollback_status
```

`rollback_status` 建议状态：

```text
not_rolled_back
rolled_back
rollback_failed
rollback_skipped
manual_required
```

Rollback 只能按 batch 或 pipeline 维度执行。回滚前必须先 dry-run，显示将删除/恢复哪些目标行。

Batch 级 rollback 策略字段由 `058a_vkpi_legacy_import_launch_plan.sql` 追加：

```text
rollback_until TIMESTAMPTZ
rollback_policy TEXT DEFAULT 'manual_30m'
auto_rollback_at TIMESTAMPTZ
```

`rollback_policy` 枚举：

```text
manual_30m   默认：30 分钟内允许触发 rollback。
manual_24h   长窗口：24 小时内允许触发 rollback，后续可限制为 lead/admin。
admin_only   只有 admin 可以 rollback，无固定时限。
no_rollback  标记不可逆导入，原则上 P2D 不应默认开放。
```

`auto_rollback_at` 只用于容灾：如果 commit worker 中途死亡，batch 长时间卡在 `committing`，P2D 可以按该字段触发自动回滚。第一版可以只写字段，不主动调度。

## 9. P2B 验收

本包验收：

```bash
rg "CREATE TABLE IF NOT EXISTS vkpi_legacy" migrations/058_vkpi_legacy_import.sql migrations/058a_vkpi_legacy_import_launch_plan.sql migrations/058d_vkpi_legacy_official_materials.sql
rg "source_sheet" migrations/058_vkpi_legacy_import.sql migrations/058a_vkpi_legacy_import_launch_plan.sql migrations/058d_vkpi_legacy_official_materials.sql
rg "source_row" migrations/058_vkpi_legacy_import.sql migrations/058a_vkpi_legacy_import_launch_plan.sql migrations/058d_vkpi_legacy_official_materials.sql
rg "import_batch_id" migrations/058_vkpi_legacy_import.sql migrations/058a_vkpi_legacy_import_launch_plan.sql migrations/058d_vkpi_legacy_official_materials.sql
rg "rollback_until|rollback_policy|auto_rollback_at" migrations/058a_vkpi_legacy_import_launch_plan.sql
ls migrations/058*_down.sql
rg "INSERT INTO|UPDATE .*vkpi_|DELETE FROM" migrations/058_vkpi_legacy_import.sql migrations/058a_vkpi_legacy_import_launch_plan.sql migrations/058d_vkpi_legacy_official_materials.sql
```

预期：

```text
只有 CREATE TABLE / CREATE INDEX / COMMENT，不出现正式表写入语句。
每个 staging 表都有 source_sheet 和 source_row。
每个 staging 表都通过 import_batch_id 关联到 vkpi_legacy_import_batches。
058 / 058a / 058d 都有 down migration。
launch_plan 不混入 cooperation / kol_project。
官方物料不再 skipped，进入 official_materials staging。
```
