# P0 Scope Test Agent

## 身份

你是 V-KPI 权限 scope 单元测试 Agent。你的任务是补测试,不是改业务。

## Allowed files

- `tests/**`
- `docs/agents/active/p0-scope-test-agent.md`

## Forbidden files

- `backend/app/core/scope.py`
- `backend/app/api/**`
- `backend/app/services/**`
- `frontend/**`
- `migrations/**`

## Task

为 scope 逻辑补充单元测试,覆盖以下行为:

- owner / admin 可看全部。
- manager 可看团队或授权范围。
- employee 只能看自己的数据。
- unauthorized staff 被拒绝。
- `staff_id_filter` 与 staff scope 同时存在时不越权。
- denied scope 返回可解释错误,不是静默空数据。

## Expected output

- 一个或多个测试文件。
- 测试 fixture 只放在测试目录。
- 不修改任何业务实现。

## Validation command

```bash
PYTHONPATH=backend .venv/bin/pytest tests/ -k "scope" -v
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py \
  --allowed tests \
  --allowed docs/agents \
  --diff-base HEAD~1
```

## Rollback rule

任何业务文件变更都视为失败。该 Agent 输出整包作废。

## Review checklist

- 是否覆盖正向和拒绝路径。
- 是否没有改业务代码。
- 是否能在本地测试环境重复运行。
- 是否没有依赖真实生产数据。
