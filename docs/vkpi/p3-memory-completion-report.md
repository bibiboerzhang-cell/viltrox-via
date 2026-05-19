# V-KPI P3 Memory 完成报告

生成日期：2026-05-19  
输入 batch：`vkpi_20260519033921_b36c6f28ec8d`  
当前阶段：P3 Memory 主体完成，可进入 P4 dry-run 设计

## 1. 总结

P3 已把 P2D 导入的历史 Excel 数据转成可解释 Memory 层。

P3 不做向量库、不训练模型、不生成推荐结果。它的作用是把历史 KOL、产品、合作、风险、官媒内容、官方物料、上市计划和 VOC 统一沉淀成后续 P4 推荐可读取的业务记忆。

当前 Memory 已具备四层：

```text
KOL Memory              1012 个历史 KOL 主体
Product Memory           885 个 raw product entity
Product Family Memory    659 个 product_family entity
Market Memory           2486 条 market_signal
```

关键结果：

```text
KOL entities                         1012
raw product entities                  885
product_family entities               659
market_topic entities                 347
official_account entities              63
worked_on_product links              2358
normalized_to_product_family links     782
official_account_published_product    1557
market_signal facts                  2486
```

## 2. P3 提交记录

```text
8dd78a7 feat(vkpi): add memory v0 tables
f3c147d feat(vkpi): build memory v0 from legacy KOL data
cef38a5 docs(vkpi): document memory v0 build
5c0ba13 feat(vkpi): add memory query helpers for P4 inputs
b99f459 docs(vkpi): document P3 memory query helpers
9739488 feat(vkpi): normalize product memory into families
2df69e7 docs(vkpi): document P3 product family normalization
d3c0b09 feat(vkpi): build market memory signals from legacy staging
036dc89 docs(vkpi): document P3 market memory signals
```

## 3. P3-1 Memory v0

新增 migration：

```text
migrations/059_vkpi_memory_tables.sql
migrations/059_vkpi_memory_tables_down.sql
```

新增表：

```text
vkpi_memory_entities
vkpi_memory_facts
vkpi_memory_links
vkpi_memory_feedback
vkpi_memory_snapshots
```

字段策略：

```text
identity_json TEXT
fact_json TEXT
source_json TEXT
metadata_json TEXT
```

未使用：

```text
JSONB
TEXT[]
BIGINT[]
```

构建入口：

```bash
python3 scripts/build_vkpi_memory.py \
  --build-legacy \
  --batch-uid vkpi_20260519033921_b36c6f28ec8d
```

构建结果：

```text
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

说明：

```text
1012 个 KOL 来自 P2D active committed refs。
6 条 blocked_risk 未进入 vkpi_kol_pool，因此不进入普通 KOL Memory。
43 条 needs_human_review 已进入 Memory，但保留 sync/review 降权信号。
```

## 4. P3-2 查询与特征层

P3-2 补了只读查询和特征提取，让 P4 可以读取 Memory，但不在 P3 内做推荐。

新增 service helper：

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
  --fit-features mem_kol_ef5c281120406d1bf9ee \
  --product-query "AF 35mm"
```

验证样本：

```text
product_query=AF 35mm
matched_products=10
total_candidates=152
top_candidate=mem_kol_ef5c281120406d1bf9ee
top_score=79
top_matched_cooperations=4
```

风险降权样本：

```text
entity_uid=mem_kol_4b1b26211f1103264289
sync_status=needs_human_review
weak_label=risk_review
risk_flag_count=1
memory_score=0
warnings=needs_human_review,risk_flags=1
```

`memory_score` 只是历史证据强弱分，不是最终推荐分。

## 5. P3-3 Product Family 归一化

P3-3 解决历史 Excel 里同一产品多种写法的问题。

新增 entity：

```text
entity_type=product_family
```

新增 link：

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

典型归一结果：

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

归一化后的 P4 输入改善：

```text
P3-2 raw query:
  product_query=AF 35mm
  total_candidates=152

P3-3 family query:
  product_query=AF 35mm
  matched_products=30
  matched_families=8
  total_candidates=201
  top_score=93
```

纯卡口名如 `FE` / `E` / `Z` / `X` 没有被强行归一到产品，标记为 `ambiguous_mount_only`。

## 6. P3-4 Market Memory v0

P3-4 把 legacy staging 的市场信号写入 Memory。

输入：

```text
vkpi_legacy_launch_plans_staging
vkpi_legacy_official_content_staging
vkpi_legacy_official_materials_staging
vkpi_legacy_voc_alerts_staging
```

新增 entity：

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
facts.market_signal=2486
signals.launch_plan=52
signals.official_content=2168
signals.official_material=229
signals.voc_alert=37
targets.product_family=1840
targets.market_topic=646
official_account entities=63
official_account_published_product links=1557
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
total_returned=5
target=product_family:AF 35mm F1.2 LAB
target=product_family:AF 35mm F1.8 EVO

query=产品相关
signal_type=voc_alert
target=market_topic:voc_alert: 产品相关
target=product_family:AF 85mm F1.8
target=product_family:AF 135mm F1.8
```

## 7. API 总表

```text
GET  /api/admin/vkpi/memory/summary
GET  /api/admin/vkpi/memory/entities
GET  /api/admin/vkpi/memory/entities/{entity_uid}/facts
GET  /api/admin/vkpi/memory/product-kol-candidates
GET  /api/admin/vkpi/memory/product-families
GET  /api/admin/vkpi/memory/market-signals
GET  /api/admin/vkpi/memory/entities/{entity_uid}/product-memory
GET  /api/admin/vkpi/memory/entities/{entity_uid}/fit-features
POST /api/admin/vkpi/memory/build-from-legacy/{batch_uid}
POST /api/admin/vkpi/memory/build-product-families
POST /api/admin/vkpi/memory/build-market-memory/{batch_uid}
POST /api/admin/vkpi/memory/feedback
```

权限：

```text
read endpoints  -> require_tab("vkpi", "read")
write endpoints -> require_tab("vkpi", "write")
```

## 8. 验收命令

```bash
python3 -m py_compile \
  backend/app/services/vkpi/memory.py \
  backend/app/api/routers/vkpi_memory.py \
  scripts/build_vkpi_memory.py

python3 scripts/build_vkpi_memory.py \
  --summary \
  --source-ref legacy_batch:vkpi_20260519033921_b36c6f28ec8d

python3 scripts/build_vkpi_memory.py \
  --summary \
  --source-ref memory_product_family:v0

python3 scripts/build_vkpi_memory.py \
  --summary \
  --source-ref market_memory:v0:batch:vkpi_20260519033921_b36c6f28ec8d

python3 scripts/build_vkpi_memory.py \
  --product-kol-candidates "AF 35mm" \
  --limit 3

python3 scripts/build_vkpi_memory.py \
  --market-signals "AF 35mm" \
  --limit 3

git diff --check
```

最近一次验收结果：

```text
py_compile: pass
git diff --check: pass
product-kol-candidates AF 35mm: total_candidates=201
market-signals AF 35mm: returned product_family signals
market-signals 产品相关/voc_alert: returned market_topic + product_family VOC signals
```

## 9. 当前限制

P3 仍有几个明确限制，不能当成 P4 已完成：

```text
1. memory_score 不是推荐分，只是历史证据强弱分。
2. product_family 有 97 条 unclassified，后续需要人工 override 或规则补丁。
3. Market Memory 只写 ready 行，review queue 里的 validation_error 未进入 Memory。
4. official_content / official_material 只形成历史信号，还没有效果回流。
5. Memory feedback 表已建，但后台处理流还没做。
6. P3 没有接 LLM provider，也没有新增成本消耗。
```

## 10. 进入 P4 的建议门槛

P4 推荐 v0 可以开始，但建议只做 dry-run：

```text
只读 Memory。
先不调用 LLM provider。
先不写正式推荐结果。
先输出 explainable recommendation preview。
必须接 Budget Guard 才允许后续 provider 调用。
```

P4 dry-run 推荐输入应包括：

```text
KOL historical cooperation memory
Product family memory
Market signal memory
Risk / review downgrade signals
Contact availability signals
```

建议 P4 第一版场景：

```text
新品上市匹配:
  launch_plan + product_family + historical KOL cooperation + market_signal

历史同品类复用:
  product_family + worked_on_product links + cooperation_count

风险过滤:
  risk_flag + needs_human_review + contact_missing
```

## 11. 结论

P3 主体目标已经完成：

```text
P2D 的历史 KOL 主表数据已转成 Memory。
产品历史写法已收敛到 product_family。
官媒、物料、上市计划、VOC 已转成 market_signal。
P4 推荐已有可解释、可查询、可审计的输入层。
```

下一步不是继续堆 Memory 表，而是进入 P4 dry-run，把 Memory 读出来形成可解释推荐预览，并保持 Budget Guard 前置。
