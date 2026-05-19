# V-KPI P2D Legacy KOL Commit

整理日期：2026-05-19
输入 batch：`vkpi_20260519033921_b36c6f28ec8d`

## 1. 范围

P2D 第一阶段只做 KOL entity 到 `vkpi_kol_pool` 的写入预演。

当前已实现：

```text
P2D-1 dry-run planner
P2D-2 actual commit + rollback refs
```

当前未实现：

```text
P2D-3 rollback execution verification package
```

P2D-1 不写：

```text
vkpi_kol_pool
vkpi_legacy_import_committed_refs
kols
vkpi_projects
vkpi_cost_ledger
```

P2D-2 只写：

```text
vkpi_kol_pool
vkpi_legacy_import_committed_refs
vkpi_legacy_import_batches.status / committed_rows / committed_at
vkpi_legacy_import_logs
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

真实 commit 必须显式加 `--commit`：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --commit-kol-pool-batch vkpi_20260519033921_b36c6f28ec8d \
  --commit
```

不加 `--commit` 时只输出同 dry-run 计划，并提示 `Add --commit to apply P2D commit.`。

回滚预览：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --rollback-kol-pool-commit vkpi_20260519033921_b36c6f28ec8d
```

真实回滚也必须显式加 `--commit`：

```bash
python3 scripts/audit_vkpi_legacy_excel.py \
  --rollback-kol-pool-commit vkpi_20260519033921_b36c6f28ec8d \
  --commit
```

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

实际 commit 已补齐：

```text
1. 单事务写 vkpi_kol_pool + vkpi_legacy_import_committed_refs
2. INSERT/UPDATE 都记录 previous_snapshot_json / new_snapshot_json
3. batch 状态 staged -> committing -> committed
4. P2D rollback 按 committed_refs 反向恢复
5. blocked_risk 默认禁止 commit，只有 admin override 后才能处理
6. commit 后再次 dry-run 显示 committed_refs_count > 0
7. 重复 commit 被拒绝
```

## 8. 当前 Commit 结果

对 batch `vkpi_20260519033921_b36c6f28ec8d` 已执行真实 commit：

```text
mode=commit
entity_count=1018
planned_writes=1012
insert_count=933
update_count=79
skip_count=6
committed_refs_count=1012
```

batch 状态：

```text
batch_status=committed
committed_rows=1012
rolled_back_rows=0
```

committed refs：

```text
refs.insert.not_rolled_back=933
refs.update.not_rolled_back=79
```

主池状态：

```text
vkpi_kol_pool source_type='legacy_excel_p2d'=1012
sync_status='needs_human_review'=43
sync_status='imported'=969
```

重复提交保护：

```text
ERROR: batch already has committed refs; rollback before committing again
```

回滚预览：

```text
rollback_refs_count=1012
insert_refs=933
update_refs=79
```

## 9. P2D-3 Rollback Drill

P2D-3 已完成真实 rollback -> recommit 演练。演练入口：

```bash
python3 scripts/verify_vkpi_p2d_rollback_drill.py
```

默认只检查当前状态和 rollback preview。真实执行必须显式：

```bash
python3 scripts/verify_vkpi_p2d_rollback_drill.py \
  --execute \
  --commit \
  --force-rollback
```

### 9.1 Window / Force

当前 batch 首次 commit 的 `rollback_until` 已经过期。普通 rollback 被拒绝：

```text
ERROR: rollback not allowed: rollback_window_expired
```

使用 `--force-rollback` 完成应急回滚演练：

```text
mode=rollback
rollback_refs_count=1012
insert_refs=933
update_refs=79
rollback_allowed=true
rollback_forced=true
rolled_back_refs=1012
```

P2D 时间戳已修正为 UTC offset 写入，避免 TIMESTAMPTZ 被 Postgres 按本地时区解释：

```text
rollback_until=2026-05-19T04:53:19Z
rollback_window_reason=ok
```

### 9.2 Rollback 验收

rollback 后状态：

```text
after_rollback.batch_status=rolled_back
after_rollback.pool_total=90
after_rollback.pool_legacy_source=0
after_rollback.refs.attempt_2.insert.rolled_back=933
after_rollback.refs.attempt_2.update.rolled_back=79
after_rollback.update_restore_checked=79
after_rollback.update_restore_mismatches=0
```

解释：

```text
933 个 insert refs 对应的 vkpi_kol_pool 行已删除。
79 个 update refs 已按 previous_snapshot_json 还原。
source_type='legacy_excel_p2d' 回到 0。
```

### 9.3 Recommit 验收

rollback 后重新 commit 成功：

```text
recommit.committed_refs_count=1012
recommit.committed_refs_total=2024
commit_attempt=2
```

最终主表状态：

```text
final.batch_status=committed
final.pool_total=1023
final.pool_legacy_source=1012
final.pool_imported=969
final.pool_needs_human_review=43
final.refs.attempt_1.insert.rolled_back=933
final.refs.attempt_1.update.rolled_back=79
final.refs.attempt_2.insert.not_rolled_back=933
final.refs.attempt_2.update.not_rolled_back=79
```

`committed_refs_total=2024` 是预期结果：

```text
第一轮 insert refs 回滚后保留 933 条 rolled_back 审计记录。
第一轮 update refs 回滚后保留 79 条 rolled_back 审计记录。
第二轮 recommit 生成 1012 条 attempt=2 active refs。
当前有效 refs 仍然是 1012。
```

### 9.4 最终状态

P2D 最终应保持：

```text
vkpi_kol_pool count=1023
source_type='legacy_excel_p2d'=1012
batch.status=committed
active committed_refs=1012
rollback preview 可用
```
