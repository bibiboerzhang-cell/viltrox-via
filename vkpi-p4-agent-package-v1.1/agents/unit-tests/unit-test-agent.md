# Unit Test Agent v1.1

## 目标

用小范围 Agent 补齐关键模块单元测试,验证 Agent 是否能守住边界、补测试、不乱改业务逻辑。

## 第一轮只允许处理

`scope.py`

不要一次处理多个文件。

## 后续扩展顺序

1. `scope.py`
2. `audit_decorator.py` + `firewall_decorator.py`
3. `costs.py`
4. `kol_pool.py`

## Allowed files

- `tests/**`
- `docs/agents/active/**`
- 必要时新增测试 fixture 文件

## Forbidden files

- `backend/app/api/**`
- `backend/app/services/**`
- `frontend/**`
- `migrations/**`
- `alembic/**`
- `.env*`
- `scripts/start_*`

## Expected output

- 新增或更新测试文件。
- 测试命令说明。
- 覆盖场景说明。
- 不修改业务逻辑。

## Validation command

```bash
PYTHONPATH=backend .venv/bin/pytest tests/ -k scope -v
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py \
  --allowed tests \
  --allowed docs/agents \
  --diff-base HEAD~1
```

## Rollback rule

如果测试 Agent 修改了业务文件,直接丢弃该 Agent 输出,重新开 Agent。

## Review checklist

- 测试是否覆盖 owner、manager、employee、denied scope。
- 测试是否只验证行为,不依赖脆弱实现细节。
- 是否没有改业务逻辑。
- 是否没有跨目录改动。
