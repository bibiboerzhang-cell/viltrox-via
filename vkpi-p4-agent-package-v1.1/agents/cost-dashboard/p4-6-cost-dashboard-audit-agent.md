# P4.6 Cost Dashboard Audit Agent

## 目标

Phase A 只做审计,不做功能开发。必须同时检查代码和数据库,输出当前成本账本能力报告。

## Allowed files

- `docs/audits/cost-dashboard-current-state.md`
- 可新增审计脚本到 `scripts/audit_cost_dashboard_state.py`,但不得修改业务代码。

## Forbidden files

- `backend/app/services/**`
- `backend/app/api/**`
- `frontend/**`
- `migrations/**`
- `.env*`

## 代码侧检查

必须检查:

- `backend/app/services/vkpi/costs.py`
- `backend/app/services/vkpi/llm_gateway.py`
- `migrations/**`
- provider cost 相关逻辑
- audit/cost ledger 调用点

## 数据库侧检查

必须检查:

- `vkpi_llm_calls` 是否存在
- `vkpi_cost_ledger` 是否存在
- provider call ledger 类表是否存在
- 当前字段是否支持 `provider / task / user / kol` 维度聚合
- 是否已有日期、金额、模型、调用目的、staff_id、kol_id 字段

## Expected output

`docs/audits/cost-dashboard-current-state.md` 必须包含:

- 现有表清单。
- 字段清单。
- 代码写入路径。
- 缺失字段。
- 是否建议复用现有表。
- 是否必须建新表。
- Phase B 实现建议。

## Validation command

```bash
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py --diff-base HEAD~1 \
  --allowed docs/audits \
  --allowed scripts/audit_cost_dashboard_state.py
```

## Rollback rule

如果 Phase A 修改了业务代码或 migration,整轮作废。

## Review checklist

- 是否真的检查了数据库,不是只看代码。
- 是否没有直接建表。
- 是否没有开始 dashboard UI。
- 是否清楚回答复用还是新建。
