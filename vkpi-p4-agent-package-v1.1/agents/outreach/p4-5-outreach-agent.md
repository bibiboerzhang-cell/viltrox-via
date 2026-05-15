# P4.5 Outreach Agent v1.1

## 目标

实现 KOL / Project 沟通历史 v1。注意: 宿主页面接入由主控线程完成,本 Agent 不得大范围修改宿主页面。

## Allowed files

- `backend/app/services/vkpi/outreach*.py`
- `backend/app/api/routers/vkpi_outreach.py`
- `frontend/src/components/vkpi/outreach/OutreachTimelinePanel.tsx`
- `frontend/src/components/vkpi/outreach/**`
- `docs/agents/active/outreach/**`
- `docs/audits/outreach/**`

## Forbidden files

- 现有 KOL 详情宿主页面,除非主控另开 host integration PR。
- 现有 Project 详情宿主页面,除非主控另开 host integration PR。
- `backend/app/core/permissions.py`
- `backend/app/core/scope.py`
- `migrations/**`,除非任务明确批准 schema 变更。
- `.env*`

## Expected output

- Outreach service。
- Outreach router。
- 独立 `OutreachTimelinePanel`。
- host integration guide。
- smoke 或测试。

## 危险按钮治理

Outreach 中以下动作必须具备确认、审计和可回滚/软删除策略:

- 发送邮件。
- 标记为已合作。
- 删除沟通记录。
- 上传敏感附件。
- 更改 KOL outreach 状态。

## Validation command

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile backend/app/api/routers/vkpi_outreach.py backend/app/services/vkpi/outreach*.py
PYTHONPATH=backend .venv/bin/pytest tests/ -k outreach -v
npm --prefix frontend run build
python3 vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py --diff-base HEAD~1 \
  --allowed backend/app/services/vkpi/outreach.py \
  --allowed backend/app/services/vkpi/outreach \
  --allowed backend/app/api/routers/vkpi_outreach.py \
  --allowed frontend/src/components/vkpi/outreach \
  --allowed docs/agents \
  --allowed docs/audits \
  --allowed tests
```

## Rollback rule

若 Agent 修改宿主页面超过 host contract,拒绝合并,改为主控线程单独做 host integration PR。

## Review checklist

- 是否遵守 host integration contract。
- 是否没有绕过权限。
- 是否所有写操作有 audit。
- 是否危险操作有确认或后端保护。
- 是否支持空态和只读态。
