# P4.6 Cost Dashboard Agent

## 前置条件

必须先完成 `p4-6-cost-dashboard-audit-agent.md`,并由主控线程确认 Phase B 方案。

## 目标

基于审计结果实现 LLM/API 成本可观测 dashboard。

## Allowed files

以 Phase A 审计结果为准。默认候选:

- `backend/app/services/vkpi/cost_dashboard*.py`
- `backend/app/api/routers/vkpi_cost_dashboard.py`
- `frontend/src/components/vkpi/cost-dashboard/**`
- `docs/audits/cost-dashboard-implementation.md`
- `tests/**`

## Forbidden files

- `llm_gateway.py`,除非 Phase A 明确要求且主控批准。
- `costs.py`,除非 Phase A 明确要求且主控批准。
- `migrations/**`,除非 Phase A 明确要求且主控批准。
- `permissions.py` / `scope.py`
- `.env*`

## Expected output

- 成本 overview endpoint。
- provider / model / task / user / kol 维度聚合。
- 日预算/月预算显示。
- 异常成本提示。
- 前端只读 dashboard。

## Validation command

```bash
PYTHONPATH=backend .venv/bin/pytest tests/ -k cost_dashboard -v
npm --prefix frontend run build
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py --diff-base HEAD~1 --allowed backend/app/services/vkpi/cost_dashboard --allowed backend/app/api/routers/vkpi_cost_dashboard.py --allowed frontend/src/components/vkpi/cost-dashboard --allowed docs/audits --allowed tests
```

## Rollback rule

如果实现过程发现现有 ledger 不足,停止实现,回到 Phase A 报告,不要临时扩 schema。

## Review checklist

- 是否基于 Phase A 审计结果。
- 是否没有重复造 ledger。
- 是否没有隐式调用真实 LLM。
- 是否默认只读,不改成本数据。
