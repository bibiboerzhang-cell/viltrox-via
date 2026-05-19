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

## 7. P3 下一步

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

## 8. P3 后续

P3-3 可以继续做：

```text
1. Memory feedback 后台列表和处理状态
2. Product memory 归一化，减少 FE/E/Z 这类卡口名噪声
3. Market memory v0，从 launch_plan / VOC / official content 读市场信号
```

P4 推荐前必须坚持：

```text
推荐读取 Memory，但不在 P4 内部临时造 memory。
P5 Budget Guard 已前置，推荐任务接入 provider 前要走预算守门。
```
