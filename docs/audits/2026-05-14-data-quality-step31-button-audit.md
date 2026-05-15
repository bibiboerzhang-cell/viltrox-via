# P4 Step31 - Data Quality Button Audit

日期: 2026-05-14  
范围: `DataQualityPage` 按钮真实性、权限、审计、回滚口径  
性质: 审计 + 真实链路验证, 不改功能代码

## 当前页面入口

文件:

- `frontend/src/components/vkpi/pages/DataQualityPage.tsx`
- `backend/app/api/routers/vkpi_data_quality.py`
- `backend/app/services/vkpi/data_quality_actions.py`
- `backend/app/services/vkpi/data_quality_checks.py`

## 按钮清单

| 按钮 | 类型 | 当前真实性 | 风险 | 说明 |
|---|---|---:|---:|---|
| 重新检查 | 读接口 | 真按钮 | 低 | 调 `GET /api/admin/vkpi/data-quality?limit=200`, 只读取真实业务表 |
| 已处理 | 写接口 | 真按钮 | 中 | 调 `POST /data-quality/{issue_id}/resolve`, manager gate + action log + business audit |
| 指派 | 写接口 | 半真按钮 | 中 | 写 action log/audit, 但当前 UI 没有选择具体负责人, 默认指派当前管理层 |
| 重检 | 写接口 | 半真按钮 | 中 | 写 action log/audit, 但当前没有触发单问题重算 worker, 只是记录请求 |
| 补证据 | 写接口 | 半真按钮 | 中 | 写 action log/audit, 但当前没有打开证据上传/关联抽屉 |
| 忽略 | 写接口 | 真按钮 | 中偏高 | 写 action log/audit, 但缺二次确认和撤销入口 |

## 后端治理状态

已具备:

- `require_tab("vkpi", "write")`
- `_require_manager_staff(staff)` 管理层限制
- `vkpi_data_quality_actions` action log
- `audit.log_business_event(...)` business audit
- action 白名单: `resolve / ignore / assign / rerun / evidence / reopen`
- employee 不能 resolve 全局 issue, 已由 smoke 覆盖

缺口:

- UI 无二次确认: `已处理 / 忽略` 会立即写入 action log。
- UI 无分组: 5 个动作都堆在行内 `更多` 菜单里, 对真实用户不够清晰。
- 回滚入口未露出: service 支持 `reopen`, 但前端没有 `重新打开` 按钮。
- `重检 / 补证据` 当前是记录动作, 不是完整工作流。

## 当前真实数据

```json
{
  "status": "ok",
  "total_count": 223,
  "summary": {
    "critical": 0,
    "high": 10,
    "medium": 212,
    "low": 1,
    "info": 0
  },
  "issue_types": [
    "deleted_project_sales",
    "kpi_ledger_without_evidence",
    "missing_metric_snapshot",
    "pending_reconciliation",
    "unmatched_attribution"
  ]
}
```

## 验证命令

```bash
./scripts/run_smoke.sh smoke_vkpi_data_quality.py
```

结果:

```text
PASS=1 / FAIL=0 / TOTAL=1
```

## 结论

Data Quality 页面不是假页面, 但属于“真实链路 + 治理不足”状态。

P4 收口前建议做一轮小改:

- 将行内动作分为 3 组:
  - `处理`: 已处理 / 忽略
  - `分派`: 指派
  - `补救`: 重检 / 补证据 / 重新打开
- 对 `已处理 / 忽略` 加确认文案。
- 在 `重检 / 补证据` 上显示明确说明: “当前记录请求, 不会自动修复数据。”
- 增加 `重新打开` 前端入口, 对应后端已存在 `reopen`。

## 备份

```text
/Users/bibiboer/Documents/V-KPI-backups/before-p4-step31-data-quality-button-audit-20260514-185838.tar.gz
```
