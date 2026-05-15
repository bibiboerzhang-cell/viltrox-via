# P4.1F Smoke Script Batch Verification

时间: 2026-05-15 08:36 Asia/Shanghai  
工作目录: `/Users/bibiboer/Documents/V-KPI——marketing`  
分支: `codex/vkpi-cleanup-d7`

## 1. 本轮目标

P4.1F 只核对 smoke 脚本批次,不做功能开发。

目标:

- 确认 `scripts/run_smoke.sh` 不再双跑 smoke。
- 确认新增 / 修改的 P4 动态 QA smoke 能编译。
- 批量运行当前 P4 smoke,验证真实后端路径仍通过。
- 检查动态 smoke 是否留下 marker-scoped 测试数据。
- 如发现旧测试残留,只按 `p4-step22-*` / `p4-step23-*` 测试前缀定向清理。

## 2. 备份

本轮开工前备份:

`/Users/bibiboer/Documents/V-KPI-backups/before-p4-1f-smoke-scripts-20260515-081117.tar.gz`

## 3. 覆盖范围

### 修改脚本

- `scripts/run_smoke.sh`
- `scripts/smoke_vkpi_p3_11c_daily_top100_ui_contract.py`
- `scripts/smoke_vkpi_p3_13c_post_detail_contract.py`
- `scripts/smoke_vkpi_p4_4_media_ux_contract.py`

### 新增脚本

- `scripts/smoke_vkpi_p4_22_settings_firewall_dynamic_qa.py`
- `scripts/smoke_vkpi_p4_23_kol_project_lifecycle_dynamic_qa.py`
- `scripts/smoke_vkpi_p4_25_runtime_health_preflight.py`
- `scripts/smoke_vkpi_p4_30_daily_top100_endpoint_qa.py`
- `scripts/smoke_vkpi_p4_32_data_quality_action_ui_contract.py`
- `scripts/smoke_vkpi_p4_33_media_full_content_contract.py`
- `scripts/smoke_vkpi_p4_34_media_loaded_count_contract.py`

## 4. 静态验证

### `run_smoke.sh`

结果: PASS

- `bash -n scripts/run_smoke.sh` 通过。
- 当前脚本使用单次执行模式: `"$PY" "$f" > "$log_file" 2>&1; rc=$?`。
- 已 source `scripts/runtime_env.sh`。
- 已强制本地 DB: `postgresql://postgres@127.0.0.1:54329/viltrox2`。
- 未发现之前的双跑逻辑。

### Python 编译

结果: PASS

已编译 10 个 P3/P4 smoke 脚本,全部通过 `py_compile`。

## 5. 批量 smoke 验证

命令:

```bash
./scripts/run_smoke.sh --batch \
  smoke_vkpi_p3_11c_daily_top100_ui_contract.py \
  smoke_vkpi_p3_13c_post_detail_contract.py \
  smoke_vkpi_p4_4_media_ux_contract.py \
  smoke_vkpi_p4_22_settings_firewall_dynamic_qa.py \
  smoke_vkpi_p4_23_kol_project_lifecycle_dynamic_qa.py \
  smoke_vkpi_p4_25_runtime_health_preflight.py \
  smoke_vkpi_p4_30_daily_top100_endpoint_qa.py \
  smoke_vkpi_p4_32_data_quality_action_ui_contract.py \
  smoke_vkpi_p4_33_media_full_content_contract.py \
  smoke_vkpi_p4_34_media_loaded_count_contract.py
```

结果:

```text
PASS=10
FAIL=0
TOTAL=10
```

判定: P4.1F smoke 批次通过。

## 6. Marker 残留检查与清理

### 初次扫描结果

初次修正扫描发现旧测试 marker 残留:

- `vkpi_business_audit_logs`: 7 条
- `vkpi_projects`: 1 条
- `vkpi_project_stage_events`: 3 条
- `kols`: 3 条

残留特征:

- `p4-step22-fw-*`
- `p4-step23-life-*`

这些均为 smoke marker 前缀,不是业务数据。

### 已执行定向清理

只清理上述测试 marker 前缀相关数据,涉及:

- `vkpi_business_audit_logs`
- `vkpi_settings_change_logs`
- `vkpi_feature_flags`
- `vkpi_platform_crawl_settings`
- `vkpi_budget_settings`
- `vkpi_project_stage_events`
- `vkpi_messages`
- `vkpi_shipments`
- `vkpi_project_terms`
- `vkpi_cost_ledger`
- `vkpi_projects`
- `vkpi_kol_claims`
- `kols`

清理对象:

- `vkpi_projects.id = 3441`
- `kols.id IN (3398, 3403, 3404)`

### 清理后复扫

结果:

```text
vkpi_feature_flags: 0
vkpi_platform_crawl_settings: 0
vkpi_budget_settings: 0
vkpi_business_audit_logs: 0
vkpi_settings_change_logs: 0
kols: 0
vkpi_projects: 0
vkpi_project_stage_events: 0
vkpi_messages: 0
vkpi_shipments: 0
vkpi_project_terms: 0
vkpi_cost_ledger: 0
vkpi_kol_claims: 0
```

判定: P4.1F marker-scoped 测试残留已清零。

说明: Python 退出时仍会偶发 `PythonFinalizationError` 的 psycopg pool 析构噪声,但本轮命令退出码为 0,不影响 smoke 判定。该噪声属于已知 Python 3.14 / psycopg_pool shutdown 问题,不等同业务失败。

## 7. Diff 检查

命令:

```bash
git diff --check
```

结果: PASS,未发现 whitespace error。

当前 git 脏改数量仍为 43 条。本轮没有 staging / commit。

## 8. 结论

P4.1F 通过。

- `run_smoke.sh` 单跑逻辑成立。
- P4 smoke 批次 10/10 PASS。
- 动态 smoke 后端真实路径可用。
- 旧 marker 测试残留已清零。
- 代码 diff 基础检查通过。

## 9. 下一步

进入 P4.1G: unit-test batch verification。

建议范围:

- `tests/test_vkpi_scope.py`
- `tests/test_vkpi_audit_firewall_decorators.py`
- `tests/test_vkpi_costs.py`
- `tests/test_vkpi_kol_pool.py`
- `tests/test_vkpi_kol_lifecycle_audit.py`
- `tests/test_vkpi_workflow_project_audit.py`
- `tests/test_vkpi_metric_lineage.py`

验收:

- `py_compile` 或 pytest collection 无失败。
- targeted pytest 全过。
- 对 `tests/test_vkpi_metric_lineage.py` 的修正必须保持生产定义,不能为了测试改业务公式。
