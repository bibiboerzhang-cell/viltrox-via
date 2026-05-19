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

### 2.3 官方内容 Staging

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

### 2.4 产品成本 Staging

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

### 2.5 风险名单 Staging

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

### 2.6 舆情 / VOC Staging

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

## 3. Review Queue 设计

统一 review queue：

```text
vkpi_legacy_import_review_queue
```

每条 review item 必须保留：

```text
batch_id
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

## 4. RBAC 权限设计

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

## 5. 前端导入预览 UI

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
   官方内容
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

## 6. 后端 API 设计

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

## 7. 导入日志与回滚

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
batch_id
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

Rollback 只能按 batch 或 pipeline 维度执行。回滚前必须先 dry-run，显示将删除/恢复哪些目标行。

## 8. P2B 验收

本包验收：

```bash
rg "CREATE TABLE IF NOT EXISTS vkpi_legacy" migrations/058_vkpi_legacy_import.sql
rg "source_sheet" migrations/058_vkpi_legacy_import.sql
rg "source_row" migrations/058_vkpi_legacy_import.sql
rg "INSERT INTO|UPDATE .*vkpi_|DELETE FROM" migrations/058_vkpi_legacy_import.sql
```

预期：

```text
只有 CREATE TABLE / CREATE INDEX / COMMENT，不出现正式表写入语句。
每个 staging 表都有 source_sheet 和 source_row。
```
