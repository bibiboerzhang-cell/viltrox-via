# P4.1G Unit Test Batch Verification

时间: 2026-05-15 08:39 Asia/Shanghai  
工作目录: `/Users/bibiboer/Documents/V-KPI——marketing`  
分支: `codex/vkpi-cleanup-d7`

## 1. 本轮目标

P4.1G 只验证当前 P4.1 单元测试批次,不改业务逻辑。

目标:

- 确认新增 / 修改的 P4 单元测试文件可编译。
- 跑 targeted pytest,验证权限、审计、防火墙、成本、KOL pool、项目生命周期、metric lineage 这些治理路径没有回退。
- 核对 warning 债务,但不在本轮扩散修复。

## 2. 备份

本轮开工前备份:

`/Users/bibiboer/Documents/V-KPI-backups/before-p4-1g-unit-tests-20260515-083847.tar.gz`

## 3. 覆盖范围

测试文件:

- `tests/test_vkpi_scope.py`
- `tests/test_vkpi_audit_firewall_decorators.py`
- `tests/test_vkpi_costs.py`
- `tests/test_vkpi_kol_pool.py`
- `tests/test_vkpi_kol_lifecycle_audit.py`
- `tests/test_vkpi_workflow_project_audit.py`
- `tests/test_vkpi_metric_lineage.py`

相关业务文件此前已在 P4.1D 覆盖:

- `backend/app/services/vkpi/costs.py`
- `backend/app/services/vkpi/kol_pool.py`
- `backend/app/services/vkpi/kol_claims_actions.py`
- `backend/app/services/vkpi/workflow_projects.py`
- `backend/app/services/vkpi/industry_data.py`
- `backend/app/api/routers/vkpi_industry_automation.py`

## 4. 编译验证

命令:

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile \
  tests/test_vkpi_scope.py \
  tests/test_vkpi_audit_firewall_decorators.py \
  tests/test_vkpi_costs.py \
  tests/test_vkpi_kol_pool.py \
  tests/test_vkpi_kol_lifecycle_audit.py \
  tests/test_vkpi_workflow_project_audit.py \
  tests/test_vkpi_metric_lineage.py
```

结果: PASS。

## 5. Targeted pytest

命令:

```bash
PYTHONPATH=backend .venv/bin/pytest \
  tests/test_vkpi_scope.py \
  tests/test_vkpi_audit_firewall_decorators.py \
  tests/test_vkpi_costs.py \
  tests/test_vkpi_kol_pool.py \
  tests/test_vkpi_kol_lifecycle_audit.py \
  tests/test_vkpi_workflow_project_audit.py \
  tests/test_vkpi_metric_lineage.py \
  -q
```

结果:

```text
49 passed, 101 warnings in 2.26s
```

判定: P4.1G 单元测试批次通过。

## 6. Warning 债务

本轮未处理 warning,只记录。

主要类别:

1. `asyncio.iscoroutinefunction()` deprecation
   - `backend/app/services/vkpi/audit_decorator.py`
   - `backend/app/services/vkpi/firewall_decorator.py`

2. `datetime.datetime.utcnow()` deprecation
   - `backend/app/services/vkpi/costs.py`
   - `backend/app/services/vkpi/audit.py`
   - `backend/app/services/vkpi/kol_pool.py`
   - `backend/app/services/vkpi/kol_claims_common.py`
   - `backend/app/services/vkpi/kol_claims_actions.py`
   - `backend/app/services/vkpi/workflow_common.py`
   - `backend/app/services/vkpi/metric_lineage_common.py`

3. 第三方库 warning
   - `google/genai/types.py` `_UnionGenericAlias` deprecation。

建议: 这些属于 P2 技术债,不阻塞 P4.1 收口,但应在后续独立 `utcnow` / decorator compatibility 小轮中集中修。

## 7. Diff 检查

命令:

```bash
git diff --check
```

结果: PASS。

## 8. 结论

P4.1G 通过。

- 7 个测试文件 py_compile PASS。
- targeted pytest 49/49 PASS。
- 101 warnings 已记录,不在本轮处理。
- 没有 staging / commit。

## 9. 下一步

P4.1 当前 7 个核验模块已完成:

- P4.1A dirty worktree classification
- P4.1B classification correction
- P4.1C docs / agent package low-risk review
- P4.1D backend governance verification
- P4.1E frontend / media UX verification
- P4.1F smoke script batch verification
- P4.1G unit test batch verification

建议下一步进入 P4.1H: closeout package + release risk matrix。

P4.1H 应输出:

- P4.1 总结报告
- 当前 43 条脏改按 batch 的最终处理建议
- P4.2 进入条件
- 是否需要纯净包 / 分享包
