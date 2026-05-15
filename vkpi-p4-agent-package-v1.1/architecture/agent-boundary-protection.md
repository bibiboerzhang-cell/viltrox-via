# Agent Boundary Protection v1.1

目标: 防止 Agent 在执行单模块任务时顺手修改无关业务代码。

## 三层保护

| 层 | 位置 | 作用 |
|---|---|---|
| 1 | 本地 pre-commit | 阻止 forbidden files 进入 commit |
| 2 | CI / PR check | 阻止越界 PR 合并 |
| 3 | Reviewer checklist | 检查语义越界,不只看路径 |

## 本地检查命令

```bash
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py \
  --allowed tests \
  --allowed docs/agents \
  --staged
```

## 示例 pre-commit hook

```bash
#!/usr/bin/env bash
set -euo pipefail
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py \
  --allowed tests \
  --allowed docs/agents \
  --staged
```

## 示例 Agent allowed paths

| Agent | Allowed paths |
|---|---|
| Scope Test Agent | `tests/`, `docs/agents/active/` |
| Outreach Agent | `backend/app/services/vkpi/outreach*`, `backend/app/api/routers/vkpi_outreach.py`, isolated frontend panel, docs |
| Cost Dashboard Audit Agent | `docs/audits/`, read-only code scan |
| Cost Dashboard Agent | implementation paths determined after audit |

## Reviewer Checklist

- 是否改了 forbidden files。
- 是否修改了业务逻辑而不是只补测试或文档。
- 是否新增 migration。
- 是否改 auth/permission/scope。
- 是否引入外部 API 调用。
- 是否改变现有 endpoint 行为。
- 是否有 rollback 说明。

## 违规处理

| 情况 | 处理 |
|---|---|
| 改了 forbidden file | 不 merge,要求 Agent 重新输出 |
| 测试 Agent 改业务逻辑 | 丢弃业务改动,只保留测试意图 |
| 未经批准新增 migration | 拒绝 |
| 改 auth/scope/permission | 升级为人工 review,不能自动合并 |
