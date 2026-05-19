# V-KPI P2C Legacy Entity Resolution

整理日期：2026-05-19
输入 batch：`vkpi_20260519033921_b36c6f28ec8d`

## 1. 目标

P2C 只做实体归并和弱标签，不写正式业务表。

输入来自 P2B staging：

```text
vkpi_legacy_kol_profiles_staging
vkpi_legacy_cooperations_staging
vkpi_legacy_risk_watchlist_staging
```

输出写入 P2C resolution layer：

```text
vkpi_legacy_resolution_runs
vkpi_legacy_kol_entities
vkpi_legacy_kol_entity_refs
```

明确禁止：

```text
不写 kols
不写 vkpi_kol_pool
不写 vkpi_projects
不写 vkpi_cost_ledger
```

## 2. 归并规则

Canonical key：

```text
canonical_key = normalized_platform || ':' || lower(normalized_handle)
```

优先来源：

```text
kol_profiles      提供主档字段、联系方式、国家、类目。
cooperations      提供合作历史证据。
risk_watchlist    提供风险提示证据。
```

无 `canonical_key` 的 staging row 不创建 entity，保留在 review 层。

## 3. 弱标签

```text
ready
  有 KOL 主档 + 有合作历史 + 无风险名单。

profile_only_review
  有 KOL 主档，但暂未匹配到合作历史。

profile_missing_review
  有合作历史，但缺 KOL 主档。

risk_review
  有 KOL 主档，且命中风险名单。

blocked_risk
  命中 high severity 风险。
```

P2C 的 `weak_label` 只是 commit 前的机器建议，不等于最终导入决策。

## 4. CLI

生成/重算 resolution：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --resolve-batch vkpi_20260519033921_b36c6f28ec8d
```

查看结果：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --inspect-resolution vkpi_20260519033921_b36c6f28ec8d
```

`--resolve-batch` 会重算当前 batch 的 P2C resolution tables；它不会删除 staging，也不会触碰正式主表。

## 5. 当前结果

```text
entity_count=1018
ready_count=675
review_count=337
blocked_count=6
no_identifier_rows=73
```

弱标签分布：

```text
blocked_risk=6
profile_missing_review=36
profile_only_review=294
ready=675
risk_review=7
```

staging refs：

```text
cooperations=2364
kol_profiles=1025
risk_watchlist=13
```

解释：

```text
P2B staging 里的 KOL 相关输入行数为 3475。
其中 73 行没有可用 platform + handle，不参与 canonical entity 生成。
剩余 3402 行通过 entity refs 关联到 1018 个 canonical KOL candidates。
```

## 6. P2D 输入

P2D 应从 `vkpi_legacy_kol_entities` 读取候选实体，按 `weak_label` 和 review 结果决定是否写入正式表。

建议：

```text
ready                  可进入 dry-run commit。
profile_only_review    先给人工确认是否创建候选池记录。
profile_missing_review 先补主档或人工确认。
risk_review            只能带风险提示进入候选，不自动合作。
blocked_risk           默认不 commit，需要 admin override。
```
