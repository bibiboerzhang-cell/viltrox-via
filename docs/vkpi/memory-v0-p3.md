# V-KPI P3 Memory v0

整理日期：2026-05-19
输入 batch：`vkpi_20260519033921_b36c6f28ec8d`

## 1. 范围

P3 Memory v0 只做可解释业务记忆，不做向量库，不做训练，不做推荐排序。

已新增：

```text
migrations/059_vkpi_memory_tables.sql
backend/app/services/vkpi/memory.py
backend/app/api/routers/vkpi_memory.py
scripts/build_vkpi_memory.py
```

表：

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

没有使用 `JSONB` / `TEXT[]` / `BIGINT[]`。

## 2. 输入

Memory v0 当前从 P2D active committed refs 构建：

```text
vkpi_legacy_import_committed_refs.rollback_status='not_rolled_back'
target_table='vkpi_kol_pool'
```

并读取：

```text
vkpi_kol_pool
vkpi_legacy_kol_entities
vkpi_legacy_kol_entity_refs
vkpi_legacy_cooperations_staging
vkpi_legacy_risk_watchlist_staging
vkpi_legacy_launch_plans_staging
vkpi_legacy_product_costs_staging
```

`blocked_risk` 没进 P2D 主池，因此 P3 Memory v0 不会把 6 条 blocked KOL 作为普通 KOL memory 写入。

## 3. Memory 事实口径

KOL entity：

```text
entity_type=kol
identity_key=platform:handle
source_table=vkpi_kol_pool
```

Product entity：

```text
entity_type=product
identity_key=lower(product_name)
source_table=legacy staging 表
```

事实类型：

```text
sync_status
weak_label
review_state
contact_status
country
evidence_count
cooperation
risk_flag
launch_plan
product_cost
```

KOL 到产品的历史合作通过 link 表保存：

```text
link_type=worked_on_product
```

风险不使用数组字段。风险作为：

```text
vkpi_memory_facts.fact_type='risk_flag'
```

## 4. CLI

构建 legacy batch memory：

```bash
python3 scripts/build_vkpi_memory.py \
  --build-legacy \
  --batch-uid vkpi_20260519033921_b36c6f28ec8d
```

查看 summary：

```bash
python3 scripts/build_vkpi_memory.py \
  --summary \
  --source-ref legacy_batch:vkpi_20260519033921_b36c6f28ec8d
```

构建是 upsert/idempotent；重复跑不会重复生成同一 entity/fact/link，但会新增一条 snapshot 记录用于审计。

## 5. API

已注册 admin router：

```text
GET  /api/admin/vkpi/memory/summary
GET  /api/admin/vkpi/memory/entities
GET  /api/admin/vkpi/memory/entities/{entity_uid}/facts
POST /api/admin/vkpi/memory/build-from-legacy/{batch_uid}
POST /api/admin/vkpi/memory/feedback
```

权限：

```text
read endpoints  -> require_tab("vkpi", "read")
write endpoints -> require_tab("vkpi", "write")
```

## 6. 当前构建结果

```text
source_ref=legacy_batch:vkpi_20260519033921_b36c6f28ec8d
entities.kol=1012
entities.product=885
facts.contact_status=1012
facts.cooperation=2358
facts.country=953
facts.evidence_count=4048
facts.launch_plan=52
facts.product_cost=823
facts.review_state=1012
facts.risk_flag=7
facts.sync_status=1012
facts.weak_label=1012
links=2358
snapshots=1
```

总表状态：

```text
memory_entities_total=1897
memory_facts_total=12289
memory_links_total=2358
memory_snapshots_total=1
```

解释：

```text
1012 个 KOL 来自 P2D 主池 active refs。
885 个 product entity 来自合作、上市计划和成本表的产品名归并。
2358 条 worked_on_product link 来自非 blocked KOL 的历史合作记录。
7 条 risk_flag 来自已进入主池但仍需人工确认的 risk_review。
6 条 blocked_risk 未进入主池，因此不进入普通 memory。
```

## 7. P3-2 查询与特征层

P3-2 已补 Memory 查询/特征层，给 P4 推荐提供可解释输入，但本身不做推荐、不写推荐结果。

新增只读查询：

```text
product_kol_candidates(product_query, limit)
kol_product_memory(entity_uid, limit)
fit_features(entity_uid, product_query)
```

新增 API：

```text
GET /api/admin/vkpi/memory/product-kol-candidates
GET /api/admin/vkpi/memory/entities/{entity_uid}/product-memory
GET /api/admin/vkpi/memory/entities/{entity_uid}/fit-features
```

新增 CLI：

```bash
python3 scripts/build_vkpi_memory.py \
  --product-kol-candidates "AF 35mm" \
  --limit 5

python3 scripts/build_vkpi_memory.py \
  --kol-product-memory mem_kol_ef5c281120406d1bf9ee \
  --limit 5

python3 scripts/build_vkpi_memory.py \
  --fit-features mem_kol_ef5c281120406d1bf9ee \
  --product-query "AF 35mm"
```

当前真实数据验证：

```text
product_query=AF 35mm
matched_products=10
total_candidates=152
top_candidate=mem_kol_ef5c281120406d1bf9ee
top_score=79
top_matched_cooperations=4
```

风险样本验证：

```text
entity_uid=mem_kol_4b1b26211f1103264289
sync_status=needs_human_review
weak_label=risk_review
risk_flag_count=1
memory_score=0
warnings=needs_human_review,risk_flags=1
```

P3-2 输出字段：

```text
memory_score
score_breakdown
reasons
warnings
features.platform
features.handle
features.country
features.sync_status
features.weak_label
features.review_state
features.contact_status
features.cooperation_count
features.product_count
features.matched_product_count
features.matched_product_cooperation_count
features.risk_flag_count
features.evidence_count
```

`memory_score` 只是历史证据强弱分，不是推荐分。P4 推荐可以读取它，但仍需要单独的推荐策略、预算守门和结果审计。

## 8. P3-3 产品归一化

P3-3 已补 product family 归一化，解决历史 Excel 里同一产品多种写法的问题。

新增 Memory entity：

```text
entity_type=product_family
```

新增 Memory link：

```text
link_type=normalized_to_product_family
source_entity=raw product
target_entity=product_family
```

新增 fact：

```text
fact_type=product_normalization
```

新增 API：

```text
GET  /api/admin/vkpi/memory/product-families
POST /api/admin/vkpi/memory/build-product-families
```

新增 CLI：

```bash
python3 scripts/build_vkpi_memory.py --build-product-families
python3 scripts/build_vkpi_memory.py --product-families "AF 35mm" --limit 3
```

构建结果：

```text
total_families=597
build.normalized_products=782
build.skipped_ambiguous_mount_only=5
build.skipped_empty=1
build.skipped_unclassified=97
facts.product_normalization=885
links.normalized_to_product_family=782
```

典型归一化结果：

```text
AF 35mm F1.7 Air
  members=5
  cooperations=289
  - AF 35mm/1.7 Air XF/E/Z
  - AF 35mm F1.7 Air E+XF+Z
  - AF 35mm/1.7 Air E

AF 50mm F2
  members=7
  cooperations=88
  - AF 50/F2.0 FE
  - AF 50/F2.0 Z
  - AF 50/2 FE
```

`FE` / `E` / `Z` / `X` 这类纯卡口名不强行归一到产品，标记为：

```text
ambiguous_mount_only
```

P3-3 后，产品候选查询会同时读取 raw product 和 product_family：

```text
product_query=AF 35mm
matched_products=30
matched_families=8
total_candidates=201
top_score=93
```

这一步仍然不是推荐。它只是把同一产品的历史合作证据合并成更稳定的 Memory 输入。

## 9. P3-4 Market Memory v0

P3-4 已把 legacy staging 里的市场信号写入 Memory，不写主业务表，不生成推荐结果。

输入：

```text
vkpi_legacy_launch_plans_staging
vkpi_legacy_official_content_staging
vkpi_legacy_official_materials_staging
vkpi_legacy_voc_alerts_staging
```

新增 Memory entity：

```text
entity_type=market_topic
entity_type=official_account
```

新增 fact：

```text
fact_type=market_signal
signal_type=launch_plan
signal_type=official_content
signal_type=official_material
signal_type=voc_alert
```

新增 link：

```text
link_type=official_account_published_product
source_entity=official_account
target_entity=product_family
```

落点规则：

```text
能归一到 product_family 的信号挂 product_family。
不能归一的活动 / VOC / 模糊产品信号挂 market_topic。
官媒账号单独成为 official_account，并通过 link 回连 product_family。
review_status != ready 的行不写入 Market Memory。
```

新增 API：

```text
GET  /api/admin/vkpi/memory/market-signals
POST /api/admin/vkpi/memory/build-market-memory/{batch_uid}
```

新增 CLI：

```bash
python3 scripts/build_vkpi_memory.py \
  --build-market-memory \
  --batch-uid vkpi_20260519033921_b36c6f28ec8d

python3 scripts/build_vkpi_memory.py \
  --market-signals "AF 35mm" \
  --limit 8

python3 scripts/build_vkpi_memory.py \
  --market-signals "产品相关" \
  --signal-type voc_alert \
  --limit 5
```

构建结果：

```text
total_signals=2486
signals.launch_plan=52
signals.official_content=2168
signals.official_material=229
signals.voc_alert=37
targets.product_family=1840
targets.market_topic=646
links.official_account_published_product=1557
entities.official_account=63
```

解释：

```text
official_content staging 总行数 2202，其中 2168 行 ready 写入 Market Memory。
official_materials staging 总行数 241，其中 229 行 ready 写入 Market Memory。
剩余 review / validation_error 行继续留在 P2B review queue，不强行进入 Memory。
```

查询验证：

```text
query=AF 35mm
total_returned=8
target=product_family:AF 35mm F1.2 LAB
target=product_family:AF 35mm F1.8 EVO

query=产品相关
signal_type=voc_alert
target=market_topic:voc_alert: 产品相关
target=product_family:AF 85mm F1.8
target=product_family:AF 135mm F1.8
```

P3-4 后，P4 可以同时读取：

```text
KOL 历史合作 Memory
Product Family Memory
Launch / Official Content / Official Material / VOC Market Signal
Risk / review 降权信号
```

这一步仍然不是推荐，只是把市场环境和产品动作纳入 Memory。

## 10. P3.5 Readiness / Feedback Queue

P3.5 已补 P4 dry-run 前的 Memory 可用性检查和反馈处理入口。

新增只读 readiness：

```text
readiness()
```

新增 feedback helpers：

```text
list_feedback(status, entity_uid, feedback_type, limit)
update_feedback(feedback_uid, status, resolution_action, resolution_note)
```

新增 API：

```text
GET   /api/admin/vkpi/memory/readiness
GET   /api/admin/vkpi/memory/feedback
PATCH /api/admin/vkpi/memory/feedback/{feedback_uid}
```

新增 CLI：

```bash
python3 scripts/build_vkpi_memory.py --readiness
python3 scripts/build_vkpi_memory.py --feedback-list --limit 20

# 默认 dry-run，不会改库
python3 scripts/build_vkpi_memory.py \
  --resolve-feedback mem_feedback_xxx \
  --feedback-status resolved \
  --resolution-note "checked"

# 真正落库必须显式加 --commit
python3 scripts/build_vkpi_memory.py \
  --resolve-feedback mem_feedback_xxx \
  --feedback-status resolved \
  --resolution-note "checked" \
  --commit
```

Readiness 验收：

```text
status=ready_for_p4_dry_run
provider_calls_allowed=false
gate.kol_memory=pass actual=1012 expected_min=1000
gate.product_family_memory=pass actual=659 expected_min=1
gate.historical_product_links=pass actual=2358 expected_min=1
gate.market_signals=pass actual=2486 expected_min=1
gate.launch_signals=pass actual=52 expected_min=1
gate.official_content_signals=pass actual=2168 expected_min=1
gate.voc_signals=pass actual=37 expected_min=1
gate.budget_guard_tables=pass actual=1 expected_min=1
```

当前 feedback queue：

```text
returned=0
```

P3.5 的边界：

```text
readiness 只证明 P4 dry-run 可以读 Memory。
provider_calls_allowed=false 表示仍不允许 P4 直接调用 LLM provider。
feedback update 是人工处理流，不会自动改 Memory facts。
```

## 11. P3 后续

P3 后续可选项：

```text
1. Product family 人工 override 表或配置，处理 97 条 unclassified
2. P4 推荐 v0 dry-run，只读 Memory + Budget Guard，不直接跑 provider
```

P4 推荐前必须坚持：

```text
推荐读取 Memory，但不在 P4 内部临时造 memory。
P5 Budget Guard 已前置，推荐任务接入 provider 前要走预算守门。
```
