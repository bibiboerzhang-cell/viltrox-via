# V-KPI P4 Agent Package v1.1

本包是 P4 后半段多 Agent 协作的安全修订版。它只定义文档、边界、审计和试点任务,不启动 Outreach 或 Cost Dashboard 功能开发。

## 当前原则

- 先审计事实,再精准修复。
- 先 1 个 Agent 跑通,再扩到 3-5 个。
- Agent 默认只能改明确允许的文件。
- 主控线程负责 review 和 merge,Agent 不自动 merge。
- P4 的目标是治理和可靠性,不是继续扩大 Socialinsider 级功能范围。

## 执行顺序

| 顺序 | 阶段 | 动作 | 结果 |
|---:|---|---|---|
| 1 | E-v1.1 | 生成本修订包 | 文档和边界脚本就绪 |
| 2 | Phase 0A | 重新统计当前仓库模块归属 | `module-ownership.md` 更新到当前 HEAD |
| 3 | Phase 0B | 决策 unknown 模块归属 | unknown 清零或明确挂起 |
| 4 | Phase 1A | 只跑 `scope.py` 单元测试 Agent | 验证 Agent 是否能守边界 |
| 5 | Phase 1B | 验证 Agent 是否越界 | `verify_agent_boundary.py` 输出 `BOUNDARY_OK` |
| 6 | Phase 1C | 再扩到 3-5 个测试 Agent | 只补测试,不改业务 |
| 7 | P4.5 | Outreach | 独立组件 + 宿主接入契约 |
| 8 | P4.6 | Cost Dashboard | 先审计 ledger,再实现 dashboard |

## 目录

```text
vkpi-p4-agent-package-v1.1/
├── README.md
├── architecture/
│   ├── module-ownership.md
│   └── agent-boundary-protection.md
├── scripts/
│   └── verify_agent_boundary.py
└── agents/
    ├── unit-tests/
    │   ├── unit-test-agent.md
    │   └── p0-scope-test-agent.md
    ├── outreach/
    │   ├── p4-5-outreach-agent.md
    │   └── host-integration-contract.md
    └── cost-dashboard/
        ├── p4-6-cost-dashboard-audit-agent.md
        └── p4-6-cost-dashboard-agent.md
```

## Phase 0A 命令

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py
```

输出必须包含:

- services 子目录数量
- vkpi 顶层 py 文件数量
- routers 数量
- unknown 模块清单
- 每个模块归属状态

## Phase 1A 试点规则

第一轮只允许处理 `scope.py` 的单元测试。不要一次处理多个文件。

允许:

- 读取 `backend/app/core/scope.py` 或真实所在 scope 文件
- 新增/修改对应测试文件
- 更新测试说明文档

禁止:

- 修改业务逻辑
- 修改 router
- 修改 frontend
- 修改数据库 migration
- 修改 auth 或 permissions 逻辑

## Review Gate

每个 Agent PR 必须通过以下检查:

```bash
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py \
  --allowed tests \
  --allowed docs/agents \
  --diff-base HEAD~1
```

如果输出 `BOUNDARY_VIOLATION`,该 Agent 输出不可 merge。
