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

## 7. P2C-2 Review Decisions

P2C-2 在 `vkpi_legacy_kol_entities` 上记录人工或批量 review 决策。决策只写字段，不移动 `vkpi_legacy_kol_entity_refs`：

```text
resolution_decision      merge_with / keep_separate / drop / escalate
merge_target_entity_id   merge_with 的目标 entity id
merge_target_uid         merge_with 的目标 entity_uid
decision_reason          drop 必填
decision_note            escalate 必填
decided_by               cli 或 bulk 来源
decided_at               决策时间
```

`merge_with` 只记录目标，不物理转移 refs。P2D dry-run/commit 时再折叠 merge 决策，这样 staging 层仍可回退、可审计。

## 8. Review CLI

列待处理项：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --list-pending-reviews vkpi_20260519033921_b36c6f28ec8d
```

查看单个 entity：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --show-entity legacy_kol_xxxxx
```

单条决策默认 dry-run，必须加 `--commit` 才落库：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --decide-resolution legacy_kol_xxxxx \
  --action keep_separate

python3 scripts/audit_vkpi_legacy_excel.py \
  --decide-resolution legacy_kol_xxxxx \
  --action merge_with \
  --target legacy_kol_yyyyy \
  --commit
```

批量决策同样默认 dry-run：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --bulk-decide vkpi_20260519033921_b36c6f28ec8d \
  --weak-label profile_only_review \
  --action keep_separate
```

查看进度：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --review-progress vkpi_20260519033921_b36c6f28ec8d
```

## 9. blocked_risk 通道

默认 `--list-pending-reviews` 不包含 `blocked_risk`。必须显式查看：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --list-pending-reviews vkpi_20260519033921_b36c6f28ec8d \
  --weak-label blocked_risk
```

`blocked_risk` 只能 `drop` 或 `escalate`，不能 `keep_separate`，也不能 `merge_with`。这是 P2D 前的硬规则，避免高风险 KOL 被普通 review 流程误放行。

## 10. 当前 Review 决策结果

对 batch `vkpi_20260519033921_b36c6f28ec8d` 已执行：

```text
profile_only_review       keep_separate   294
profile_missing_review    escalate         36
risk_review               escalate          7
blocked_risk              NULL              6
ready                     NULL            675
```

验收状态：

```text
Pending (excluding blocked_risk): 0
Blocked pending: 6
```

说明：

```text
profile_only_review 作为独立 KOL 保留，P2D 可进入候选池。
profile_missing_review 缺主档，先 escalate，P2D 需要单独标记。
risk_review 无业务逐条判断时先 escalate，保留风险确认入口。
blocked_risk 6 条暂不决策，后续只能 admin drop/escalate。
```
