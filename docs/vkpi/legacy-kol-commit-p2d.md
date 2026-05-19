# V-KPI P2D Legacy KOL Commit

整理日期：2026-05-19
输入 batch：`vkpi_20260519033921_b36c6f28ec8d`

## 1. 范围

P2D 第一阶段只做 KOL entity 到 `vkpi_kol_pool` 的写入预演。

当前已实现：

```text
P2D-1 dry-run planner
```

当前未实现：

```text
P2D-2 actual commit
P2D-3 main-table rollback
```

P2D-1 不写：

```text
vkpi_kol_pool
vkpi_legacy_import_committed_refs
kols
vkpi_projects
vkpi_cost_ledger
```

## 2. 输入

读取 P2C 结果：

```text
vkpi_legacy_kol_entities
vkpi_legacy_kol_entity_refs
```

依赖 P2C-2 review decision：

```text
ready                     自动可入池
profile_only_review       keep_separate 后可入池
profile_missing_review    escalate 后可入池，但标 needs_human_review
risk_review               escalate 后可入池，但标 needs_human_review
blocked_risk              默认跳过
drop                      跳过
merge_with                source 跳过，P2D-2 按 target 折叠
```

## 3. 目标表

P2D-1 只生成 `vkpi_kol_pool` 计划：

```text
target_table=vkpi_kol_pool
plan_action=insert/update/skip
```

匹配规则：

```text
lower(platform) + lower(handle)
```

如果目标池里已有同平台同 handle，计划为 `update`；否则计划为 `insert`。

## 4. 联系方式处理

P2B staging 中的联系方式仍保留在 staging/review 层。P2D-1 计划不会把 restricted 联系方式默认为普通可见。

规则：

```text
contact_visibility_level=public      可计划写 email
contact_visibility_level!=public     email 不写入 vkpi_kol_pool
phone                                不写入 vkpi_kol_pool
```

dry-run 的 `raw_platform_data` 只保留布尔提示：

```text
contact_has_email
contact_has_phone
contact_visibility_level
contact_status
```

不在主池 payload 中复制 restricted email/phone 原文。

## 5. CLI

运行 dry-run：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --dry-run-kol-pool-commit vkpi_20260519033921_b36c6f28ec8d \
  --limit 12
```

默认跳过 `blocked_risk`。如果后续需要演练 blocked 通道，必须显式加：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --dry-run-kol-pool-commit vkpi_20260519033921_b36c6f28ec8d \
  --include-blocked
```

当前 `blocked_risk` 未做 admin 决策，即使 include 也不会直接进入普通写入流。

## 6. 当前 Dry-run 结果

```text
entity_count=1018
planned_writes=1012
insert_count=933
update_count=79
skip_count=6
committed_refs_count=0
```

弱标签分布：

```text
blocked_risk=6
profile_missing_review=36
profile_only_review=294
ready=675
risk_review=7
```

review state：

```text
blocked=6
needs_human_review=43
ready_auto=675
reviewed_keep_separate=294
```

skip 原因：

```text
blocked_risk=6
```

联系方式保护：

```text
email_restricted_omitted=542
phone_restricted_omitted=395
```

主表写入验证：

```text
vkpi_legacy_import_committed_refs=0
vkpi_kol_pool source_type='legacy_excel_p2d'=0
```

## 7. P2D-2 要求

实际 commit 前必须补齐：

```text
1. 单事务写 vkpi_kol_pool + vkpi_legacy_import_committed_refs
2. INSERT/UPDATE 都记录 previous_snapshot_json / new_snapshot_json
3. batch 状态 staged -> committing -> committed
4. P2D rollback 按 committed_refs 反向恢复
5. blocked_risk 默认禁止 commit，只有 admin override 后才能处理
6. commit 后再次 dry-run 应显示 committed_refs_count > 0 并拒绝重复提交
```
